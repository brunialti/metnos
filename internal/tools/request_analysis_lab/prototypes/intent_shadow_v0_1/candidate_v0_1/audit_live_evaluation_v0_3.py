#!/usr/bin/env python3
"""Read-only post-evaluation audit for the single 0.3 output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import file_sha256, strict_json_file
from live_evaluator_v0_3 import (
    EFFECTIVE_CRITICAL_COLUMNS,
    OUTPUT_PATH,
    PRESERVED_V0_2_COLUMNS,
)


HERE = Path(__file__).resolve().parent
HISTORICAL_PATH = HERE / "live_evaluation_v0_2_retry1.json"
SELFCHECK_PATH = HERE / "live_evaluation_v0_3_selfcheck.json"


def _check(condition: bool, code: str, errors: list[str]) -> None:
    if not condition:
        errors.append(code)


def audit(path: Path = OUTPUT_PATH) -> dict[str, Any]:
    report = strict_json_file(path)
    historical = strict_json_file(HISTORICAL_PATH)
    errors: list[str] = []
    _check(report.get("status") == "ok", "STATUS", errors)
    _check(report.get("evaluation_executed") is True, "EXECUTED", errors)
    _check(report.get("semantic_headline") == "semantic_exact_canonical", "HEADLINE", errors)
    _check(report.get("historical_semantic_column") == "semantic_exact_v0_2_raw", "RAW_LABEL", errors)
    _check(report.get("effective_critical_columns_v0_3") == list(EFFECTIVE_CRITICAL_COLUMNS), "CRITICAL_COLUMNS", errors)
    _check(len(report.get("effective_critical_columns_v0_3", [])) == 9, "CRITICAL_COUNT", errors)

    panels = report.get("typed_panels", {})
    expected_totals = {
        "canonical_120.A": 120,
        "canonical_120.B": 120,
        "typed_controls_4.A": 4,
        "typed_controls_4.B": 4,
    }
    for key, total in expected_totals.items():
        _check(type(panels.get(key)) is dict, f"PANEL_MISSING:{key}", errors)
        if type(panels.get(key)) is dict:
            _check(panels[key].get("total") == total, f"PANEL_TOTAL:{key}", errors)

    canonical_a = panels.get("canonical_120.A", {})
    canonical_b = panels.get("canonical_120.B", {})
    typed_b = panels.get("typed_controls_4.B", {})
    _check(canonical_a.get("semantic_exact_canonical") == 75, "CANONICAL_A", errors)
    _check(canonical_b.get("semantic_exact_canonical") == 25, "CANONICAL_B", errors)
    _check(canonical_a.get("semantic_exact_v0_2_raw") == 29, "RAW_A", errors)
    _check(canonical_b.get("semantic_exact_v0_2_raw") == 9, "RAW_B", errors)
    corrected = (
        canonical_a.get("semantic_exact_canonical", 0)
        - canonical_a.get("semantic_exact_v0_2_raw", 0)
        + canonical_b.get("semantic_exact_canonical", 0)
        - canonical_b.get("semantic_exact_v0_2_raw", 0)
    )
    _check(corrected == 62, "FALSE_NEGATIVE_CORRECTION", errors)
    _check(typed_b.get("semantic_exact_canonical") == 0, "TYPED_B", errors)
    _check(report.get("canonical_delta_B_minus_A") == -50, "DELTA", errors)
    _check(report.get("verdict") == "candidate_fail", "VERDICT", errors)
    _check(report.get("typed_special_4_of_4") is False, "SPECIAL_GATE", errors)

    for key in expected_totals:
        current = panels.get(key, {})
        previous = historical.get("typed_panels", {}).get(key, {})
        for column in PRESERVED_V0_2_COLUMNS:
            _check(current.get(column) == previous.get(column), f"PRESERVED:{key}:{column}", errors)
        _check(current.get("accepted_root_exact") == previous.get("root_exact"), f"ACCEPTED_ROOT:{key}", errors)

    historical_rows = {
        (row["panel"], row["opaque_case_id"], row["arm"]): row
        for row in historical.get("rows", [])
    }
    current_rows = report.get("rows", [])
    _check(len(current_rows) == 248, "ROW_COUNT", errors)
    for row in current_rows:
        key = (row.get("panel"), row.get("opaque_case_id"), row.get("arm"))
        previous = historical_rows.get(key)
        _check(type(previous) is dict, f"ROW_IDENTITY:{key}", errors)
        if type(previous) is not dict:
            continue
        for column in (
            "sample_index", "panel", "opaque_case_id", "query_sha256", "arm",
            "extraction_status", *PRESERVED_V0_2_COLUMNS,
        ):
            _check(row.get(column) == previous.get(column), f"ROW_PRESERVED:{key}:{column}", errors)
        _check(row.get("semantic_exact_v0_2_raw") == previous.get("semantic_exact"), f"ROW_RAW:{key}", errors)
        _check("semantic_exact" not in row, f"AMBIGUOUS_HEADLINE:{key}", errors)

    for arm in ("A", "B"):
        consent = panels.get(f"canonical_120.{arm}", {}).get("applicable_metrics", {}).get("consent_exact", {})
        branch = panels.get(f"canonical_120.{arm}", {}).get("applicable_metrics", {}).get("branch_ownership_exact", {})
        control = panels.get(f"canonical_120.{arm}", {}).get("applicable_metrics", {}).get("system_control_exact", {})
        _check(consent.get("passed_applicable") == 0 and consent.get("applicable") == 3, f"CONSENT_DENOM:{arm}", errors)
        _check(branch.get("passed_applicable") == 0 and branch.get("applicable") == 3, f"BRANCH_DENOM:{arm}", errors)
        _check(control.get("passed_applicable") == 2 and control.get("applicable") == 2, f"CONTROL_DENOM:{arm}", errors)

    _check(report.get("legacy_phase1") == historical.get("legacy_phase1"), "LEGACY_PHASE1", errors)
    pre_gold = report.get("pre_gold_replay_gate", {})
    _check(pre_gold.get("status") == "pass", "PRE_GOLD_STATUS", errors)
    _check(pre_gold.get("records_verified") == 316, "PRE_GOLD_COUNT", errors)
    _check(pre_gold.get("unexpected_mismatch_count") == 0, "PRE_GOLD_UNEXPECTED", errors)

    return {
        "audit_version": "metnos.intent-shadow-live-evaluation-audit/0.3",
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "evaluation_sha256": file_sha256(path),
        "historical_evaluation_sha256": file_sha256(HISTORICAL_PATH),
        "checks": {
            "false_negatives_corrected": corrected,
            "canonical_A": canonical_a.get("semantic_exact_canonical"),
            "canonical_B": canonical_b.get("semantic_exact_canonical"),
            "raw_A": canonical_a.get("semantic_exact_v0_2_raw"),
            "raw_B": canonical_b.get("semantic_exact_v0_2_raw"),
            "typed_B": typed_b.get("semantic_exact_canonical"),
            "canonical_delta_B_minus_A": report.get("canonical_delta_B_minus_A"),
            "verdict": report.get("verdict"),
            "rows": len(current_rows),
            "critical_columns": len(report.get("effective_critical_columns_v0_3", [])),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.evaluation)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        if args.output.exists():
            raise RuntimeError("self-check output already exists")
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
