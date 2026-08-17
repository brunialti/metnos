#!/usr/bin/env python3
"""KISS reference with only two surgical boundary corrections."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.projection import build_schema
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import (
    build_request as build_s0,
)
from .run_kiss_compiled import CASES, authority_lines, prepare_cases
from .run_kiss_pipeline import ROOT_BLOCK
from .run_kiss_semantic_probe import DECISION_CHECK
from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "kiss_surgical_probe_v2_results.json"


def gate_request(case, registry):
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['language_tag']}",
        "Return four independent structural facts about the complete request.",
        "alternative_action_paths=true only for IF/ELSE alternative actions; approval before actions is false.",
        "system_control names a control only when the request explicitly targets the previous conversational turn as defined below; otherwise none.",
        "Deleting, closing, changing, or cancelling a domain object is an ordinary action, not a system control.",
        "ordinary_action=true when any non-control action is requested.",
        "approval_scope=all_actions when confirmation occurs before the first ordinary action; suffix_actions only when at least one ordinary action completes before confirmation; otherwise none.",
        "SYSTEM CONTROL AUTHORITY:",
        *authority_lines(registry["system_controls"]),
    )) + "\n"
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "alternative_action_paths": {"type": "boolean"},
            "system_control": {"type": "string", "enum": ["none", *sorted(registry["system_controls"])]},
            "ordinary_action": {"type": "boolean"},
            "approval_scope": {"type": "string", "enum": ["none", "all_actions", "suffix_actions"]},
        },
        "required": ["alternative_action_paths", "system_control", "ordinary_action", "approval_scope"],
    }
    request = profiled({
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": case["query"]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "metnos_kiss_surgical_gate", "strict": True, "schema": schema,
        }},
        "temperature": 0,
    })
    request["max_tokens"] = 100
    return request


def parse_gate(body):
    value = json.loads(json.loads(body)["choices"][0]["message"]["content"])
    required = {"alternative_action_paths", "system_control", "ordinary_action", "approval_scope"}
    if type(value) is not dict or set(value) != required:
        raise ValueError("gate document")
    return value


def operation_shapes(registry, scope):
    route = {"type": "string", "enum": sorted(registry["operations"])}
    first_properties = {"route": route, "uses_previous": {"type": "boolean"}}
    next_properties = {"route": route, "uses_previous": {"type": "boolean"}}
    first_required = ["route", "uses_previous"]
    next_required = ["route", "uses_previous"]
    if scope is not None:
        approval = ({"const": False} if scope == "none" else
                    {"const": True} if scope == "all_actions" else
                    {"type": "boolean"})
        first_properties["needs_approval"] = approval
        next_properties["needs_approval"] = approval
        first_required.append("needs_approval")
        next_required.append("needs_approval")
    first = {"type": "object", "additionalProperties": False,
             "properties": first_properties, "required": first_required}
    following = {"type": "object", "additionalProperties": False,
                 "properties": next_properties, "required": next_required}
    return first, following


def step_array_schema(registry, scope):
    _, following = operation_shapes(registry, scope)
    return {
        "type": "array", "minItems": 1,
        "items": following,
    }


def response_schema(registry, scope):
    if scope is not None:
        return {
            "type": "object", "additionalProperties": False,
            "properties": {"steps": step_array_schema(registry, scope)},
            "required": ["steps"],
        }
    schema = deepcopy(build_schema(registry))
    schema["oneOf"][0]["properties"]["steps"] = step_array_schema(registry, None)
    schema.pop("$defs", None)
    return schema


def surgical_prompt(prompt):
    replacements = {
        "OPERATION ORDINAL 0 MUST OMIT FROM: it has no previous operation.":
            "DEPENDENCY FORMAT: every operation declares uses_previous; the first must set it false.",
        "FROM: omit from unless an operation has a real data dependency; each index must identify a strictly previous operation visible on the same path.":
            "Every later operation must include uses_previous: true only when it consumes the immediately previous result; otherwise false.",
        "A STEP MUST NEVER REFERENCE ITS OWN ORDINAL OR A LATER ORDINAL.":
            "A non-adjacent or branching data dependency is unrepresentable/unsupported_dependency.",
        'VALID COMPLETE GRAPH: {"kind":"operation_graph","steps":[{"route":"<source-route>"},{"route":"<consumer-route>","from":[0]}]}':
            'VALID COMPLETE GRAPH: {"kind":"operation_graph","steps":[{"route":"<source-route>","uses_previous":false},{"route":"<consumer-route>","uses_previous":true}]}',
        "- An operation graph contains only necessary operations; step 0 omits from.":
            "- An operation graph contains only necessary operations; the first operation sets uses_previous=false.",
        "- Every from denotes a real dependency on a strictly previous visible operation.":
            "- Every later operation declares whether it uses the immediately previous result.",
        "- Independent actions have no from; use from only when a later action consumes an earlier result.":
            "- Independent later actions set uses_previous=false; a direct consumer of the previous result sets it true.",
        "FROM uses step order.":
            "uses_previous refers only to the immediately previous step.",
    }
    result = prompt
    for old, new in replacements.items():
        if old in result:
            result = result.replace(old, new)
    if '"from"' in result:
        raise RuntimeError("model-facing numeric dependency remains")
    return result


def main_request(case, registry, scope):
    request = deepcopy(build_s0(case["query"], registry, case["language_tag"]))
    prompt = request["messages"][0]["content"]
    if prompt.count(ROOT_BLOCK) != 1:
        raise RuntimeError("S0 root block")
    if scope == "none":
        prompt = prompt.replace(ROOT_BLOCK, ROOT_BLOCK + "\n" + DECISION_CHECK, 1)
    else:
        graph_only = (
            "STRUCTURE: return each requested action exactly once in steps. "
            "needs_approval=true only when that action occurs after user confirmation. "
            "Confirmation is not an operation. FROM uses step order."
        )
        prompt = prompt.replace(ROOT_BLOCK, graph_only + "\n" + DECISION_CHECK, 1)
    request["messages"][0]["content"] = surgical_prompt(prompt)
    request["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "metnos_kiss_surgical", "strict": True,
        "schema": response_schema(registry, None if scope == "none" else scope),
    }}
    return profiled(request)


def convert_steps(model_steps, with_approval):
    steps = []
    flags = []
    for index, model_step in enumerate(model_steps):
        required = ({"route", "uses_previous", "needs_approval"} if with_approval else
                    {"route", "uses_previous"})
        if type(model_step) is not dict or set(model_step) != required:
            raise ValueError("step document")
        if index == 0 and model_step["uses_previous"]:
            raise ValueError("first step dependency")
        step = {"route": model_step["route"]}
        if index and model_step["uses_previous"]:
            step["from"] = [index - 1]
        steps.append(step)
        if with_approval:
            flags.append(model_step["needs_approval"])
    return steps, flags


def extract_result(body, registry, scope):
    raw = None
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
        raw = content.encode("utf-8", errors="strict")
        value = json.loads(raw)
        if scope != "none":
            if type(value) is not dict or set(value) != {"steps"}:
                raise ValueError("approval root")
            steps, flags = convert_steps(value["steps"], True)
            if scope == "all_actions":
                if not all(flags):
                    raise ValueError("approval all")
                graph_steps = [{"barrier": "get/approval", "body": steps}]
            else:
                if True not in flags or flags[0] or any(not flag for flag in flags[flags.index(True):]):
                    raise ValueError("approval suffix")
                split = flags.index(True)
                graph_steps = [*steps[:split], {"barrier": "get/approval", "body": steps[split:]}]
            document = {"kind": "operation_graph", "steps": graph_steps}
        elif value.get("kind") == "operation_graph":
            if set(value) != {"kind", "steps"}:
                raise ValueError("graph root")
            steps, _ = convert_steps(value["steps"], False)
            document = {"kind": "operation_graph", "steps": steps}
        else:
            document = value
    except Exception as exc:
        return "technical_invalid", None, raw, type(exc).__name__
    compilation = compile_ir(document, registry)
    if not compilation.valid:
        return "document_invalid", None, raw, ";".join(issue.code for issue in compilation.issues)
    return "valid", semantic_projection(compilation), raw, None


def run_cases(cases, registry, output):
    if output.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    calls = 0
    for case in cases:
        gate_req = gate_request(case, registry)
        gate_body, gate_ms, gate_transport = post(gate_req)
        calls += 1
        try:
            gate = None if gate_transport else parse_gate(gate_body)
            status, error = ("transport_invalid", gate_transport) if gate_transport else ("valid", None)
        except Exception as exc:
            gate, status, error = None, "technical_invalid", type(exc).__name__
        semantic = raw = None
        main_ms = 0
        if status == "valid" and gate["alternative_action_paths"]:
            semantic = {"kind": "unrepresentable", "reason": "unsupported_dependency"}
        elif status == "valid" and gate["system_control"] != "none" and gate["ordinary_action"]:
            semantic = {"kind": "unrepresentable", "reason": "mixed_root_kinds"}
        elif status == "valid" and gate["system_control"] != "none":
            semantic = {"kind": "system_control", "control": gate["system_control"]}
        elif status == "valid" and not gate["ordinary_action"]:
            semantic = {"kind": "unrepresentable", "reason": "no_actionable_intent"}
        elif status == "valid":
            request = main_request(case, registry, gate["approval_scope"])
            body, main_ms, transport = post(request)
            calls += 1
            if transport:
                status, error = "transport_invalid", transport
            else:
                status, semantic, raw, error = extract_result(body, registry, gate["approval_scope"])
        exact = status == "valid" and semantic == case["expected"]
        records.append({
            "case_id": case["case_id"], "source": case["source"], "language_tag": case["language_tag"],
            "gate": gate, "status": status, "error": error, "exact": exact,
            "semantic": semantic, "expected": case["expected"],
            "gate_elapsed_ms": gate_ms, "main_elapsed_ms": main_ms,
            "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
            "gate_request_sha256": sha256(canonical(gate_req)).hexdigest(),
        })
        print(json.dumps({"completed": len(records), "case": case["case_id"],
                          "exact": exact, "valid": status == "valid"}), flush=True)
        if status == "transport_invalid":
            break
    summary = {
        "exact": sum(row["exact"] for row in records),
        "valid": sum(row["status"] == "valid" for row in records),
        "new_multilingual_exact": sum(row["exact"] for row in records if row["source"] == "new_multilingual"),
        "new_multilingual_valid": sum(row["status"] == "valid" for row in records if row["source"] == "new_multilingual"),
    }
    atomic_exclusive(output, {
        "format": "metnos.intent-kiss-surgical-probe/0.1", "call_count": calls,
        "cases_sha256": sha256(CASES.read_bytes()).hexdigest(), "summary": summary, "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def main() -> int:
    _, registry = validate_cases()
    run_cases(prepare_cases(registry), registry, OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
