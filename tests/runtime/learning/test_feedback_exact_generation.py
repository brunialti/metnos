"""RM-0008 F5: the one ✗ threshold reaches the store that owns the lifecycle.

A migrated installation quarantines the generation that actually ran; the
executor name never selects what is restricted, and an installation whose
owning store cannot be resolved gets neither effect.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

import executor_birth_activation_mode as mode
import executor_birth_lifecycle as lifecycle
from executor_birth_feedback import (
    FeedbackResult, FeedbackStatus, make_execution_receipt,
)
from manifest_inventory import ContractId, ManifestOrigin


TOOL = "find_evil_synth"
QUERY = "bad q"
CID = ContractId(ManifestOrigin.USER, "find_evil_synth/manifest.toml")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_EXECUTOR_STATS_DB", str(tmp_path / "executor_stats.db"))
    monkeypatch.setenv("METNOS_FEEDBACK_DEMOTE_THRESHOLD", "1")
    import executor_aging
    importlib.reload(executor_aging)
    monkeypatch.setattr(executor_aging, "EFFICACY_AUDIT_DIR", tmp_path / "audit")
    import turn_feedback
    importlib.reload(turn_feedback)
    monkeypatch.setattr(turn_feedback, "FEEDBACK_PATH", tmp_path / "turn_feedback.jsonl")
    turns = tmp_path / "turns"
    turns.mkdir()
    monkeypatch.setattr(turn_feedback, "TURNS_DIR", turns)
    executor_aging.register(TOOL, source="synth:reactive")
    yield turn_feedback, executor_aging, turns
    importlib.reload(executor_aging)
    importlib.reload(turn_feedback)


def receipt(*, executor_name=TOOL, contract_id=CID):
    return make_execution_receipt(
        request_id="sha256:" + "a" * 64, turn_id="sha256:" + "b" * 64,
        reduced_query_ref="sha256:" + "c" * 64,
        arguments={"pattern": "*"}, reduced_output={"ok": False},
        contract_id=contract_id, executor_name=executor_name,
        generation_id="sha256:" + "d" * 64, candidate_id="sha256:" + "e" * 64,
        dispatched_at="2026-09-16T10:00:00Z", completed_at="2026-09-16T10:00:01Z",
    )


def write_turn(turns: Path, turn_id: str, *, execution_receipt=None):
    step = {"chosen_tool": TOOL, "canonical_query": QUERY,
            "llm_in_tokens": 1000, "llm_latency_ms": 500}
    if execution_receipt is not None:
        step["execution_receipt"] = json.loads(
            json.dumps(asdict(execution_receipt), default=str))
    (turns / "today.jsonl").open("a", encoding="utf-8").write(json.dumps({
        "turn_id": turn_id, "user_query": QUERY,
        "steps": [step, {"chosen_tool": "final_answer"}],
    }) + "\n")


def owned_by(monkeypatch, owner):
    monkeypatch.setattr(mode, "read_birth_activation_state", lambda: mode.BirthActivationState(
        owner, "sha256:" + "2" * 64 if owner is mode.BirthStateOwner.EPOCH else None,
        None, None))


def test_a_migrated_installation_quarantines_the_exact_generation(env, monkeypatch):
    turn_feedback, aging, turns = env
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    exact = receipt()
    seen = {}

    def apply_execution_failure(value, *, failure_evidence_hash, error_code):
        seen.update(receipt=value, evidence=failure_evidence_hash, code=error_code)
        return FeedbackResult(FeedbackStatus.QUARANTINED, value.receipt_id, "sha256:" + "f" * 64, True)

    monkeypatch.setattr(lifecycle, "apply_execution_failure", apply_execution_failure)
    write_turn(turns, "t0", execution_receipt=exact)
    effects = turn_feedback.apply_feedback("t0", "error")["effects"]
    quarantine = [effect for effect in effects if effect["type"] == "feedback_quarantine"]
    assert quarantine == [{
        "type": "feedback_quarantine", "tool": TOOL, "status": "quarantined",
        "receipt_id": exact.receipt_id, "failure_job_id": "sha256:" + "f" * 64,
        "quarantine_applied": True,
    }]
    assert seen["receipt"] == exact
    assert seen["code"] == "user_feedback_error"
    assert seen["evidence"].startswith("sha256:")
    # The name-based store keeps its earlier decision untouched.
    assert aging.lookup(TOOL).deprecated_at is None


def test_the_evidence_hash_binds_this_turn_and_this_receipt(env, monkeypatch):
    turn_feedback, _aging, turns = env
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    exact = receipt()
    observed = []
    monkeypatch.setattr(lifecycle, "apply_execution_failure",
                        lambda value, *, failure_evidence_hash, error_code: (
                            observed.append(failure_evidence_hash),
                            FeedbackResult(FeedbackStatus.QUARANTINED, value.receipt_id, None, True),
                        )[1])
    for turn_id in ("t0", "t1"):
        write_turn(turns, turn_id, execution_receipt=exact)
        turn_feedback.apply_feedback(turn_id, "error")
    assert len(observed) == 2 and observed[0] != observed[1]


def test_a_step_without_a_receipt_is_reported_not_demoted_by_name(env, monkeypatch):
    turn_feedback, aging, turns = env
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr(lifecycle, "apply_execution_failure",
                        lambda *_a, **_k: pytest.fail("quarantine without a receipt"))
    write_turn(turns, "t0")
    effects = turn_feedback.apply_feedback("t0", "error")["effects"]
    assert {"type": "feedback_quarantine", "tool": TOOL,
            "status": "no_execution_receipt"} in effects
    assert aging.lookup(TOOL).deprecated_at is None


def test_a_forged_receipt_record_never_reaches_the_publisher(env, monkeypatch):
    turn_feedback, aging, turns = env
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr(lifecycle, "apply_execution_failure",
                        lambda *_a, **_k: pytest.fail("forged receipt accepted"))
    exact = receipt()
    record = json.loads(json.dumps(asdict(exact), default=str))
    record["generation_id"] = "sha256:" + "9" * 64
    step = {"chosen_tool": TOOL, "execution_receipt": record}
    (turns / "today.jsonl").open("a", encoding="utf-8").write(json.dumps({
        "turn_id": "t0", "user_query": QUERY,
        "steps": [step, {"chosen_tool": "final_answer"}],
    }) + "\n")
    effects = turn_feedback.apply_feedback("t0", "error")["effects"]
    refused = [effect for effect in effects if effect["type"] == "feedback_quarantine"]
    assert refused and refused[0]["status"] == "refused"
    assert refused[0]["reason"] == "feedback_binding_invalid"
    assert aging.lookup(TOOL).deprecated_at is None


def test_an_unresolvable_owner_applies_neither_effect(env, monkeypatch):
    turn_feedback, aging, turns = env

    def unreadable():
        raise lifecycle.LifecycleError("f5_migration_marker_invalid", "document")

    monkeypatch.setattr(mode, "read_birth_activation_state", unreadable)
    write_turn(turns, "t0", execution_receipt=receipt())
    effects = turn_feedback.apply_feedback("t0", "error")["effects"]
    assert {"type": "feedback_demote", "action": "refused",
            "reason": "f5_migration_marker_invalid"} in effects
    assert aging.lookup(TOOL).deprecated_at is None


def test_an_unmigrated_installation_keeps_the_name_based_demote(env, monkeypatch):
    turn_feedback, aging, turns = env
    owned_by(monkeypatch, mode.BirthStateOwner.LEGACY)
    monkeypatch.setattr(lifecycle, "apply_execution_failure",
                        lambda *_a, **_k: pytest.fail("F5 feedback on a legacy owner"))
    write_turn(turns, "t0", execution_receipt=receipt())
    effects = turn_feedback.apply_feedback("t0", "error")["effects"]
    demote = [effect for effect in effects if effect["type"] == "feedback_demote"]
    assert demote and demote[0]["action"] == "demoted"
    assert aging.lookup(TOOL).deprecated_at is not None
