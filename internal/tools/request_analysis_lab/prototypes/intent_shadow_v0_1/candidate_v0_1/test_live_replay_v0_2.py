#!/usr/bin/env python3
"""Finite offline tests for replay gate/evaluator 0.2; gold is never opened."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from live_replay_gate_v0_2 import (
    ALLOWED_POINTER,
    ALLOWED_RECORD_IDENTITY,
    ALLOWED_RECORD_INDEX,
    BATCH_PATH,
    FREEZE_PATH,
    FROZEN_SOURCE_FILES,
    SEAL_PATH,
    compare_saved_and_replay,
    verify_v0_2_freeze,
)


HERE = Path(__file__).resolve().parent


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _document(value: bool = False) -> dict[str, Any]:
    return {
        "contract_version": "metnos.intent-shadow/0.1",
        "status": "valid_unrepresentable",
        "decoded_document": {"kind": "unrepresentable", "reason": "outside_registry"},
        "projection_result": {
            "semantic_document": {"kind": "unrepresentable", "reason": "outside_registry"},
            "semantic_sha256": "1" * 64,
        },
        "adapter_metadata": {
            "primary_response_consumed": True,
            "current_result_present": True,
            "implicit_actions_ignored": value,
            "semantic_document_source": "current_extractor_result_plus_registry_adapter",
        },
    }


def _accepted(saved: dict[str, Any], replay: dict[str, Any], index: int = ALLOWED_RECORD_INDEX) -> dict[str, Any] | None:
    return compare_saved_and_replay(
        saved,
        replay,
        record_index=index,
        identity=dict(ALLOWED_RECORD_IDENTITY),
    )


def _rejected(function: Callable[[], Any]) -> None:
    try:
        function()
    except RuntimeError:
        return
    raise AssertionError("mutation accepted")


def exact_match_is_accepted() -> None:
    value = _document(False)
    _assert(_accepted(value, deepcopy(value)) is None, "exact match")


def false_true_is_visible_and_accepted() -> None:
    mismatch = _accepted(_document(False), _document(True))
    _assert(type(mismatch) is dict, "false/true mismatch missing")
    _assert(mismatch["saved"] is False and mismatch["replay"] is True, "false/true values")
    _assert(mismatch["json_pointer"] == ALLOWED_POINTER, "pointer")


def true_false_is_visible_and_accepted() -> None:
    mismatch = _accepted(_document(True), _document(False))
    _assert(type(mismatch) is dict, "true/false mismatch missing")
    _assert(mismatch["saved"] is True and mismatch["replay"] is False, "true/false values")


def missing_field_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["adapter_metadata"].pop("implicit_actions_ignored")
    _rejected(lambda: _accepted(saved, replay))


def non_boolean_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["adapter_metadata"]["implicit_actions_ignored"] = 1
    _rejected(lambda: _accepted(saved, replay))


def other_metadata_change_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["adapter_metadata"]["primary_response_consumed"] = False
    _rejected(lambda: _accepted(saved, replay))


def semantic_change_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["projection_result"]["semantic_document"]["reason"] = "missing_required_information"
    _rejected(lambda: _accepted(saved, replay))


def two_differences_are_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["status"] = "technical_invalid"
    _rejected(lambda: _accepted(saved, replay))


def other_record_index_is_rejected() -> None:
    _rejected(lambda: _accepted(_document(False), _document(True), ALLOWED_RECORD_INDEX + 1))


def freeze_is_closed_and_gold_free() -> None:
    freeze = verify_v0_2_freeze(FREEZE_PATH)
    _assert(freeze["oracle_opened"] is False, "freeze oracle state")
    _assert(freeze["evaluation_executed"] is False, "freeze evaluation state")
    lowered = "\n".join(FROZEN_SOURCE_FILES).casefold()
    _assert("intent_shadow_oracle" not in lowered and "/oracles/" not in lowered, "gold source in pre-gold freeze")
    import live_evaluator_v0_2 as evaluator

    gold_called = False
    phase1_called = False
    original_gold = evaluator._verified_gold
    original_phase1 = evaluator._verified_phase1_after_gate

    def forbidden_gold() -> dict[str, Any]:
        nonlocal gold_called
        gold_called = True
        raise AssertionError("gold reached before gate")

    def forbidden_phase1() -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal phase1_called
        phase1_called = True
        raise AssertionError("Phase-1 gold reached before gate")

    evaluator._verified_gold = forbidden_gold
    evaluator._verified_phase1_after_gate = forbidden_phase1
    try:
        try:
            evaluator.evaluate(freeze_path=HERE / "missing-v0-2-freeze.json")
        except Exception:
            pass
        else:
            raise AssertionError("missing gate freeze accepted")
    finally:
        evaluator._verified_gold = original_gold
        evaluator._verified_phase1_after_gate = original_phase1
    _assert(not gold_called and not phase1_called, "gold boundary crossed on failed gate")


def _subprocess_gate(seed: int) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(seed)
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(HERE / "live_replay_gate_v0_2.py"),
            "--batch", str(BATCH_PATH),
            "--seal", str(SEAL_PATH),
            "--freeze", str(FREEZE_PATH),
        ],
        cwd=HERE,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=180,
    )
    _assert(completed.returncode == 0, f"subprocess seed {seed} failed")
    report = json.loads(completed.stdout)
    _assert(report["status"] == "pass", f"subprocess seed {seed} status")
    _assert(report["records_verified"] == 316, f"subprocess seed {seed} count")
    _assert(report["unexpected_mismatch_count"] == 0, f"subprocess seed {seed} unexpected")
    _assert(report["oracle_opened"] is False, f"subprocess seed {seed} oracle")
    _assert(report["evaluation_executed"] is False, f"subprocess seed {seed} evaluation")
    return report


def full_replay_hash_seed_0() -> None:
    report = _subprocess_gate(0)
    _assert(report["allowed_mismatch_count"] == 1, "seed 0 mismatch count")
    mismatch = report["allowed_mismatches"][0]
    _assert(mismatch["record_index_zero_based"] == ALLOWED_RECORD_INDEX, "seed 0 mismatch index")
    _assert(mismatch["json_pointer"] == ALLOWED_POINTER, "seed 0 mismatch pointer")
    _assert(mismatch["saved"] is False and mismatch["replay"] is True, "seed 0 mismatch values")


def full_replay_hash_seed_2() -> None:
    report = _subprocess_gate(2)
    _assert(report["allowed_mismatch_count"] == 0, "seed 2 mismatch count")
    _assert(report["allowed_mismatches"] == [], "seed 2 mismatch details")


def main() -> int:
    tests = (
        ("exact_match_is_accepted", exact_match_is_accepted),
        ("false_true_is_visible_and_accepted", false_true_is_visible_and_accepted),
        ("true_false_is_visible_and_accepted", true_false_is_visible_and_accepted),
        ("missing_field_is_rejected", missing_field_is_rejected),
        ("non_boolean_is_rejected", non_boolean_is_rejected),
        ("other_metadata_change_is_rejected", other_metadata_change_is_rejected),
        ("semantic_change_is_rejected", semantic_change_is_rejected),
        ("two_differences_are_rejected", two_differences_are_rejected),
        ("other_record_index_is_rejected", other_record_index_is_rejected),
        ("freeze_is_closed_and_gold_free", freeze_is_closed_and_gold_free),
        ("full_replay_hash_seed_0", full_replay_hash_seed_0),
        ("full_replay_hash_seed_2", full_replay_hash_seed_2),
    )
    failures: list[dict[str, str]] = []
    for name, function in tests:
        try:
            function()
        except Exception as exc:
            failures.append({"name": name, "error": f"{type(exc).__name__}:{exc}"})
    print(json.dumps({
        "status": "ok" if not failures else "error",
        "passed": len(tests) - len(failures),
        "total": len(tests),
        "failures": failures,
        "oracle_opened": False,
        "evaluation_executed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
