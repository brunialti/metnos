#!/usr/bin/env python3
"""KISS candidate with registry-scoped controls and compiler-owned dependencies."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import (
    build_request as build_s0,
)
from .run_kiss_semantic_probe import CORRECTIONS, DECISION_CHECK
from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


CASES = HERE / "kiss_compiled_multilingual_cases.json"
OUTPUT = HERE / "kiss_compiled_probe_results.json"
OLD_CASE_IDS = ("bp-001", "bp-018")


def authority_lines(mapping):
    lines = []
    for name, metadata in sorted(mapping.items()):
        scopes = []
        seen = set()
        for description in metadata.get("descriptions", []):
            for tag, text in sorted(description.get("languages", {}).items()):
                scope = text.get("scope") if type(text) is dict else None
                if tag not in seen and type(scope) is str and scope:
                    scopes.append(f"[{tag}] {scope}")
                    seen.add(tag)
        lines.append(f"- {name}" + (" | " + " | ".join(scopes) if scopes else ""))
    return lines


def gate_request(case, registry):
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['language_tag']}",
        "Return four independent structural facts about the complete request.",
        "alternative_action_paths=true only for IF/ELSE alternative actions; approval is not a branch.",
        "system_control names a control only when the request explicitly targets the previous conversational turn as defined below.",
        "Deleting, closing, changing, or cancelling a domain object is an ordinary action, not a system control.",
        "ordinary_action=true when any non-control action is requested.",
        "approval_scope=all_actions when confirmation precedes every action; suffix_actions when actions occur both before and after confirmation; otherwise none.",
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
            "name": "metnos_kiss_gate_v2", "strict": True, "schema": schema,
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


def approval_property(scope):
    if scope == "none":
        return {"const": False}
    if scope == "all_actions":
        return {"const": True}
    return {"type": "boolean"}


def plan_schema(registry, scope):
    routes = sorted(registry["operations"])
    approval = approval_property(scope)
    first = {
        "type": "object", "additionalProperties": False,
        "properties": {"route": {"type": "string", "enum": routes}, "needs_approval": approval},
        "required": ["route", "needs_approval"],
    }
    following = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "route": {"type": "string", "enum": routes},
            "uses_previous": {"type": "boolean"},
            "needs_approval": approval,
        },
        "required": ["route", "uses_previous", "needs_approval"],
    }
    reasons = sorted(registry["unrepresentable_reasons"])
    return {
        "type": "object", "oneOf": [
            {
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "operation_plan"},
                    "first": first,
                    "then": {"type": "array", "items": following},
                },
                "required": ["kind", "first", "then"],
            },
            {
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "unrepresentable"},
                    "reason": {"type": "string", "enum": reasons},
                },
                "required": ["kind", "reason"],
            },
        ],
    }


def plan_request(case, registry, scope):
    base = deepcopy(build_s0(case["query"], registry, case["language_tag"]))
    source = base["messages"][0]["content"]
    registry_start = source.index("OPERATION REGISTRY")
    final_start = source.index("FINAL MINI-CHECK:")
    registry_text = source[registry_start:final_start]
    scope_rule = {
        "none": "needs_approval must always be false.",
        "all_actions": "needs_approval must always be true.",
        "suffix_actions": "needs_approval must be false before confirmation and true from confirmation onward.",
    }[scope]
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['language_tag']}",
        "Return exactly one JSON object.",
        '- Plan: {"kind":"operation_plan","first":{"route":"<route>","needs_approval":false},"then":[{"route":"<route>","uses_previous":true,"needs_approval":false}]}',
        '- Abstain: {"kind":"unrepresentable","reason":"<registry-reason>"}',
        "The first action cannot depend on itself and therefore has no dependency field.",
        "For later actions, uses_previous=true only when the action consumes the immediately previous result; otherwise false.",
        "A non-adjacent or branching dependency is unrepresentable/unsupported_dependency.",
        f"APPROVAL_SCOPE={scope}: {scope_rule}",
        DECISION_CHECK,
        registry_text.rstrip(),
        "FINAL CHECK: exact routes, no extra actions, and every boolean follows the rules above.",
    )) + "\n"
    base["messages"][0]["content"] = prompt
    base["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "metnos_kiss_compiled_plan", "strict": True,
        "schema": plan_schema(registry, scope),
    }}
    base["max_tokens"] = 1000
    return profiled(base)


def extract_plan(body, registry, scope):
    raw = None
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
        raw = content.encode("utf-8", errors="strict")
        value = json.loads(raw)
        if value.get("kind") == "unrepresentable":
            document = value
        elif value.get("kind") == "operation_plan" and set(value) == {"kind", "first", "then"}:
            model_steps = [value["first"], *value["then"]]
            flags = [step["needs_approval"] for step in model_steps]
            if scope == "none" and any(flags):
                raise ValueError("approval none")
            if scope == "all_actions" and not all(flags):
                raise ValueError("approval all")
            if scope == "suffix_actions":
                if True not in flags or flags[0] or any(not flag for flag in flags[flags.index(True):]):
                    raise ValueError("approval suffix")
            steps = []
            for index, model_step in enumerate(model_steps):
                step = {"route": model_step["route"]}
                if index and model_step["uses_previous"]:
                    step["from"] = [index - 1]
                steps.append(step)
            if scope == "none":
                graph_steps = steps
            elif scope == "all_actions":
                graph_steps = [{"barrier": "get/approval", "body": steps}]
            else:
                split = flags.index(True)
                graph_steps = [*steps[:split], {"barrier": "get/approval", "body": steps[split:]}]
            document = {"kind": "operation_graph", "steps": graph_steps}
        else:
            raise ValueError("plan document")
    except Exception as exc:
        return "technical_invalid", None, raw, type(exc).__name__
    compilation = compile_ir(document, registry)
    if not compilation.valid:
        return "document_invalid", None, raw, ";".join(issue.code for issue in compilation.issues)
    return "valid", semantic_projection(compilation), raw, None


def prepare_cases(registry):
    old, _ = validate_cases()
    corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]
    by_id = {item["proposal"].proposal_id: item for item in old}
    prepared = []
    for case_id in OLD_CASE_IDS:
        source = by_id[case_id]
        prepared.append({
            "case_id": case_id,
            "language_tag": source["proposal"].language_tag,
            "query": corrections.get(case_id, source["query"]),
            "expected": source["expected"],
            "source": "previous_failure",
        })
    value = json.loads(CASES.read_text(encoding="utf-8"))
    for row in value["cases"]:
        compilation = compile_ir(row["document"], registry)
        if not compilation.valid:
            raise RuntimeError(f"invalid multilingual gold: {row['case_id']}")
        prepared.append({
            "case_id": row["case_id"], "language_tag": row["language_tag"],
            "query": row["query"], "expected": semantic_projection(compilation),
            "source": "new_multilingual",
        })
    if len(prepared) != 10 or len({row["query"] for row in prepared}) != 10:
        raise RuntimeError("probe cases")
    return prepared


def main() -> int:
    _, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    calls = 0
    for case in prepare_cases(registry):
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
            request = plan_request(case, registry, gate["approval_scope"])
            body, main_ms, transport = post(request)
            calls += 1
            if transport:
                status, error = "transport_invalid", transport
            else:
                status, semantic, raw, error = extract_plan(body, registry, gate["approval_scope"])
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
    atomic_exclusive(OUTPUT, {
        "format": "metnos.intent-kiss-compiled-probe/0.1", "call_count": calls,
        "cases_sha256": sha256(CASES.read_bytes()).hexdigest(), "summary": summary, "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
