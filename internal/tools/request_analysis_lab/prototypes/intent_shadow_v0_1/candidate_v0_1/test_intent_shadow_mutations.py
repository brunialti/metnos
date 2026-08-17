#!/usr/bin/env python3
"""Negative mutation suite for intent-shadow candidate 0.1."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

import intent_shadow_evaluate as evaluator
from intent_shadow_evaluate import evaluate_saved_batch
from intent_shadow_extract import envelope_status, extract_raw_json
from intent_shadow_io import (
    TechnicalLimits,
    canonical_json_bytes,
    require_exact_unique_set,
    strict_json_loads,
)
from intent_shadow_normalize import normalize_document, verify_continuation
from intent_shadow_registry import load_frozen_registry, registry_payload_sha256
from intent_shadow_runner import load_legacy_panel, load_query_suite
from verify_candidate import FREEZE_PATH, verify_candidate


HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE.parent / "intent_shadow_registry_v0_1.json"
ORACLE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.json"
ORACLE_FREEZE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.freeze.json"


def _expect_rejected(function: Callable[[], Any]) -> str:
    try:
        result = function()
    except Exception:
        return "caught"
    if hasattr(result, "valid") and result.valid is False:
        return "caught"
    if hasattr(result, "validation_result") and result.validation_result.valid is False:
        return "caught"
    if type(result) is bool and result is True:
        return "caught"
    raise AssertionError("negative mutation was accepted")


def _extract_rejected(registry: dict[str, Any], document: Any) -> bool:
    envelope = extract_raw_json(canonical_json_bytes(document), registry)
    return envelope.validation_result.valid is False or envelope.technical_failure is not None


def _raw_rejected(registry: dict[str, Any], raw: bytes, *, limits: TechnicalLimits | None = None) -> bool:
    kwargs = {"limits": limits} if limits is not None else {}
    envelope = extract_raw_json(raw, registry, **kwargs)
    return envelope_status(envelope) in {"technical_invalid", "document_invalid", "projection_invalid"}


def _synthetic_multiport(registry: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(registry)
    first_route = next(iter(result["operations"]))
    result["operations"][first_route]["input_ports"] = ["left", "right"]
    result["operations"][first_route]["output_ports"] = ["one", "two"]
    result["integrity"]["registry_payload_sha256"] = ""
    result["integrity"]["registry_payload_sha256"] = registry_payload_sha256(result)
    return result


def _candidate_freeze_rejected(mutator: Callable[[dict[str, Any]], None]) -> bool:
    document = strict_json_loads(FREEZE_PATH.read_bytes())
    mutator(document)
    with tempfile.TemporaryDirectory(prefix="intent-shadow-freeze-mut-") as temp:
        path = Path(temp) / "freeze.json"
        path.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
        return verify_candidate(path)["error_count"] > 0


def _candidate_freeze_raw_rejected(raw: bytes) -> bool:
    with tempfile.TemporaryDirectory(prefix="intent-shadow-freeze-raw-") as temp:
        path = Path(temp) / "freeze.json"
        path.write_bytes(raw)
        return verify_candidate(path)["error_count"] > 0


def _suite_rejected(mutator: Callable[[dict[str, Any]], None]) -> bool:
    document = strict_json_loads((HERE / "intent_shadow_query_suite_v0_1.json").read_bytes())
    mutator(document)
    with tempfile.TemporaryDirectory(prefix="intent-shadow-suite-mut-") as temp:
        path = Path(temp) / "suite.json"
        path.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
        try:
            load_query_suite(path)
        except Exception:
            return True
    return False


def _gold_fake_batch(
    registry: dict[str, Any],
    *,
    first_raw_override: bytes | None = None,
) -> dict[str, Any]:
    from intent_shadow_runner import run_offline

    oracle = strict_json_loads(ORACLE_PATH.read_bytes())
    gold: dict[str, bytes] = {}
    for case in oracle["cases"]:
        gold[case["query_sha256"]] = canonical_json_bytes(case["expected"])
    for control in oracle["new_controls"]:
        gold[control["query_sha256"]] = canonical_json_bytes(control["expected"])
    first_query_sha = oracle["cases"][0]["query_sha256"]

    def fake(request: dict[str, Any]) -> bytes:
        from hashlib import sha256

        query = request["input"]["query"]
        query_sha = sha256(query.encode("utf-8")).hexdigest()
        if first_raw_override is not None and query_sha == first_query_sha:
            return first_raw_override
        return gold[query_sha]

    return run_offline(fake_transport=fake)


def _batch_schema_rejected_before_gold(
    batch: dict[str, Any],
    mutator: Callable[[dict[str, Any]], None],
) -> bool:
    document = deepcopy(batch)
    mutator(document)
    with tempfile.TemporaryDirectory(prefix="intent-shadow-batch-mut-") as temp:
        path = Path(temp) / "batch.json"
        path.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
        gold_touched = [False]
        original = evaluator.load_verified_canonical_oracle

        def forbidden_gold() -> tuple[dict[str, Any], str]:
            gold_touched[0] = True
            raise AssertionError("gold gate reached by invalid saved batch")

        evaluator.load_verified_canonical_oracle = forbidden_gold
        try:
            try:
                evaluate_saved_batch(path)
            except Exception:
                return not gold_touched[0]
        finally:
            evaluator.load_verified_canonical_oracle = original
    return False


def _oracle_mutation_rejected(
    batch: dict[str, Any],
    mutator: Callable[[dict[str, Any]], None] | None,
    *,
    freeze_mode: str,
) -> bool:
    oracle = strict_json_loads(ORACLE_PATH.read_bytes())
    if mutator is not None:
        mutator(oracle)
    with tempfile.TemporaryDirectory(prefix="intent-shadow-oracle-mut-") as temp:
        temp_path = Path(temp)
        oracle_path = temp_path / "oracle.json"
        oracle_path.write_bytes(json.dumps(oracle, ensure_ascii=False, indent=2).encode("utf-8"))
        if freeze_mode == "canonical":
            freeze_path = ORACLE_FREEZE_PATH
        elif freeze_mode == "absent":
            freeze_path = temp_path / "absent.freeze.json"
        elif freeze_mode == "wrong":
            freeze = strict_json_loads(ORACLE_FREEZE_PATH.read_bytes())
            freeze["oracle_payload_sha256"] = "0" * 64
            freeze_path = temp_path / "wrong.freeze.json"
            freeze_path.write_bytes(json.dumps(freeze, ensure_ascii=False, indent=2).encode("utf-8"))
        elif freeze_mode == "resigned":
            verifier = evaluator._load_canonical_oracle_verifier()
            oracle["integrity"]["oracle_payload_sha256"] = (
                verifier.oracle_payload_sha256(oracle)
            )
            oracle_path.write_bytes(
                json.dumps(oracle, ensure_ascii=False, indent=2).encode("utf-8")
            )
            freeze = strict_json_loads(ORACLE_FREEZE_PATH.read_bytes())
            freeze["files"][verifier.ORACLE_REL] = sha256(
                oracle_path.read_bytes()
            ).hexdigest()
            freeze["oracle_payload_sha256"] = verifier.oracle_payload_sha256(oracle)
            freeze["lock_payload_sha256"] = verifier.freeze_payload_sha256(freeze)
            freeze_path = temp_path / "resigned.freeze.json"
            freeze_path.write_bytes(
                json.dumps(freeze, ensure_ascii=False, indent=2).encode("utf-8")
            )
        else:
            raise AssertionError(f"unknown freeze mode {freeze_mode!r}")
        batch_path = temp_path / "batch.json"
        batch_path.write_bytes(json.dumps(batch, ensure_ascii=False, indent=2).encode("utf-8"))
        old_oracle = evaluator.ORACLE_PATH
        old_freeze = evaluator.ORACLE_FREEZE_PATH
        evaluator.ORACLE_PATH = oracle_path
        evaluator.ORACLE_FREEZE_PATH = freeze_path
        try:
            try:
                evaluate_saved_batch(batch_path)
            except Exception:
                return True
        finally:
            evaluator.ORACLE_PATH = old_oracle
            evaluator.ORACLE_FREEZE_PATH = old_freeze
    return False


def _mutate_first_edge_from(document: dict[str, Any], value: Any) -> None:
    for record in document["records"]:
        normalization = record["extraction"].get("normalization_result")
        if normalization is None:
            continue
        stack = list(normalization["normalized_document"].get("body", []))
        while stack:
            node = stack.pop(0)
            if node.get("data_from"):
                node["data_from"][0]["from"] = value
                return
            for case in node.get("cases", []):
                stack.extend(case["body"])
    raise AssertionError("gold fake batch has no data edge")


def _mutate_valid_oracle_reason(document: dict[str, Any]) -> None:
    for case in document["cases"]:
        expected = case["expected"]
        if expected.get("kind") == "unrepresentable":
            expected["reason"] = (
                "ambiguous_intent"
                if expected["reason"] != "ambiguous_intent"
                else "outside_registry"
            )
            return
    raise AssertionError("canonical oracle has no unrepresentable case")


def main() -> int:
    registry, _identity = load_frozen_registry(REGISTRY_PATH)
    first_route = next(iter(registry["operations"]))
    second_route = next(iter(list(registry["operations"])[1:]))
    good_graph = {
        "kind": "operation_graph",
        "body": [
            {"kind": "operation", "route": first_route},
            {"kind": "operation", "route": second_route, "data_from": [{"from": 0}]},
        ],
    }
    good_barrier = {
        "kind": "operation_graph",
        "body": [
            {
                "kind": "barrier",
                "barrier": "get/approval",
                "cases": [
                    {
                        "outcome": "approved",
                        "body": [{"kind": "operation", "route": "set/issues"}],
                    }
                ],
            }
        ],
    }
    normalization = normalize_document(good_barrier, registry)
    continuation = normalization.continuations[0]
    valid_batch = _gold_fake_batch(registry)
    document_invalid_batch = _gold_fake_batch(registry, first_raw_override=b"false")
    technical_invalid_batch = _gold_fake_batch(registry, first_raw_override=b"{")

    mutations: dict[str, Callable[[], Any]] = {
        # D-01 and D-02
        "duplicate_json_key": lambda: strict_json_loads(b'{"kind":"unrepresentable","kind":"operation_graph"}'),
        "nan": lambda: strict_json_loads(b'{"x":NaN}'),
        "infinity": lambda: strict_json_loads(b'{"x":Infinity}'),
        "negative_infinity": lambda: strict_json_loads(b'{"x":-Infinity}'),
        "overflow": lambda: strict_json_loads(b'{"x":1e999}'),
        "huge_integer": lambda: _raw_rejected(
            registry, b'{"x":' + b'9' * 5000 + b'}'
        ),
        "negative_huge_integer": lambda: _raw_rejected(
            registry, b'{"x":-' + b'9' * 5000 + b'}'
        ),
        "surrogate_value": lambda: _raw_rejected(registry, b'{"x":"\\ud800"}'),
        "surrogate_key": lambda: _raw_rejected(registry, b'{"\\udfff":1}'),
        "invalid_utf8": lambda: _raw_rejected(registry, b'{"x":"\xff"}'),
        "decoder_recursion": lambda: _raw_rejected(
            registry, b"[" * 2_000 + b"0" + b"]" * 2_000
        ),
        # D-03 exact types and closed structures
        "root_array": lambda: _extract_rejected(registry, []),
        "extra_root_key": lambda: _extract_rejected(registry, {"kind":"unrepresentable","reason":"outside_registry","body":[]}),
        "bool_edge_ordinal": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route},{"kind":"operation","route":second_route,"data_from":[{"from":False}]}]}),
        "float_edge_ordinal": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route},{"kind":"operation","route":second_route,"data_from":[{"from":0.0}]}]}),
        "control_inputs_empty": lambda: _extract_rejected(registry, {"kind":"system_control","control":"undo_last_turn","inputs":{}}),
        # D-04 primitive used for canonical authority sets
        "set_duplicate": lambda: require_exact_unique_set(["a","a"], {"a","b"}, "$"),
        "set_junk": lambda: require_exact_unique_set(["a","b","c"], {"a","b"}, "$"),
        "set_omission": lambda: require_exact_unique_set(["a"], {"a","b"}, "$"),
        "set_substitution": lambda: require_exact_unique_set(["a","c"], {"a","b"}, "$"),
        # roots
        "unknown_root": lambda: _extract_rejected(registry, {"kind":"unknown"}),
        "unknown_reason": lambda: _extract_rejected(registry, {"kind":"unrepresentable","reason":"unknown"}),
        "unknown_control": lambda: _extract_rejected(registry, {"kind":"system_control","control":"unknown"}),
        "mixed_control_graph": lambda: _extract_rejected(registry, {"kind":"system_control","control":"undo_last_turn","body":[]}),
        "empty_graph": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[]}),
        "unknown_route": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":"unknown/route"}]}),
        # graph/data dominance and ports
        "forward_edge": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route,"data_from":[{"from":1}]},{"kind":"operation","route":second_route}]}),
        "self_edge": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route,"data_from":[{"from":0}]}]}),
        "sibling_branch_edge": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":"approved","body":[{"kind":"operation","route":first_route}]},{"outcome":"rejected","body":[{"kind":"operation","route":second_route,"data_from":[{"from":0}]}]}]}]}),
        "branch_to_outer_edge": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":"approved","body":[{"kind":"operation","route":first_route}]}]},{"kind":"operation","route":second_route,"data_from":[{"from":0}]}]}),
        "bad_output_port": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route},{"kind":"operation","route":second_route,"data_from":[{"from":0,"output":"bad"}]}]}),
        "bad_input_port": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route},{"kind":"operation","route":second_route,"data_from":[{"from":0,"input":"bad"}]}]}),
        "duplicate_edge": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"operation","route":first_route},{"kind":"operation","route":second_route,"data_from":[{"from":0},{"from":0}]}]}),
        "ambiguous_ports": lambda: _extract_rejected(_synthetic_multiport(registry), good_graph),
        # barrier shape/ownership
        "unknown_barrier": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"unknown","cases":[]}]}),
        "empty_barrier_cases": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[]}]}),
        "empty_emitted_case": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":"approved","body":[]}]}]}),
        "duplicate_outcome": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":"approved","body":[{"kind":"operation","route":first_route}]},{"outcome":"approved","body":[{"kind":"operation","route":second_route}]}]}]}),
        "outcome_order": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":"rejected","body":[{"kind":"operation","route":first_route}]},{"outcome":"approved","body":[{"kind":"operation","route":second_route}]}]}]}),
        "unknown_outcome": lambda: _extract_rejected(registry, {"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":"maybe","body":[{"kind":"operation","route":first_route}]}]}]}),
        "outcome_list": lambda: _raw_rejected(
            registry,
            b'{"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":[],"body":[{"kind":"operation","route":"set/issues"}]}]}]}',
        ),
        "outcome_object": lambda: _raw_rejected(
            registry,
            b'{"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":{},"body":[{"kind":"operation","route":"set/issues"}]}]}]}',
        ),
        "outcome_number": lambda: _raw_rejected(
            registry,
            b'{"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":1,"body":[{"kind":"operation","route":"set/issues"}]}]}]}',
        ),
        "outcome_boolean": lambda: _raw_rejected(
            registry,
            b'{"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":true,"body":[{"kind":"operation","route":"set/issues"}]}]}]}',
        ),
        "outcome_null": lambda: _raw_rejected(
            registry,
            b'{"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":null,"body":[{"kind":"operation","route":"set/issues"}]}]}]}',
        ),
        # continuations
        "continuation_hash": lambda: verify_continuation(replace(continuation, continuation_sha256="0"*64), normalization.document, continuation.registry_sha256),
        "continuation_registry": lambda: verify_continuation(replace(continuation, registry_sha256="0"*64), normalization.document, continuation.registry_sha256),
        "continuation_root": lambda: verify_continuation(replace(continuation, root_document_sha256="0"*64), normalization.document, continuation.registry_sha256),
        "continuation_path": lambda: verify_continuation(replace(continuation, barrier_path=("body",99)), normalization.document, continuation.registry_sha256),
        "continuation_outcome": lambda: verify_continuation(replace(continuation, outcome_ref="rejected"), normalization.document, continuation.registry_sha256),
        "continuation_body": lambda: verify_continuation(replace(continuation, typed_body=()), normalization.document, continuation.registry_sha256),
        # technical guardrails remain technical invalidity
        "byte_limit": lambda: _raw_rejected(registry, b'{"kind":"unrepresentable","reason":"outside_registry"}', limits=TechnicalLimits(max_bytes=4)),
        "depth_limit": lambda: _raw_rejected(registry, b'{"a":{"b":{"c":1}}}', limits=TechnicalLimits(max_depth=2)),
        "node_limit": lambda: _raw_rejected(registry, b'{"a":[1,2,3]}', limits=TechnicalLimits(max_nodes=3)),
        "string_limit": lambda: _raw_rejected(registry, b'{"a":"long"}', limits=TechnicalLimits(max_string_chars=3)),
        # candidate freeze: D-01..D-04 and exact bindings
        "freeze_duplicate_key": lambda: _candidate_freeze_raw_rejected(
            FREEZE_PATH.read_bytes().replace(b'{\n  "algorithm"', b'{\n  "algorithm": "sha256",\n  "algorithm"', 1)
        ),
        "freeze_nan": lambda: _candidate_freeze_raw_rejected(
            FREEZE_PATH.read_bytes().replace(b'{\n', b'{\n  "probe": NaN,\n', 1)
        ),
        "freeze_overflow": lambda: _candidate_freeze_raw_rejected(
            FREEZE_PATH.read_bytes().replace(b'{\n', b'{\n  "probe": 1e999,\n', 1)
        ),
        "freeze_created_date_type": lambda: _candidate_freeze_rejected(
            lambda d: d.__setitem__("created_date", ["2026-08-12"])
        ),
        "freeze_gpu_int": lambda: _candidate_freeze_rejected(
            lambda d: d.__setitem__("gpu_mode_present", 0)
        ),
        "freeze_local_omission": lambda: _candidate_freeze_rejected(
            lambda d: d["local_files"].pop(next(iter(d["local_files"])))
        ),
        "freeze_local_addition": lambda: _candidate_freeze_rejected(
            lambda d: d["local_files"].__setitem__("junk", "0" * 64)
        ),
        "freeze_authority_substitution": lambda: _candidate_freeze_rejected(
            lambda d: d["authorities"].__setitem__(
                next(iter(d["authorities"])), "0" * 64
            )
        ),
        "freeze_contract_type": lambda: _candidate_freeze_rejected(
            lambda d: d.__setitem__("contract_version", True)
        ),
        # query-only binding mutations
        "suite_missing_case": lambda: _suite_rejected(
            lambda d: d["panels"]["canonical_120"].pop()
        ),
        "suite_duplicate_case": lambda: _suite_rejected(
            lambda d: d["panels"]["canonical_120"].__setitem__(
                1, deepcopy(d["panels"]["canonical_120"][0])
            )
        ),
        "suite_changed_query_hash": lambda: _suite_rejected(
            lambda d: d["panels"]["canonical_120"][0].__setitem__(
                "query_sha256", "0" * 64
            )
        ),
        "suite_gold_flag": lambda: _suite_rejected(
            lambda d: d.__setitem__("gold_fields_present", True)
        ),
        # evaluator exact-type/closed replay before gold
        "batch_validation_bool_to_int": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"]["validation_result"].__setitem__("valid", 1),
        ),
        "batch_validation_false_to_int": lambda: _batch_schema_rejected_before_gold(
            document_invalid_batch,
            lambda d: d["records"][0]["extraction"]["validation_result"].__setitem__("valid", 0),
        ),
        "batch_gpu_bool_to_int": lambda: _batch_schema_rejected_before_gold(
            valid_batch, lambda d: d.__setitem__("gpu_mode_present", 0)
        ),
        "batch_network_bool_to_int": lambda: _batch_schema_rejected_before_gold(
            valid_batch, lambda d: d.__setitem__("network_transport_present", 0)
        ),
        "batch_panel_count_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["panel_counts"].__setitem__("canonical_120", True),
        ),
        "batch_record_ordinal_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch, lambda d: d["records"][0].__setitem__("ordinal", True)
        ),
        "batch_normalized_ordinal_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"]["normalization_result"]["normalized_document"]["body"][0].__setitem__("ordinal", False),
        ),
        "batch_edge_from_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: _mutate_first_edge_from(d, False),
        ),
        "batch_extra_extraction_key": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"].__setitem__("extra", None),
        ),
        "batch_missing_extraction_key": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"].pop("technical_failure"),
        ),
        "batch_issue_code_int": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: (
                d["records"][0]["extraction"]["validation_result"]["issues"].append(
                    {"code": 1, "path": "$", "message": "x"}
                )
            ),
        ),
        "batch_projection_sha_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"]["projection_result"].__setitem__("semantic_sha256", True),
        ),
        "batch_panel_counts_extra": lambda: _batch_schema_rejected_before_gold(
            valid_batch, lambda d: d["panel_counts"].__setitem__("legacy_phase1_34", 34)
        ),
        "batch_decoded_bool_to_int": lambda: _batch_schema_rejected_before_gold(
            document_invalid_batch,
            lambda d: d["records"][0]["extraction"].__setitem__("decoded_document", 0),
        ),
        "batch_technical_code_int": lambda: _batch_schema_rejected_before_gold(
            technical_invalid_batch,
            lambda d: d["records"][0]["extraction"]["technical_failure"].__setitem__("code", 1),
        ),
        "batch_status_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"].__setitem__("status", True),
        ),
        "batch_raw_b64_bool": lambda: _batch_schema_rejected_before_gold(
            valid_batch,
            lambda d: d["records"][0]["extraction"].__setitem__("raw_model_output_b64", True),
        ),
        # oracle authority gate: modified, absent or wrong freeze all fail closed
        "oracle_expected_modified": lambda: _oracle_mutation_rejected(
            valid_batch,
            _mutate_valid_oracle_reason,
            freeze_mode="canonical",
        ),
        "oracle_freeze_absent": lambda: _oracle_mutation_rejected(
            valid_batch, None, freeze_mode="absent"
        ),
        "oracle_freeze_wrong": lambda: _oracle_mutation_rejected(
            valid_batch, None, freeze_mode="wrong"
        ),
        "oracle_expected_modified_resigned": lambda: _oracle_mutation_rejected(
            valid_batch,
            _mutate_valid_oracle_reason,
            freeze_mode="resigned",
        ),
    }

    results: dict[str, str] = {}
    errors: list[str] = []
    for name, function in mutations.items():
        try:
            results[name] = _expect_rejected(function)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    positives: dict[str, Callable[[], bool]] = {
        "valid_data_graph": lambda: extract_raw_json(canonical_json_bytes(good_graph), registry).validation_result.valid,
        "valid_barrier": lambda: extract_raw_json(canonical_json_bytes(good_barrier), registry).validation_result.valid,
        "valid_continuation": lambda: verify_continuation(continuation, normalization.document, continuation.registry_sha256).valid,
        "valid_unrepresentable": lambda: envelope_status(extract_raw_json(b'{"kind":"unrepresentable","reason":"outside_registry"}',registry)) == "valid_unrepresentable",
        "set_reorder": lambda: (require_exact_unique_set(["b","a"], {"a","b"}, "$"), True)[1],
        "query_suite": lambda: len(load_query_suite()["panels"]["canonical_120"]) == 120,
        "legacy_panel": lambda: len(load_legacy_panel()["cases"]) == 34,
    }
    positive_results: dict[str, str] = {}
    for name, function in positives.items():
        try:
            if function() is not True:
                raise AssertionError("positive returned false")
            positive_results[name] = "accepted"
        except Exception as exc:
            errors.append(f"positive {name}: {type(exc).__name__}: {exc}")

    report = {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "negative_count": len(mutations),
        "negative_results": results,
        "positive_count": len(positives),
        "positive_results": positive_results,
        "gpu_calls": 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
