#!/usr/bin/env python3
"""Offline structural/i18n audit for the V26.5 compact decoder schema."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates")
SCHEMA = ROOT / "v265/metnos_v265_compact.schema.json"
PARENT = ROOT / "v2641/metnos_v2641_typed_phase1.schema.json"
COMPACT_PROBE = ROOT / "v265/metnos_v265_compact_normal_form_probe.py"
EXISTING_PROBE = Path("/tmp/metnos_v264_independent_graph_probe.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enum_literals(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "const" and isinstance(item, str):
                found.add(item)
            elif key == "enum" and isinstance(item, list):
                found.update(entry for entry in item if isinstance(entry, str))
            else:
                found.update(enum_literals(item))
    elif isinstance(value, list):
        for item in value:
            found.update(enum_literals(item))
    return found


def property_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "properties" and isinstance(item, dict):
                found.update(item)
            found.update(property_names(item))
    elif isinstance(value, list):
        for item in value:
            found.update(property_names(item))
    return found


def object_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            found.update(object_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(object_keys(item))
    return found


def schema_valid(validator: Any, value: dict[str, Any]) -> bool:
    return not list(validator.iter_errors(value))


def run() -> dict[str, Any]:
    schema = json.loads(SCHEMA.read_text())
    parent = json.loads(PARENT.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    n = load("metnos_v265_schema_adapter", COMPACT_PROBE)
    p = load("metnos_v265_schema_existing", EXISTING_PROBE)

    positives: list[dict[str, Any]] = []
    original_valid = p.valid

    def audited_valid(test_id: str, value: dict[str, Any], check: Any = None) -> None:
        compact = n.compact_frame(value)
        positives.append({"id": test_id, "pass": schema_valid(validator, compact)})
        original_valid(test_id, value, check)

    p.valid = audited_valid
    existing = p.run()

    dependency = p.frame(p.location_dependency(), p.near_projection())
    compact_dependency = n.compact_frame(dependency)
    negative_values: list[tuple[str, dict[str, Any]]] = []

    changed = copy.deepcopy(compact_dependency)
    changed["atoms"][0]["atom_id"] = 1
    negative_values.append(("reject_atom_id", changed))
    changed = copy.deepcopy(compact_dependency)
    changed["atoms"][0]["clause_id"] = 1
    negative_values.append(("reject_clause_id", changed))
    changed = copy.deepcopy(compact_dependency)
    changed["atoms"][1]["arguments"][1]["output_index"] = 2
    negative_values.append(("reject_output_index", changed))
    changed = copy.deepcopy(compact_dependency)
    changed["atoms"][1]["arguments"][1]["kind"] = "from_atom_output"
    negative_values.append(("reject_old_edge_kind", changed))
    changed = copy.deepcopy(compact_dependency)
    changed["atoms"][1]["arguments"][1].pop("source_ordinal")
    negative_values.append(("require_source_ordinal", changed))

    negatives = [
        {"id": test_id, "pass": not schema_valid(validator, value)}
        for test_id, value in negative_values
    ]
    parent_literals = enum_literals(parent)
    compact_literals = enum_literals(schema)
    expected_literals = (parent_literals - {"from_atom_output"}) | {"from_prior_atom"}
    parent_properties = property_names(parent)
    compact_properties = property_names(schema)
    forbidden_serialized = {
        item: item in json.dumps(schema, ensure_ascii=False, sort_keys=True)
        for item in ("atom_id", "clause_id", "output_index", "from_atom_output")
    }
    language_fields = {"description", "examples", "title", "default"}
    audit_checks = [
        {"id": "schema_draft_valid", "pass": True},
        {"id": "no_removed_identifiers", "pass": not any(forbidden_serialized.values())},
        {"id": "only_new_property_is_source_ordinal", "pass": compact_properties - parent_properties == {"source_ordinal"}},
        {"id": "technical_literals_inherited_exactly", "pass": compact_literals == expected_literals},
        {"id": "no_natural_language_schema_fields", "pass": not (object_keys(schema) & language_fields)},
        {"id": "existing_probe_still_green", "pass": existing["summary"]["failed"] == 0},
    ]
    tests = positives + negatives + audit_checks
    return {
        "version": "metnos.v26.5-compact-schema-audit/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "schema_sha256": sha(SCHEMA),
        "parent_schema_sha256": sha(PARENT),
        "generator_sha256": sha(ROOT / "v265/metnos_v265_compact_schema_generator.py"),
        "summary": {
            "tests": len(tests),
            "passed": sum(item["pass"] for item in tests),
            "failed": sum(not item["pass"] for item in tests),
            "positive_schema_frames": len(positives),
            "negative_schema_mutations": len(negatives),
            "audit_checks": len(audit_checks),
            "schema_bytes": len(SCHEMA.read_bytes()),
        },
        "i18n_evidence": {
            "new_enum_literals": sorted(compact_literals - parent_literals),
            "removed_enum_literals": sorted(parent_literals - compact_literals),
            "new_property_names": sorted(compact_properties - parent_properties),
            "source_language_lexicon_added": False
        },
        "tests": tests,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
