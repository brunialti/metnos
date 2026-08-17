#!/usr/bin/env python3
"""V25.2 shadow experiment: compositional desired-observation contract.

This candidate replaces the atomic current-location binding with mandatory,
flat, typed semantic dimensions.  A catalog binding is derived only after the
complete relation signature type-checks.  No validator/projector reads source
text, lemmas, glosses, languages, or benchmark identifiers.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema

import sys
sys.path.insert(0, "/tmp")
import metnos_v25_live_runner as v25  # noqa: E402


VERSION = "metnos.v25.2-desired-observation/1.0"
RUNNER_PATH = Path(__file__)
SCHEMA_PATH = Path("/tmp/metnos_v252_relation_goal.schema.json")
PROMPT_PATH = Path("/tmp/metnos_v252_relation_goal.prompt.txt")
CONTROLS_PATH = Path("/tmp/metnos_question_focus_negative_controls_v1.json")
MATRIX_PATH = Path("/tmp/metnos_v252_relation_goal_controls_frozen.json")
MUTATIONS_PATH = Path("/tmp/metnos_v252_relation_goal_mutations_frozen.json")
FREEZE_PATH = Path("/tmp/metnos_v252_relation_goal.freeze.json")


SPEECH_ACTS = [
    "not_applicable", "open_question", "polar_question", "imperative",
    "condition", "quote", "description",
]
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
FOCUS_TYPES = [
    "none", "spatial_position", "person", "identity", "boolean", "host",
    "entity_collection", "permission_result", "resource_locator",
    "workflow_progress",
]
SUBJECT_REFS = [
    "none", "current_actor", "explicit_entity", "unknown_entity",
    "deictic_context", "current_actor_location_operand", "quoted_actor",
]
SUBJECT_TYPES = [
    "none", "actor", "person", "place", "file", "process", "collection",
    "resource",
]
TIME_SCOPES = ["none", "current", "historical", "contextual"]
EVIDENCE_KINDS = [
    "none", "explicit_span", "predicate_morphology", "speaker_context",
    "clause_span",
]


def observation_schema() -> dict[str, Any]:
    props: dict[str, Any] = {
        "speech_act": {"type": "string", "enum": SPEECH_ACTS},
        "relation": {"type": "string", "enum": RELATIONS},
        "focus_role": {"type": "string", "enum": FOCUS_ROLES},
        "focus_type": {"type": "string", "enum": FOCUS_TYPES},
        "subject_ref": {"type": "string", "enum": SUBJECT_REFS},
        "subject_type": {"type": "string", "enum": SUBJECT_TYPES},
        "time_scope": {"type": "string", "enum": TIME_SCOPES},
        "focus_evidence_kind": {"type": "string", "enum": EVIDENCE_KINDS},
        "focus_start_token_id": {"type": "integer", "minimum": 0},
        "focus_end_token_id": {"type": "integer", "minimum": 0},
        "subject_evidence_kind": {"type": "string", "enum": EVIDENCE_KINDS},
        "subject_start_token_id": {"type": "integer", "minimum": 0},
        "subject_end_token_id": {"type": "integer", "minimum": 0},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": list(props),
    }


def live_schema() -> dict[str, Any]:
    schema = copy.deepcopy(v25.live_schema())
    head = schema["properties"]["semantic_heads"]["items"]
    head["properties"].pop("question_binding")
    head["required"].remove("question_binding")
    head["properties"]["desired_observation"] = observation_schema()
    head["required"].append("desired_observation")
    return schema


QUESTION_BLOCK_START = "For every semantic head emit `question_binding`, a tagged union."
QUESTION_BLOCK_END = "For every semantic head emit `argument_binding`, also a tagged union."

OBSERVATION_PROMPT = """For every semantic head emit `desired_observation`. It is a mandatory,
flat relation contract; it is not a route, capability ID, rewritten query, or
surface trigger. Determine every field from the untouched clause before Phase
2 routing. Never copy words into enum fields.

- `speech_act` records how the clause is used. `open_question` asks for an
  unknown answer variable; `polar_question` asks for a boolean. Imperatives,
  conditions, quotations, and descriptions retain their distinct values.
- `relation` names the technical relation, not the lexical predicate.
  `spatial.located_at` is geographic placement of an entity at a spatial
  position and time. Keep filesystem location, process hosting, nearby search,
  document position, workflow progress, identity, sharing, and movement in
  their distinct relation families.
- `focus_role` is the argument requested as the answer, not merely an argument
  mentioned in the clause. `focus_type` is that answer variable's value type.
- `subject_ref` identifies whose attribute is queried. Use `current_actor` only
  for the user/device/session participating in this turn; an explicit or
  unknown third party is not the current actor. An actor location used only as
  an input to another request is `current_actor_location_operand`.
- `time_scope=current` means the requested observation is now/latest, never a
  historical or merely contextual time.
- Focus and subject evidence are independent. `explicit_span` and
  `clause_span` use inclusive positive token ranges. `predicate_morphology`
  may ground a subject encoded by inflection and uses the predicate token span.
  `speaker_context` grounds an unspoken current actor and uses 0,0. `none`
  always uses 0,0. Overlapping evidence is valid.
- Use relation=none and neutral focus/subject/time/evidence only when the
  predicate does not express a relation or requested observation. Do not erase
  a relation merely because its speech act is condition, quote, description,
  polar question, or imperative.

The catalog projector derives `runtime.current_location` only from this full
signature on a request head: open_question + spatial.located_at + focus
location:spatial_position + subject current_actor:actor + time current. The
model never emits that binding ID. Nearby results, identity, another entity's
location, historical location, filesystem/runtime/document/workflow position,
quoted text, conditions, and polar questions cannot satisfy the signature.

"""


def system_prompt() -> str:
    base = v25.system_prompt()
    start = base.index(QUESTION_BLOCK_START)
    end = base.index(QUESTION_BLOCK_END, start)
    return base[:start] + OBSERVATION_PROMPT + base[end:]


def _span_ok(start: Any, end: Any, count: int) -> bool:
    return isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= count


def _evidence_errors(obs: dict[str, Any], prefix: str, token_count: int) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for role in ("focus", "subject"):
        kind = obs[f"{role}_evidence_kind"]
        start = obs[f"{role}_start_token_id"]
        end = obs[f"{role}_end_token_id"]
        if kind in {"none", "speaker_context"}:
            ok = start == 0 and end == 0
        else:
            ok = _span_ok(start, end, token_count)
        if not ok:
            errors.append({
                "code": f"observation_{role}_evidence",
                "path": f"{prefix}/desired_observation",
                "message": f"{role} evidence kind/span mismatch",
                "retryable": True,
            })
    return errors


def is_current_location_goal(head: dict[str, Any]) -> bool:
    obs = head["desired_observation"]
    return (
        head.get("role") == "request"
        and obs["speech_act"] == "open_question"
        and obs["relation"] == "spatial.located_at"
        and obs["focus_role"] == "location"
        and obs["focus_type"] == "spatial_position"
        and obs["subject_ref"] == "current_actor"
        and obs["subject_type"] == "actor"
        and obs["time_scope"] == "current"
        and obs["focus_evidence_kind"] != "none"
        and obs["subject_evidence_kind"] != "none"
    )


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

    base = copy.deepcopy(frame)
    for head in base["semantic_heads"]:
        head.pop("desired_observation")
        head["question_binding"] = {"kind": "none"}
    base_validation = v25.validate_live_frame(base, tokens)
    errors.extend(base_validation["errors"])

    for index, (head, route) in enumerate(zip(frame["semantic_heads"], frame["predicates"])):
        prefix = f"semantic_heads/{index}"
        obs = head["desired_observation"]
        errors.extend(_evidence_errors(obs, prefix, len(tokens)))
        if obs["relation"] == "none":
            neutral = (
                obs["focus_role"] == "none" and obs["focus_type"] == "none"
                and obs["subject_ref"] == "none" and obs["subject_type"] == "none"
                and obs["time_scope"] == "none"
                and obs["focus_evidence_kind"] == "none"
                and obs["subject_evidence_kind"] == "none"
            )
            if not neutral:
                errors.append({
                    "code": "observation_none_not_neutral", "path": prefix,
                    "message": "relation none requires neutral relation arguments",
                    "retryable": True,
                })
        if is_current_location_goal(head):
            patient = head.get("patient", {})
            if patient.get("object") != "places":
                errors.append({
                    "code": "observation_location_patient", "path": prefix,
                    "message": "current geographic position requires places patient",
                    "retryable": True,
                })
            if route.get("verb") != "get" or route.get("object") != "places":
                errors.append({
                    "code": "observation_location_route", "path": f"predicates/{index}",
                    "message": "derived current-location contract requires get/places",
                    "retryable": True,
                })
    return {
        "valid": not errors,
        "errors": errors,
        "base_normalizations": base_validation.get("base_normalizations", []),
    }


def to_v241(frame: dict[str, Any], tokens: list[str]) -> dict[str, Any]:
    adapted = copy.deepcopy(frame)
    for head in adapted["semantic_heads"]:
        obs = head.pop("desired_observation")
        if is_current_location_goal({**head, "desired_observation": obs}):
            starts = [x for x in [obs["focus_start_token_id"], obs["subject_start_token_id"]] if x > 0]
            ends = [x for x in [obs["focus_end_token_id"], obs["subject_end_token_id"]] if x > 0]
            head["question_binding"] = {
                "kind": "catalog_query",
                "binding_id": "runtime.current_location",
                "start_token_id": min(starts) if starts else head["predicate_anchor_token_id"],
                "end_token_id": max(ends) if ends else head["predicate_anchor_token_id"],
            }
        else:
            head["question_binding"] = {"kind": "none"}
    return v25.to_v241(adapted, tokens)


def request_body(query: str, *, seed: int, feedback: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = v25.bench.tokenize(query)
    payload: dict[str, Any] = {
        "original_request": query,
        "tokens": [{"id": i, "text": token} for i, token in enumerate(tokens, 1)],
    }
    if feedback:
        payload["validation_feedback"] = feedback
    return {
        "model": "local", "temperature": 0, "seed": seed, "max_tokens": 3800,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "grounded_request_analysis_v252", "schema": live_schema(), "strict": True,
        }},
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def call_once(query: str, *, seed: int, feedback: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    body = request_body(query, seed=seed, feedback=feedback)
    endpoint = v25.bench.tier_endpoint(v25.bench.tier_for("intent.extract")).rstrip("/")
    request = urllib.request.Request(endpoint + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw), {"raw": raw, "latency_ms": (time.perf_counter()-started)*1000, "error": ""}
    except Exception as exc:
        return {}, {"raw": f"{type(exc).__name__}: {exc}", "latency_ms": (time.perf_counter()-started)*1000, "error": "transport_or_json"}


def run_case(query: str, max_attempts: int = 2) -> dict[str, Any]:
    tokens = v25.bench.tokenize(query)
    attempts = []
    feedback: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        frame, transport = call_once(query, seed=71+attempt, feedback=feedback)
        validation = validate_live_frame(frame, tokens) if not transport["error"] else {
            "valid": False, "errors": [{"code": transport["error"], "path": "", "message": transport["raw"], "retryable": True}]}
        attempts.append({"attempt": attempt, "frame": frame, "transport": transport, "validation": validation})
        if validation["valid"]:
            converted = to_v241(frame, tokens)
            projection = v25.v241.project_v241(converted, tokens, mode="research", delta=v25.v241.load_delta())
            return {"status": projection["status"], "query": query, "tokens": tokens,
                    "attempts": attempts, "frame": frame, "v241_frame": converted,
                    "signature": projection.get("signature"), "projection": projection}
        feedback = [{k: e.get(k, "") for k in ("code", "path", "message")} for e in validation["errors"]]
    return {"status": "fail_closed", "query": query, "tokens": tokens, "attempts": attempts, "signature": None}


def _expected_binding(case: dict[str, Any]) -> bool:
    return bool(case["expect_binding"])


def controls() -> list[dict[str, Any]]:
    return json.loads(CONTROLS_PATH.read_text())["cases"]


def run_controls(start: int = 1, end: int | None = None) -> dict[str, Any]:
    rows = []
    cases = controls()[start-1:end]
    for case in cases:
        result = run_case(case["query"])
        heads = (result.get("frame") or {}).get("semantic_heads", [])
        active = [is_current_location_goal(h) for h in heads]
        predicted = any(active)
        rows.append({"case": case, "result": result, "predicted_binding": predicted,
                     "binding_ok": predicted == _expected_binding(case),
                     "active_head_count": sum(active)})
    latencies = [a["transport"]["latency_ms"] for r in rows for a in r["result"].get("attempts", [])]
    return {"version": VERSION, "freeze": json.loads(FREEZE_PATH.read_text()),
            "summary": {"records": len(rows), "valid": sum(r["result"]["status"] == "projected" for r in rows),
                        "binding_exact": sum(r["binding_ok"] for r in rows),
                        "positive_recall": sum(r["predicted_binding"] and r["case"]["expect_binding"] for r in rows),
                        "negative_leakage": sum(r["predicted_binding"] and not r["case"]["expect_binding"] for r in rows),
                        "retries": sum(len(r["result"].get("attempts", []))-1 for r in rows),
                        "latency_ms_total": sum(latencies)}, "records": rows}


def synthetic_mutations() -> dict[str, Any]:
    base_obs = {
        "speech_act": "open_question", "relation": "spatial.located_at",
        "focus_role": "location", "focus_type": "spatial_position",
        "subject_ref": "current_actor", "subject_type": "actor", "time_scope": "current",
        "focus_evidence_kind": "explicit_span", "focus_start_token_id": 1, "focus_end_token_id": 1,
        "subject_evidence_kind": "predicate_morphology", "subject_start_token_id": 2, "subject_end_token_id": 2,
    }
    dimensions = {
        "speech_act": "condition", "relation": "filesystem.located_at",
        "focus_role": "identity", "focus_type": "identity",
        "subject_ref": "explicit_entity", "subject_type": "person", "time_scope": "historical",
        "focus_evidence_kind": "none", "subject_evidence_kind": "none",
    }
    results = [{"id": "base_positive", "pass": is_current_location_goal({"role": "request", "desired_observation": base_obs})}]
    for field, value in dimensions.items():
        obs = copy.deepcopy(base_obs); obs[field] = value
        results.append({"id": f"mutate_{field}", "pass": not is_current_location_goal({"role": "request", "desired_observation": obs})})
    results.append({"id": "mutate_role", "pass": not is_current_location_goal({"role": "description", "desired_observation": base_obs})})
    return {"tests": results, "summary": {"tests": len(results), "passed": sum(r["pass"] for r in results)}}


def freeze() -> dict[str, Any]:
    SCHEMA_PATH.write_text(json.dumps(live_schema(), ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    PROMPT_PATH.write_text(system_prompt())
    matrix = {"version": VERSION, "source_controls_sha256": hashlib.sha256(CONTROLS_PATH.read_bytes()).hexdigest(),
              "cases": [{"id": c["id"], "expect_binding": c["expect_binding"], "expected_tuple": c["tuple"]} for c in controls()]}
    MATRIX_PATH.write_text(json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    mutations = synthetic_mutations()
    MUTATIONS_PATH.write_text(json.dumps(mutations, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    lock = {"version": VERSION, "network_calls_before_freeze": 0,
            "runner_sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
            "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
            "prompt_sha256": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
            "controls_sha256": hashlib.sha256(CONTROLS_PATH.read_bytes()).hexdigest(),
            "matrix_sha256": hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest(),
            "mutations_sha256": hashlib.sha256(MUTATIONS_PATH.read_bytes()).hexdigest(),
            "v25_runner_sha256": hashlib.sha256(Path(v25.__file__).read_bytes()).hexdigest()}
    FREEZE_PATH.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    return lock


def verify_freeze() -> None:
    lock = json.loads(FREEZE_PATH.read_text())
    paths = {"runner_sha256": RUNNER_PATH, "schema_sha256": SCHEMA_PATH,
             "prompt_sha256": PROMPT_PATH, "controls_sha256": CONTROLS_PATH,
             "matrix_sha256": MATRIX_PATH, "mutations_sha256": MUTATIONS_PATH}
    changed = {k: (lock[k], hashlib.sha256(p.read_bytes()).hexdigest()) for k,p in paths.items()
               if lock[k] != hashlib.sha256(p.read_bytes()).hexdigest()}
    if changed: raise RuntimeError(f"freeze changed: {changed}")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--self-test", action="store_true"); ap.add_argument("--controls", action="store_true")
    ap.add_argument("--start", type=int, default=1); ap.add_argument("--end", type=int); ap.add_argument("--output")
    args = ap.parse_args()
    if args.freeze: print(json.dumps(freeze(), sort_keys=True))
    if args.self_test:
        result = synthetic_mutations(); print(json.dumps(result, sort_keys=True))
        if result["summary"]["passed"] != result["summary"]["tests"]: return 1
    if args.controls:
        verify_freeze(); result = run_controls(args.start, args.end)
        if args.output: Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
        print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
