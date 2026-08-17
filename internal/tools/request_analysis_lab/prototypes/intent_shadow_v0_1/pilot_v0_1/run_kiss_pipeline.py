#!/usr/bin/env python3
"""KISS candidate: one small structural gate followed by S0."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.projection import build_schema
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_s0
from .run_pilot import HERE, atomic_exclusive, canonical, extract, post, profiled, validate_cases


OUTPUT = HERE / "kiss_pipeline_v2_results.json"
ROOT_BLOCK = "\n".join((
    "CHOOSE ROOT FIRST: select exactly one root that matches the request, then emit it as one JSON object with kind first.",
    "EQUIVALENT ROOT TEMPLATES:",
    '- {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
    '- {"kind":"system_control","control":"<registry-control>"}',
    '- {"kind":"unrepresentable","reason":"<registry-reason>"}',
))


def gate_request(case, registry):
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['proposal'].language_tag}",
        "Return four independent structural facts about the complete request.",
        "alternative_action_paths=true only for IF/ELSE alternative actions; approval before actions is false.",
        "system_control names an explicit system control; otherwise none.",
        "ordinary_action=true when any non-control action is requested.",
        "approval_scope=all_actions when confirmation occurs before the first ordinary action; suffix_actions only when at least one ordinary action completes before confirmation; otherwise none.",
        "SYSTEM CONTROLS: " + ", ".join(sorted(registry["system_controls"])),
    )) + "\n"
    schema = {"type": "object", "additionalProperties": False,
              "properties": {
                  "alternative_action_paths": {"type": "boolean"},
                  "system_control": {"type": "string", "enum": ["none", *sorted(registry["system_controls"])]},
                  "ordinary_action": {"type": "boolean"},
                  "approval_scope": {"type": "string", "enum": ["none", "all_actions", "suffix_actions"]},
              },
              "required": ["alternative_action_paths", "system_control", "ordinary_action", "approval_scope"]}
    request = profiled({"messages": [{"role": "system", "content": prompt},
                                     {"role": "user", "content": case["query"]}],
                        "response_format": {"type": "json_schema", "json_schema": {
                            "name": "metnos_structural_gate", "strict": True, "schema": schema}},
                        "temperature": 0})
    request["max_tokens"] = 100
    return request


def parse_gate(body):
    wrapper = json.loads(body)
    content = wrapper["choices"][0]["message"]["content"]
    value = json.loads(content)
    required = {"alternative_action_paths", "system_control", "ordinary_action", "approval_scope"}
    if type(value) is not dict or set(value) != required:
        raise ValueError("gate document")
    return value


def approval_schema(registry, scope):
    source = build_schema(registry)
    operation = deepcopy(source["$defs"]["step"]["oneOf"][0])
    operation["properties"]["needs_approval"] = {"type": "boolean"}
    operation["required"] = [*operation["required"], "needs_approval"]
    return {"$schema": source["$schema"], "type": "object", "additionalProperties": False,
            "properties": {"steps": {"type": "array", "minItems": 1, "items": operation}},
            "required": ["steps"]}


def approval_request(case, registry, scope):
    request = deepcopy(build_s0(case["query"], registry, case["proposal"].language_tag))
    prompt = request["messages"][0]["content"]
    if prompt.count(ROOT_BLOCK) != 1:
        raise RuntimeError("S0 root block")
    fixed = ("STRUCTURE: return each requested action exactly once in steps. "
             "needs_approval=true only when that action occurs after user confirmation. "
             "Confirmation is not an operation. FROM uses step order.")
    request["messages"][0]["content"] = prompt.replace(ROOT_BLOCK, fixed, 1)
    request["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "metnos_approval_graph", "strict": True,
        "schema": approval_schema(registry, scope)}}
    return profiled(request)


def extract_approval(body, registry):
    try:
        wrapper = json.loads(body)
        content = wrapper["choices"][0]["message"]["content"]
        raw = content.encode("utf-8", errors="strict")
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {"steps"}:
            raise ValueError("approval document")
        flags = [step["needs_approval"] for step in value["steps"]]
        if True not in flags:
            raise ValueError("approval absent")
        split = flags.index(True)
        if any(not flag for flag in flags[split:]):
            raise ValueError("approval not suffix")
        clean = [{key: item for key, item in step.items() if key != "needs_approval"}
                 for step in value["steps"]]
        document = {"kind": "operation_graph", "steps": [*clean[:split],
                    {"barrier": "get/approval", "body": clean[split:]}]}
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
    records = []
    calls = 0
    for case in cases:
        gate_req = gate_request(case, registry)
        gate_body, gate_ms, gate_transport = post(gate_req)
        calls += 1
        gate = None
        error = gate_transport
        if error is None:
            try:
                gate = parse_gate(gate_body)
            except Exception as exc:
                error = type(exc).__name__
        status = "transport_invalid" if gate_transport else "technical_invalid" if error else "valid"
        semantic = None
        raw = None
        main_ms = 0
        main_request_sha = None
        if status == "valid" and gate["alternative_action_paths"]:
            semantic = {"kind": "unrepresentable", "reason": "unsupported_dependency"}
        elif status == "valid" and gate["system_control"] != "none" and gate["ordinary_action"]:
            semantic = {"kind": "unrepresentable", "reason": "mixed_root_kinds"}
        elif status == "valid":
            scope = gate["approval_scope"]
            request = (approval_request(case, registry, scope) if scope != "none"
                       else profiled(build_s0(case["query"], registry, case["proposal"].language_tag)))
            main_request_sha = sha256(canonical(request)).hexdigest()
            body, main_ms, transport = post(request)
            calls += 1
            if transport is not None:
                status, error = "transport_invalid", transport
            elif scope != "none":
                status, semantic, raw, error = extract_approval(body, registry)
            else:
                status, semantic, raw, error = extract(body, registry)
            if (gate["system_control"] != "none" and not gate["ordinary_action"]
                    and not (status == "valid" and semantic and semantic.get("kind") == "operation_graph")):
                status, error = "valid", None
                semantic = {"kind": "system_control", "control": gate["system_control"]}
        exact = status == "valid" and semantic == case["expected"]
        record = {"case_id": case["proposal"].proposal_id, "gate": gate, "status": status,
                  "error": error, "exact": exact, "semantic": semantic, "expected": case["expected"],
                  "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
                  "gate_elapsed_ms": gate_ms, "main_elapsed_ms": main_ms,
                  "gate_request_sha256": sha256(canonical(gate_req)).hexdigest(),
                  "main_request_sha256": main_request_sha}
        records.append(record)
        print(json.dumps({"completed": len(records), "calls": calls, "case": record["case_id"],
                          "status": status, "exact": exact}, sort_keys=True), flush=True)
        if status == "transport_invalid":
            atomic_exclusive(OUTPUT, {"state": "transport_stop", "calls": calls, "records": records})
            return 2
    summary = {"exact": sum(item["exact"] for item in records),
               "valid": sum(item["status"] == "valid" for item in records),
               "document_invalid": sum(item["status"] == "document_invalid" for item in records),
               "technical_invalid": sum(item["status"] == "technical_invalid" for item in records)}
    atomic_exclusive(OUTPUT, {"format": "metnos.intent-kiss-pipeline/0.1", "state": "complete",
                              "case_count": len(records), "call_count": calls, "retry_count": 0,
                              "summary": summary, "records": records})
    print(json.dumps({"complete": True, "calls": calls, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
