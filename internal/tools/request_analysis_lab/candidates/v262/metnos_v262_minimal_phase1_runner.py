#!/usr/bin/env python3
"""V26.2: native, minimal, proof-carrying phase-1 analyzer.

This candidate is frozen separately from the superseded complete-tuple V26.1
prototype.  It removes dimensions that the V25.3 first-attempt diagnostic
showed to be redundant for relation grounding, uses a tagged evidence union,
and performs exactly one model call.  No post-hoc V25.3 projection receives
acceptance credit.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
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
import metnos_v253_phase1_relation_runner as base  # noqa: E402


VERSION = "metnos.v26.2-minimal-phase1/1.0"
RUNNER_PATH = Path(__file__)
SCHEMA_PATH = Path("/tmp/metnos_v262_minimal_phase1.schema.json")
PROMPT_PATH = Path("/tmp/metnos_v262_minimal_phase1.prompt.txt")
FIXTURE_PATH = Path("/tmp/metnos_v262_minimal_phase1_controls_frozen.json")
MUTATIONS_PATH = Path("/tmp/metnos_v262_minimal_phase1_mutations_frozen.json")
FREEZE_PATH = Path("/tmp/metnos_v262_minimal_phase1.freeze.json")
GATE_PATH = Path("/tmp/metnos_v262_minimal_phase1_preinference_gate.json")

CONTROLS_PATH = base.CONTROLS_PATH
v25 = base.v25

ROLES = ["request", "condition", "description", "quote"]
INTERPRETATIONS = ["supported", "ambiguous", "unsupported"]
SPEECH_ACTS = [
    "assertion", "open_question", "polar_question", "imperative",
    "condition", "quote", "description",
]
RELATIONS = [
    "none", "spatial.located_at", "identity.same_as",
    "filesystem.located_at", "runtime.host_of", "spatial.near",
    "acl.share", "movement.destination", "workflow.position",
    "document.position",
]
SUBJECT_REFS = [
    "none", "current_actor", "explicit_entity", "unknown_entity",
    "deictic_context", "current_actor_location_operand", "quoted_actor",
]
GRAMMATICAL_PERSONS = ["none", "first", "second", "third", "ambiguous"]
TIME_SCOPES = ["none", "current", "historical", "contextual"]
EVIDENCE_CLAIMS = ["relation", "subject", "time"]
EVIDENCE_KINDS = [
    "none", "explicit_segment", "predicate_morphology", "clause_semantics",
    "speaker_context", "discourse_context", "tense_morphology",
]

MINIMAL_SIGNATURE = {
    "role": "request",
    "interpretation": "supported",
    "speech_act": "open_question",
    "relation": "spatial.located_at",
    "subject_ref": "current_actor",
    "grammatical_person": "first",
    "time_scope": "current",
}
SIGNATURE_FIELDS = tuple(MINIMAL_SIGNATURE)
REMOVED_REDUNDANT_FIELDS = (
    "answer_mode", "focus_role", "focus_type", "subject_type", "deixis_center",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def unicode_segments(text: str) -> list[dict[str, Any]]:
    return base.unicode_segments(text)


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


def evidence_schema() -> dict[str, Any]:
    variants = [
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
        {
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "predicate_morphology"},
                "predicate_segment_id": {"type": "integer", "minimum": 1},
            },
            "required": ["kind", "predicate_segment_id"],
        },
        {
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "clause_semantics"},
                "clause_start_segment_id": {"type": "integer", "minimum": 1},
                "clause_end_segment_id": {"type": "integer", "minimum": 1},
            },
            "required": ["kind", "clause_start_segment_id", "clause_end_segment_id"],
        },
        {
            "type": "object", "additionalProperties": False,
            "properties": {"kind": {"const": "speaker_context"}},
            "required": ["kind"],
        },
        {
            "type": "object", "additionalProperties": False,
            "properties": {"kind": {"const": "discourse_context"}},
            "required": ["kind"],
        },
        {
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "tense_morphology"},
                "predicate_segment_id": {"type": "integer", "minimum": 1},
            },
            "required": ["kind", "predicate_segment_id"],
        },
    ]
    return {"oneOf": variants}


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
        "properties": {"clauses": {"type": "array", "minItems": 1, "items": clause}},
        "required": ["clauses"],
    }


def system_prompt() -> str:
    return """You are a phase-1 structural semantic analyzer.
Return only JSON conforming to the supplied schema.
Analyze clause structure and semantic relations over the supplied Unicode segments.
Do not choose product actions, capabilities, routes, tools, objects, or execution plans.

Emit one record per clause in source order.
Clause and predicate identifiers must refer to the supplied segments.
role classifies the clause's discourse role.
interpretation is supported when the semantic tuple is determined by evidence, ambiguous when multiple tuples remain, and unsupported when the tuple cannot be grounded.
speech_act classifies the clause-level communicative act.
relation names the semantic relation expressed by the clause; use none only when no listed relation is expressed.
subject_ref identifies the relation subject independently of any product object taxonomy.
grammatical_person records the grammatical person supported by clause structure.
time_scope records temporal anchoring of the relation.

evidence is an object with required relation, subject, and time claims; each value is a tagged union.
explicit_segment points to a source span that directly realizes a claim.
predicate_morphology and tense_morphology point to the predicate segment carrying grammatical evidence.
clause_semantics points to the exact clause span supporting a compositional inference.
speaker_context and discourse_context are zero-span contextual evidence.
none states that the corresponding claim has no support; never invent a span.
Evidence offsets are segment identifiers, not character offsets.

current_actor denotes the discourse participant who produced the original request.
current denotes temporal anchoring to the active request context.
spatial.located_at relates a subject to its spatial position.
open_question denotes a request whose answer supplies an unknown value.
Use semantic and grammatical evidence from the input itself; do not rely on language-specific word lists.
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
    for index, clause in enumerate(frame["clauses"]):
        path = f"/clauses/{index}"
        if clause["clause_id"] <= previous_clause_id:
            errors.append(_error("clause_order", path + "/clause_id", "clause ids must increase"))
        previous_clause_id = clause["clause_id"]
        start = clause["clause_start_segment_id"]
        end = clause["clause_end_segment_id"]
        predicate = clause["predicate_segment_id"]
        if start not in segment_ids or end not in segment_ids or start > end:
            errors.append(_error("clause_span", path, "clause span is outside supplied segments"))
        if predicate not in segment_ids or not (start <= predicate <= end):
            errors.append(_error("predicate_span", path + "/predicate_segment_id", "predicate must be inside clause"))
        for evidence_claim, evidence in clause["evidence"].items():
            evidence_path = f"{path}/evidence/{evidence_claim}"
            kind = evidence["kind"]
            if kind == "explicit_segment":
                ev_start = evidence["start_segment_id"]
                ev_end = evidence["end_segment_id"]
                if ev_start not in segment_ids or ev_end not in segment_ids or not (start <= ev_start <= ev_end <= end):
                    errors.append(_error("evidence_span", evidence_path, "explicit evidence must be inside clause"))
            elif kind in {"predicate_morphology", "tense_morphology"}:
                if evidence["predicate_segment_id"] != predicate:
                    errors.append(_error("evidence_predicate", evidence_path, "morphology evidence must identify the predicate"))
            elif kind == "clause_semantics":
                if (evidence["clause_start_segment_id"], evidence["clause_end_segment_id"]) != (start, end):
                    errors.append(_error("evidence_clause", evidence_path, "clause evidence must identify this clause"))
    return {"valid": not errors, "errors": errors}


def evidence_map(clause: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return clause.get("evidence", {})


def has_complete_evidence(clause: dict[str, Any]) -> bool:
    evidence = evidence_map(clause)
    return all(evidence.get(claim, {}).get("kind") not in {None, "none"} for claim in EVIDENCE_CLAIMS)


def matches_minimal_signature(clause: dict[str, Any]) -> bool:
    return all(clause.get(key) == value for key, value in MINIMAL_SIGNATURE.items()) and has_complete_evidence(clause)


def request_body(query: str, seed: int = 92) -> dict[str, Any]:
    payload = {"original_request": query, "segments": unicode_segments(query)}
    return {
        "model": "local", "temperature": 0, "seed": seed, "max_tokens": 1200,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "phase1_minimal_relation_v262", "schema": live_schema(), "strict": True,
        }},
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def call_once(query: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = v25.bench.tier_endpoint(v25.bench.tier_for("intent.extract")).rstrip("/")
    request = urllib.request.Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(request_body(query, seed), ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw), {
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error": "",
        }
    except Exception as exc:
        return {}, {
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error": "transport_or_json", "error_type": type(exc).__name__,
        }


def verify_gate() -> None:
    gate = json.loads(GATE_PATH.read_text())
    if not gate.get("inference_allowed"):
        raise RuntimeError("V26.2 minimal native inference blocked by audit gate")
    if gate.get("freeze_sha256") != sha(FREEZE_PATH):
        raise RuntimeError("V26.2 minimal freeze changed after audit")
    audit_path = Path(gate["audit_path"])
    if gate.get("audit_sha256") != sha(audit_path):
        raise RuntimeError("V26.2 minimal contamination audit changed")


def run_case(query: str) -> dict[str, Any]:
    verify_freeze(); verify_gate()
    segments = unicode_segments(query)
    frame, transport = call_once(query, 92)
    validation = (
        validate_frame(frame, segments) if not transport["error"]
        else {"valid": False, "errors": [_error(transport["error"], "", transport.get("error_type", "transport"))]}
    )
    base_result = {
        "segment_count": len(segments),
        "segment_layout_sha256": canonical_hash([
            {key: segment[key] for key in ("id", "start_char", "end_char")}
            for segment in segments
        ]),
        "model_calls": 1, "latency_ms": transport["latency_ms"],
        "transport_error": transport["error"],
    }
    if validation["valid"]:
        return {**base_result, "status": "evaluated", "frame": frame, "validation": {"valid": True, "error_codes": []}}
    return {
        **base_result, "status": "not_evaluated", "validation": {
            "valid": False,
            "error_codes": [error["code"] for error in validation["errors"]],
        },
    }


def controls() -> list[dict[str, Any]]:
    return base.controls()


def projected_expected_signature(case: dict[str, Any]) -> dict[str, Any]:
    expected = base.expected_signature(case)
    expected["interpretation"] = "supported"
    return {field: expected[field] for field in SIGNATURE_FIELDS}


def relevant_clause(frame: dict[str, Any]) -> dict[str, Any] | None:
    active = [clause for clause in frame.get("clauses", []) if clause.get("relation") != "none"]
    if active:
        return active[0]
    clauses = frame.get("clauses", [])
    return clauses[0] if clauses else None


def evaluate_records(records: list[dict[str, Any]], oracle: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for record in records:
        result = record["result"]
        expected = oracle[record["opaque_case_id"]]
        frame = result.get("frame") or {}
        ambiguous = any(clause.get("interpretation") == "ambiguous" for clause in frame.get("clauses", []))
        evaluable = result.get("status") == "evaluated" and not ambiguous
        clause = relevant_clause(frame) if evaluable else None
        signature_exact = (
            clause is not None
            and all(clause.get(field) == value for field, value in expected["signature"].items())
        ) if evaluable else None
        predicted = any(matches_minimal_signature(item) for item in frame.get("clauses", [])) if evaluable else None
        binding_ok = predicted == expected["expected_binding"] if evaluable else None
        rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "query_sha256_utf8": record["query_sha256_utf8"],
            "evaluable": evaluable, "ambiguous": ambiguous,
            "signature_exact": signature_exact, "binding_ok": binding_ok,
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
            "ambiguous": sum(row["ambiguous"] for row in rows),
            "signature_exact": sum(bool(row["signature_exact"]) for row in evaluated),
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
    records = []
    for case in selected:
        records.append({
            "opaque_case_id": sha_text(case["id"]),
            "query_sha256_utf8": sha_text(case["query"]),
            "result": run_case(case["query"]),
        })
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
        node = functions[name]
        symbols = (
            {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
            | {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
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
        **MINIMAL_SIGNATURE,
        "evidence": {
            "relation": {"kind": "explicit_segment", "start_segment_id": 1, "end_segment_id": 1},
            "subject": {"kind": "predicate_morphology", "predicate_segment_id": 2},
            "time": {"kind": "tense_morphology", "predicate_segment_id": 2},
        },
    }]}
    return frame, segments


def mutation_tests() -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    frame, segments = canonical_frame()
    tests.append({"id": "canonical_valid", "pass": validate_frame(frame, segments)["valid"]})
    tests.append({"id": "canonical_matches", "pass": matches_minimal_signature(frame["clauses"][0])})
    schema_text = json.dumps(live_schema(), sort_keys=True)
    for removed in REMOVED_REDUNDANT_FIELDS:
        tests.append({"id": f"removed_{removed}", "pass": removed not in schema_text})
    for field in SIGNATURE_FIELDS:
        changed = copy.deepcopy(frame)
        value = changed["clauses"][0][field]
        alternatives = live_schema()["properties"]["clauses"]["items"]["properties"][field]["enum"]
        changed["clauses"][0][field] = next(item for item in alternatives if item != value)
        tests.append({"id": f"binding_mutation_{field}", "pass": not matches_minimal_signature(changed["clauses"][0])})
    for claim in EVIDENCE_CLAIMS:
        changed = copy.deepcopy(frame)
        changed["clauses"][0]["evidence"][claim] = {"kind": "none"}
        tests.append({"id": f"evidence_none_{claim}", "pass": validate_frame(changed, segments)["valid"] and not matches_minimal_signature(changed["clauses"][0])})
    missing = copy.deepcopy(frame); missing["clauses"][0]["evidence"].pop("time")
    tests.append({"id": "missing_evidence_claim_invalid", "pass": not validate_frame(missing, segments)["valid"]})
    unknown = copy.deepcopy(frame); unknown["clauses"][0]["evidence"]["unknown"] = {"kind": "none"}
    tests.append({"id": "unknown_evidence_claim_invalid", "pass": not validate_frame(unknown, segments)["valid"]})
    leaked = copy.deepcopy(frame); leaked["clauses"][0]["evidence"]["relation"]["free_text"] = "x"
    tests.append({"id": "evidence_free_text_invalid", "pass": not validate_frame(leaked, segments)["valid"]})
    tests.extend([
        {"id": "uax29_zh", "pass": len(unicode_segments("我在哪里")) == 4},
        {"id": "uax29_ja", "pass": len(unicode_segments("私はどこにいますか")) == 9},
        {"id": "runtime_gold_isolated", "pass": _runtime_ast_isolated()},
        {"id": "one_call_no_retry", "pass": inspect.getsource(run_case).count("call_once(") == 1 and "for attempt" not in inspect.getsource(run_case)},
        {"id": "schema_no_product_route", "pass": all(term not in schema_text.casefold() for term in (
            "semantic_action", "object_qualifier", "verb_resolution", "capability_id", "product_route",
        ))},
    ])
    oracle = {
        "0" * 64: {"signature": MINIMAL_SIGNATURE, "expected_binding": True},
        "2" * 64: {"signature": MINIMAL_SIGNATURE, "expected_binding": False},
    }
    outage = evaluate_records([
        {"opaque_case_id": "0" * 64, "query_sha256_utf8": "1" * 64, "result": {"status": "not_evaluated", "model_calls": 1}},
        {"opaque_case_id": "2" * 64, "query_sha256_utf8": "3" * 64, "result": {"status": "not_evaluated", "model_calls": 1}},
    ], oracle)["summary"]
    tests.append({"id": "failclosed_not_evaluated", "pass": outage["evaluable"] == 0 and outage["not_evaluated"] == 2})
    ambiguous = copy.deepcopy(frame); ambiguous["clauses"][0]["interpretation"] = "ambiguous"
    ambiguity = evaluate_records([
        {"opaque_case_id": "0" * 64, "query_sha256_utf8": "1" * 64, "result": {"status": "evaluated", "frame": ambiguous, "model_calls": 1}},
    ], {"0" * 64: {"signature": MINIMAL_SIGNATURE, "expected_binding": True}})["summary"]
    tests.append({"id": "ambiguity_not_evaluated", "pass": ambiguity["evaluable"] == 0 and ambiguity["ambiguous"] == 1})
    return {
        "tests": tests,
        "summary": {
            "tests": len(tests), "passed": sum(bool(test["pass"]) for test in tests),
            "failed": sum(not bool(test["pass"]) for test in tests),
        },
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
    lock = {
        "version": VERSION, "status": "frozen_pre_inference",
        "network_calls_before_freeze": 0, "native_run_required": True,
        "posthoc_credit_allowed": False, "model_calls_per_case": 1, "retries": 0,
        "runner_sha256": sha(RUNNER_PATH), "schema_sha256": sha(SCHEMA_PATH),
        "prompt_sha256": sha(PROMPT_PATH), "fixture_sha256": sha(FIXTURE_PATH),
        "mutations_sha256": sha(MUTATIONS_PATH), "controls_sha256": sha(CONTROLS_PATH),
        "base_v253_runner_sha256": sha(Path(base.__file__)),
        "base_v253_freeze_sha256": sha(base.FREEZE_PATH),
        "segmenter_source_sha256": hashlib.sha256(inspect.getsource(base.unicode_segments).encode()).hexdigest(),
        "validator_source_sha256": hashlib.sha256(inspect.getsource(validate_frame).encode()).hexdigest(),
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe", 92)),
        "minimal_signature_sha256": canonical_hash(MINIMAL_SIGNATURE),
        "removed_redundant_fields": list(REMOVED_REDUNDANT_FIELDS),
        "evidence_contract": "tagged_union_object_required_relation_subject_time",
        "segmentation": "Unicode UAX#29 default word boundary via regex.WORD|VERSION1",
        "action_ontology_present": False, "product_route_present": False,
        "runtime_cutover_eligible": False,
    }
    FREEZE_PATH.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return lock


def verify_freeze() -> None:
    lock = json.loads(FREEZE_PATH.read_text())
    paths = {
        "runner_sha256": RUNNER_PATH, "schema_sha256": SCHEMA_PATH,
        "prompt_sha256": PROMPT_PATH, "fixture_sha256": FIXTURE_PATH,
        "mutations_sha256": MUTATIONS_PATH, "controls_sha256": CONTROLS_PATH,
        "base_v253_runner_sha256": Path(base.__file__),
        "base_v253_freeze_sha256": base.FREEZE_PATH,
    }
    changed = {
        key: {"locked": lock.get(key), "actual": sha(path)}
        for key, path in paths.items() if lock.get(key) != sha(path)
    }
    derived = {
        "segmenter_source_sha256": hashlib.sha256(inspect.getsource(base.unicode_segments).encode()).hexdigest(),
        "validator_source_sha256": hashlib.sha256(inspect.getsource(validate_frame).encode()).hexdigest(),
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe", 92)),
        "minimal_signature_sha256": canonical_hash(MINIMAL_SIGNATURE),
    }
    for key, actual in derived.items():
        if lock.get(key) != actual:
            changed[key] = {"locked": lock.get(key), "actual": actual}
    if changed:
        raise RuntimeError(f"V26.2 minimal freeze changed: {changed}")


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
        result = mutation_tests(); print(json.dumps(result["summary"], sort_keys=True))
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
