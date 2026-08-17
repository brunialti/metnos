#!/usr/bin/env python3
"""Post-seal evaluator 0.2 with the approved pre-gold replay gate.

The module preserves the frozen 120/4/34 panels and verdict criteria from
evaluator 0.1.  It may open typed and Phase-1 gold only after replay gate 0.2
has validated all 316 saved records and the entire sealed measurement chain.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import file_sha256, strict_json_file
from live_evaluator import (
    PHASE1_CONTROLS_PATH,
    PHASE1_PATH,
    _actual_document,
    _aggregate,
    _aggregate_legacy,
    _gold_map,
    _row,
    _verified_gold,
    typed_verdict,
)
from live_protocol import CRITICAL_NO_REGRESSION_COLUMNS, PROTOCOL_FREEZE_PATH, load_protocol
from live_replay_gate_v0_2 import (
    BATCH_PATH,
    FREEZE_PATH,
    ROOT,
    SEAL_PATH,
    validate_pre_gold,
)


EVALUATOR_VERSION = "metnos.intent-shadow-live-evaluator/0.2"


def _verified_phase1_after_gate() -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the separate legacy gold to the historical measurement freeze."""
    protocol_freeze = strict_json_file(PROTOCOL_FREEZE_PATH)
    authorities = protocol_freeze.get("repo_authorities")
    if type(authorities) is not dict:
        raise RuntimeError("historical Phase-1 authority map missing")
    for path in (PHASE1_PATH, PHASE1_CONTROLS_PATH):
        relative = str(path.relative_to(ROOT))
        expected = authorities.get(relative)
        if type(expected) is not str or file_sha256(path) != expected:
            raise RuntimeError(f"historical Phase-1 authority drift:{relative}")
    return strict_json_file(PHASE1_PATH), strict_json_file(PHASE1_CONTROLS_PATH)


def evaluate(
    batch_path: Path = BATCH_PATH,
    seal_path: Path = SEAL_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> dict[str, Any]:
    batch, _manifest, pre_gold = validate_pre_gold(batch_path, seal_path, freeze_path)

    # Gold boundary: no oracle or Phase-1 expected artifact is imported or
    # opened above this line.  A failed replay gate raises before this point.
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
            grouped[f"{panel}.{arm}"] = _aggregate(
                [row for row in rows if row["panel"] == panel and row["arm"] == arm]
            )
    protocol = load_protocol()
    decision = typed_verdict(
        grouped["canonical_120.A"],
        grouped["canonical_120.B"],
        grouped["typed_controls_4.B"],
        protocol["evaluation"]["critical_columns_no_regression"],
    )

    phase1, controls = _verified_phase1_after_gate()
    expected_binding = {case["id"]: case["expect_binding"] for case in controls["cases"]}
    legacy_rows: list[dict[str, Any]] = []
    for record in legacy_records:
        status, actual = _actual_document(record)
        direct = actual == {
            "kind": "operation_graph",
            "body": [{"kind": "operation", "route": "get/location", "data_from": []}],
        }
        legacy_rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "arm": record["arm"],
            "query_sha256": record["query_sha256"],
            "expected_direct_binding": expected_binding[record["opaque_case_id"]],
            "actual_direct_binding": direct,
            "direct_binding_exact": direct is expected_binding[record["opaque_case_id"]],
            "extraction_status": status,
        })
    legacy_grouped = {
        arm: _aggregate_legacy([row for row in legacy_rows if row["arm"] == arm])
        for arm in ("A", "B")
    }
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "status": "ok",
        "pre_gold_replay_gate": pre_gold,
        "gold_opened_after_v0_2_gate": True,
        "evaluation_executed": True,
        "batch_sha256": file_sha256(batch_path),
        "oracle_sha256": file_sha256(Path(__file__).resolve().parent.parent / "intent_shadow_oracle_v0_1.json"),
        "critical_columns_no_regression": list(CRITICAL_NO_REGRESSION_COLUMNS),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, default=BATCH_PATH)
    parser.add_argument("--seal", type=Path, default=SEAL_PATH)
    parser.add_argument("--freeze", type=Path, default=FREEZE_PATH)
    args = parser.parse_args()
    try:
        report = evaluate(args.batch, args.seal, args.freeze)
    except Exception as exc:
        print(json.dumps({
            "evaluator_version": EVALUATOR_VERSION,
            "status": "error",
            "evaluation_executed": False,
            "error": f"{type(exc).__name__}:{exc}",
        }, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
