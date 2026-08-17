#!/usr/bin/env python3
"""KISS linear-plan probe: routes plus two booleans, no model-facing ordinals."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.projection import build_schema
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_s0
from .run_pilot import HERE, atomic_exclusive, post, profiled, validate_cases


OUTPUT = HERE / "linear_probe_results.json"
CORRECTIONS = HERE / "query_corrections_v1.json"
CASE_IDS = ("bp-001", "bp-003", "bp-005", "bp-006", "bp-013", "bp-015", "bp-018")
ROOT_BLOCK = "\n".join((
    "CHOOSE ROOT FIRST: select exactly one root that matches the request, then emit it as one JSON object with kind first.",
    "EQUIVALENT ROOT TEMPLATES:",
    '- {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
    '- {"kind":"system_control","control":"<registry-control>"}',
    '- {"kind":"unrepresentable","reason":"<registry-reason>"}',
))


def request_for(case, registry):
    request = deepcopy(build_s0(case["query"], registry, case["proposal"].language_tag))
    prompt = request["messages"][0]["content"]
    if prompt.count(ROOT_BLOCK) != 1:
        raise RuntimeError("S0 root block")
    rule = ("ROOT IS A LINEAR OPERATION GRAPH. Emit every requested action exactly once. "
            "uses_previous=true only when this action consumes the immediately previous action result. "
            "needs_approval=true only when this action waits for user confirmation. "
            "Sequencing words and confirmation are not actions.")
    request["messages"][0]["content"] = prompt.replace(ROOT_BLOCK, rule, 1)
    source = build_schema(registry)
    operation = deepcopy(source["$defs"]["step"]["oneOf"][0])
    operation["properties"].pop("from", None)
    operation["properties"]["uses_previous"] = {"type": "boolean"}
    operation["properties"]["needs_approval"] = {"type": "boolean"}
    operation["required"] = ["route", "uses_previous", "needs_approval"]
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"steps": {"type": "array", "minItems": 1, "items": operation}},
              "required": ["steps"]}
    request["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "metnos_linear_plan_probe", "strict": True, "schema": schema}}
    return profiled(request)


def compile_plan(value, registry):
    if type(value) is not dict or set(value) != {"steps"} or type(value["steps"]) is not list:
        raise ValueError("linear document")
    operations = []
    for index, item in enumerate(value["steps"]):
        if type(item) is not dict or set(item) != {"route", "uses_previous", "needs_approval"}:
            raise ValueError("linear step")
        if index == 0 and item["uses_previous"]:
            raise ValueError("first dependency")
        operation = {"route": item["route"]}
        if item["uses_previous"]:
            operation["from"] = [index - 1]
        operations.append((operation, item["needs_approval"]))
    document_steps = []
    index = 0
    while index < len(operations):
        operation, approved = operations[index]
        if not approved:
            document_steps.append(operation)
            index += 1
            continue
        body = []
        while index < len(operations) and operations[index][1]:
            body.append(operations[index][0])
            index += 1
        document_steps.append({"barrier": "get/approval", "body": body})
    compilation = compile_ir({"kind": "operation_graph", "steps": document_steps}, registry)
    if not compilation.valid:
        raise ValueError(";".join(issue.code for issue in compilation.issues))
    return semantic_projection(compilation)


def main() -> int:
    cases, registry = validate_cases()
    corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    by_id = {case["proposal"].proposal_id: case for case in cases}
    records = []
    for case_id in CASE_IDS:
        case = deepcopy(by_id[case_id])
        if case_id in corrections:
            case["query"] = corrections[case_id]
        body, elapsed, transport = post(request_for(case, registry))
        semantic = None
        error = transport
        raw = None
        if error is None:
            try:
                wrapper = json.loads(body)
                raw = wrapper["choices"][0]["message"]["content"]
                semantic = compile_plan(json.loads(raw), registry)
            except Exception as exc:
                error = f"{type(exc).__name__}:{exc}"
        record = {"case_id": case_id, "exact": semantic == case["expected"],
                  "valid": semantic is not None, "error": error, "raw": raw,
                  "semantic": semantic, "expected": case["expected"], "elapsed_ms": elapsed}
        records.append(record)
        print(json.dumps({"case": case_id, "exact": record["exact"],
                          "valid": record["valid"]}, sort_keys=True), flush=True)
    result = {"format": "metnos.linear-plan-probe/0.1", "count": len(records),
              "exact": sum(item["exact"] for item in records),
              "valid": sum(item["valid"] for item in records), "records": records}
    atomic_exclusive(OUTPUT, result)
    print(json.dumps({key: result[key] for key in ("count", "exact", "valid")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
