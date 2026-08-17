#!/usr/bin/env python3
"""Finite pre-evaluation tests for replay gate and evaluator 0.3.

These tests never open gold and never call ``evaluate`` on the sealed batch.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

from live_evaluator_v0_3 import (
    EFFECTIVE_CRITICAL_COLUMNS,
    canonical_semantic_projection,
    typed_verdict_v0_3,
)
from live_protocol import CRITICAL_NO_REGRESSION_COLUMNS
from live_replay_gate_v0_3 import (
    ALLOWED_POINTER,
    BATCH_PATH,
    FREEZE_PATH,
    FROZEN_SOURCE_FILES,
    IDENTITY_FIELDS,
    SEAL_PATH,
    compare_saved_and_replay,
    verify_v0_3_freeze,
)


HERE = Path(__file__).resolve().parent


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _identity(number: int) -> dict[str, Any]:
    return {
        "request_ordinal": number + 1,
        "sample_index": number,
        "panel": "synthetic_panel",
        "panel_ordinal": number + 1,
        "opaque_case_id": f"synthetic.{number}",
        "query_sha256": f"{number % 16:x}" * 64,
        "arm": "A" if number % 2 == 0 else "B",
        "request_sha256": f"{(number + 1) % 16:x}" * 64,
    }


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


def _accepted(saved: dict[str, Any], replay: dict[str, Any], number: int) -> dict[str, Any] | None:
    return compare_saved_and_replay(
        saved,
        replay,
        record_index=number,
        identity=_identity(number),
    )


def _rejected(function: Callable[[], Any]) -> None:
    try:
        function()
    except RuntimeError:
        return
    raise AssertionError("mutation accepted")


def replay_exact_match_is_accepted() -> None:
    value = _document(False)
    _assert(_accepted(value, deepcopy(value), 3) is None, "exact match")


def replay_false_true_is_recorded_on_one_record() -> None:
    mismatch = _accepted(_document(False), _document(True), 2)
    _assert(type(mismatch) is dict, "false/true mismatch missing")
    _assert(mismatch["saved"] is False and mismatch["replay"] is True, "false/true values")
    _assert(mismatch["json_pointer"] == ALLOWED_POINTER, "pointer")
    _assert({key: mismatch[key] for key in IDENTITY_FIELDS} == _identity(2), "identity")


def replay_true_false_is_recorded_on_another_record() -> None:
    mismatch = _accepted(_document(True), _document(False), 7)
    _assert(type(mismatch) is dict, "true/false mismatch missing")
    _assert(mismatch["saved"] is True and mismatch["replay"] is False, "true/false values")
    _assert(mismatch["record_index_zero_based"] == 7, "dynamic record")


def replay_multiple_tolerances_remain_individually_visible() -> None:
    mismatches = [
        _accepted(_document(False), _document(True), number)
        for number in (1, 4, 9)
    ]
    _assert(all(type(item) is dict for item in mismatches), "multiple tolerance")
    _assert([item["record_index_zero_based"] for item in mismatches] == [1, 4, 9], "order")


def replay_missing_field_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["adapter_metadata"].pop("implicit_actions_ignored")
    _rejected(lambda: _accepted(saved, replay, 5))


def replay_non_boolean_is_rejected() -> None:
    for value in (0, 1, "false", None):
        saved = _document(False)
        replay = _document(True)
        replay["adapter_metadata"]["implicit_actions_ignored"] = value
        _rejected(lambda saved=saved, replay=replay: _accepted(saved, replay, 6))


def replay_other_pointer_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["adapter_metadata"]["primary_response_consumed"] = False
    _rejected(lambda: _accepted(saved, replay, 8))


def replay_semantic_change_is_rejected() -> None:
    saved = _document(False)
    replay = _document(True)
    replay["projection_result"]["semantic_document"]["reason"] = "missing_required_information"
    _rejected(lambda: _accepted(saved, replay, 10))


def replay_identity_shape_is_closed() -> None:
    identity = _identity(12)
    identity["extra"] = True
    _rejected(lambda: compare_saved_and_replay(
        _document(False), _document(True), record_index=12, identity=identity
    ))


def _registry() -> dict[str, Any]:
    operation = {
        "input_ports": ["primary"],
        "output_ports": ["result"],
    }
    return {
        "operations": {
            "find/files": deepcopy(operation),
            "read/files": deepcopy(operation),
            "set/issues": deepcopy(operation),
            "delete/files": deepcopy(operation),
        },
        "barriers": {
            "get/approval": {
                "input_ports": [],
                "outcomes": ["approved", "rejected"],
            }
        },
    }


def canonical_projection_is_symmetric_for_empty_edges_and_ports() -> None:
    expected = {
        "kind": "operation_graph",
        "body": [
            {"kind": "operation", "route": "find/files"},
            {"kind": "operation", "route": "read/files", "data_from": [{"from": 0}]},
        ],
    }
    actual = {
        "kind": "operation_graph",
        "body": [
            {"kind": "operation", "route": "find/files", "data_from": []},
            {
                "kind": "operation",
                "route": "read/files",
                "data_from": [{"from": 0, "output": "result", "input": "primary"}],
            },
        ],
    }
    expected_before = deepcopy(expected)
    actual_before = deepcopy(actual)
    left = canonical_semantic_projection(expected, _registry())
    right = canonical_semantic_projection(actual, _registry())
    _assert(left == right, "edge representation equivalence")
    _assert(expected == expected_before and actual == actual_before, "input mutated")


def canonical_projection_omits_only_empty_materialized_outcomes() -> None:
    expected = {
        "kind": "operation_graph",
        "body": [{
            "kind": "barrier",
            "barrier": "get/approval",
            "cases": [{
                "outcome": "approved",
                "body": [{"kind": "operation", "route": "set/issues"}],
            }],
        }],
    }
    actual = deepcopy(expected)
    actual["body"][0]["data_from"] = []
    actual["body"][0]["cases"].append({"outcome": "rejected", "body": []})
    _assert(
        canonical_semantic_projection(expected, _registry())
        == canonical_semantic_projection(actual, _registry()),
        "empty outcome equivalence",
    )


def canonical_projection_preserves_semantic_differences() -> None:
    base = {
        "kind": "operation_graph",
        "body": [
            {"kind": "operation", "route": "find/files"},
            {"kind": "operation", "route": "read/files", "data_from": [{"from": 0}]},
        ],
    }
    mutations: list[dict[str, Any]] = []
    route = deepcopy(base)
    route["body"][1]["route"] = "delete/files"
    mutations.append(route)
    order = deepcopy(base)
    order["body"].reverse()
    mutations.append(order)
    edge = deepcopy(base)
    edge["body"][1]["data_from"][0]["from"] = 1
    mutations.append(edge)
    root = {"kind": "unrepresentable", "reason": "outside_registry"}
    mutations.append(root)
    canonical_base = canonical_semantic_projection(base, _registry())
    _assert(
        all(canonical_semantic_projection(item, _registry()) != canonical_base for item in mutations),
        "semantic mutation erased",
    )
    reason_a = {"kind": "unrepresentable", "reason": "outside_registry"}
    reason_b = {"kind": "unrepresentable", "reason": "missing_required_information"}
    _assert(
        canonical_semantic_projection(reason_a, _registry())
        != canonical_semantic_projection(reason_b, _registry()),
        "reason erased",
    )


def critical_hierarchy_has_nine_effective_columns() -> None:
    _assert(len(CRITICAL_NO_REGRESSION_COLUMNS) == 9, "historical critical count")
    _assert(len(EFFECTIVE_CRITICAL_COLUMNS) == 9, "effective critical count")
    _assert(EFFECTIVE_CRITICAL_COLUMNS[0] == "semantic_exact_canonical", "headline")
    _assert(EFFECTIVE_CRITICAL_COLUMNS[1:] == tuple(CRITICAL_NO_REGRESSION_COLUMNS[1:]), "other gates")


def canonical_verdict_uses_corrected_headline() -> None:
    common = {
        "root_exact": 90,
        "correct_abstention": 20,
        "technical_valid": 120,
        "false_action_avoided": 110,
        "undo_exact": 120,
        "consent_exact": 117,
        "negation_exact": 110,
        "branch_ownership_exact": 117,
    }
    arm_a = {"semantic_exact_canonical": 75, **common}
    arm_b = {"semantic_exact_canonical": 25, **common}
    typed_b = {"semantic_exact_canonical": 0}
    result = typed_verdict_v0_3(
        arm_a,
        arm_b,
        typed_b,
        list(CRITICAL_NO_REGRESSION_COLUMNS),
    )
    _assert(result["canonical_delta_B_minus_A"] == -50, "canonical delta")
    _assert(result["verdict"] == "candidate_fail", "verdict")
    _assert(result["typed_special_4_of_4"] is False, "special gate")


def anti_hardcoding_scan_has_no_case_specific_rule() -> None:
    source_names = (
        "live_replay_gate_v0_3.py",
        "live_evaluator_v0_3.py",
        "build_live_replay_v0_3_freeze.py",
    )
    forbidden_patterns = (
        r"ALLOWED_RECORD_(?:INDEX|IDENTITY)",
        r"frozen_sample\.\d+",
        r"(?:record_index|sample_index|request_ordinal)\s*(?:==|!=)\s*\d+",
        r"query_sha256[\"']?\s*[:=]\s*[\"'][0-9a-f]{64}[\"']",
        r"request_sha256[\"']?\s*[:=]\s*[\"'][0-9a-f]{64}[\"']",
        r"[\"']query[\"']\s*:\s*[\"'][^\"']+[\"']",
    )
    for name in source_names:
        source = (HERE / name).read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            _assert(re.search(pattern, source) is None, f"hardcoding:{name}:{pattern}")


def freeze_is_closed_and_gold_free() -> None:
    freeze = verify_v0_3_freeze(FREEZE_PATH)
    _assert(freeze["oracle_opened"] is False, "freeze oracle state")
    _assert(freeze["evaluation_executed"] is False, "freeze evaluation state")
    lowered = "\n".join(FROZEN_SOURCE_FILES).casefold()
    _assert("intent_shadow_oracle" not in lowered and "/oracles/" not in lowered, "gold source")


def failed_gate_stays_before_all_gold() -> None:
    import live_evaluator_v0_3 as evaluator

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
            evaluator.evaluate(freeze_path=HERE / "missing-v0-3-freeze.json")
        except Exception:
            pass
        else:
            raise AssertionError("missing freeze accepted")
    finally:
        evaluator._verified_gold = original_gold
        evaluator._verified_phase1_after_gate = original_phase1
    _assert(not gold_called and not phase1_called, "gold boundary crossed")


def full_pre_gold_replay_passes() -> None:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = "0"
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(HERE / "live_replay_gate_v0_3.py"),
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
    _assert(completed.returncode == 0, "full replay process")
    report = json.loads(completed.stdout)
    _assert(report["status"] == "pass", "full replay status")
    _assert(report["records_verified"] == 316, "full replay count")
    _assert(report["unexpected_mismatch_count"] == 0, "unexpected mismatch")
    _assert(report["oracle_opened"] is False, "oracle state")
    _assert(report["evaluation_executed"] is False, "evaluation state")
    for mismatch in report["allowed_mismatches"]:
        _assert(mismatch["json_pointer"] == ALLOWED_POINTER, "reported pointer")
        _assert(type(mismatch["saved"]) is bool and type(mismatch["replay"]) is bool, "types")
        _assert(mismatch["saved"] is not mismatch["replay"], "direction")


def main() -> int:
    tests = (
        replay_exact_match_is_accepted,
        replay_false_true_is_recorded_on_one_record,
        replay_true_false_is_recorded_on_another_record,
        replay_multiple_tolerances_remain_individually_visible,
        replay_missing_field_is_rejected,
        replay_non_boolean_is_rejected,
        replay_other_pointer_is_rejected,
        replay_semantic_change_is_rejected,
        replay_identity_shape_is_closed,
        canonical_projection_is_symmetric_for_empty_edges_and_ports,
        canonical_projection_omits_only_empty_materialized_outcomes,
        canonical_projection_preserves_semantic_differences,
        critical_hierarchy_has_nine_effective_columns,
        canonical_verdict_uses_corrected_headline,
        anti_hardcoding_scan_has_no_case_specific_rule,
        freeze_is_closed_and_gold_free,
        failed_gate_stays_before_all_gold,
        full_pre_gold_replay_passes,
    )
    failures: list[dict[str, str]] = []
    for function in tests:
        try:
            function()
        except Exception as exc:
            failures.append({"name": function.__name__, "error": f"{type(exc).__name__}:{exc}"})
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
