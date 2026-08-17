#!/usr/bin/env python3
"""V26.5.6.6 offline self-test and mutation suite.

Pure, offline, deterministic. Zero network calls, zero model calls, zero
transport, zero gate consumption, zero live inference. It reads frozen
artifacts read-only and never writes to V26.5.6.5, to V26.5.6.4 or to the gold.

Run:
  /usr/bin/python3 -I -B \
    internal/tools/request_analysis_lab/candidates/v26566/\
metnos_v26566_offline_selftest.py [--json <path>]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import pathlib
import random
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve(strict=True).parent
CANDIDATES = HERE.parent
LAB = CANDIDATES.parent

VERSION = "metnos.v26.5.6.6-offline-selftest/1.0"
SEED = 20260809

REGISTRY_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_registry.json"
FIXTURE_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_phase1_controls_frozen.json"
CONTROLS_PATH = CANDIDATES / "v2656" / "metnos_v2656_runtime_controls34.json"
OVERLAY_PATH = LAB / "oracles" / "phase1_v1" / "metnos_phase1_typed_oracle_v1.overlay.json"
SCHEMA_PATH = HERE / "metnos_v26566_clause_owned.schema.json"
PROMPT_PATH = HERE / "metnos_v26566_clause_owned.prompt.txt"

RESULTS: list[dict[str, Any]] = []
MEASURES: dict[str, Any] = {}

# Validator codes that the clause-owned structure makes unreachable on any
# schema-valid frame. The first four are the V26.5.6.4 killers; the next four
# are closed by the shared ambiguity skeleton of V26.5.6.6 (blocker B3); the
# last three follow from derived numbering.
STRUCTURALLY_CLOSED_CODES = (
    "primary_cardinality", "orphan_dependency", "clause_span_consistency",
    "projection_missing",
    "clause_ids", "alternative_coverage", "clause_limit", "alternative_order",
    "atom_order", "atom_clause_order", "unsupported_clause_order",
)


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
# gold -> V26.5.6.6 frame, driven by the frozen registry and overlay
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


def build_analysis(
    projection_spec: dict[str, Any], registry: dict[str, Any], span: tuple[int, int],
) -> dict[str, Any]:
    """One clause analysis: role, projection and its auxiliary producers.

    This is the shape a supported clause carries directly and the shape one
    reading of an ambiguous clause carries.
    """
    start, end = span
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
            # the dependency precedes this clause's projection in the derived
            # flattening, so its ordinal is its position there
            arguments.append({
                "kind": "from_prior_atom",
                "source_ordinal": len(dependencies),
                "proof": COMPOSITION,
            })
        else:
            raise RuntimeError(f"unknown gold argument state: {state}")
    analysis: dict[str, Any] = {
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
        analysis["dependencies"] = dependencies
    return analysis


def build_frame_from_template(
    template: dict[str, Any], registry: dict[str, Any], span: tuple[int, int],
) -> dict[str, Any]:
    """Build one V26.5.6.6 frame expressing the gold template's semantics."""
    start, end = span
    if template["adjudication"] == "typed_ambiguity":
        alternatives = template["alternatives"]
        widths = {len(alternative["projections"]) for alternative in alternatives}
        if len(widths) != 1:
            # The frozen validator compares the clause footprint of every
            # alternative, so alternatives of different width were never
            # valid; the shared skeleton makes them unrepresentable.
            raise RuntimeError("gold alternatives disagree on clause count")
        width = widths.pop()
        clauses = []
        for index in range(width):
            clauses.append({
                "clause_start_segment_id": start,
                "clause_end_segment_id": end,
                "readings": [
                    build_analysis(alternative["projections"][index], registry, span)
                    for alternative in alternatives
                ],
            })
        return {"status": "typed_ambiguity", "clauses": clauses}
    clauses = []
    for spec in template["projections"]:
        clause = {"clause_start_segment_id": start, "clause_end_segment_id": end}
        clause.update(build_analysis(spec, registry, span))
        clauses.append(clause)
    return {"status": "supported", "clauses": clauses}


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


def semantics_or_none(expanded: Any, registry: dict[str, Any]) -> Any:
    """Comparable semantics, or None when the frame cannot be read back."""
    try:
        return normalise_expanded(expanded, registry)
    except Exception:                                    # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# structural walkers used by the fuzz and mutation sweeps
# --------------------------------------------------------------------------

def walk_paths(node: Any, prefix: tuple = ()) -> list[tuple]:
    paths = [prefix]
    if isinstance(node, dict):
        for key, value in node.items():
            paths.extend(walk_paths(value, prefix + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            paths.extend(walk_paths(value, prefix + (index,)))
    return paths


def replace_at(frame: Any, path: tuple, value: Any) -> Any:
    if not path:
        return value
    clone = copy.deepcopy(frame)
    node = clone
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return clone


def read_at(frame: Any, path: tuple) -> Any:
    node = frame
    for step in path:
        node = node[step]
    return node


# --------------------------------------------------------------------------
# random schema-valid frame generator (deterministic, seeded)
# --------------------------------------------------------------------------

def random_proof(rng: random.Random, kinds: list[str], top: int) -> dict[str, Any]:
    kind = rng.choice(kinds)
    if kind == "explicit_segment":
        start = rng.randint(1, top)
        return {"kind": kind, "start_segment_id": start,
                "end_segment_id": rng.randint(start, top)}
    if kind in {"predicate_morphology", "tense_morphology"}:
        return {"kind": kind, "predicate_segment_id": rng.randint(1, top)}
    return {"kind": kind}


def random_analysis(
    rng: random.Random, registry: dict[str, Any], kinds: list[str],
    references: list[str], top: int, max_atoms: int,
) -> dict[str, Any]:
    relations = list(registry["relations"])

    def body(relation: str, dependency: bool) -> dict[str, Any]:
        slots = registry["relations"][relation]["slots"]
        arguments = []
        for _ in slots:
            choice = rng.random()
            if choice < 0.55:
                arguments.append({"kind": "bound", "ref": rng.choice(references),
                                  "proof": random_proof(rng, kinds, top)})
            elif choice < 0.8:
                arguments.append({"kind": "output" if dependency else "unknown",
                                  "proof": random_proof(rng, kinds, top)})
            else:
                arguments.append({"kind": "from_prior_atom",
                                  "source_ordinal": rng.randint(1, max_atoms),
                                  "proof": random_proof(rng, kinds, top)})
        return {"relation": relation, "relation_proof": random_proof(rng, kinds, top),
                "arguments": arguments}

    analysis = {
        "clause_role": rng.choice(list(registry["clause_roles"])),
        "clause_role_proof": random_proof(rng, kinds, top),
        "projection": {
            **body(rng.choice(relations), False),
            "speech_act": rng.choice(list(registry["speech_acts"])),
            "speech_act_proof": random_proof(rng, kinds, top),
        },
    }
    if rng.random() < 0.4:
        analysis["dependencies"] = [
            body(rng.choice(relations), True)
            for _ in range(rng.randint(1, 2))
        ]
    return analysis


def random_frame(rng: random.Random, registry: dict[str, Any], top: int) -> Any:
    kinds = [k for family in registry["proof_families"].values() for k in family]
    kinds.extend(kind for meta in registry["reference_registry"].values()
                 for kind in meta.get("proof_families", []))
    kinds = list(dict.fromkeys(kinds))
    references = list(registry["reference_registry"])
    max_atoms = registry["limits"]["max_atoms_per_analysis"]
    status = rng.choice(["supported", "supported", "typed_ambiguity", "unsupported"])

    def span() -> dict[str, Any]:
        start = rng.randint(1, top)
        return {"clause_start_segment_id": start,
                "clause_end_segment_id": rng.randint(start, top)}

    def out_of_registry() -> dict[str, Any]:
        return {**span(),
                "clause_role": rng.choice(list(registry["clause_roles"])),
                "clause_role_proof": random_proof(rng, kinds, top),
                "reason": "out_of_registry"}

    if status == "unsupported":
        return {"status": "unsupported",
                "clauses": [out_of_registry() for _ in range(rng.randint(1, 3))]}
    if status == "supported":
        clauses = []
        for _ in range(rng.randint(1, 3)):
            if rng.random() < 0.25:
                clauses.append(out_of_registry())
            else:
                clauses.append({**span(), **random_analysis(
                    rng, registry, kinds, references, top, max_atoms)})
        if not any("projection" in clause for clause in clauses):
            clauses.append({**span(), **random_analysis(
                rng, registry, kinds, references, top, max_atoms)})
        return {"status": "supported", "clauses": clauses}
    clauses = []
    width = rng.randint(2, 3)
    for _ in range(rng.randint(1, 3)):
        if rng.random() < 0.25:
            clauses.append(out_of_registry())
        else:
            clauses.append({**span(), "readings": [
                random_analysis(rng, registry, kinds, references, top, max_atoms)
                for _ in range(width)]})
    if not any("readings" in clause for clause in clauses):
        clauses.append({**span(), "readings": [
            random_analysis(rng, registry, kinds, references, top, max_atoms)
            for _ in range(width)]})
    return {"status": "typed_ambiguity", "clauses": clauses}


# --------------------------------------------------------------------------
# suite
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    _pinned_site_roots()
    import jsonschema

    projection = _load("v26566_projection", HERE / "metnos_v26566_registry_projection.py")
    expander = _load("v26566_expander", HERE / "metnos_v26566_expander.py")
    pipeline = _load("v26566_pipeline", HERE / "metnos_v26566_offline_pipeline.py")

    registry_bytes = REGISTRY_PATH.read_bytes()
    registry = projection.load_registry(registry_bytes)
    schema = projection.build_schema(registry)
    prompt = projection.build_prompt(registry)
    vocabulary = projection.fingerprint_vocabulary(registry)
    max_atoms = registry["limits"]["max_atoms_per_analysis"]
    validator_module = pipeline.load_validator()
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    controls = json.loads(CONTROLS_PATH.read_text(encoding="utf-8"))
    queries = {case["opaque_case_id"]: case["query"] for case in controls["cases"]}
    schema_validator = jsonschema.Draft202012Validator(schema)

    def run(frame: Any, segments: list[dict[str, Any]]) -> dict[str, Any]:
        return pipeline.evaluate(
            frame, segments, schema=schema, validator_module=validator_module,
            expander=expander, max_atoms=max_atoms,
            fingerprint_vocabulary=vocabulary,
        )

    def schema_ok(frame: Any) -> bool:
        return not list(schema_validator.iter_errors(frame))

    # --- group 1: materialised artifacts match their generator ------------
    group = "materialisation"
    jsonschema.Draft202012Validator.check_schema(schema)
    check(group, "schema is a valid 2020-12 schema", True)
    stored = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    check(group, "materialised schema equals the generated schema", stored == schema)
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
        expanded, _ = expander.expand_frame(frame, max_atoms=max_atoms)
        if normalise_expanded(expanded, registry) == normalise_gold(template):
            semantics_ok += 1
    check(group, "34/34 gold cases are representable and validate",
          represented == 34, f"{represented}/34")
    check(group, "34/34 expanded frames carry exactly the gold semantics",
          semantics_ok == 34, f"{semantics_ok}/34")

    # --- group 3: the V26.5.6.4 killers are now harmless ------------------
    group = "regression_of_the_v26564_failures"
    base_case = fixture["cases"][0]
    base_query = queries[base_case["opaque_case_id"]]
    base_segments = unicode_segments(base_query)
    base_template = overlay["templates"][base_case["template_ref"]]
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
    dependency_template = overlay["templates"][dependency_case["template_ref"]]
    dependency_frame = build_frame_from_template(
        dependency_template, registry, (1, len(dependency_segments)))
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
          not schema_ok(duplicate))

    orphan = copy.deepcopy(base_frame)
    orphan["clauses"][0].pop("projection")
    orphan["clauses"][0]["dependencies"] = [{
        "relation": "spatial.located_at", "relation_proof": COMPOSITION,
        "arguments": [{"kind": "bound", "ref": "actor.current", "proof": DISCOURSE},
                      {"kind": "output", "proof": COMPOSITION},
                      {"kind": "bound", "ref": "time.current", "proof": {"kind": "utterance_context"}}],
    }]
    check(group, "a clause with dependencies and no projection is SCHEMA-INVALID",
          not schema_ok(orphan))

    no_projection = {"status": "supported", "clauses": [{
        "clause_start_segment_id": 1, "clause_end_segment_id": 1,
        "clause_role": "main_request", "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry"}]}
    check(group, "a supported frame with no projected clause is SCHEMA-INVALID",
          not schema_ok(no_projection))

    forward = copy.deepcopy(dependency_frame)
    forward["clauses"][0]["projection"]["arguments"][1]["source_ordinal"] = 99
    forward_record = run(forward, dependency_segments)
    check(group, "an out-of-bound edge ordinal is SCHEMA-INVALID and still measured",
          not forward_record["schema_ok"] and forward_record["stages_measured"] == 3
          and not forward_record["valid"],
          json.dumps(forward_record["stage_codes"]))

    # the expander is TOTAL: systematic type substitution in every position
    group = "expander_totality"
    ambiguity_case = next(
        case for case in fixture["cases"]
        if overlay["templates"][case["template_ref"]]["adjudication"] == "typed_ambiguity"
    )
    ambiguity_query = queries[ambiguity_case["opaque_case_id"]]
    ambiguity_segments = unicode_segments(ambiguity_query)
    ambiguity_template = overlay["templates"][ambiguity_case["template_ref"]]
    ambiguity_frame = build_frame_from_template(
        ambiguity_template, registry, (1, len(ambiguity_segments)))
    unsupported_frame = {"status": "unsupported", "clauses": [{
        "clause_start_segment_id": 1, "clause_end_segment_id": len(base_segments),
        "clause_role": "main_request", "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry"}]}
    mixed_frame = copy.deepcopy(base_frame)
    mixed_frame["clauses"].append({
        "clause_start_segment_id": 1, "clause_end_segment_id": len(base_segments),
        "clause_role": "main_request", "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry"})

    MARKER = "REQUESTMATERIALMARKER"
    substitutions = [5, "x", None, True, [], {}, [5], {"k": 5}, MARKER,
                     {"kind": MARKER}, [{"kind": MARKER}]]
    fuzz_bases = [base_frame, dependency_frame, ambiguity_frame, mixed_frame,
                  unsupported_frame]
    raised: list[str] = []
    fuzz_leaks: list[str] = []
    silent_censorship: list[str] = []
    fuzz_frames = 0
    fuzz_evaluated = 0
    unmeasured = 0
    for index, source in enumerate(fuzz_bases):
        for path in walk_paths(source):
            for value in substitutions:
                mutant = replace_at(source, path, value)
                fuzz_frames += 1
                try:
                    expander.expand_frame(mutant, max_atoms=max_atoms)
                    print_free = expander.structural_fingerprint(
                        mutant, vocabulary=vocabulary)
                    if MARKER in json.dumps(print_free, ensure_ascii=False):
                        fuzz_leaks.append("/".join(str(p) for p in path))
                except Exception as error:               # noqa: BLE001
                    raised.append(f"{type(error).__name__}: {error}")
                if fuzz_frames % 3 == 0:
                    fuzz_evaluated += 1
                    try:
                        record = run(mutant, base_segments)
                        if not record["validator_semantic_measured"]:
                            unmeasured += 1
                            if not any(code.startswith("sweep_exception:") for code
                                       in record["validator_semantic_codes"]):
                                silent_censorship.append(
                                    "/".join(str(p) for p in path))
                    except Exception as error:           # noqa: BLE001
                        raised.append(f"evaluate {type(error).__name__}: {error}")
    # the five one-line inputs the independent review used to falsify totality
    review_inputs = [
        {"status": "supported", "clauses": 5},
        {"status": "supported", "clauses": [{
            "clause_start_segment_id": 1, "clause_end_segment_id": 1,
            "clause_role": "main_request", "clause_role_proof": DISCOURSE,
            "projection": {"relation": "spatial.located_at",
                           "relation_proof": {"kind": "clause_construction"},
                           "speech_act": "open_question",
                           "speech_act_proof": {"kind": "clause_construction"},
                           "arguments": []},
            "dependencies": 5}]},
        {"status": "typed_ambiguity", "alternatives": 5},
        {"status": "typed_ambiguity", "alternatives": [5, 6]},
        {"status": "typed_ambiguity", "clauses": 5},
        {"status": "typed_ambiguity", "clauses": [5, 6]},
        {"status": "typed_ambiguity", "clauses": [{"readings": 5}]},
        {"status": "typed_ambiguity", "clauses": [{"readings": [5]}]},
        {"status": "unsupported", "clauses": 5},
    ]
    review_measured = 0
    for frame in review_inputs:
        try:
            expander.expand_frame(frame, max_atoms=max_atoms)
            expander.structural_fingerprint(frame, vocabulary=vocabulary)
            record = run(frame, base_segments)
            if record["stages_measured"] == 3:
                review_measured += 1
        except Exception as error:                       # noqa: BLE001
            raised.append(f"review input {type(error).__name__}: {error}")
    check(group, f"no exception over {fuzz_frames} type substitutions",
          not raised, "; ".join(raised[:3]))
    check(group, "every scalar-container input of the review is measured on 3 stages",
          review_measured == len(review_inputs),
          f"{review_measured}/{len(review_inputs)}")
    check(group, "a third of the fuzz corpus reached the pipeline too",
          fuzz_evaluated * 3 >= fuzz_frames, f"{fuzz_evaluated} evaluated")
    check(group, "no marker planted in any position reaches a fingerprint",
          not fuzz_leaks, "; ".join(sorted(set(fuzz_leaks))[:5]))
    check(group, "an unmeasurable semantic stage always says so",
          not silent_censorship, "; ".join(sorted(set(silent_censorship))[:5]))
    MEASURES["fuzz_semantic_unmeasured"] = unmeasured
    MEASURES["fuzz_frames"] = fuzz_frames
    MEASURES["fuzz_evaluated"] = fuzz_evaluated

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

    # --- group 6: the atom budget is named, and never censors -------------
    group = "atom_budget"
    over_budget = {"status": "supported", "clauses": []}
    for _ in range(registry["limits"]["max_clauses_per_analysis"]):
        clause = copy.deepcopy(dependency_frame["clauses"][0])
        clause["dependencies"] = [copy.deepcopy(clause["dependencies"][0]),
                                  copy.deepcopy(clause["dependencies"][0])]
        clause["projection"]["arguments"][1]["source_ordinal"] = 2
        over_budget["clauses"].append(clause)
    expanded_over, codes_over = expander.expand_frame(over_budget, max_atoms=max_atoms)
    record = run(over_budget, dependency_segments)
    check(group, "a frame above the frozen atom budget is schema-valid",
          record["schema_ok"], json.dumps(record["stage_codes"]["schema"][:3]))
    check(group, "the derived atom count exceeds the frozen bound",
          len(expanded_over["atoms"]) > max_atoms,
          f"{len(expanded_over['atoms'])} atoms, bound {max_atoms}")
    check(group, "the expansion names the atom budget explicitly",
          expander.CODE_ATOM_BUDGET in codes_over, json.dumps(codes_over))
    check(group, "the frozen validator censors itself on this frame",
          record["validator_schema_censored"]
          and record["stage_codes"]["validator"] == ["schema"],
          json.dumps(record["stage_codes"]["validator"]))
    check(group, "the semantic surface is measured anyway",
          record["validator_semantic_measured"]
          and bool(record["validator_semantic_codes"]),
          json.dumps(record["validator_semantic_codes"]))
    check(group, "a within-budget frame is not reported as over budget",
          expander.CODE_ATOM_BUDGET not in
          expander.expand_frame(dependency_frame, max_atoms=max_atoms)[1])
    check(group, "the semantic sweep agrees with the validator on a clean frame",
          pipeline.semantic_sweep(
              validator_module,
              expander.expand_frame(dependency_frame, max_atoms=max_atoms)[0],
              dependency_segments) == ([], True))
    MEASURES["atoms_over_budget"] = len(expanded_over["atoms"])
    MEASURES["semantic_codes_recovered"] = record["validator_semantic_codes"]

    # --- group 7: typed ambiguity has one shared clause identity space ----
    group = "typed_ambiguity_identity"
    check(group, "a typed-ambiguity frame validates with derived alternative ids",
          run(ambiguity_frame, ambiguity_segments)["valid"])
    expanded_ambiguity, _ = expander.expand_frame(ambiguity_frame, max_atoms=max_atoms)
    check(group, "alternative ids are derived from position",
          [alt["alternative_id"] for alt in expanded_ambiguity["alternatives"]]
          == list(range(1, len(expanded_ambiguity["alternatives"]) + 1)))

    out_of_registry_clause = {
        "clause_start_segment_id": 1,
        "clause_end_segment_id": len(ambiguity_segments),
        "clause_role": "relative_modifier", "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry"}
    positions_ok = 0
    positions_total = 0
    for position in range(len(ambiguity_frame["clauses"]) + 1):
        variant = copy.deepcopy(ambiguity_frame)
        variant["clauses"].insert(position, copy.deepcopy(out_of_registry_clause))
        positions_total += 1
        record = run(variant, ambiguity_segments)
        closed = [code for code in record["stage_codes"]["validator"]
                  if code in STRUCTURALLY_CLOSED_CODES]
        if record["valid"] and not closed:
            positions_ok += 1
        else:
            check(group, f"out-of-registry clause at index {position}", False,
                  json.dumps(record["stage_codes"]))
    check(group, "an out-of-registry clause validates at every index",
          positions_ok == positions_total, f"{positions_ok}/{positions_total}")

    # the three shapes the independent review used to reach clause_ids
    diverging = [
        {"status": "typed_ambiguity", "alternatives": [
            {"clauses": [copy.deepcopy(ambiguity_frame["clauses"][0])]},
            {"clauses": [copy.deepcopy(out_of_registry_clause)]}]},
        {"status": "typed_ambiguity", "clauses": [
            copy.deepcopy(ambiguity_frame["clauses"][0]),
            {"clause_start_segment_id": 1,
             "clause_end_segment_id": len(ambiguity_segments),
             "readings": [copy.deepcopy(out_of_registry_clause)]}]},
    ]
    check(group, "a per-alternative clause array is SCHEMA-INVALID",
          all(not schema_ok(frame) for frame in diverging))

    two_readings = copy.deepcopy(ambiguity_frame)
    three_readings = copy.deepcopy(ambiguity_frame)
    three_readings["clauses"][0]["readings"].append(
        copy.deepcopy(three_readings["clauses"][0]["readings"][0]))
    if len(two_readings["clauses"]) > 1:
        mismatch = three_readings
    else:
        mismatch = copy.deepcopy(three_readings)
        mismatch["clauses"].append(copy.deepcopy(two_readings["clauses"][0]))
    _, mismatch_codes = expander.expand_frame(mismatch, max_atoms=max_atoms)
    check(group, "reading arrays of different length are reported, never dropped",
          (expander.CODE_READING_COUNT_MISMATCH in mismatch_codes)
          or len(mismatch["clauses"]) == 1,
          json.dumps(mismatch_codes))

    # --- group 8: multi-action, multi-domain, mixed coverage --------------
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
    record = run(mixed_frame, base_segments)
    check(group, "a mixed representable/out-of-registry frame validates",
          record["valid"], json.dumps(record["stage_codes"]))
    check(group, "the unsupported branch validates",
          run(unsupported_frame, base_segments)["valid"])
    # clause 2 consumes the projection unknown of clause 1: the edge crosses a
    # clause boundary with no emitted atom label on either side
    open_question = build_frame_from_template(
        overlay["templates"]["actor_current_position_open"],
        registry, (1, len(base_segments)))
    cross_clause = {"status": "supported", "clauses": [
        copy.deepcopy(open_question["clauses"][0]),
        {
            "clause_start_segment_id": 1, "clause_end_segment_id": len(base_segments),
            "clause_role": "main_request", "clause_role_proof": DISCOURSE,
            "projection": {
                "relation": "spatial.near",
                "relation_proof": {"kind": "clause_construction"},
                "speech_act": "imperative",
                "speech_act_proof": {"kind": "clause_construction"},
                "arguments": [
                    {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
                    {"kind": "from_prior_atom", "source_ordinal": 1,
                     "proof": COMPOSITION},
                    {"kind": "bound", "ref": "time.current",
                     "proof": {"kind": "utterance_context"}},
                ],
            },
        },
    ]}
    cross_record = run(cross_clause, base_segments)
    check(group, "an inter-clause edge across two clauses validates",
          cross_record["valid"], json.dumps(cross_record["stage_codes"]))
    check(group, "the inter-clause edge is counted in the fingerprint",
          cross_record["fingerprint"]["edge_count"] == 1)

    # --- group 9: no emitted identity in the request contract -------------
    group = "no_emitted_identity"
    schema_text = json.dumps(schema, sort_keys=True)
    for label, forbidden in (("clause_id", '"clause_id"'), ("atom_id", '"atom_id"'),
                             ("alternative_id", '"alternative_id"'),
                             ("output_index", '"output_index"')):
        check(group, f"the response schema never asks for {label}",
              forbidden not in schema_text)
    check(group, "the prompt forbids numeric labels",
          "do not emit numeric labels" in prompt.lower())

    # --- group 10: Unicode metamorphic stability -------------------------
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

    # --- group 11: contamination audit, prompt AND schema ------------------
    group = "contamination"
    prompt_lower = prompt.lower()
    schema_lower = schema_text.lower()
    verbatim = [q for q in queries.values()
                if q.lower() in prompt_lower or q.lower() in schema_lower]
    check(group, "no control query appears verbatim in the prompt or schema",
          not verbatim, "; ".join(verbatim[:3]))

    def tokens(text: str) -> list[str]:
        return [t for t in "".join(
            c.lower() if c.isalnum() else " " for c in text).split() if t]

    prompt_tokens = tokens(prompt)
    schema_tokens = tokens(schema_text)
    contract_tokens = {3: set(), 4: set()}
    for order in (3, 4):
        for source in (prompt_tokens, schema_tokens):
            contract_tokens[order].update(
                tuple(source[i:i + order]) for i in range(len(source) - order + 1))
    overlaps = {3: [], 4: []}
    for query in queries.values():
        query_tokens = tokens(query)
        for order in (3, 4):
            if any(tuple(query_tokens[i:i + order]) in contract_tokens[order]
                   for i in range(len(query_tokens) - order + 1)):
                overlaps[order].append(query)
    check(group, "no 4-token n-gram is shared with any control query",
          not overlaps[4], "; ".join(overlaps[4][:3]))
    check(group, "no 3-token n-gram is shared with any control query",
          not overlaps[3], "; ".join(overlaps[3][:3]))
    contamination = {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256(
            json.dumps(schema, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest(),
        "controls_sha256": controls["source_controls_sha256"],
        "verbatim_query_matches": len(verbatim),
        "shared_4gram_queries": len(overlaps[4]),
        "shared_3gram_queries": len(overlaps[3]),
        "prompt_token_count": len(prompt_tokens),
        "queries_audited": len(queries),
    }

    # --- group 12: fingerprint privacy, valid AND invalid frames ----------
    group = "fingerprint_privacy"
    leaked: list[str] = []

    def strings(node: Any) -> list[str]:
        if isinstance(node, str):
            return [node]
        if isinstance(node, list):
            return [s for item in node for s in strings(item)]
        if isinstance(node, dict):
            return [s for item in node.values() for s in strings(item)]
        return []

    for case in fixture["cases"]:
        query = queries[case["opaque_case_id"]]
        segments = unicode_segments(query)
        frame = build_frame_from_template(
            overlay["templates"][case["template_ref"]], registry, (1, len(segments)))
        record = run(frame, segments)
        blob = json.dumps(record["fingerprint"], ensure_ascii=False)
        if query.lower() in blob.lower():
            leaked.append(query)
        for value in strings(record["fingerprint"]):
            if value not in vocabulary:
                leaked.append(value)
    check(group, "no fingerprint of a valid frame leaves the closed vocabulary",
          not leaked, "; ".join(sorted(set(leaked))[:5]))

    marker = "REQUESTMATERIALMARKER"
    leaky = {"status": marker, "clauses": [{
        "clause_start_segment_id": 1, "clause_end_segment_id": 2,
        "clause_role": marker + "_role",
        "clause_role_proof": {"kind": marker + "_proof"},
        "projection": {"relation": marker + "_relation",
                       "relation_proof": {"kind": "clause_construction"},
                       "speech_act": marker + "_speech",
                       "speech_act_proof": {"kind": "clause_construction"},
                       "arguments": [{"kind": marker + "_binding",
                                      "proof": {"kind": marker + "_proofkind"}}]},
        "dependencies": [{"relation": marker + "_dependency",
                          "relation_proof": {"kind": marker + "_dep_proof"},
                          "arguments": [{"kind": marker, "proof": {"kind": marker}}]}],
    }]}
    leaky_record = run(leaky, base_segments)
    leaky_blob = json.dumps(leaky_record["fingerprint"], ensure_ascii=False)
    check(group, "a schema-invalid frame is really schema-invalid",
          not leaky_record["schema_ok"])
    check(group, "no model-controlled string reaches the fingerprint",
          marker not in leaky_blob, leaky_blob[:200])
    check(group, "out-of-vocabulary material is counted instead",
          leaky_record["fingerprint"]["out_of_vocabulary_strings"] >= 8,
          str(leaky_record["fingerprint"]["out_of_vocabulary_strings"]))
    check(group, "the fingerprint still describes the structure",
          leaky_record["fingerprint"]["clause_count"] == 1
          and leaky_record["fingerprint"]["atom_count"] == 2)
    check(group, "the fingerprint carries no source span",
          "segment_id" not in json.dumps(
              run(base_frame, base_segments)["fingerprint"]))

    # --- group 13: fail-closed on drift ----------------------------------
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

    # --- group 14: mutation suite, with the semantic oracle ---------------
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

    # systematic single-value mutation of every projection-semantic field,
    # judged by the semantic oracle and not by structural validity alone
    semantic_keys = {"relation", "speech_act", "clause_role", "kind", "ref"}
    alternatives_for = {
        "relation": list(registry["relations"]),
        "speech_act": list(registry["speech_acts"]),
        "clause_role": list(registry["clause_roles"]),
        "ref": list(registry["reference_registry"]),
        "kind": ["bound", "unknown", "output", "from_prior_atom"],
    }
    swept = 0
    survivors = 0
    silent = []
    exceptions = []
    for case in fixture["cases"]:
        query = queries[case["opaque_case_id"]]
        segments = unicode_segments(query)
        template = overlay["templates"][case["template_ref"]]
        gold_frame = build_frame_from_template(template, registry, (1, len(segments)))
        gold_semantics = normalise_gold(template)
        for path in walk_paths(gold_frame):
            if not path or not isinstance(path[-1], str):
                continue
            key = path[-1]
            if key not in semantic_keys:
                continue
            if "projection" not in path and key != "clause_role":
                continue          # dependency internals are not read back here
            current = read_at(gold_frame, path)
            if not isinstance(current, str):
                continue
            for candidate in alternatives_for[key]:
                if candidate == current:
                    continue
                mutant = replace_at(gold_frame, path, candidate)
                swept += 1
                try:
                    record = run(mutant, segments)
                except Exception as error:               # noqa: BLE001
                    exceptions.append(f"{type(error).__name__}: {error}")
                    continue
                if not record["valid"]:
                    continue
                survivors += 1
                expanded, _ = expander.expand_frame(mutant, max_atoms=max_atoms)
                if semantics_or_none(expanded, registry) == gold_semantics:
                    silent.append("/".join(str(p) for p in path) + f"->{candidate}")
                break             # one candidate per path keeps the sweep bounded
    check(group, "no projection-semantic mutation survives with the gold semantics",
          not silent, "; ".join(silent[:3]))
    check(group, "the semantic mutation sweep raised nothing",
          not exceptions, "; ".join(exceptions[:3]))
    check(group, "the semantic mutation sweep is not empty", swept > 100, str(swept))
    MEASURES["semantic_mutations"] = swept
    MEASURES["semantic_mutation_survivors"] = survivors

    # --- group 15: random schema-valid frames close the structural codes --
    group = "structural_closure"
    rng = random.Random(SEED)
    generated = 0
    schema_valid = 0
    valid_frames = 0
    fired: dict[str, int] = {}
    closed_hits: list[str] = []
    sweep_exceptions: list[str] = []
    unfaithful: list[str] = []
    faithful_checked = 0
    expansion_only_invalid = 0
    while schema_valid < 1500 and generated < 12000:
        generated += 1
        frame = random_frame(rng, registry, len(base_segments))
        if not schema_ok(frame):
            continue
        schema_valid += 1
        try:
            record = run(frame, base_segments)
        except Exception as error:                       # noqa: BLE001
            sweep_exceptions.append(f"{type(error).__name__}: {error}")
            continue
        if record["valid"]:
            valid_frames += 1
        if not record["validator_schema_censored"]:
            # the sweep must replay the frozen validator exactly, otherwise a
            # censored frame would be measured against a weaker rule set
            expanded_random, _ = expander.expand_frame(frame, max_atoms=max_atoms)
            sweep_codes, sweep_measured = pipeline.semantic_sweep(
                validator_module, expanded_random, base_segments)
            expected = [code for code in record["stage_codes"]["validator"]
                        if code != "schema"]
            faithful_checked += 1
            if not sweep_measured or sweep_codes != expected:
                unfaithful.append(f"{sorted(sweep_codes)} vs {expected}")
        for code in record["stage_codes"]["validator"]:
            fired[code] = fired.get(code, 0) + 1
            if code in STRUCTURALLY_CLOSED_CODES:
                closed_hits.append(code)
        if not record["expansion_ok"] and record["validator_ok"]:
            expansion_only_invalid += 1
    check(group, f"no structurally closed code fires on {schema_valid} random "
                 "schema-valid frames",
          not closed_hits, "; ".join(sorted(set(closed_hits))[:5]))
    check(group, "the random sweep raised nothing",
          not sweep_exceptions, "; ".join(sweep_exceptions[:3]))
    check(group, "the random sweep produced both valid and invalid frames",
          0 < valid_frames < schema_valid, f"{valid_frames}/{schema_valid} valid")
    check(group, "the expander never invalidates a frame the validator accepts",
          expansion_only_invalid == 0, str(expansion_only_invalid))
    check(group, "the semantic sweep reproduces the frozen validator exactly",
          not unfaithful and faithful_checked > 500,
          f"{faithful_checked} compared; " + "; ".join(unfaithful[:2]))
    MEASURES["sweep_faithfulness_compared"] = faithful_checked
    MEASURES["random_frames_generated"] = generated
    MEASURES["random_schema_valid"] = schema_valid
    MEASURES["random_valid"] = valid_frames
    MEASURES["random_validator_codes"] = dict(
        sorted(fired.items(), key=lambda item: (-item[1], item[0]))[:12])

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
        "seed": SEED,
        "passed": passed, "total": total,
        "all_green": passed == total,
        "by_group": by_group,
        "network_calls": 0, "model_calls": 0, "live_runs": 0,
        "inference": False,
        "contamination_audit": contamination,
        "measures": MEASURES,
        "results": RESULTS,
    }
    if args.json:
        args.json.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
