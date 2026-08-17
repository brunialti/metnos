#!/usr/bin/env python3
"""Post-seal evaluator for RUN 2 using the symmetric v0.3 metric."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

from .protocol import (
    AUTHORIZATION_PATH,
    CANDIDATE_PROJECTION_PATH,
    CRITICAL_COLUMNS,
    HERE,
    MANIFEST_PATH,
    ROOT,
    RUN_ID,
    RUNNER_VERSION,
    SEALED_BATCH_FORMAT,
    TechnicalLimits,
    canonical_json_bytes,
    file_sha256,
    load_manifest,
    load_protocol,
    strict_json_file,
    strict_json_loads,
)
from .runner import DEFAULT_PATHS, RunPaths, _content_from_http, _extract, validate_authorization
from .verify import verify_frozen_files


EVALUATOR_VERSION = "metnos.intent-live-evaluator/0.3-run2"
ORACLE_PATH = HERE.parents[1] / "intent_shadow_oracle_v0_1.json"
ORACLE_FREEZE_PATH = HERE.parents[1] / "intent_shadow_oracle_v0_1.freeze.json"
ORACLE_VERIFY_PATH = HERE.parents[1] / "verify_oracle.py"
PHASE1_PATH = HERE.parents[3] / "oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json"
PHASE1_CONTROLS_PATH = HERE.parents[3] / "question_focus_controls_v1.json"
PHASE1_SHA256 = "e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af"
PHASE1_CONTROLS_SHA256 = "22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf"
LARGE_LIMITS = TechnicalLimits(64 * 1024 * 1024, 96, 500_000, 2 * 1024 * 1024, 64)


def _exact(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def compare_replay(saved: Any, replay: Any, arm: str, record_index: int) -> dict[str, Any] | None:
    if _exact(saved, replay):
        return None
    if arm != "A" or type(saved) is not dict or type(replay) is not dict:
        raise RuntimeError(f"replay mismatch:{record_index}")
    saved_metadata = saved.get("adapter_metadata")
    replay_metadata = replay.get("adapter_metadata")
    field = "implicit_actions_ignored"
    if (
        type(saved_metadata) is not dict or type(replay_metadata) is not dict
        or field not in saved_metadata or field not in replay_metadata
        or type(saved_metadata[field]) is not bool or type(replay_metadata[field]) is not bool
        or saved_metadata[field] == replay_metadata[field]
    ):
        raise RuntimeError(f"unexpected replay difference:{record_index}")
    normalized_saved = deepcopy(saved)
    normalized_replay = deepcopy(replay)
    normalized_saved["adapter_metadata"][field] = False
    normalized_replay["adapter_metadata"][field] = False
    if not _exact(normalized_saved, normalized_replay):
        raise RuntimeError(f"unexpected replay difference:{record_index}")
    return {
        "record_index_zero_based": record_index,
        "json_pointer": "adapter_metadata.implicit_actions_ignored",
        "saved": saved_metadata[field], "replay": replay_metadata[field],
        "reason": "approved_exact_boolean_diagnostic_variance",
    }


def _validate_marker_checkpoint(
    marker: Any, checkpoint: Any, batch: dict[str, Any],
    manifest: dict[str, Any], paths: RunPaths,
) -> None:
    expected_marker = {
        "runner_version": RUNNER_VERSION, "run_id": RUN_ID,
        "state": "measurement_consumed_first_post_accepted",
        "authorization_sha256": batch["authorization_sha256"],
        "protocol_freeze_sha256": file_sha256(HERE / "protocol_run2.freeze.json"),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "accepted_http_posts": 316, "first_request_ordinal": 1,
    }
    expected_checkpoint = {
        "runner_version": RUNNER_VERSION, "run_id": RUN_ID,
        "state": "in_progress",
        "authorization_sha256": batch["authorization_sha256"],
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "record_count": 316, "accepted_http_posts": 316,
        "last_request_ordinal": 316,
        "journal_sha256": file_sha256(paths.journal),
    }
    if not _exact(marker, expected_marker):
        raise RuntimeError("consumption mismatch")
    if not _exact(checkpoint, expected_checkpoint):
        raise RuntimeError("checkpoint mismatch")


def validate_complete_batch(
    paths: RunPaths = DEFAULT_PATHS, *, authorization_path: Path = AUTHORIZATION_PATH,
    require_authorization: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate and replay every byte before any gold-loader may be called."""
    verify_frozen_files()
    protocol = load_protocol()
    manifest = load_manifest()
    batch = strict_json_file(paths.sealed_batch, LARGE_LIMITS)
    seal = strict_json_file(paths.seal, LARGE_LIMITS)
    marker = strict_json_file(paths.consumption)
    checkpoint = strict_json_file(paths.checkpoint)
    if require_authorization:
        validate_authorization(authorization_path)
        if file_sha256(authorization_path) != batch.get("authorization_sha256"):
            raise RuntimeError("authorization hash mismatch")
    batch_keys = {
        "batch_format", "runner_version", "run_id", "state", "gold_opened",
        "authorization_sha256", "protocol_file_sha256", "protocol_freeze_file_sha256",
        "manifest_file_sha256", "manifest_payload_sha256", "record_count",
        "accepted_http_posts", "stopped_reason", "records",
    }
    if type(batch) is not dict or set(batch) != batch_keys:
        raise RuntimeError("batch closed schema")
    if (
        batch["batch_format"] != SEALED_BATCH_FORMAT or batch["runner_version"] != RUNNER_VERSION
        or batch["run_id"] != RUN_ID or batch["state"] != "complete"
        or batch["gold_opened"] is not False or batch["record_count"] != 316
        or type(batch["record_count"]) is not int or batch["accepted_http_posts"] != 316
        or type(batch["accepted_http_posts"]) is not int or batch["stopped_reason"] is not None
        or type(batch["records"]) is not list or len(batch["records"]) != 316
        or batch["manifest_payload_sha256"] != manifest["manifest_payload_sha256"]
        or batch["protocol_file_sha256"] != file_sha256(HERE / "protocol_run2.json")
        or batch["protocol_freeze_file_sha256"] != file_sha256(HERE / "protocol_run2.freeze.json")
        or batch["manifest_file_sha256"] != file_sha256(MANIFEST_PATH)
    ):
        raise RuntimeError("batch completeness or binding")
    expected_seal = {
        "freeze_format": "metnos.intent-live-batch-seal/0.3-run2",
        "algorithm": "sha256", "run_id": RUN_ID, "state": "complete",
        "batch_file": paths.sealed_batch.name, "batch_sha256": file_sha256(paths.sealed_batch),
        "journal_sha256": file_sha256(paths.journal),
        "consumption_marker_sha256": file_sha256(paths.consumption),
        "authorization_sha256": batch["authorization_sha256"],
        "record_count": 316, "accepted_http_posts": 316,
        "stopped_reason": None,
    }
    if not _exact(seal, expected_seal):
        raise RuntimeError("seal mismatch")
    _validate_marker_checkpoint(marker, checkpoint, batch, manifest, paths)
    lines = paths.journal.read_bytes().splitlines()
    if len(lines) != 316:
        raise RuntimeError("journal count")
    tolerances: list[dict[str, Any]] = []
    for index, (record, public, raw_line) in enumerate(zip(batch["records"], manifest["records"], lines, strict=True)):
        if canonical_json_bytes(strict_json_loads(raw_line, LARGE_LIMITS)) != canonical_json_bytes(record):
            raise RuntimeError(f"journal record mismatch:{index}")
        for key in ("request_ordinal", "sample_index", "panel", "panel_ordinal", "opaque_case_id", "query_sha256", "arm", "request_sha256"):
            if type(record.get(key)) is not type(public[key]) or record.get(key) != public[key]:
                raise RuntimeError(f"record identity:{index}:{key}")
        record_keys = {
            "request_ordinal", "sample_index", "panel", "panel_ordinal",
            "opaque_case_id", "query_sha256", "arm", "request_sha256",
            "http_accepted", "http_status", "elapsed_ms", "transport_error",
            "raw_http_response", "model_content_b64", "model_content_sha256",
            "extraction",
        }
        if type(record) is not dict or set(record) != record_keys:
            raise RuntimeError(f"record closed schema:{index}")
        if (
            record.get("http_accepted") is not True or type(record.get("http_status")) is not int
            or type(record.get("elapsed_ms")) is not int or record["elapsed_ms"] < 0
            or record.get("transport_error") is not None
        ):
            raise RuntimeError(f"record transport:{index}")
        raw = record.get("raw_http_response")
        if type(raw) is not dict or set(raw) != {"body_b64", "body_sha256", "headers"}:
            raise RuntimeError(f"raw schema:{index}")
        if type(raw["body_b64"]) is not str or type(raw["body_sha256"]) is not str or type(raw["headers"]) is not list:
            raise RuntimeError(f"raw types:{index}")
        raw_bytes = base64.b64decode(raw["body_b64"], validate=True)
        if sha256(raw_bytes).hexdigest() != raw["body_sha256"]:
            raise RuntimeError(f"raw hash:{index}")
        content, wrapper_error = _content_from_http(raw_bytes)
        if content is None or wrapper_error is not None:
            raise RuntimeError(f"complete response wrapper:{index}")
        if type(record["model_content_b64"]) is not str or type(record["model_content_sha256"]) is not str or type(record["extraction"]) is not dict:
            raise RuntimeError(f"content types:{index}")
        if base64.b64decode(record["model_content_b64"], validate=True) != content or sha256(content).hexdigest() != record["model_content_sha256"]:
            raise RuntimeError(f"content binding:{index}")
        replay = _extract(public, content)
        tolerance = compare_replay(record["extraction"], replay, record["arm"], index)
        if tolerance is not None:
            tolerances.append(tolerance)
    return batch, manifest, {
        "status": "pass", "records": 316, "raw_replayed": 316,
        "unexpected_differences": 0, "allowed_differences": tolerances,
        "oracle_opened": False,
    }


def _walk(body: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if type(body) is not list:
        return result
    for node in body:
        if type(node) is not dict:
            continue
        result.append(node)
        if node.get("kind") == "barrier" and type(node.get("cases")) is list:
            for case in node["cases"]:
                if type(case) is dict:
                    result.extend(_walk(case.get("body")))
    return result


def _routes(document: Any) -> list[str]:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return []
    return [node["route"] for node in _walk(document.get("body")) if node.get("kind") == "operation" and type(node.get("route")) is str]


def _barriers(document: Any) -> list[Any]:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return []
    return [node for node in _walk(document.get("body")) if node.get("kind") == "barrier"]


def _canonical_edge(edge: Any, destination_ports: list[str], operations: dict[int, dict[str, Any]]) -> Any:
    if type(edge) is not dict:
        return deepcopy(edge)
    result = deepcopy(edge)
    source = result.get("from")
    source_metadata = operations.get(source) if type(source) is int else None
    source_ports = source_metadata.get("output_ports") if type(source_metadata) is dict else None
    if type(source_ports) is list and len(source_ports) == 1 and result.get("output") == source_ports[0]:
        result.pop("output", None)
    if len(destination_ports) == 1 and result.get("input") == destination_ports[0]:
        result.pop("input", None)
    return result


def _canonical_body(body: Any, registry: dict[str, Any], operations: dict[int, dict[str, Any]], next_ordinal: list[int]) -> Any:
    if type(body) is not list:
        return deepcopy(body)
    result: list[Any] = []
    for raw in body:
        if type(raw) is not dict:
            result.append(deepcopy(raw))
            continue
        node = deepcopy(raw)
        kind = node.get("kind")
        metadata = registry.get("operations", {}).get(node.get("route"), {}) if kind == "operation" else {}
        ports = metadata.get("input_ports", []) if type(metadata) is dict else []
        edges = node.get("data_from")
        if edges == []:
            node.pop("data_from", None)
        elif type(edges) is list:
            node["data_from"] = [_canonical_edge(edge, ports, operations) for edge in edges]
        if kind == "operation":
            operations[next_ordinal[0]] = metadata
            next_ordinal[0] += 1
        elif kind == "barrier" and type(node.get("cases")) is list:
            cases = []
            for raw_case in node["cases"]:
                if type(raw_case) is not dict:
                    cases.append(deepcopy(raw_case))
                    continue
                case = deepcopy(raw_case)
                case["body"] = _canonical_body(case.get("body"), registry, operations, next_ordinal)
                if case["body"] != []:
                    cases.append(case)
            node["cases"] = cases
        result.append(node)
    return result


def canonical_semantic_projection(document: Any, registry: dict[str, Any]) -> Any:
    result = deepcopy(document)
    if type(result) is dict and result.get("kind") == "operation_graph":
        result["body"] = _canonical_body(result.get("body"), registry, {}, [0])
    return result


def _actual(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    extraction = record.get("extraction")
    if type(extraction) is not dict:
        return "technical_invalid", None
    document = extraction.get("semantic_document")
    return extraction.get("status", "technical_invalid"), document if type(document) is dict else None


def _row(record: dict[str, Any], expected: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    status, actual = _actual(record)
    expected_canonical = canonical_semantic_projection(expected, registry)
    actual_canonical = canonical_semantic_projection(actual, registry)
    expected_kind = expected.get("kind")
    actual_kind = actual.get("kind") if actual else None
    expected_routes = _routes(expected_canonical)
    actual_routes = _routes(actual_canonical)
    expected_barriers = _barriers(expected_canonical)
    actual_barriers = _barriers(actual_canonical)
    expected_unrep = expected_kind == "unrepresentable"
    actual_unrep = actual_kind == "unrepresentable"
    false_action = bool(set(actual_routes) - set(expected_routes)) or (bool(actual_routes) and not expected_routes)
    return {
        "sample_index": record["sample_index"], "panel": record["panel"],
        "opaque_case_id": record["opaque_case_id"], "query_sha256": record["query_sha256"],
        "arm": record["arm"], "extraction_status": status,
        "semantic_exact_canonical": actual_canonical == expected_canonical,
        "root_exact": actual_kind == expected_kind,
        "correct_abstention": expected_unrep and actual_canonical == expected_canonical,
        "incorrect_abstention": actual_unrep and not (expected_unrep and actual_canonical == expected_canonical),
        "technical_valid": status != "technical_invalid",
        "false_action_avoided": not false_action,
        "undo_exact": expected_kind != "system_control" or actual_canonical == expected_canonical,
        "consent_exact": not expected_barriers or actual_barriers == expected_barriers,
        "negation_exact": not false_action,
        "branch_ownership_exact": not expected_barriers or actual_barriers == expected_barriers,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns = (*CRITICAL_COLUMNS, "incorrect_abstention")
    return {
        "total": len(rows),
        **{column: sum(int(row[column]) for row in rows) for column in columns},
        "extraction_status_counts": dict(sorted(Counter(row["extraction_status"] for row in rows).items())),
    }


def _load_gold() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any], dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("run2_oracle_verifier", ORACLE_VERIFY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("oracle verifier import")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.verify(ORACLE_PATH, ORACLE_FREEZE_PATH)
    if report.get("error_count") != 0:
        raise RuntimeError("oracle verification")
    oracle = strict_json_file(ORACLE_PATH, LARGE_LIMITS)
    gold = {("canonical_120", case["case_id"]): case["expected"] for case in oracle["cases"]}
    gold.update({("typed_controls_4", case["control_id"]): case["expected"] for case in oracle["new_controls"]})
    if len(gold) != 124:
        raise RuntimeError("gold count")
    if file_sha256(PHASE1_PATH) != PHASE1_SHA256 or file_sha256(PHASE1_CONTROLS_PATH) != PHASE1_CONTROLS_SHA256:
        raise RuntimeError("phase1 authority drift")
    return gold, strict_json_file(PHASE1_PATH, LARGE_LIMITS), strict_json_file(PHASE1_CONTROLS_PATH, LARGE_LIMITS)


def evaluate(paths: RunPaths = DEFAULT_PATHS, *, authorization_path: Path = AUTHORIZATION_PATH) -> dict[str, Any]:
    batch, _manifest, pre_gold = validate_complete_batch(paths, authorization_path=authorization_path)
    # Gold boundary: the loader is first referenced after complete validation/replay.
    gold, phase1, controls = _load_gold()
    registry = strict_json_file(CANDIDATE_PROJECTION_PATH, LARGE_LIMITS)
    rows: list[dict[str, Any]] = []
    legacy_records: list[dict[str, Any]] = []
    for record in batch["records"]:
        if record["panel"] == "legacy_phase1_34":
            legacy_records.append(record)
        else:
            rows.append(_row(record, gold[(record["panel"], record["opaque_case_id"])], registry))
    grouped = {
        f"{panel}.{arm}": _aggregate([row for row in rows if row["panel"] == panel and row["arm"] == arm])
        for panel in ("canonical_120", "typed_controls_4") for arm in ("A", "B")
    }
    canonical_a = grouped["canonical_120.A"]
    canonical_b = grouped["canonical_120.B"]
    typed_b = grouped["typed_controls_4.B"]
    delta = canonical_b["semantic_exact_canonical"] - canonical_a["semantic_exact_canonical"]
    regressions = [column for column in CRITICAL_COLUMNS if canonical_b[column] < canonical_a[column]]
    special = typed_b["semantic_exact_canonical"] == 4
    verdict = "candidate_pass" if delta >= 3 and special and not regressions else ("inconclusive" if -2 <= delta <= 2 else "candidate_fail")
    expected_binding = {case["id"]: case["expect_binding"] for case in controls["cases"]}
    legacy_rows = []
    for record in legacy_records:
        status, actual = _actual(record)
        direct = canonical_semantic_projection(actual, registry) == {"kind": "operation_graph", "body": [{"kind": "operation", "route": "get/location"}]}
        legacy_rows.append({"opaque_case_id": record["opaque_case_id"], "arm": record["arm"], "query_sha256": record["query_sha256"], "expected_direct_binding": expected_binding[record["opaque_case_id"]], "actual_direct_binding": direct, "direct_binding_exact": direct is expected_binding[record["opaque_case_id"]], "extraction_status": status})
    return {
        "evaluator_version": EVALUATOR_VERSION, "run_id": RUN_ID, "status": "ok",
        "pre_gold_replay_gate": pre_gold, "gold_opened_after_complete_gate": True,
        "evaluation_executed": True, "batch_sha256": file_sha256(paths.sealed_batch),
        "oracle_sha256": file_sha256(ORACLE_PATH), "semantic_headline": "semantic_exact_canonical",
        "canonicalization_policy": {"symmetric_expected_and_actual": True, "may_omit_empty_data_from": True, "may_omit_uniquely_derivable_ports": True, "may_omit_materialized_empty_outcomes": True, "may_change_root_route_order_edge_or_reason": False},
        "typed_panels": grouped, "canonical_delta_B_minus_A": delta,
        "critical_columns": list(CRITICAL_COLUMNS), "critical_regressions": regressions,
        "typed_special_4_of_4": special, "verdict": verdict, "rows": rows,
        "legacy_phase1": {"authority_version": phase1["version"], "separate": True, "automatic_conversion": False, "cross_panel_compensation": False, "arms": {arm: {"total": sum(row["arm"] == arm for row in legacy_rows), "direct_binding_exact": sum(row["arm"] == arm and row["direct_binding_exact"] for row in legacy_rows)} for arm in ("A", "B")}, "rows": legacy_rows},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        return 1
    try:
        report = evaluate()
    except Exception as exc:
        print(json.dumps({"evaluator_version": EVALUATOR_VERSION, "status": "error", "evaluation_executed": False, "error": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 1
    rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
