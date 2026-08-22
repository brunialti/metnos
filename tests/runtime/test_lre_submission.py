"""F13 conversational admission: closed routing, i18n failures and replay."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import lre_submission as submission
from durable_workloads.models import WorkloadState
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.storage import DurableWorkloadStore
from engine.proposer import SimpleProposer
from engine.routing_pool import build_routing_pool
from engine.types import Intent
from loader import invalidate_catalog_cache, load_catalog
from lre_config import LREFeatureConfiguration


def _configuration(enabled: bool) -> LREFeatureConfiguration:
    return LREFeatureConfiguration(enabled=enabled, valid=True, source="file")


def test_start_lre_rejects_relative_paths_before_opening_state(monkeypatch):
    monkeypatch.setattr(
        submission,
        "default_runtime_registry",
        lambda: (_ for _ in ()).throw(AssertionError("registry must not open")),
    )
    monkeypatch.setattr(submission, "_msg", lambda key, **_kwargs: key)

    result = submission.handle_start_lre(
        {"profile": "images.questions.v1", "paths": ["relative/file.png"]},
        owner_user_id="owner-f13",
        turn_id="turn:f13-invalid",
    )

    assert result == {
        "ok": False,
        "error": "ERR_LRE_REQUEST_INVALID",
        "error_code": "ERR_LRE_REQUEST_INVALID",
        "error_class": "invalid_args",
    }


def test_channel_request_identity_is_stable_opaque_and_bounded():
    from agent_runtime import _opaque_source_request_id

    scope = {
        "owner_user_id": "owner-f13",
        "conversation_id": "conversation-a",
        "channel": "telegram",
    }
    first = _opaque_source_request_id("telegram:424242", **scope)
    assert first == _opaque_source_request_id("telegram:424242", **scope)
    assert first.startswith("sha256:")
    assert "424242" not in first
    assert _opaque_source_request_id("x" * 513) == ""
    assert first != _opaque_source_request_id(
        "telegram:424242",
        **{**scope, "conversation_id": "conversation-b"},
    )
    assert _opaque_source_request_id(first, **scope) == first


def test_http_request_identity_has_a_closed_namespace_and_turn_fallback():
    from http_routes_agent import _http_source_request_id

    request = SimpleNamespace(headers={"Idempotency-Key": " delivery-42 "})
    assert _http_source_request_id(request) == "http-idempotency:delivery-42"
    assert _http_source_request_id(
        SimpleNamespace(headers={}),
        fallback_turn_id="accepted-turn",
    ) == "http-turn:accepted-turn"


def test_approval_resume_reuses_the_accepted_request_identity(monkeypatch):
    import agent_runtime
    import orchestration

    observed = {}

    def resume(_query, **kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(agent_runtime, "run_turn", resume)
    monkeypatch.setattr(
        orchestration,
        "_completion_from_turnlog",
        lambda _log: SimpleNamespace(text="ok"),
    )

    result = orchestration._process_resume_engine_gate(
        {
            "gate_approve_value": "approve",
            "original_query": "Affida a LRE il corpus.",
            "conversation_id": "conversation-test",
            "owner_user_id": "owner-f13",
            "source_request_id": "sha256:" + "c" * 64,
        },
        {"decision": "approve"},
        actor="host",
        channel="telegram",
    )

    assert result.text == "ok"
    assert observed["source_request_id"] == "sha256:" + "c" * 64
    assert observed["pre_approved_gate"] is True


def test_start_lre_off_on_replay_off_preserves_one_readable_workload(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "synthetic-corpus" / "page.png"
    source.parent.mkdir()
    source.write_bytes(b"not personal; admission-only fixture")
    database = tmp_path / "workloads.sqlite3"
    authority_database = tmp_path / "authority.sqlite3"
    state = {"enabled": False}

    monkeypatch.setattr(
        submission,
        "read_feature_configuration",
        lambda: _configuration(state["enabled"]),
    )
    monkeypatch.setattr(
        submission,
        "feature_configuration_lock",
        lambda: nullcontext(),
    )
    monkeypatch.setattr(
        submission,
        "DurableWorkloadStore",
        SimpleNamespace(open=lambda: DurableWorkloadStore.open(database)),
    )
    monkeypatch.setattr(
        submission,
        "SourceAuthority",
        SimpleNamespace(open=lambda: SourceAuthority.open(authority_database)),
    )
    monkeypatch.setattr(
        submission,
        "_msg",
        lambda key, **values: key.format(**values),
    )

    import durable_workloads.service as service

    monkeypatch.setattr(
        service,
        "health_snapshot",
        lambda: {
            "state": "ready",
            "enabled": state["enabled"],
            "worker_available": state["enabled"],
        },
    )
    arguments = {
        "profile": "images.questions.v1",
        "paths": [str(source)],
    }
    call = {
        "owner_user_id": "owner-f13-route",
        "turn_id": "turn:f13-restart",
        "source_request_id": "telegram:424242",
    }

    disabled = submission.handle_start_lre(arguments, **call)
    assert disabled["error_code"] == "ERR_LRE_DISABLED"
    assert not database.exists()

    state["enabled"] = True
    original_summary = DurableWorkloadStore.execution_summary
    monkeypatch.setattr(
        DurableWorkloadStore,
        "execution_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("projection unavailable")
        ),
    )
    admitted = submission.handle_start_lre(arguments, **call)
    monkeypatch.setattr(
        DurableWorkloadStore,
        "execution_summary",
        original_summary,
    )
    replayed_after_reopen = submission.handle_start_lre(
        arguments,
        owner_user_id=call["owner_user_id"],
        turn_id="turn:f13-after-process-restart",
        source_request_id=call["source_request_id"],
    )
    assert admitted["ok"] is True
    assert admitted["state"] == WorkloadState.QUEUED.value
    assert admitted["decision"] == "accepted"
    assert admitted["final_message_hint"] == "MSG_LRE_SUBMITTED"
    assert "plan_summary" not in admitted
    assert replayed_after_reopen["plan_summary"]["stage_count"] > 0
    assert replayed_after_reopen["plan_summary"]["required_stage_count"] > 0
    assert replayed_after_reopen["limits"]["max_units"] > 0
    assert replayed_after_reopen["limits"]["max_concurrency"] > 0
    assert replayed_after_reopen["workload_id"] == admitted["workload_id"]
    assert replayed_after_reopen["revision_id"] == admitted["revision_id"]
    assert admitted["status_url"] == "/agent/workloads"
    assert str(source) not in str(admitted)

    state["enabled"] = False
    rejected_again = submission.handle_start_lre(
        arguments,
        owner_user_id=call["owner_user_id"],
        turn_id="turn:f13-disabled-again",
        source_request_id="telegram:424243",
    )
    assert rejected_again["error_code"] == "ERR_LRE_DISABLED"

    with DurableWorkloadStore.open(database) as store:
        visible = store.list_workloads(call["owner_user_id"])
        assert len(visible) == 1
        assert visible[0].workload_id == admitted["workload_id"]
        assert visible[0].state is WorkloadState.QUEUED


def test_natural_long_running_request_keeps_the_closed_system_executor():
    invalidate_catalog_cache()
    catalog = list(load_catalog(verify=True, include_synth=False))
    intent = Intent(
        verb="read",
        object="files",
        keywords=["intero", "corpus"],
        confidence=0.99,
        lang="it",
    )
    query = "Analizza in background l’intero corpus della cartella indicata."

    pool = build_routing_pool(query, intent, catalog)
    assert "start_lre" in pool

    effective = SimpleProposer()._effective_pool(
        query=query,
        intent=intent,
        pool=pool,
        catalog=catalog,
        exclude_tools=(),
    )
    assert "start_lre" in effective
