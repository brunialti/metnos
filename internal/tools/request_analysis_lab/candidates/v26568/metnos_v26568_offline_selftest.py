#!/usr/bin/env python3
"""V26.5.6.8 offline self-test: the relation-typed contract.

Pure, offline, deterministic. Zero network, zero model calls, zero transport,
zero gate created or consumed, zero live inference. Frozen bytes are read only.

The suite of V26.5.6.6 is inherited by RUNNING it: the expander, the pipeline
and the frozen validator are reused byte for byte here, so their 71 checks
still stand. What is new is the schema, and what it must prove is exactly what
the live run of 10 August measured.

Run:
  /usr/bin/python3 -I -B \
    internal/tools/request_analysis_lab/candidates/v26568/\
metnos_v26568_offline_selftest.py [--json <path>]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import pathlib
import random
import subprocess
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve(strict=True).parent
CANDIDATES = HERE.parent
LAB = CANDIDATES.parent

VERSION = "metnos.v26.5.6.8-offline-selftest/1.0"
SEED = 20260810

PREDECESSOR = CANDIDATES / "v26566"
BASELINE_SELFTEST = PREDECESSOR / "metnos_v26566_offline_selftest.py"
REGISTRY_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_registry.json"
FIXTURE_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_phase1_controls_frozen.json"
CONTROLS_PATH = CANDIDATES / "v2656" / "metnos_v2656_runtime_controls34.json"
OVERLAY_PATH = LAB / "oracles" / "phase1_v1" / "metnos_phase1_typed_oracle_v1.overlay.json"
SCHEMA_PATH = HERE / "metnos_v26568_relation_typed.schema.json"
PROMPT_PATH = HERE / "metnos_v26568_relation_typed.prompt.txt"
LIVE_BATCH_PATH = CANDIDATES / "v26567" / "metnos_v26567_live_k1_34.json"

RESULTS: list[dict[str, Any]] = []
MEASURES: dict[str, Any] = {}

# Codes the frozen validator can no longer reach on a schema-valid frame.
# The first eleven were closed by V26.5.6.6 (clause identity in the structure);
# the rest are closed here, and each one is a family the live run measured.
STRUCTURALLY_CLOSED_CODES = (
    "primary_cardinality", "orphan_dependency", "clause_span_consistency",
    "projection_missing", "clause_ids", "alternative_coverage", "clause_limit",
    "alternative_order", "atom_order", "atom_clause_order",
    "unsupported_clause_order",
    "proof_family", "reference_type", "relation_arity", "role_speech",
    "dependency_output_slot", "dependency_unknown", "projection_output",
    "dependency_relation", "dependency_output_count",
)


def check(group: str, label: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append({"group": group, "label": label,
                    "passed": bool(condition), "detail": detail})
    return bool(condition)


def _load(name: str, path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pinned_site_roots() -> None:
    manifest = json.loads(
        (CANDIDATES / "v2656" / "metnos_v2656_python_dependency_tree.json")
        .read_text(encoding="utf-8"))
    for package in manifest["packages"]:
        if package["name"] not in {"regex", "jsonschema"}:
            continue
        root = str(pathlib.Path(package["package_root"]).parent)
        if root not in sys.path:
            sys.path.append(root)


# --------------------------------------------------------------------------
# a generator that samples the relation-typed contract from the registry
# --------------------------------------------------------------------------

def random_analysis(
    rng: random.Random, registry: dict[str, Any], projection: Any,
    role: str, top: int, max_atoms: int,
) -> dict[str, Any]:
    relations = registry["relations"]
    families = registry["proof_families"]

    def proof(kinds: list[str]) -> dict[str, Any]:
        kind = rng.choice(list(kinds))
        if kind == "explicit_segment":
            start = rng.randint(1, top)
            return {"kind": kind, "start_segment_id": start,
                    "end_segment_id": rng.randint(start, top)}
        if kind in {"predicate_morphology", "tense_morphology"}:
            return {"kind": kind, "predicate_segment_id": rng.randint(1, top)}
        return {"kind": kind}

    def arguments(relation: str, dependency: bool) -> list[dict[str, Any]]:
        metadata = relations[relation]
        output = metadata.get("dependency_output")
        emitted = []
        for index, slot in enumerate(metadata["slots"], 1):
            options: list[Any] = []
            for reference in projection._references_for_slot(registry, slot):
                options.append(("bound", reference))
            if dependency:
                if output and output.get("slot_index") == index:
                    options.append(("output", None))
            else:
                options.append(("unknown", None))
            if slot.get("accepted_outputs"):
                options.append(("from_prior_atom", None))
            kind, reference = rng.choice(options)
            if kind == "bound":
                declared = registry["reference_registry"][reference].get("proof_families")
                kinds = declared or projection._slot_argument_family(registry, slot)
                emitted.append({"kind": "bound", "ref": reference,
                                "proof": proof(list(kinds))})
            elif kind == "unknown":
                emitted.append({"kind": "unknown", "proof": proof(
                    projection._slot_unknown_family(registry, slot))})
            elif kind == "output":
                emitted.append({"kind": "output",
                                "proof": proof(families["dependency_output"])})
            else:
                emitted.append({"kind": "from_prior_atom",
                                "source_ordinal": rng.randint(1, max_atoms),
                                "proof": proof(families["from_atom_output"])})
        return emitted

    relation = rng.choice(list(relations))
    analysis = {
        "clause_role": role,
        "clause_role_proof": proof(families["clause_role"]),
        "projection": {
            "relation": relation,
            "relation_proof": proof(families["relation"]),
            "speech_act": rng.choice(registry["clause_roles"][role]["speech_acts"]),
            "speech_act_proof": proof(families["speech_act"]),
            "arguments": arguments(relation, False),
        },
    }
    producers = [
        name for name, meta in relations.items()
        if meta.get("dependency_output") is not None
    ]
    if producers and rng.random() < 0.4:
        analysis["dependencies"] = [{
            "relation": (producer := rng.choice(producers)),
            "relation_proof": proof(families["dependency_relation"]),
            "arguments": arguments(producer, True),
        } for _ in range(rng.randint(1, 2))]
    return analysis


def random_frame(
    rng: random.Random, registry: dict[str, Any], projection: Any, top: int,
) -> Any:
    max_atoms = registry["limits"]["max_atoms_per_analysis"]
    roles = list(registry["clause_roles"])
    families = registry["proof_families"]

    def span() -> dict[str, Any]:
        start = rng.randint(1, top)
        return {"clause_start_segment_id": start,
                "clause_end_segment_id": rng.randint(start, top)}

    def out_of_registry() -> dict[str, Any]:
        kind = rng.choice(families["clause_role"])
        return {**span(), "clause_role": rng.choice(roles),
                "clause_role_proof": {"kind": kind},
                "reason": "out_of_registry"}

    status = rng.choice(["supported", "supported", "typed_ambiguity", "unsupported"])
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
                    rng, registry, projection, rng.choice(roles), top, max_atoms)})
        if not any("projection" in clause for clause in clauses):
            clauses.append({**span(), **random_analysis(
                rng, registry, projection, rng.choice(roles), top, max_atoms)})
        return {"status": "supported", "clauses": clauses}
    width = rng.randint(2, 3)
    clauses = []
    for _ in range(rng.randint(1, 3)):
        if rng.random() < 0.25:
            clauses.append(out_of_registry())
        else:
            clauses.append({**span(), "readings": [
                random_analysis(rng, registry, projection, rng.choice(roles),
                                top, max_atoms)
                for _ in range(width)]})
    if not any("readings" in clause for clause in clauses):
        clauses.append({**span(), "readings": [
            random_analysis(rng, registry, projection, rng.choice(roles),
                            top, max_atoms)
            for _ in range(width)]})
    return {"status": "typed_ambiguity", "clauses": clauses}


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    _pinned_site_roots()
    import jsonschema

    projection = _load("v26568_projection", HERE / "metnos_v26568_registry_projection.py")
    expander = _load("v26568_expander", PREDECESSOR / "metnos_v26566_expander.py")
    pipeline = _load("v26568_pipeline", PREDECESSOR / "metnos_v26566_offline_pipeline.py")
    old_projection = _load(
        "v26566_projection", PREDECESSOR / "metnos_v26566_registry_projection.py")
    fixture_builder = _load(
        "v26566_selftest", PREDECESSOR / "metnos_v26566_offline_selftest.py")

    registry_bytes = REGISTRY_PATH.read_bytes()
    registry = projection.load_registry(registry_bytes)
    schema = projection.build_schema(registry)
    prompt = projection.build_prompt(registry)
    old_schema = old_projection.build_schema(registry)
    vocabulary = projection.fingerprint_vocabulary(registry)
    max_atoms = registry["limits"]["max_atoms_per_analysis"]
    validator_module = pipeline.load_validator()
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    controls = json.loads(CONTROLS_PATH.read_text(encoding="utf-8"))
    queries = {case["opaque_case_id"]: case["query"] for case in controls["cases"]}
    schema_validator = jsonschema.Draft202012Validator(schema)
    old_validator = jsonschema.Draft202012Validator(old_schema)

    def run(frame: Any, segments: list[dict[str, Any]]) -> dict[str, Any]:
        return pipeline.evaluate(
            frame, segments, schema=schema, validator_module=validator_module,
            expander=expander, max_atoms=max_atoms,
            fingerprint_vocabulary=vocabulary)

    def schema_ok(frame: Any) -> bool:
        return not list(schema_validator.iter_errors(frame))

    # --- group 1: the reused bundle still passes its own suite ------------
    group = "inherited_v26566"
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", str(BASELINE_SELFTEST)],
        cwd=LAB.parents[2], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    tail = completed.stdout.decode("utf-8").strip().splitlines()[-1:] or [""]
    check(group, "the V26.5.6.6 suite still passes unchanged",
          completed.returncode == 0 and tail[0].endswith("PASS"), tail[0][:60])
    MEASURES["inherited_v26566"] = tail[0]

    # --- group 2: materialised artifacts match their generator ------------
    group = "materialisation"
    jsonschema.Draft202012Validator.check_schema(schema)
    check(group, "schema is a valid 2020-12 schema", True)
    check(group, "materialised schema equals the generated schema",
          json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == schema)
    check(group, "materialised prompt equals the generated prompt",
          PROMPT_PATH.read_text(encoding="utf-8") == prompt)
    check(group, "registry hash is pinned",
          hashlib.sha256(registry_bytes).hexdigest()
          == projection.EXPECTED_REGISTRY_SHA256)
    MEASURES["schema_definitions"] = len(schema["$defs"])
    MEASURES["schema_bytes"] = len(json.dumps(schema, separators=(",", ":")))
    MEASURES["previous_schema_bytes"] = len(
        json.dumps(old_schema, separators=(",", ":")))

    # --- group 3: the gold is still representable, exactly ----------------
    group = "representability"
    represented = semantics_ok = 0
    for case in fixture["cases"]:
        segments = fixture_builder.unicode_segments(queries[case["opaque_case_id"]])
        template = overlay["templates"][case["template_ref"]]
        frame = fixture_builder.build_frame_from_template(
            template, registry, (1, len(segments)))
        record = run(frame, segments)
        if record["valid"]:
            represented += 1
        else:
            check(group, f"case {case['template_ref']} valid", False,
                  json.dumps(record["stage_codes"]))
        expanded, _ = expander.expand_frame(frame, max_atoms=max_atoms)
        if fixture_builder.normalise_expanded(expanded, registry) \
                == fixture_builder.normalise_gold(template):
            semantics_ok += 1
    check(group, "34/34 gold cases are representable and validate",
          represented == 34, f"{represented}/34")
    check(group, "34/34 expanded frames carry exactly the gold semantics",
          semantics_ok == 34, f"{semantics_ok}/34")

    # --- group 4: what the live run measured is now unrepresentable -------
    group = "live_failure_families_closed"
    base_case = fixture["cases"][0]
    base_segments = fixture_builder.unicode_segments(
        queries[base_case["opaque_case_id"]])
    base = fixture_builder.build_frame_from_template(
        overlay["templates"][base_case["template_ref"]],
        registry, (1, len(base_segments)))

    def variant(edit) -> dict[str, Any]:
        frame = copy.deepcopy(base)
        edit(frame)
        return frame

    families = {
        "clause role proved by an explicit segment (72 of 80 live proof errors)":
            variant(lambda f: f["clauses"][0].__setitem__(
                "clause_role_proof", {"kind": "explicit_segment",
                                      "start_segment_id": 1, "end_segment_id": 2})),
        "wrong relation arity (13 live cases)":
            variant(lambda f: f["clauses"][0]["projection"]["arguments"].pop()),
        "reference incompatible with its slot (13 live cases)":
            variant(lambda f: f["clauses"][0]["projection"]["arguments"].__setitem__(
                1, {"kind": "bound", "ref": "actor.current",
                    "proof": {"kind": "discourse_context"}})),
        "clause role and speech act disagree (1 live case)":
            variant(lambda f: f["clauses"][0].__setitem__(
                "clause_role", "main_assertion")),
        "relation proved by a family reserved to dependencies":
            variant(lambda f: f["clauses"][0]["projection"].__setitem__(
                "relation_proof", {"kind": "relation_composition"})),
        "a dependency declaring a requested unknown":
            variant(lambda f: f["clauses"][0].__setitem__("dependencies", [{
                "relation": "spatial.located_at",
                "relation_proof": {"kind": "relation_composition"},
                "arguments": [
                    {"kind": "bound", "ref": "actor.current",
                     "proof": {"kind": "discourse_context"}},
                    {"kind": "unknown",
                     "proof": {"kind": "interrogative_construction"}},
                    {"kind": "bound", "ref": "time.current",
                     "proof": {"kind": "utterance_context"}}]}])),
    }
    still_representable = []
    newly_closed = []
    for label, frame in families.items():
        if schema_ok(frame):
            still_representable.append(label)
        elif not list(old_validator.iter_errors(frame)):
            newly_closed.append(label)
    check(group, "every live failure family is now unrepresentable",
          not still_representable, "; ".join(still_representable[:2]))
    # one of the six -- a dependency declaring a requested unknown -- was
    # already unrepresentable before; the rest are closed here for the first
    # time, and each of them is a family the live run actually measured.
    check(group, "at least the five families the live run hit are newly closed",
          len(newly_closed) >= 5, f"{len(newly_closed)}/{len(families)} newly closed")
    MEASURES["live_families_closed"] = len(families)

    # --- group 5: closure measured on random schema-valid frames ----------
    group = "structural_closure"
    rng = random.Random(SEED)
    generated = schema_valid = valid_frames = 0
    closed_hits: list[str] = []
    fired: dict[str, int] = {}
    exceptions: list[str] = []
    while schema_valid < 1200 and generated < 6000:
        generated += 1
        frame = random_frame(rng, registry, projection, len(base_segments))
        if not schema_ok(frame):
            continue
        schema_valid += 1
        try:
            record = run(frame, base_segments)
        except Exception as error:                       # noqa: BLE001
            exceptions.append(f"{type(error).__name__}: {error}")
            continue
        if record["valid"]:
            valid_frames += 1
        for code in record["stage_codes"]["validator"]:
            fired[code] = fired.get(code, 0) + 1
            if code in STRUCTURALLY_CLOSED_CODES:
                closed_hits.append(code)
    check(group, f"no closed code fires on {schema_valid} random schema-valid frames",
          not closed_hits, "; ".join(sorted(set(closed_hits))[:5]))
    check(group, "the generator produced schema-valid frames at a high rate",
          schema_valid >= 1200, f"{schema_valid}/{generated}")
    check(group, "the random sweep raised nothing",
          not exceptions, "; ".join(exceptions[:2]))
    check(group, "the random sweep still produces both valid and invalid frames",
          0 < valid_frames < schema_valid, f"{valid_frames}/{schema_valid}")
    MEASURES["random_schema_valid"] = schema_valid
    MEASURES["random_generated"] = generated
    MEASURES["random_valid"] = valid_frames
    MEASURES["random_validator_codes"] = dict(
        sorted(fired.items(), key=lambda item: (-item[1], item[0]))[:10])
    MEASURES["closed_codes"] = len(STRUCTURALLY_CLOSED_CODES)

    # --- group 6: no emitted identity, no contamination -------------------
    group = "contract_hygiene"
    schema_text = json.dumps(schema, sort_keys=True)
    for label in ("clause_id", "atom_id", "alternative_id", "output_index"):
        check(group, f"the response schema never asks for {label}",
              f'"{label}"' not in schema_text)
    check(group, "the prompt forbids numeric labels",
          "do not emit numeric labels" in prompt.lower())

    def tokens(text: str) -> list[str]:
        return [t for t in "".join(
            c.lower() if c.isalnum() else " " for c in text).split() if t]

    contract_ngrams = {3: set(), 4: set()}
    for source in (tokens(prompt), tokens(schema_text)):
        for order in (3, 4):
            contract_ngrams[order].update(
                tuple(source[i:i + order]) for i in range(len(source) - order + 1))
    overlaps = {3: [], 4: []}
    verbatim = []
    prompt_lower, schema_lower = prompt.lower(), schema_text.lower()
    for query in queries.values():
        if query.lower() in prompt_lower or query.lower() in schema_lower:
            verbatim.append(query)
        query_tokens = tokens(query)
        for order in (3, 4):
            if any(tuple(query_tokens[i:i + order]) in contract_ngrams[order]
                   for i in range(len(query_tokens) - order + 1)):
                overlaps[order].append(query)
    check(group, "no control query appears verbatim in prompt or schema",
          not verbatim, "; ".join(verbatim[:2]))
    check(group, "no 3-token or 4-token n-gram is shared with a control query",
          not overlaps[3] and not overlaps[4],
          f"3-gram {len(overlaps[3])}, 4-gram {len(overlaps[4])}")
    contamination = {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps(
            schema, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest(),
        "controls_sha256": controls["source_controls_sha256"],
        "verbatim_query_matches": len(verbatim),
        "shared_3gram_queries": len(overlaps[3]),
        "shared_4gram_queries": len(overlaps[4]),
        "queries_audited": len(queries),
    }

    # --- group 7: every enum comes from the frozen registry ---------------
    group = "registry_derived"
    denotations = (set(registry["relations"]) | set(registry["reference_registry"])
                   | set(registry["clause_roles"]) | set(registry["speech_acts"])
                   | set(projection.all_proof_kinds(registry)))
    structural = {
        "supported", "typed_ambiguity", "unsupported", "out_of_registry",
        "bound", "unknown", "output", "from_prior_atom",
    }
    unexplained: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"const", "enum"}:
                    for item in (value if isinstance(value, list) else [value]):
                        if isinstance(item, str) and item not in denotations \
                                and item not in structural:
                            unexplained.append(item)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    check(group, "every schema const/enum is registry-derived or structural",
          not unexplained, "; ".join(sorted(set(unexplained))[:5]))
    check(group, "the prompt carries no example between quotes",
          '"' not in prompt.replace('\\"', ""))

    # --- report -----------------------------------------------------------
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
        "version": VERSION, "seed": SEED,
        "passed": passed, "total": total, "all_green": passed == total,
        "by_group": by_group, "measures": MEASURES,
        "contamination_audit": contamination,
        "network_calls": 0, "model_calls": 0, "live_runs": 0, "inference": False,
        "results": RESULTS,
    }
    if args.json:
        args.json.write_text(json.dumps(
            payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
