#!/usr/bin/env python3
"""V25.3 phase-1-only relation analysis.

No route, action ontology, product patient, capability ID, or source-language
term list is visible to this analyzer.  It emits a flat proof-carrying
relation/question contract over Unicode word-boundary segments.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import time
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema
import regex

import sys
sys.path.insert(0, "/tmp")
import metnos_v25_live_runner as v25  # only endpoint/tier configuration


VERSION = "metnos.v25.3-phase1-relation/1.0"
RUNNER_PATH = Path(__file__)
SCHEMA_PATH = Path("/tmp/metnos_v253_phase1_relation.schema.json")
PROMPT_PATH = Path("/tmp/metnos_v253_phase1_relation.prompt.txt")
CONTROLS_PATH = Path("/tmp/metnos_question_focus_negative_controls_v1.json")
FIXTURE_PATH = Path("/tmp/metnos_v253_phase1_relation_controls_frozen.json")
MUTATIONS_PATH = Path("/tmp/metnos_v253_phase1_relation_mutations_frozen.json")
FREEZE_PATH = Path("/tmp/metnos_v253_phase1_relation.freeze.json")


ROLES = ["request", "condition", "description", "quote"]
SPEECH_ACTS = [
    "assertion", "open_question", "polar_question", "imperative",
    "condition", "quote", "description",
]
ANSWER_MODES = ["none", "open_variable", "boolean", "operation_result"]
RELATIONS = [
    "none", "spatial.located_at", "identity.same_as",
    "filesystem.located_at", "runtime.host_of", "spatial.near",
    "acl.share", "movement.destination", "workflow.position",
    "document.position",
]
FOCUS_ROLES = [
    "none", "location", "entity", "identity", "truth_value", "host",
    "entities", "delivery_result", "destination", "progress",
]
VALUE_TYPES = [
    "none", "spatial_position", "person", "place_identity", "identity",
    "boolean", "host", "entity_collection", "permission_result",
    "resource_locator", "workflow_progress",
]
SUBJECT_REFS = [
    "none", "current_actor", "explicit_entity", "unknown_entity",
    "deictic_context", "current_actor_location_operand", "quoted_actor",
]
SUBJECT_TYPES = [
    "none", "actor", "person", "place", "file", "process", "collection",
    "resource", "spatial_position",
]
GRAMMATICAL_PERSONS = ["none", "first", "second", "third", "ambiguous"]
DEIXIS_CENTERS = [
    "none", "speaker", "addressee", "context", "explicit_entity",
    "quoted_speaker",
]
TIME_SCOPES = ["none", "current", "historical", "contextual"]
EVIDENCE_KINDS = [
    "none", "explicit_segment", "predicate_morphology", "clause_semantics",
    "speaker_context", "discourse_context", "tense_morphology",
]
INTERPRETATION = ["supported", "ambiguous", "unsupported"]


def unicode_segments(text: str) -> list[dict[str, Any]]:
    """UAX #29 default word boundaries; no language or script table."""
    parts = regex.split(r"\b", text, flags=regex.WORD | regex.VERSION1)
    segments: list[dict[str, Any]] = []
    cursor = 0
    for part in parts:
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            raise ValueError("segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        segments.append({
            "id": len(segments) + 1,
            "text": part,
            "start_char": start,
            "end_char": end,
        })
    return segments


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


def live_schema() -> dict[str, Any]:
    props: dict[str, Any] = {
        "clause_id": {"type": "integer", "minimum": 1},
        "clause_start_segment_id": {"type": "integer", "minimum": 1},
        "clause_end_segment_id": {"type": "integer", "minimum": 1},
        "predicate_segment_id": {"type": "integer", "minimum": 1},
        "role": _enum(ROLES),
        "interpretation": _enum(INTERPRETATION),
        "speech_act": _enum(SPEECH_ACTS),
        "answer_mode": _enum(ANSWER_MODES),
        "relation": _enum(RELATIONS),
        "focus_role": _enum(FOCUS_ROLES),
        "focus_type": _enum(VALUE_TYPES),
        "focus_evidence_kind": _enum(EVIDENCE_KINDS),
        "focus_start_segment_id": {"type": "integer", "minimum": 0},
        "focus_end_segment_id": {"type": "integer", "minimum": 0},
        "subject_ref": _enum(SUBJECT_REFS),
        "subject_type": _enum(SUBJECT_TYPES),
        "grammatical_person": _enum(GRAMMATICAL_PERSONS),
        "deixis_center": _enum(DEIXIS_CENTERS),
        "subject_evidence_kind": _enum(EVIDENCE_KINDS),
        "subject_start_segment_id": {"type": "integer", "minimum": 0},
        "subject_end_segment_id": {"type": "integer", "minimum": 0},
        "time_scope": _enum(TIME_SCOPES),
        "time_evidence_kind": _enum(EVIDENCE_KINDS),
        "time_start_segment_id": {"type": "integer", "minimum": 0},
        "time_end_segment_id": {"type": "integer", "minimum": 0},
    }
    clause = {
        "type": "object", "additionalProperties": False,
        "properties": props, "required": list(props),
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"clauses": {"type": "array", "items": clause}},
        "required": ["clauses"],
    }


PROMPT = """Analyze the exact user request in any language as phase-1 semantics.
The input provides Unicode word-boundary segments with stable ids and character
offsets. Return one clause record for every predicate/relation that could be
mistaken for a requested operation. Preserve source order. Output JSON only.

This phase has no product route, action/tool ontology, capability binding, or
rewritten query. Determine syntax and denotation directly from the untouched
request. Enum values are technical concepts, never words to match in source.

Clause role:
- request: the user asks the assistant to produce a result or effect;
- condition: a state controlling a request, not independently requested;
- description: a state, relative clause, history, or assertion;
- quote: predicate-like literal/quoted content.

Speech act and answer:
- open_question asks for one unknown variable; answer_mode=open_variable;
- polar_question asks whether a proposition is true; answer_mode=boolean;
- imperative requests an effect/result; answer_mode=operation_result when the
  relation's output is relevant, otherwise none;
- assertion, condition, description, and quote have answer_mode=none.
Do not turn a declarative proposition, quoted question, condition, or relative
clause into an open question.

Relation registry:
- spatial.located_at(entity, spatial_position, time): physical/geographic
  placement in the world;
- identity.same_as(entity, identity): identity, class, or name of an entity;
- filesystem.located_at(file, resource_locator): storage path/container of a
  digital filesystem resource;
- runtime.host_of(process, host): machine/runtime environment of a process;
- spatial.near(collection, spatial_position): entities selected by proximity;
- acl.share(resource, grantee): continuing permission/access grant;
- movement.destination(resource, resource_locator): destination of movement;
- workflow.position(actor, workflow_progress): state/progress in a workflow;
- document.position(actor, resource_locator): position within a document;
- none: no relation represented by this predicate.
Namespace follows denotation, not the lexical predicate's dictionary sense.

Focus is the answer/output variable, not every argument that is mentioned.
Set its semantic role and value type. A location used only as an operand for a
different result is not the focus. A polar question focuses truth_value:boolean.
Conditions and quotes may preserve a relation while having no requested focus.

The grammatical subject is separate from the focus. Record:
- subject_ref: current turn actor, explicit/unknown entity, deictic context,
  current-actor-location used only as an operand, quoted actor, or none;
- subject_type;
- grammatical_person independently of identity;
- deixis_center independently of person.
Current_actor requires first-person morphology or speaker-context evidence and
speaker deixis. An explicit third party/resource is explicit_entity with its
own span and must never become current_actor. A context-deictic place is not the
speaker. Quoted first person belongs to quoted_actor, not current_actor.

Time is independent. current means now/latest; historical means a past
observation; contextual means a relative/discourse time. An explicit temporal
argument must be grounded and must not be replaced by speaker-context current.

Evidence is typed:
- explicit_segment uses a positive inclusive segment range;
- predicate_morphology and tense_morphology refer to predicate_segment_id and
  therefore use 0,0;
- clause_semantics refers to the clause span and uses 0,0;
- speaker_context/discourse_context use 0,0;
- none uses 0,0.
Focus, subject, and time evidence are independent and may overlap. For an open
answer variable, focus evidence cannot be none. For an explicit entity,
subject evidence must be explicit_segment. Historical time requires explicit
segment or tense morphology evidence.

interpretation=supported only when the complete tuple and evidence are clear.
Use ambiguous when multiple typed analyses remain plausible, unsupported when
the relation registry cannot express the denotation. Ambiguous/unsupported are
valid structured outcomes and must not be forced into a supported relation.

Before output, check clause/predicate segment ranges, speech-act/answer-mode
agreement, subject/person/deixis agreement, time/evidence agreement, and that
no quoted/conditional/descriptive predicate is marked as a request.
"""


def system_prompt() -> str:
    return PROMPT


def _span_valid(kind: str, start: Any, end: Any, count: int) -> bool:
    if kind == "explicit_segment":
        return isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= count
    return start == 0 and end == 0


def validate_frame(frame: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    for issue in sorted(jsonschema.Draft202012Validator(live_schema()).iter_errors(frame), key=lambda e: list(e.path)):
        errors.append({"code": "schema", "path": "/".join(str(x) for x in issue.absolute_path),
                       "message": issue.message, "retryable": True})
    if errors:
        return {"valid": False, "errors": errors}
    count = len(segments)
    clauses = frame["clauses"]
    if [c["clause_id"] for c in clauses] != list(range(1, len(clauses)+1)):
        errors.append({"code": "clause_ids", "path": "clauses", "message": "clause ids must be 1..N", "retryable": True})
    for i, clause in enumerate(clauses):
        path = f"clauses/{i}"
        cs, ce, pred = clause["clause_start_segment_id"], clause["clause_end_segment_id"], clause["predicate_segment_id"]
        if not (1 <= cs <= pred <= ce <= count):
            errors.append({"code": "clause_span", "path": path,
                           "message": "clause/predicate segment ids are out of range", "retryable": True})
        for role in ("focus", "subject", "time"):
            if not _span_valid(clause[f"{role}_evidence_kind"], clause[f"{role}_start_segment_id"],
                               clause[f"{role}_end_segment_id"], count):
                errors.append({"code": f"{role}_evidence", "path": path,
                               "message": f"{role} evidence kind/span mismatch", "retryable": True})

        speech, answer = clause["speech_act"], clause["answer_mode"]
        expected_answers = {
            "open_question": {"open_variable"}, "polar_question": {"boolean"},
            "imperative": {"none", "operation_result"}, "assertion": {"none"},
            "condition": {"none"}, "quote": {"none"}, "description": {"none"},
        }
        if answer not in expected_answers[speech]:
            errors.append({"code": "speech_answer", "path": path,
                           "message": "speech act and answer mode disagree; revise phase-1 claim only", "retryable": True})
        if clause["role"] != "request" and speech in {"open_question", "polar_question", "imperative"}:
            errors.append({"code": "role_speech", "path": path,
                           "message": "non-request role cannot have a requesting speech act", "retryable": True})
        if speech == "condition" and clause["role"] != "condition":
            errors.append({"code": "condition_role", "path": path, "message": "condition speech requires condition role", "retryable": True})
        if speech == "quote" and clause["role"] != "quote":
            errors.append({"code": "quote_role", "path": path, "message": "quote speech requires quote role", "retryable": True})

        if clause["relation"] == "none":
            if any(clause[k] != "none" for k in ("focus_role", "focus_type", "subject_ref", "subject_type", "time_scope")):
                errors.append({"code": "none_relation_not_neutral", "path": path,
                               "message": "relation none requires neutral relation arguments", "retryable": True})
        if (clause["focus_role"] == "none") != (clause["focus_type"] == "none"):
            errors.append({"code": "focus_pair", "path": path, "message": "focus role/type must both be none or both active", "retryable": True})
        if answer in {"open_variable", "boolean"} and clause["focus_evidence_kind"] == "none":
            errors.append({"code": "answer_focus_evidence", "path": path,
                           "message": "answer variable requires focus evidence", "retryable": True})

        ref, typ = clause["subject_ref"], clause["subject_type"]
        person, deixis, ev = clause["grammatical_person"], clause["deixis_center"], clause["subject_evidence_kind"]
        if (ref == "none") != (typ == "none"):
            errors.append({"code": "subject_pair", "path": path, "message": "subject ref/type must both be none or active", "retryable": True})
        if ref == "current_actor" and not (
            typ == "actor" and person == "first" and deixis == "speaker"
            and ev in {"explicit_segment", "predicate_morphology", "speaker_context"}
        ):
            errors.append({"code": "current_actor_proof", "path": path,
                           "message": "revise subject claim: current_actor requires first-person + speaker deixis + evidence", "retryable": True})
        if ref == "explicit_entity" and not (deixis == "explicit_entity" and ev == "explicit_segment"):
            errors.append({"code": "explicit_subject_proof", "path": path,
                           "message": "explicit entity requires its own segment evidence", "retryable": True})
        if ref == "quoted_actor" and deixis != "quoted_speaker":
            errors.append({"code": "quoted_subject", "path": path, "message": "quoted actor requires quoted-speaker deixis", "retryable": True})
        if ref == "deictic_context" and deixis != "context":
            errors.append({"code": "context_subject", "path": path, "message": "deictic context requires context deixis", "retryable": True})

        time_scope, time_ev = clause["time_scope"], clause["time_evidence_kind"]
        if time_scope == "none" and time_ev != "none":
            errors.append({"code": "time_none_evidence", "path": path, "message": "time none cannot have evidence", "retryable": True})
        if time_scope == "historical" and time_ev not in {"explicit_segment", "tense_morphology"}:
            errors.append({"code": "historical_time_proof", "path": path,
                           "message": "historical time requires explicit or tense evidence", "retryable": True})
        if time_scope == "current" and time_ev not in {"explicit_segment", "tense_morphology", "speaker_context", "clause_semantics"}:
            errors.append({"code": "current_time_proof", "path": path, "message": "current time requires typed evidence", "retryable": True})

        if clause["interpretation"] == "supported":
            namespace_types = {
                "filesystem.located_at": {"file"}, "runtime.host_of": {"process"},
                "workflow.position": {"actor"}, "document.position": {"actor"},
            }
            allowed = namespace_types.get(clause["relation"])
            if allowed and typ not in allowed:
                errors.append({"code": "relation_subject_type", "path": path,
                               "message": "relation namespace conflicts with subject type; revise phase-1 claim", "retryable": True})
    return {"valid": not errors, "errors": errors}


CURRENT_LOCATION = {
    "role": "request", "interpretation": "supported",
    "speech_act": "open_question", "answer_mode": "open_variable",
    "relation": "spatial.located_at", "focus_role": "location",
    "focus_type": "spatial_position", "subject_ref": "current_actor",
    "subject_type": "actor", "grammatical_person": "first",
    "deixis_center": "speaker", "time_scope": "current",
}


def is_current_location(clause: dict[str, Any]) -> bool:
    return all(clause.get(k) == value for k, value in CURRENT_LOCATION.items()) and all(
        clause.get(f"{role}_evidence_kind") != "none" for role in ("focus", "subject", "time")
    )


def expected_signature(case: dict[str, Any]) -> dict[str, Any]:
    """Frozen oracle translation; only benchmark labels, never query text."""
    old = case["tuple"]
    speech0, rel0, focus0, subject0, time0 = old
    speech = {
        "open_question": "open_question", "polar_question": "polar_question",
        "imperative": "imperative", "condition": "condition", "quote": "quote",
    }[speech0]
    role = "condition" if speech == "condition" else "quote" if speech == "quote" else "request"
    answer = "open_variable" if speech == "open_question" else "boolean" if speech == "polar_question" else "operation_result" if speech == "imperative" else "none"
    relation = {
        "located_at": "spatial.located_at", "identity": "identity.same_as",
        "filesystem_location": "filesystem.located_at", "runtime_host": "runtime.host_of",
        "proximity_search": "spatial.near", "share": "acl.share", "move": "movement.destination",
        "workflow_state": "workflow.position", "document_position": "document.position",
    }[rel0]
    focus_map = {
        "location": ("location", "spatial_position"), "identity": ("identity", "identity"),
        "entity": ("entity", "person"), "place_identity": ("identity", "place_identity"),
        "host": ("host", "host"), "entities": ("entities", "entity_collection"),
        "truth_value": ("truth_value", "boolean"), "delivery_result": ("delivery_result", "permission_result"),
        "none": ("none", "none"), "destination": ("destination", "resource_locator"),
        "progress": ("progress", "workflow_progress"),
    }
    focus_role, focus_type = focus_map[focus0]
    subject_map = {
        "current_actor": ("current_actor", "actor", "first", "speaker"),
        "explicit_person": ("explicit_entity", "person", "third", "explicit_entity"),
        "unknown_person": ("unknown_entity", "person", "third", "context"),
        "deictic_place": ("deictic_context", "place", "third", "context"),
        "explicit_file": ("explicit_entity", "file", "third", "explicit_entity"),
        "explicit_process": ("explicit_entity", "process", "third", "explicit_entity"),
        "restaurants": ("explicit_entity", "collection", "none", "explicit_entity"),
        "current_actor_location_operand": ("current_actor_location_operand", "spatial_position", "first", "speaker"),
        "quoted_actor": ("quoted_actor", "actor", "first", "quoted_speaker"),
        "explicit_files": ("explicit_entity", "collection", "none", "explicit_entity"),
    }
    subject_ref, subject_type, person, deixis = subject_map[subject0]
    time_scope = "current" if time0 in {"current", "current_context", "explicit_place"} else "historical" if time0 == "historical" else "contextual"
    return {"role": role, "speech_act": speech, "answer_mode": answer, "relation": relation,
            "focus_role": focus_role, "focus_type": focus_type, "subject_ref": subject_ref,
            "subject_type": subject_type, "grammatical_person": person,
            "deixis_center": deixis, "time_scope": time_scope}


def _request_body(query: str, feedback: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    segments = unicode_segments(query)
    payload: dict[str, Any] = {"original_request": query, "segments": segments}
    if feedback:
        payload["validation_feedback"] = feedback
    return {"model": "local", "temperature": 0, "seed": seed, "max_tokens": 1600,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "phase1_relation_v253", "schema": live_schema(), "strict": True}},
            "messages": [{"role": "system", "content": system_prompt()},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}


def call_once(query: str, feedback: list[dict[str, Any]], seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = v25.bench.tier_endpoint(v25.bench.tier_for("intent.extract")).rstrip("/")
    request = urllib.request.Request(endpoint + "/v1/chat/completions",
        data=json.dumps(_request_body(query, feedback, seed), ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw), {"raw": raw, "latency_ms": (time.perf_counter()-started)*1000, "error": ""}
    except Exception as exc:
        return {}, {"raw": f"{type(exc).__name__}: {exc}", "latency_ms": (time.perf_counter()-started)*1000, "error": "transport_or_json"}


def run_case(query: str, max_attempts: int = 2) -> dict[str, Any]:
    segments = unicode_segments(query); feedback: list[dict[str, Any]] = []; attempts = []
    for attempt in range(1, max_attempts+1):
        frame, transport = call_once(query, feedback, 91+attempt)
        validation = validate_frame(frame, segments) if not transport["error"] else {
            "valid": False, "errors": [{"code": transport["error"], "path": "", "message": transport["raw"], "retryable": True}]}
        attempts.append({"attempt": attempt, "frame": frame, "transport": transport, "validation": validation})
        if validation["valid"]:
            return {"status": "evaluated", "query": query, "segments": segments, "frame": frame, "attempts": attempts}
        feedback = [{k: e.get(k, "") for k in ("code", "path", "message")} for e in validation["errors"]]
    return {"status": "not_evaluated", "query": query, "segments": segments, "attempts": attempts}


def _relevant_clause(frame: dict[str, Any]) -> dict[str, Any] | None:
    active = [c for c in frame.get("clauses", []) if c.get("relation") != "none"]
    return active[0] if active else None


def score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if result["status"] != "evaluated":
        return {"evaluable": False, "semantic_exact": None, "binding_ok": None,
                "predicted_binding": None, "expected": expected_signature(case)}
    clause = _relevant_clause(result["frame"])
    expected = expected_signature(case)
    exact = clause is not None and all(clause.get(k) == v for k,v in expected.items())
    predicted = any(is_current_location(c) for c in result["frame"].get("clauses", []))
    return {"evaluable": True, "semantic_exact": exact,
            "binding_ok": predicted == case["expect_binding"], "predicted_binding": predicted,
            "expected": expected}


def controls() -> list[dict[str, Any]]:
    return json.loads(CONTROLS_PATH.read_text())["cases"]


def run_controls(start: int = 1, end: int | None = None) -> dict[str, Any]:
    records = []
    for case in controls()[start-1:end]:
        result = run_case(case["query"]); score = score_case(case, result)
        records.append({"case": case, "result": result, "score": score})
    evaluated = [r for r in records if r["score"]["evaluable"]]
    pos = [r for r in evaluated if r["case"]["expect_binding"]]
    neg = [r for r in evaluated if not r["case"]["expect_binding"]]
    lat = [a["transport"]["latency_ms"] for r in records for a in r["result"].get("attempts", [])]
    summary = {"records": len(records), "evaluable": len(evaluated), "not_evaluated": len(records)-len(evaluated),
               "semantic_exact": sum(r["score"]["semantic_exact"] for r in evaluated),
               "binding_exact": sum(r["score"]["binding_ok"] for r in evaluated),
               "positive_hits": sum(r["score"]["predicted_binding"] for r in pos), "positive_denominator": len(pos),
               "negative_leakage": sum(r["score"]["predicted_binding"] for r in neg), "negative_denominator": len(neg),
               "retries": sum(len(r["result"].get("attempts", []))-1 for r in records),
               "latency_attempt_p50_ms": statistics.median(lat) if lat else None,
               "latency_attempt_p95_ms": statistics.quantiles(lat,n=100,method="inclusive")[94] if len(lat)>1 else None}
    return {"version": VERSION, "freeze": json.loads(FREEZE_PATH.read_text()), "summary": summary, "records": records}


def mutation_tests() -> dict[str, Any]:
    base = {"clause_id":1,"clause_start_segment_id":1,"clause_end_segment_id":2,"predicate_segment_id":2,
            "role":"request","interpretation":"supported","speech_act":"open_question","answer_mode":"open_variable",
            "relation":"spatial.located_at","focus_role":"location","focus_type":"spatial_position",
            "focus_evidence_kind":"explicit_segment","focus_start_segment_id":1,"focus_end_segment_id":1,
            "subject_ref":"current_actor","subject_type":"actor","grammatical_person":"first","deixis_center":"speaker",
            "subject_evidence_kind":"predicate_morphology","subject_start_segment_id":0,"subject_end_segment_id":0,
            "time_scope":"current","time_evidence_kind":"speaker_context","time_start_segment_id":0,"time_end_segment_id":0}
    tests = []
    frame={"clauses":[base]}; tests.append({"id":"base_valid_current","pass":validate_frame(frame,[{},{}])["valid"] and is_current_location(base)})
    for field,value in {"speech_act":"polar_question","answer_mode":"boolean","relation":"identity.same_as",
                        "focus_role":"identity","focus_type":"identity","subject_ref":"explicit_entity",
                        "subject_type":"person","grammatical_person":"third","deixis_center":"explicit_entity",
                        "time_scope":"historical"}.items():
        changed=copy.deepcopy(base);changed[field]=value
        tests.append({"id":f"binding_mutation_{field}","pass":not is_current_location(changed)})
    bad=copy.deepcopy(base);bad["subject_ref"]="explicit_entity"
    tests.append({"id":"incoherent_subject_rejected","pass":not validate_frame({"clauses":[bad]},[{},{}])["valid"]})
    bad=copy.deepcopy(base);bad["time_scope"]="historical"
    tests.append({"id":"historical_without_evidence_rejected","pass":not validate_frame({"clauses":[bad]},[{},{}])["valid"]})
    seg={s:[x["text"] for x in unicode_segments(s)] for s in ["我在哪里","私はどこにいますか","neredeyim","où suis-je"]}
    tests.append({"id":"unicode_word_boundaries","pass":len(seg["我在哪里"])==4 and len(seg["neredeyim"])==1,"segments":seg})
    return {"tests":tests,"summary":{"tests":len(tests),"passed":sum(t["pass"] for t in tests)}}


def freeze() -> dict[str, Any]:
    SCHEMA_PATH.write_text(json.dumps(live_schema(),ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    PROMPT_PATH.write_text(system_prompt())
    fixture={"version":VERSION,"source_sha256":hashlib.sha256(CONTROLS_PATH.read_bytes()).hexdigest(),
             "cases":[{"id":c["id"],"expect_binding":c["expect_binding"],"expected":expected_signature(c)} for c in controls()]}
    FIXTURE_PATH.write_text(json.dumps(fixture,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    mutations=mutation_tests();MUTATIONS_PATH.write_text(json.dumps(mutations,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    lock={"version":VERSION,"network_calls_before_freeze":0,
          "runner_sha256":hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
          "schema_sha256":hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
          "prompt_sha256":hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
          "controls_sha256":hashlib.sha256(CONTROLS_PATH.read_bytes()).hexdigest(),
          "fixture_sha256":hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
          "mutations_sha256":hashlib.sha256(MUTATIONS_PATH.read_bytes()).hexdigest(),
          "segmentation":"Unicode UAX#29 default word boundary via regex.WORD"}
    FREEZE_PATH.write_text(json.dumps(lock,ensure_ascii=False,indent=2,sort_keys=True)+"\n");return lock


def verify_freeze() -> None:
    lock=json.loads(FREEZE_PATH.read_text());paths={"runner_sha256":RUNNER_PATH,"schema_sha256":SCHEMA_PATH,
        "prompt_sha256":PROMPT_PATH,"controls_sha256":CONTROLS_PATH,"fixture_sha256":FIXTURE_PATH,"mutations_sha256":MUTATIONS_PATH}
    changed={k:(lock[k],hashlib.sha256(p.read_bytes()).hexdigest()) for k,p in paths.items() if lock[k]!=hashlib.sha256(p.read_bytes()).hexdigest()}
    if changed:raise RuntimeError(f"frozen artifacts changed: {changed}")


def main() -> int:
    ap=argparse.ArgumentParser();ap.add_argument("--freeze",action="store_true");ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--controls",action="store_true");ap.add_argument("--start",type=int,default=1);ap.add_argument("--end",type=int);ap.add_argument("--output")
    args=ap.parse_args()
    if args.freeze:print(json.dumps(freeze(),sort_keys=True))
    if args.self_test:
        result=mutation_tests();print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        if result["summary"]["passed"]!=result["summary"]["tests"]:return 1
    if args.controls:
        verify_freeze();result=run_controls(args.start,args.end)
        if args.output:Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
        print(json.dumps(result["summary"],sort_keys=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
