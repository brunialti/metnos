"""Static closure, namespace and artifact preflight for holdout blueprint v0.2."""
from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

import build
from engine import ValidationError, digest, load_json, require


HERE = Path(__file__).resolve().parent
ALLOWED_FILES = set(build.LOCAL) | set(build.GENERATED) | {"process.freeze.json"}


def validate_authority(value: dict[str, Any]) -> None:
    require(value == build.authority(), "HASH_BINDING", "authority")
    require(value["precedence"] == ["registry", "canonical_schema", "normative_projection", "design_provenance"],
            "HASH_BINDING", "precedence")
    require(value["normative_projection"]["arbitration"] == "forbidden"
            and value["normative_projection"]["confidence"] == "high_only", "HASH_BINDING", "old contract override")
    require("intent_shadow_model_v0_1.schema.json" in value["semantic"]["canonical_schema"]["path"],
            "HASH_BINDING", "canonical schema")


def validate_canonical_schema() -> None:
    schema = load_json(build.CANONICAL_SCHEMA)
    registry = load_json(build.REGISTRY)
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "GOLD_SCHEMA", "draft")
    definitions = schema.get("$defs")
    require(type(definitions) is dict, "GOLD_SCHEMA", "defs")
    stack = [schema]
    while stack:
        value = stack.pop()
        if type(value) is dict:
            reference = value.get("$ref")
            if reference is not None:
                require(type(reference) is str and reference.startswith("#/$defs/")
                        and reference.split("/")[-1] in definitions, "GOLD_SCHEMA", "unresolved ref")
            stack.extend(value.values())
        elif type(value) is list:
            stack.extend(value)
    roots = schema["oneOf"]
    operation_root = next(item for item in roots if item["properties"]["kind"].get("const") == "operation_graph")
    require(operation_root["required"] == ["kind", "body"], "GOLD_SCHEMA", "canonical body")
    nodes = definitions["node"]["oneOf"]
    operation = next(item for item in nodes if item["properties"]["kind"].get("const") == "operation")
    barrier = next(item for item in nodes if item["properties"]["kind"].get("const") == "barrier")
    require(set(operation["properties"]["route"]["enum"]) == set(registry["operations"]), "GOLD_SCHEMA", "routes")
    require(barrier["properties"]["barrier"]["const"] in registry["barriers"], "GOLD_SCHEMA", "barrier")
    unrep = next(item for item in roots if item["properties"]["kind"].get("const") == "unrepresentable")
    require(set(unrep["properties"]["reason"]["enum"]) == set(registry["unrepresentable_reasons"]), "GOLD_SCHEMA", "reasons")


def validate_glossary(value: dict[str, Any]) -> None:
    registry = load_json(build.REGISTRY)
    require(value == build.glossary(), "HASH_BINDING", "glossary")
    require(set(value["operations"]) == set(registry["operations"]), "CAPABILITY_SCOPE", "routes")
    require(set(value["outside_families"]) == set(build.OUTSIDE), "CAPABILITY_SCOPE", "outside")
    require(value["languages"] == list(build.LANGUAGES), "CAPABILITY_SCOPE", "languages")
    forbidden = re.compile(r"`|§|\b(?:from_step|with_step|on_keys|base_path|executor|manifest|sha256)\b", re.I)
    route_names = set(registry["operations"])
    executor_names = {
        description["executor"]
        for metadata in load_json(build.PROJECTION_SOURCE)["operations"].values()
        for description in metadata["descriptions"]
    }
    for route, item in value["operations"].items():
        require(set(item) == {"author_gloss", "input_ports", "output_ports"}, "CLOSED_SCHEMA", route)
        gloss = item["author_gloss"]
        require(type(gloss) is str and bool(gloss.strip()) and not forbidden.search(gloss), "PROJECTION_LEAK", route)
        require(not any(name in gloss for name in route_names | executor_names), "PROJECTION_LEAK", route)
        require(item["input_ports"] == registry["operations"][route]["input_ports"]
                and item["output_ports"] == registry["operations"][route]["output_ports"], "CAPABILITY_SCOPE", route)


def validate_matrix(value: dict[str, Any]) -> None:
    slots = value.get("slots")
    require(type(slots) is list and len(slots) == 20, "CLOSED_SCHEMA", "matrix")
    require([item["proposal_id"] for item in slots] == [f"bp-{i:03d}" for i in range(1, 21)], "CLOSED_SCHEMA", "ids")
    require(Counter(item["language_tag"] for item in slots) == Counter({name: 2 for name in build.LANGUAGES}),
            "CLOSED_SCHEMA", "languages")
    cells = {item["cell"] for item in slots}
    require(cells == {
        "G1_SINGLE", "G2_COMPOUND_INDEPENDENT", "G3_COMPOUND_DEPENDENT", "G4_COVERAGE_BOUNDARY",
        "G5_LINGUISTIC_VARIATION", "S1_APPROVAL", "S2_NEGATION", "S3_CONDITIONAL_BRANCH",
        "S4_UNDO", "S5_MIXED_CONTROL", "S6_FALSE_ACTION_TRAP",
    }, "CLOSED_SCHEMA", "cells")
    require({item["subtype"] for item in slots if item["cell"] == "G4_COVERAGE_BOUNDARY"} == {"mixed", "outside_only"},
            "CLOSED_SCHEMA", "G4")


def validate_rubrics(value: dict[str, Any]) -> None:
    require(value == build.rubrics() and value["uncertain_is_fail"] is True, "HASH_BINDING", "rubrics")
    require(all(value[name] for name in ("native", "semantic", "cross_language", "isolation")), "CLOSED_SCHEMA", "rubrics")


def validate_mutations(value: dict[str, Any]) -> None:
    expected = build.mutation_catalog()
    require(value == expected, "HASH_BINDING", "mutations")
    rows = value["mutations"]
    require([item["id"] for item in rows] == [f"M{i:02d}" for i in range(1, 19)], "CLOSED_SCHEMA", "mutation ids")
    require(len({item["expected_code"] for item in rows}) == 18, "CLOSED_SCHEMA", "mutation codes")


def validate_source_closure() -> None:
    freeze = load_json(HERE / "process.freeze.json")
    local = freeze["local_files"]
    sources = freeze["source_files"]
    require(set(local) == set(build.LOCAL), "BUILD_DRIFT", "local closure")
    require(set(sources) == {str(path.relative_to(build.REPO)) for path in build.SOURCES}, "BUILD_DRIFT", "source closure")
    # All local Python imports must be stdlib or another hash-bound local module.
    local_modules = {Path(name).stem for name in build.LOCAL if name.endswith(".py")}
    allowed_external = {"__future__", "argparse", "ast", "collections", "copy", "dataclasses", "hashlib", "json", "pathlib", "re", "sys", "typing", "unittest"}
    for name in build.LOCAL:
        if not name.endswith(".py"): continue
        tree = ast.parse((HERE / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom): modules = [(node.module or "").split(".")[0]]
            else: continue
            for module in modules:
                require(module in local_modules or module in allowed_external, "BUILD_DRIFT", f"unbound import {module}")


def validate_namespace() -> None:
    actual = {str(path.relative_to(HERE)) for path in HERE.rglob("*") if path.is_file()}
    require(actual == ALLOWED_FILES, "PREMATURE_ARTIFACT", repr(sorted(actual - ALLOWED_FILES)))
    forbidden_fragments = ("query", "author", "gold", "run", "cache", "pyc")
    for item in actual:
        lowered = item.lower()
        if item in {"FORMAL_SPEC.md", "authority.json"}: continue
        require(not any(fragment in lowered for fragment in forbidden_fragments), "PREMATURE_ARTIFACT", item)


def preflight() -> dict[str, Any]:
    errors = build.check(); require(not errors, "BUILD_DRIFT", repr(errors))
    validate_namespace()
    validate_source_closure()
    authority = load_json(HERE / "authority.json"); validate_authority(authority)
    validate_canonical_schema()
    glossary = load_json(HERE / "glossary.json"); validate_glossary(glossary)
    validate_rubrics(load_json(HERE / "rubrics.json"))
    validate_matrix(load_json(HERE / "pilot_matrix.json"))
    validate_mutations(load_json(HERE / "mutation_catalog.json"))
    return {"status": "ready_for_synthetic_tests", "errors": [], "queries": 0, "case_gold": 0, "runs": 0,
            "cache_files": 0, "pilot_slots": 20, "authority_sha256": digest(authority)}


if __name__ == "__main__":
    try: result = preflight(); code = 0
    except ValidationError as exc: result = {"status": "fail", "code": exc.code}; code = 1
    print(json.dumps(result, sort_keys=True)); raise SystemExit(code)
