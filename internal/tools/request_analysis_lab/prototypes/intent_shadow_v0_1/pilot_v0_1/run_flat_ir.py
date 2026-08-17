#!/usr/bin/env python3
"""One-call S0 variant with a flat decision-first response schema."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.projection import build_schema
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_s0
from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "flat_ir_results.json"
PROBE_IDS = ("bp-002", "bp-004", "bp-006", "bp-007", "bp-010", "bp-014", "bp-020")
ROOT_BLOCK = "\n".join((
    "CHOOSE ROOT FIRST: select exactly one root that matches the request, then emit it as one JSON object with kind first.",
    "EQUIVALENT ROOT TEMPLATES:",
    '- {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
    '- {"kind":"system_control","control":"<registry-control>"}',
    '- {"kind":"unrepresentable","reason":"<registry-reason>"}',
))
FLAT_BLOCK = "\n".join((
    "DECISION FIRST: classify the complete request, then fill steps.",
    "OUTPUT TEMPLATES:",
    '- {"decision":"operation_graph","steps":[{"route":"<registry-route>"}]}',
    '- {"decision":"system_control:<registry-control>","steps":null}',
    '- {"decision":"unrepresentable:<registry-reason>","steps":null}',
    "IF/ELSE/OTHERWISE -> unrepresentable:unsupported_dependency, never mixed_root_kinds.",
    "UNDO only -> system_control:undo_last_turn; UNDO + operation -> unrepresentable:mixed_root_kinds.",
    "Absent indispensable capability -> unrepresentable:outside_registry; do not approximate it.",
))
BARRIER_ANCHOR = "BARRIER: body contains every operation that requires approval."
BARRIER_RULE = "APPROVAL: confirmation before actions -> get/approval barrier containing all gated steps; never get/inputs."


def decision_values(registry):
    priority = ("unsupported_dependency", "mixed_root_kinds", "outside_registry")
    values = ["unrepresentable:unsupported_dependency"]
    values.extend("system_control:" + name for name in sorted(registry["system_controls"]))
    values.extend(("unrepresentable:mixed_root_kinds", "unrepresentable:outside_registry",
                   "operation_graph"))
    values.extend("unrepresentable:" + name for name in sorted(registry["unrepresentable_reasons"])
                  if name not in priority)
    return values


def flat_schema(registry):
    source = build_schema(registry)
    return {
        "$schema": source["$schema"],
        "$id": "urn:metnos:intent-ir:decision-first-probe:0.1",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": decision_values(registry)},
            "steps": {"oneOf": [{"$ref": "#/$defs/nonempty_steps"}, {"type": "null"}]},
        },
        "required": ["decision", "steps"],
        "$defs": deepcopy(source["$defs"]),
    }


def request_for(case, registry):
    request = deepcopy(build_s0(case["query"], registry, case["proposal"].language_tag))
    prompt = request["messages"][0]["content"]
    if prompt.count(ROOT_BLOCK) != 1 or prompt.count(BARRIER_ANCHOR) != 1:
        raise RuntimeError("prompt anchor")
    prompt = prompt.replace(ROOT_BLOCK, FLAT_BLOCK, 1)
    prompt = prompt.replace(BARRIER_ANCHOR, BARRIER_RULE, 1)
    request["messages"][0]["content"] = prompt
    request["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "metnos_intent_decision_first_probe", "strict": True,
        "schema": flat_schema(registry),
    }}
    return profiled(request)


def extract_flat(body, registry):
    try:
        wrapper = json.loads(body)
        content = wrapper["choices"][0]["message"]["content"]
        raw = content.encode("utf-8", errors="strict")
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {"decision", "steps"}:
            raise ValueError("flat document")
        decision = value["decision"]
        steps = value["steps"]
        if decision == "operation_graph":
            if type(steps) is not list:
                raise ValueError("graph steps")
            document = {"kind": "operation_graph", "steps": steps}
        elif decision.startswith("system_control:"):
            if steps is not None:
                raise ValueError("control steps")
            document = {"kind": "system_control", "control": decision.split(":", 1)[1]}
        elif decision.startswith("unrepresentable:"):
            if steps is not None:
                raise ValueError("reason steps")
            document = {"kind": "unrepresentable", "reason": decision.split(":", 1)[1]}
        else:
            raise ValueError("decision")
    except Exception as exc:
        return "technical_invalid", None, None, type(exc).__name__
    compilation = compile_ir(document, registry)
    if not compilation.valid:
        return "document_invalid", None, raw, ";".join(item.code for item in compilation.issues)
    return "valid", semantic_projection(compilation), raw, None


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
            status, semantic, raw, parse_error = extract_flat(body, registry)
        exact = status == "valid" and semantic == case["expected"]
        record = {"case_id": case["proposal"].proposal_id, "status": status,
                  "error": transport_error or parse_error, "exact": exact,
                  "semantic": semantic, "expected": case["expected"],
                  "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
                  "elapsed_ms": elapsed, "request_sha256": sha256(canonical(request)).hexdigest()}
        records.append(record)
        print(json.dumps({"completed": len(records), "case": record["case_id"],
                          "status": status, "exact": exact}, sort_keys=True), flush=True)
        if transport_error is not None:
            atomic_exclusive(OUTPUT, {"state": "transport_stop", "records": records})
            return 2
        if len(records) == len(PROBE_IDS):
            probe_exact = sum(item["exact"] for item in records)
            probe_valid = sum(item["status"] == "valid" for item in records)
            if probe_exact < 5 or probe_valid < len(PROBE_IDS):
                atomic_exclusive(OUTPUT, {"state": "stopped_after_probe",
                                          "probe_exact": probe_exact, "probe_valid": probe_valid,
                                          "records": records})
                return 2
    records.sort(key=lambda item: item["case_id"])
    elapsed_values = sorted(item["elapsed_ms"] for item in records)
    summary = {"exact": sum(item["exact"] for item in records),
               "valid": sum(item["status"] == "valid" for item in records),
               "document_invalid": sum(item["status"] == "document_invalid" for item in records),
               "technical_invalid": sum(item["status"] == "technical_invalid" for item in records),
               "median_elapsed_ms": elapsed_values[len(elapsed_values) // 2]}
    atomic_exclusive(OUTPUT, {"format": "metnos.intent-flat-ir-pilot/0.1", "state": "complete",
                              "request_count": len(records), "retry_count": 0,
                              "summary": summary, "records": records})
    print(json.dumps({"complete": True, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
