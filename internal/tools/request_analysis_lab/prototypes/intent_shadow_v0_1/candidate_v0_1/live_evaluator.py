#!/usr/bin/env python3
"""Post-seal paired evaluator for the single approved measurement.

The module opens no gold until the 316-record batch, its separate seal, the
protocol, and the single-use consumption evidence have all validated.  The 34
legacy records remain a separate Phase-1 panel and never compensate the 120 or
four typed controls.
"""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import canonical_json_bytes, file_sha256, strict_json_file, strict_json_loads
from live_protocol import (
    CRITICAL_NO_REGRESSION_COLUMNS,
    HERE,
    REQUEST_MANIFEST_PATH,
    load_protocol,
    load_request_manifest,
)


EVALUATOR_VERSION = "metnos.intent-shadow-live-evaluator/0.1"
ORACLE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.json"
ORACLE_FREEZE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.freeze.json"
ORACLE_VERIFY_PATH = HERE.parent / "verify_oracle.py"
PHASE1_PATH = HERE.parents[2] / "oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json"
PHASE1_CONTROLS_PATH = HERE.parents[2] / "question_focus_controls_v1.json"


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


def _validated_complete_batch(batch_path: Path, seal_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = strict_json_file(batch_path)
    seal = strict_json_file(seal_path)
    manifest = load_request_manifest(REQUEST_MANIFEST_PATH)
    batch_keys = {
        "batch_format", "runner_version", "state", "gold_opened",
        "authorization_sha256", "protocol_file_sha256", "protocol_freeze_file_sha256",
        "manifest_file_sha256", "manifest_payload_sha256", "record_count",
        "accepted_http_posts", "stopped_reason", "records",
    }
    if type(batch) is not dict or set(batch) != batch_keys:
        raise RuntimeError("sealed batch closed schema mismatch")
    if (
        batch["batch_format"] != "metnos.intent-shadow-live-sealed-batch/0.1"
        or batch["state"] != "complete"
        or batch["gold_opened"] is not False
        or type(batch["record_count"]) is not int
        or batch["record_count"] != 316
        or type(batch["accepted_http_posts"]) is not int
        or batch["accepted_http_posts"] != 316
        or batch["stopped_reason"] is not None
        or type(batch["records"]) is not list
        or len(batch["records"]) != 316
        or batch["manifest_payload_sha256"] != manifest["manifest_payload_sha256"]
    ):
        raise RuntimeError("sealed batch completeness mismatch")
    seal_keys = {
        "freeze_format", "algorithm", "batch_sha256", "journal_sha256",
        "consumption_marker_sha256", "authorization_sha256", "record_count",
        "accepted_http_posts",
    }
    if type(seal) is not dict or set(seal) != seal_keys:
        raise RuntimeError("sealed batch freeze closed schema mismatch")
    if (
        seal["freeze_format"] != "metnos.intent-shadow-live-sealed-batch-freeze/0.1"
        or seal["algorithm"] != "sha256"
        or seal["batch_sha256"] != file_sha256(batch_path)
        or seal["authorization_sha256"] != batch["authorization_sha256"]
        or seal["record_count"] != 316
        or seal["accepted_http_posts"] != 316
    ):
        raise RuntimeError("sealed batch freeze binding mismatch")
    journal_path = batch_path.parent / "live_run_journal_v0_1.jsonl"
    marker_path = batch_path.parent / "live_run_consumption_v0_1.json"
    if (
        not journal_path.is_file()
        or not marker_path.is_file()
        or seal["journal_sha256"] != file_sha256(journal_path)
        or seal["consumption_marker_sha256"] != file_sha256(marker_path)
    ):
        raise RuntimeError("sealed batch journal/consumption binding mismatch")
    marker = strict_json_file(marker_path)
    if (
        type(marker) is not dict
        or set(marker) != {
            "runner_version", "state", "authorization_sha256",
            "protocol_freeze_sha256", "manifest_payload_sha256",
            "accepted_http_posts", "first_request_ordinal",
        }
        or marker["state"] != "measurement_consumed_first_post_accepted"
        or marker["authorization_sha256"] != batch["authorization_sha256"]
        or marker["manifest_payload_sha256"] != manifest["manifest_payload_sha256"]
        or type(marker["accepted_http_posts"]) is not int
        or marker["accepted_http_posts"] != 316
        or type(marker["first_request_ordinal"]) is not int
        or marker["first_request_ordinal"] != 1
    ):
        raise RuntimeError("consumption marker schema/binding mismatch")
    journal_lines = journal_path.read_bytes().splitlines()
    if len(journal_lines) != 316:
        raise RuntimeError("journal count mismatch")
    for index, (record, expected, journal_raw) in enumerate(
        zip(batch["records"], manifest["records"], journal_lines, strict=True)
    ):
        required = {
            "request_ordinal", "sample_index", "panel", "panel_ordinal",
            "opaque_case_id", "query_sha256", "arm", "request_sha256",
            "http_accepted", "http_status", "elapsed_ms", "transport_error",
            "raw_http_response", "model_content_b64", "model_content_sha256", "extraction",
        }
        if type(record) is not dict or set(record) != required:
            raise RuntimeError(f"saved record schema mismatch at {index}")
        journal_record = strict_json_loads(journal_raw)
        if canonical_json_bytes(journal_record) != canonical_json_bytes(record):
            raise RuntimeError(f"journal record mismatch at {index}")
        exact = {
            "request_ordinal": expected["request_ordinal"],
            "sample_index": expected["sample_index"],
            "panel": expected["panel"],
            "panel_ordinal": expected["panel_ordinal"],
            "opaque_case_id": expected["opaque_case_id"],
            "query_sha256": expected["query_sha256"],
            "arm": expected["arm"],
            "request_sha256": expected["request_sha256"],
            "http_accepted": True,
        }
        for key, value in exact.items():
            if type(record.get(key)) is not type(value) or record.get(key) != value:
                raise RuntimeError(f"saved record binding mismatch at {index}:{key}")
        if type(record["http_status"]) is not int or type(record["elapsed_ms"]) is not int or record["elapsed_ms"] < 0:
            raise RuntimeError(f"saved record transport type mismatch at {index}")
        if record["transport_error"] is not None and type(record["transport_error"]) is not str:
            raise RuntimeError(f"saved record transport error type at {index}")
        raw = record["raw_http_response"]
        if type(raw) is not dict or set(raw) != {"body_b64", "body_sha256", "headers"}:
            raise RuntimeError(f"saved raw response schema at {index}")
        if type(raw["body_b64"]) is not str or type(raw["body_sha256"]) is not str or type(raw["headers"]) is not list:
            raise RuntimeError(f"saved raw response types at {index}")
        try:
            raw_bytes = base64.b64decode(raw["body_b64"], validate=True)
        except Exception as exc:
            raise RuntimeError(f"saved raw base64 at {index}") from exc
        if sha256(raw_bytes).hexdigest() != raw["body_sha256"]:
            raise RuntimeError(f"saved raw hash at {index}")
        if record["model_content_b64"] is not None:
            if type(record["model_content_b64"]) is not str or type(record["model_content_sha256"]) is not str:
                raise RuntimeError(f"saved content types at {index}")
            content = base64.b64decode(record["model_content_b64"], validate=True)
            if sha256(content).hexdigest() != record["model_content_sha256"]:
                raise RuntimeError(f"saved content hash at {index}")
            if record["arm"] == "A":
                from live_arm_current import extract_response
                replayed = extract_response(
                    content, query=expected["query"], language=expected["language"]
                )
            else:
                from live_arm_candidate import extract_response
                replayed = extract_response(content)
            if canonical_json_bytes(replayed) != canonical_json_bytes(record["extraction"]):
                raise RuntimeError(f"saved extraction replay mismatch at {index}")
        elif record["model_content_sha256"] is not None:
            raise RuntimeError(f"saved content null coherence at {index}")
        elif record["extraction"] is not None:
            raise RuntimeError(f"saved extraction without content at {index}")
        if record["extraction"] is not None and type(record["extraction"]) is not dict:
            raise RuntimeError(f"saved extraction type at {index}")
    return batch, manifest


def _verified_gold() -> dict[str, Any]:
    # Import occurs only after batch validation in ``evaluate``.
    import importlib.util
    spec = importlib.util.spec_from_file_location("live_pinned_oracle_verifier", ORACLE_VERIFY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("oracle verifier import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.verify(ORACLE_PATH, ORACLE_FREEZE_PATH)
    if type(report) is not dict or report.get("status") != "ok" or report.get("error_count") != 0:
        raise RuntimeError("oracle canonical verification failed")
    return strict_json_file(ORACLE_PATH)


def _gold_map(oracle: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {("canonical_120", case["case_id"]): case["expected"] for case in oracle["cases"]}
    result.update({("typed_controls_4", case["control_id"]): case["expected"] for case in oracle["new_controls"]})
    if len(result) != 124:
        raise RuntimeError("gold map count mismatch")
    return result


def _actual_document(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    extraction = record["extraction"]
    if type(extraction) is not dict:
        return "technical_invalid", None
    status = extraction.get("status")
    projection = extraction.get("projection_result")
    document = projection.get("semantic_document") if type(projection) is dict else None
    return status if type(status) is str else "technical_invalid", document if type(document) is dict else None


def _row(record: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    status, actual = _actual_document(record)
    expected_kind = expected["kind"]
    actual_kind = actual.get("kind") if actual else None
    exact = actual == expected
    expected_routes = _routes(expected)
    actual_routes = _routes(actual)
    expected_barriers = _barriers(expected)
    actual_barriers = _barriers(actual)
    expected_unrep = expected_kind == "unrepresentable"
    actual_unrep = actual_kind == "unrepresentable"
    false_action = bool(set(actual_routes) - set(expected_routes)) or (bool(actual_routes) and not expected_routes)
    return {
        "sample_index": record["sample_index"],
        "panel": record["panel"],
        "opaque_case_id": record["opaque_case_id"],
        "query_sha256": record["query_sha256"],
        "arm": record["arm"],
        "extraction_status": status,
        "semantic_exact": exact,
        "root_exact": actual_kind == expected_kind,
        "correct_abstention": expected_unrep and actual == expected,
        "incorrect_abstention": actual_unrep and not (expected_unrep and actual == expected),
        "technical_valid": status != "technical_invalid",
        "false_action_avoided": not false_action,
        "undo_exact": expected_kind != "system_control" or actual == expected,
        "consent_exact": not expected_barriers or actual_barriers == expected_barriers,
        "negation_exact": not false_action,
        "branch_ownership_exact": not expected_barriers or actual_barriers == expected_barriers,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns = (
        "semantic_exact", "root_exact", "correct_abstention", "incorrect_abstention",
        "technical_valid", "false_action_avoided", "undo_exact", "consent_exact",
        "negation_exact", "branch_ownership_exact",
    )
    return {"total": len(rows), **{column: sum(int(row[column]) for row in rows) for column in columns}}


def typed_verdict(
    canonical_a: dict[str, Any],
    canonical_b: dict[str, Any],
    typed_b: dict[str, Any],
    protocol_columns: list[str],
) -> dict[str, Any]:
    """Apply the frozen typed verdict using its exact canonical column set."""
    if (
        type(protocol_columns) is not list
        or any(type(column) is not str for column in protocol_columns)
        or len(protocol_columns) != len(set(protocol_columns))
        or set(protocol_columns) != set(CRITICAL_NO_REGRESSION_COLUMNS)
    ):
        raise RuntimeError("critical no-regression column set mismatch")
    for label, aggregate in (("canonical_A", canonical_a), ("canonical_B", canonical_b)):
        if type(aggregate) is not dict:
            raise RuntimeError(f"{label} aggregate type mismatch")
        for column in CRITICAL_NO_REGRESSION_COLUMNS:
            if type(aggregate.get(column)) is not int:
                raise RuntimeError(f"{label} aggregate column type mismatch:{column}")
    if type(typed_b) is not dict or type(typed_b.get("semantic_exact")) is not int:
        raise RuntimeError("typed_B aggregate mismatch")

    delta = canonical_b["semantic_exact"] - canonical_a["semantic_exact"]
    regressions = [
        column
        for column in CRITICAL_NO_REGRESSION_COLUMNS
        if canonical_b[column] < canonical_a[column]
    ]
    special_pass = typed_b["semantic_exact"] == 4
    if delta >= 3 and special_pass and not regressions:
        verdict = "candidate_pass"
    elif -2 <= delta <= 2:
        verdict = "inconclusive"
    else:
        verdict = "candidate_fail"
    return {
        "canonical_delta_B_minus_A": delta,
        "critical_columns_no_regression": list(CRITICAL_NO_REGRESSION_COLUMNS),
        "critical_regressions": regressions,
        "typed_special_4_of_4": special_pass,
        "verdict": verdict,
    }


def evaluate(batch_path: Path, seal_path: Path) -> dict[str, Any]:
    batch, manifest = _validated_complete_batch(batch_path, seal_path)
    protocol = load_protocol()
    # Gold boundary: nothing above imports or opens the oracle/Phase-1 files.
    oracle = _verified_gold()
    gold = _gold_map(oracle)
    rows: list[dict[str, Any]] = []
    legacy_records: list[dict[str, Any]] = []
    for record in batch["records"]:
        if record["panel"] == "legacy_phase1_34":
            legacy_records.append(record)
            continue
        expected = gold[(record["panel"], record["opaque_case_id"])]
        rows.append(_row(record, expected))
    grouped: dict[str, dict[str, Any]] = {}
    for panel in ("canonical_120", "typed_controls_4"):
        for arm in ("A", "B"):
            grouped[f"{panel}.{arm}"] = _aggregate([row for row in rows if row["panel"] == panel and row["arm"] == arm])
    decision = typed_verdict(
        grouped["canonical_120.A"],
        grouped["canonical_120.B"],
        grouped["typed_controls_4.B"],
        protocol["evaluation"]["critical_columns_no_regression"],
    )

    phase1 = strict_json_file(PHASE1_PATH)
    controls = strict_json_file(PHASE1_CONTROLS_PATH)
    expected_binding = {case["id"]: case["expect_binding"] for case in controls["cases"]}
    legacy_rows: list[dict[str, Any]] = []
    for record in legacy_records:
        status, actual = _actual_document(record)
        direct = actual == {"kind": "operation_graph", "body": [{"kind": "operation", "route": "get/location", "data_from": []}]}
        legacy_rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "arm": record["arm"],
            "query_sha256": record["query_sha256"],
            "expected_direct_binding": expected_binding[record["opaque_case_id"]],
            "actual_direct_binding": direct,
            "direct_binding_exact": direct is expected_binding[record["opaque_case_id"]],
            "extraction_status": status,
        })
    legacy_grouped = {arm: _aggregate_legacy([row for row in legacy_rows if row["arm"] == arm]) for arm in ("A", "B")}
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "status": "ok",
        "gold_opened_after_sealed_complete_batch_validation": True,
        "batch_sha256": file_sha256(batch_path),
        "oracle_sha256": file_sha256(ORACLE_PATH),
        "typed_panels": grouped,
        **decision,
        "rows": rows,
        "legacy_phase1": {
            "authority_version": phase1["version"],
            "separate": True,
            "automatic_conversion": False,
            "cross_panel_compensation": False,
            "arms": legacy_grouped,
            "rows": legacy_rows,
        },
    }


def _aggregate_legacy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"total": len(rows), "direct_binding_exact": sum(int(row["direct_binding_exact"]) for row in rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--seal", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.batch, args.seal), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
