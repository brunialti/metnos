"""Before the migration, F5 changes nothing an installation can observe.

This is the release argument, asserted rather than reasoned. Every F5 path is
reachable in the shipped code and every one of them, on an installation that
has no migration marker, does exactly what the previous code did and touches
exactly the stores it touched before. If one of these fails, the release is not
safe to ship dormant, whatever the rest of the suite says.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import executor_birth_activation_mode as mode
import executor_lifecycle_state as state


@pytest.fixture(autouse=True)
def unmigrated(monkeypatch):
    """The real state of an installation that has not been cut over."""
    monkeypatch.setattr(state, "read_birth_activation_state",
                        lambda: mode.BirthActivationState(
                            mode.BirthStateOwner.LEGACY, None, None, None))
    monkeypatch.setattr(state, "_epoch_snapshot",
                        lambda: pytest.fail("the epoch store was opened"))
    monkeypatch.setattr(state, "_epoch_db_path",
                        lambda: pytest.fail("the epoch store was located"))


def executor(name="demo", source="synthesized"):
    return SimpleNamespace(name=name, source=source, lifecycle="active",
                           contract_id="user:demo/manifest.toml",
                           generation_id="sha256:" + "a" * 64)


def test_an_installation_without_a_marker_never_opens_the_certificate(monkeypatch):
    """The optional authority stays optional: F4 startup does not read it."""
    monkeypatch.setattr(mode, "load_f5_activation",
                        lambda: pytest.fail("the certificate was opened"))
    observed = mode.read_birth_activation_state()
    assert observed.owner is mode.BirthStateOwner.LEGACY
    assert observed.migration_id is None


def test_restrictions_come_from_the_name_based_store(monkeypatch):
    monkeypatch.setattr("executor_aging.lifecycle_override_map",
                        lambda *, read_only: {"gone": "archived", "old": "deprecated"})
    assert state.restriction_snapshot() == (
        ("gone", "", "archived", "inactive too long"),
        ("old", "", "deprecated", "deprecated"),
    )


def test_registration_uses_the_previous_source_tags(monkeypatch):
    seen = []
    monkeypatch.setattr("executor_aging.register",
                        lambda name, *, source: seen.append((name, source)))
    state.register_loaded_executors((
        executor("a", "imported"), executor("b", "synthesized"),
        executor("c", "handcrafted"), executor("d", "anything else"),
    ))
    assert seen == [("a", "skill"), ("b", "synth:reactive"),
                    ("c", "handcrafted"), ("d", "handcrafted")]


def test_invocation_accounting_still_counts_by_name(monkeypatch):
    seen = []
    monkeypatch.setattr("executor_aging.record_invocation",
                        lambda name, *, ok: seen.append((name, ok)))
    state.record_invocation(executor(), ok=True)
    state.record_invocation(executor(), ok=False)
    state.record_invocation(executor(), ok=None)
    assert seen == [("demo", True), ("demo", False), ("demo", None)]


def test_the_verdict_counter_stays_absent():
    """The legacy store has no such column, and none is invented for it."""
    assert state.record_verdict(SimpleNamespace(
        contract_id=None, generation_id=None), positive=False) is None


def test_the_inactivity_decision_is_the_previous_one(monkeypatch):
    seen = {}
    monkeypatch.setattr("executor_aging.apply_executor_ager",
                        lambda **kwargs: seen.update(kwargs) or {"deprecated": []})
    state.apply_inactivity_decay(deprecate_days=30, archive_days=14,
                                 now_iso="2026-09-17T00:00:00Z", catalog_names=["a"])
    assert seen == {"deprecate_days": 30, "archive_days": 14,
                    "now_iso": "2026-09-17T00:00:00Z", "catalog_names": ["a"]}


def test_inherited_demand_still_reaches_the_name(monkeypatch):
    seen = []
    monkeypatch.setattr("executor_aging.touch", lambda name: seen.append(name))
    assert state.credit_uses("demo", 3) == 3
    assert seen == ["demo"] * 3


def test_provenance_still_comes_from_the_name_based_store(monkeypatch):
    monkeypatch.setattr("executor_aging.lookup",
                        lambda name: SimpleNamespace(source="synth:reactive"))
    assert state.recorded_source("demo") == "synth:reactive"


def test_the_durable_service_composes_without_a_guard(monkeypatch):
    """LRE runs exactly as it did: no attestation, no new refusal."""
    import executor_birth_durable_guard as guard

    monkeypatch.setattr("executor_birth_activation_mode.read_birth_activation_state",
                        lambda: mode.BirthActivationState(
                            mode.BirthStateOwner.LEGACY, None, None, None))
    assert guard.productive_birth_attempt_guard() is None


def test_the_nightly_review_finds_no_outbox_and_touches_nothing(monkeypatch):
    from jobs import birth_failure_reviews as job

    monkeypatch.setattr("executor_birth_activation_mode.read_birth_activation_state",
                        lambda: mode.BirthActivationState(
                            mode.BirthStateOwner.LEGACY, None, None, None))
    monkeypatch.setattr("executor_birth_failure_review.review_failure_once",
                        lambda *_a, **_k: pytest.fail("a review ran"))
    assert job.task_birth_failure_reviews()["status"] == "no_outbox"


def test_user_feedback_keeps_the_previous_effect(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("METNOS_EXECUTOR_STATS_DB", str(tmp_path / "stats.db"))
    monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "1")
    import executor_aging
    importlib.reload(executor_aging)
    monkeypatch.setattr(executor_aging, "EFFICACY_AUDIT_DIR", tmp_path / "audit")
    import turn_feedback
    importlib.reload(turn_feedback)
    monkeypatch.setattr(turn_feedback, "FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    turns = tmp_path / "turns"
    turns.mkdir()
    monkeypatch.setattr(turn_feedback, "TURNS_DIR", turns)
    executor_aging.register("demo", source="synth:reactive")
    import json
    (turns / "today.jsonl").write_text(json.dumps({
        "turn_id": "t0", "user_query": "q",
        "steps": [{"chosen_tool": "demo"}, {"chosen_tool": "final_answer"}],
    }) + "\n")
    monkeypatch.setattr("executor_birth_lifecycle.apply_execution_failure",
                        lambda *_a, **_k: pytest.fail("the F5 feedback entry ran"))
    effects = turn_feedback.apply_feedback("t0", "error")["effects"]
    assert any(item.get("type") == "feedback_demote" and item.get("action") == "demoted"
               for item in effects)
    assert executor_aging.lookup("demo").deprecated_at is not None
    importlib.reload(executor_aging)
    importlib.reload(turn_feedback)
