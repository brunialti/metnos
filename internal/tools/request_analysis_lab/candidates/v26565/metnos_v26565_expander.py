#!/usr/bin/env python3
"""V26.5.6.5 total expander: clause-owned form -> frozen V26.5 normal form.

Pure, offline, deterministic. No network, no model, no I/O.

Contract (S2 of the V26.5.6.4 post mortem): this function is TOTAL on any
input that satisfies the V26.5.6.5 schema. It never raises and it never
aborts. Everything it cannot derive becomes a collected, non-fatal code, and
the frozen V26.5.3 validator remains the single semantic authority.

The output is exactly the normal form the frozen validator already accepts, so
the typed registry, the proof contract and every semantic check are reused
unchanged.

Derivations, all structural:
  clause_id       = 1-based index in the clause array of its scope
  atom_id         = 1-based index in the deterministic atom flattening
                    (per clause, in array order: dependencies then projection)
  clause span     = inherited by every atom from the clause that owns it
  output_index    = the source atom's single derivable output slot
  alternative_id  = 1-based index in the alternatives array
"""
from __future__ import annotations

import copy
from typing import Any

VERSION = "metnos.v26.5.6.5-total-expander/1.0"

KEY_PROJECTION = "projection"
KEY_DEPENDENCIES = "dependencies"
KEY_CLAUSES = "clauses"
KEY_ALTERNATIVES = "alternatives"
KEY_REASON = "reason"
SPAN_START = "clause_start_segment_id"
SPAN_END = "clause_end_segment_id"
BINDING_FROM_PRIOR = "from_prior_atom"
BINDING_SOURCE_ORDINAL = "source_ordinal"

# Non-fatal expansion codes. None of them aborts; each one is reported and the
# frame still reaches the validator.
CODE_EDGE_NOT_BACKWARD = "edge_source_ordinal_not_backward"
CODE_EDGE_OUT_OF_RANGE = "edge_source_ordinal_out_of_range"
CODE_OUTPUT_SLOT_UNDERIVABLE = "source_output_slot_not_derivable"
CODE_ALTERNATIVE_CLAUSE_MISMATCH = "alternatives_disagree_on_unsupported_clauses"

_FALLBACK_OUTPUT_INDEX = 1


def _is_projected(clause: dict[str, Any]) -> bool:
    return isinstance(clause, dict) and KEY_PROJECTION in clause


def _clause_head(clause: dict[str, Any], clause_id: int) -> dict[str, Any]:
    """Identity and span every atom of the clause inherits.

    Clause role lives on the projection atom and on unsupported clauses only:
    the frozen normal form does not carry it on a dependency atom.
    """
    return {
        "clause_id": clause_id,
        SPAN_START: clause.get(SPAN_START),
        SPAN_END: clause.get(SPAN_END),
    }


def _clause_role(clause: dict[str, Any]) -> dict[str, Any]:
    return {
        "clause_role": clause.get("clause_role"),
        "clause_role_proof": copy.deepcopy(clause.get("clause_role_proof")),
    }


def _derivable_output_slot(atom: dict[str, Any]) -> int | None:
    """The single slot an edge may consume from this atom.

    A dependency exposes its declared output; a projection exposes its
    requested unknown. Anything else is not derivable, and that is reported
    rather than raised.
    """
    wanted = "output" if atom.get("atom_kind") == "dependency" else "unknown"
    arguments = atom.get("arguments")
    if not isinstance(arguments, list):
        return None
    slots = [
        index for index, item in enumerate(arguments, 1)
        if isinstance(item, dict) and item.get("kind") == wanted
    ]
    if len(slots) != 1:
        return None
    return slots[0]


def _expand_clause_array(
    clauses: list[Any], codes: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (atoms, unsupported_clauses) in frozen normal form."""
    atoms: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    # pass 1: materialise atoms in the derived flattening order
    for position, clause in enumerate(clauses, 1):
        if not isinstance(clause, dict):
            continue
        head = _clause_head(clause, position)
        if not _is_projected(clause):
            entry = {**head, **_clause_role(clause)}
            entry[KEY_REASON] = clause.get(KEY_REASON)
            unsupported.append(entry)
            continue
        for dependency in clause.get(KEY_DEPENDENCIES, []) or []:
            atom = dict(head)
            atom["atom_kind"] = "dependency"
            atom["relation"] = (dependency or {}).get("relation")
            atom["relation_proof"] = copy.deepcopy((dependency or {}).get("relation_proof"))
            atom["arguments"] = copy.deepcopy((dependency or {}).get("arguments"))
            atoms.append(atom)
        projection = clause.get(KEY_PROJECTION) or {}
        atom = {**head, **_clause_role(clause)}
        atom["atom_kind"] = "projection"
        atom["relation"] = projection.get("relation")
        atom["relation_proof"] = copy.deepcopy(projection.get("relation_proof"))
        atom["speech_act"] = projection.get("speech_act")
        atom["speech_act_proof"] = copy.deepcopy(projection.get("speech_act_proof"))
        atom["arguments"] = copy.deepcopy(projection.get("arguments"))
        atoms.append(atom)

    for ordinal, atom in enumerate(atoms, 1):
        atom["atom_id"] = ordinal

    # pass 2: resolve inter-clause edges against the derived order
    for ordinal, atom in enumerate(atoms, 1):
        arguments = atom.get("arguments")
        if not isinstance(arguments, list):
            continue
        for binding in arguments:
            if not isinstance(binding, dict) or binding.get("kind") != BINDING_FROM_PRIOR:
                continue
            source = binding.pop(BINDING_SOURCE_ORDINAL, None)
            binding["kind"] = "from_atom_output"
            binding["atom_id"] = source
            if not isinstance(source, int) or isinstance(source, bool):
                binding["output_index"] = _FALLBACK_OUTPUT_INDEX
                codes.append(CODE_EDGE_OUT_OF_RANGE)
                continue
            if source >= ordinal:
                # reported, not repaired: the frozen validator also reports it
                codes.append(CODE_EDGE_NOT_BACKWARD)
            if not 1 <= source <= len(atoms):
                binding["output_index"] = _FALLBACK_OUTPUT_INDEX
                codes.append(CODE_EDGE_OUT_OF_RANGE)
                continue
            slot = _derivable_output_slot(atoms[source - 1])
            if slot is None:
                binding["output_index"] = _FALLBACK_OUTPUT_INDEX
                codes.append(CODE_OUTPUT_SLOT_UNDERIVABLE)
            else:
                binding["output_index"] = slot
    return atoms, unsupported


def expand_frame(frame: Any) -> tuple[dict[str, Any], list[str]]:
    """Expand a clause-owned frame. Total: never raises on schema-valid input."""
    codes: list[str] = []
    if not isinstance(frame, dict):
        return {}, ["frame_is_not_an_object"]
    source = copy.deepcopy(frame)
    status = source.get("status")

    if status == "unsupported":
        clauses = source.get(KEY_CLAUSES) or []
        expanded = []
        for position, clause in enumerate(clauses, 1):
            if not isinstance(clause, dict):
                continue
            entry = {**_clause_head(clause, position), **_clause_role(clause)}
            entry[KEY_REASON] = clause.get(KEY_REASON)
            expanded.append(entry)
        return {"status": "unsupported", "clauses": expanded}, codes

    if status == "supported":
        atoms, unsupported = _expand_clause_array(source.get(KEY_CLAUSES) or [], codes)
        result: dict[str, Any] = {"status": "supported", "atoms": atoms}
        if unsupported:
            result["unsupported_clauses"] = unsupported
        return result, codes

    if status == "typed_ambiguity":
        alternatives = source.get(KEY_ALTERNATIVES) or []
        expanded_alternatives = []
        common: list[dict[str, Any]] | None = None
        for index, alternative in enumerate(alternatives, 1):
            clauses = (alternative or {}).get(KEY_CLAUSES) or []
            atoms, unsupported = _expand_clause_array(clauses, codes)
            expanded_alternatives.append({"alternative_id": index, "atoms": atoms})
            if common is None:
                common = unsupported
            elif unsupported != common:
                codes.append(CODE_ALTERNATIVE_CLAUSE_MISMATCH)
        result = {"status": "typed_ambiguity", "alternatives": expanded_alternatives}
        if common:
            result["unsupported_clauses"] = common
        return result, codes

    return {"status": status}, ["unknown_status_branch"]


# --------------------------------------------------------------------------
# structural fingerprint (S4): query-free, emitted on success AND on failure
# --------------------------------------------------------------------------

def _binding_kinds(atom: dict[str, Any]) -> list[str]:
    arguments = atom.get("arguments")
    if not isinstance(arguments, list):
        return []
    return [
        item.get("kind") for item in arguments
        if isinstance(item, dict) and isinstance(item.get("kind"), str)
    ]


def _proof_kinds(node: Any, sink: list[str]) -> None:
    if isinstance(node, dict):
        kind = node.get("kind")
        if isinstance(kind, str) and set(node) <= {
            "kind", "start_segment_id", "end_segment_id", "predicate_segment_id",
        }:
            sink.append(kind)
        for value in node.values():
            _proof_kinds(value, sink)
    elif isinstance(node, list):
        for item in node:
            _proof_kinds(item, sink)


def structural_fingerprint(frame: Any) -> dict[str, Any]:
    """A structural, query-free description of whatever the model emitted.

    Contains only integers, booleans and technical denotations from the frozen
    registry plus the fixed structural vocabulary. It never contains request
    text, segment text, or a source span: spans are reduced to the single bit
    that mattered in the V26.5.6.4 failure, namely whether they collide.
    """
    if not isinstance(frame, dict):
        return {
            "status": None, "well_formed": False, "clause_count": 0,
            "projected_clause_count": 0, "unsupported_clause_count": 0,
            "alternative_count": 0, "atom_count": 0, "dependency_count": 0,
            "edge_count": 0, "relations": [], "clause_roles": [],
            "speech_acts": [], "binding_kinds": [], "proof_kinds": [],
            "distinct_clause_spans": None, "max_dependencies_in_a_clause": 0,
        }
    status = frame.get("status")
    scopes: list[list[Any]] = []
    if status == "typed_ambiguity":
        for alternative in frame.get(KEY_ALTERNATIVES) or []:
            scopes.append((alternative or {}).get(KEY_CLAUSES) or [])
    else:
        scopes.append(frame.get(KEY_CLAUSES) or [])

    relations: list[str] = []
    clause_roles: list[str] = []
    speech_acts: list[str] = []
    binding_kinds: list[str] = []
    proof_kinds: list[str] = []
    projected = unsupported = atoms = dependencies = edges = 0
    max_dependencies = 0
    spans: list[tuple[Any, Any]] = []
    clause_total = 0

    for clauses in scopes:
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            clause_total += 1
            spans.append((clause.get(SPAN_START), clause.get(SPAN_END)))
            role = clause.get("clause_role")
            if isinstance(role, str):
                clause_roles.append(role)
            if not _is_projected(clause):
                unsupported += 1
                continue
            projected += 1
            clause_dependencies = clause.get(KEY_DEPENDENCIES) or []
            max_dependencies = max(max_dependencies, len(clause_dependencies))
            for dependency in clause_dependencies:
                if not isinstance(dependency, dict):
                    continue
                atoms += 1
                dependencies += 1
                if isinstance(dependency.get("relation"), str):
                    relations.append(dependency["relation"])
                kinds = _binding_kinds(dependency)
                binding_kinds.extend(kinds)
                edges += kinds.count(BINDING_FROM_PRIOR)
            projection = clause.get(KEY_PROJECTION)
            if isinstance(projection, dict):
                atoms += 1
                if isinstance(projection.get("relation"), str):
                    relations.append(projection["relation"])
                if isinstance(projection.get("speech_act"), str):
                    speech_acts.append(projection["speech_act"])
                kinds = _binding_kinds(projection)
                binding_kinds.extend(kinds)
                edges += kinds.count(BINDING_FROM_PRIOR)
    _proof_kinds(frame, proof_kinds)

    hashable_spans = [item for item in spans if all(isinstance(v, int) for v in item)]
    return {
        "status": status if isinstance(status, str) else None,
        "well_formed": isinstance(status, str),
        "clause_count": clause_total,
        "projected_clause_count": projected,
        "unsupported_clause_count": unsupported,
        "alternative_count": len(scopes) if status == "typed_ambiguity" else 0,
        "atom_count": atoms,
        "dependency_count": dependencies,
        "edge_count": edges,
        "max_dependencies_in_a_clause": max_dependencies,
        "relations": sorted(relations),
        "clause_roles": sorted(clause_roles),
        "speech_acts": sorted(speech_acts),
        "binding_kinds": sorted(binding_kinds),
        "proof_kinds": sorted(proof_kinds),
        # the one bit the V26.5.6.4 failure turned on; no span is disclosed
        "distinct_clause_spans": (
            len(set(hashable_spans)) == len(hashable_spans)
            if len(hashable_spans) == len(spans) else None
        ),
    }
