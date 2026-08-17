#!/usr/bin/env python3
"""Probe one concise root-selection delta over S0, then finish the pilot if useful."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_s0
from .run_pilot import HERE, atomic_exclusive, canonical, extract, post, profiled, validate_cases


OUTPUT = HERE / "s0_final_check_results.json"
FINAL_ANCHOR = "- Exactly one root was selected before writing details."
FINAL_RULES = "\n".join((
    "- ROOT: IF/ELSE -> unsupported_dependency; undo-only -> system_control; undo + operation -> mixed_root_kinds.",
    "- COVERAGE: absent capability -> outside_registry, even when its arguments are missing.",
    "- APPROVAL: confirmation before actions -> get/approval barrier, never get/inputs.",
))
PROBE_IDS = ("bp-004", "bp-006", "bp-007", "bp-014", "bp-020")


def request_for(case, registry):
    request = deepcopy(build_s0(case["query"], registry, case["proposal"].language_tag))
    prompt = request["messages"][0]["content"]
    if prompt.count(FINAL_ANCHOR) != 1:
        raise RuntimeError("prompt anchor")
    prompt = prompt.replace(FINAL_ANCHOR, FINAL_RULES, 1)
    request["messages"][0]["content"] = prompt
    return profiled(request)


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    by_id = {case["proposal"].proposal_id: case for case in cases}
    ordered = [by_id[case_id] for case_id in PROBE_IDS]
    ordered.extend(case for case in cases if case["proposal"].proposal_id not in PROBE_IDS)
    records = []
    for case in ordered:
        request = request_for(case, registry)
        body, elapsed, transport_error = post(request)
        status, semantic, raw, parse_error = ("transport_invalid", None, None, None)
        if transport_error is None:
            status, semantic, raw, parse_error = extract(body, registry)
        exact = status == "valid" and semantic == case["expected"]
        record = {
            "case_id": case["proposal"].proposal_id,
            "status": status,
            "error": transport_error or parse_error,
            "exact": exact,
            "semantic": semantic,
            "expected": case["expected"],
            "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
            "elapsed_ms": elapsed,
            "request_sha256": sha256(canonical(request)).hexdigest(),
        }
        records.append(record)
        print(json.dumps({"completed": len(records), "case": record["case_id"],
                          "status": status, "exact": exact}, sort_keys=True), flush=True)
        if transport_error is not None:
            atomic_exclusive(OUTPUT, {"state": "transport_stop", "records": records})
            return 2
        if len(records) == len(PROBE_IDS):
            probe_exact = sum(item["exact"] for item in records)
            probe_valid = sum(item["status"] == "valid" for item in records)
            if probe_exact < 4 or probe_valid < len(PROBE_IDS):
                atomic_exclusive(OUTPUT, {"state": "stopped_after_probe",
                                          "probe_exact": probe_exact, "probe_valid": probe_valid,
                                          "records": records})
                return 2
    records.sort(key=lambda item: item["case_id"])
    elapsed_values = sorted(item["elapsed_ms"] for item in records)
    summary = {
        "exact": sum(item["exact"] for item in records),
        "valid": sum(item["status"] == "valid" for item in records),
        "document_invalid": sum(item["status"] == "document_invalid" for item in records),
        "technical_invalid": sum(item["status"] == "technical_invalid" for item in records),
        "median_elapsed_ms": elapsed_values[len(elapsed_values) // 2],
    }
    atomic_exclusive(OUTPUT, {
        "format": "metnos.intent-s0-root-fix-pilot/0.1",
        "state": "complete",
        "request_count": len(records),
        "retry_count": 0,
        "prompt_delta_sha256": sha256(FINAL_RULES.encode()).hexdigest(),
        "summary": summary,
        "records": records,
    })
    print(json.dumps({"complete": True, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
