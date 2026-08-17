#!/usr/bin/env python3
"""V26.5 compact normal-form adapter probe; offline and model-output-free."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Callable


RUNNER = Path("/tmp/metnos_v2641_typed_phase1_runner.py")
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


v = load("metnos_v265_target", RUNNER)
p = load("metnos_v265_existing_probe", EXISTING_PROBE)


class UnsafeCompactGraph(ValueError):
    pass


def _source_output_slot(atom: dict[str, Any]) -> int:
    kinds = {"output"} if atom["atom_kind"] == "dependency" else {"unknown"}
    slots = [index for index, item in enumerate(atom["arguments"], 1) if item["kind"] in kinds]
    if len(slots) != 1:
        raise UnsafeCompactGraph("source must expose exactly one derivable output")
    return slots[0]


def _compact_atoms(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(atoms)
    by_id = {atom["atom_id"]: atom for atom in atoms}
    if [atom["atom_id"] for atom in atoms] != list(range(1, len(atoms) + 1)):
        raise UnsafeCompactGraph("non-topological full input")
    for atom in result:
        atom.pop("atom_id")
        atom.pop("clause_id")
        for binding in atom["arguments"]:
            if binding["kind"] != "from_atom_output":
                continue
            source = binding.pop("atom_id")
            declared_slot = binding.pop("output_index")
            if source not in by_id or declared_slot != _source_output_slot(by_id[source]):
                raise UnsafeCompactGraph("edge output is not derivable")
            binding["kind"] = "from_prior_atom"
            binding["source_ordinal"] = source
    return result


def compact_frame(frame: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(frame)
    if result["status"] == "supported":
        result["atoms"] = _compact_atoms(result["atoms"])
        for clause in result.get("unsupported_clauses", []):
            clause.pop("clause_id")
    elif result["status"] == "typed_ambiguity":
        for alternative in result["alternatives"]:
            alternative["atoms"] = _compact_atoms(alternative["atoms"])
        for clause in result.get("unsupported_clauses", []):
            clause.pop("clause_id")
    else:
        for clause in result["clauses"]:
            clause.pop("clause_id")
    return result


def _span(item: dict[str, Any]) -> tuple[int, int]:
    return item["clause_start_segment_id"], item["clause_end_segment_id"]


def _expand_graph(
    atoms: list[dict[str, Any]], unsupported: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    atoms = copy.deepcopy(atoms)
    unsupported = copy.deepcopy(unsupported)
    projections = [atom for atom in atoms if atom["atom_kind"] == "projection"]
    anchors: list[dict[str, Any]] = projections + unsupported
    spans = [_span(item) for item in anchors]
    if len(spans) != len(set(spans)):
        raise UnsafeCompactGraph("two semantic clauses claim the same source span")
    ordered_spans = sorted(spans)
    clause_by_span = {span: index for index, span in enumerate(ordered_spans, 1)}
    projection_spans = {_span(item) for item in projections}

    for ordinal, atom in enumerate(atoms, 1):
        span = _span(atom)
        if span not in projection_spans:
            raise UnsafeCompactGraph("dependency has no projection with the same clause span")
        atom["atom_id"] = ordinal
        atom["clause_id"] = clause_by_span[span]
    for clause in unsupported:
        clause["clause_id"] = clause_by_span[_span(clause)]

    for ordinal, atom in enumerate(atoms, 1):
        for binding in atom["arguments"]:
            if binding["kind"] != "from_prior_atom":
                continue
            source = binding.pop("source_ordinal")
            if not isinstance(source, int) or not 1 <= source < ordinal:
                raise UnsafeCompactGraph("edge does not point to a prior array item")
            binding["kind"] = "from_atom_output"
            binding["atom_id"] = source
            binding["output_index"] = _source_output_slot(atoms[source - 1])
    return atoms, unsupported


def _expand_unsupported(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(clauses)
    spans = [_span(item) for item in result]
    if len(spans) != len(set(spans)):
        raise UnsafeCompactGraph("unsupported clauses share one span")
    mapping = {span: index for index, span in enumerate(sorted(spans), 1)}
    for clause in result:
        clause["clause_id"] = mapping[_span(clause)]
    return result


def expand_frame(frame: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(frame)
    if result["status"] == "unsupported":
        result["clauses"] = _expand_unsupported(result["clauses"])
        return result
    common = result.get("unsupported_clauses", [])
    if result["status"] == "supported":
        result["atoms"], expanded_common = _expand_graph(result["atoms"], common)
        if common:
            result["unsupported_clauses"] = expanded_common
        return result
    agreed_common: list[dict[str, Any]] | None = None
    for alternative in result["alternatives"]:
        alternative["atoms"], candidate_common = _expand_graph(alternative["atoms"], common)
        if agreed_common is None:
            agreed_common = candidate_common
        elif agreed_common != candidate_common:
            raise UnsafeCompactGraph("alternatives disagree on common clause order")
    if common:
        result["unsupported_clauses"] = agreed_common
    return result


def encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def run() -> dict[str, Any]:
    roundtrips: list[dict[str, Any]] = []
    original_valid: Callable[..., None] = p.valid

    def audited_valid(test_id: str, value: dict[str, Any], check: Any = None) -> None:
        try:
            compact = compact_frame(value)
            expanded = expand_frame(compact)
            validation = p.v.validate_frame(expanded, p.SEGMENTS)
            passed = expanded == value and validation["valid"]
            detail = {
                "pass": passed,
                "full_bytes": encoded_size(value),
                "compact_bytes": encoded_size(compact),
            }
        except Exception as exc:  # diagnostic only; no query/model content
            detail = {"pass": False, "error_type": type(exc).__name__}
        roundtrips.append({"id": test_id, **detail})
        original_valid(test_id, value, check)

    p.valid = audited_valid
    existing = p.run()

    direct = p.frame(p.direct_atom())
    dependency = p.frame(p.location_dependency(), p.near_projection())
    negatives: list[dict[str, Any]] = []

    duplicate_span = p.frame(p.direct_atom(), p.filesystem_query(2, 2, 1))
    orphan = p.frame(p.location_dependency())
    collision = p.frame(p.direct_atom(), unsupported=[p.unsupported_clause(2, 1)])
    future_edge = compact_frame(dependency)
    future_edge["atoms"][1]["arguments"][1]["source_ordinal"] = 2
    ambiguous_source_output = compact_frame(p.frame(p.direct_atom(), p.send_projection(2, 2, 4, 1)))
    ambiguous_source_output["atoms"][0]["arguments"][2] = copy.deepcopy(
        ambiguous_source_output["atoms"][0]["arguments"][1]
    )

    for test_id, compact_or_full, already_compact in [
        ("duplicate_clause_span", duplicate_span, False),
        ("orphan_dependency_span", orphan, False),
        ("supported_unsupported_span_collision", collision, False),
        ("future_source_ordinal", future_edge, True),
        ("ambiguous_source_output", ambiguous_source_output, True),
    ]:
        rejected = False
        try:
            compact = compact_or_full if already_compact else compact_frame(compact_or_full)
            expand_frame(compact)
        except (UnsafeCompactGraph, KeyError, ValueError):
            rejected = True
        negatives.append({"id": test_id, "pass": rejected, "fail_closed": rejected})

    total_full = sum(item.get("full_bytes", 0) for item in roundtrips)
    total_compact = sum(item.get("compact_bytes", 0) for item in roundtrips)
    tests = roundtrips + negatives
    return {
        "version": "metnos.v26.5-compact-normal-form-probe/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "runner_sha256": sha(RUNNER),
        "existing_probe_sha256": sha(EXISTING_PROBE),
        "existing_probe_summary": existing["summary"],
        "summary": {
            "tests": len(tests),
            "passed": sum(item["pass"] for item in tests),
            "failed": sum(not item["pass"] for item in tests),
            "existing_positive_roundtrips": len(roundtrips),
            "new_fail_closed_controls": len(negatives),
            "full_bytes": total_full,
            "compact_bytes": total_compact,
            "byte_reduction_percent": round(100 * (total_full - total_compact) / total_full, 2),
        },
        "scope": "design adapter only; no decoder schema, prompt, freeze, model run, or live accuracy credit",
        "tests": tests,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
