#!/usr/bin/env python3
"""Post-batch evaluator for canonical and typed-control panels.

Gold is opened only after the complete saved batch has been loaded and
validated.  The 34 legacy controls are deliberately excluded: their Phase-1
evaluator remains the separate authority selected by Roberto's option A.
"""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import re
from typing import Any

from intent_shadow_extract import envelope_json, extract_raw_json
from intent_shadow_io import canonical_json_bytes, file_sha256, strict_json_file
from intent_shadow_normalize import (
    normalize_document,
    project_normalized,
    semantic_document_json,
)
from intent_shadow_projection import compile_model_contract
from intent_shadow_registry import load_frozen_registry
from intent_shadow_runner import (
    QUERY_SUITE_PATH,
    REGISTRY_PATH,
    RUNNER_VERSION,
    load_query_suite,
    request_document,
)


EVALUATOR_VERSION = "metnos.intent-shadow-offline-evaluator/0.1"
HERE = Path(__file__).resolve().parent
ORACLE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.json"
ORACLE_FREEZE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.freeze.json"
CANONICAL_VERIFY_PATH = HERE.parent / "verify_oracle.py"
CANDIDATE_FREEZE_PATH = HERE / "candidate_v0_1.freeze.json"
CANDIDATE_VERIFY_PATH = HERE / "verify_candidate.py"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _schema_error(path: str, message: str) -> None:
    raise RuntimeError(f"saved batch schema at {path}: {message}")


def _exact_type(value: Any, expected: type, path: str) -> None:
    if type(value) is not expected:
        _schema_error(
            path,
            f"expected={expected.__name__},actual={type(value).__name__}",
        )


def _closed(value: Any, keys: set[str], path: str) -> None:
    _exact_type(value, dict, path)
    if set(value) != keys:
        _schema_error(
            path,
            f"missing={sorted(keys - set(value))!r},extra={sorted(set(value) - keys)!r}",
        )


def _sha256_string(value: Any, path: str) -> None:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        _schema_error(path, "expected lowercase SHA-256 string")


def _path_array(value: Any, path: str) -> None:
    _exact_type(value, list, path)
    for index, part in enumerate(value):
        if type(part) not in (str, int):
            _schema_error(f"{path}[{index}]", "expected exact string or integer")


def _json_value(value: Any, path: str) -> None:
    """Validate the exact recursively closed universe representable by JSON."""
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _schema_error(path, "non-finite number")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            _exact_type(key, str, f"{path}.<key>")
            _json_value(item, f"{path}[{json.dumps(key, ensure_ascii=False)}]")
        return
    _schema_error(path, f"non-JSON type={type(value).__name__}")


def _edge(value: Any, path: str) -> None:
    _closed(value, {"from", "output", "input"}, path)
    _exact_type(value["from"], int, f"{path}.from")
    _exact_type(value["output"], str, f"{path}.output")
    _exact_type(value["input"], str, f"{path}.input")


def _edge_array(value: Any, path: str) -> None:
    _exact_type(value, list, path)
    for index, edge in enumerate(value):
        _edge(edge, f"{path}[{index}]")


def _normalized_body(value: Any, path: str) -> None:
    _exact_type(value, list, path)
    for index, node in enumerate(value):
        _normalized_node(node, f"{path}[{index}]")


def _normalized_node(value: Any, path: str) -> None:
    _exact_type(value, dict, path)
    kind = value.get("kind")
    if kind == "operation":
        _closed(value, {"kind", "node_path", "ordinal", "route", "data_from"}, path)
        _path_array(value["node_path"], f"{path}.node_path")
        _exact_type(value["ordinal"], int, f"{path}.ordinal")
        _exact_type(value["route"], str, f"{path}.route")
        _edge_array(value["data_from"], f"{path}.data_from")
        return
    if kind == "barrier":
        _closed(value, {"kind", "node_path", "barrier", "data_from", "cases"}, path)
        _path_array(value["node_path"], f"{path}.node_path")
        _exact_type(value["barrier"], str, f"{path}.barrier")
        _edge_array(value["data_from"], f"{path}.data_from")
        _exact_type(value["cases"], list, f"{path}.cases")
        for index, case in enumerate(value["cases"]):
            case_path = f"{path}.cases[{index}]"
            _closed(case, {"outcome", "body"}, case_path)
            _exact_type(case["outcome"], str, f"{case_path}.outcome")
            _normalized_body(case["body"], f"{case_path}.body")
        return
    _schema_error(f"{path}.kind", "unknown normalized node kind")


def _normalized_document(value: Any, path: str) -> None:
    _exact_type(value, dict, path)
    kind = value.get("kind")
    if kind == "operation_graph":
        _closed(value, {"kind", "body"}, path)
        _normalized_body(value["body"], f"{path}.body")
    elif kind == "system_control":
        keys = {"kind", "control"} | ({"inputs"} if "inputs" in value else set())
        _closed(value, keys, path)
        _exact_type(value["control"], str, f"{path}.control")
        if "inputs" in value:
            _exact_type(value["inputs"], dict, f"{path}.inputs")
    elif kind == "unrepresentable":
        _closed(value, {"kind", "reason"}, path)
        _exact_type(value["reason"], str, f"{path}.reason")
    else:
        _schema_error(f"{path}.kind", "unknown normalized root kind")


def _semantic_body(value: Any, path: str) -> None:
    _exact_type(value, list, path)
    for index, node in enumerate(value):
        _semantic_node(node, f"{path}[{index}]")


def _semantic_node(value: Any, path: str) -> None:
    _exact_type(value, dict, path)
    kind = value.get("kind")
    if kind == "operation":
        _closed(value, {"kind", "route", "data_from"}, path)
        _exact_type(value["route"], str, f"{path}.route")
        _edge_array(value["data_from"], f"{path}.data_from")
        return
    if kind == "barrier":
        _closed(value, {"kind", "barrier", "data_from", "cases"}, path)
        _exact_type(value["barrier"], str, f"{path}.barrier")
        _edge_array(value["data_from"], f"{path}.data_from")
        _exact_type(value["cases"], list, f"{path}.cases")
        for index, case in enumerate(value["cases"]):
            case_path = f"{path}.cases[{index}]"
            _closed(case, {"outcome", "body"}, case_path)
            _exact_type(case["outcome"], str, f"{case_path}.outcome")
            _semantic_body(case["body"], f"{case_path}.body")
        return
    _schema_error(f"{path}.kind", "unknown semantic node kind")


def _semantic_document(value: Any, path: str) -> None:
    _exact_type(value, dict, path)
    kind = value.get("kind")
    if kind == "operation_graph":
        _closed(value, {"kind", "body"}, path)
        _semantic_body(value["body"], f"{path}.body")
    elif kind == "system_control":
        keys = {"kind", "control"} | ({"inputs"} if "inputs" in value else set())
        _closed(value, keys, path)
        _exact_type(value["control"], str, f"{path}.control")
        if "inputs" in value:
            _exact_type(value["inputs"], dict, f"{path}.inputs")
    elif kind == "unrepresentable":
        _closed(value, {"kind", "reason"}, path)
        _exact_type(value["reason"], str, f"{path}.reason")
    else:
        _schema_error(f"{path}.kind", "unknown semantic root kind")


def _continuation(value: Any, path: str) -> None:
    _closed(
        value,
        {
            "contract_version",
            "registry_sha256",
            "root_document_sha256",
            "barrier_path",
            "outcome_ref",
            "typed_body",
            "continuation_sha256",
        },
        path,
    )
    _exact_type(value["contract_version"], str, f"{path}.contract_version")
    _sha256_string(value["registry_sha256"], f"{path}.registry_sha256")
    _sha256_string(value["root_document_sha256"], f"{path}.root_document_sha256")
    _path_array(value["barrier_path"], f"{path}.barrier_path")
    _exact_type(value["outcome_ref"], str, f"{path}.outcome_ref")
    _normalized_body(value["typed_body"], f"{path}.typed_body")
    _sha256_string(value["continuation_sha256"], f"{path}.continuation_sha256")


def _saved_extraction(value: Any, path: str) -> None:
    _closed(
        value,
        {
            "contract_version",
            "registry_sha256",
            "status",
            "raw_model_output_b64",
            "raw_model_output_sha256",
            "decoded_document",
            "technical_failure",
            "validation_result",
            "normalization_result",
            "projection_result",
        },
        path,
    )
    _exact_type(value["contract_version"], str, f"{path}.contract_version")
    _sha256_string(value["registry_sha256"], f"{path}.registry_sha256")
    _exact_type(value["status"], str, f"{path}.status")
    if value["status"] not in {
        "technical_invalid",
        "document_invalid",
        "projection_invalid",
        "valid_unrepresentable",
        "valid_representable",
    }:
        _schema_error(f"{path}.status", "unknown extraction status")
    _exact_type(value["raw_model_output_b64"], str, f"{path}.raw_model_output_b64")
    _sha256_string(value["raw_model_output_sha256"], f"{path}.raw_model_output_sha256")
    _json_value(value["decoded_document"], f"{path}.decoded_document")

    failure = value["technical_failure"]
    if failure is not None:
        failure_path = f"{path}.technical_failure"
        _closed(failure, {"code", "path", "message"}, failure_path)
        for key in ("code", "path", "message"):
            _exact_type(failure[key], str, f"{failure_path}.{key}")

    validation = value["validation_result"]
    validation_path = f"{path}.validation_result"
    _closed(validation, {"valid", "issues"}, validation_path)
    _exact_type(validation["valid"], bool, f"{validation_path}.valid")
    _exact_type(validation["issues"], list, f"{validation_path}.issues")
    for index, issue in enumerate(validation["issues"]):
        issue_path = f"{validation_path}.issues[{index}]"
        _closed(issue, {"code", "path", "message"}, issue_path)
        for key in ("code", "path", "message"):
            _exact_type(issue[key], str, f"{issue_path}.{key}")

    normalization = value["normalization_result"]
    if normalization is not None:
        normalization_path = f"{path}.normalization_result"
        _closed(
            normalization,
            {"normalized_document", "normalized_document_sha256", "continuations"},
            normalization_path,
        )
        _normalized_document(
            normalization["normalized_document"],
            f"{normalization_path}.normalized_document",
        )
        _sha256_string(
            normalization["normalized_document_sha256"],
            f"{normalization_path}.normalized_document_sha256",
        )
        _exact_type(normalization["continuations"], list, f"{normalization_path}.continuations")
        for index, continuation in enumerate(normalization["continuations"]):
            _continuation(continuation, f"{normalization_path}.continuations[{index}]")

    projection = value["projection_result"]
    if projection is not None:
        projection_path = f"{path}.projection_result"
        _closed(projection, {"semantic_document", "semantic_sha256"}, projection_path)
        _semantic_document(projection["semantic_document"], f"{projection_path}.semantic_document")
        _sha256_string(projection["semantic_sha256"], f"{projection_path}.semantic_sha256")

    if value["status"] in {"valid_unrepresentable", "valid_representable"}:
        if failure is not None or validation["valid"] is not True or normalization is None or projection is None:
            _schema_error(path, "valid status has inconsistent phase fields")
    elif value["status"] == "document_invalid":
        if failure is not None or validation["valid"] is not False or normalization is not None or projection is not None:
            _schema_error(path, "document-invalid status has inconsistent phase fields")
    elif value["status"] == "technical_invalid":
        if failure is None or validation["valid"] is not False or normalization is not None or projection is not None:
            _schema_error(path, "technical-invalid status has inconsistent phase fields")
    elif value["status"] == "projection_invalid":
        if failure is not None or validation["valid"] is not True or projection is not None:
            _schema_error(path, "projection-invalid status has inconsistent phase fields")


def _validate_saved_batch(
    batch: Any,
    suite: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if type(batch) is not dict:
        raise RuntimeError("saved batch root is not an object")
    required = {
        "runner_version", "artifact_kind", "gpu_mode_present",
        "network_transport_present", "registry_file_sha256",
        "registry_payload_sha256", "query_suite_file_sha256", "schema_sha256",
        "prompt_sha256", "panel_counts", "legacy_binding", "records",
    }
    _closed(batch, required, "$batch")
    _exact_type(batch["runner_version"], str, "$batch.runner_version")
    if batch["runner_version"] != RUNNER_VERSION:
        _schema_error("$batch.runner_version", "runner version mismatch")
    _exact_type(batch["artifact_kind"], str, "$batch.artifact_kind")
    _exact_type(batch["gpu_mode_present"], bool, "$batch.gpu_mode_present")
    _exact_type(
        batch["network_transport_present"],
        bool,
        "$batch.network_transport_present",
    )
    for key in (
        "registry_file_sha256",
        "registry_payload_sha256",
        "query_suite_file_sha256",
        "schema_sha256",
        "prompt_sha256",
    ):
        _sha256_string(batch[key], f"$batch.{key}")
    _closed(
        batch["panel_counts"],
        {"canonical_120", "typed_controls_4"},
        "$batch.panel_counts",
    )
    _exact_type(
        batch["panel_counts"]["canonical_120"],
        int,
        "$batch.panel_counts.canonical_120",
    )
    _exact_type(
        batch["panel_counts"]["typed_controls_4"],
        int,
        "$batch.panel_counts.typed_controls_4",
    )
    _exact_type(batch["records"], list, "$batch.records")
    if batch["gpu_mode_present"] is not False or batch["network_transport_present"] is not False:
        raise RuntimeError("candidate 0.1 evaluator accepts offline batches only")
    if (
        batch["artifact_kind"] != "fake_transport_batch"
        or batch["panel_counts"] != {"canonical_120": 120, "typed_controls_4": 4}
        or batch["legacy_binding"] is not None
        or batch["query_suite_file_sha256"] != file_sha256(QUERY_SUITE_PATH)
    ):
        raise RuntimeError("saved batch panel/checkpoint mismatch")
    contract = compile_model_contract(registry)
    if (
        batch["registry_file_sha256"] != file_sha256(REGISTRY_PATH)
        or batch["registry_payload_sha256"] != contract.registry_sha256
        or batch["schema_sha256"] != contract.schema_sha256
        or batch["prompt_sha256"] != contract.prompt_sha256
    ):
        raise RuntimeError("saved batch contract binding mismatch")
    records = batch["records"]
    if type(records) is not list:
        raise RuntimeError("saved batch records are not an array")
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    for panel in ("canonical_120", "typed_controls_4"):
        for case in suite["panels"][panel]:
            expected[(panel, case["opaque_case_id"])] = case
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for index, record in enumerate(records):
        record_path = f"$batch.records[{index}]"
        _closed(record, {
            "ordinal", "opaque_case_id", "query_sha256", "panel",
            "request_sha256", "status", "extraction",
        }, record_path)
        _exact_type(record["ordinal"], int, f"{record_path}.ordinal")
        _exact_type(record["opaque_case_id"], str, f"{record_path}.opaque_case_id")
        _sha256_string(record["query_sha256"], f"{record_path}.query_sha256")
        _exact_type(record["panel"], str, f"{record_path}.panel")
        _sha256_string(record["request_sha256"], f"{record_path}.request_sha256")
        _exact_type(record["status"], str, f"{record_path}.status")
        _saved_extraction(record["extraction"], f"{record_path}.extraction")
        panel = record.get("panel")
        case_id = record.get("opaque_case_id")
        key = (panel, case_id)
        if key not in expected or key in observed:
            raise RuntimeError(f"record {index} identity mismatch")
        case = expected[key]
        if (
            record["ordinal"] != case["ordinal"]
            or record.get("query_sha256") != case["query_sha256"]
            or record.get("status") != "fake_transport_complete"
        ):
            raise RuntimeError(f"record {index} query hash mismatch")
        expected_request_sha = sha256(
            canonical_json_bytes(request_document(case["query"], registry))
        ).hexdigest()
        if record.get("request_sha256") != expected_request_sha:
            raise RuntimeError(f"record {index} request hash mismatch")
        extraction = record["extraction"]
        if type(extraction) is not dict:
            raise RuntimeError(f"record {index} extraction is not an object")
        encoded = extraction.get("raw_model_output_b64")
        if type(encoded) is not str:
            raise RuntimeError(f"record {index} lacks raw model output")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError(f"record {index} raw output encoding invalid") from exc
        reproduced = envelope_json(extract_raw_json(raw, registry))
        if canonical_json_bytes(reproduced) != canonical_json_bytes(extraction):
            raise RuntimeError(f"record {index} extraction evidence mismatch")
        observed[key] = record
    if set(observed) != set(expected):
        raise RuntimeError("saved batch is incomplete")
    return {f"{panel}\0{case_id}": record for (panel, case_id), record in observed.items()}


def _expected_map(oracle: Any) -> dict[str, dict[str, Any]]:
    if type(oracle) is not dict:
        raise RuntimeError("oracle root is not an object")
    cases = oracle.get("cases")
    controls = oracle.get("new_controls")
    if type(cases) is not list or len(cases) != 120 or type(controls) is not list or len(controls) != 4:
        raise RuntimeError("oracle counts mismatch")
    result: dict[str, dict[str, Any]] = {}
    for case in cases:
        result[f"canonical_120\0{case['case_id']}"] = case["expected"]
    for control in controls:
        result[f"typed_controls_4\0{control['control_id']}"] = control["expected"]
    if len(result) != 124:
        raise RuntimeError("oracle identities not unique")
    return result


def _load_canonical_oracle_verifier() -> Any:
    expected_sha = "8fc61e29057a1b04b0386a9af44b9051c27f0a4ebda2fc84331be4cea4774687"
    if sha256(CANONICAL_VERIFY_PATH.read_bytes()).hexdigest() != expected_sha:
        raise RuntimeError("canonical oracle verifier identity mismatch")
    spec = importlib.util.spec_from_file_location(
        "intent_shadow_candidate_pinned_oracle_verifier",
        CANONICAL_VERIFY_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical oracle verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_candidate_checkpoint() -> None:
    """Require the sealed candidate, including its pinned oracle authorities."""
    expected_sha = "df84fb5f87759ee3aa5bcf8c55d0abb0dace8c5c3a7381cf7d95dc6fed07d5a8"
    if sha256(CANDIDATE_VERIFY_PATH.read_bytes()).hexdigest() != expected_sha:
        raise RuntimeError("candidate verifier identity mismatch")
    spec = importlib.util.spec_from_file_location(
        "intent_shadow_candidate_pinned_self_verifier",
        CANDIDATE_VERIFY_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.verify_candidate(CANDIDATE_FREEZE_PATH)
    if (
        type(report) is not dict
        or report.get("status") != "ok"
        or type(report.get("error_count")) is not int
        or report["error_count"] != 0
    ):
        raise RuntimeError("candidate checkpoint verification failed")


def load_verified_canonical_oracle() -> tuple[dict[str, Any], str]:
    """Verify oracle, freeze, lock and every authoritative source before gold use."""
    _verify_candidate_checkpoint()
    oracle_file_sha = file_sha256(ORACLE_PATH)
    freeze_file_sha = file_sha256(ORACLE_FREEZE_PATH)
    verifier = _load_canonical_oracle_verifier()
    report = verifier.verify(ORACLE_PATH, ORACLE_FREEZE_PATH)
    if (
        type(report) is not dict
        or report.get("status") != "ok"
        or type(report.get("error_count")) is not int
        or report["error_count"] != 0
    ):
        raise RuntimeError("canonical oracle/freeze verification failed")
    oracle = strict_json_file(ORACLE_PATH, expected_sha256=oracle_file_sha)
    if file_sha256(ORACLE_FREEZE_PATH) != freeze_file_sha:
        raise RuntimeError("canonical oracle freeze changed during verification")
    observed_payload = verifier.oracle_payload_sha256(oracle)
    if report.get("oracle_payload_sha256") != observed_payload:
        raise RuntimeError("canonical oracle verifier payload mismatch")
    return oracle, oracle_file_sha


def _walk_nodes(body: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    if type(body) is not list:
        return []
    result: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for index, node in enumerate(body):
        if type(node) is not dict:
            continue
        node_path = path + (index,)
        result.append((node_path, node))
        if node.get("kind") == "barrier" and type(node.get("cases")) is list:
            for case in node["cases"]:
                if type(case) is dict:
                    result.extend(
                        _walk_nodes(
                            case.get("body"),
                            node_path + ("case", case.get("outcome")),
                        )
                    )
    return result


def _route_projection(document: Any) -> list[dict[str, Any]] | None:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return None
    return [
        {"path": list(path), "route": node.get("route")}
        for path, node in _walk_nodes(document.get("body"))
        if node.get("kind") == "operation"
    ]


def _data_projection(document: Any) -> list[dict[str, Any]] | None:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return None
    return [
        {"path": list(path), "data_from": node.get("data_from")}
        for path, node in _walk_nodes(document.get("body"))
    ]


def _barrier_projection(document: Any) -> list[dict[str, Any]] | None:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return None
    barriers = []
    for path, node in _walk_nodes(document.get("body")):
        if node.get("kind") != "barrier":
            continue
        barriers.append(
            {
                "path": list(path),
                "barrier": node.get("barrier"),
                "cases": [
                    {
                        "outcome": case.get("outcome"),
                        "owned_body": case.get("body"),
                    }
                    for case in node.get("cases", [])
                    if type(case) is dict
                ],
            }
        )
    return barriers


def evaluate_saved_batch(batch_path: Path) -> dict[str, Any]:
    # Phase 1: the full saved batch and query-only identities are loaded and
    # validated before the gold path is touched.
    batch = strict_json_file(batch_path)
    suite = load_query_suite()
    registry, identity = load_frozen_registry(REGISTRY_PATH)
    records = _validate_saved_batch(batch, suite, registry)
    if batch["registry_payload_sha256"] != identity.payload_sha256:
        raise RuntimeError("batch registry mismatch")

    # Phase 2: gold opens only after the batch checkpoint above succeeded.
    oracle, oracle_file_sha256 = load_verified_canonical_oracle()
    gold = _expected_map(oracle)
    if set(records) != set(gold):
        raise RuntimeError("batch/oracle identity mismatch")

    rows: list[dict[str, Any]] = []
    panel_counts = {
        panel: {
            "total": total,
            "valid": 0,
            "semantic_exact": 0,
            "root_exact": 0,
            "operation_graph_applicable": 0,
            "operation_graph_exact": 0,
            "route_exact": 0,
            "data_edge_exact": 0,
            "barrier_applicable": 0,
            "barrier_ownership_exact": 0,
            "system_control_applicable": 0,
            "system_control_exact": 0,
            "unrepresentable_applicable": 0,
            "unrepresentable_exact": 0,
            "technical_invalid": 0,
            "document_invalid": 0,
        }
        for panel, total in (("canonical_120", 120), ("typed_controls_4", 4))
    }
    for key in sorted(records):
        record = records[key]
        panel, case_id = key.split("\0", 1)
        extraction = record.get("extraction")
        expected_normalized = normalize_document(gold[key], registry)
        expected_projection = project_normalized(expected_normalized)
        expected_document = semantic_document_json(expected_normalized.document)
        if record.get("status") != "fake_transport_complete" or type(extraction) is not dict:
            status = "technical_invalid"
            exact = False
            actual_sha = None
            actual_document = None
        else:
            status = extraction.get("status")
            projection = extraction.get("projection_result")
            actual_sha = projection.get("semantic_sha256") if type(projection) is dict else None
            actual_document = projection.get("semantic_document") if type(projection) is dict else None
            exact = actual_sha == expected_projection.semantic_sha256
        expected_kind = expected_document["kind"]
        actual_kind = actual_document.get("kind") if type(actual_document) is dict else None
        root_exact = actual_kind == expected_kind
        operation_applicable = expected_kind == "operation_graph"
        operation_exact = operation_applicable and exact
        route_exact = operation_applicable and _route_projection(actual_document) == _route_projection(expected_document)
        data_edge_exact = operation_applicable and _data_projection(actual_document) == _data_projection(expected_document)
        expected_barriers = _barrier_projection(expected_document)
        barrier_applicable = operation_applicable and bool(expected_barriers)
        barrier_exact = barrier_applicable and _barrier_projection(actual_document) == expected_barriers
        system_applicable = expected_kind == "system_control"
        system_exact = system_applicable and actual_document == expected_document
        unrep_applicable = expected_kind == "unrepresentable"
        unrep_exact = unrep_applicable and actual_document == expected_document
        if status in {"valid_representable", "valid_unrepresentable"}:
            panel_counts[panel]["valid"] += 1
        elif status == "technical_invalid":
            panel_counts[panel]["technical_invalid"] += 1
        else:
            panel_counts[panel]["document_invalid"] += 1
        if exact:
            panel_counts[panel]["semantic_exact"] += 1
        for field, value in (
            ("root_exact", root_exact),
            ("operation_graph_applicable", operation_applicable),
            ("operation_graph_exact", operation_exact),
            ("route_exact", route_exact),
            ("data_edge_exact", data_edge_exact),
            ("barrier_applicable", barrier_applicable),
            ("barrier_ownership_exact", barrier_exact),
            ("system_control_applicable", system_applicable),
            ("system_control_exact", system_exact),
            ("unrepresentable_applicable", unrep_applicable),
            ("unrepresentable_exact", unrep_exact),
        ):
            panel_counts[panel][field] += int(value)
        rows.append(
            {
                "panel": panel,
                "opaque_case_id": case_id,
                "query_sha256": record["query_sha256"],
                "extraction_status": status,
                "semantic_exact": exact,
                "root_exact": root_exact,
                "operation_graph_applicable": operation_applicable,
                "operation_graph_exact": operation_exact,
                "route_exact": route_exact,
                "data_edge_exact": data_edge_exact,
                "barrier_applicable": barrier_applicable,
                "barrier_ownership_exact": barrier_exact,
                "system_control_applicable": system_applicable,
                "system_control_exact": system_exact,
                "unrepresentable_applicable": unrep_applicable,
                "unrepresentable_exact": unrep_exact,
                "actual_semantic_sha256": actual_sha,
            }
        )
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "status": "ok",
        "gold_opened_after_complete_batch_validation": True,
        "oracle_sha256": oracle_file_sha256,
        "batch_sha256": sha256(batch_path.read_bytes()).hexdigest(),
        "panel_counts": panel_counts,
        "legacy_panel": {
            "count": 34,
            "evaluated_here": False,
            "authority": "phase1_typed_oracle_v1",
            "cross_panel_compensation": False,
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_saved_batch(args.batch)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
