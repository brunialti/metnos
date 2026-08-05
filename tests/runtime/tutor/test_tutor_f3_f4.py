from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from tutor.models import (
    TutorAnswer,
    TutorEvidence,
    TutorPrincipal,
    TutorRequest,
)
from tutor.mode import ModeDecision


def _principal(
        user_id: str = "user-a", *, actor: str = "alice",
        audience: str = "user", channel: str = "http",
        conversation_id: str = "conv-a") -> TutorPrincipal:
    return TutorPrincipal(
        user_id=user_id,
        actor=actor,
        audience=audience,
        channel=channel,
        conversation_id=conversation_id,
    )


def _device_facts(total: int) -> dict:
    return {"total": total, "available": total, "devices": []}


def test_f3_probe_registry_is_closed_and_attested_by_observation_views():
    from tutor.observation_views import catalog, validate_views
    from tutor.probes import registered_probe_ids

    expected = {
        "admitted_executor_state",
        "service_health",
        "owned_device_state",
        "actor_task_state",
        "scheduler_health",
    }
    assert registered_probe_ids() == expected
    assert validate_views() == ()
    refs = {view.probe_id for view in catalog()}
    assert refs == expected


def test_f3_only_dedicated_view_units_carry_live_authority():
    from tutor.sources import (
        _observation_view_units,
        _service_registry_units,
        _ui_surface_units,
    )

    broad = (*_service_registry_units(), *_ui_surface_units())
    live = _observation_view_units()
    assert broad and all(unit.observation_ref == "" for unit in broad)
    assert live and all(
        unit.source_kind == "live_observation" and unit.observation_ref
        for unit in live)


def test_f3_user_selector_inventory_contains_only_user_visible_views(
        monkeypatch):
    from tutor.observation_views import select_view

    observed = {}

    def select(**kwargs):
        observed.update(kwargs)
        return "NONE", "semantic_decision"

    monkeypatch.setattr("tutor.mode._invoke_closed_classifier", select)
    result = select_view(
        query="stato corrente", lang="it", principal=_principal(),
        deadline_at=10**12)
    ids = {row["view_id"] for row in observed["payload"]["views"]}
    assert result.view is None and result.available
    assert ids == {"OWNED_DEVICE_STATE", "ACTOR_TASK_STATE"}
    assert "SERVICES_STATUS" not in observed["allowed"]


@pytest.mark.parametrize(("query", "lang"), (
    ("Quali servizi sono attivi e a cosa servono?", "it"),
    ("Mostrami lo stato attuale dei servizi di Metnos.", "it"),
    ("Which services are currently running and what do they do?", "en"),
    ("Show me the current health of the Metnos services.", "en"),
))
def test_f3_current_service_questions_select_the_live_view(
        monkeypatch, query, lang):
    """Selection is semantic and can return only a visible registered view."""

    from tutor.observation_views import select_view

    observed = {}

    def select(**kwargs):
        observed.update(kwargs)
        return "SERVICES_STATUS", "semantic_decision"

    monkeypatch.setattr("tutor.mode._invoke_closed_classifier", select)
    result = select_view(
        query=query, lang=lang,
        principal=_principal(audience="instance_admin"),
        deadline_at=10**12,
    )

    assert result.available and result.view is not None
    assert result.view.view_id == "SERVICES_STATUS"
    assert result.view.probe_id == "service_health"
    assert observed["payload"]["user_query"] == query
    assert "SERVICES_STATUS" in observed["allowed"]


def test_f3_proposed_view_needs_an_independent_complete_coverage_pass(
        monkeypatch):
    from tutor.observation_views import select_view

    calls = []

    def classify(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "SERVICES_STATUS", "semantic_decision"
        return "NONE", "semantic_decision"

    monkeypatch.setattr("tutor.mode._invoke_closed_classifier", classify)
    result = select_view(
        query="Controlla se c'è posta Metnos", lang="it",
        principal=_principal(audience="instance_admin"),
        deadline_at=10**12,
    )

    assert result.view is None and result.available
    assert result.reason == "verification_rejected"
    assert len(calls) == 2
    assert calls[1]["payload"]["verification_pass"] is True
    assert [row["view_id"] for row in calls[1]["payload"]["views"]] == [
        "SERVICES_STATUS"
    ]


def test_tutor_mode_unavailability_falls_through_before_authority(monkeypatch):
    from tutor.service import answer_request

    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "tutor.service.classify", lambda _query: SimpleNamespace(reason=""))
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_args, **_kwargs: ModeDecision(
            "UNKNOWN", False, "scheduler_timeout"),
    )

    answer = answer_request(TutorRequest(
        "Leggi le mie email", "it", _principal(),
        deadline_at=10**12,
    ))

    assert answer is None


def test_f3_live_admission_deadline_falls_through_to_runtime(monkeypatch):
    from tutor.deadline import TutorDeadlineExceeded
    from tutor.service import answer_request

    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "tutor.service.classify", lambda _query: SimpleNamespace(reason=""))
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_args, **_kwargs: ModeDecision("OBSERVE", True),
    )
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot",
        lambda: SimpleNamespace(units=(), knowledge_index=None),
    )
    monkeypatch.setattr(
        "tutor.observation_views.select_view",
        lambda **_kwargs: (_ for _ in ()).throw(
            TutorDeadlineExceeded("busy local model")),
    )

    answer = answer_request(TutorRequest(
        "Trova i file duplicati", "it", _principal(),
        deadline_at=10**12,
    ))

    assert answer is None


@pytest.mark.parametrize(("vector", "expected", "reason"), (
    ([1.0, 0.0, 0.0, 0.0, 0.0], True, "coverage_verified"),
    ([0.80, 0.79, 0.0, 0.0, 0.0], False, "coverage_ambiguous"),
    ([0.70, 0.90, 0.0, 0.0, 0.0], False, "coverage_not_primary"),
    ([0.75, 0.0, 0.0, 0.0, 0.0], False,
     "coverage_below_authority_floor"),
))
def test_f3_live_authority_requires_contrastive_semantic_coverage(
        monkeypatch, vector, expected, reason):
    from tutor.observation_views import catalog, verify_semantic_coverage
    from tutor.sources import _observation_view_units

    visible = catalog()
    units = tuple(
        unit for unit in _observation_view_units() if unit.lang == "it")
    refs = tuple((unit.unit_id, unit.lang) for unit in units)
    snapshot = SimpleNamespace(
        units=units,
        knowledge_index=SimpleNamespace(
            refs=refs,
            matrix=np.eye(len(units), dtype=np.float32),
            dimension=len(units),
        ),
    )
    monkeypatch.setattr(
        "tutor.semantic._query_vector",
        lambda *_args, **_kwargs: np.asarray(vector, dtype=np.float32),
    )
    monkeypatch.setattr("tutor.semantic.knowledge_minimum", lambda: 0.70)
    monkeypatch.setattr("tutor.semantic.knowledge_band", lambda: 0.06)

    accepted, observed_reason = verify_semantic_coverage(
        query="richiesta", lang="it",
        principal=_principal(audience="instance_admin"),
        selected=visible[0], snapshot=snapshot, deadline_at=10**12,
    )

    assert accepted is expected
    assert observed_reason == reason


def test_f3_observation_view_locales_are_projected_from_registry_data(
        monkeypatch):
    import tutor.observation_views as views
    from tutor.sources import _observation_view_units

    view = views.ObservationViewSpec(
        view_id="TEST_LOCALIZED_VIEW",
        probe_id="service_health",
        audience="instance_admin",
        title={"en": "Current test state", "fr": "État actuel du test"},
        coverage={
            "en": "Observed test facts.",
            "fr": "Faits observés du test.",
        },
        excluded={
            "en": "No internal details.",
            "fr": "Aucun détail interne.",
        },
        fact_paths=("total",),
    )
    monkeypatch.setattr(views, "_VIEWS", (view,))

    assert views.validate_views() == ()
    units = _observation_view_units()
    assert {unit.lang for unit in units} == {"en", "fr"}
    french = next(unit for unit in units if unit.lang == "fr")
    assert french.title == "État actuel du test"
    assert french.text == "Faits observés du test. Aucun détail interne."
    assert "Read-only" not in french.text


def test_f3_observation_full_chain_uses_only_selected_signed_view(monkeypatch):
    from tutor.compose import Composition
    from tutor.observation_views import ViewSelection, catalog
    from tutor.probes import _clear_cache_for_tests
    from tutor.service import answer_request
    from tutor.sources import _observation_view_units

    _clear_cache_for_tests()
    view = next(item for item in catalog()
                if item.view_id == "SERVICES_STATUS")
    units = tuple(_observation_view_units())
    snapshot = SimpleNamespace(
        version="sha256:view-catalog", cards=(), units=units,
        card_index=None, knowledge_index=None,
    )
    observed = {}
    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "tutor.service.classify", lambda _query: SimpleNamespace(reason=""))
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_args, **_kwargs: ModeDecision("OBSERVE", True))
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        "tutor.observation_views.select_view",
        lambda **_kwargs: ViewSelection(view, True, "semantic_view"))
    monkeypatch.setattr(
        "tutor.observation_views.verify_semantic_coverage",
        lambda **_kwargs: (True, "coverage_verified"))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("live observation crossed static retrieval/F4")))

    def compose(**kwargs):
        observed.update(kwargs)
        return Composition("answer", "Sono attivi due servizi.")

    monkeypatch.setattr("tutor.compose.compose_answer", compose)
    facts = {
        "total": 3,
        "running": 2,
        "degraded": 0,
        "services": [{
            "key": "http", "label": "HTTP", "description": "Chat web",
            "status": "running", "installed": True, "healthy": True,
            "scope": "system",
        }],
    }
    answer = answer_request(TutorRequest(
        "Qual è lo stato corrente dei servizi?", "it",
        _principal(audience="instance_admin"),
        probes={"service_health": facts},
        conversation_context="OLD SOURCE MUST NOT AUTHORIZE LIVE DATA",
    ))

    assert answer is not None and answer.esito == "fondata"
    assert answer.evidence is None
    assert answer.probe_statuses == (("service_health", "ok"),)
    assert f"view:{view.view_id}" in answer.source_ids
    assert observed["conversation_context"] == ""
    assert '"view_id":"SERVICES_STATUS"' in observed["context"]
    assert "OLD SOURCE MUST NOT AUTHORIZE" not in observed["context"]


def test_f3_post_authority_uses_full_budget_and_composer_failure_falls_through(
        monkeypatch):
    import time

    from tutor.compose import Composition
    from tutor.observation_views import ViewSelection, catalog
    from tutor.probes import _clear_cache_for_tests
    from tutor.service import answer_request
    from tutor.sources import _observation_view_units

    _clear_cache_for_tests()
    view = next(item for item in catalog()
                if item.view_id == "SERVICES_STATUS")
    snapshot = SimpleNamespace(
        version="sha256:view-catalog", cards=(),
        units=tuple(_observation_view_units()),
        card_index=None, knowledge_index=None,
    )
    deadlines = {}
    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "tutor.service.classify", lambda _query: SimpleNamespace(reason=""))
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_args, **_kwargs: ModeDecision("OBSERVE", True))
    monkeypatch.setattr("tutor.deadline.mode_budget_s", lambda: 0.5)
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot", lambda: snapshot)

    def select(**kwargs):
        deadlines["select"] = kwargs["deadline_at"]
        return ViewSelection(view, True, "semantic_view")

    def verify(**kwargs):
        deadlines["verify"] = kwargs["deadline_at"]
        return True, "coverage_verified"

    def compose(**kwargs):
        deadlines["compose"] = kwargs["deadline_at"]
        return Composition("unavailable")

    monkeypatch.setattr("tutor.observation_views.select_view", select)
    monkeypatch.setattr(
        "tutor.observation_views.verify_semantic_coverage", verify)
    monkeypatch.setattr("tutor.compose.compose_answer", compose)
    request_deadline = time.monotonic() + 30.0
    answer = answer_request(TutorRequest(
        "Qual è lo stato corrente dei servizi?", "it",
        _principal(audience="instance_admin"),
        probes={"service_health": {
            "total": 1, "running": 1, "degraded": 0, "services": [],
        }},
        deadline_at=request_deadline,
    ))

    assert answer is None
    assert deadlines["select"] == deadlines["verify"]
    assert deadlines["select"] < request_deadline
    assert deadlines["compose"] == request_deadline


def test_f3_new_locale_uses_english_view_evidence_but_keeps_output_language(
        monkeypatch):
    from tutor.compose import Composition
    from tutor.observation_views import ViewSelection, catalog
    from tutor.probes import _clear_cache_for_tests
    from tutor.service import answer_request
    from tutor.sources import _observation_view_units

    _clear_cache_for_tests()
    view = next(item for item in catalog()
                if item.view_id == "SERVICES_STATUS")
    snapshot = SimpleNamespace(
        version="sha256:view-catalog", cards=(),
        units=tuple(_observation_view_units()),
        card_index=None, knowledge_index=None,
    )
    observed = {}
    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "tutor.service.classify", lambda _query: SimpleNamespace(reason=""))
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_args, **_kwargs: ModeDecision("OBSERVE", True))
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        "tutor.observation_views.select_view",
        lambda **_kwargs: ViewSelection(view, True, "semantic_view"))
    monkeypatch.setattr(
        "tutor.observation_views.verify_semantic_coverage",
        lambda **_kwargs: (True, "coverage_verified"))

    def compose(**kwargs):
        observed.update(kwargs)
        return Composition("answer", "Le service est actif.")

    monkeypatch.setattr("tutor.compose.compose_answer", compose)
    answer = answer_request(TutorRequest(
        "Quel est l’état actuel des services ?", "fr",
        _principal(audience="instance_admin"),
        probes={"service_health": {
            "total": 1, "running": 1, "degraded": 0, "services": [],
        }},
        deadline_at=10**12,
    ))

    assert answer is not None and answer.esito == "fondata"
    assert observed["lang"] == "fr"
    assert "Observed service inventory" in observed["context"]
    assert any(source.endswith("services-status-en")
               for source in answer.source_ids)


def test_f3_probe_cache_is_scoped_by_user_actor_audience_and_language():
    from tutor.probes import _clear_cache_for_tests, execute_probe_refs

    _clear_cache_for_tests()
    first = execute_probe_refs(
        ("owned_device_state",),
        principal=_principal(),
        lang="it",
        injected={"owned_device_state": _device_facts(1)},
    )[0]
    cached = execute_probe_refs(
        ("owned_device_state",),
        principal=_principal(),
        lang="it",
        injected={"owned_device_state": _device_facts(9)},
    )[0]
    other_language = execute_probe_refs(
        ("owned_device_state",),
        principal=_principal(),
        lang="en",
        injected={"owned_device_state": _device_facts(2)},
    )[0]
    other_actor = execute_probe_refs(
        ("owned_device_state",),
        principal=_principal(actor="alice-as-guest"),
        lang="it",
        injected={"owned_device_state": _device_facts(3)},
    )[0]
    other_user = execute_probe_refs(
        ("owned_device_state",),
        principal=_principal("user-b", actor="bob"),
        lang="it",
        injected={"owned_device_state": _device_facts(4)},
    )[0]

    assert first.facts["total"] == cached.facts["total"] == 1
    assert other_language.facts["total"] == 2
    assert other_actor.facts["total"] == 3
    assert other_user.facts["total"] == 4


def test_f3_probe_audience_timeout_and_bounded_stale_fallback(monkeypatch):
    import tutor.probes as probes

    probes._clear_cache_for_tests()
    denied = probes.execute_probe_refs(
        ("service_health",),
        principal=_principal(audience="user"),
        lang="it",
        injected={"service_health": {}},
    )[0]
    assert denied.status == "unavailable"
    assert denied.redactions == ("audience_mismatch",)

    monkeypatch.setattr(
        probes.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))),
    )
    timed_out = probes.execute_probe_refs(
        ("service_health",),
        principal=_principal(audience="instance_admin"),
        lang="it",
    )[0]
    assert timed_out.status == "unavailable"
    assert timed_out.redactions == ("timeout",)

    probes._clear_cache_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(probes.time, "time", lambda: clock["now"])
    initial = probes.execute_probe_refs(
        ("owned_device_state",), principal=_principal(), lang="it",
        injected={"owned_device_state": _device_facts(5)},
    )[0]
    assert initial.status == "ok"
    clock["now"] = 221.0  # TTL 120 s, still inside stale horizon.
    stale = probes.execute_probe_refs(
        ("owned_device_state",), principal=_principal(), lang="it",
        injected={"owned_device_state": object()},
    )[0]
    assert stale.status == "stale"
    assert stale.facts["total"] == 5
    clock["now"] = 311.0  # fresh_until 220 + stale horizon 90.
    unavailable = probes.execute_probe_refs(
        ("owned_device_state",), principal=_principal(), lang="it",
        injected={"owned_device_state": object()},
    )[0]
    assert unavailable.status == "unavailable"
    assert unavailable.facts == {}


def test_f3_probe_owner_filter_is_passed_to_device_registry(monkeypatch):
    from tutor.probes import ProbeContext, _device_payload

    observed = []
    monkeypatch.setattr(
        "devices.list_by_owner_readonly",
        lambda owner: observed.append(owner) or [],
    )
    monkeypatch.setattr("placement.is_available", lambda _device: True)
    payload = _device_payload(ProbeContext(_principal("owner-17"), "it"))
    assert observed == ["owner-17"]
    assert payload.facts == {"total": 0, "available": 0, "devices": []}


def test_f3_probe_rejects_unattested_nested_fields():
    from tutor.probes import _clear_cache_for_tests, execute_probe_refs

    _clear_cache_for_tests()
    invalid = {
        "total": 1,
        "available": 1,
        "devices": [{
            "id": "device-1", "name": "PC", "os_family": "windows",
            "os_arch": "x86_64", "client_version": "1.0",
            "last_heartbeat": "2026-07-29T12:00:00Z", "available": True,
            "unattested_metric": 72,
        }],
    }
    capsule = execute_probe_refs(
        ("owned_device_state",), principal=_principal("nested-owner"),
        lang="it", injected={"owned_device_state": invalid})[0]
    assert capsule.status == "unavailable"
    assert capsule.redactions == ("invalid_payload",)


def test_f3_scheduler_probe_reports_loop_cause_not_job_recency(monkeypatch):
    from tutor.probes import ProbeContext, _scheduler_health_payload

    monkeypatch.setattr("scheduler_v2.health.snapshot", lambda: {
        "component": "scheduler_v2",
        "cohost": "http",
        "state": "running",
        "healthy": True,
        "reason_code": "loop_active",
        "heartbeat_at": "2026-07-29T12:00:00Z",
        "heartbeat_age_s": 0.2,
        "jobs_total": 15,
        "jobs_enabled": 9,
        "jobs_running": 0,
        "last_run_at": "",
        "last_run_status": "",
    })
    payload = _scheduler_health_payload(
        ProbeContext(_principal(audience="instance_admin"), "it"))
    assert payload.facts["state"] == "running"
    assert payload.facts["healthy"] is True
    assert payload.facts["reason_code"] == "loop_active"
    # No recent job is needed to attest that the loop itself is alive.
    assert payload.facts["last_run_at"] == ""


def test_f3_operational_mode_never_reaches_retrieval_or_probes(monkeypatch):
    from tutor.service import answer_request

    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda *_a, **_k: ModeDecision("ACT", True))
    monkeypatch.setattr(
        "tutor.service.retrieve_sources",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("retrieval crossed the ACT gate")),
    )
    monkeypatch.setattr(
        "tutor.probes.execute_probe_refs",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("probe crossed the ACT gate")),
    )
    assert answer_request(TutorRequest(
        "riavvia il servizio", "it", _principal())) is None


def test_published_document_identity_routes_read_but_not_mutation(monkeypatch):
    from published_docs import PublishedDocument
    from tutor.compose import Composition
    from tutor.semantic import SemanticContext, SourceHit
    from tutor.service import answer_request
    from tutor.sources import KnowledgeUnit

    document = PublishedDocument(
        path=Path("/published/it/Guide.html"),
        relative_path="it/Guide.html", lang="it",
        canonical_url="https://metnos.com/it/guide",
        concept_key="https://metnos.com/guide",
    )
    unit = KnowledgeUnit(
        unit_id="doc-guide-it-0001", concept_id="doc-guide-0001",
        lang="it", audience="user", source_kind="operational",
        authority="published_documentation", priority=95,
        title="Guida", text="Il contenuto della guida.",
        semantic="Il contenuto della guida.",
        source_ref="docs/it/Guide.html#1", content_hash="sha256:guide",
        public_url=document.canonical_url,
    )
    context = SemanticContext((SourceHit(
        source_type="knowledge", source_id=unit.unit_id, lang="it",
        score=0.01, unit=unit,
    ),), top_score=0.01)
    observed = {"modes": [], "retrievals": 0}

    monkeypatch.setattr("tutor.service.enabled", lambda: True)
    monkeypatch.setattr(
        "published_docs.resolve_reference",
        lambda text, **_kwargs: (
            document if "guide.html" in text.casefold() else None),
    )

    def mode(query, _lang, **kwargs):
        observed["modes"].append((query, tuple(sorted(kwargs))))
        value = "ACT" if query.casefold().startswith("cancella") else "OBSERVE"
        return ModeDecision(value, True)

    monkeypatch.setattr("tutor.mode.classify_mode_decision", mode)
    monkeypatch.setattr(
        "tutor.catalog.load_request_snapshot",
        lambda: SimpleNamespace(
            version="sha256:catalog", cards=(), units=(unit,),
            card_index=None, knowledge_index=None,
        ),
    )

    def retrieve(*_args, **kwargs):
        observed["retrievals"] += 1
        assert kwargs["units"] == (unit,)
        assert kwargs["required_source_ref"] == "docs/it/Guide.html"
        return context

    monkeypatch.setattr("tutor.service.retrieve_sources", retrieve)
    monkeypatch.setattr(
        "tutor.compose.compose_answer",
        lambda **_kwargs: Composition("answer", "La guida tratta questi temi."),
    )

    answer = answer_request(TutorRequest(
        "Cosa contiene il file Guide.html?", "it", _principal()))
    action = answer_request(TutorRequest(
        "Cancella il file Guide.html", "it", _principal()))

    assert answer is not None
    assert answer.detection == "published_document_reference"
    assert answer.source_ids == (f"knowledge:{unit.unit_id}",)
    assert answer.score_band == "high"
    assert action is None
    assert observed["retrievals"] == 1
    assert observed["modes"] == [
        ("Cosa contiene il file Guide.html?",
         ("conversation_context", "deadline_at")),
        ("Cancella il file Guide.html",
         ("conversation_context", "deadline_at")),
    ]


def test_f3_mixed_split_is_literal_and_fails_closed(monkeypatch):
    from tutor.handoff import split_mixed_query

    query = "Spiegami il servizio e poi riavvialo"
    monkeypatch.setattr(
        "compound_decomposer.split_query_chunks",
        lambda _query: ["Spiegami il servizio", "riavvialo"],
    )
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda chunk, *_a, **_k: ModeDecision(
            "EXPLAIN" if chunk.startswith("Spiegami") else "ACT", True),
    )
    split = split_mixed_query(query, "it")
    assert split is not None
    assert split.explanation == "Spiegami il servizio"
    assert split.action == "riavvialo"
    assert split.explanation_decision.mode == "EXPLAIN"

    query = "Come funziona la pagina Servizi? Poi cerca README.md"
    monkeypatch.setattr(
        "compound_decomposer.split_query_chunks",
        lambda _query: ["Come funziona la pagina Servizi?", "cerca README.md"],
    )
    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda chunk, *_a, **_k: ModeDecision(
            "EXPLAIN" if chunk.startswith("Come funziona") else "OBSERVE",
            True,
        ),
    )
    split = split_mixed_query(query, "it")
    assert split is not None
    assert split.explanation == "Come funziona la pagina Servizi?"
    assert split.action == "cerca README.md"

    monkeypatch.setattr(
        "compound_decomposer.split_query_chunks",
        lambda _query: ["testo riscritto", "riavvialo"],
    )
    assert split_mixed_query(query, "it") is None
    monkeypatch.setattr(
        "compound_decomposer.split_query_chunks",
        lambda _query: ["Spiegami il servizio", "riavvialo", "subito"],
    )
    assert split_mixed_query(query, "it") is None


def test_f3_mixed_split_uses_literal_sentence_boundaries(monkeypatch):
    """A strong punctuation boundary is a general clause boundary.

    This exercises the real shared splitter: the handoff must not depend on a
    language-specific connector or on a generated rewrite of either clause.
    """

    from compound_decomposer import split_query_chunks
    from tutor.handoff import split_mixed_query

    query = "Come funziona l'archiviazione? Archivia i messaggi precedenti"
    assert split_query_chunks(query) == [
        "Come funziona l'archiviazione?",
        "Archivia i messaggi precedenti",
    ]
    assert split_query_chunks(
        "How does archiving work? Archive the previous messages") == [
            "How does archiving work?",
            "Archive the previous messages",
        ]
    # A question-mark inside one token is not a sentence boundary.
    assert split_query_chunks(
        "Leggi https://example.test/?q=uno e riassumi") == [
            "Leggi https://example.test/?q=uno",
            "riassumi",
        ]

    monkeypatch.setattr(
        "tutor.mode.classify_mode_decision",
        lambda chunk, *_a, **_k: ModeDecision(
            "EXPLAIN" if chunk.endswith("?") else "ACT", True),
    )
    split = split_mixed_query(query, "it")
    assert split is not None
    assert split.explanation == "Come funziona l'archiviazione?"
    assert split.action == "Archivia i messaggi precedenti"


def _create_handoff(monkeypatch, tmp_path: Path, *, sender: str = "sender-a",
                    catalog: str = "catalog-v1"):
    import dialog_pending
    import tutor.handoff as handoff

    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    monkeypatch.setattr(handoff, "_msg", lambda key: key)
    answer = TutorAnswer(
        esito="handoff", answer_md="Spiegazione.",
        handoff_query="riavvia il servizio",
    )
    created = handoff.create_pending(
        sender_id=sender,
        principal=_principal(channel="telegram"),
        action_query="riavvia il servizio",
        catalog_version=catalog,
        answer=answer,
    )
    state = dialog_pending.load_pending(
        sender, created.pending_dialog_id, owner_user_id="user-a")
    return created, state


def test_f3_handoff_pending_is_owner_bound_expiring_and_not_replaced(
        monkeypatch, tmp_path):
    import dialog_pending
    import tutor.handoff as handoff

    created, state = _create_handoff(monkeypatch, tmp_path)
    assert created.handoff_created is True
    assert created.pending_dialog_id
    assert state["owner_user_id"] == "user-a"
    assert state["conversation_id"] == "conv-a"
    assert state["on_complete"]["literal_query"] == "riavvia il servizio"
    assert state["on_complete"]["query_hash"] == hashlib.sha256(
        b"riavvia il servizio").hexdigest()
    assert 60 <= state["timeout_s"] <= 600
    pending_path = (dialog_pending.DIALOG_DIR / "sender-a" /
                    f"{created.pending_dialog_id}.json")
    assert os.stat(pending_path).st_mode & 0o777 == 0o600

    second = handoff.create_pending(
        sender_id="sender-a", principal=_principal(channel="telegram"),
        action_query="elimina tutto", catalog_version="catalog-v1",
        answer=TutorAnswer(esito="handoff", answer_md="Seconda."),
    )
    assert second.handoff_created is False
    assert len(dialog_pending.list_pending(
        "sender-a", owner_user_id="user-a")) == 1


def test_f3_handoff_executes_exact_literal_once(monkeypatch, tmp_path):
    import agent_runtime
    import dialog_pending
    import orchestration

    created, state = _create_handoff(monkeypatch, tmp_path)
    consumed = dialog_pending.consume_pending_step(
        "sender-a", created.pending_dialog_id, "decision", "1",
        owner_user_id="user-a")
    assert consumed["completed"] is True
    calls = []
    monkeypatch.setattr(
        "tutor.catalog.admitted_catalog_version", lambda: "catalog-v1")
    monkeypatch.setattr(
        "users.get_user",
        lambda _owner: {
            "id": "user-a", "name": "alice",
            "autonomy_level": "supervised",
        },
    )
    monkeypatch.setattr(
        "users.get_channel",
        lambda _owner, _channel: {
            "recipient_id": "sender-a", "verified_at": "2026-07-29",
        },
    )
    monkeypatch.setattr(
        "users.find_user_by_recipient",
        lambda _channel, _recipient: {"id": "user-a"},
    )
    monkeypatch.setattr(
        "pairing.get_pairing",
        lambda _channel, _sender: SimpleNamespace(
            autonomy_level="supervised"),
    )
    monkeypatch.setattr(
        agent_runtime,
        "run_turn",
        lambda query, **kwargs: calls.append((query, kwargs)) or SimpleNamespace(
            final_message="Operazione conclusa.", attachments=[],
            turn_id="turn-operation", ts_start=1.0, ts_end=1.1, steps=[],
            target_device="",
        ),
    )
    monkeypatch.setattr(
        orchestration, "_msg",
        lambda key, **kwargs: key.format(**kwargs) if kwargs else key,
    )
    first = orchestration.process_completion_callback(
        "sender-a", created.pending_dialog_id,
        actor="alice", channel="telegram", owner_user_id="user-a",
    )
    second = orchestration.process_completion_callback(
        "sender-a", created.pending_dialog_id,
        actor="alice", channel="telegram", owner_user_id="user-a",
    )
    assert first.text == "Operazione conclusa."
    assert calls == [("riavvia il servizio", {
        "actor": "alice", "channel": "telegram",
        "conversation_id": "conv-a",
        "owner_user_id": "user-a",
    })]
    # The durable outbox replays the committed result without executing the
    # literal action a second time.
    assert second.text == "Operazione conclusa."


def test_f3_handoff_rejects_state_tampering_before_claim(monkeypatch, tmp_path):
    import agent_runtime
    import dialog_pending
    import orchestration

    created, state = _create_handoff(monkeypatch, tmp_path)
    state["conversation_id"] = "other-conversation"
    dialog_pending.save_pending("sender-a", created.pending_dialog_id, state)
    dialog_pending.consume_pending_step(
        "sender-a", created.pending_dialog_id, "decision", "execute",
        owner_user_id="user-a")
    monkeypatch.setattr(
        "tutor.catalog.admitted_catalog_version", lambda: "catalog-v1")
    monkeypatch.setattr(
        agent_runtime, "run_turn",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("tampered handoff executed")),
    )
    monkeypatch.setattr(orchestration, "_msg", lambda key, **_k: key)
    result = orchestration.process_completion_callback(
        "sender-a", created.pending_dialog_id, owner_user_id="user-a")
    assert result.text == "MSG_TUTOR_HANDOFF_INVALID"


def test_f3_handoff_rechecks_current_read_only_autonomy(monkeypatch, tmp_path):
    import agent_runtime
    import dialog_pending
    import orchestration

    created, _state = _create_handoff(monkeypatch, tmp_path)
    dialog_pending.consume_pending_step(
        "sender-a", created.pending_dialog_id, "decision", "execute",
        owner_user_id="user-a")
    monkeypatch.setattr(
        "tutor.catalog.admitted_catalog_version", lambda: "catalog-v1")
    monkeypatch.setattr(
        "users.get_user",
        lambda _owner: {"autonomy_level": "read_only"},
    )
    monkeypatch.setattr("pairing.get_pairing", lambda *_a: None)
    monkeypatch.setattr(
        agent_runtime, "run_turn",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("read-only handoff executed")),
    )
    monkeypatch.setattr(
        orchestration, "_msg",
        lambda key, **kwargs: f"{key}:{kwargs.get('level', '')}",
    )
    result = orchestration.process_completion_callback(
        "sender-a", created.pending_dialog_id, owner_user_id="user-a")
    assert result.text == "MSG_LEVEL_BLOCKED:read_only"


def test_f3_http_invalid_choice_reprompts_instead_of_becoming_a_turn(
        monkeypatch, tmp_path):
    import dialog_pending
    import http_routes_agent

    _created, _state = _create_handoff(monkeypatch, tmp_path)
    monkeypatch.setattr(
        http_routes_agent, "_msg",
        lambda key, **kwargs: f"{key}:{kwargs.get('err', '')}",
    )
    reply = http_routes_agent._apply_dialog_pending(
        "sender-a", "una nuova operazione non confermata",
        actor="alice", channel="telegram", conversation_id="conv-a",
        owner_user_id="user-a",
    )
    assert reply.startswith("MSG_DIALOG_STEP_REPROMPT:")
    assert "MSG_TUTOR_HANDOFF_CONTINUE" in reply
    assert len(dialog_pending.list_pending(
        "sender-a", owner_user_id="user-a")) == 1


def _stores(monkeypatch, tmp_path):
    import tutor.associations as associations
    import tutor.gaps as gaps

    unified = tmp_path / "learning.sqlite"
    monkeypatch.setattr(associations, "STORE_PATH", unified)
    monkeypatch.setattr(gaps, "STORE_PATH", unified)
    return associations, gaps


def _learning_answer(turn_id: str, *, gap_reason: str = "",
                     eligible: bool = True, vector=(1.0, 0.0)) -> TutorAnswer:
    return TutorAnswer(
        esito="fondata",
        answer_md="Risposta.",
        turn_id=turn_id,
        source_ids=("knowledge:unit-a",),
        gap_reason=gap_reason,
        evidence=TutorEvidence(
            query_vector=tuple(vector),
            embedding_fingerprint="embed-v1",
            catalog_version="catalog-v1",
            primary_source_id="knowledge:unit-a",
            primary_content_hash="unit-hash-v1",
            eligible_for_association=eligible,
        ),
    )


def _feedback_turn(turn_id: str, owner: str, query: str) -> dict:
    from tutor.associations import query_hash

    return {
        "mode": "tutor",
        "turn_id": turn_id,
        "owner_user_id": owner,
        "actor": owner,
        "tutor_query_hash": query_hash(query),
        "tutor_source_ids": ["knowledge:unit-a"],
    }


def test_f4_feedback_is_private_audience_bounded_and_last_write_wins(
        monkeypatch, tmp_path):
    associations, gaps = _stores(monkeypatch, tmp_path)
    query = "Come funziona la pagina dei servizi?"
    request = TutorRequest(
        query, "it", _principal(audience="instance_admin"))
    gaps.record_turn(request, _learning_answer("turn-a"))
    turn = _feedback_turn("turn-a", "user-a", query)

    promoted = gaps.apply_feedback(turn, "ok")
    assert promoted[0]["type"] == "tutor_association_promoted"
    vector = np.asarray([1.0, 0.0], dtype=np.float32)
    known = {"unit-a": "unit-hash-v1"}
    assert associations.match(
        vector, "embed-v1", known, owner_user_id="user-a",
        audience="instance_admin")
    assert associations.match(
        vector, "embed-v1", known, owner_user_id="user-b",
        audience="instance_admin") == ()
    assert associations.match(
        vector, "embed-v1", known, owner_user_id="user-a",
        audience="user") == ()

    gaps.apply_feedback(turn, "error")
    gaps.apply_feedback(turn, "error")
    assert associations.match(
        vector, "embed-v1", known, owner_user_id="user-a",
        audience="instance_admin") == ()
    negative = [group for group in gaps.debt_map(owner_user_id="user-a")
                if group["reason"] == "feedback_negative"]
    assert len(negative) == 1
    assert negative[0]["count"] == 1

    # The evidence remains for its bounded TTL, so a corrected verdict wins.
    gaps.apply_feedback(turn, "ok")
    assert associations.match(
        vector, "embed-v1", known, owner_user_id="user-a",
        audience="instance_admin")
    assert not [group for group in gaps.debt_map(owner_user_id="user-a")
                if group["reason"] == "feedback_negative"]


def test_f4_store_never_persists_clear_query_and_expiry_blocks_learning(
        monkeypatch, tmp_path):
    associations, gaps = _stores(monkeypatch, tmp_path)
    query = "frase privata riconoscibile soltanto in questo test"
    request = TutorRequest(query, "it", _principal())
    gaps.record_turn(request, _learning_answer("turn-expiring"))
    assert query.encode("utf-8") not in gaps.STORE_PATH.read_bytes()
    with gaps._connect() as connection:
        connection.execute(
            "UPDATE turn_evidence SET expires=0 WHERE turn_id=?",
            ("turn-expiring",),
        )
    gaps.apply_feedback(
        _feedback_turn("turn-expiring", "user-a", query), "ok")
    assert associations.list_rows(owner_user_id="user-a") == ()


def test_f4_source_change_invalidates_association(monkeypatch, tmp_path):
    associations, gaps = _stores(monkeypatch, tmp_path)
    query = "Dove vedo i servizi?"
    gaps.record_turn(
        TutorRequest(query, "it", _principal()),
        _learning_answer("turn-source"),
    )
    gaps.apply_feedback(
        _feedback_turn("turn-source", "user-a", query), "ok")
    vector = np.asarray([1.0, 0.0], dtype=np.float32)
    assert associations.match(
        vector, "embed-v1", {"unit-a": "changed-hash"},
        owner_user_id="user-a") == ()
    assert associations.list_rows(owner_user_id="user-a") == ()


def test_f4_debt_map_groups_semantic_and_exact_hash_recurrence(
        monkeypatch, tmp_path):
    _associations, gaps = _stores(monkeypatch, tmp_path)
    for turn_id, query, vector in (
            ("gap-1", "Domanda uno", (1.0, 0.0)),
            ("gap-2", "Domanda simile", (0.999, 0.02))):
        gaps.record_turn(
            TutorRequest(query, "it", _principal()),
            _learning_answer(
                turn_id, gap_reason="composer_incomplete", vector=vector),
        )
    no_evidence = TutorAnswer(
        esito="lacuna", answer_md="Lacuna.",
        gap_reason="no_source", turn_id="gap-3")
    gaps.record_turn(
        TutorRequest("Stessa domanda", "it", _principal()), no_evidence)
    gaps.record_turn(
        TutorRequest("Stessa domanda", "it", _principal()),
        TutorAnswer(
            esito="lacuna", answer_md="Lacuna.",
            gap_reason="no_source", turn_id="gap-4"),
    )
    groups = gaps.debt_map(owner_user_id="user-a")
    assert any(group["reason"] == "composer_incomplete"
               and group["count"] == 2 for group in groups)
    assert any(group["reason"] == "no_source"
               and group["count"] == 2 for group in groups)


def test_f4_purge_removes_only_the_requested_owner(monkeypatch, tmp_path):
    associations, gaps = _stores(monkeypatch, tmp_path)
    for owner, turn_id in (("user-a", "purge-a"), ("user-b", "purge-b")):
        query = f"Domanda {owner}"
        gaps.record_turn(
            TutorRequest(query, "it", _principal(owner, actor=owner)),
            _learning_answer(turn_id),
        )
        gaps.apply_feedback(_feedback_turn(turn_id, owner, query), "ok")
    deleted = gaps.purge_owner(owner_user_id="user-a")
    assert deleted["turn_evidence"] == 1
    assert deleted["associations"] == 1
    assert associations.list_rows(owner_user_id="user-a") == ()
    assert len(associations.list_rows(owner_user_id="user-b")) == 1


def test_f4_counterfactual_reports_corrupt_vectors_instead_of_crashing(
        monkeypatch):
    from tutor.counterfactual import replay_associations

    monkeypatch.setattr(
        "tutor.catalog.load_knowledge_units",
        lambda: (SimpleNamespace(
            unit_id="unit-a", content_hash="unit-hash-v1"),),
    )
    monkeypatch.setattr(
        "tutor.catalog.load_knowledge_vector_index",
        lambda: SimpleNamespace(
            refs=(("unit-a", "it"),),
            matrix=np.asarray([[1.0, 0.0]], dtype=np.float32),
            dimension=2,
            fingerprint="embed-v1",
        ),
    )
    monkeypatch.setattr(
        "tutor.associations.list_rows",
        lambda **_kwargs: ({
            "query_hash": "hash-a",
            "unit_id": "unit-a",
            "unit_hash": "unit-hash-v1",
            "fingerprint": "embed-v1",
            "vector": b"bad",
        },),
    )
    report = replay_associations(owner_user_id="user-a")
    assert report["failed"] == 1
    assert report["failures"][0]["reason"] == "vector_encoding_invalid"
