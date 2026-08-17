#!/usr/bin/env python3
"""Relation-only multilingual diagnostic for the KISS safety classifier."""
from __future__ import annotations

from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.language_tag import (
    normalize_language_tag,
)
from .run_kiss_safe_relation import parse_relations, relation_request
from .run_pilot import HERE, atomic_exclusive, post


CASES = HERE / "kiss_safe_relation_multilingual_cases.json"
OUTPUT = HERE / "kiss_safe_relation_multilingual_v3_results.json"


def load_cases():
    value = json.loads(CASES.read_text(encoding="utf-8"))
    if set(value) != {"format", "cases"} or value["format"] != "metnos.intent-safe-relation-diagnostic/0.1":
        raise RuntimeError("case file")
    rows = value["cases"]
    if type(rows) is not list or len(rows) != 20:
        raise RuntimeError("exactly 20 cases required")
    ids = set()
    queries = set()
    expected_sources = {"direct", "previous_step", "none", "unknown"}
    for row in rows:
        if type(row) is not dict or set(row) != {
            "case_id", "language_tag", "query", "routes", "expected",
        }:
            raise RuntimeError("case schema")
        if row["case_id"] in ids or row["query"] in queries:
            raise RuntimeError("duplicate case")
        ids.add(row["case_id"])
        queries.add(row["query"])
        if normalize_language_tag(row["language_tag"]) != row["language_tag"]:
            raise RuntimeError("language tag")
        if type(row["routes"]) is not list or len(row["routes"]) != 2:
            raise RuntimeError("route pair")
        if any(type(route) is not str or not route for route in row["routes"]):
            raise RuntimeError("route")
        expected = row["expected"]
        if (
            type(expected) is not dict
            or set(expected) != {"target_source", "after_previous"}
            or expected["target_source"] not in expected_sources
            or type(expected["after_previous"]) is not bool
        ):
            raise RuntimeError("expected relation")
    return rows


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    for case in load_cases():
        request = relation_request(case, case["routes"])
        body, elapsed, transport = post(request)
        relation = None
        error = transport
        if transport is None:
            try:
                relation = parse_relations(body, 1)[0]
            except Exception as exc:
                error = type(exc).__name__
        observed = None if relation is None else {
            "uses_previous_result": relation["uses_previous_result"],
            "after_previous": relation["after_previous"],
        }
        expected = {
            "uses_previous_result": case["expected"]["target_source"] == "previous_step",
            "after_previous": case["expected"]["after_previous"],
        }
        exact = observed == expected
        records.append({
            "case_id": case["case_id"],
            "language_tag": case["language_tag"],
            "target_expectation": case["expected"],
            "expected": expected,
            "observed": observed,
            "exact": exact,
            "error": error,
            "elapsed_ms": elapsed,
        })
        print(json.dumps({
            "completed": len(records), "case": case["case_id"], "exact": exact,
        }), flush=True)
        if transport is not None:
            break
    summary = {
        "exact": sum(row["exact"] for row in records),
        "valid": sum(row["observed"] is not None for row in records),
        "total": len(records),
    }
    atomic_exclusive(OUTPUT, {
        "format": "metnos.intent-safe-relation-diagnostic-result/0.1",
        "cases_sha256": sha256(CASES.read_bytes()).hexdigest(),
        "summary": summary,
        "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
