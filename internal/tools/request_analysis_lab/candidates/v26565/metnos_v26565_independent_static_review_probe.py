#!/usr/bin/env python3
"""Independent offline probe behind the V26.5.6.5 static review.

Pure, offline, deterministic. Zero network, zero model calls, zero transport,
zero gate creation or consumption, zero live inference. Every frozen artifact
is opened read-only; the gold, the overlay, V26.5.6.4 and the V26.5.6.5 freeze
are never written.

This file is NOT part of the V26.5.6.5 author freeze: it is review evidence.

Run:
  /usr/bin/python3 -I -B \\
    internal/tools/request_analysis_lab/candidates/v26565/\\
metnos_v26565_independent_static_review_probe.py [--json <path>]
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
import unicodedata
from typing import Any

HERE = pathlib.Path(__file__).resolve(strict=True).parent
CANDIDATES = HERE.parent
LAB = CANDIDATES.parent
ROOT = LAB.parent.parent.parent

VERSION = "metnos.v26.5.6.5-independent-static-review-probe/1.0"
SEED = 20260809

PROBES: list[dict[str, Any]] = []


def record(pid: str, title: str, outcome: str, evidence: Any) -> None:
    PROBES.append({"probe": pid, "title": title, "outcome": outcome,
                   "evidence": evidence})
    print(f"[{outcome:9}] {pid:5} {title}")


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pinned_site_roots() -> None:
    """Re-add only the third-party roots the frozen dependency tree pins."""
    manifest = json.loads((CANDIDATES / "v2656" /
                           "metnos_v2656_python_dependency_tree.json").read_text("utf-8"))
    for package in manifest["packages"]:
        if package["name"] not in {"regex", "jsonschema"}:
            continue
        root = str(pathlib.Path(package["package_root"]).parent)
        if root not in sys.path:
            sys.path.append(root)


_pinned_site_roots()
import jsonschema                                              # noqa: E402

projection = _load("v26565_projection", HERE / "metnos_v26565_registry_projection.py")
expander = _load("v26565_expander", HERE / "metnos_v26565_expander.py")
pipeline = _load("v26565_pipeline", HERE / "metnos_v26565_offline_pipeline.py")
selftest = _load("v26565_selftest", HERE / "metnos_v26565_offline_selftest.py")

REGISTRY_BYTES = (CANDIDATES / "v2641" / "metnos_v2641_typed_registry.json").read_bytes()
REGISTRY = projection.load_registry(REGISTRY_BYTES)
LIMITS = REGISTRY["limits"]
SCHEMA = projection.build_schema(REGISTRY)
PROMPT = projection.build_prompt(REGISTRY)
VALIDATOR = pipeline.load_validator()
OVERLAY = json.loads((LAB / "oracles" / "phase1_v1" /
                      "metnos_phase1_typed_oracle_v1.overlay.json").read_text("utf-8"))
FIXTURE = json.loads((CANDIDATES / "v2641" /
                      "metnos_v2641_typed_phase1_controls_frozen.json").read_text("utf-8"))
CONTROLS = json.loads((CANDIDATES / "v2656" /
                       "metnos_v2656_runtime_controls34.json").read_text("utf-8"))
QUERIES = {case["opaque_case_id"]: case["query"] for case in CONTROLS["cases"]}

SCHEMA_V = jsonschema.Draft202012Validator(SCHEMA)
NORMAL_FORM_V = jsonschema.Draft202012Validator(VALIDATOR.live_schema())

RELATIONS = list(REGISTRY["relations"])
REFS = list(REGISTRY["reference_registry"])
ROLES = list(REGISTRY["clause_roles"])
ACTS = list(REGISTRY["speech_acts"])
PROOF_KINDS = projection.all_proof_kinds(REGISTRY)

DISCOURSE = {"kind": "discourse_structure"}
COMPOSITION = {"kind": "relation_composition"}


def segments_of(text: str) -> list[dict[str, Any]]:
    return selftest.unicode_segments(text)


def run(frame: Any, segments: list[dict[str, Any]]) -> dict[str, Any]:
    return pipeline.evaluate(frame, segments, schema=SCHEMA,
                             validator_module=VALIDATOR, expander=expander)


def schema_errors(frame: Any) -> list[str]:
    return ["/" + "/".join(str(part) for part in error.absolute_path)
            for error in SCHEMA_V.iter_errors(frame)]


def gold_frame(template_ref: str):
    case = next(c for c in FIXTURE["cases"] if c["template_ref"] == template_ref)
    segments = segments_of(QUERIES[case["opaque_case_id"]])
    frame = selftest.build_frame_from_template(
        OVERLAY["templates"][template_ref], REGISTRY, (1, len(segments)))
    return frame, segments


def projected_clause(relation, speech_act, arguments, span, role="main_request"):
    return {"clause_start_segment_id": span[0], "clause_end_segment_id": span[1],
            "clause_role": role, "clause_role_proof": DISCOURSE,
            "projection": {"relation": relation,
                           "relation_proof": {"kind": "clause_construction"},
                           "speech_act": speech_act,
                           "speech_act_proof": {"kind": "clause_construction"},
                           "arguments": arguments}}


def unsupported_clause(span, role="main_request"):
    return {"clause_start_segment_id": span[0], "clause_end_segment_id": span[1],
            "clause_role": role, "clause_role_proof": DISCOURSE,
            "reason": "out_of_registry"}


BASE_REF = FIXTURE["cases"][0]["template_ref"]
BASE_FRAME, BASE_SEGMENTS = gold_frame(BASE_REF)
DEP_FRAME, DEP_SEGMENTS = gold_frame("nearby_places_actor_position_dependency")
N = len(BASE_SEGMENTS)

WORKFLOW_ARGS = [
    {"kind": "bound", "ref": "actor.current",
     "proof": {"kind": "explicit_segment", "start_segment_id": 1, "end_segment_id": N}},
    {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
    {"kind": "bound", "ref": "time.current", "proof": {"kind": "utterance_context"}},
]


# ==========================================================================
# R0 - reproduction: freeze, README table, dependency bytes, bytecode
# ==========================================================================
def probe_r0() -> None:
    freeze = json.loads((HERE / "metnos_v26565_author.freeze.json").read_text("utf-8"))
    artifacts = {}
    for name, meta in freeze["artifacts"].items():
        payload = (HERE / name).read_bytes()
        artifacts[name] = {
            "sha_ok": hashlib.sha256(payload).hexdigest() == meta["sha256"],
            "bytes_ok": len(payload) == meta["bytes"]}
    dependencies = {}
    for key, meta in freeze["reused_frozen_dependencies"].items():
        path = ROOT / meta["path"]
        dependencies[key] = {
            "sha_ok": path.exists()
            and hashlib.sha256(path.read_bytes()).hexdigest() == meta["sha256"],
            "exists": path.exists()}
    readme_rows, readme_bad = 0, []
    for line in (HERE / "README.md").read_text("utf-8").splitlines():
        if line.startswith("| `metnos_v26565") and line.count("`") >= 4:
            name, digest = line.split("`")[1], line.split("`")[3]
            readme_rows += 1
            if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest:
                readme_bad.append(name)
    bytecode = sorted(str(p.relative_to(HERE)) for p in HERE.rglob("*")
                      if p.name == "__pycache__" or p.suffix == ".pyc")
    ok = (all(v["sha_ok"] and v["bytes_ok"] for v in artifacts.values())
          and all(v["sha_ok"] for v in dependencies.values())
          and not readme_bad and not bytecode)
    record("R0", "freeze table, README hash table, pinned dependency bytes, no bytecode",
           "confirmed" if ok else "FINDING",
           {"artifacts_verified": len(artifacts),
            "artifacts_ok": sum(1 for v in artifacts.values()
                                if v["sha_ok"] and v["bytes_ok"]),
            "dependencies_verified": len(dependencies),
            "dependencies_ok": sum(1 for v in dependencies.values() if v["sha_ok"]),
            "readme_hash_rows": readme_rows, "readme_mismatches": readme_bad,
            "pyc_or_pycache": bytecode,
            "freeze_sha256": hashlib.sha256(
                (HERE / "metnos_v26565_author.freeze.json").read_bytes()).hexdigest(),
            "declared_selftest": freeze["selftest"]})


# ==========================================================================
# B1 - the expander is not total: schema-invalid containers abort the pipeline
# ==========================================================================
def probe_b1() -> None:
    scalar_frames = [
        ("clauses is a scalar", {"status": "supported", "clauses": 5}),
        ("clauses is an object", {"status": "supported", "clauses": {"a": 1}}),
        ("dependencies is a scalar", {"status": "supported", "clauses": [{
            "clause_start_segment_id": 1, "clause_end_segment_id": 1,
            "clause_role": "main_request", "clause_role_proof": DISCOURSE,
            "projection": {"relation": "workflow.position",
                           "relation_proof": {"kind": "clause_construction"},
                           "speech_act": "open_question",
                           "speech_act_proof": {"kind": "clause_construction"},
                           "arguments": [{"kind": "unknown",
                                          "proof": {"kind": "interrogative_construction"}}]},
            "dependencies": 5}]}),
        ("alternatives is a scalar", {"status": "typed_ambiguity", "alternatives": 5}),
        ("an alternative is a scalar",
         {"status": "typed_ambiguity", "alternatives": [5, 6]}),
        ("unsupported clauses is a scalar", {"status": "unsupported", "clauses": 5}),
    ]
    expand, fingerprint, evaluate = [], [], []
    for label, frame in scalar_frames:
        assert schema_errors(frame), label
        for sink, call in ((expand, lambda f=frame: expander.expand_frame(f)),
                           (fingerprint, lambda f=frame: expander.structural_fingerprint(f)),
                           (evaluate, lambda f=frame: run(f, BASE_SEGMENTS))):
            try:
                call()
            except Exception as error:                          # noqa: BLE001
                sink.append(f"{label}: {type(error).__name__}: {error}")
    record("B1", "expand_frame raises on schema-invalid scalar-where-container input",
           "FINDING" if expand else "confirmed", expand)
    record("B1b", "structural_fingerprint raises on the same inputs",
           "FINDING" if fingerprint else "confirmed", fingerprint)
    record("B1c", "pipeline.evaluate has no guard around stage 2, so the raise escapes "
           "and the case is recorded on ZERO stages",
           "FINDING" if evaluate else "confirmed",
           {"raised": evaluate,
            "stage_3_is_guarded": "except Exception" in
            (HERE / "metnos_v26565_offline_pipeline.py").read_text("utf-8"),
            "stage_2_is_guarded": False})


# ==========================================================================
# B2 - S4 fingerprint privacy holds only for schema-valid frames
# ==========================================================================
def probe_b2() -> None:
    marker = "sifre kelimesi hangi klasorde"       # stands in for request material
    leaky = {
        "status": marker + " STATUS",
        "clauses": [{
            "clause_start_segment_id": 1, "clause_end_segment_id": 1,
            "clause_role": marker + " ROLE",
            "clause_role_proof": {"kind": marker + " PROOF"},
            "projection": {"relation": marker + " RELATION",
                           "relation_proof": {"kind": "clause_construction"},
                           "speech_act": marker + " ACT",
                           "speech_act_proof": {"kind": "clause_construction"},
                           "arguments": [{"kind": marker + " BINDING",
                                          "proof": {"kind": "interrogative_construction"}}]},
        }],
    }
    assert schema_errors(leaky)
    fingerprint = expander.structural_fingerprint(leaky)
    leaked = sorted({value for value in
                     [fingerprint["status"], *fingerprint["relations"],
                      *fingerprint["clause_roles"], *fingerprint["speech_acts"],
                      *fingerprint["binding_kinds"], *fingerprint["proof_kinds"]]
                     if isinstance(value, str) and marker in value})
    source = (HERE / "metnos_v26565_offline_selftest.py").read_text("utf-8")
    group = source.split('group = "fingerprint_privacy"')[1].split('group = "fail_closed"')[0]
    record("B2", "structural_fingerprint copies model-controlled strings verbatim when "
           "the frame is schema-invalid, which is exactly when S4 is needed",
           "FINDING" if leaked else "confirmed",
           {"leaked_fields": leaked,
            "fingerprint": {k: v for k, v in fingerprint.items()
                            if k in {"status", "relations", "clause_roles",
                                     "speech_acts", "binding_kinds", "proof_kinds"}}})
    record("B2b", "the author privacy group probes only frames built by "
           "build_frame_from_template, i.e. schema-valid ones",
           "FINDING" if "build_frame_from_template" in group and "iter_errors" not in group
           else "confirmed",
           {"group_probes_an_invalid_frame": "iter_errors" in group})


# ==========================================================================
# B3 - typed_ambiguity carrying out-of-registry clauses
# ==========================================================================
def probe_b3() -> None:
    def alt_workflow():
        return projected_clause("workflow.position", "open_question",
                                copy.deepcopy(WORKFLOW_ARGS), (1, N))

    def alt_document():
        return projected_clause("document.position", "open_question",
                                copy.deepcopy(WORKFLOW_ARGS), (1, N))

    disagree = {"status": "typed_ambiguity", "alternatives": [
        {"clauses": [unsupported_clause((1, N)), alt_workflow()]},
        {"clauses": [alt_document()]},
    ]}
    reordered = {"status": "typed_ambiguity", "alternatives": [
        {"clauses": [unsupported_clause((1, N)), alt_workflow()]},
        {"clauses": [alt_document(), unsupported_clause((1, N))]},
    ]}
    results = {}
    for label, frame in (("alternatives_disagree", disagree),
                         ("same_clause_other_index", reordered)):
        errors = schema_errors(frame)
        outcome = run(frame, BASE_SEGMENTS)
        results[label] = {"schema_errors": errors,
                          "expansion": outcome["stage_codes"]["expansion"],
                          "validator": outcome["stage_codes"]["validator"],
                          "valid": outcome["valid"]}
    expanded, codes = expander.expand_frame(disagree)
    record("B3", "typed_ambiguity alternatives that differ on out-of-registry clauses "
           "are SCHEMA-VALID yet always invalid, through rules the response schema "
           "cannot express",
           "FINDING" if all(not r["schema_errors"] and not r["valid"]
                            for r in results.values()) else "confirmed",
           results)
    record("B3b", "the expander keeps only alternative 1's out-of-registry clauses and "
           "silently drops those of the later alternatives",
           "FINDING",
           {"emitted_unsupported_clauses": expanded.get("unsupported_clauses"),
            "expansion_codes": codes,
            "alternative_clause_counts": [len(a["clauses"])
                                          for a in disagree["alternatives"]]})

    max_clauses = LIMITS["max_clauses_per_analysis"]
    half = max_clauses // 2
    heavy = {"status": "typed_ambiguity", "alternatives": [
        {"clauses": [unsupported_clause((1, N)) for _ in range(half)]
                    + [alt_workflow() for _ in range(half)]},
        {"clauses": [alt_document() for _ in range(max_clauses)]},
    ]}
    heavy_errors = schema_errors(heavy)
    heavy_result = run(heavy, BASE_SEGMENTS)
    record("B3c", "a per-alternative clause array within the frozen bound can still "
           "trip clause_limit once alternative 1's out-of-registry clauses are added "
           "to every alternative's footprint",
           "FINDING" if not heavy_errors and "clause_limit"
           in heavy_result["stage_codes"]["validator"] else "confirmed",
           {"schema_errors": heavy_errors,
            "max_clauses_per_analysis": max_clauses,
            "validator": heavy_result["stage_codes"]["validator"]})


# ==========================================================================
# B4 - max_atoms_per_analysis is unenforced, and stage 3 censors itself
# ==========================================================================
def probe_b4() -> None:
    clauses = []
    for _ in range(LIMITS["max_clauses_per_analysis"]):
        clause = projected_clause("spatial.near", "open_question", [
            {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
            {"kind": "bound", "ref": "place.explicit",
             "proof": {"kind": "discourse_context"}},
            {"kind": "bound", "ref": "time.current",
             "proof": {"kind": "utterance_context"}},
        ], (1, N))
        clause["dependencies"] = [{
            "relation": "spatial.located_at", "relation_proof": COMPOSITION,
            "arguments": [
                {"kind": "bound", "ref": "actor.current",
                 "proof": {"kind": "explicit_segment",
                           "start_segment_id": 1, "end_segment_id": N}},
                {"kind": "output", "proof": COMPOSITION},
                {"kind": "bound", "ref": "time.current",
                 "proof": {"kind": "utterance_context"}},
            ]} for _ in range(2)]
        clauses.append(clause)
    frame = {"status": "supported", "clauses": clauses}
    errors = schema_errors(frame)
    expanded, _ = expander.expand_frame(frame)
    outcome = run(frame, BASE_SEGMENTS)
    ceiling = LIMITS["max_clauses_per_analysis"] * LIMITS["max_atoms_per_analysis"]
    record("B4", "the response schema bounds dependencies per clause but not atoms per "
           "analysis; past the frozen bound the validator's own schema rejects the "
           "normal form and validate_frame returns early, so every semantic check is "
           "censored",
           "FINDING" if not errors and outcome["stage_codes"]["validator"] == ["schema"]
           else "confirmed",
           {"response_schema_errors": errors,
            "atoms_after_expansion": len(expanded["atoms"]),
            "max_atoms_per_analysis": LIMITS["max_atoms_per_analysis"],
            "response_schema_atom_ceiling": ceiling,
            "validator_stage_codes": outcome["stage_codes"]["validator"],
            "stages_measured_reported": outcome["stages_measured"],
            "semantic_codes_measured": 0})


# ==========================================================================
# C1 - property test over random schema-valid frames
# ==========================================================================
IMMUNE = {"primary_cardinality", "orphan_dependency",
          "clause_span_consistency", "projection_missing"}


def _random_proof(rng):
    kind = rng.choice(PROOF_KINDS)
    if kind == "explicit_segment":
        a, b = rng.randint(1, N), rng.randint(1, N)
        return {"kind": kind, "start_segment_id": min(a, b), "end_segment_id": max(a, b)}
    if kind in {"predicate_morphology", "tense_morphology"}:
        return {"kind": kind, "predicate_segment_id": rng.randint(1, N)}
    return {"kind": kind}


def _random_arguments(rng, relation, dependency):
    arity = len(REGISTRY["relations"][relation]["slots"])
    arguments = []
    for _ in range(rng.randint(1, arity)):
        roll = rng.random()
        if roll < 0.45:
            arguments.append({"kind": "bound", "ref": rng.choice(REFS),
                              "proof": _random_proof(rng)})
        elif roll < 0.70:
            arguments.append({"kind": "output" if dependency else "unknown",
                              "proof": _random_proof(rng)})
        else:
            arguments.append({"kind": "from_prior_atom",
                              "source_ordinal": rng.randint(1, 8),
                              "proof": _random_proof(rng)})
    return arguments


def _random_clause(rng):
    a, b = rng.randint(1, N), rng.randint(1, N)
    span = (min(a, b), max(a, b))
    if rng.random() < 0.25:
        return unsupported_clause(span, rng.choice(ROLES))
    relation = rng.choice(RELATIONS)
    clause = projected_clause(relation, rng.choice(ACTS),
                              _random_arguments(rng, relation, False),
                              span, rng.choice(ROLES))
    clause["clause_role_proof"] = _random_proof(rng)
    clause["projection"]["relation_proof"] = _random_proof(rng)
    clause["projection"]["speech_act_proof"] = _random_proof(rng)
    if rng.random() < 0.5:
        dependencies = []
        for _ in range(rng.randint(1, 2)):
            relation = rng.choice(RELATIONS)
            dependencies.append({"relation": relation,
                                 "relation_proof": _random_proof(rng),
                                 "arguments": _random_arguments(rng, relation, True)})
        clause["dependencies"] = dependencies
    return clause


def _random_frame(rng):
    roll = rng.random()
    if roll < 0.60:
        return {"status": "supported",
                "clauses": [_random_clause(rng) for _ in range(rng.randint(1, 4))]}
    if roll < 0.85:
        return {"status": "typed_ambiguity", "alternatives": [
            {"clauses": [_random_clause(rng) for _ in range(rng.randint(1, 3))]}
            for _ in range(rng.randint(2, 3))]}
    return {"status": "unsupported",
            "clauses": [unsupported_clause((1, N), rng.choice(ROLES))
                        for _ in range(rng.randint(1, 3))]}


def probe_c1(target: int = 4000) -> None:
    rng = random.Random(SEED)
    generated = accepted = valid = 0
    immune_hits: list[Any] = []
    expansion_only = 0
    inner_schema = 0
    census: dict[str, int] = {}
    raised: list[str] = []
    while accepted < target and generated < target * 200:
        generated += 1
        frame = _random_frame(rng)
        if schema_errors(frame):
            continue
        accepted += 1
        try:
            outcome = run(frame, BASE_SEGMENTS)
        except Exception as error:                              # noqa: BLE001
            raised.append(f"{type(error).__name__}: {error}")
            continue
        valid += int(outcome["valid"])
        for code in (outcome["stage_codes"]["validator"]
                     + outcome["stage_codes"]["expansion"]):
            census[code] = census.get(code, 0) + 1
        hit = IMMUNE.intersection(outcome["stage_codes"]["validator"])
        if hit:
            immune_hits.append(sorted(hit))
        if outcome["validator_ok"] and not outcome["expansion_ok"]:
            expansion_only += 1
        if outcome["stage_codes"]["validator"] == ["schema"]:
            inner_schema += 1
    record("C1", f"{accepted} random SCHEMA-VALID frames never trigger "
           "primary_cardinality / orphan_dependency / clause_span_consistency / "
           "projection_missing",
           "FINDING" if immune_hits else "confirmed",
           {"schema_valid_frames": accepted, "frames_generated": generated,
            "immune_code_hits": len(immune_hits), "pipeline_exceptions": len(raised),
            "seed": SEED})
    record("C1b", "invalidity of a schema-valid frame is dominated by rules the "
           "response schema cannot express; some are contributed by the expander alone",
           "context",
           {"valid_frames": valid, "invalid_frames": accepted - valid,
            "invalid_from_expansion_only": expansion_only,
            "validator_inner_schema_only": inner_schema,
            "code_census": dict(sorted(census.items(), key=lambda kv: -kv[1]))})


# ==========================================================================
# C2 - source_ordinal: inter-clause edges and per-scope numbering
# ==========================================================================
def probe_c2() -> None:
    inter = {"status": "supported", "clauses": [
        projected_clause("spatial.located_at", "open_question", [
            {"kind": "bound", "ref": "actor.current",
             "proof": {"kind": "explicit_segment",
                       "start_segment_id": 1, "end_segment_id": N}},
            {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
            {"kind": "bound", "ref": "time.current",
             "proof": {"kind": "utterance_context"}},
        ], (1, N)),
        projected_clause("spatial.near", "open_question", [
            {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
            {"kind": "from_prior_atom", "source_ordinal": 1, "proof": COMPOSITION},
            {"kind": "bound", "ref": "time.current",
             "proof": {"kind": "utterance_context"}},
        ], (1, N)),
    ]}
    errors = schema_errors(inter)
    outcome = run(inter, BASE_SEGMENTS)
    record("C2", "an inter-clause edge - clause 2 consuming clause 1's projection "
           "unknown - is representable and validates",
           "confirmed" if not errors and outcome["valid"] else "FINDING",
           {"schema_errors": errors, "stage_codes": outcome["stage_codes"],
            "valid": outcome["valid"]})

    ambiguous = {"status": "typed_ambiguity", "alternatives": [
        {"clauses": copy.deepcopy(inter["clauses"])},
        {"clauses": copy.deepcopy(DEP_FRAME["clauses"])},
    ]}
    expanded, _ = expander.expand_frame(ambiguous)
    ordinals = [[atom["atom_id"] for atom in alternative["atoms"]]
                for alternative in expanded["alternatives"]]
    global_read = copy.deepcopy(ambiguous)
    binding = global_read["alternatives"][1]["clauses"][0]["projection"]["arguments"][1]
    binding["source_ordinal"] = 3
    outcome = run(global_read, DEP_SEGMENTS)
    record("C2b", "ordinals restart at 1 inside every alternative while the prompt says "
           "source_ordinal counts 'from one across the whole analysis'; the literal "
           "reading yields a schema-valid frame that fails expansion and validation",
           "FINDING",
           {"per_alternative_atom_ids": ordinals,
            "prompt_sentence": next(line for line in PROMPT.splitlines()
                                    if "across the whole analysis" in line)[:240],
            "schema_errors": schema_errors(global_read),
            "expansion": outcome["stage_codes"]["expansion"],
            "validator": outcome["stage_codes"]["validator"]})
    record("C2c", "source_ordinal is emitted but its target space is derived; the "
           "schema bounds it below and not above",
           "FINDING",
           {"schema_constraint": {"type": "integer", "minimum": 1},
            "derived_ceiling": "number of atoms in the enclosing scope",
            "static_upper_bound_available": LIMITS["max_atoms_per_analysis"]})


# ==========================================================================
# C3 - clause order is prose-only
# ==========================================================================
def probe_c3() -> None:
    frame = {"status": "supported", "clauses": [
        projected_clause("workflow.position", "open_question",
                         copy.deepcopy(WORKFLOW_ARGS), (max(1, N - 1), N)),
        projected_clause("document.position", "open_question",
                         copy.deepcopy(WORKFLOW_ARGS), (1, 1)),
    ]}
    errors = schema_errors(frame)
    outcome = run(frame, BASE_SEGMENTS)
    record("C3", "clauses emitted out of source order are schema-valid and rejected by "
           "prose-only ordering rules",
           "FINDING" if not errors and not outcome["valid"] else "confirmed",
           {"schema_errors": errors, "validator": outcome["stage_codes"]["validator"]})


# ==========================================================================
# C4 - Unicode metamorphic stability, extended to normalisation forms
# ==========================================================================
def probe_c4() -> None:
    fingerprints, counts, validity = set(), {}, set()
    scripts = 0
    for case in FIXTURE["cases"]:
        if case["template_ref"] != "actor_current_position_open":
            continue
        scripts += 1
        query = QUERIES[case["opaque_case_id"]]
        for form in ("NFC", "NFD", "NFKC", "NFKD"):
            segments = segments_of(unicodedata.normalize(form, query))
            frame = selftest.build_frame_from_template(
                OVERLAY["templates"]["actor_current_position_open"],
                REGISTRY, (1, len(segments)))
            outcome = run(frame, segments)
            fingerprints.add(outcome["fingerprint_sha256"])
            validity.add(outcome["valid"])
            counts.setdefault(form, set()).add(len(segments))
    record("C4", "one fingerprint over 9 scripts x NFC/NFD/NFKC/NFKD, uniformly valid",
           "confirmed" if len(fingerprints) == 1 and validity == {True} else "FINDING",
           {"scripts": scripts, "distinct_fingerprints": len(fingerprints),
            "uniformly_valid": validity == {True},
            "segment_counts_by_form": {k: sorted(v) for k, v in counts.items()}})


# ==========================================================================
# C5 - contamination of prompt AND schema; gold and query material
# ==========================================================================
def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " "
                               for c in text).split() if t]


def probe_c5() -> None:
    schema_text = json.dumps(SCHEMA, ensure_ascii=False)
    report = {}
    for name, text in (("prompt", PROMPT), ("schema", schema_text)):
        lowered = text.lower()
        tokens = _tokens(text)
        verbatim = [q for q in QUERIES.values() if q.lower() in lowered]
        grams: dict[str, Any] = {}
        for order in (2, 3, 4):
            pool = {tuple(tokens[i:i + order])
                    for i in range(max(0, len(tokens) - order + 1))}
            hits = []
            for query in QUERIES.values():
                qt = _tokens(query)
                shared = [" ".join(qt[i:i + order])
                          for i in range(max(0, len(qt) - order + 1))
                          if tuple(qt[i:i + order]) in pool]
                if shared:
                    hits.append(shared[:2])
            grams[f"shared_{order}gram_queries"] = len(hits)
            if order == 2:
                grams["shared_2gram_examples"] = hits[:3]
        report[name] = {"verbatim_query_matches": len(verbatim), **grams}
    identifiers = [key for key in ("template_ref", "opaque_case_id", "adjudication",
                                   "projections", "source_controls_sha256")
                   if key in PROMPT or key in schema_text]
    record("C5", "no control query is copied into the prompt or the schema; no 3-gram "
           "or 4-gram is shared",
           "confirmed" if all(r["verbatim_query_matches"] == 0
                              and r["shared_4gram_queries"] == 0
                              and r["shared_3gram_queries"] == 0
                              for r in report.values()) else "FINDING", report)
    record("C5b", "no gold, fixture or overlay identifier reaches the prompt or the "
           "schema", "confirmed" if not identifiers else "FINDING",
           {"leaked_identifiers": identifiers})


# ==========================================================================
# C6 - source-language surface scan of the request contract
# ==========================================================================
def probe_c6() -> None:
    quoted = [line for line in PROMPT.splitlines() if '"' in line]
    non_ascii = sorted({ch for ch in PROMPT if ord(ch) > 127})
    enums: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "enum" and isinstance(value, list):
                    enums.update(v for v in value if isinstance(v, str))
                if key == "const" and isinstance(value, str):
                    enums.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(SCHEMA)
    structural = {projection.KEY_STATUS, projection.KEY_CLAUSES,
                  projection.KEY_ALTERNATIVES, projection.KEY_PROJECTION,
                  projection.KEY_DEPENDENCIES, projection.KEY_REASON,
                  projection.REASON_OUT_OF_REGISTRY, projection.STATUS_SUPPORTED,
                  projection.STATUS_TYPED_AMBIGUITY, projection.STATUS_UNSUPPORTED,
                  projection.BINDING_FROM_PRIOR, "bound", "unknown", "output"}
    derived = set(RELATIONS) | set(REFS) | set(ROLES) | set(ACTS) | set(PROOF_KINDS)
    unexplained = sorted(enums - derived - structural)
    record("C6", "every enum and const in the response schema is either a fixed "
           "structural key or a denotation of the frozen registry",
           "FINDING" if unexplained else "confirmed",
           {"enum_literals": len(enums), "registry_derived": len(enums & derived),
            "structural": len(enums & structural), "unexplained": unexplained})
    record("C6b", "the prompt carries no double-quoted surface example and is ASCII only",
           "FINDING" if quoted or non_ascii else "confirmed",
           {"double_quoted_lines": quoted[:3], "non_ascii_characters": non_ascii,
            "registry_descriptions_injected":
                len(REGISTRY["relations"]) + len(REGISTRY["clause_roles"])
                + len(REGISTRY["speech_acts"])})
    bridge_sites = {}
    for path in sorted(HERE.glob("*")):
        if path.suffix not in {".py", ".json", ".txt"}:
            continue
        text = path.read_text("utf-8", errors="replace")
        if "DERIVED_REFERENCE_BRIDGE" in text or "actor.current_position" in text:
            bridge_sites[path.name] = text.count("DERIVED_REFERENCE_BRIDGE")
    record("C6c", "DERIVED_REFERENCE_BRIDGE lives in the self-test fixture only and "
           "reaches neither the prompt, the schema, the expander nor the pipeline",
           "confirmed" if set(bridge_sites) <= {
               "metnos_v26565_offline_selftest.py",
               "metnos_v26565_independent_static_review_probe.py"} else "FINDING",
           {"files_mentioning_it": bridge_sites,
            "bridge": selftest.DERIVED_REFERENCE_BRIDGE})


# ==========================================================================
# C7 - mutation sweep over the 34 gold frames, with a semantic oracle
# ==========================================================================
REPLACEMENTS = {
    "relation": RELATIONS, "clause_role": ROLES, "speech_act": ACTS,
    "ref": REFS, "kind": PROOF_KINDS + ["bound", "unknown", "output", "from_prior_atom"],
    "status": ["supported", "typed_ambiguity", "unsupported"],
}


def _leaves(frame: Any) -> list[tuple[list[Any], Any]]:
    out: list[tuple[list[Any], Any]] = []

    def walk(node: Any, path: list[Any]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + [key])
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + [index])
        else:
            out.append((path, node))

    walk(frame, [])
    return out


def _set_at(frame: Any, path: list[Any], value: Any) -> None:
    node = frame
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def probe_c7() -> None:
    total = rejected = survived = schema_blocked = drifted = 0
    raised: list[str] = []
    examples: list[dict[str, Any]] = []
    for case in FIXTURE["cases"]:
        frame, segments = gold_frame(case["template_ref"])
        template = OVERLAY["templates"][case["template_ref"]]
        assert run(frame, segments)["valid"], case["template_ref"]
        gold_semantics = selftest.normalise_gold(template)
        for path, value in _leaves(frame):
            key = path[-1] if isinstance(path[-1], str) else (
                path[-2] if len(path) > 1 and isinstance(path[-2], str) else "")
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                candidates: list[Any] = [value + 1, value - 1, 0, 999]
            elif isinstance(value, str):
                pool = REPLACEMENTS.get(key, [])
                candidates = [v for v in pool if v != value][:2] or ["x"]
            else:
                continue
            for candidate in candidates:
                mutant = copy.deepcopy(frame)
                _set_at(mutant, path, candidate)
                if mutant == frame:
                    continue
                total += 1
                if schema_errors(mutant):
                    schema_blocked += 1
                try:
                    outcome = run(mutant, segments)
                except Exception as error:                      # noqa: BLE001
                    raised.append(f"{'/'.join(map(str, path))}={candidate!r}: "
                                  f"{type(error).__name__}")
                    continue
                if not outcome["valid"]:
                    rejected += 1
                    continue
                survived += 1
                expanded, _ = expander.expand_frame(mutant)
                try:
                    same = (selftest.normalise_expanded(expanded, REGISTRY)
                            == gold_semantics)
                except Exception:                               # noqa: BLE001
                    same = False
                if not same:
                    drifted += 1
                    if len(examples) < 8:
                        examples.append({"case": case["template_ref"],
                                         "path": "/".join(map(str, path)),
                                         "from": value, "to": candidate})
    record("C7", "single-scalar mutation sweep over all 34 gold frames",
           "FINDING" if raised else "context",
           {"mutants": total, "rejected": rejected, "still_valid": survived,
            "schema_blocked": schema_blocked, "pipeline_exceptions": len(raised),
            "still_valid_but_semantically_different": drifted,
            "examples_of_undetected_drift": examples})
    record("C7b", "structural validity is not a semantic oracle: the author mutation "
           "group asserts only that `valid` is false, so a mutation that stays valid "
           "while changing the gold semantics is not counted",
           "context",
           {"author_mutations": 12, "independent_mutants": total,
            "undetected_semantic_drift": drifted,
            "note": "the representability group does compare semantics, but only on "
                    "the unmutated gold frames"})


# ==========================================================================
# C8 - self-test bookkeeping
# ==========================================================================
def probe_c8() -> None:
    source = (HERE / "metnos_v26565_offline_selftest.py").read_text("utf-8")
    guards = [line.strip() for line in source.splitlines()
              if line.strip().startswith("if ") and "PATH.exists()" in line]
    record("C8", "two materialisation checks run only if the artifact exists, so the "
           "reported total is conditional on the bundle being complete",
           "FINDING" if guards else "confirmed",
           {"conditional_guards": guards, "reported_total": 42,
            "total_without_those_files": 40})
    record("C8b", "the 'prompt forbids numeric labels' check has a dead first disjunct: "
           "prompt.replace(chr(10), chr(10)) is a no-op and the literal never matches",
           "FINDING" if "Do not \\nemit numeric labels" in source else "confirmed",
           {"first_disjunct_matches": "Do not \nemit numeric labels" in PROMPT,
            "second_disjunct_matches": "do not emit numeric labels" in PROMPT.lower()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    for probe in (probe_r0, probe_b1, probe_b2, probe_b3, probe_b4, probe_c1,
                  probe_c2, probe_c3, probe_c4, probe_c5, probe_c6, probe_c7,
                  probe_c8):
        probe()

    summary = {
        "probes": len(PROBES),
        "findings": sum(1 for p in PROBES if p["outcome"] == "FINDING"),
        "confirmed": sum(1 for p in PROBES if p["outcome"] == "confirmed"),
        "context": sum(1 for p in PROBES if p["outcome"] == "context"),
    }
    print(f"\n{summary}")
    payload = {"version": VERSION, "seed": SEED, "inference": False,
               "network_calls": 0, "model_calls": 0, "live_runs": 0,
               "gold_modified": False, "frozen_artifacts_modified": False,
               "summary": summary, "probes": PROBES}
    if args.json:
        args.json.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
