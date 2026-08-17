#!/usr/bin/env python3
"""Live analyzer candidate with explicit question/argument bindings.

The main agent only prepares and freezes this artifact offline.  A coordinated
evaluator may invoke its network-backed ``run_case``/CLI later.  Projection
never reads query, lemma, gloss, or language-specific term lists.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema

sys.path.insert(0, "/tmp")
import metnos_v24_offline as v24  # noqa: E402
import metnos_v241_offline as v241  # noqa: E402


BENCH_PATH = Path("/tmp/metnos_unified_query_bench_v22.py")
SCHEMA_PATH = Path("/tmp/metnos_v25_live.schema.json")
FIXTURE_PATH = Path("/tmp/metnos_v25_targeted_fixture.json")
PROMPT_PATH = Path("/tmp/metnos_v25_live.prompt.txt")
FREEZE_PATH = Path("/tmp/metnos_v25_live.freeze.json")
FULL_GLOB = "/tmp/metnos_v23lite_full_*"


def _load_bench():
    spec = importlib.util.spec_from_file_location("metnos_v25_bench_base", BENCH_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import bench base")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load_bench()


QUESTION_BINDINGS = ["runtime.current_location"]
ARGUMENT_BINDINGS = ["calendar_event_attachment"]


V25_REFINEMENTS = """\
Catalog-grounded bindings (language-neutral; never match source-word lists):

For every semantic head emit `question_binding`, a tagged union.
- Use {"kind":"none"} unless the clause asks for a catalog query relation.
- Use `runtime.current_location` only when the requested result is the current
  geospatial position of the user/device/session itself. Ground the relation in
  a strictly positive source span that establishes that question. This is not
  personal identity/profile, a person lookup, a named-place lookup, or the
  location of some other entity.
- With runtime.current_location, the semantic patient domain and Phase 2 object
  are places, the route is get/places, and the binding span is evidence for that
  decision. Do not use a persons route merely because the location belongs to
  the user.

For every semantic head emit `argument_binding`, also a tagged union.
- Use {"kind":"none"} unless this predicate supplies an existing resource as
  a typed argument to an earlier predicate.
- `calendar_event_attachment` means this predicate references an already
  existing file as attachment/support for an earlier create/events predicate.
  Set target_predicate_id to that earlier id, ground the existing file in a
  positive span, keep source_predicate_id equal to the target id, use an
  explicit files patient, route this support predicate as get/files, and use
  {"kind":"none"} sink. The binding is not a new predicate.
- Creating, saving, updating, or writing a file is not an argument binding,
  even when another predicate also creates an event.

Sink ownership comes from the technical operation contract, not a verb list.
An explicit persistent store for an otherwise ephemeral extracted entries
result is secondary_persistence and is separate from extract/entries. A
compression operation owns its archive as primary output. A dedicated
write/create predicate owns its own output. Never mark an existing support file
as a new primary output.

Bindings are semantic graph facts. Complete them before Phase 2 routing. Never
derive them from lemma/gloss spelling, never copy source text, and never emit a
binding merely to force a known route.
"""


def live_schema() -> dict[str, Any]:
    schema = copy.deepcopy(bench.schema("v24"))
    head = schema["properties"]["semantic_heads"]["items"]
    head["properties"]["question_binding"] = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "catalog_query"},
                    "binding_id": {
                        "type": "string",
                        "enum": QUESTION_BINDINGS,
                    },
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "kind",
                    "binding_id",
                    "start_token_id",
                    "end_token_id",
                ],
            },
        ]
    }
    head["properties"]["argument_binding"] = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "support_argument"},
                    "binding_id": {
                        "type": "string",
                        "enum": ARGUMENT_BINDINGS,
                    },
                    "target_predicate_id": {"type": "integer", "minimum": 1},
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                    "object": {"const": "files"},
                },
                "required": [
                    "kind",
                    "binding_id",
                    "target_predicate_id",
                    "start_token_id",
                    "end_token_id",
                    "object",
                ],
            },
        ]
    }
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
intent unless the user explicitly commands its verification. The canonical
intent stays semantic; do not change it merely to imitate a tool name.
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
        bench.TAGGED_ARGUMENT_GRAPH_V24_REFINEMENTS,
        V25_REFINEMENTS,
    ]:
        instruction += "\n" + refinement
    return instruction


def _span_ok(value: dict[str, Any], token_count: int) -> bool:
    start = value.get("start_token_id")
    end = value.get("end_token_id")
    return (
        isinstance(start, int)
        and isinstance(end, int)
        and 1 <= start <= end <= token_count
    )


def _strip_bindings(frame: dict[str, Any]) -> dict[str, Any]:
    base = copy.deepcopy(frame)
    for head in base.get("semantic_heads", []):
        head.pop("question_binding", None)
        head.pop("argument_binding", None)
    return base


def validate_live_frame(frame: dict[str, Any], tokens: list[str]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    validator = jsonschema.Draft202012Validator(live_schema())
    for issue in sorted(validator.iter_errors(frame), key=lambda e: list(e.path)):
        errors.append(
            {
                "code": "schema",
                "path": "/".join(str(p) for p in issue.absolute_path),
                "message": issue.message,
                "retryable": True,
            }
        )
    if errors:
        return {"valid": False, "errors": errors}

    base = _strip_bindings(frame)
    base, normalizations = bench.canonicalize_v24_frame(base)
    ok, reason = bench.validate_frame_v23(base, tokens, prompt_variant="v24")
    if not ok:
        errors.append(
            {
                "code": "base_v24",
                "path": "",
                "message": reason,
                "retryable": True,
            }
        )

    heads = frame["semantic_heads"]
    predicates = frame["predicates"]
    for index, (head, route) in enumerate(zip(heads, predicates), 1):
        prefix = f"semantic_heads/{index - 1}"
        question = head["question_binding"]
        if question["kind"] != "none":
            if not _span_ok(question, len(tokens)):
                errors.append(
                    {
                        "code": "question_span",
                        "path": f"{prefix}/question_binding",
                        "message": "active question binding requires source evidence",
                        "retryable": True,
                    }
                )
            patient = head["patient"]
            if patient.get("object") != "places":
                errors.append(
                    {
                        "code": "question_patient",
                        "path": f"{prefix}/patient",
                        "message": "current-location binding requires places patient",
                        "retryable": True,
                    }
                )
            if route.get("verb") != "get" or route.get("object") != "places":
                errors.append(
                    {
                        "code": "question_route",
                        "path": f"predicates/{index - 1}",
                        "message": "current-location binding requires get/places",
                        "retryable": True,
                    }
                )

        argument = head["argument_binding"]
        if argument["kind"] != "none":
            target_id = argument["target_predicate_id"]
            if target_id >= index:
                errors.append(
                    {
                        "code": "argument_target",
                        "path": f"{prefix}/argument_binding",
                        "message": "support target must be an earlier predicate",
                        "retryable": True,
                    }
                )
            else:
                target = predicates[target_id - 1]
                if target.get("verb") != "create" or target.get("object") != "events":
                    errors.append(
                        {
                            "code": "argument_target_route",
                            "path": f"{prefix}/argument_binding",
                            "message": "calendar attachment must target create/events",
                            "retryable": True,
                        }
                    )
            if not _span_ok(argument, len(tokens)):
                errors.append(
                    {
                        "code": "argument_span",
                        "path": f"{prefix}/argument_binding",
                        "message": "support argument requires source evidence",
                        "retryable": True,
                    }
                )
            patient = head["patient"]
            if (
                patient.get("kind") != "explicit"
                or patient.get("object") != "files"
            ):
                errors.append(
                    {
                        "code": "argument_patient",
                        "path": f"{prefix}/patient",
                        "message": "attachment support requires explicit files patient",
                        "retryable": True,
                    }
                )
            if head.get("source_predicate_id") != target_id:
                errors.append(
                    {
                        "code": "argument_source_edge",
                        "path": f"{prefix}/source_predicate_id",
                        "message": "support source edge must equal target predicate id",
                        "retryable": True,
                    }
                )
            if route.get("verb") != "get" or route.get("object") != "files":
                errors.append(
                    {
                        "code": "argument_route",
                        "path": f"predicates/{index - 1}",
                        "message": "existing file support requires get/files",
                        "retryable": True,
                    }
                )
            if head.get("sink", {}).get("kind") != "none":
                errors.append(
                    {
                        "code": "argument_sink",
                        "path": f"{prefix}/sink",
                        "message": "existing support file is not a new output sink",
                        "retryable": True,
                    }
                )
    return {
        "valid": not errors,
        "errors": errors,
        "base_normalizations": normalizations,
    }


def to_v241(frame: dict[str, Any], tokens: list[str]) -> dict[str, Any]:
    """Convert live dual-array output without legacy relation inference."""
    nodes: list[dict[str, Any]] = []
    for index, (head, predicate) in enumerate(
        zip(frame["semantic_heads"], frame["predicates"]), 1
    ):
        source_id = head.get("source_predicate_id", 0)
        live_patient = head["patient"]
        kind = live_patient["kind"]
        if kind == "none":
            patient = {"kind": "none"}
        elif kind == "explicit":
            patient = copy.deepcopy(live_patient)
        else:
            patient = {
                "kind": "implicit",
                "object": live_patient["object"],
                "basis": (
                    "deictic_context" if kind == "elided" else "predicate_semantics"
                ),
            }

        old_carrier = head["carrier"]
        if old_carrier["kind"] == "none":
            carrier = {"kind": "none"}
        else:
            carrier = copy.deepcopy(old_carrier)
            carrier["value_type"] = v24._carrier_value_type(
                carrier, tokens, v24.load_rules()
            )

        old_sink = head["sink"]
        if old_sink["kind"] == "none":
            sink = {"kind": "none"}
        else:
            sink = copy.deepcopy(old_sink)
            if sink["kind"] == "secondary_persistence":
                sink["destination_role"] = "external_store"
            elif sink["object"] == "dirs":
                sink["destination_role"] = "destination_locator"
            else:
                sink["destination_role"] = "artifact_domain"

        route = (
            {
                "kind": "candidate",
                "action": predicate["verb"],
                "object": predicate["object"],
                "qualifier": predicate["object_qualifier"],
                "resolution": predicate["verb_resolution"],
            }
            if predicate["role"] == "request"
            else {"kind": "none"}
        )
        question = copy.deepcopy(head["question_binding"])
        live_argument = head["argument_binding"]
        argument_relations = []
        if live_argument["kind"] != "none":
            argument_relations.append(
                {
                    "kind": "support_argument",
                    "binding_id": live_argument["binding_id"],
                    "target_predicate_id": live_argument["target_predicate_id"],
                    "support_object": live_argument["object"],
                    "start_token_id": live_argument["start_token_id"],
                    "end_token_id": live_argument["end_token_id"],
                }
            )
        node = {
            "predicate_id": index,
            "predicate_anchor_token_id": head["predicate_anchor_token_id"],
            "role": head["role"],
            "lemma": head["lemma"],
            "semantic_gloss_en": head["semantic_gloss_en"],
            "semantic_action": head["semantic_action"],
            "resource_scope": head["resource_scope"],
            "input_from_predicate_id": source_id,
            "patient": patient,
            "carrier": carrier,
            "sink": sink,
            "route": route,
            "attribute_claims": [],
            "question_binding": question,
            "argument_relations": argument_relations,
            "evidence_refs": [],
        }
        node["evidence_refs"] = v241._ordered_evidence(
            v241.expected_evidence(node)
        )
        nodes.append(node)
    return {"schema_version": v241.VERSION, "nodes": nodes}


def _request_body(query: str, *, seed: int, feedback: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = bench.tokenize(query)
    payload = {
        "original_request": query,
        "tokens": [
            {"id": index, "text": token}
            for index, token in enumerate(tokens, 1)
        ],
    }
    if feedback:
        payload["validation_feedback"] = feedback
    return {
        "model": "local",
        "temperature": 0,
        "seed": seed,
        "max_tokens": 3400,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "grounded_request_analysis_v25",
                "schema": live_schema(),
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def _call_once(
    query: str, *, seed: int, feedback: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = _request_body(query, seed=seed, feedback=feedback)
    endpoint = bench.tier_endpoint(bench.tier_for("intent.extract")).rstrip("/")
    request = urllib.request.Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        frame = json.loads(raw)
        transport_error = ""
    except Exception as exc:
        raw = f"{type(exc).__name__}: {exc}"
        frame = {}
        transport_error = "transport_or_json"
    return frame, {
        "raw": raw,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "transport_error": transport_error,
    }


def run_case(query: str, *, max_attempts: int = 2) -> dict[str, Any]:
    tokens = bench.tokenize(query)
    attempts: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        frame, transport = _call_once(
            query, seed=41 + attempt, feedback=feedback
        )
        if transport["transport_error"]:
            validation = {
                "valid": False,
                "errors": [
                    {
                        "code": transport["transport_error"],
                        "path": "",
                        "message": transport["raw"],
                        "retryable": True,
                    }
                ],
            }
        else:
            validation = validate_live_frame(frame, tokens)
        attempts.append(
            {
                "attempt": attempt,
                "frame": frame,
                "transport": transport,
                "validation": validation,
            }
        )
        if validation["valid"]:
            v241_frame = to_v241(frame, tokens)
            v241_validation = v241.validate_v241(
                v241_frame, tokens, v241.load_delta()
            )
            projection = v241.project_v241(
                v241_frame, tokens, mode="research", delta=v241.load_delta()
            )
            return {
                "status": projection["status"],
                "query": query,
                "tokens": tokens,
                "attempts": attempts,
                "frame": frame,
                "v241_frame": v241_frame,
                "v241_validation": v241_validation,
                "signature": projection.get("signature"),
                "projection": projection,
            }
        feedback = [
            {
                "code": error["code"],
                "path": error.get("path", ""),
                "message": error.get("message", ""),
            }
            for error in validation["errors"]
            if error.get("retryable")
        ]
    return {
        "status": "fail_closed",
        "reason": "invalid_after_retry",
        "query": query,
        "tokens": tokens,
        "attempts": attempts,
        "signature": None,
    }


TARGETED_CASES = [
    {
        "id": "location.it.where",
        "track": "location",
        "language": "it",
        "query": "dove mi trovo",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "location.it.position",
        "track": "location",
        "language": "it",
        "query": "dimmi la mia posizione",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "location.en",
        "track": "location",
        "language": "en",
        "query": "where am I",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "location.fr",
        "track": "location",
        "language": "fr",
        "query": "où suis-je",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "location.es",
        "track": "location",
        "language": "es",
        "query": "dónde estoy",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "location.de",
        "track": "location",
        "language": "de",
        "query": "wo bin ich",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "location.pt_holdout",
        "track": "location_holdout",
        "language": "pt",
        "query": "onde estou",
        "expected": "get/places",
        "expected_question": True,
        "expected_argument": False,
    },
    {
        "id": "control.identity.it",
        "track": "control",
        "language": "it",
        "query": "chi sono io",
        "expected": "read/persons",
        "expected_question": False,
        "expected_argument": False,
    },
    {
        "id": "control.identity.en",
        "track": "control",
        "language": "en",
        "query": "who am I",
        "expected": "read/persons",
        "expected_question": False,
        "expected_argument": False,
    },
    {
        "id": "control.person.it",
        "track": "control",
        "language": "it",
        "query": "dimmi tutto su ospite alfa",
        "expected": "read/persons",
        "expected_question": False,
        "expected_argument": False,
    },
    {
        "id": "control.place.it",
        "track": "control",
        "language": "it",
        "query": "dammi le coordinate di Parigi",
        "expected": "get/places",
        "expected_question": False,
        "expected_argument": False,
    },
    {
        "id": "control.place.en",
        "track": "control",
        "language": "en",
        "query": "where is the Eiffel Tower",
        "expected": "get/places",
        "expected_question": False,
        "expected_argument": False,
    },
    {
        "id": "attachment.compact.it",
        "track": "attachment",
        "language": "it",
        "query": "crea un evento promemoria domani e allega la foto logo.png",
        "expected": ["create/events", "get/files"],
        "expected_question": False,
        "expected_argument": True,
    },
    {
        "id": "attachment.full.it",
        "track": "attachment",
        "language": "it",
        "query": "scarica il pdf da https://x.com/a.pdf, salvalo in /tmp, mandalo via mail a roberto, crea un evento promemoria domani e allega la foto logo.png",
        "expected": [
            "read/urls",
            "write/files",
            "send/messages",
            "create/events",
            "get/files"
        ],
        "expected_question": False,
        "expected_argument": True,
    },
    {
        "id": "control.write_file.it",
        "track": "negative_write",
        "language": "it",
        "query": "scrivi il file report.txt",
        "expected": "write/files",
        "expected_question": False,
        "expected_argument": False,
    },
    {
        "id": "control.event_then_write.it",
        "track": "negative_write",
        "language": "it",
        "query": "crea un evento domani e scrivi un file di riepilogo",
        "expected": ["create/events", "write/files"],
        "expected_question": False,
        "expected_argument": False,
    },
]


def _active_binding_counts(frame: dict[str, Any]) -> tuple[int, int]:
    questions = sum(
        head.get("question_binding", {}).get("kind") != "none"
        for head in frame.get("semantic_heads", [])
    )
    arguments = sum(
        head.get("argument_binding", {}).get("kind") != "none"
        for head in frame.get("semantic_heads", [])
    )
    return questions, arguments


def score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    questions, arguments = _active_binding_counts(result.get("frame") or {})
    route_ok = result.get("signature") == case["expected"]
    question_ok = (questions > 0) == case.get("expected_question", False)
    argument_ok = (arguments > 0) == case.get("expected_argument", False)
    return {
        "route_ok": route_ok,
        "question_binding_ok": question_ok,
        "argument_binding_ok": argument_ok,
        "strict_ok": route_ok and question_ok and argument_ok,
        "question_binding_count": questions,
        "argument_binding_count": arguments,
    }


def full_cases(start: int = 1, end: int | None = None) -> list[dict[str, Any]]:
    import glob

    rows = [
        row
        for path in sorted(glob.glob(FULL_GLOB))
        for row in json.loads(Path(path).read_text())
    ]
    stop = end or len(rows)
    cases = []
    for index, row in enumerate(rows, 1):
        if not start <= index <= stop:
            continue
        cases.append(
            {
                "id": f"full.{index:03d}",
                "index": index,
                "track": "full",
                "language": "mixed",
                "query": row["query"],
                "expected": row["expected"],
                "expected_question": index in {48},
                "expected_argument": index in {19},
                "gold_overlay": (
                    {
                        "status": "adjudicated_stale",
                        "expected": [
                            "read/messages",
                            "extract/entries",
                            "create/files_spreadsheet",
                        ],
                        "reason": "qualifier fixture inconsistent with another spreadsheet case",
                    }
                    if index == 12
                    else None
                ),
            }
        )
    return cases


def run_suite(cases: list[dict[str, Any]]) -> dict[str, Any]:
    records = []
    for case in cases:
        result = run_case(case["query"])
        score = score_case(case, result)
        overlay = case.get("gold_overlay")
        overlay_ok = (
            result.get("signature") == overlay["expected"] if overlay else score["route_ok"]
        )
        records.append(
            {
                "case": case,
                "result": result,
                "score": score,
                "adjudicated_route_ok": overlay_ok,
            }
        )
    return {
        "suite_version": "metnos.v25-explicit-bindings/1.0",
        "freeze": json.loads(FREEZE_PATH.read_text()) if FREEZE_PATH.exists() else None,
        "summary": {
            "records": len(records),
            "valid": sum(r["result"]["status"] == "projected" for r in records),
            "strict": sum(r["score"]["strict_ok"] for r in records),
            "route_exact_original": sum(r["score"]["route_ok"] for r in records),
            "route_exact_adjudicated": sum(r["adjudicated_route_ok"] for r in records),
            "question_binding_gate": sum(
                r["score"]["question_binding_ok"] for r in records
            ),
            "argument_binding_gate": sum(
                r["score"]["argument_binding_ok"] for r in records
            ),
            "retries": sum(len(r["result"].get("attempts", [])) - 1 for r in records),
        },
        "records": records,
    }


def _synthetic_self_test() -> dict[str, Any]:
    """Offline schema/validator/projector tests; no model or network."""
    tests = []

    def base_frame() -> dict[str, Any]:
        return {
            "semantic_heads": [
                {
                    "predicate_id": 1,
                    "predicate_anchor_token_id": 2,
                    "role": "request",
                    "lemma": "locate",
                    "semantic_gloss_en": "locate",
                    "semantic_action": "get",
                    "resource_scope": "single_known",
                    "patient": {"kind": "implicit", "object": "places"},
                    "carrier": {"kind": "none"},
                    "source_predicate_id": 0,
                    "sink": {"kind": "none"},
                    "question_binding": {
                        "kind": "catalog_query",
                        "binding_id": "runtime.current_location",
                        "start_token_id": 1,
                        "end_token_id": 1,
                    },
                    "argument_binding": {"kind": "none"},
                }
            ],
            "predicates": [
                {
                    "predicate_id": 1,
                    "predicate_anchor_token_id": 2,
                    "role": "request",
                    "verb": "get",
                    "verb_resolution": "technical_override",
                    "object": "places",
                    "object_qualifier": "none",
                    "input_from_predicate_id": 0,
                }
            ],
        }

    location = base_frame()
    valid = validate_live_frame(location, ["where", "locate"])
    converted = to_v241(location, ["where", "locate"])
    projection = v241.project_v241(converted, ["where", "locate"])
    tests.append(
        {
            "id": "location.explicit_binding",
            "pass": valid["valid"] and projection["signature"] == "get/places",
        }
    )

    wrong = base_frame()
    wrong["predicates"][0]["object"] = "persons"
    valid = validate_live_frame(wrong, ["where", "locate"])
    tests.append(
        {
            "id": "location.route_mismatch_rejected",
            "pass": (not valid["valid"])
            and "question_route" in {e["code"] for e in valid["errors"]},
        }
    )

    negative = base_frame()
    h = negative["semantic_heads"][0]
    h["lemma"] = "write"
    h["semantic_gloss_en"] = "write"
    h["semantic_action"] = "write"
    h["patient"] = {
        "kind": "explicit",
        "start_token_id": 2,
        "end_token_id": 2,
        "object": "files",
    }
    h["question_binding"] = {"kind": "none"}
    negative["predicates"][0].update(
        {"verb": "write", "object": "files", "verb_resolution": "direct"}
    )
    valid = validate_live_frame(negative, ["write", "file"])
    converted = to_v241(negative, ["write", "file"])
    projection = v241.project_v241(converted, ["write", "file"])
    tests.append(
        {
            "id": "write_file.no_argument_binding",
            "pass": valid["valid"] and projection["signature"] == "write/files",
        }
    )
    return {
        "tests": tests,
        "summary": {
            "tests": len(tests),
            "passed": sum(t["pass"] for t in tests),
            "failed": sum(not t["pass"] for t in tests),
        },
    }


def emit_and_freeze() -> dict[str, Any]:
    SCHEMA_PATH.write_text(
        json.dumps(live_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    PROMPT_PATH.write_text(system_prompt())
    FIXTURE_PATH.write_text(
        json.dumps(TARGETED_CASES, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    lock = {
        "freeze_version": "metnos.v25-live-explicit-bindings/1.0",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "base_bench_sha256": hashlib.sha256(BENCH_PATH.read_bytes()).hexdigest(),
        "v241_rules_sha256": hashlib.sha256(v241.RULES_PATH.read_bytes()).hexdigest(),
        "network_calls_by_preparer": 0,
    }
    FREEZE_PATH.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return lock


def verify_freeze() -> None:
    lock = json.loads(FREEZE_PATH.read_text())
    actual = {
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "base_bench_sha256": hashlib.sha256(BENCH_PATH.read_bytes()).hexdigest(),
        "v241_rules_sha256": hashlib.sha256(v241.RULES_PATH.read_bytes()).hexdigest(),
    }
    changed = {
        key: {"locked": lock.get(key), "actual": value}
        for key, value in actual.items()
        if lock.get(key) != value
    }
    if changed:
        raise RuntimeError(f"frozen live artifact changed: {changed}")


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
        if not SCHEMA_PATH.exists():
            emit_and_freeze()
        result = _synthetic_self_test()
        print(json.dumps(result, sort_keys=True))
        if result["summary"]["failed"]:
            return 1
    if args.dataset:
        verify_freeze()
        cases = (
            TARGETED_CASES
            if args.dataset == "targeted"
            else full_cases(args.start, args.end)
        )
        result = run_suite(cases)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
        print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
