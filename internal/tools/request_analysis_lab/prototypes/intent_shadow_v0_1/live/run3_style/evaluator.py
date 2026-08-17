#!/usr/bin/env python3
"""Post-seal four-arm evaluator using the unchanged symmetric v0.3 metric."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from statistics import median
from typing import Any

from ..run2.evaluator import (
    _aggregate as semantic_aggregate_v03,
    _row as semantic_row_v03,
    canonical_semantic_projection,
)
from .protocol import (
    ARMS,
    AUTHORIZATION_PATH,
    CANDIDATE_PROJECTION_PATH,
    CRITICAL_COLUMNS,
    HERE,
    MANIFEST_PATH,
    REQUEST_COUNT,
    ROOT,
    RUN2_EVALUATION_PATH,
    RUN2_BATCH_PATH,
    RUN_ID,
    RUNNER_VERSION,
    SAFETY_COLUMNS,
    SEALED_BATCH_FORMAT,
    TechnicalLimits,
    canonical_json_bytes,
    file_sha256,
    load_manifest,
    verify_hash_seed_environment,
    strict_json_file,
    strict_json_loads,
)
from .runner import DEFAULT_PATHS, RunPaths, _content_from_http, _extract, validate_authorization
from .verify import verify_frozen_files


EVALUATOR_VERSION = "metnos.intent-prompt-style-live-evaluator/0.1-run3"
ORACLE_PATH = HERE.parents[1] / "intent_shadow_oracle_v0_1.json"
ORACLE_FREEZE_PATH = HERE.parents[1] / "intent_shadow_oracle_v0_1.freeze.json"
ORACLE_VERIFY_PATH = HERE.parents[1] / "verify_oracle.py"
PHASE1_PATH = HERE.parents[3] / "oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json"
PHASE1_CONTROLS_PATH = HERE.parents[3] / "question_focus_controls_v1.json"
PHASE1_SHA256 = "e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af"
PHASE1_CONTROLS_SHA256 = "22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf"
LARGE_LIMITS = TechnicalLimits(96 * 1024 * 1024, 96, 750_000, 2 * 1024 * 1024, 64)


def _exact(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def compare_replay(saved: Any, replay: Any, _arm: str, record_index: int) -> None:
    """Every arm reuses its frozen RUN2 adapter with no replay tolerance."""
    if not _exact(saved, replay):
        raise RuntimeError(f"replay mismatch:{record_index}")


def _validate_marker_checkpoint(
    marker: Any, checkpoint: Any, batch: dict[str, Any],
    manifest: dict[str, Any], paths: RunPaths,
) -> None:
    expected_marker = {
        "runner_version": RUNNER_VERSION, "run_id": RUN_ID,
        "state": "measurement_consumed_first_post_accepted",
        "authorization_sha256": batch["authorization_sha256"],
        "protocol_freeze_sha256": file_sha256(HERE / "protocol_run3_style.freeze.json"),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "accepted_http_posts": REQUEST_COUNT, "first_request_ordinal": 1,
    }
    expected_checkpoint = {
        "runner_version": RUNNER_VERSION, "run_id": RUN_ID,
        "state": "in_progress", "authorization_sha256": batch["authorization_sha256"],
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "record_count": REQUEST_COUNT, "accepted_http_posts": REQUEST_COUNT,
        "last_request_ordinal": REQUEST_COUNT,
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
    verify_hash_seed_environment()
    verify_frozen_files()
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
        batch["batch_format"] != SEALED_BATCH_FORMAT
        or batch["runner_version"] != RUNNER_VERSION or batch["run_id"] != RUN_ID
        or batch["state"] != "complete" or batch["gold_opened"] is not False
        or type(batch["record_count"]) is not int or batch["record_count"] != REQUEST_COUNT
        or type(batch["accepted_http_posts"]) is not int or batch["accepted_http_posts"] != REQUEST_COUNT
        or batch["stopped_reason"] is not None
        or type(batch["records"]) is not list or len(batch["records"]) != REQUEST_COUNT
        or batch["manifest_payload_sha256"] != manifest["manifest_payload_sha256"]
        or batch["protocol_file_sha256"] != file_sha256(HERE / "protocol_run3_style.json")
        or batch["protocol_freeze_file_sha256"] != file_sha256(HERE / "protocol_run3_style.freeze.json")
        or batch["manifest_file_sha256"] != file_sha256(MANIFEST_PATH)
    ):
        raise RuntimeError("batch completeness or binding")
    expected_seal = {
        "freeze_format": "metnos.intent-prompt-style-live-batch-seal/0.1-run3",
        "algorithm": "sha256", "run_id": RUN_ID, "state": "complete",
        "batch_file": paths.sealed_batch.name,
        "batch_sha256": file_sha256(paths.sealed_batch),
        "journal_sha256": file_sha256(paths.journal),
        "consumption_marker_sha256": file_sha256(paths.consumption),
        "authorization_sha256": batch["authorization_sha256"],
        "record_count": REQUEST_COUNT, "accepted_http_posts": REQUEST_COUNT,
        "stopped_reason": None,
    }
    if not _exact(seal, expected_seal):
        raise RuntimeError("seal mismatch")
    _validate_marker_checkpoint(marker, checkpoint, batch, manifest, paths)
    lines = paths.journal.read_bytes().splitlines()
    if len(lines) != REQUEST_COUNT:
        raise RuntimeError("journal count")
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
            "raw_http_response", "model_content_b64", "model_content_sha256", "extraction",
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
        compare_replay(record["extraction"], _extract(public, content), record["arm"], index)
    return batch, manifest, {
        "status": "pass", "records": REQUEST_COUNT, "raw_replayed": REQUEST_COUNT,
        "unexpected_differences": 0, "allowed_differences": [], "oracle_opened": False,
    }


def _response_usage(record: dict[str, Any]) -> tuple[int, int, int]:
    raw = base64.b64decode(record["raw_http_response"]["body_b64"], validate=True)
    wrapper = strict_json_loads(raw, LARGE_LIMITS)
    usage = wrapper.get("usage") if type(wrapper) is dict else None
    if type(usage) is not dict:
        raise RuntimeError("response usage missing")
    values = tuple(usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
    if any(type(value) is not int or value < 0 for value in values):
        raise RuntimeError("response usage invalid")
    if values[0] + values[1] != values[2]:
        raise RuntimeError("response usage total invalid")
    return values  # type: ignore[return-value]


def _row(record: dict[str, Any], expected: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    row = semantic_row_v03(record, expected, registry)
    prompt_tokens, completion_tokens, total_tokens = _response_usage(record)
    row.update({
        "elapsed_ms": record["elapsed_ms"], "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens, "total_tokens": total_tokens,
    })
    return row


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = semantic_aggregate_v03(rows)
    elapsed = [row["elapsed_ms"] for row in rows]
    result["latency_ms"] = {
        "sum": sum(elapsed), "mean": sum(elapsed) / len(elapsed),
        "median": median(elapsed), "max": max(elapsed),
    }
    result["token_usage"] = {
        key: {
            "sum": sum(row[key] for row in rows),
            "mean": sum(row[key] for row in rows) / len(rows),
            "median": median(row[key] for row in rows),
            "max": max(row[key] for row in rows),
        }
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    return result


def _load_gold() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any], dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("run3_style_oracle_verifier", ORACLE_VERIFY_PATH)
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


def _pairwise(canonical: dict[str, dict[str, Any]], *, attributable: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for left_index, left in enumerate(ARMS):
        for right in ARMS[left_index + 1:]:
            left_deltas = {column: canonical[left][column] - canonical[right][column] for column in CRITICAL_COLUMNS}
            right_deltas = {column: -value for column, value in left_deltas.items()}
            headline = left_deltas["semantic_exact_canonical"]
            left_regressions = [column for column in SAFETY_COLUMNS if left_deltas[column] < 0]
            right_regressions = [column for column in SAFETY_COLUMNS if right_deltas[column] < 0]
            left_pass = attributable and headline >= 3 and not left_regressions
            right_pass = attributable and headline <= -3 and not right_regressions
            if not attributable:
                interpretation = "non_attributable_anchor_drift"
            elif -3 < headline < 3:
                interpretation = "inconclusive_below_three_cases"
            elif left_pass:
                interpretation = "left_better"
            elif right_pass:
                interpretation = "right_better"
            else:
                interpretation = "headline_gain_blocked_by_safety"
            result[f"{left}_vs_{right}"] = {
                "left_arm": left, "right_arm": right,
                "left_minus_right_deltas": left_deltas,
                "right_minus_left_deltas": right_deltas,
                "headline_interpretation": interpretation,
                "left_safety_regressions": left_regressions,
                "right_safety_regressions": right_regressions,
                "left_style_pass": left_pass,
                "right_style_pass": right_pass,
                "combined_score": None,
            }
    return result


def _run2_anchor(
    batch: dict[str, Any], grouped: dict[str, dict[str, Any]],
    legacy: dict[str, int], *, run3_arm: str, run2_arm: str,
) -> dict[str, Any]:
    run2_batch = strict_json_file(RUN2_BATCH_PATH, LARGE_LIMITS)
    run2_records = [record for record in run2_batch["records"] if record.get("arm") == run2_arm]
    run3_records = [record for record in batch["records"] if record.get("arm") == run3_arm]
    if len(run2_records) != 158 or len(run3_records) != 158:
        raise RuntimeError("S0 anchor case count")
    run2_by_case = {record["sample_index"]: record for record in run2_records}
    run3_by_case = {record["sample_index"]: record for record in run3_records}
    if set(run2_by_case) != set(range(158)) or set(run3_by_case) != set(range(158)):
        raise RuntimeError("S0 anchor case identity")
    raw_drift: list[int] = []
    extraction_drift: list[int] = []
    for index in range(158):
        before = run2_by_case[index]
        after = run3_by_case[index]
        if (
            before.get("model_content_sha256") != after.get("model_content_sha256")
            or before.get("model_content_b64") != after.get("model_content_b64")
        ):
            raw_drift.append(index)
        if not _exact(before.get("extraction"), after.get("extraction")):
            extraction_drift.append(index)
    run2 = strict_json_file(RUN2_EVALUATION_PATH, LARGE_LIMITS)
    aggregate_fields = (*CRITICAL_COLUMNS, "incorrect_abstention", "extraction_status_counts", "total")
    aggregate_drift: list[str] = []
    for panel in ("canonical_120", "typed_controls_4"):
        before = run2["typed_panels"][f"{panel}.{run2_arm}"]
        after = grouped[f"{panel}.{run3_arm}"]
        aggregate_drift.extend(
            f"{panel}.{field}"
            for field in aggregate_fields
            if not _exact(before.get(field), after.get(field))
        )
    run2_legacy = run2["legacy_phase1"]["arms"][run2_arm]
    aggregate_drift.extend(
        f"legacy_phase1.{field}"
        for field in ("total", "direct_binding_exact")
        if not _exact(run2_legacy.get(field), legacy.get(field))
    )
    exact = not raw_drift and not extraction_drift and not aggregate_drift
    return {
        "cases": 158,
        "run3_arm": run3_arm,
        "run2_arm": run2_arm,
        "raw_exact_count": 158 - len(raw_drift),
        "extraction_exact_count": 158 - len(extraction_drift),
        "aggregate_exact": not aggregate_drift,
        "raw_drift_sample_indices": raw_drift,
        "extraction_drift_sample_indices": extraction_drift,
        "aggregate_drift_fields": aggregate_drift,
        "exact": exact,
        "verdict": "attributable" if exact else "non_attributable_anchor_drift",
    }


def _run2_anchors(
    batch: dict[str, Any], grouped: dict[str, dict[str, Any]],
    legacy_by_arm: dict[str, dict[str, int]],
) -> dict[str, Any]:
    anchors = {
        "A_SYSTEM_CURRENT": _run2_anchor(
            batch, grouped, legacy_by_arm["A_SYSTEM_CURRENT"],
            run3_arm="A_SYSTEM_CURRENT", run2_arm="A",
        ),
        "S0_CURRENT": _run2_anchor(
            batch, grouped, legacy_by_arm["S0_CURRENT"],
            run3_arm="S0_CURRENT", run2_arm="B",
        ),
    }
    both_exact = all(anchor["exact"] for anchor in anchors.values())
    return {
        "anchors": anchors,
        "both_exact": both_exact,
        "verdict": "attributable" if both_exact else "non_attributable_anchor_drift",
    }


def evaluate(paths: RunPaths = DEFAULT_PATHS, *, authorization_path: Path = AUTHORIZATION_PATH) -> dict[str, Any]:
    batch, _manifest, pre_gold = validate_complete_batch(paths, authorization_path=authorization_path)
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
        for panel in ("canonical_120", "typed_controls_4") for arm in ARMS
    }
    canonical = {arm: grouped[f"canonical_120.{arm}"] for arm in ARMS}
    expected_binding = {case["id"]: case["expect_binding"] for case in controls["cases"]}
    legacy_rows = []
    for record in legacy_records:
        extraction = record.get("extraction")
        status = extraction.get("status", "technical_invalid") if type(extraction) is dict else "technical_invalid"
        actual = extraction.get("semantic_document") if type(extraction) is dict else None
        direct = canonical_semantic_projection(actual, registry) == {"kind": "operation_graph", "body": [{"kind": "operation", "route": "get/location"}]}
        legacy_rows.append({
            "opaque_case_id": record["opaque_case_id"], "arm": record["arm"],
            "query_sha256": record["query_sha256"],
            "expected_direct_binding": expected_binding[record["opaque_case_id"]],
            "actual_direct_binding": direct,
            "direct_binding_exact": direct is expected_binding[record["opaque_case_id"]],
            "extraction_status": status,
        })
    legacy_by_arm = {
        arm: {
            "total": sum(row["arm"] == arm for row in legacy_rows),
            "direct_binding_exact": sum(row["arm"] == arm and row["direct_binding_exact"] for row in legacy_rows),
        }
        for arm in ARMS
    }
    run2_anchors = _run2_anchors(batch, grouped, legacy_by_arm)
    pairwise = _pairwise(canonical, attributable=run2_anchors["both_exact"])
    return {
        "evaluator_version": EVALUATOR_VERSION, "run_id": RUN_ID, "status": "ok",
        "pre_gold_replay_gate": pre_gold, "gold_opened_after_complete_gate": True,
        "evaluation_executed": True, "batch_sha256": file_sha256(paths.sealed_batch),
        "oracle_sha256": file_sha256(ORACLE_PATH),
        "semantic_metric": "v0.3-symmetric-reused-from-run2",
        "semantic_metric_source_sha256": file_sha256(HERE.parent / "run2" / "evaluator.py"),
        "canonicalization_policy": {
            "symmetric_expected_and_actual": True, "may_omit_empty_data_from": True,
            "may_omit_uniquely_derivable_ports": True,
            "may_omit_materialized_empty_outcomes": True,
            "may_change_root_route_order_edge_or_reason": False,
        },
        "typed_panels": grouped, "canonical_by_arm": canonical,
        "pairwise": pairwise, "combined_score": None,
        "run2_anchors": run2_anchors,
        "style_measure_valid": run2_anchors["both_exact"],
        "style_verdict": run2_anchors["verdict"],
        "all_pairwise_claims_require_both_anchors_exact": True,
        "critical_columns": list(CRITICAL_COLUMNS),
        "safety_columns": list(SAFETY_COLUMNS), "rows": rows,
        "legacy_phase1": {
            "authority_version": phase1["version"], "separate": True,
            "automatic_conversion": False, "cross_panel_compensation": False,
            "arms": legacy_by_arm,
            "rows": legacy_rows,
        },
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
