#!/usr/bin/env python3
"""Four-case prompt-only probe for the remaining KISS semantic errors."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import sys

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import (
    build_request as build_s0,
)
from .run_kiss_pipeline import (
    ROOT_BLOCK,
    approval_schema,
    extract_approval,
    gate_request,
    parse_gate,
)
from .run_pilot import HERE, atomic_exclusive, canonical, extract, post, profiled, validate_cases


OUTPUT = HERE / "kiss_semantic_probe_results.json"
FULL_OUTPUT = HERE / "kiss_semantic_full_results.json"
PREVIOUS = HERE / "kiss_pipeline_v2_results.json"
CORRECTIONS = HERE / "query_corrections_v1.json"
CASE_IDS = ("bp-007", "bp-012", "bp-013", "bp-018")
DECISION_CHECK = "\n".join((
    "DECISION CHECK:",
    "- Match every explicitly requested action to one exact registry route; arguments are not capabilities.",
    "- If any indispensable action has no exact route, return the whole request as unrepresentable/outside_registry; do not emit a partial graph.",
    "- missing_required_information applies only when information is required to choose the intent, not when a capability is absent or a runtime argument is omitted.",
    "- Emit only requested actions; do not add guessed context or support operations.",
    "- Independent actions have no from; use from only when a later action consumes an earlier result.",
    "- Approval wraps exactly the requested gated actions and never changes their route choice.",
))


def request_for(case, registry, approval_scope):
    request = deepcopy(build_s0(case["query"], registry, case["proposal"].language_tag))
    prompt = request["messages"][0]["content"]
    if prompt.count(ROOT_BLOCK) != 1:
        raise RuntimeError("S0 root block")
    if approval_scope == "none":
        prompt = prompt.replace(ROOT_BLOCK, ROOT_BLOCK + "\n" + DECISION_CHECK, 1)
    else:
        graph_only = (
            "STRUCTURE: return each requested action exactly once in steps. "
            "needs_approval=true only when that action occurs after user confirmation. "
            "Confirmation is not an operation. FROM uses step order."
        )
        prompt = prompt.replace(ROOT_BLOCK, graph_only + "\n" + DECISION_CHECK, 1)
        request["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "metnos_kiss_semantic_probe", "strict": True,
            "schema": approval_schema(registry, approval_scope),
        }}
    request["messages"][0]["content"] = prompt
    return profiled(request)


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    gates = {row["case_id"]: row["gate"] for row in previous["records"]}
    corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]
    by_id = {case["proposal"].proposal_id: case for case in cases}
    records = []
    for case_id in CASE_IDS:
        case = dict(by_id[case_id])
        case["query"] = corrections.get(case_id, case["query"])
        gate = gates[case_id]
        request = request_for(case, registry, gate["approval_scope"])
        body, elapsed, transport_error = post(request)
        status, semantic, raw, parse_error = ("transport_invalid", None, None, None)
        if transport_error is None:
            extractor = extract_approval if gate["approval_scope"] != "none" else extract
            status, semantic, raw, parse_error = extractor(body, registry)
        exact = status == "valid" and semantic == case["expected"]
        record = {
            "case_id": case_id, "status": status, "error": transport_error or parse_error,
            "exact": exact, "semantic": semantic, "expected": case["expected"],
            "elapsed_ms": elapsed, "request_sha256": sha256(canonical(request)).hexdigest(),
            "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
        }
        records.append(record)
        print(json.dumps({"case": case_id, "exact": exact, "valid": status == "valid"}), flush=True)
        if transport_error is not None:
            break
    summary = {
        "exact": sum(row["exact"] for row in records),
        "valid": sum(row["status"] == "valid" for row in records),
    }
    atomic_exclusive(OUTPUT, {
        "format": "metnos.intent-kiss-semantic-probe/0.1", "summary": summary,
        "prompt_delta_sha256": sha256(DECISION_CHECK.encode()).hexdigest(), "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


def full_main() -> int:
    cases, registry = validate_cases()
    if FULL_OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]
    records = []
    calls = 0
    for source in cases:
        case = dict(source)
        case_id = case["proposal"].proposal_id
        case["query"] = corrections.get(case_id, case["query"])
        gate_req = gate_request(case, registry)
        gate_body, gate_elapsed, gate_transport = post(gate_req)
        calls += 1
        try:
            gate = None if gate_transport else parse_gate(gate_body)
            status = "transport_invalid" if gate_transport else "valid"
            error = gate_transport
        except Exception as exc:
            gate, status, error = None, "technical_invalid", type(exc).__name__
        semantic = raw = None
        main_elapsed = 0
        if status == "valid" and gate["alternative_action_paths"]:
            semantic = {"kind": "unrepresentable", "reason": "unsupported_dependency"}
        elif status == "valid" and gate["system_control"] != "none" and gate["ordinary_action"]:
            semantic = {"kind": "unrepresentable", "reason": "mixed_root_kinds"}
        elif status == "valid":
            request = request_for(case, registry, gate["approval_scope"])
            body, main_elapsed, transport = post(request)
            calls += 1
            if transport:
                status, error = "transport_invalid", transport
            else:
                extractor = extract_approval if gate["approval_scope"] != "none" else extract
                status, semantic, raw, error = extractor(body, registry)
            if (gate["system_control"] != "none" and not gate["ordinary_action"]
                    and not (status == "valid" and semantic and semantic.get("kind") == "operation_graph")):
                status, error = "valid", None
                semantic = {"kind": "system_control", "control": gate["system_control"]}
        exact = status == "valid" and semantic == case["expected"]
        records.append({
            "case_id": case_id, "gate": gate, "status": status, "error": error,
            "exact": exact, "semantic": semantic, "expected": case["expected"],
            "gate_elapsed_ms": gate_elapsed, "main_elapsed_ms": main_elapsed,
            "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
        })
        print(json.dumps({"completed": len(records), "case": case_id, "exact": exact,
                          "valid": status == "valid"}), flush=True)
        if status == "transport_invalid":
            break
    summary = {
        "exact": sum(row["exact"] for row in records),
        "valid": sum(row["status"] == "valid" for row in records),
        "document_invalid": sum(row["status"] == "document_invalid" for row in records),
        "technical_invalid": sum(row["status"] == "technical_invalid" for row in records),
    }
    atomic_exclusive(FULL_OUTPUT, {
        "format": "metnos.intent-kiss-semantic-full/0.1", "call_count": calls,
        "prompt_delta_sha256": sha256(DECISION_CHECK.encode()).hexdigest(),
        "summary": summary, "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(full_main() if sys.argv[1:] == ["--full"] else main())
