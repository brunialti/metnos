#!/usr/bin/env python3
"""Independent V25.2 scorer with explicit NOT_EVALUATED semantics.

Candidate 1.0 is immutable.  This evaluator never turns transport, JSON,
schema, validator, or fail-closed outcomes into semantic true negatives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


VERSION = "metnos.v25.2-evaluator/0.1"


def _attempt_errors(result: dict[str, Any]) -> list[str]:
    return [
        str(error.get("code", "unknown"))
        for attempt in result.get("attempts", [])
        for error in attempt.get("validation", {}).get("errors", [])
    ]


def classify_record(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result") or {}
    frame = result.get("frame")
    heads = frame.get("semantic_heads", []) if isinstance(frame, dict) else []
    errors = _attempt_errors(result)
    transport = any(code in {"transport_or_json", "transport", "json"} for code in errors)
    evaluable = (
        result.get("status") == "projected"
        and bool(heads)
        and all(isinstance(h.get("desired_observation"), dict) for h in heads)
    )
    if evaluable:
        semantic_status = "PASS" if bool(record.get("binding_ok")) else "FAIL"
    else:
        semantic_status = "NOT_EVALUATED"
    return {
        "id": record.get("case", {}).get("id"),
        "expect_binding": bool(record.get("case", {}).get("expect_binding")),
        "semantic_status": semantic_status,
        "evaluable": evaluable,
        "transport_failure": transport,
        "errors": errors,
        "predicted_binding": record.get("predicted_binding") if evaluable else None,
        "binding_ok": bool(record.get("binding_ok")) if evaluable else None,
    }


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    rows = [classify_record(record) for record in data.get("records", [])]
    evaluable = [row for row in rows if row["evaluable"]]
    positives = [row for row in evaluable if row["expect_binding"]]
    negatives = [row for row in evaluable if not row["expect_binding"]]
    exact = sum(bool(row["binding_ok"]) for row in evaluable)
    positive_hits = sum(row["predicted_binding"] is True for row in positives)
    leakage = sum(row["predicted_binding"] is True for row in negatives)
    return {
        "evaluator_version": VERSION,
        "input_candidate_version": data.get("version"),
        "summary": {
            "records": len(rows),
            "evaluable": len(evaluable),
            "not_evaluated": len(rows) - len(evaluable),
            "transport_failures": sum(row["transport_failure"] for row in rows),
            "binding_exact_count": exact,
            "binding_exact_denominator": len(evaluable),
            "binding_exact_rate": exact / len(evaluable) if evaluable else None,
            "positive_hits": positive_hits,
            "positive_evaluable_denominator": len(positives),
            "positive_recall": positive_hits / len(positives) if positives else None,
            "negative_leakage_count": leakage,
            "negative_evaluable_denominator": len(negatives),
            "negative_leakage_rate": leakage / len(negatives) if negatives else None,
        },
        "records": rows,
    }


def outage_mutation() -> dict[str, Any]:
    synthetic = {
        "version": "synthetic-outage",
        "records": [
            {
                "case": {"id": "positive.outage", "expect_binding": True},
                "result": {"status": "fail_closed", "attempts": [{"validation": {"errors": [{"code": "transport_or_json"}]}}]},
                "predicted_binding": False,
                "binding_ok": False,
            },
            {
                "case": {"id": "negative.outage", "expect_binding": False},
                "result": {"status": "fail_closed", "attempts": [{"validation": {"errors": [{"code": "transport_or_json"}]}}]},
                "predicted_binding": False,
                "binding_ok": True,
            },
        ],
    }
    result = evaluate(synthetic)
    summary = result["summary"]
    passed = (
        summary["evaluable"] == 0
        and summary["not_evaluated"] == 2
        and summary["binding_exact_denominator"] == 0
        and summary["binding_exact_rate"] is None
        and summary["positive_recall"] is None
        and summary["negative_leakage_rate"] is None
    )
    return {"pass": passed, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?")
    parser.add_argument("--output")
    parser.add_argument("--outage-self-test", action="store_true")
    args = parser.parse_args()
    if args.outage_self_test:
        mutation = outage_mutation()
        print(json.dumps(mutation, sort_keys=True))
        if not mutation["pass"]:
            return 1
    if args.input:
        path = Path(args.input)
        result = evaluate(json.loads(path.read_text()))
        result["input_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
