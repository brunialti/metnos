#!/usr/bin/env python3
"""Six-case probe for a flat, schema-neutral root decision."""
from __future__ import annotations

from hashlib import sha256
import json

from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "root_classifier_v3_results.json"
CASE_IDS = ("bp-002", "bp-004", "bp-007", "bp-010", "bp-014", "bp-020")


def expected_decision(expected):
    if expected["kind"] == "operation_graph":
        return "operation_graph"
    if expected["kind"] == "system_control":
        return "system_control:" + expected["control"]
    return "unrepresentable:" + expected["reason"]


def build_request(case, registry):
    priority_reasons = ("unsupported_dependency", "mixed_root_kinds", "outside_registry")
    if any(name not in registry["unrepresentable_reasons"] for name in priority_reasons):
        raise RuntimeError("root authority")
    decisions = ["unrepresentable:unsupported_dependency"]
    decisions.extend("system_control:" + name for name in sorted(registry["system_controls"]))
    decisions.extend(("unrepresentable:mixed_root_kinds", "unrepresentable:outside_registry",
                      "operation_graph"))
    decisions.extend("unrepresentable:" + name for name in sorted(registry["unrepresentable_reasons"])
                     if name not in priority_reasons)
    controls = []
    for name, metadata in sorted(registry["system_controls"].items()):
        scopes = []
        for item in metadata.get("descriptions", []):
            for tag, value in sorted(item.get("languages", {}).items()):
                scope = value.get("scope")
                if scope:
                    scopes.append(f"[{tag}] {scope}")
        controls.append(f"- {name}: " + " | ".join(scopes))
    reasons = [f"- {name}: {description}" for name, description in
               sorted(registry["unrepresentable_reasons"].items())]
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['proposal'].language_tag}",
        "Classify the complete request. Return exactly one decision.",
        "IF/ELSE/OTHERWISE is one conditional branch -> unrepresentable:unsupported_dependency, never mixed_root_kinds.",
        "System control plus ordinary operation -> unrepresentable:mixed_root_kinds.",
        "System control only -> its system_control decision.",
        "Do not approximate a requested capability with a merely similar operation.",
        "Absent indispensable capability -> unrepresentable:outside_registry; missing_required_information is allowed only when every capability exists.",
        "Otherwise use operation_graph or the matching reason.",
        "SYSTEM CONTROLS:", *controls,
        "UNREPRESENTABLE REASONS:", *reasons,
        "OPERATIONS:", ", ".join(sorted(registry["operations"])),
    )) + "\n"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"decision": {"type": "string", "enum": decisions}},
        "required": ["decision"],
    }
    return profiled({
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": case["query"]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "metnos_root_decision_probe", "strict": True, "schema": schema}},
        "temperature": 0,
    })


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    by_id = {case["proposal"].proposal_id: case for case in cases}
    records = []
    for case_id in CASE_IDS:
        case = by_id[case_id]
        request = build_request(case, registry)
        body, elapsed, transport_error = post(request)
        decision = None
        error = transport_error
        if error is None:
            try:
                wrapper = json.loads(body)
                content = wrapper["choices"][0]["message"]["content"]
                value = json.loads(content)
                decision = value["decision"]
            except Exception as exc:
                error = type(exc).__name__
        expected = expected_decision(case["expected"])
        record = {"case_id": case_id, "decision": decision, "expected": expected,
                  "exact": decision == expected, "error": error, "elapsed_ms": elapsed,
                  "request_sha256": sha256(canonical(request)).hexdigest()}
        records.append(record)
        print(json.dumps(record, sort_keys=True), flush=True)
        if error is not None:
            break
    atomic_exclusive(OUTPUT, {"format": "metnos.root-decision-probe/0.1",
                              "exact": sum(item["exact"] for item in records),
                              "count": len(records), "records": records})
    return 0 if len(records) == len(CASE_IDS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
