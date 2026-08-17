#!/usr/bin/env python3
"""Offline proof that clause ids are canonical bookkeeping, not LLM semantics.

This probe does not read the live model output and never calls the network.  It
uses the frozen V26.4.1 validator and the independent V26.4 graph fixtures.
Canonicalization is deliberately fail-closed: it only renumbers an already
unambiguous partition of atoms/unsupported clauses into source-ordered clauses.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


RUNNER = Path("/tmp/metnos_v2641_typed_phase1_runner.py")
FIXTURES = Path("/tmp/metnos_v264_independent_graph_probe.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load("metnos_v2641_clause_probe_target", RUNNER)
p = load("metnos_v2641_clause_probe_fixtures", FIXTURES)


class UnsafeClausePartition(ValueError):
    """The identifiers encode an ambiguity that must remain fail-closed."""


def _normalize_graph(
    atoms: list[dict[str, Any]], unsupported: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    atoms = copy.deepcopy(atoms)
    unsupported = copy.deepcopy(unsupported)
    projections = [atom for atom in atoms if atom["atom_kind"] == "projection"]
    anchors = [
        (atom["clause_id"], atom["clause_start_segment_id"],
         atom["clause_end_segment_id"], "supported")
        for atom in projections
    ] + [
        (clause["clause_id"], clause["clause_start_segment_id"],
         clause["clause_end_segment_id"], "unsupported")
        for clause in unsupported
    ]
    old_ids = [item[0] for item in anchors]
    if len(old_ids) != len(set(old_ids)):
        raise UnsafeClausePartition("two clause anchors share one old id")
    if len({(item[1], item[2]) for item in anchors}) != len(anchors):
        raise UnsafeClausePartition("two distinct clauses have the same source span")

    span_by_id = {item[0]: (item[1], item[2]) for item in anchors}
    for atom in atoms:
        old_id = atom["clause_id"]
        if old_id not in span_by_id:
            raise UnsafeClausePartition("dependency has no projection anchor")
        if span_by_id[old_id] != (
            atom["clause_start_segment_id"], atom["clause_end_segment_id"],
        ):
            raise UnsafeClausePartition("one old id names inconsistent source spans")

    ordered = sorted(anchors, key=lambda item: (item[1], item[2]))
    mapping = {old_id: new_id for new_id, (old_id, _, _, _) in enumerate(ordered, 1)}
    for atom in atoms:
        atom["clause_id"] = mapping[atom["clause_id"]]
    for clause in unsupported:
        clause["clause_id"] = mapping[clause["clause_id"]]
    return atoms, unsupported


def canonicalize_clause_ids(frame: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(frame)
    common = result.get("unsupported_clauses", [])
    if result["status"] == "supported":
        atoms, normalized_common = _normalize_graph(result["atoms"], common)
        result["atoms"] = atoms
        if common:
            result["unsupported_clauses"] = normalized_common
        return result
    if result["status"] == "typed_ambiguity":
        normalized_common: list[dict[str, Any]] | None = None
        for alternative in result["alternatives"]:
            atoms, candidate_common = _normalize_graph(alternative["atoms"], common)
            alternative["atoms"] = atoms
            if normalized_common is None:
                normalized_common = candidate_common
            elif normalized_common != candidate_common:
                raise UnsafeClausePartition("alternatives disagree on common clause numbering")
        if common:
            result["unsupported_clauses"] = normalized_common
        return result
    return result


def without_clause_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: without_clause_ids(item)
            for key, item in value.items()
            if key != "clause_id"
        }
    if isinstance(value, list):
        return [without_clause_ids(item) for item in value]
    return value


def codes(frame: dict[str, Any]) -> list[str]:
    return [item["code"] for item in v.validate_frame(frame, p.SEGMENTS)["errors"]]


def renumber(frame: dict[str, Any], mapping: dict[int, int]) -> dict[str, Any]:
    changed = copy.deepcopy(frame)
    for atom in changed.get("atoms", []):
        atom["clause_id"] = mapping[atom["clause_id"]]
    for alternative in changed.get("alternatives", []):
        for atom in alternative["atoms"]:
            atom["clause_id"] = mapping[atom["clause_id"]]
    for clause in changed.get("unsupported_clauses", []):
        clause["clause_id"] = mapping[clause["clause_id"]]
    return changed


def run() -> dict[str, Any]:
    direct = p.frame(p.direct_atom())
    dependency = p.frame(p.location_dependency(), p.near_projection())
    independent = p.frame(p.filesystem_query(1, 1, 1), p.runtime_query(2, 2, 4))
    mixed = p.frame(p.direct_atom(), unsupported=[p.unsupported_clause(2, 4)])
    ambiguity = p.ambiguity(
        [p.polar_location(role="main_assertion", speech="assertion"), p.move_projection(2, 2, 4)],
        [p.polar_location(), p.move_projection(2, 2, 4)],
    )

    cases = {
        "direct_arbitrary_id": renumber(direct, {1: 41}),
        "dependency_shared_arbitrary_id": renumber(dependency, {1: 17}),
        "two_domains_nonconsecutive_ids": renumber(independent, {1: 90, 2: 12}),
        "mixed_supported_unsupported_ids": renumber(mixed, {1: 70, 2: 8}),
        "ambiguity_alternative_ids": renumber(ambiguity, {1: 31, 2: 44}),
    }
    tests: list[dict[str, Any]] = []
    for name, original in cases.items():
        before = codes(original)
        normalized = canonicalize_clause_ids(original)
        after = codes(normalized)
        tests.append({
            "id": name,
            "pass": (
                "clause_ids" in before
                and not after
                and without_clause_ids(original) == without_clause_ids(normalized)
                and canonicalize_clause_ids(normalized) == normalized
            ),
            "before_codes": sorted(set(before)),
            "after_codes": sorted(set(after)),
            "meaning_except_ids_unchanged": without_clause_ids(original) == without_clause_ids(normalized),
            "idempotent": canonicalize_clause_ids(normalized) == normalized,
        })

    invalid_collision = p.frame(p.direct_atom(), p.filesystem_query(2, 1, 4))
    invalid_orphan = p.frame(p.location_dependency())
    invalid_span = p.frame(p.location_dependency(), p.near_projection())
    invalid_span["atoms"][0]["clause_end_segment_id"] = 2
    for name, value in {
        "reject_two_projection_anchors_same_id": invalid_collision,
        "reject_dependency_without_projection": invalid_orphan,
        "reject_inconsistent_span_for_shared_id": invalid_span,
    }.items():
        rejected = False
        try:
            canonicalize_clause_ids(value)
        except UnsafeClausePartition:
            rejected = True
        tests.append({"id": name, "pass": rejected, "fail_closed": rejected})

    return {
        "version": "metnos.v26.4.1-clause-id-canonicalization-probe/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "live_frames_available": False,
        "runner_sha256": sha(RUNNER),
        "fixtures_sha256": sha(FIXTURES),
        "summary": {
            "tests": len(tests),
            "passed": sum(item["pass"] for item in tests),
            "failed": sum(not item["pass"] for item in tests),
        },
        "scope_limit": (
            "synthetic structural proof only; the redacted K1 artifact does not persist decoded frames, "
            "so it cannot provide a post-hoc live accuracy claim"
        ),
        "tests": tests,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
