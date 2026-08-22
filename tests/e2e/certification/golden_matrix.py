#!/usr/bin/env python3
"""Freeze and validate the 24 bilingual RM-0006 golden flows."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from certification.coordinator import canonical_json, matrix_digest, validate_record


HERE = Path(__file__).resolve().parent
FLOW_PATH = HERE / "fixtures" / "golden_flows.json"
CASE_PATH = HERE / "fixtures" / "golden_cases.jsonl"
COVERAGE_PATH = HERE / "fixtures" / "golden_coverage.json"

EXPECTED_FAMILIES = {
    "explain_tutor": 3,
    "read_select": 4,
    "compose_dataflow": 4,
    "mutation_undo": 4,
    "device_owner": 3,
    "dialog_resume": 2,
    "durable_work": 2,
    "failure_partial": 2,
}


class MatrixError(ValueError):
    pass


def load_flows(path: Path = FLOW_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "metnos.golden-flows/1":
        raise MatrixError("unsupported golden-flow schema")
    if value.get("oracle_version") != "rm0006-golden-oracle/1":
        raise MatrixError("unexpected oracle version")
    flows = value.get("flows")
    if not isinstance(flows, list):
        raise MatrixError("flows must be a list")
    return value


def expand_cases(document: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    flows = document["flows"]
    flow_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    families: Counter[str] = Counter()
    for flow in flows:
        flow_id = flow.get("logical_flow_id")
        if not isinstance(flow_id, str) or not flow_id:
            raise MatrixError("every flow needs a logical_flow_id")
        if flow_id in flow_ids:
            raise MatrixError(f"duplicate logical_flow_id: {flow_id}")
        flow_ids.add(flow_id)
        family = flow.get("family")
        if family not in EXPECTED_FAMILIES:
            raise MatrixError(f"unknown family for {flow_id}: {family}")
        families[family] += 1
        requests = flow.get("requests")
        if not isinstance(requests, dict) or set(requests) != {"it", "en"}:
            raise MatrixError(f"{flow_id}: requests must contain exactly it/en")
        shared = {
            key: value for key, value in flow.items()
            if key not in {"family", "requests"}
        }
        for locale in ("it", "en"):
            case = {
                "schema_version": "metnos.certification-case/1",
                "case_id": f"{flow_id.replace('.', '-')}--{locale}",
                **shared,
                "locale": locale,
                "request": requests[locale],
            }
            validate_record("CaseSpec", case)
            cases.append(case)
    if families != Counter(EXPECTED_FAMILIES):
        raise MatrixError(
            f"family coverage differs: actual={dict(families)}, "
            f"expected={EXPECTED_FAMILIES}"
        )
    if len(flows) != 24 or len(cases) != 48:
        raise MatrixError(f"matrix size differs: flows={len(flows)}, cases={len(cases)}")
    coverage = {
        "schema_version": "metnos.golden-coverage/1",
        "oracle_version": document["oracle_version"],
        "frozen_on": document["frozen_on"],
        "logical_flows": len(flows),
        "cases": len(cases),
        "locales": {"it": 24, "en": 24},
        "families": dict(sorted(families.items())),
        "required_approvals": sum(flow["required_approval"] for flow in flows),
        "placements": dict(sorted(Counter(
            flow["expected_placement"] for flow in flows
        ).items())),
        "terminals": dict(sorted(Counter(
            flow["expected_terminal"] for flow in flows
        ).items())),
        "matrix_sha256": matrix_digest(cases),
    }
    return cases, coverage


def render_jsonl(cases: list[dict[str, Any]]) -> str:
    return "".join(canonical_json(case) + "\n" for case in cases)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    cases, coverage = expand_cases(load_flows())
    rendered_cases = render_jsonl(cases)
    rendered_coverage = json.dumps(
        coverage, ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n"
    if args.check:
        if not CASE_PATH.exists() or CASE_PATH.read_text(encoding="utf-8") != rendered_cases:
            raise MatrixError("golden_cases.jsonl is not the frozen expansion")
        if not COVERAGE_PATH.exists() or COVERAGE_PATH.read_text(encoding="utf-8") != rendered_coverage:
            raise MatrixError("golden_coverage.json is not the frozen report")
    else:
        CASE_PATH.write_text(rendered_cases, encoding="utf-8")
        COVERAGE_PATH.write_text(rendered_coverage, encoding="utf-8")
    print(rendered_coverage, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
