#!/usr/bin/env python3
"""Small offline acceptance test for the self-contained V26.5.1 validator."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import types
from typing import Any

import jsonschema


HERE = Path(__file__).resolve().parent
VALIDATOR_PATH = HERE / "metnos_v2651_self_contained_validator.py"
EXPECTED_VALIDATOR_SHA256 = (
    "b19b3aa781ab3ac44b973db2aed3f6da889c29703addfbdf8251a5c60ae97656"
)
ALLOWED_IMPORT_ROOTS = {
    "__future__", "hashlib", "json", "jsonschema", "pathlib", "typing",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_validator() -> Any:
    source = VALIDATOR_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != EXPECTED_VALIDATOR_SHA256:
        raise RuntimeError("validator hash mismatch before execution")
    module = types.ModuleType("metnos_v2651_validator")
    module.__file__ = str(VALIDATOR_PATH)
    exec(compile(source, str(VALIDATOR_PATH), "exec"), module.__dict__)
    return module


def proof(kind: str, **anchors: int) -> dict[str, Any]:
    return {"kind": kind, **anchors}


def direct_location_frame() -> dict[str, Any]:
    return {
        "status": "supported",
        "atoms": [{
            "atom_id": 1,
            "atom_kind": "projection",
            "clause_id": 1,
            "clause_start_segment_id": 1,
            "clause_end_segment_id": 6,
            "relation": "spatial.located_at",
            "relation_proof": proof("predicate_morphology", predicate_segment_id=2),
            "arguments": [
                {
                    "kind": "bound", "ref": "actor.current",
                    "proof": proof("explicit_segment", start_segment_id=1, end_segment_id=1),
                },
                {"kind": "unknown", "proof": proof("interrogative_construction")},
                {
                    "kind": "bound", "ref": "time.current",
                    "proof": proof("utterance_context"),
                },
            ],
            "clause_role": "main_request",
            "clause_role_proof": proof("discourse_structure"),
            "speech_act": "open_question",
            "speech_act_proof": proof("clause_construction"),
        }],
    }


def dependency_frame() -> dict[str, Any]:
    return {
        "status": "supported",
        "atoms": [
            {
                "atom_id": 1,
                "atom_kind": "dependency",
                "clause_id": 1,
                "clause_start_segment_id": 1,
                "clause_end_segment_id": 6,
                "relation": "spatial.located_at",
                "relation_proof": proof("relation_composition"),
                "arguments": [
                    {
                        "kind": "bound", "ref": "actor.current",
                        "proof": proof("explicit_segment", start_segment_id=1, end_segment_id=1),
                    },
                    {"kind": "output", "proof": proof("relation_composition")},
                    {
                        "kind": "bound", "ref": "time.current",
                        "proof": proof("utterance_context"),
                    },
                ],
            },
            {
                "atom_id": 2,
                "atom_kind": "projection",
                "clause_id": 1,
                "clause_start_segment_id": 1,
                "clause_end_segment_id": 6,
                "relation": "spatial.near",
                "relation_proof": proof("predicate_morphology", predicate_segment_id=4),
                "arguments": [
                    {"kind": "unknown", "proof": proof("interrogative_construction")},
                    {
                        "kind": "from_atom_output", "atom_id": 1, "output_index": 2,
                        "proof": proof("relation_composition"),
                    },
                    {
                        "kind": "bound", "ref": "time.current",
                        "proof": proof("utterance_context"),
                    },
                ],
                "clause_role": "main_request",
                "clause_role_proof": proof("discourse_structure"),
                "speech_act": "open_question",
                "speech_act_proof": proof("clause_construction"),
            },
        ],
    }


def error_codes(result: dict[str, Any]) -> set[str]:
    return {item["code"] for item in result["errors"]}


def run() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"id": name, "pass": bool(passed), "detail": detail})

    source = VALIDATOR_PATH.read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    record("validator_hash_preimport", sha256(VALIDATOR_PATH) == EXPECTED_VALIDATOR_SHA256)
    record("imports_are_offline_allowlist", imported <= ALLOWED_IMPORT_ROOTS, sorted(imported))
    record(
        "no_transport_or_temporary_paths",
        not any(term in source for term in ("/tmp/", "urllib", "chat/completions")),
    )

    validator = load_validator()
    record("mechanical_closure_size", len(validator.EXTRACTED_FUNCTIONS) == 23)
    record("registry_consistency", validator.registry_errors() == [], validator.registry_errors())
    try:
        jsonschema.Draft202012Validator.check_schema(validator.live_schema())
        schema_valid = True
    except jsonschema.SchemaError:
        schema_valid = False
    record("draft_2020_12_schema", schema_valid)

    segments = [{"id": value} for value in range(1, 7)]
    direct = validator.validate_frame(direct_location_frame(), segments)
    record("direct_location_valid", direct["valid"], sorted(error_codes(direct)))
    dependency = validator.validate_frame(dependency_frame(), segments)
    record("dependency_consumer_valid", dependency["valid"], sorted(error_codes(dependency)))

    future = copy.deepcopy(dependency_frame())
    future["atoms"][1]["arguments"][1]["atom_id"] = 2
    future_result = validator.validate_frame(future, segments)
    record(
        "future_or_self_edge_rejected",
        not future_result["valid"] and "dependency_order" in error_codes(future_result),
        sorted(error_codes(future_result)),
    )

    wrong_proof = copy.deepcopy(direct_location_frame())
    wrong_proof["atoms"][0]["relation_proof"] = proof("relation_composition")
    wrong_proof_result = validator.validate_frame(wrong_proof, segments)
    record(
        "wrong_proof_family_rejected",
        not wrong_proof_result["valid"] and "proof_family" in error_codes(wrong_proof_result),
        sorted(error_codes(wrong_proof_result)),
    )

    wrong_arity = copy.deepcopy(direct_location_frame())
    wrong_arity["atoms"][0]["arguments"].pop()
    wrong_arity_result = validator.validate_frame(wrong_arity, segments)
    record(
        "wrong_arity_rejected",
        not wrong_arity_result["valid"],
        sorted(error_codes(wrong_arity_result)),
    )

    return {
        "version": "metnos.v26.5.1-validator-selftest/0.1",
        "network_calls": 0,
        "historical_runner_imports": 0,
        "validator_sha256": sha256(VALIDATOR_PATH),
        "summary": {
            "tests": len(checks),
            "passed": sum(item["pass"] for item in checks),
            "failed": sum(not item["pass"] for item in checks),
        },
        "checks": checks,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["failed"] == 0 else 1)
