"""F13 conversational admission: closed routing, i18n failures and replay."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import lre_submission as submission
from durable_workloads.direct_invocation import (
    direct_runtime_registration,
    is_intrinsically_long,
)
from durable_workloads.models import WorkloadState
from durable_workloads.runtime_bindings import RuntimeRegistry
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.storage import DurableWorkloadStore
from engine.proposer import SimpleProposer
from engine.routing_pool import build_routing_pool
from engine.types import Framework, Intent, StepSpec
from loader import invalidate_catalog_cache, load_catalog
from lre_config import LREFeatureConfiguration


def _configuration(enabled: bool) -> LREFeatureConfiguration:
    return LREFeatureConfiguration(enabled=enabled, valid=True, source="file")


def _direct_executor(*, timeout_s: int = 600):
    return SimpleNamespace(
        name="scan_direct_fixture",
        version="1.0.0",
        signed_by="fixture-authority",
        digest="sha256:" + "d" * 64,
        lifecycle="active",
        dormant=False,
        transport="local-or-remote",
        intelligence="deterministic",
        timeout_s=timeout_s,
        args_schema={
            "type": "object",
            "required": ["root"],
            "properties": {"root": {"type": "string"}},
        },
        capabilities=(),
        placement={"scope": "any", "device_ok": True},
        execution_policy_declared=True,
        execution_policy={
            "effect": "read_only",
            "parallelism_class": 1,
            "resource_class": "local_io",
            "concurrency_key": "none",
            "equivalence_gate": "verified",
        },
    )


def _ready_direct_runtime(monkeypatch, tmp_path, executor):
    registration = direct_runtime_registration([executor])
    assert registration is not None
    registry = RuntimeRegistry((registration,))
    database = tmp_path / "private" / "automatic.sqlite3"
    monkeypatch.setattr(
        submission, "read_feature_configuration", lambda: _configuration(True),
    )
    monkeypatch.setattr(
        submission, "feature_configuration_lock", lambda: nullcontext(),
    )
    monkeypatch.setattr(submission, "default_runtime_registry", lambda: registry)
    monkeypatch.setattr(
        submission,
        "DurableWorkloadStore",
        SimpleNamespace(open=lambda: DurableWorkloadStore.open(database)),
    )
    monkeypatch.setattr(submission, "_msg", lambda key, **_values: key)
    import durable_workloads.service as service

    monkeypatch.setattr(
        service,
        "health_snapshot",
        lambda: {"state": "ready", "enabled": True, "worker_available": True},
    )
    return database


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
    assert admitted["status_url"] == "/admin/lre"
    assert str(source) not in str(admitted)

    conflicting = submission.handle_start_lre(
        {"profile": "images.questions.v1", "paths": [str(source.parent)]},
        owner_user_id=call["owner_user_id"],
        turn_id="turn:f13-conflict",
        source_request_id=call["source_request_id"],
    )
    assert conflicting["error_code"] == "ERR_LRE_IDEMPOTENCY_CONFLICT"

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


def test_automatic_admission_only_claims_finalized_intrinsically_long_work(
    monkeypatch,
):
    short = _direct_executor(timeout_s=599)
    long = _direct_executor(timeout_s=600)
    monkeypatch.setattr(
        submission,
        "_require_ready",
        lambda: (_ for _ in ()).throw(AssertionError("must not inspect runtime")),
    )

    assert submission.submit_automatic_lre(
        Framework(steps=[StepSpec(short.name, {"root": "/short"})]),
        catalog=[short],
        owner_user_id="owner-auto",
        turn_id="turn:auto-short",
    ) is None
    assert submission.submit_automatic_lre(
        Framework(steps=[
            StepSpec("get_approval", {}),
            StepSpec(long.name, {"root": "/long"}),
        ]),
        catalog=[long],
        owner_user_id="owner-auto",
        turn_id="turn:auto-gated",
    ) is None

    rejected = submission.submit_automatic_lre(
        Framework(steps=[
            StepSpec(long.name, {"root": "/long"}),
            StepSpec("ordinary_step", {}),
        ]),
        catalog=[long],
        owner_user_id="owner-auto",
        turn_id="turn:auto-compound",
    )
    assert rejected["decision"] == "rejected"
    assert rejected["error_code"] == "ERR_LRE_PLAN_NOT_ADMISSIBLE"


def test_automatic_admission_failure_never_falls_through_to_inline(monkeypatch):
    executor = _direct_executor()
    framework = Framework(steps=[StepSpec(executor.name, {"root": "/long"})])
    monkeypatch.setattr(submission, "_msg", lambda key, **_values: key)
    monkeypatch.setattr(
        submission,
        "_require_ready",
        lambda: (_ for _ in ()).throw(KeyError("unexpected fixture failure")),
    )

    result = submission.submit_automatic_lre(
        framework,
        catalog=[executor],
        owner_user_id="owner-auto",
        turn_id="turn:auto-failure",
    )

    assert result["decision"] == "rejected"
    assert result["error_code"] == "ERR_LRE_SUBMISSION_FAILED"
    assert result["final_message_hint"] == "ERR_LRE_SUBMISSION_FAILED"


def test_automatic_admission_reports_each_readiness_failure(monkeypatch):
    executor = _direct_executor()
    framework = Framework(steps=[StepSpec(executor.name, {"root": "/long"})])
    state = {"enabled": False, "valid": True, "worker": False}
    monkeypatch.setattr(submission, "_msg", lambda key, **_values: key)
    monkeypatch.setattr(
        submission,
        "read_feature_configuration",
        lambda: LREFeatureConfiguration(
            enabled=state["enabled"], valid=state["valid"], source="fixture",
        ),
    )
    import durable_workloads.service as service

    monkeypatch.setattr(
        service,
        "health_snapshot",
        lambda: {
            "state": "ready" if state["worker"] else "degraded",
            "enabled": state["enabled"],
            "worker_available": state["worker"],
        },
    )

    disabled = submission.submit_automatic_lre(
        framework, catalog=[executor], owner_user_id="owner-auto",
        turn_id="turn:auto-disabled",
    )
    state.update(enabled=True, valid=False)
    invalid = submission.submit_automatic_lre(
        framework, catalog=[executor], owner_user_id="owner-auto",
        turn_id="turn:auto-invalid",
    )
    state.update(valid=True, worker=False)
    unavailable = submission.submit_automatic_lre(
        framework, catalog=[executor], owner_user_id="owner-auto",
        turn_id="turn:auto-worker",
    )

    assert disabled["error_code"] == "ERR_LRE_DISABLED"
    assert invalid["error_code"] == "ERR_LRE_CONFIG_INVALID"
    assert unavailable["error_code"] == "ERR_LRE_WORKER_UNAVAILABLE"
    assert all(
        result["decision"] == "rejected"
        for result in (disabled, invalid, unavailable)
    )


def test_automatic_admission_replays_once_and_binds_idempotency_to_device(
    monkeypatch,
    tmp_path,
):
    executor = _direct_executor()
    database = _ready_direct_runtime(monkeypatch, tmp_path, executor)
    root = "/private/fixture/photo-library"
    device = "PC-PRIVATE-FIXTURE"
    framework = Framework(steps=[StepSpec(executor.name, {"root": root})])
    call = {
        "catalog": [executor],
        "owner_user_id": "owner-auto",
        "turn_id": "turn:auto-first",
        "source_request_id": "telegram:delivery-fixture",
        "target_device": device,
    }

    admitted = submission.submit_automatic_lre(framework, **call)
    replayed = submission.submit_automatic_lre(
        framework,
        **{**call, "turn_id": "turn:auto-replay"},
    )
    retargeted = submission.submit_automatic_lre(
        framework,
        **{
            **call,
            "turn_id": "turn:auto-retarget",
            "target_device": "PC-OTHER-FIXTURE",
        },
    )
    changed_arguments = submission.submit_automatic_lre(
        Framework(steps=[StepSpec(executor.name, {"root": "/other"})]),
        **{**call, "turn_id": "turn:auto-changed-arguments"},
    )

    assert admitted["decision"] == "accepted"
    assert replayed["workload_id"] == admitted["workload_id"]
    assert replayed["revision_id"] == admitted["revision_id"]
    assert retargeted["decision"] == "rejected"
    assert retargeted["error_code"] == "ERR_LRE_IDEMPOTENCY_CONFLICT"
    assert changed_arguments["decision"] == "rejected"
    assert changed_arguments["error_code"] == "ERR_LRE_IDEMPOTENCY_CONFLICT"
    assert root not in str(admitted)
    assert device not in str(admitted)

    with DurableWorkloadStore.open(database) as store:
        rows = store._connection.execute(
            "SELECT redacted_request_json FROM workloads WHERE owner_user_id=?",
            (call["owner_user_id"],),
        ).fetchall()
        assert len(rows) == 1
        redacted = str(rows[0][0])
        assert root not in redacted
        assert device not in redacted
        assert '"device_present":true' in redacted
        assert '"placement_digest":"sha256:' in redacted


def test_natural_long_running_request_does_not_require_the_technical_profile():
    invalidate_catalog_cache()
    catalog = list(load_catalog(verify=True, include_synth=False))
    image_indexer = next(
        executor for executor in catalog
        if executor.name == "create_images_indices"
    )
    assert image_indexer.timeout_s == 14_400
    assert is_intrinsically_long(image_indexer) is True
    intent = Intent(
        verb="read",
        object="files",
        keywords=["intero", "corpus"],
        confidence=0.99,
        lang="it",
    )
    query = "Analizza in background l’intero corpus della cartella indicata."

    pool = build_routing_pool(query, intent, catalog)
    assert "start_lre" not in pool

    effective = SimpleProposer()._effective_pool(
        query=query,
        intent=intent,
        pool=pool,
        catalog=catalog,
        exclude_tools=(),
    )
    assert "start_lre" not in effective

    technical_pool = build_routing_pool(
        "Esegui start_lre con il profilo LRE registrato.", intent, catalog,
    )
    assert "start_lre" in technical_pool
