#!/usr/bin/env python3
"""Run the same 20 pilot cases through the current production extractor."""
from __future__ import annotations

from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run2 import arm_a
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run2.evaluator import canonical_semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run2.runner import _content_from_http
from .run_pilot import CASES, HERE, atomic_exclusive, canonical, post, validate_cases


OUTPUT = HERE / "production_results.json"


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    for case in cases:
        request = arm_a.build_request(case["query"], case["proposal"].language_tag)
        body, elapsed, error = post(request)
        extraction = None
        if error is None:
            content, wrapper_error = _content_from_http(body)
            error = wrapper_error
            if content is not None:
                extraction = arm_a.extract_response(
                    content, query=case["query"], language=case["proposal"].language_tag)
        semantic = extraction.get("semantic_document") if type(extraction) is dict else None
        exact = (
            type(semantic) is dict
            and canonical_semantic_projection(semantic, registry)
            == canonical_semantic_projection(case["expected"], registry)
        )
        record = {
            "case_id": case["proposal"].proposal_id,
            "request_sha256": sha256(canonical(request)).hexdigest(),
            "status": extraction.get("status") if type(extraction) is dict else "transport_invalid",
            "error": error,
            "semantic": semantic,
            "expected": case["expected"],
            "exact": exact,
            "elapsed_ms": elapsed,
        }
        records.append(record)
        print(json.dumps({"completed": len(records), "total": 20, "case": record["case_id"],
                          "status": record["status"], "exact": exact}, sort_keys=True), flush=True)
        if error is not None:
            atomic_exclusive(OUTPUT, {"format": "metnos.intent-production-pilot-result/0.1",
                                      "state": "partial_transport_stop", "records": records})
            return 2
    summary = {
        "exact": sum(item["exact"] for item in records),
        "valid": sum(item["status"] in {"valid_representable", "valid_unrepresentable"} for item in records),
        "technical_invalid": sum(item["status"] == "technical_invalid" for item in records),
        "median_elapsed_ms": sorted(item["elapsed_ms"] for item in records)[len(records) // 2],
    }
    atomic_exclusive(OUTPUT, {"format": "metnos.intent-production-pilot-result/0.1",
                              "state": "complete", "request_count": 20, "retry_count": 0,
                              "cases_sha256": sha256(CASES.read_bytes()).hexdigest(),
                              "summary": summary, "records": records})
    print(json.dumps({"complete": True, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
