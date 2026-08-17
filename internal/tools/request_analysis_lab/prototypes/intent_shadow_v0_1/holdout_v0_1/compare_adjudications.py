"""Mechanical, non-adjudicating comparison of the two blind RUN4 reviews."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .validate_adjudication import validate


ROOT = Path(__file__).resolve().parent
DEFAULT_A = ROOT / "reviews" / "reviewer_a" / "adjudication_a.json"
DEFAULT_B = ROOT / "reviews" / "reviewer_b" / "adjudication_b.json"
DEFAULT_OUTPUT = ROOT / "reviews" / "comparison_blind.json"


class ComparisonError(ValueError):
    """The blind adjudications cannot be compared safely."""


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot load {path}") from exc


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ComparisonError(f"cannot hash {path}") from exc


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def compare(path_a: Path = DEFAULT_A, path_b: Path = DEFAULT_B) -> dict[str, Any]:
    summary_a = validate(path_a)
    summary_b = validate(path_b)
    if summary_a["reviewer_id"] == summary_b["reviewer_id"]:
        raise ComparisonError("reviewer IDs must be distinct")
    a = _load(path_a)
    b = _load(path_b)
    if a["query_panel_sha256"] != b["query_panel_sha256"]:
        raise ComparisonError("reviewers used different query panels")
    if len(a["cases"]) != len(b["cases"]):
        raise ComparisonError("case lengths differ")

    disagreements: list[dict[str, Any]] = []
    low_confidence: list[dict[str, Any]] = []
    expected_agreement = 0
    safety_agreement = 0
    full_agreement = 0
    disagreement_types: Counter[str] = Counter()
    for case_a, case_b in zip(a["cases"], b["cases"], strict=True):
        if case_a["case_id"] != case_b["case_id"] or case_a["query_sha256"] != case_b["query_sha256"]:
            raise ComparisonError("case identity/order differs")
        expected_equal = case_a["expected"] == case_b["expected"]
        safety_equal = case_a["safety_applicability"] == case_b["safety_applicability"]
        expected_agreement += int(expected_equal)
        safety_agreement += int(safety_equal)
        full_agreement += int(expected_equal and safety_equal)
        if not expected_equal or not safety_equal:
            kinds: list[str] = []
            if not expected_equal:
                if case_a["expected"].get("kind") != case_b["expected"].get("kind"):
                    kinds.append("root")
                else:
                    kinds.append("expected_detail")
            if not safety_equal:
                kinds.append("safety")
            for kind in kinds:
                disagreement_types[kind] += 1
            disagreements.append(
                {
                    "case_id": case_a["case_id"],
                    "query_sha256": case_a["query_sha256"],
                    "difference_types": kinds,
                    "reviewer_a_expected": case_a["expected"],
                    "reviewer_b_expected": case_b["expected"],
                    "reviewer_a_safety": case_a["safety_applicability"],
                    "reviewer_b_safety": case_b["safety_applicability"],
                    "reviewer_a_confidence": case_a["confidence"],
                    "reviewer_b_confidence": case_b["confidence"],
                }
            )
        if case_a["confidence"] == "low" or case_b["confidence"] == "low":
            low_confidence.append(
                {
                    "case_id": case_a["case_id"],
                    "query_sha256": case_a["query_sha256"],
                    "reviewer_a_confidence": case_a["confidence"],
                    "reviewer_b_confidence": case_b["confidence"],
                }
            )

    return {
        "format_version": "metnos.intent-holdout-blind-comparison/1.0",
        "status": "agreement_complete" if full_agreement == len(a["cases"]) and not low_confidence else "arbitration_required",
        "query_panel_sha256": a["query_panel_sha256"],
        "reviewers": {
            "a": {"id": a["reviewer_id"], "file_sha256": _sha256(path_a)},
            "b": {"id": b["reviewer_id"], "file_sha256": _sha256(path_b)},
        },
        "counts": {
            "cases": len(a["cases"]),
            "expected_agreement": expected_agreement,
            "safety_agreement": safety_agreement,
            "full_agreement": full_agreement,
            "disagreements": len(disagreements),
            "low_confidence_union": len(low_confidence),
            "disagreement_types": dict(disagreement_types),
        },
        "disagreements": disagreements,
        "low_confidence": low_confidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-a", type=Path, default=DEFAULT_A)
    parser.add_argument("--review-b", type=Path, default=DEFAULT_B)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        result = compare(args.review_a, args.review_b)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(_canonical(result))
    except (ComparisonError, ValueError) as exc:
        print(json.dumps({"status": "fail", "error_count": 1, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": result["status"], "error_count": 0, **result["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
