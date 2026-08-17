#!/usr/bin/env python3
"""One universal approval-prompt fix; probe two failures, then finish the 20-case pilot."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_s0
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run2.evaluator import canonical_semantic_projection
from .run_pilot import HERE, atomic_exclusive, extract, post, profiled, validate_cases


OUTPUT = HERE / "approval_fix_compact_v3_results.json"
ANCHOR = "BARRIER: body contains every operation that requires approval."
RULE = "\n".join((
    'ALL ACTIONS NEED APPROVAL: {"kind":"operation_graph","steps":[{"barrier":"get/approval","body":[{"route":"<first>"},{"route":"<next>","from":[0]}]}]}',
    "CONFIRMATION IS APPROVAL, NOT get/inputs. FROM only j<i; the first operation has no from.",
))


def request_for(case, registry):
    request = build_s0(case["query"], registry, case["proposal"].language_tag)
    prompt = request["messages"][0]["content"]
    if prompt.count(ANCHOR) != 1:
        raise RuntimeError("approval prompt anchor")
    request["messages"][0]["content"] = prompt.replace(ANCHOR, RULE, 1)
    return profiled(request)


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    by_id = {case["proposal"].proposal_id: case for case in cases}
    ordered = [by_id["bp-006"], by_id["bp-018"]] + [
        case for case in cases if case["proposal"].proposal_id not in {"bp-006", "bp-018"}
    ]
    records = []
    for index, case in enumerate(ordered):
        request = request_for(case, registry)
        body, elapsed, transport_error = post(request)
        status, semantic, raw, parse_error = ("transport_invalid", None, None, None)
        if transport_error is None:
            status, semantic, raw, parse_error = extract(body, registry)
        exact = status == "valid" and canonical_semantic_projection(semantic, registry) == canonical_semantic_projection(case["expected"], registry)
        record = {"case_id": case["proposal"].proposal_id, "status": status,
                  "error": transport_error or parse_error, "semantic": semantic,
                  "expected": case["expected"], "exact": exact, "elapsed_ms": elapsed,
                  "request_sha256": sha256(json.dumps(request, ensure_ascii=False, allow_nan=False, sort_keys=True,
                                                       separators=(",", ":")).encode()).hexdigest()}
        records.append(record)
        print(json.dumps({"completed": len(records), "total": 20, "case": record["case_id"],
                          "status": status, "exact": exact}, sort_keys=True), flush=True)
        if transport_error is not None or (index == 1 and any(item["status"] != "valid" for item in records)):
            atomic_exclusive(OUTPUT, {"format": "metnos.intent-approval-fix-pilot/0.1",
                                      "state": "stopped_after_probe", "records": records})
            return 2
    records.sort(key=lambda item: item["case_id"])
    summary = {"exact": sum(item["exact"] for item in records),
               "valid": sum(item["status"] == "valid" for item in records),
               "document_invalid": sum(item["status"] == "document_invalid" for item in records),
               "technical_invalid": sum(item["status"] == "technical_invalid" for item in records),
               "median_elapsed_ms": sorted(item["elapsed_ms"] for item in records)[len(records) // 2]}
    atomic_exclusive(OUTPUT, {"format": "metnos.intent-approval-fix-pilot/0.1", "state": "complete",
                              "request_count": 20, "retry_count": 0,
                              "prompt_delta_sha256": sha256(RULE.encode()).hexdigest(),
                              "summary": summary, "records": records})
    print(json.dumps({"complete": True, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
