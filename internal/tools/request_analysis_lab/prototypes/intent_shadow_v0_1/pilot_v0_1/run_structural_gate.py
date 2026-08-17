#!/usr/bin/env python3
"""One-field structural gate; all ordinary intent work stays in S0."""
from __future__ import annotations

from hashlib import sha256
import json

from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "structural_mode_results.json"
MODES = ("defer", "conditional_branch", "undo_only", "undo_plus_action",
         "approval_all", "approval_suffix")


def expected_mode(case):
    cell = case["proposal"].cell
    if cell == "S3_CONDITIONAL_BRANCH":
        return "conditional_branch"
    if cell == "S4_UNDO":
        return "undo_only"
    if cell == "S5_MIXED_CONTROL":
        return "undo_plus_action"
    if cell == "S1_APPROVAL":
        return "approval_all" if case["proposal"].subtype == "all_approved" else "approval_suffix"
    return "defer"


def build_request(case, registry):
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['proposal'].language_tag}",
        "Return exactly one structural mode:",
        "conditional_branch = mutually exclusive requested actions depend on IF/ELSE.",
        "undo_only = undo/revert the previous turn and nothing else.",
        "undo_plus_action = undo/revert the previous turn plus an ordinary action.",
        "approval_all = every requested action waits for confirmation.",
        "approval_suffix = action(s) occur before confirmation, then gated action(s).",
        "defer = none of the above.",
    )) + "\n"
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"mode": {"type": "string", "enum": list(MODES)}},
              "required": ["mode"]}
    request = profiled({
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": case["query"]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "metnos_structural_gate_probe", "strict": True, "schema": schema}},
        "temperature": 0,
    })
    request["max_tokens"] = 100
    return request


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    for case in cases:
        request = build_request(case, registry)
        body, elapsed, transport_error = post(request)
        features = None
        error = transport_error
        decision = None
        if error is None:
            try:
                wrapper = json.loads(body)
                features = json.loads(wrapper["choices"][0]["message"]["content"])
                decision = features["mode"]
            except Exception as exc:
                error = type(exc).__name__
        expected = expected_mode(case)
        record = {"case_id": case["proposal"].proposal_id, "features": features,
                  "decision": decision, "expected": expected, "exact": decision == expected,
                  "error": error, "elapsed_ms": elapsed,
                  "request_sha256": sha256(canonical(request)).hexdigest()}
        records.append(record)
        print(json.dumps({"completed": len(records), "case": record["case_id"],
                          "decision": decision, "exact": record["exact"]}, sort_keys=True), flush=True)
        if error is not None:
            break
    result = {"format": "metnos.structural-gate-probe/0.1", "count": len(records),
              "exact": sum(item["exact"] for item in records), "records": records}
    atomic_exclusive(OUTPUT, result)
    print(json.dumps({"count": result["count"], "exact": result["exact"]}, sort_keys=True), flush=True)
    return 0 if len(records) == len(cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
