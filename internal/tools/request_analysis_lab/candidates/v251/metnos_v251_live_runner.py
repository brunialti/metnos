#!/usr/bin/env python3
"""V25.1 live experiment: requested-result union coupled to routing evidence.

This imports the frozen V25 transport/projector stack, but replaces its two
optional binding fields with one mandatory tagged result union.  Projection
and validation never inspect input-language words.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema

sys.path.insert(0, "/tmp")
import metnos_v25_live_runner as base  # noqa: E402


bench = base.bench
v241 = base.v241

RUNNER_PATH = Path(__file__)
SCHEMA_PATH = Path("/tmp/metnos_v251_live.schema.json")
PROMPT_PATH = Path("/tmp/metnos_v251_live.prompt.txt")
FIXTURE_PATH = Path("/tmp/metnos_v251_targeted_fixture.json")
FREEZE_PATH = Path("/tmp/metnos_v251_live.freeze.json")


V251_REFINEMENTS = """\
Ground the requested result before product routing. Every semantic head emits
exactly one `requested_result` tagged-union variant and only that variant's
fields. This is answer/argument structure, not grammatical object detection:

- none: only a non-request with no semantic result or argument;
- explicit_entity: source tokens explicitly name the acted-on or returned
  entity; ground its positive inclusive span and canonical domain;
- held_result: an earlier predicate supplies an elided result; its domain must
  be preserved and source_predicate_id must identify that earlier predicate;
- context_implicit: predicate/context supplies an unstated result and there is
  no source edge;
- question_slot/runtime.current_location: the requested answer is the current
  geospatial position of the user, device, or session itself. Ground the source
  span establishing that answer focus. Its answer_object is places and its
  product route is get/places. It is not personal identity/profile, a person
  lookup, a named-place lookup, or the location of another explicit entity;
- support_argument/calendar_event_attachment: this predicate references one
  already-existing file as support for an earlier create/events predicate.
  Ground the file, target that earlier predicate, keep the source edge equal to
  the target, route get/files, and emit no sink. Creating, saving, or updating a
  file is not support_argument.

For every request, the Phase 2 product object MUST equal the requested-result
object (or answer_object), except where the catalog contract explicitly changes
representation. A current-location answer cannot be encoded as a generic
implicit places result: use question_slot, which couples evidence and route by
construction. An existing explicit file acquired through a source edge from an
earlier create/events predicate cannot be encoded as an independent entity: use
support_argument.

Sink ownership comes from technical contracts. An explicit persistent store
for an otherwise ephemeral extracted result is secondary persistence; a
transformation-owned artifact or dedicated create/write output is primary.
Never derive any result variant from lemma/gloss spelling or source-language
term lists. Output only the schema.
"""


V251_GRAPH_REFINEMENTS = """\
Build a grounded predicate graph before Phase 2 routing. Assign predicate_id
exactly 1,2,3,... in source order; predicate_anchor_token_id is a source token,
not the graph id. source_predicate_id is 0 or an earlier predicate id supplying
the held result.

Carrier and sink are tagged unions. A non-none carrier or sink has a strictly
positive inclusive source span and canonical object. Carrier distinguishes a
source container, transport channel, or representation. Sink primary_output is
owned by the predicate itself; secondary_persistence is an additional durable
commit without a dedicated persistence predicate. A dedicated downstream
predicate and an upstream secondary sink must never encode the same operation.

A held result preserves its canonical domain through selection, ordering,
classification, or deletion unless an operation explicitly changes its
representation. Phase 2 copies graph ids, anchors, roles, and source edges, then
selects the product route without revising Phase 1.
"""


def requested_result_schema() -> dict[str, Any]:
    objects = list(bench.OBJECTS)
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
                    "kind": {"const": "explicit_entity"},
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                    "object": {"type": "string", "enum": objects},
                },
                "required": [
                    "kind", "start_token_id", "end_token_id", "object",
                ],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "held_result"},
                    "object": {"type": "string", "enum": objects},
                },
                "required": ["kind", "object"],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "context_implicit"},
                    "object": {"type": "string", "enum": objects},
                },
                "required": ["kind", "object"],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "question_slot"},
                    "binding_id": {"const": "runtime.current_location"},
                    "answer_object": {"const": "places"},
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "kind", "binding_id", "answer_object",
                    "start_token_id", "end_token_id",
                ],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "support_argument"},
                    "binding_id": {"const": "calendar_event_attachment"},
                    "target_predicate_id": {"type": "integer", "minimum": 1},
                    "object": {"const": "files"},
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "kind", "binding_id", "target_predicate_id", "object",
                    "start_token_id", "end_token_id",
                ],
            },
        ]
    }


def live_schema() -> dict[str, Any]:
    schema = copy.deepcopy(base.live_schema())
    head = schema["properties"]["semantic_heads"]["items"]
    head["properties"].pop("patient")
    head["properties"].pop("question_binding")
    head["properties"].pop("argument_binding")
    head["properties"]["requested_result"] = requested_result_schema()
    head["required"] = list(head["properties"])
    return schema


def system_prompt() -> str:
    instruction = bench.BASE_INSTRUCTION.format(
        boundaries=bench.render_boundaries("en"),
        object_contract=bench.OBJECT_CONTRACT,
    )
    instruction += """\
\nValidation policy: emit every independently requested operation exactly once.
An operation embedded only as the patient/state of another predicate is not an
extra request. A condition may require later planning, but is not an independent
intent unless explicitly requested. Canonical intent stays semantic.
"""
    for refinement in [
        bench.ONTOLOGY_REFINEMENTS,
        bench.TIE_BREAK_REFINEMENTS,
        bench.STRUCTURE_REFINEMENTS,
        bench.COMPOUND_REFINEMENTS,
        bench.STRICT_COMPOUND_REFINEMENTS,
        bench.ANCHOR_AND_SINK_REFINEMENTS,
        bench.LEMMA_LAYER_REFINEMENTS,
        bench.GLOSS_REFINEMENTS,
        bench.SCOPE_REFINEMENTS,
        V251_GRAPH_REFINEMENTS,
        V251_REFINEMENTS,
    ]:
        instruction += "\n" + refinement
    return instruction


def _span_ok(value: dict[str, Any], token_count: int) -> bool:
    start = value.get("start_token_id")
    end = value.get("end_token_id")
    return (isinstance(start, int) and isinstance(end, int)
            and 1 <= start <= end <= token_count)


def to_base_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Derive V25 fields from the single V25.1 union, without text access."""
    converted = copy.deepcopy(frame)
    for head in converted.get("semantic_heads", []):
        result = head.pop("requested_result")
        kind = result["kind"]
        question: dict[str, Any] = {"kind": "none"}
        argument: dict[str, Any] = {"kind": "none"}
        if kind == "none":
            patient = {"kind": "none"}
        elif kind == "explicit_entity":
            patient = {
                "kind": "explicit", "object": result["object"],
                "start_token_id": result["start_token_id"],
                "end_token_id": result["end_token_id"],
            }
        elif kind == "held_result":
            patient = {"kind": "elided", "object": result["object"]}
        elif kind == "context_implicit":
            patient = {"kind": "implicit", "object": result["object"]}
        elif kind == "question_slot":
            patient = {"kind": "implicit", "object": result["answer_object"]}
            question = {
                "kind": "catalog_query",
                "binding_id": result["binding_id"],
                "start_token_id": result["start_token_id"],
                "end_token_id": result["end_token_id"],
            }
        elif kind == "support_argument":
            patient = {
                "kind": "explicit", "object": result["object"],
                "start_token_id": result["start_token_id"],
                "end_token_id": result["end_token_id"],
            }
            argument = {
                "kind": "support_argument",
                "binding_id": result["binding_id"],
                "target_predicate_id": result["target_predicate_id"],
                "start_token_id": result["start_token_id"],
                "end_token_id": result["end_token_id"],
                "object": result["object"],
            }
        else:
            raise ValueError(f"unknown requested_result kind: {kind}")
        head["patient"] = patient
        head["question_binding"] = question
        head["argument_binding"] = argument
    return converted


def validate_live_frame(frame: dict[str, Any], tokens: list[str]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    validator = jsonschema.Draft202012Validator(live_schema())
    for issue in sorted(validator.iter_errors(frame), key=lambda e: list(e.path)):
        errors.append({
            "code": "schema",
            "path": "/".join(str(p) for p in issue.absolute_path),
            "message": issue.message,
            "retryable": True,
        })
    if errors:
        return {"valid": False, "errors": errors}

    try:
        converted = to_base_frame(frame)
    except Exception as exc:
        return {"valid": False, "errors": [{
            "code": "requested_result_conversion", "path": "",
            "message": str(exc), "retryable": True,
        }]}
    base_validation = base.validate_live_frame(converted, tokens)
    if not base_validation["valid"]:
        return base_validation

    heads = frame["semantic_heads"]
    routes = frame["predicates"]
    for index, (head, route) in enumerate(zip(heads, routes), 1):
        result = head["requested_result"]
        kind = result["kind"]
        prefix = f"semantic_heads/{index - 1}/requested_result"
        source_id = head["source_predicate_id"]
        if head["role"] == "request" and kind == "none":
            errors.append({"code": "request_result_required", "path": prefix,
                           "message": "request requires a requested result",
                           "retryable": True})
        if kind in ("explicit_entity", "question_slot", "support_argument"):
            if not _span_ok(result, len(tokens)):
                errors.append({"code": "result_span", "path": prefix,
                               "message": "active result requires source span",
                               "retryable": True})
        if kind == "held_result" and source_id <= 0:
            errors.append({"code": "held_result_source", "path": prefix,
                           "message": "held result requires earlier source id",
                           "retryable": True})
        if kind in ("context_implicit", "question_slot") and source_id != 0:
            errors.append({"code": "implicit_result_source", "path": prefix,
                           "message": "context/question result has no source edge",
                           "retryable": True})
        result_object = result.get("answer_object", result.get("object"))
        if (head["role"] == "request" and result_object
                and route.get("object") != result_object):
            errors.append({"code": "result_route_object", "path": prefix,
                           "message": "route object must equal result object",
                           "retryable": True})
        if kind == "question_slot":
            if route.get("verb") != "get" or route.get("object") != "places":
                errors.append({"code": "question_route", "path": prefix,
                               "message": "current-location slot requires get/places",
                               "retryable": True})
        elif (kind == "context_implicit" and source_id == 0
              and result.get("object") == "places"
              and route.get("verb") == "get"):
            errors.append({"code": "question_slot_required", "path": prefix,
                           "message": "implicit current-location answer must use question_slot",
                           "retryable": True})
        if kind == "support_argument":
            target_id = result["target_predicate_id"]
            if (target_id >= index
                    or routes[target_id - 1].get("verb") != "create"
                    or routes[target_id - 1].get("object") != "events"
                    or source_id != target_id
                    or route.get("verb") != "get"
                    or route.get("object") != "files"
                    or head.get("sink", {}).get("kind") != "none"):
                errors.append({"code": "support_argument_contract", "path": prefix,
                               "message": "support must target earlier create/events and route get/files without sink",
                               "retryable": True})
        elif (kind == "explicit_entity" and source_id > 0
              and route.get("verb") == "get" and route.get("object") == "files"
              and routes[source_id - 1].get("verb") == "create"
              and routes[source_id - 1].get("object") == "events"
              and head.get("sink", {}).get("kind") == "none"):
            errors.append({"code": "support_argument_required", "path": prefix,
                           "message": "existing file supporting create/events must use support_argument",
                           "retryable": True})
    return {"valid": not errors, "errors": errors,
            "base_normalizations": base_validation.get("base_normalizations", [])}


def to_v241(frame: dict[str, Any], tokens: list[str]) -> dict[str, Any]:
    return base.to_v241(to_base_frame(frame), tokens)


def request_body(query: str, *, seed: int,
                 feedback: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = bench.tokenize(query)
    payload: dict[str, Any] = {
        "original_request": query,
        "tokens": [{"id": i, "text": token}
                   for i, token in enumerate(tokens, 1)],
    }
    if feedback:
        payload["validation_feedback"] = feedback
    return {
        "model": "local", "temperature": 0, "seed": seed,
        "max_tokens": 3400,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "grounded_request_analysis_v251",
            "schema": live_schema(), "strict": True,
        }},
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def call_once(query: str, *, seed: int,
              feedback: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    body = request_body(query, seed=seed, feedback=feedback)
    endpoint = bench.tier_endpoint(bench.tier_for("intent.extract")).rstrip("/")
    request = urllib.request.Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        frame = json.loads(raw)
        error = ""
    except Exception as exc:
        raw, frame, error = f"{type(exc).__name__}: {exc}", {}, "transport_or_json"
    return frame, {"raw": raw,
                   "latency_ms": (time.perf_counter() - started) * 1000,
                   "transport_error": error}


def run_case(query: str, *, max_attempts: int = 2) -> dict[str, Any]:
    tokens = bench.tokenize(query)
    attempts: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        frame, transport = call_once(query, seed=51 + attempt, feedback=feedback)
        validation = ({"valid": False, "errors": [{
            "code": transport["transport_error"], "path": "",
            "message": transport["raw"], "retryable": True,
        }]} if transport["transport_error"] else validate_live_frame(frame, tokens))
        attempts.append({"attempt": attempt, "frame": frame,
                         "transport": transport, "validation": validation})
        if validation["valid"]:
            contract = to_v241(frame, tokens)
            projection = v241.project_v241(
                contract, tokens, mode="research", delta=v241.load_delta())
            return {
                "status": projection["status"], "query": query, "tokens": tokens,
                "attempts": attempts, "frame": frame, "v241_frame": contract,
                "v241_validation": v241.validate_v241(
                    contract, tokens, v241.load_delta()),
                "signature": projection.get("signature"),
                "projection": projection,
            }
        feedback = [{"code": e["code"], "path": e.get("path", ""),
                     "message": e.get("message", "")}
                    for e in validation["errors"] if e.get("retryable")]
    return {"status": "fail_closed", "reason": "invalid_after_retry",
            "query": query, "tokens": tokens, "attempts": attempts,
            "signature": None}


def claim_counts(frame: dict[str, Any]) -> tuple[int, int]:
    results = [h.get("requested_result", {})
               for h in frame.get("semantic_heads", [])]
    return (sum(r.get("kind") == "question_slot" for r in results),
            sum(r.get("kind") == "support_argument" for r in results))


def score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    questions, arguments = claim_counts(result.get("frame") or {})
    route_ok = result.get("signature") == case["expected"]
    question_ok = (questions > 0) == case.get("expected_question", False)
    argument_ok = (arguments > 0) == case.get("expected_argument", False)
    return {
        "route_ok": route_ok, "question_binding_ok": question_ok,
        "argument_binding_ok": argument_ok,
        "strict_ok": route_ok and question_ok and argument_ok,
        "question_binding_count": questions,
        "argument_binding_count": arguments,
    }


def run_suite(cases: list[dict[str, Any]]) -> dict[str, Any]:
    records = []
    for case in cases:
        result = run_case(case["query"])
        score = score_case(case, result)
        overlay = case.get("gold_overlay")
        adjudicated = (result.get("signature") == overlay["expected"]
                       if overlay else score["route_ok"])
        records.append({"case": case, "result": result, "score": score,
                        "adjudicated_route_ok": adjudicated})
    return {
        "suite_version": "metnos.v25.1-requested-result/1.0",
        "freeze": json.loads(FREEZE_PATH.read_text()),
        "summary": {
            "records": len(records),
            "valid": sum(r["result"]["status"] == "projected" for r in records),
            "strict": sum(r["score"]["strict_ok"] for r in records),
            "route_exact_original": sum(r["score"]["route_ok"] for r in records),
            "route_exact_adjudicated": sum(r["adjudicated_route_ok"] for r in records),
            "question_binding_gate": sum(r["score"]["question_binding_ok"] for r in records),
            "argument_binding_gate": sum(r["score"]["argument_binding_ok"] for r in records),
            "retries": sum(len(r["result"].get("attempts", [])) - 1 for r in records),
        },
        "records": records,
    }


def synthetic_self_test() -> dict[str, Any]:
    def frame(result: dict[str, Any], verb: str, obj: str,
              *, source: int = 0, target: bool = False) -> dict[str, Any]:
        heads = []
        routes = []
        if target:
            heads.append({
                "predicate_id": 1, "predicate_anchor_token_id": 1,
                "role": "request", "lemma": "create",
                "semantic_gloss_en": "create", "semantic_action": "create",
                "resource_scope": "new_resource", "carrier": {"kind": "none"},
                "source_predicate_id": 0, "sink": {"kind": "none"},
                "requested_result": {"kind": "explicit_entity",
                                     "start_token_id": 2, "end_token_id": 2,
                                     "object": "events"},
            })
            routes.append({
                "predicate_id": 1, "predicate_anchor_token_id": 1,
                "role": "request", "verb": "create", "verb_resolution": "direct",
                "object": "events", "object_qualifier": "none",
                "input_from_predicate_id": 0,
            })
        index = len(heads) + 1
        heads.append({
            "predicate_id": index, "predicate_anchor_token_id": index + 1,
            "role": "request", "lemma": verb,
            "semantic_gloss_en": verb, "semantic_action": verb,
            "resource_scope": "single_known", "carrier": {"kind": "none"},
            "source_predicate_id": source, "sink": {"kind": "none"},
            "requested_result": result,
        })
        routes.append({
            "predicate_id": index, "predicate_anchor_token_id": index + 1,
            "role": "request", "verb": verb, "verb_resolution": "direct",
            "object": obj, "object_qualifier": "none",
            "input_from_predicate_id": source,
        })
        return {"semantic_heads": heads, "predicates": routes}

    q = frame({"kind": "question_slot", "binding_id": "runtime.current_location",
               "answer_object": "places", "start_token_id": 1,
               "end_token_id": 1}, "get", "places")
    q_bad = copy.deepcopy(q); q_bad["predicates"][0]["object"] = "persons"
    implicit = frame({"kind": "context_implicit", "object": "places"},
                     "get", "places")
    support = frame({"kind": "support_argument",
                     "binding_id": "calendar_event_attachment",
                     "target_predicate_id": 1, "object": "files",
                     "start_token_id": 3, "end_token_id": 3},
                    "get", "files", source=1, target=True)
    support_missing = copy.deepcopy(support)
    support_missing["semantic_heads"][1]["requested_result"] = {
        "kind": "explicit_entity", "start_token_id": 3,
        "end_token_id": 3, "object": "files"}
    write = frame({"kind": "explicit_entity", "start_token_id": 2,
                   "end_token_id": 2, "object": "files"},
                  "write", "files")
    tests = [
        ("question.valid", q, ["where", "am"] , True),
        ("question.route_reject", q_bad, ["where", "am"], False),
        ("question.slot_required", implicit, ["where", "am"], False),
        ("support.valid", support, ["create", "event", "file"], True),
        ("support.required", support_missing, ["create", "event", "file"], False),
        ("write.control", write, ["write", "file"], True),
    ]
    rows = [{"id": name, "pass": validate_live_frame(value, tokens)["valid"] == expected}
            for name, value, tokens, expected in tests]
    return {"tests": rows, "summary": {"tests": len(rows),
            "passed": sum(r["pass"] for r in rows),
            "failed": sum(not r["pass"] for r in rows)}}


def emit_and_freeze() -> dict[str, Any]:
    SCHEMA_PATH.write_text(json.dumps(live_schema(), ensure_ascii=False,
                                      indent=2, sort_keys=True) + "\n")
    PROMPT_PATH.write_text(system_prompt())
    FIXTURE_PATH.write_text(json.dumps(base.TARGETED_CASES, ensure_ascii=False,
                                       indent=2, sort_keys=True) + "\n")
    lock = {
        "freeze_version": "metnos.v25.1-requested-result/1.0",
        "runner_sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "base_v25_sha256": hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest(),
        "base_bench_sha256": hashlib.sha256(base.BENCH_PATH.read_bytes()).hexdigest(),
        "v241_rules_sha256": hashlib.sha256(v241.RULES_PATH.read_bytes()).hexdigest(),
        "network_calls_by_preparer": 0,
    }
    FREEZE_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock


def verify_freeze() -> None:
    lock = json.loads(FREEZE_PATH.read_text())
    actual = {
        "runner_sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "base_v25_sha256": hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest(),
        "base_bench_sha256": hashlib.sha256(base.BENCH_PATH.read_bytes()).hexdigest(),
        "v241_rules_sha256": hashlib.sha256(v241.RULES_PATH.read_bytes()).hexdigest(),
    }
    changed = {k: {"locked": lock.get(k), "actual": v}
               for k, v in actual.items() if lock.get(k) != v}
    if changed:
        raise RuntimeError(f"frozen V25.1 artifact changed: {changed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dataset", choices=["targeted", "full"])
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.freeze:
        print(json.dumps(emit_and_freeze(), sort_keys=True))
    if args.self_test:
        result = synthetic_self_test()
        print(json.dumps(result, sort_keys=True))
        if result["summary"]["failed"]:
            return 1
    if args.dataset:
        verify_freeze()
        cases = (base.TARGETED_CASES if args.dataset == "targeted"
                 else base.full_cases(args.start, args.end))
        result = run_suite(cases)
        if args.output:
            Path(args.output).write_text(json.dumps(
                result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
