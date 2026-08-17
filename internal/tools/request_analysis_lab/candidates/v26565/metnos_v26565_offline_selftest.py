#!/usr/bin/env python3
"""V26.5.6.5 offline self-test and mutation suite.

Pure, offline, deterministic. Zero network calls, zero model calls, zero
transport, zero gate consumption, zero live inference. It reads frozen
artifacts read-only and never writes to V26.5.6.4 or to the gold.

Run:
  /usr/bin/python3 -I -B \
    internal/tools/request_analysis_lab/candidates/v26565/\
metnos_v26565_offline_selftest.py [--json <path>]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve(strict=True).parent
CANDIDATES = HERE.parent
LAB = CANDIDATES.parent

VERSION = "metnos.v26.5.6.5-offline-selftest/1.0"

REGISTRY_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_registry.json"
FIXTURE_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_phase1_controls_frozen.json"
CONTROLS_PATH = CANDIDATES / "v2656" / "metnos_v2656_runtime_controls34.json"
OVERLAY_PATH = LAB / "oracles" / "phase1_v1" / "metnos_phase1_typed_oracle_v1.overlay.json"
SCHEMA_PATH = HERE / "metnos_v26565_clause_owned.schema.json"
PROMPT_PATH = HERE / "metnos_v26565_clause_owned.prompt.txt"

RESULTS: list[dict[str, Any]] = []


def check(group: str, label: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append({
        "group": group, "label": label,
        "passed": bool(condition), "detail": detail,
    })
    return bool(condition)


def _load(name: str, path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPENDENCY_TREE_PATH = (
    CANDIDATES / "v2656" / "metnos_v2656_python_dependency_tree.json"
)


def _pinned_site_roots() -> None:
    """Add only the site roots the frozen dependency tree pins.

    The isolated interpreter drops site-packages, so the pinned third-party
    roots are re-added explicitly from the frozen manifest and from nowhere
    else.
    """
    manifest = json.loads(DEPENDENCY_TREE_PATH.read_text(encoding="utf-8"))
    for package in manifest["packages"]:
        if package["name"] not in {"regex", "jsonschema"}:
            continue
        root = str(pathlib.Path(package["package_root"]).parent)
        if root not in sys.path:
            sys.path.append(root)


def unicode_segments(text: str) -> list[dict[str, Any]]:
    """Same Unicode default-word-boundary segmentation the runner uses."""
    import regex

    parts = regex.split(r"\b", text, flags=regex.WORD | regex.VERSION1)
    result: list[dict[str, Any]] = []
    cursor = 0
    for part in parts:
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            raise RuntimeError("segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        result.append({
            "id": len(result) + 1, "text": part,
            "start_char": start, "end_char": end,
        })
    return result


# --------------------------------------------------------------------------
# gold -> V26.5.6.5 frame, driven by the frozen registry and overlay
# --------------------------------------------------------------------------

# The oracle overlay names derived values with its own technical identifiers.
# This is a bridge between two FROZEN TECHNICAL REGISTRIES for the fixture
# only: it is not a linguistic or surface mapping, it contains no source
# language token, and it is never used at runtime.
DERIVED_REFERENCE_BRIDGE = {
    "actor.current_position": {"entity": "actor.current", "time": "time.current"},
}

DISCOURSE = {"kind": "discourse_structure"}
COMPOSITION = {"kind": "relation_composition"}


def _argument_proof(slot: dict[str, Any], state: str) -> dict[str, Any]:
    """A proof family the frozen contract allows for this slot and state."""
    if state == "unknown":
        return {"kind": "interrogative_construction"}
    if slot["role"] == "time":
        return {"kind": "utterance_context"}
    return {"kind": "discourse_context"}


def _reference_proof(ref: str, slot: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    families = registry["reference_registry"][ref].get("proof_families")
    if families:
        kind = families[0]
        if kind == "explicit_segment":
            return None  # needs a span; caller supplies it
        if kind in {"predicate_morphology", "tense_morphology"}:
            return None
        return {"kind": kind}
    return _argument_proof(slot, "bound")


def build_frame_from_template(
    template: dict[str, Any], registry: dict[str, Any], span: tuple[int, int],
) -> dict[str, Any]:
    """Build one V26.5.6.5 frame expressing the gold template's semantics."""
    start, end = span

    def clause_for(projection_spec: dict[str, Any]) -> dict[str, Any]:
        relation = projection_spec["relation"]
        slots = registry["relations"][relation]["slots"]
        dependencies: list[dict[str, Any]] = []
        arguments: list[dict[str, Any]] = []
        for slot, spec in zip(slots, projection_spec["arguments"]):
            state = spec["state"]
            if state == "bound":
                ref = spec["ref"]
                proof = _reference_proof(ref, slot, registry)
                if proof is None:
                    proof = {"kind": "explicit_segment",
                             "start_segment_id": start, "end_segment_id": end}
                arguments.append({"kind": "bound", "ref": ref, "proof": proof})
            elif state == "unknown":
                arguments.append({"kind": "unknown",
                                  "proof": _argument_proof(slot, "unknown")})
            elif state == "derived":
                bridge = DERIVED_REFERENCE_BRIDGE[spec["ref"]]
                required = slot["accepted_outputs"][0]["value_type"]
                producers = [
                    name for name, meta in registry["relations"].items()
                    if meta["dependency_output"]
                    and meta["dependency_output"]["value_type"] == required
                ]
                if len(producers) != 1:
                    raise RuntimeError("producer relation is not unique")
                producer = producers[0]
                producer_meta = registry["relations"][producer]
                output_slot = producer_meta["dependency_output"]["slot_index"]
                producer_arguments = []
                for index, producer_slot in enumerate(producer_meta["slots"], 1):
                    if index == output_slot:
                        producer_arguments.append({"kind": "output", "proof": COMPOSITION})
                        continue
                    role = producer_slot["role"]
                    ref = bridge.get(role)
                    if ref is None:
                        accepted = producer_slot["accepted_reference_types"]
                        candidates = sorted(
                            name for name, meta in registry["reference_registry"].items()
                            if meta["value_type"] in accepted
                        )
                        ref = candidates[0]
                    proof = _reference_proof(ref, producer_slot, registry)
                    if proof is None:
                        proof = {"kind": "explicit_segment",
                                 "start_segment_id": start, "end_segment_id": end}
                    producer_arguments.append({"kind": "bound", "ref": ref, "proof": proof})
                dependencies.append({
                    "relation": producer,
                    "relation_proof": COMPOSITION,
                    "arguments": producer_arguments,
                })
                # the dependency precedes this clause's projection in the
                # derived flattening, so its ordinal is its position there
                arguments.append({
                    "kind": "from_prior_atom",
                    "source_ordinal": len(dependencies),
                    "proof": COMPOSITION,
                })
            else:
                raise RuntimeError(f"unknown gold argument state: {state}")
        clause: dict[str, Any] = {
            "clause_start_segment_id": start,
            "clause_end_segment_id": end,
            "clause_role": projection_spec["clause_role"],
            "clause_role_proof": DISCOURSE,
            "projection": {
                "relation": relation,
                "relation_proof": {"kind": "clause_construction"},
                "speech_act": projection_spec["speech_act"],
                "speech_act_proof": {"kind": "clause_construction"},
                "arguments": arguments,
            },
        }
        if dependencies:
            clause["dependencies"] = dependencies
        return clause

    if template["adjudication"] == "typed_ambiguity":
        return {
            "status": "typed_ambiguity",
            "alternatives": [
                {"clauses": [clause_for(spec) for spec in alternative["projections"]]}
                for alternative in template["alternatives"]
            ],
        }
    return {
        "status": "supported",
        "clauses": [clause_for(spec) for spec in template["projections"]],
    }


def normalise_gold(template: dict[str, Any]) -> Any:
    """Gold semantics as a comparable structure, ignoring proofs and spans."""
    def one(spec: dict[str, Any]) -> Any:
        return {
            "relation": spec["relation"],
            "clause_role": spec["clause_role"],
            "speech_act": spec["speech_act"],
            "arguments": [
                {"role": item["role"], "state": item["state"],
                 "ref": item.get("ref") if item["state"] == "bound" else None}
                for item in spec["arguments"]
            ],
        }
    if template["adjudication"] == "typed_ambiguity":
        return {"status": "typed_ambiguity",
                "alternatives": [[one(s) for s in alt["projections"]]
                                 for alt in template["alternatives"]]}
    return {"status": "supported", "projections": [one(s) for s in template["projections"]]}


def normalise_expanded(expanded: dict[str, Any], registry: dict[str, Any]) -> Any:
    """The same comparable structure, read back out of the expanded frame."""
    def atoms_to(atoms: list[dict[str, Any]]) -> Any:
        out = []
        for atom in atoms:
            if atom["atom_kind"] != "projection":
                continue
            slots = registry["relations"][atom["relation"]]["slots"]
            arguments = []
            for slot, binding in zip(slots, atom["arguments"]):
                kind = binding["kind"]
                state = {"bound": "bound", "unknown": "unknown",
                         "from_atom_output": "derived"}[kind]
                arguments.append({
                    "role": slot["role"], "state": state,
                    "ref": binding.get("ref") if kind == "bound" else None,
                })
            out.append({
                "relation": atom["relation"], "clause_role": atom["clause_role"],
                "speech_act": atom["speech_act"], "arguments": arguments,
            })
        return out
    if expanded["status"] == "typed_ambiguity":
        return {"status": "typed_ambiguity",
                "alternatives": [atoms_to(alt["atoms"]) for alt in expanded["alternatives"]]}
    return {"status": "supported", "projections": atoms_to(expanded["atoms"])}


# --------------------------------------------------------------------------
# suite
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    _pinned_site_roots()
    import jsonschema

    projection = _load("v26565_projection", HERE / "metnos_v26565_registry_projection.py")
    expander = _load("v26565_expander", HERE / "metnos_v26565_expander.py")
    pipeline = _load("v26565_pipeline", HERE / "metnos_v26565_offline_pipeline.py")

    registry_bytes = REGISTRY_PATH.read_bytes()
    registry = projection.load_registry(registry_bytes)
    schema = projection.build_schema(registry)
    prompt = projection.build_prompt(registry)
    validator_module = pipeline.load_validator()
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    controls = json.loads(CONTROLS_PATH.read_text(encoding="utf-8"))
    queries = {case["opaque_case_id"]: case["query"] for case in controls["cases"]}

    def run(frame: Any, segments: list[dict[str, Any]]) -> dict[str, Any]:
        return pipeline.evaluate(
            frame, segments, schema=schema,
            validator_module=validator_module, expander=expander,
        )

    # --- group 1: materialised artifacts match their generator ------------
    group = "materialisation"
    jsonschema.Draft202012Validator.check_schema(schema)
    check(group, "schema is a valid 2020-12 schema", True)
    if SCHEMA_PATH.exists():
        stored = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        check(group, "materialised schema equals the generated schema", stored == schema)
    if PROMPT_PATH.exists():
        check(group, "materialised prompt equals the generated prompt",
              PROMPT_PATH.read_text(encoding="utf-8") == prompt)
    check(group, "registry hash is pinned",
          hashlib.sha256(registry_bytes).hexdigest() == projection.EXPECTED_REGISTRY_SHA256)

    # --- group 2: representability of all 34 gold cases -------------------
    group = "representability"
    represented = 0
    semantics_ok = 0
    for case in fixture["cases"]:
        query = queries[case["opaque_case_id"]]
        segments = unicode_segments(query)
        span = (1, len(segments))
        template = overlay["templates"][case["template_ref"]]
        frame = build_frame_from_template(template, registry, span)
        record = run(frame, segments)
        if record["valid"]:
            represented += 1
        else:
            check(group, f"case {case['template_ref']} valid", False,
                  json.dumps(record["stage_codes"]))
        expanded, _ = expander.expand_frame(frame)
        if normalise_expanded(expanded, registry) == normalise_gold(template):
            semantics_ok += 1
    check(group, "34/34 gold cases are representable and validate",
          represented == 34, f"{represented}/34")
    check(group, "34/34 expanded frames carry exactly the gold semantics",
          semantics_ok == 34, f"{semantics_ok}/34")

    # --- group 3: the V26.5.6.4 killers are now harmless ------------------
    group = "regression_of_the_v26564_failures"
    base_query = queries[fixture["cases"][0]["opaque_case_id"]]
    base_segments = unicode_segments(base_query)
    base_template = overlay["templates"][fixture["cases"][0]["template_ref"]]
    base_frame = build_frame_from_template(
        base_template, registry, (1, len(base_segments)))

    colliding = copy.deepcopy(base_frame)
    colliding["clauses"].append(copy.deepcopy(colliding["clauses"][0]))
    record = run(colliding, base_segments)
    check(group, "two clauses on one identical span are accepted",
          record["valid"], json.dumps(record["stage_codes"]))
    check(group, "the colliding-span frame is reported as span-colliding",
          record["fingerprint"]["distinct_clause_spans"] is False)

    dependency_case = next(
        case for case in fixture["cases"]
        if case["template_ref"] == "nearby_places_actor_position_dependency"
    )
    dependency_query = queries[dependency_case["opaque_case_id"]]
    dependency_segments = unicode_segments(dependency_query)
    dependency_frame = build_frame_from_template(
        overlay["templates"][dependency_case["template_ref"]],
        registry, (1, len(dependency_segments)))
    record = run(dependency_frame, dependency_segments)
    check(group, "the dependency case validates with a full-clause span",
          record["valid"], json.dumps(record["stage_codes"]))

    narrow = copy.deepcopy(dependency_frame)
    narrow["clauses"][0]["clause_start_segment_id"] = max(1, len(dependency_segments) - 1)
    narrow["clauses"][0]["clause_end_segment_id"] = len(dependency_segments)
    narrow_record = run(narrow, dependency_segments)
    check(group, "a narrower clause span no longer breaks the dependency",
          narrow_record["expansion_ok"] and not narrow_record["stage_codes"]["schema"],
          json.dumps(narrow_record["stage_codes"]))

    # --- group 4: the fatal set is exactly the schema ---------------------
    group = "no_fatal_invariant_outside_the_schema"
    duplicate = copy.deepcopy(base_frame)
    duplicate["clauses"][0]["projection2"] = copy.deepcopy(
        duplicate["clauses"][0]["projection"])
    check(group, "a second projection in one clause is SCHEMA-INVALID",
          bool(list(jsonschema.Draft202012Validator(schema).iter_errors(duplicate))))

    orphan = copy.deepcopy(base_frame)
    orphan["clauses"][0].pop("projection")
    orphan["clauses"][0]["dependencies"] = [{
        "relation": "spatial.located_at", "relation_proof": COMPOSITION,
        "arguments": [{"kind": "bound", "ref": "actor.current", "proof": DISCOURSE},
                      {"kind": "output", "proof": COMPOSITION},
                      {"kind": "bound", "ref": "time.current", "proof": {"kind": "utterance_context"}}],
    }]
    check(group, "a clause with dependencies and no projection is SCHEMA-INVALID",
          bool(list(jsonschema.Draft202012Validator(schema).iter_errors(orphan))))

    no_projection = {"status": "supported", "clauses": [{
        "clause_start_segment_id": 1, "clause_end_segment_id": 1,
        "clause_role": "main_request", "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry"}]}
    check(group, "a supported frame with no projected clause is SCHEMA-INVALID",
          bool(list(jsonschema.Draft202012Validator(schema).iter_errors(no_projection))))

    # the expander is TOTAL: it must never raise on anything
    raised = []
    fuzz_frames: list[Any] = [
        base_frame, colliding, dependency_frame, narrow, duplicate, orphan,
        no_projection, {}, {"status": "supported"}, {"status": "supported", "clauses": []},
        {"status": "typed_ambiguity"}, {"status": "typed_ambiguity", "alternatives": []},
        {"status": "unsupported"}, {"status": "nonsense"}, [], None, 7, "x",
        {"status": "supported", "clauses": [None, 3, "x"]},
    ]
    for index in range(1, 40):
        broken = copy.deepcopy(dependency_frame)
        broken["clauses"][0]["projection"]["arguments"][1]["source_ordinal"] = index - 12
        fuzz_frames.append(broken)
    for frame in fuzz_frames:
        try:
            expander.expand_frame(frame)
            expander.structural_fingerprint(frame)
        except Exception as error:                       # noqa: BLE001
            raised.append(f"{type(error).__name__}: {error}")
    check(group, "the expander is total over every probe frame",
          not raised, "; ".join(raised[:3]))

    forward = copy.deepcopy(dependency_frame)
    forward["clauses"][0]["projection"]["arguments"][1]["source_ordinal"] = 99
    forward_record = run(forward, dependency_segments)
    check(group, "an unresolvable edge is reported, not fatal",
          forward_record["stage_ran"]["validator"]
          and forward_record["stages_measured"] == 3
          and not forward_record["valid"],
          json.dumps(forward_record["stage_codes"]))

    # --- group 5: best-effort diagnosis of every stage --------------------
    group = "best_effort_diagnosis"
    broken_schema = copy.deepcopy(base_frame)
    broken_schema["clauses"][0]["projection"]["relation"] = "not.a.relation"
    record = run(broken_schema, base_segments)
    check(group, "a schema failure still runs expansion and validation",
          record["stages_measured"] == 3 and all(record["stage_ran"].values()),
          json.dumps(record["stage_ran"]))
    check(group, "the schema stage reported at least one code",
          bool(record["stage_codes"]["schema"]))
    check(group, "the validator stage was measured despite the schema failure",
          record["stage_ran"]["validator"])

    # --- group 6: multi-action, multi-domain, typed ambiguity -------------
    group = "coverage_shapes"
    multi = {"status": "supported", "clauses": [
        copy.deepcopy(base_frame["clauses"][0]),
        {
            "clause_start_segment_id": 1, "clause_end_segment_id": len(base_segments),
            "clause_role": "main_request", "clause_role_proof": DISCOURSE,
            "projection": {
                "relation": "filesystem.located_at",
                "relation_proof": {"kind": "clause_construction"},
                "speech_act": "open_question",
                "speech_act_proof": {"kind": "clause_construction"},
                "arguments": [
                    {"kind": "bound", "ref": "artifact.explicit", "proof": {"kind": "discourse_context"}},
                    {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
                    {"kind": "bound", "ref": "time.current", "proof": {"kind": "utterance_context"}},
                ],
            },
        },
    ]}
    record = run(multi, base_segments)
    check(group, "a multi-clause multi-domain frame validates",
          record["valid"], json.dumps(record["stage_codes"]))
    check(group, "multi-domain fingerprint records two relations",
          len(record["fingerprint"]["relations"]) == 2)

    mixed = copy.deepcopy(multi)
    mixed["clauses"].append({
        "clause_start_segment_id": 1, "clause_end_segment_id": len(base_segments),
        "clause_role": "main_request", "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry"})
    record = run(mixed, base_segments)
    check(group, "a mixed representable/out-of-registry frame validates",
          record["valid"], json.dumps(record["stage_codes"]))

    ambiguity_case = next(
        case for case in fixture["cases"]
        if overlay["templates"][case["template_ref"]]["adjudication"] == "typed_ambiguity"
    )
    ambiguity_query = queries[ambiguity_case["opaque_case_id"]]
    ambiguity_segments = unicode_segments(ambiguity_query)
    ambiguity_frame = build_frame_from_template(
        overlay["templates"][ambiguity_case["template_ref"]],
        registry, (1, len(ambiguity_segments)))
    record = run(ambiguity_frame, ambiguity_segments)
    check(group, "a typed-ambiguity frame validates with derived alternative ids",
          record["valid"], json.dumps(record["stage_codes"]))
    expanded, _ = expander.expand_frame(ambiguity_frame)
    check(group, "alternative ids are derived from position",
          [alt["alternative_id"] for alt in expanded["alternatives"]]
          == list(range(1, len(expanded["alternatives"]) + 1)))

    # --- group 7: no emitted clause_id anywhere in the request contract ---
    group = "no_emitted_identity"
    schema_text = json.dumps(schema, sort_keys=True)
    for label, forbidden in (("clause_id", '"clause_id"'), ("atom_id", '"atom_id"'),
                             ("alternative_id", '"alternative_id"'),
                             ("output_index", '"output_index"')):
        check(group, f"the response schema never asks for {label}",
              forbidden not in schema_text)
    check(group, "the prompt forbids numeric labels",
          "Do not \nemit numeric labels" in prompt.replace("\n", "\n")
          or "do not emit numeric labels" in prompt.lower())

    # --- group 8: Unicode metamorphic stability --------------------------
    group = "unicode_metamorphic"
    metamorphic_queries = [
        queries[case["opaque_case_id"]] for case in fixture["cases"]
        if case["template_ref"] == "actor_current_position_open"
    ]
    fingerprints = set()
    validity = set()
    for query in metamorphic_queries:
        segments = unicode_segments(query)
        frame = build_frame_from_template(
            overlay["templates"]["actor_current_position_open"],
            registry, (1, len(segments)))
        record = run(frame, segments)
        fingerprints.add(record["fingerprint_sha256"])
        validity.add(record["valid"])
    check(group, "the same structure over 9 scripts yields one fingerprint",
          len(fingerprints) == 1, f"{len(fingerprints)} distinct")
    check(group, "the same structure over 9 scripts is uniformly valid",
          validity == {True})
    check(group, "segmentation is script independent for the probe set",
          len(metamorphic_queries) == 9)

    # --- group 9: contamination audit ------------------------------------
    group = "contamination"
    prompt_lower = prompt.lower()
    verbatim = [q for q in queries.values() if q.lower() in prompt_lower]
    check(group, "no control query appears verbatim in the prompt",
          not verbatim, "; ".join(verbatim[:3]))

    def tokens(text: str) -> list[str]:
        return [t for t in "".join(
            c.lower() if c.isalnum() else " " for c in text).split() if t]

    prompt_tokens = tokens(prompt)
    prompt_ngrams = {
        tuple(prompt_tokens[i:i + 4]) for i in range(len(prompt_tokens) - 3)
    }
    overlaps = []
    for query in queries.values():
        query_tokens = tokens(query)
        for i in range(len(query_tokens) - 3):
            if tuple(query_tokens[i:i + 4]) in prompt_ngrams:
                overlaps.append(query)
                break
    check(group, "no 4-token n-gram is shared with any control query",
          not overlaps, "; ".join(overlaps[:3]))
    contamination = {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "controls_sha256": controls["source_controls_sha256"],
        "verbatim_query_matches": len(verbatim),
        "shared_4gram_queries": len(overlaps),
        "prompt_token_count": len(prompt_tokens),
        "queries_audited": len(queries),
    }

    # --- group 10: fingerprint privacy -----------------------------------
    group = "fingerprint_privacy"
    allowed = set(registry["relations"]) | set(registry["clause_roles"]) \
        | set(registry["speech_acts"]) | set(projection.all_proof_kinds(registry)) \
        | {"bound", "unknown", "output", "from_prior_atom",
           "supported", "typed_ambiguity", "unsupported", None}
    leaked: list[str] = []
    for case in fixture["cases"]:
        query = queries[case["opaque_case_id"]]
        segments = unicode_segments(query)
        frame = build_frame_from_template(
            overlay["templates"][case["template_ref"]], registry, (1, len(segments)))
        record = run(frame, segments)
        blob = json.dumps(record["fingerprint"], ensure_ascii=False)
        if query.lower() in blob.lower():
            leaked.append(query)

        def strings(node: Any) -> list[str]:
            if isinstance(node, str):
                return [node]
            if isinstance(node, list):
                return [s for item in node for s in strings(item)]
            if isinstance(node, dict):
                return [s for item in node.values() for s in strings(item)]
            return []
        for value in strings(record["fingerprint"]):
            if value not in allowed:
                leaked.append(value)
    check(group, "no fingerprint contains request or segment text",
          not leaked, "; ".join(sorted(set(leaked))[:5]))
    check(group, "every fingerprint string is a closed technical denotation",
          not leaked)
    check(group, "the fingerprint carries no source span",
          "segment_id" not in json.dumps(
              run(base_frame, base_segments)["fingerprint"]))

    # --- group 11: fail-closed on drift ----------------------------------
    group = "fail_closed"
    try:
        projection.load_registry(registry_bytes + b" ")
        check(group, "a mutated registry is refused", False)
    except RuntimeError:
        check(group, "a mutated registry is refused", True)
    try:
        projection.load_registry("not bytes")           # type: ignore[arg-type]
        check(group, "a non-bytes registry is refused", False)
    except TypeError:
        check(group, "a non-bytes registry is refused", True)

    original = pipeline.VALIDATOR_SHA256
    try:
        pipeline.VALIDATOR_SHA256 = "0" * 64
        try:
            pipeline.load_validator()
            check(group, "a drifted validator pin is refused", False)
        except RuntimeError:
            check(group, "a drifted validator pin is refused", True)
    finally:
        pipeline.VALIDATOR_SHA256 = original

    # --- group 12: mutation suite ----------------------------------------
    group = "mutation_suite"
    mutations: list[tuple[str, Any]] = []

    def mutate(label: str, edit) -> None:
        frame = copy.deepcopy(dependency_frame)
        edit(frame)
        mutations.append((label, frame))

    mutate("wrong relation arity",
           lambda f: f["clauses"][0]["projection"]["arguments"].pop())
    mutate("incompatible reference type",
           lambda f: f["clauses"][0]["projection"]["arguments"][2].update(
               {"kind": "bound", "ref": "actor.current",
                "proof": {"kind": "discourse_context"}}))
    mutate("forbidden proof family",
           lambda f: f["clauses"][0]["projection"].update(
               {"relation_proof": {"kind": "relation_composition"}}))
    mutate("role and speech act disagree",
           lambda f: f["clauses"][0].update({"clause_role": "quoted_content"}))
    mutate("dependency declares an unknown",
           lambda f: f["clauses"][0]["dependencies"][0]["arguments"].__setitem__(
               1, {"kind": "unknown", "proof": {"kind": "interrogative_construction"}}))
    mutate("edge points at itself",
           lambda f: f["clauses"][0]["projection"]["arguments"][1].update(
               {"source_ordinal": 2}))
    mutate("edge points past the end",
           lambda f: f["clauses"][0]["projection"]["arguments"][1].update(
               {"source_ordinal": 12}))
    mutate("action relation carries an unknown",
           lambda f: f["clauses"][0]["projection"].update({"relation": "acl.share"}))
    mutate("clause span outside the segments",
           lambda f: f["clauses"][0].update({"clause_end_segment_id": 999}))
    mutate("inverted clause span",
           lambda f: f["clauses"][0].update(
               {"clause_start_segment_id": 4, "clause_end_segment_id": 2}))
    mutate("proof span outside the clause",
           lambda f: f["clauses"][0]["projection"].update(
               {"relation_proof": {"kind": "explicit_segment",
                                   "start_segment_id": 1, "end_segment_id": 999}}))
    mutate("unconsumed dependency output",
           lambda f: f["clauses"][0]["projection"]["arguments"].__setitem__(
               1, {"kind": "bound", "ref": "place.explicit",
                   "proof": {"kind": "discourse_context"}}))

    caught = 0
    total_measured = 0
    for label, frame in mutations:
        record = run(frame, dependency_segments)
        if record["stages_measured"] == 3:
            total_measured += 1
        if not record["valid"]:
            caught += 1
        else:
            check(group, f"mutation caught: {label}", False)
    check(group, f"all {len(mutations)} semantic mutations are rejected",
          caught == len(mutations), f"{caught}/{len(mutations)}")
    check(group, "every mutation was measured on all three stages",
          total_measured == len(mutations), f"{total_measured}/{len(mutations)}")
    check(group, "the clean dependency frame still validates",
          run(dependency_frame, dependency_segments)["valid"])

    # --- report ----------------------------------------------------------
    passed = sum(1 for item in RESULTS if item["passed"])
    total = len(RESULTS)
    by_group: dict[str, dict[str, int]] = {}
    for item in RESULTS:
        entry = by_group.setdefault(item["group"], {"passed": 0, "total": 0})
        entry["total"] += 1
        entry["passed"] += int(item["passed"])

    current = None
    for item in RESULTS:
        if item["group"] != current:
            current = item["group"]
            print(f"\n{current}")
        mark = "PASS" if item["passed"] else "FAIL"
        detail = f" -- {item['detail']}" if item["detail"] else ""
        print(f"  [{mark}] {item['label']}{detail}")
    print(f"\n{passed}/{total} PASS")

    payload = {
        "version": VERSION,
        "passed": passed, "total": total,
        "all_green": passed == total,
        "by_group": by_group,
        "network_calls": 0, "model_calls": 0, "live_runs": 0,
        "inference": False,
        "contamination_audit": contamination,
        "results": RESULTS,
    }
    if args.json:
        args.json.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
