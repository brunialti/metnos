#!/usr/bin/env python3
"""Pure V26.5 compact/full normal-form adapter; no I/O and no network."""
from __future__ import annotations

import copy
from typing import Any


class UnsafeCompactGraph(ValueError):
    """The compact graph cannot be expanded without guessing semantics."""


def _source_output_slot(atom: dict[str, Any]) -> int:
    kinds = {"output"} if atom["atom_kind"] == "dependency" else {"unknown"}
    slots = [
        index for index, item in enumerate(atom["arguments"], 1)
        if item["kind"] in kinds
    ]
    if len(slots) != 1:
        raise UnsafeCompactGraph("source must expose exactly one derivable output")
    return slots[0]


def _span(item: dict[str, Any]) -> tuple[int, int]:
    return item["clause_start_segment_id"], item["clause_end_segment_id"]


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
    """Project a valid full frame into the compact decoder representation."""
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
    clause_by_span = {
        span: index for index, span in enumerate(sorted(spans), 1)
    }
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
    """Expand a compact frame without guessing any semantic relationship."""
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
        alternative["atoms"], candidate_common = _expand_graph(
            alternative["atoms"], common
        )
        if agreed_common is None:
            agreed_common = candidate_common
        elif agreed_common != candidate_common:
            raise UnsafeCompactGraph("alternatives disagree on common clause order")
    if common:
        result["unsupported_clauses"] = agreed_common
    return result

