#!/usr/bin/env python3
"""Minimal two-stage pilot: flat root decision, then S0 only for operation graphs."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.projection import build_schema
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_s0
from .run_pilot import HERE, atomic_exclusive, canonical, extract, post, profiled, validate_cases
from .run_root_classifier import build_request as build_root_request


OUTPUT = HERE / "two_stage_results.json"
ROOT_BLOCK = "\n".join((
    "CHOOSE ROOT FIRST: select exactly one root that matches the request, then emit it as one JSON object with kind first.",
    "EQUIVALENT ROOT TEMPLATES:",
    '- {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
    '- {"kind":"system_control","control":"<registry-control>"}',
    '- {"kind":"unrepresentable","reason":"<registry-reason>"}',
))
GRAPH_ROOT = "ROOT IS operation_graph. Emit exactly one operation_graph; do not reconsider another root."
BARRIER_ANCHOR = "BARRIER: body contains every operation that requires approval."
BARRIER_RULE = "APPROVAL: confirmation before actions -> get/approval barrier containing all gated steps; never get/inputs."


def graph_schema(registry):
    source = build_schema(registry)
    result = deepcopy(source["oneOf"][0])
    result.update({"$schema": source["$schema"],
                   "$id": "urn:metnos:intent-ir:graph-only-probe:0.1",
                   "$defs": deepcopy(source["$defs"])})
    return result


def graph_request(case, registry):
    request = deepcopy(build_s0(case["query"], registry, case["proposal"].language_tag))
    prompt = request["messages"][0]["content"]
    if prompt.count(ROOT_BLOCK) != 1 or prompt.count(BARRIER_ANCHOR) != 1:
        raise RuntimeError("graph prompt anchor")
    prompt = prompt.replace(ROOT_BLOCK, GRAPH_ROOT, 1)
    prompt = prompt.replace(BARRIER_ANCHOR, BARRIER_RULE, 1)
    request["messages"][0]["content"] = prompt
    request["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "metnos_intent_graph_only_probe", "strict": True,
        "schema": graph_schema(registry),
    }}
    return profiled(request)


def parse_root(body):
    try:
        wrapper = json.loads(body)
        content = wrapper["choices"][0]["message"]["content"]
        raw = content.encode("utf-8", errors="strict")
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {"decision"} or type(value["decision"]) is not str:
            raise ValueError("root document")
        return value["decision"], raw, None
    except Exception as exc:
        return None, None, type(exc).__name__


def semantic_from_decision(decision):
    if decision.startswith("system_control:"):
        return {"kind": "system_control", "control": decision.split(":", 1)[1]}
    if decision.startswith("unrepresentable:"):
        return {"kind": "unrepresentable", "reason": decision.split(":", 1)[1]}
    raise ValueError("nonterminal decision")


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    total_calls = 0
    for case in cases:
        root_request = build_root_request(case, registry)
        root_body, root_elapsed, root_transport = post(root_request)
        total_calls += 1
        decision, root_raw, root_parse = (None, None, None)
        if root_transport is None:
            decision, root_raw, root_parse = parse_root(root_body)
        status = "transport_invalid" if root_transport else "technical_invalid" if root_parse else "valid"
        semantic = None
        graph_raw = None
        graph_error = None
        graph_elapsed = 0
        graph_request_sha = None
        if status == "valid" and decision == "operation_graph":
            request = graph_request(case, registry)
            graph_request_sha = sha256(canonical(request)).hexdigest()
            body, graph_elapsed, transport_error = post(request)
            total_calls += 1
            if transport_error is not None:
                status, graph_error = "transport_invalid", transport_error
            else:
                status, semantic, graph_raw, graph_error = extract(body, registry)
        elif status == "valid":
            try:
                semantic = semantic_from_decision(decision)
            except Exception as exc:
                status, graph_error = "technical_invalid", type(exc).__name__
        exact = status == "valid" and semantic == case["expected"]
        record = {
            "case_id": case["proposal"].proposal_id,
            "decision": decision,
            "status": status,
            "error": root_transport or root_parse or graph_error,
            "exact": exact,
            "semantic": semantic,
            "expected": case["expected"],
            "root_raw_b64": None if root_raw is None else base64.b64encode(root_raw).decode("ascii"),
            "graph_raw_b64": None if graph_raw is None else base64.b64encode(graph_raw).decode("ascii"),
            "root_elapsed_ms": root_elapsed,
            "graph_elapsed_ms": graph_elapsed,
            "root_request_sha256": sha256(canonical(root_request)).hexdigest(),
            "graph_request_sha256": graph_request_sha,
        }
        records.append(record)
        print(json.dumps({"completed": len(records), "calls": total_calls,
                          "case": record["case_id"], "decision": decision,
                          "status": status, "exact": exact}, sort_keys=True), flush=True)
        if status == "transport_invalid":
            atomic_exclusive(OUTPUT, {"state": "transport_stop", "call_count": total_calls,
                                      "records": records})
            return 2
    summary = {
        "exact": sum(item["exact"] for item in records),
        "valid": sum(item["status"] == "valid" for item in records),
        "document_invalid": sum(item["status"] == "document_invalid" for item in records),
        "technical_invalid": sum(item["status"] == "technical_invalid" for item in records),
    }
    atomic_exclusive(OUTPUT, {"format": "metnos.intent-two-stage-pilot/0.1", "state": "complete",
                              "case_count": len(records), "call_count": total_calls,
                              "retry_count": 0, "summary": summary, "records": records})
    print(json.dumps({"complete": True, "calls": total_calls, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
