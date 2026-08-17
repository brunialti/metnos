#!/usr/bin/env python3
"""KISS reference plus the ordinal-zero dependency convention."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import sys
from types import SimpleNamespace

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from .run_kiss_compiled import CASES, prepare_cases
from .run_kiss_semantic_probe import CORRECTIONS, request_for as semantic_request
from .run_kiss_surgical import gate_request as base_gate_request, parse_gate
from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "kiss_convention_relation_results.json"
FULL_OUTPUT = HERE / "kiss_convention_relation_full_v4_results.json"


def gate_request(case, registry):
    request = base_gate_request(case, registry)
    request["messages"][0]["content"] += (
        "Approval counts only when the user explicitly requests confirmation; "
        "never infer it from risk or from the type of action.\n"
    )
    return request


def normalize_ordinal_zero(steps):
    """Only the first executable operation may encode [0] as no predecessor."""
    normalized = deepcopy(steps)

    def first_operation(items):
        for item in items:
            if type(item) is not dict:
                continue
            if "route" in item:
                return item
            body = item.get("body")
            if type(body) is list:
                found = first_operation(body)
                if found is not None:
                    return found
        return None

    first = first_operation(normalized)
    if first is not None and first.get("from") == [0]:
        first.pop("from")
    return normalized


def decode_document(body, scope):
    content = json.loads(body)["choices"][0]["message"]["content"]
    raw = content.encode("utf-8", errors="strict")
    value = json.loads(raw)
    if value.get("kind") == "unrepresentable":
        return raw, value
    if scope == "none":
        if value.get("kind") == "operation_graph":
            value = deepcopy(value)
            value["steps"] = normalize_ordinal_zero(value["steps"])
        return raw, value
    if type(value) is not dict or set(value) != {"steps"}:
        raise ValueError("approval root")
    model_steps = normalize_ordinal_zero(value["steps"])
    flags = [step["needs_approval"] for step in model_steps]
    if True not in flags:
        raise ValueError("approval absent")
    split = flags.index(True)
    if any(not flag for flag in flags[split:]):
        raise ValueError("approval not suffix")
    clean = [{key: item for key, item in step.items() if key != "needs_approval"}
             for step in model_steps]
    graph_steps = ([{"barrier": "get/approval", "body": clean}] if split == 0 else
                   [*clean[:split], {"barrier": "get/approval", "body": clean[split:]}])
    return raw, {"kind": "operation_graph", "steps": graph_steps}


def flatten_operations(steps):
    operations = []
    for step in steps:
        if "route" in step:
            operations.append(step)
        elif type(step.get("body")) is list:
            operations.extend(flatten_operations(step["body"]))
    return operations


def relation_request(case, routes):
    pairs = "\n".join(
        f"{index - 1}->{index}: {routes[index - 1]} -> {routes[index]}"
        for index in range(1, len(routes))
    )
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['language_tag']}",
        "For each adjacent action pair, decide whether the request requires the later action to happen after the earlier action.",
        "Return true for result use, a pronoun or demonstrative linking the same entity, or an explicit temporal prerequisite.",
        "Return false for actions declared independent and for mere list order without any such link.",
        "PAIRS:", pairs,
    )) + "\n"
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"uses_previous": {
            "type": "array", "minItems": len(routes) - 1, "maxItems": len(routes) - 1,
            "items": {"type": "boolean"},
        }},
        "required": ["uses_previous"],
    }
    request = profiled({
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": case["query"]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "metnos_adjacent_relations", "strict": True, "schema": schema,
        }},
        "temperature": 0,
    })
    request["max_tokens"] = 100
    return request


def parse_relations(body, count):
    value = json.loads(json.loads(body)["choices"][0]["message"]["content"])
    if (type(value) is not dict or set(value) != {"uses_previous"}
            or type(value["uses_previous"]) is not list
            or len(value["uses_previous"]) != count
            or any(type(flag) is not bool for flag in value["uses_previous"])):
        raise ValueError("relation document")
    return value["uses_previous"]


def main_request(case, registry, scope):
    adapted = {
        "query": case["query"],
        "proposal": SimpleNamespace(language_tag=case["language_tag"]),
    }
    return profiled(semantic_request(adapted, registry, scope))


def compile_document(raw, document, registry, relation_flags):
    if document.get("kind") == "operation_graph" and relation_flags is not None:
        operations = flatten_operations(document["steps"])
        for index, flag in enumerate(relation_flags, start=1):
            if flag and "from" not in operations[index]:
                operations[index]["from"] = [index - 1]
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
        relation_ms = 0
        relation_flags = None
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
                try:
                    raw, document = decode_document(body, gate["approval_scope"])
                    operations = (flatten_operations(document["steps"])
                                  if document.get("kind") == "operation_graph" else [])
                    if len(operations) > 1 and any("from" not in operation for operation in operations[1:]):
                        relation_req = relation_request(case, [operation["route"] for operation in operations])
                        relation_body, relation_ms, relation_transport = post(relation_req)
                        calls += 1
                        if relation_transport:
                            status, error = "transport_invalid", relation_transport
                        else:
                            relation_flags = parse_relations(relation_body, len(operations) - 1)
                    if status == "valid":
                        status, semantic, raw, error = compile_document(
                            raw, document, registry, relation_flags)
                except Exception as exc:
                    status, semantic, error = "technical_invalid", None, type(exc).__name__
        exact = status == "valid" and semantic == case["expected"]
        records.append({
            "case_id": case["case_id"], "source": case["source"], "language_tag": case["language_tag"],
            "gate": gate, "status": status, "error": error, "exact": exact,
            "semantic": semantic, "expected": case["expected"],
            "gate_elapsed_ms": gate_ms, "main_elapsed_ms": main_ms,
            "relation_elapsed_ms": relation_ms, "relation_flags": relation_flags,
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
        "format": "metnos.intent-kiss-convention-probe/0.1", "call_count": calls,
        "cases_sha256": sha256(CASES.read_bytes()).hexdigest(), "summary": summary, "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def prepare_full_cases(cases):
    corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]
    return [{
        "case_id": case["proposal"].proposal_id,
        "language_tag": case["proposal"].language_tag,
        "query": corrections.get(case["proposal"].proposal_id, case["query"]),
        "expected": case["expected"],
        "source": "reference_panel",
    } for case in cases]


def main() -> int:
    cases, registry = validate_cases()
    full = sys.argv[1:] == ["--full"]
    if sys.argv[1:] not in ([], ["--full"]):
        raise SystemExit("usage: run_kiss_convention.py [--full]")
    selected = prepare_full_cases(cases) if full else prepare_cases(registry)
    run_cases(selected, registry, FULL_OUTPUT if full else OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
