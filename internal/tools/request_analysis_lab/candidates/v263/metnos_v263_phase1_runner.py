#!/usr/bin/env python3
"""V26.3: minimal proof-carrying phase-1 candidate, frozen offline.

The candidate keeps the V26.2 semantic output dimensions, restores a complete
and symmetric technical contract, uses proof references by construction, and
performs one inference call with no semantic retry.  This file must be frozen
and audited before any native run; offline analyses receive no acceptance
credit.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import inspect
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema

sys.path.insert(0, "/tmp")
import metnos_v262_minimal_phase1_runner as prior  # noqa: E402


VERSION = "metnos.v26.3-minimal-technical-contract/1.0"
RUNNER_PATH = Path(__file__)
SCHEMA_PATH = Path("/tmp/metnos_v263_phase1.schema.json")
PROMPT_PATH = Path("/tmp/metnos_v263_phase1.prompt.txt")
FIXTURE_PATH = Path("/tmp/metnos_v263_phase1_controls_frozen.json")
MUTATIONS_PATH = Path("/tmp/metnos_v263_phase1_mutations_frozen.json")
CONTAMINATION_PATH = Path("/tmp/metnos_v263_phase1_contamination_audit.json")
FREEZE_PATH = Path("/tmp/metnos_v263_phase1.freeze.json")
GATE_PATH = Path("/tmp/metnos_v263_phase1_preinference_gate.json")
AUDITOR_PATH = Path("/tmp/metnos_prompt_contamination_audit.py")

CONTROLS_PATH = prior.CONTROLS_PATH
ROLES = prior.ROLES
INTERPRETATIONS = prior.INTERPRETATIONS
SPEECH_ACTS = prior.SPEECH_ACTS
RELATIONS = prior.RELATIONS
SUBJECT_REFS = prior.SUBJECT_REFS
GRAMMATICAL_PERSONS = prior.GRAMMATICAL_PERSONS
TIME_SCOPES = prior.TIME_SCOPES
EVIDENCE_CLAIMS = prior.EVIDENCE_CLAIMS
EVIDENCE_KINDS = prior.EVIDENCE_KINDS
SEMANTIC_FIELDS = (
    "role", "interpretation", "speech_act", "relation", "subject_ref",
    "grammatical_person", "time_scope",
)

# grammatical_person is retained as analysis output but is not duplicated in
# the route binding predicate: subject_ref already denotes the current actor.
BINDING_SIGNATURE = {
    "role": "request",
    "interpretation": "supported",
    "speech_act": "open_question",
    "relation": "spatial.located_at",
    "subject_ref": "current_actor",
    "time_scope": "current",
}
REMOVED_REDUNDANT_FIELDS = prior.REMOVED_REDUNDANT_FIELDS


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def unicode_segments(text: str) -> list[dict[str, Any]]:
    return prior.unicode_segments(text)


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


def evidence_schema() -> dict[str, Any]:
    """Proof references whose local anchors are implied by their variant."""
    return {
        "oneOf": [
            {
                "type": "object", "additionalProperties": False,
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "explicit_segment"},
                    "start_segment_id": {"type": "integer", "minimum": 1},
                    "end_segment_id": {"type": "integer", "minimum": 1},
                },
                "required": ["kind", "start_segment_id", "end_segment_id"],
            },
            *[
                {
                    "type": "object", "additionalProperties": False,
                    "properties": {"kind": {"const": kind}},
                    "required": ["kind"],
                }
                for kind in (
                    "predicate_morphology", "clause_semantics",
                    "speaker_context", "discourse_context", "tense_morphology",
                )
            ],
        ]
    }


def live_schema() -> dict[str, Any]:
    props = {
        "clause_id": {"type": "integer", "minimum": 1},
        "clause_start_segment_id": {"type": "integer", "minimum": 1},
        "clause_end_segment_id": {"type": "integer", "minimum": 1},
        "predicate_segment_id": {"type": "integer", "minimum": 1},
        "role": _enum(ROLES),
        "interpretation": _enum(INTERPRETATIONS),
        "speech_act": _enum(SPEECH_ACTS),
        "relation": _enum(RELATIONS),
        "subject_ref": _enum(SUBJECT_REFS),
        "grammatical_person": _enum(GRAMMATICAL_PERSONS),
        "time_scope": _enum(TIME_SCOPES),
        "evidence": {
            "type": "object", "additionalProperties": False,
            "properties": {claim: evidence_schema() for claim in EVIDENCE_CLAIMS},
            "required": EVIDENCE_CLAIMS,
        },
    }
    clause = {
        "type": "object", "additionalProperties": False,
        "properties": props, "required": list(props),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "properties": {
            "clauses": {"type": "array", "minItems": 1, "items": clause},
        },
        "required": ["clauses"],
    }


def system_prompt() -> str:
    return """You are a multilingual phase-1 structural semantic analyzer.
Return only JSON conforming to the supplied schema. Analyze the untouched request through its supplied Unicode default-word-boundary segments. Do not rewrite the request and do not choose product actions, capabilities, routes, tools, product objects, or execution plans. Enum labels are technical denotations, never source-language tokens or lexical triggers.

Decompose the request into clauses and emit one record per predicate or semantic relation in source order. A nested condition, description, relative clause, or quotation remains distinct from the clause that contains it. Clause and predicate identifiers refer only to supplied segment ids.

Role contract:
- request: the discourse move asks the assistant to produce an answer, result, or effect;
- condition: the clause constrains when or whether another request applies and is not independently requested;
- description: the clause asserts, reports, qualifies, or describes a state and is not independently requested;
- quote: the clause belongs to quoted or mentioned content rather than the current speaker's request.

Speech-act contract:
- open_question: one semantic argument is the requested unknown value;
- polar_question: the requested result is the truth value of a complete proposition;
- imperative: the requested result is an effect or operation outcome;
- assertion: a proposition is presented without requesting its truth value;
- condition, description, and quote preserve their corresponding non-requesting clause functions.
Role and speech_act must agree. Embedded interrogative or imperative grammar does not override condition, description, or quote scope.

Relation registry with ordered argument signatures:
- spatial.located_at(entity, spatial_position, time): physical or geographic placement in the world;
- identity.same_as(entity, identity): identity, class, or name of an entity;
- filesystem.located_at(file, resource_locator): storage containment or path of a digital filesystem resource;
- runtime.host_of(process, host): machine or runtime environment hosting a process;
- spatial.near(collection, spatial_position): entities selected by spatial proximity to a position;
- acl.share(resource, grantee): continuing permission or access granted on a resource;
- movement.destination(resource, resource_locator): target locator of movement;
- workflow.position(actor, workflow_progress): state or progress within a workflow;
- document.position(actor, resource_locator): position within a document;
- none: no registry relation is represented by the clause.
Choose the namespace from the denoted argument types and relation, not from dictionary similarity of the predicate. For open_question, the missing ordered argument is the requested value; subject_ref classifies the first relation argument and time_scope classifies temporal anchoring. Do not emit a separate focus label.

Subject-reference contract:
- current_actor: the active discourse participant that produced the original request;
- explicit_entity: the first relation argument is an overtly identified entity or resource;
- unknown_entity: the first relation argument itself is the open unknown;
- deictic_context: the first relation argument is identified by the active non-speaker context;
- current_actor_location_operand: the current actor's spatial position is only an operand of a different requested relation or effect;
- quoted_actor: an actor reference is internal to quoted or mentioned content;
- none: the clause has no identifiable first relation argument.
Do not convert an explicit third party, resource, contextual place, or quoted speaker into current_actor.

Grammatical-person contract:
- first: clause structure or morphology indexes the speaker;
- second: it indexes the addressee;
- third: it indexes neither discourse participant;
- none: grammatical person is not expressed for the relation subject;
- ambiguous: more than one person analysis remains after syntax and discourse context.
Grammatical person is independent of the requested unknown and must follow the relation subject, including fused or omitted surface subjects when morphology determines person.

Temporal contract:
- current: the relation is anchored to the active request state or uncontradicted current discourse state;
- historical: the relation is anchored before the active request state;
- contextual: anchoring is supplied by a relative, quoted, conditional, document, workflow, destination, or other local discourse frame;
- none: the denotation is genuinely atemporal.
An explicit or grammatical non-current anchor overrides the current discourse default.

Evidence is proof for relation, subject, and time independently. Every claim uses exactly one tagged variant:
- explicit_segment carries the smallest positive inclusive segment range that directly realizes the claim;
- predicate_morphology means the current clause's predicate morphology supplies the claim;
- tense_morphology means the current clause's predicate tense or aspect supplies temporal anchoring;
- clause_semantics means composition of the current clause supplies the claim;
- speaker_context means the active turn participant supplies the subject reference;
- discourse_context means the active discourse supplies a referent or temporal anchor;
- none means the claim has no support.
Only explicit_segment carries ids. The current clause and predicate anchors of all other variants are implicit by construction. Implicit arguments supported by morphology, compositional semantics, speaker context, or discourse context are grounded; lack of an overt segment alone does not make the analysis ambiguous or unsupported. Never invent evidence and never use current context against explicit contrary evidence.

Interpretation is a coverage state:
- supported: exactly one complete registry analysis remains and every emitted claim has permitted evidence;
- ambiguous: two or more materially different complete analyses remain after syntax, denotation, and discourse constraints;
- unsupported: the clause has a denotation but no relation in the registry can represent it.
When relation identity is unresolved across registry members, use relation=none with ambiguous. When no registry member applies, use relation=none with unsupported. Do not use ambiguous merely because an argument is implicit, and do not use unsupported merely because the predicate lacks an overt lexical counterpart to an enum label.

Before output, verify clause order and bounds, predicate containment, role/speech agreement, relation namespace and arity, subject/person consistency, temporal anchoring, evidence grounding, and condition/description/quote scope. Do not invent extra requested clauses.
"""


def _error(code: str, path: str, message: str) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message, "retryable": False}


def validate_frame(frame: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    try:
        jsonschema.Draft202012Validator(live_schema()).validate(frame)
    except jsonschema.ValidationError as exc:
        errors.append(_error("schema", "/" + "/".join(str(item) for item in exc.absolute_path), exc.message))
        return {"valid": False, "errors": errors}
    segment_ids = {segment["id"] for segment in segments}
    previous_clause_id = 0
    previous_start = 0
    for index, clause in enumerate(frame["clauses"]):
        path = f"/clauses/{index}"
        clause_id = clause["clause_id"]
        start = clause["clause_start_segment_id"]
        end = clause["clause_end_segment_id"]
        predicate = clause["predicate_segment_id"]
        if clause_id <= previous_clause_id:
            errors.append(_error("clause_order", path + "/clause_id", "clause ids must increase"))
        if start < previous_start:
            errors.append(_error("source_order", path + "/clause_start_segment_id", "clauses must follow source order"))
        previous_clause_id = clause_id
        previous_start = start
        if start not in segment_ids or end not in segment_ids or start > end:
            errors.append(_error("clause_span", path, "clause span is outside supplied segments"))
        if predicate not in segment_ids or not (start <= predicate <= end):
            errors.append(_error("predicate_span", path + "/predicate_segment_id", "predicate must be inside clause"))
        for claim, evidence in clause["evidence"].items():
            if evidence["kind"] == "explicit_segment":
                ev_start = evidence["start_segment_id"]
                ev_end = evidence["end_segment_id"]
                if ev_start not in segment_ids or ev_end not in segment_ids or not (start <= ev_start <= ev_end <= end):
                    errors.append(_error("evidence_span", f"{path}/evidence/{claim}", "explicit evidence must be inside clause"))
        if clause["interpretation"] == "supported" and clause["relation"] == "none":
            errors.append(_error("supported_relation", path + "/relation", "supported analysis requires a registry relation"))
        if clause["interpretation"] in {"ambiguous", "unsupported"} and clause["relation"] != "none":
            errors.append(_error("coverage_relation", path + "/relation", "unresolved or out-of-registry analysis must not assert one relation"))
    return {"valid": not errors, "errors": errors}


def has_complete_evidence(clause: dict[str, Any]) -> bool:
    evidence = clause.get("evidence", {})
    return all(evidence.get(claim, {}).get("kind") not in {None, "none"} for claim in EVIDENCE_CLAIMS)


def matches_binding(clause: dict[str, Any]) -> bool:
    return all(clause.get(key) == value for key, value in BINDING_SIGNATURE.items()) and has_complete_evidence(clause)


def request_body(query: str, seed: int = 92) -> dict[str, Any]:
    payload = {"original_request": query, "segments": unicode_segments(query)}
    return {
        "model": "local", "temperature": 0, "seed": seed, "max_tokens": 1200,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "phase1_minimal_contract_v263", "schema": live_schema(), "strict": True,
        }},
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def call_once(query: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = prior.v25.bench.tier_endpoint(prior.v25.bench.tier_for("intent.extract")).rstrip("/")
    request = urllib.request.Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(request_body(query, seed), ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw), {"latency_ms": (time.perf_counter() - started) * 1000, "error": ""}
    except Exception as exc:
        return {}, {
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error": "transport_or_json", "error_type": type(exc).__name__,
        }


def verify_gate() -> None:
    gate = json.loads(GATE_PATH.read_text())
    if not gate.get("inference_allowed"):
        raise RuntimeError("V26.3 inference blocked by pre-inference gate")
    if gate.get("freeze_sha256") != sha(FREEZE_PATH):
        raise RuntimeError("V26.3 freeze changed after audit")
    if gate.get("contamination_audit_sha256") != sha(CONTAMINATION_PATH):
        raise RuntimeError("V26.3 contamination audit changed after review")


def run_case(query: str) -> dict[str, Any]:
    verify_freeze()
    verify_gate()
    segments = unicode_segments(query)
    frame, transport = call_once(query, 92)
    validation = (
        validate_frame(frame, segments) if not transport["error"]
        else {"valid": False, "errors": [_error(transport["error"], "", transport.get("error_type", "transport"))]}
    )
    common = {
        "segment_count": len(segments),
        "segment_layout_sha256": canonical_hash([
            {key: segment[key] for key in ("id", "start_char", "end_char")}
            for segment in segments
        ]),
        "model_calls": 1, "latency_ms": transport["latency_ms"],
        "transport_error": transport["error"],
    }
    if validation["valid"]:
        return {**common, "status": "evaluated", "frame": frame, "validation": {"valid": True, "error_codes": []}}
    return {
        **common, "status": "not_evaluated",
        "validation": {"valid": False, "error_codes": [error["code"] for error in validation["errors"]]},
    }


def controls() -> list[dict[str, Any]]:
    return prior.controls()


def projected_expected_signature(case: dict[str, Any]) -> dict[str, Any]:
    expected = prior.base.expected_signature(case)
    expected["interpretation"] = "supported"
    return {field: expected[field] for field in SEMANTIC_FIELDS}


def relevant_clause(frame: dict[str, Any]) -> dict[str, Any] | None:
    active = [clause for clause in frame.get("clauses", []) if clause.get("relation") != "none"]
    return active[0] if active else (frame.get("clauses") or [None])[0]


def evaluate_records(records: list[dict[str, Any]], oracle: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for record in records:
        expected = oracle[record["opaque_case_id"]]
        result = record["result"]
        frame = result.get("frame") or {}
        clauses = frame.get("clauses", [])
        coverage_states = [clause.get("interpretation") for clause in clauses]
        fully_supported = bool(clauses) and all(state == "supported" for state in coverage_states)
        evaluable = result.get("status") == "evaluated" and fully_supported
        clause = relevant_clause(frame) if evaluable else None
        semantic_exact = (
            clause is not None
            and all(clause.get(field) == value for field, value in expected["signature"].items())
        ) if evaluable else None
        predicted = any(matches_binding(item) for item in clauses) if evaluable else None
        rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "query_sha256_utf8": record["query_sha256_utf8"],
            "evaluable": evaluable,
            "coverage": (
                "supported" if fully_supported else
                "ambiguous" if "ambiguous" in coverage_states else
                "unsupported" if "unsupported" in coverage_states else
                "invalid"
            ),
            "semantic_exact": semantic_exact,
            "binding_ok": predicted == expected["expected_binding"] if evaluable else None,
            "predicted_binding": predicted,
            "expected_binding": expected["expected_binding"],
            "evidence_complete": has_complete_evidence(clause) if clause is not None else None,
        })
    evaluated = [row for row in rows if row["evaluable"]]
    positives = [row for row in evaluated if row["expected_binding"]]
    negatives = [row for row in evaluated if not row["expected_binding"]]
    return {
        "summary": {
            "records": len(rows), "evaluable": len(evaluated),
            "not_evaluated": len(rows) - len(evaluated),
            "ambiguous": sum(row["coverage"] == "ambiguous" for row in rows),
            "unsupported": sum(row["coverage"] == "unsupported" for row in rows),
            "invalid": sum(row["coverage"] == "invalid" for row in rows),
            "semantic_exact": sum(bool(row["semantic_exact"]) for row in evaluated),
            "binding_exact": sum(bool(row["binding_ok"]) for row in evaluated),
            "positive_hits": sum(row["predicted_binding"] is True for row in positives),
            "positive_denominator": len(positives),
            "negative_leakage": sum(row["predicted_binding"] is True for row in negatives),
            "negative_denominator": len(negatives),
            "evidence_complete": sum(bool(row["evidence_complete"]) for row in evaluated),
            "model_calls": sum(record["result"].get("model_calls", 0) for record in records),
            "retries": 0,
        },
        "records": rows,
    }


def run_controls(start: int = 1, end: int | None = None) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text())
    oracle = {case["opaque_case_id"]: case for case in fixture["cases"]}
    selected = controls()[start - 1:end]
    records = [{
        "opaque_case_id": sha_text(case["id"]),
        "query_sha256_utf8": sha_text(case["query"]),
        "result": run_case(case["query"]),
    } for case in selected]
    evaluation = evaluate_records(records, oracle)
    latencies = [record["result"]["latency_ms"] for record in records]
    summary = {
        **evaluation["summary"],
        "latency_call_p50_ms": statistics.median(latencies) if latencies else None,
        "latency_call_p95_ms": (
            statistics.quantiles(latencies, n=100, method="inclusive")[94]
            if len(latencies) > 1 else None
        ),
    }
    return {
        "version": VERSION, "freeze_sha256": sha(FREEZE_PATH),
        "summary": summary, "records": records,
        "evaluation_records": evaluation["records"],
        "native_run": True, "posthoc_credit": False,
    }


def _runtime_ast_isolated() -> bool:
    tree = ast.parse(RUNNER_PATH.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    forbidden = {"controls", "FIXTURE_PATH", "expected", "gold", "oracle", "overlay"}
    for name in ("request_body", "call_once", "run_case"):
        symbols = (
            {item.id for item in ast.walk(functions[name]) if isinstance(item, ast.Name)}
            | {item.attr for item in ast.walk(functions[name]) if isinstance(item, ast.Attribute)}
        )
        if forbidden & symbols:
            return False
    return True


def canonical_frame() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segments = [
        {"id": 1, "text": "x", "start_char": 0, "end_char": 1},
        {"id": 2, "text": "y", "start_char": 1, "end_char": 2},
    ]
    frame = {"clauses": [{
        "clause_id": 1, "clause_start_segment_id": 1,
        "clause_end_segment_id": 2, "predicate_segment_id": 2,
        **BINDING_SIGNATURE, "grammatical_person": "first",
        "evidence": {
            "relation": {"kind": "clause_semantics"},
            "subject": {"kind": "predicate_morphology"},
            "time": {"kind": "tense_morphology"},
        },
    }]}
    return frame, segments


def mutation_tests() -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    frame, segments = canonical_frame()
    tests.append({"id": "canonical_valid", "pass": validate_frame(frame, segments)["valid"]})
    tests.append({"id": "canonical_matches", "pass": matches_binding(frame["clauses"][0])})
    schema_text = json.dumps(live_schema(), sort_keys=True)
    prompt = system_prompt()
    for removed in REMOVED_REDUNDANT_FIELDS:
        tests.append({"id": f"removed_{removed}", "pass": removed not in schema_text})
    for field in BINDING_SIGNATURE:
        changed = copy.deepcopy(frame)
        alternatives = live_schema()["properties"]["clauses"]["items"]["properties"][field]["enum"]
        changed["clauses"][0][field] = next(value for value in alternatives if value != changed["clauses"][0][field])
        tests.append({"id": f"binding_mutation_{field}", "pass": not matches_binding(changed["clauses"][0])})
    person = copy.deepcopy(frame)
    person["clauses"][0]["grammatical_person"] = "none"
    tests.append({"id": "person_not_duplicated_in_binding", "pass": matches_binding(person["clauses"][0])})
    for claim in EVIDENCE_CLAIMS:
        changed = copy.deepcopy(frame)
        changed["clauses"][0]["evidence"][claim] = {"kind": "none"}
        tests.append({"id": f"evidence_none_{claim}", "pass": validate_frame(changed, segments)["valid"] and not matches_binding(changed["clauses"][0])})
    bad_coverage = copy.deepcopy(frame)
    bad_coverage["clauses"][0]["interpretation"] = "unsupported"
    tests.append({"id": "unsupported_relation_assertion_invalid", "pass": not validate_frame(bad_coverage, segments)["valid"]})
    unsupported = copy.deepcopy(frame)
    unsupported["clauses"][0]["interpretation"] = "unsupported"
    unsupported["clauses"][0]["relation"] = "none"
    tests.append({"id": "unsupported_none_valid", "pass": validate_frame(unsupported, segments)["valid"]})
    explicit = copy.deepcopy(frame)
    explicit["clauses"][0]["evidence"]["subject"] = {"kind": "explicit_segment", "start_segment_id": 3, "end_segment_id": 3}
    tests.append({"id": "out_of_clause_proof_invalid", "pass": not validate_frame(explicit, segments)["valid"]})
    leaked = copy.deepcopy(frame)
    leaked["clauses"][0]["evidence"]["relation"]["free_text"] = "x"
    tests.append({"id": "evidence_free_text_invalid", "pass": not validate_frame(leaked, segments)["valid"]})
    relation_arity_lines = [line for line in prompt.splitlines() if line.startswith("- ") and "(" in line and "):" in line]
    tests.extend([
        {"id": "all_relation_enum_values_documented", "pass": all(value in prompt for value in RELATIONS)},
        {"id": "all_role_values_documented", "pass": all(value in prompt for value in ROLES)},
        {"id": "all_speech_values_documented", "pass": all(value in prompt for value in SPEECH_ACTS)},
        {"id": "all_subject_values_documented", "pass": all(value in prompt for value in SUBJECT_REFS)},
        {"id": "all_person_values_documented", "pass": all(value in prompt for value in GRAMMATICAL_PERSONS)},
        {"id": "all_time_values_documented", "pass": all(value in prompt for value in TIME_SCOPES)},
        {"id": "all_evidence_values_documented", "pass": all(value in prompt for value in EVIDENCE_KINDS)},
        {"id": "nine_relation_arities", "pass": len(relation_arity_lines) == 9},
        {"id": "uax29_zh", "pass": len(unicode_segments("我在哪里")) == 4},
        {"id": "uax29_ja", "pass": len(unicode_segments("私はどこにいますか")) == 9},
        {"id": "runtime_gold_isolated", "pass": _runtime_ast_isolated()},
        {"id": "one_call_no_retry", "pass": inspect.getsource(run_case).count("call_once(") == 1 and "for attempt" not in inspect.getsource(run_case)},
        {"id": "schema_no_product_route", "pass": all(term not in schema_text.casefold() for term in (
            "semantic_action", "object_qualifier", "verb_resolution", "capability_id", "product_route",
        ))},
        {"id": "prompt_no_surface_example_markers", "pass": all(marker not in prompt.casefold() for marker in (
            "for example", "e.g.", "example:", "examples:", "user:", "utente:",
        ))},
    ])
    outage_oracle = {
        "0" * 64: {"signature": {**BINDING_SIGNATURE, "grammatical_person": "first"}, "expected_binding": True},
    }
    for coverage in ("ambiguous", "unsupported"):
        abstain = copy.deepcopy(frame)
        abstain["clauses"][0]["interpretation"] = coverage
        abstain["clauses"][0]["relation"] = "none"
        summary = evaluate_records([{
            "opaque_case_id": "0" * 64, "query_sha256_utf8": "1" * 64,
            "result": {"status": "evaluated", "frame": abstain, "model_calls": 1},
        }], outage_oracle)["summary"]
        tests.append({"id": f"{coverage}_not_evaluated", "pass": summary["evaluable"] == 0 and summary[coverage] == 1})
    return {
        "tests": tests,
        "summary": {
            "tests": len(tests),
            "passed": sum(bool(test["pass"]) for test in tests),
            "failed": sum(not bool(test["pass"]) for test in tests),
        },
    }


def _load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_prompt_contamination_audit_v263", AUDITOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load contamination auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contamination_audit() -> dict[str, Any]:
    auditor = _load_auditor()
    datasets, manifests = auditor.load_datasets()
    audit = auditor.audit_prompt(PROMPT_PATH, datasets)
    return {
        "audit_version": "metnos.v26.3-prompt-contamination-redacted/1.0",
        "network_calls": 0,
        "prompt": audit,
        "datasets": manifests,
        "redaction": {
            "raw_queries_persisted": False,
            "prompt_excerpts_persisted": False,
            "matched_ngrams_persisted": False,
            "opaque_fingerprints": "sha256",
        },
        "auditor_path": str(AUDITOR_PATH),
        "auditor_sha256": sha(AUDITOR_PATH),
    }


def freeze() -> dict[str, Any]:
    SCHEMA_PATH.write_text(json.dumps(live_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    PROMPT_PATH.write_text(system_prompt())
    fixture = {
        "version": VERSION, "source_controls_sha256": sha(CONTROLS_PATH),
        "cases": [{
            "opaque_case_id": sha_text(case["id"]),
            "query_sha256_utf8": sha_text(case["query"]),
            "expected_binding": bool(case["expect_binding"]),
            "signature": projected_expected_signature(case),
        } for case in controls()],
    }
    FIXTURE_PATH.write_text(json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    mutations = mutation_tests()
    MUTATIONS_PATH.write_text(json.dumps(mutations, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    audit = contamination_audit()
    CONTAMINATION_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lock = {
        "version": VERSION, "status": "frozen_pre_inference",
        "network_calls_before_freeze": 0, "native_run_required": True,
        "posthoc_credit_allowed": False, "model_calls_per_case": 1, "retries": 0,
        "runner_sha256": sha(RUNNER_PATH), "schema_sha256": sha(SCHEMA_PATH),
        "prompt_sha256": sha(PROMPT_PATH), "fixture_sha256": sha(FIXTURE_PATH),
        "mutations_sha256": sha(MUTATIONS_PATH),
        "contamination_audit_sha256": sha(CONTAMINATION_PATH),
        "controls_sha256": sha(CONTROLS_PATH),
        "prior_v262_runner_sha256": sha(Path(prior.__file__)),
        "prior_v262_freeze_sha256": sha(prior.FREEZE_PATH),
        "auditor_sha256": sha(AUDITOR_PATH),
        "segmenter_source_sha256": hashlib.sha256(inspect.getsource(prior.unicode_segments).encode()).hexdigest(),
        "validator_source_sha256": hashlib.sha256(inspect.getsource(validate_frame).encode()).hexdigest(),
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe", 92)),
        "binding_signature_sha256": canonical_hash(BINDING_SIGNATURE),
        "semantic_fields": list(SEMANTIC_FIELDS),
        "removed_redundant_fields": list(REMOVED_REDUNDANT_FIELDS),
        "evidence_contract": "tagged_union_symbolic_local_anchors_required_relation_subject_time",
        "segmentation": "Unicode UAX#29 default word boundary via regex.WORD|VERSION1",
        "action_ontology_present": False, "product_route_present": False,
        "runtime_cutover_eligible": False,
    }
    FREEZE_PATH.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    gate = {
        "version": VERSION,
        "inference_allowed": (
            mutations["summary"]["failed"] == 0
            and audit["prompt"]["maximum_objective_acceptance_credit_eligible"]
        ),
        "freeze_sha256": sha(FREEZE_PATH),
        "contamination_audit_sha256": sha(CONTAMINATION_PATH),
        "requirements": {
            "mutation_failures": mutations["summary"]["failed"],
            "surface_or_mixed_lines": audit["prompt"]["line_inventory"]["surface_or_mixed_line_count"],
            "whole_query_matches": {
                name: result["whole_query_occurs_in_prompt"]
                for name, result in audit["prompt"]["dataset_overlap"].items()
            },
            "overlap_n_ge_3": {
                name: result["cases_with_maximal_overlap_n_ge_3"]
                for name, result in audit["prompt"]["dataset_overlap"].items()
            },
        },
        "native_run_required": True,
        "posthoc_credit_allowed": False,
    }
    GATE_PATH.write_text(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return lock


def verify_freeze() -> None:
    lock = json.loads(FREEZE_PATH.read_text())
    paths = {
        "runner_sha256": RUNNER_PATH, "schema_sha256": SCHEMA_PATH,
        "prompt_sha256": PROMPT_PATH, "fixture_sha256": FIXTURE_PATH,
        "mutations_sha256": MUTATIONS_PATH,
        "contamination_audit_sha256": CONTAMINATION_PATH,
        "controls_sha256": CONTROLS_PATH,
        "prior_v262_runner_sha256": Path(prior.__file__),
        "prior_v262_freeze_sha256": prior.FREEZE_PATH,
        "auditor_sha256": AUDITOR_PATH,
    }
    changed = {
        key: {"locked": lock.get(key), "actual": sha(path)}
        for key, path in paths.items() if lock.get(key) != sha(path)
    }
    derived = {
        "segmenter_source_sha256": hashlib.sha256(inspect.getsource(prior.unicode_segments).encode()).hexdigest(),
        "validator_source_sha256": hashlib.sha256(inspect.getsource(validate_frame).encode()).hexdigest(),
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe", 92)),
        "binding_signature_sha256": canonical_hash(BINDING_SIGNATURE),
    }
    for key, actual in derived.items():
        if lock.get(key) != actual:
            changed[key] = {"locked": lock.get(key), "actual": actual}
    if changed:
        raise RuntimeError(f"V26.3 freeze changed: {changed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.freeze:
        print(json.dumps(freeze(), sort_keys=True))
    if args.self_test:
        result = mutation_tests()
        print(json.dumps(result["summary"], sort_keys=True))
        if result["summary"]["failed"]:
            return 1
    if args.controls:
        result = run_controls(args.start, args.end)
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
