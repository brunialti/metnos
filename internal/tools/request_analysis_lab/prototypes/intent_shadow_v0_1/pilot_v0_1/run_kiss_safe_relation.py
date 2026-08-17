#!/usr/bin/env python3
"""Twenty-case KISS probe with target provenance separate from ordering."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from .run_kiss_convention import (
    decode_document,
    flatten_operations,
    gate_request,
    main_request,
    prepare_full_cases,
)
from .run_kiss_surgical import parse_gate
from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "kiss_safe_relation_20_v4_results.json"


def relation_request(case, routes):
    pairs = "\n".join(
        f"{index - 1}->{index}: {routes[index - 1]} -> {routes[index]}"
        for index in range(1, len(routes))
    )
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['language_tag']}",
        "Answer two yes/no questions for each adjacent action pair.",
        "DEVI: uses_previous_result=true only when the later action consumes the earlier output, including a pronoun naming the found results.",
        "NON DEVI: use true merely because actions share an explicitly named object or appear in textual order.",
        "OK: find matching files, then compress them => uses_previous_result=true.",
        "ERRORE: read Issue 42, then delete Issue 42 => uses_previous_result=true.",
        "DEVI: ignore an approval step while deciding where the later target comes from.",
        "NON DEVI: turn a pronoun or found-result target into direct because confirmation occurs between the actions.",
        "OK: find files and, after approval, delete those found => uses_previous_result=true.",
        "ERRORE: because approval intervenes, set uses_previous_result=false.",
        "DEVI: explicit_after_previous=true only when the request explicitly requires that order.",
        "NON DEVI: infer explicit order from textual mention order alone; data dependency is ordered later by code.",
        "OK: read Issue 42, then delete Issue 42 => explicit_after_previous=true.",
        "ERRORE: find a contact and independently show the time => explicit_after_previous=true.",
        "PAIRS:",
        pairs,
    )) + "\n"
    count = len(routes) - 1
    relation = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "uses_previous_result": {"type": "boolean"},
            "explicit_after_previous": {"type": "boolean"},
        },
        "required": ["uses_previous_result", "explicit_after_previous"],
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relations": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": relation,
            }
        },
        "required": ["relations"],
    }
    request = profiled({
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": case["query"]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "metnos_safe_adjacent_relations", "strict": True, "schema": schema,
        }},
        "temperature": 0,
    })
    request["max_tokens"] = 180
    return request


def parse_relations(body, count):
    value = json.loads(json.loads(body)["choices"][0]["message"]["content"])
    if type(value) is not dict or set(value) != {"relations"}:
        raise ValueError("relation document")
    relations = value["relations"]
    if type(relations) is not list or len(relations) != count:
        raise ValueError("relation count")
    normalized = []
    required = {"uses_previous_result", "explicit_after_previous"}
    for relation in relations:
        if (
            type(relation) is not dict
            or set(relation) != required
            or any(type(relation[key]) is not bool for key in required)
        ):
            raise ValueError("relation item")
        previous = relation["uses_previous_result"]
        normalized.append({
            **relation,
            "after_previous": previous or relation["explicit_after_previous"],
        })
    return normalized


def apply_relations(document, relations):
    adjusted = deepcopy(document)
    operations = flatten_operations(adjusted["steps"])
    if len(relations) != len(operations) - 1:
        raise ValueError("relation/operation mismatch")
    for index, relation in enumerate(relations, start=1):
        operation = operations[index]
        if relation["uses_previous_result"]:
            operation["from"] = [index - 1]
        else:
            operation.pop("from", None)
    return adjusted


def run() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    selected = prepare_full_cases(cases)
    records = []
    calls = 0
    for case in selected:
        gate_req = gate_request(case, registry)
        gate_body, gate_ms, gate_transport = post(gate_req)
        calls += 1
        try:
            gate = None if gate_transport else parse_gate(gate_body)
            status = "transport_invalid" if gate_transport else "valid"
            error = gate_transport
        except Exception as exc:
            gate, status, error = None, "technical_invalid", type(exc).__name__

        semantic = raw = None
        main_ms = relation_ms = 0
        relations = None
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
                    if document.get("kind") == "operation_graph":
                        operations = flatten_operations(document["steps"])
                        if len(operations) > 1:
                            relation_req = relation_request(case, [item["route"] for item in operations])
                            relation_body, relation_ms, relation_transport = post(relation_req)
                            calls += 1
                            if relation_transport:
                                status, error = "transport_invalid", relation_transport
                            else:
                                relations = parse_relations(relation_body, len(operations) - 1)
                                document = apply_relations(document, relations)
                    if status == "valid":
                        compilation = compile_ir(document, registry)
                        if compilation.valid:
                            semantic = semantic_projection(compilation)
                        else:
                            status = "document_invalid"
                            error = ";".join(issue.code for issue in compilation.issues)
                except Exception as exc:
                    status, semantic, error = "technical_invalid", None, type(exc).__name__

        exact = status == "valid" and semantic == case["expected"]
        records.append({
            "case_id": case["case_id"],
            "language_tag": case["language_tag"],
            "status": status,
            "error": error,
            "exact": exact,
            "semantic": semantic,
            "expected": case["expected"],
            "relations": relations,
            "gate": gate,
            "gate_elapsed_ms": gate_ms,
            "main_elapsed_ms": main_ms,
            "relation_elapsed_ms": relation_ms,
            "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
            "gate_request_sha256": sha256(canonical(gate_req)).hexdigest(),
        })
        print(json.dumps({
            "completed": len(records), "case": case["case_id"],
            "exact": exact, "status": status,
        }), flush=True)
        if status == "transport_invalid":
            break

    summary = {
        "exact": sum(row["exact"] for row in records),
        "valid": sum(row["status"] == "valid" for row in records),
        "technical_invalid": sum(row["status"] == "technical_invalid" for row in records),
        "relation_previous": sum(
            relation["uses_previous_result"]
            for row in records for relation in (row["relations"] or [])
        ),
        "relation_not_previous": sum(
            not relation["uses_previous_result"]
            for row in records for relation in (row["relations"] or [])
        ),
        "relation_after": sum(
            relation["after_previous"]
            for row in records for relation in (row["relations"] or [])
        ),
    }
    atomic_exclusive(OUTPUT, {
        "format": "metnos.intent-kiss-safe-relation/0.1",
        "call_count": calls,
        "summary": summary,
        "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
