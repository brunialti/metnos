#!/usr/bin/env python3
"""V26.5.6.6 total expander: clause-owned form -> frozen V26.5 normal form.

Pure, offline, deterministic. No network, no model, no I/O.

Contract (S2 of the V26.5.6.4 post mortem, hardened after blocker B1 of the
independent V26.5.6.5 review): these functions are TOTAL on ANY input, not
only on schema-valid input. They never raise and they never abort. Everything
that cannot be derived becomes a collected, non-fatal code, and the frozen
V26.5.3 validator remains the single semantic authority.

V26.5.6.5 claimed totality but reached its containers with
``value.get(KEY, []) or []``, an idiom that neutralises None and falsy values
and NOT a scalar; five one-line inputs made it raise. Every container access
here goes through _as_list/_as_dict, so a scalar in any position degrades to
an empty container plus a code.

The output is exactly the normal form the frozen validator already accepts, so
the typed registry, the proof contract and every semantic check are reused
unchanged.

Derivations, all structural:
  clause_id       = 1-based index in the clause array of its scope
  atom_id         = 1-based index in the deterministic atom flattening
                    (per clause, in array order: dependencies then projection)
  clause span     = inherited by every atom from the clause that owns it
  output_index    = the source atom's single derivable output slot
  alternative_id  = 1-based index in the reading arrays of the clause skeleton
"""
from __future__ import annotations

import copy
from typing import Any

VERSION = "metnos.v26.5.6.6-total-expander/1.0"

KEY_PROJECTION = "projection"
KEY_DEPENDENCIES = "dependencies"
KEY_CLAUSES = "clauses"
KEY_READINGS = "readings"
KEY_REASON = "reason"
SPAN_START = "clause_start_segment_id"
SPAN_END = "clause_end_segment_id"
BINDING_FROM_PRIOR = "from_prior_atom"
BINDING_SOURCE_ORDINAL = "source_ordinal"

STATUS_SUPPORTED = "supported"
STATUS_TYPED_AMBIGUITY = "typed_ambiguity"
STATUS_UNSUPPORTED = "unsupported"

# Non-fatal expansion codes. None of them aborts; each one is reported and the
# frame still reaches the validator.
CODE_FRAME_NOT_AN_OBJECT = "frame_is_not_an_object"
CODE_UNKNOWN_STATUS = "unknown_status_branch"
CODE_CLAUSE_NOT_AN_OBJECT = "clause_is_not_an_object"
CODE_CLAUSE_SHAPE = "clause_carries_neither_projection_nor_reason"
CODE_EDGE_NOT_BACKWARD = "edge_source_ordinal_not_backward"
CODE_EDGE_OUT_OF_RANGE = "edge_source_ordinal_out_of_range"
CODE_OUTPUT_SLOT_UNDERIVABLE = "source_output_slot_not_derivable"
CODE_NO_READINGS = "no_clause_carries_readings"
CODE_READING_COUNT_MISMATCH = "clauses_disagree_on_reading_count"
CODE_READING_MISSING = "clause_reading_is_missing"
CODE_ATOM_BUDGET = "derived_atom_count_over_frozen_bound"

_FALLBACK_OUTPUT_INDEX = 1


def _as_list(value: Any) -> list[Any]:
    """Every list-shaped read goes through here: a scalar becomes empty."""
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    """Every object-shaped read goes through here: a scalar becomes empty."""
    return value if isinstance(value, dict) else {}


def _is_projected(clause: Any) -> bool:
    return isinstance(clause, dict) and KEY_PROJECTION in clause


def _clause_head(clause: dict[str, Any], clause_id: int) -> dict[str, Any]:
    """Identity and span every atom of the clause inherits."""
    return {
        "clause_id": clause_id,
        SPAN_START: clause.get(SPAN_START),
        SPAN_END: clause.get(SPAN_END),
    }


def _clause_role(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "clause_role": body.get("clause_role"),
        "clause_role_proof": copy.deepcopy(body.get("clause_role_proof")),
    }


def _derivable_output_slot(atom: dict[str, Any]) -> int | None:
    """The single slot an edge may consume from this atom.

    A dependency exposes its declared output; a projection exposes its
    requested unknown. Anything else is not derivable, and that is reported
    rather than raised.
    """
    wanted = "output" if atom.get("atom_kind") == "dependency" else "unknown"
    slots = [
        index for index, item in enumerate(_as_list(atom.get("arguments")), 1)
        if isinstance(item, dict) and item.get("kind") == wanted
    ]
    if len(slots) != 1:
        return None
    return slots[0]


def _append_analysis_atoms(
    head: dict[str, Any], body: dict[str, Any], atoms: list[dict[str, Any]],
) -> None:
    """Materialise one clause analysis: dependencies first, then projection."""
    for dependency in _as_list(body.get(KEY_DEPENDENCIES)):
        dependency = _as_dict(dependency)
        atom = dict(head)
        atom["atom_kind"] = "dependency"
        atom["relation"] = dependency.get("relation")
        atom["relation_proof"] = copy.deepcopy(dependency.get("relation_proof"))
        atom["arguments"] = copy.deepcopy(dependency.get("arguments"))
        atoms.append(atom)
    projection = _as_dict(body.get(KEY_PROJECTION))
    atom = {**head, **_clause_role(body)}
    atom["atom_kind"] = "projection"
    atom["relation"] = projection.get("relation")
    atom["relation_proof"] = copy.deepcopy(projection.get("relation_proof"))
    atom["speech_act"] = projection.get("speech_act")
    atom["speech_act_proof"] = copy.deepcopy(projection.get("speech_act_proof"))
    atom["arguments"] = copy.deepcopy(projection.get("arguments"))
    atoms.append(atom)


def _resolve_scope(
    atoms: list[dict[str, Any]], codes: list[str], max_atoms: int | None,
) -> None:
    """Number one flattening and resolve its inter-clause edges."""
    for ordinal, atom in enumerate(atoms, 1):
        atom["atom_id"] = ordinal
    if max_atoms is not None and len(atoms) > max_atoms:
        # The frozen atom budget is a registry resource limit, not a shape:
        # a sum over clauses is not expressible in JSON Schema, so it is
        # named here instead of surfacing as an opaque validator schema code.
        codes.append(CODE_ATOM_BUDGET)
    for ordinal, atom in enumerate(atoms, 1):
        for binding in _as_list(atom.get("arguments")):
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


def _unsupported_entry(clause: dict[str, Any], clause_id: int) -> dict[str, Any]:
    entry = {**_clause_head(clause, clause_id), **_clause_role(clause)}
    entry[KEY_REASON] = clause.get(KEY_REASON)
    return entry


def _expand_supported(
    clauses: Any, codes: list[str], max_atoms: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (atoms, unsupported_clauses) in frozen normal form."""
    atoms: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for position, clause in enumerate(_as_list(clauses), 1):
        if not isinstance(clause, dict):
            # the identity slot is still consumed: the model emitted a clause
            codes.append(CODE_CLAUSE_NOT_AN_OBJECT)
            continue
        if _is_projected(clause):
            _append_analysis_atoms(_clause_head(clause, position), clause, atoms)
            continue
        if KEY_REASON not in clause:
            codes.append(CODE_CLAUSE_SHAPE)
        unsupported.append(_unsupported_entry(clause, position))
    _resolve_scope(atoms, codes, max_atoms)
    return atoms, unsupported


def _expand_ambiguity(
    clauses: Any, codes: list[str], max_atoms: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Expand the shared clause skeleton into one atom list per alternative.

    The skeleton is shared by construction, so every alternative carries the
    same clause identities, spans and out-of-registry membership. The frozen
    validator compares exactly that footprint across alternatives, therefore
    clause_ids, alternative_coverage, clause_limit and alternative_order
    cannot be reached by a schema-valid frame any more.
    """
    clause_list = _as_list(clauses)
    counts = [
        len(_as_list(clause.get(KEY_READINGS)))
        for clause in clause_list
        if isinstance(clause, dict) and KEY_READINGS in clause
    ]
    alternatives_count = max(counts) if counts else 0
    if alternatives_count == 0:
        codes.append(CODE_NO_READINGS)
        return [], []
    if any(count != alternatives_count for count in counts):
        # best effort, explicitly reported: the shortest reading array is
        # clamped instead of being dropped, so no alternative loses a clause
        codes.append(CODE_READING_COUNT_MISMATCH)

    unsupported: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    for index in range(alternatives_count):
        atoms: list[dict[str, Any]] = []
        for position, clause in enumerate(clause_list, 1):
            if not isinstance(clause, dict):
                if index == 0:
                    codes.append(CODE_CLAUSE_NOT_AN_OBJECT)
                continue
            if KEY_READINGS not in clause:
                if index == 0:
                    if KEY_REASON not in clause:
                        codes.append(CODE_CLAUSE_SHAPE)
                    unsupported.append(_unsupported_entry(clause, position))
                continue
            readings = _as_list(clause.get(KEY_READINGS))
            if not readings:
                if index == 0:
                    codes.append(CODE_READING_MISSING)
                continue
            body = _as_dict(readings[min(index, len(readings) - 1)])
            _append_analysis_atoms(_clause_head(clause, position), body, atoms)
        _resolve_scope(atoms, codes, max_atoms)
        alternatives.append({"alternative_id": index + 1, "atoms": atoms})
    return alternatives, unsupported


def expand_frame(
    frame: Any, *, max_atoms: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Expand a clause-owned frame. Total: it never raises, on any input.

    ``max_atoms`` is the frozen registry budget, supplied by the caller so
    this module stays pure. Codes are returned de-duplicated and sorted.
    """
    codes: list[str] = []
    if not isinstance(frame, dict):
        return {}, [CODE_FRAME_NOT_AN_OBJECT]
    source = copy.deepcopy(frame)
    status = source.get("status")

    if status == STATUS_UNSUPPORTED:
        expanded = []
        for position, clause in enumerate(_as_list(source.get(KEY_CLAUSES)), 1):
            if not isinstance(clause, dict):
                codes.append(CODE_CLAUSE_NOT_AN_OBJECT)
                continue
            expanded.append(_unsupported_entry(clause, position))
        return ({"status": STATUS_UNSUPPORTED, "clauses": expanded},
                sorted(set(codes)))

    if status == STATUS_SUPPORTED:
        atoms, unsupported = _expand_supported(
            source.get(KEY_CLAUSES), codes, max_atoms)
        result: dict[str, Any] = {"status": STATUS_SUPPORTED, "atoms": atoms}
        if unsupported:
            result["unsupported_clauses"] = unsupported
        return result, sorted(set(codes))

    if status == STATUS_TYPED_AMBIGUITY:
        alternatives, unsupported = _expand_ambiguity(
            source.get(KEY_CLAUSES), codes, max_atoms)
        result = {"status": STATUS_TYPED_AMBIGUITY, "alternatives": alternatives}
        if unsupported:
            result["unsupported_clauses"] = unsupported
        return result, sorted(set(codes))

    return {"status": status}, [CODE_UNKNOWN_STATUS]


# --------------------------------------------------------------------------
# structural fingerprint (S4): query-free, emitted on success AND on failure
# --------------------------------------------------------------------------

class _ClosedVocabulary:
    """Keeps only strings of the frozen technical vocabulary, counts the rest.

    Blocker B2 of the independent V26.5.6.5 review: on a schema-invalid frame
    the fingerprint copied model-controlled strings verbatim, so the record a
    query-free batch would persist could carry request material. Here nothing
    outside the closed vocabulary ever reaches the fingerprint.
    """

    def __init__(self, allowed: Any) -> None:
        self.allowed = allowed if isinstance(allowed, (set, frozenset)) else frozenset()
        self.rejected = 0

    def keep(self, value: Any) -> str | None:
        if isinstance(value, str) and value in self.allowed:
            return value
        if value is not None:
            self.rejected += 1
        return None

    def collect(self, value: Any, sink: list[str]) -> None:
        kept = self.keep(value)
        if kept is not None:
            sink.append(kept)


def _binding_kinds(atom: Any, vocabulary: _ClosedVocabulary) -> list[str]:
    kinds: list[str] = []
    for item in _as_list(_as_dict(atom).get("arguments")):
        if isinstance(item, dict):
            vocabulary.collect(item.get("kind"), kinds)
    return kinds


def _raw_binding_kinds(atom: Any) -> list[str]:
    """Unfiltered kinds, used only to count edges. Never emitted."""
    return [
        item.get("kind") for item in _as_list(_as_dict(atom).get("arguments"))
        if isinstance(item, dict)
    ]


def _proof_kinds(node: Any, sink: list[str], vocabulary: _ClosedVocabulary) -> None:
    if isinstance(node, dict):
        kind = node.get("kind")
        if isinstance(kind, str) and set(node) <= {
            "kind", "start_segment_id", "end_segment_id", "predicate_segment_id",
        }:
            vocabulary.collect(kind, sink)
        for value in node.values():
            _proof_kinds(value, sink, vocabulary)
    elif isinstance(node, list):
        for item in node:
            _proof_kinds(item, sink, vocabulary)


def _empty_fingerprint() -> dict[str, Any]:
    return {
        "status": None, "well_formed": False, "clause_count": 0,
        "projected_clause_count": 0, "unsupported_clause_count": 0,
        "alternative_count": 0, "reading_count": 0, "atom_count": 0,
        "dependency_count": 0, "edge_count": 0,
        "max_dependencies_in_a_clause": 0, "relations": [], "clause_roles": [],
        "speech_acts": [], "binding_kinds": [], "proof_kinds": [],
        "distinct_clause_spans": None, "out_of_vocabulary_strings": 0,
    }


def structural_fingerprint(frame: Any, *, vocabulary: Any) -> dict[str, Any]:
    """A structural, query-free description of whatever the model emitted.

    Contains only integers, booleans and technical denotations of the frozen
    registry plus the fixed structural vocabulary. It never contains request
    text, segment text, or a source span: spans are reduced to the single bit
    that mattered in the V26.5.6.4 failure, namely whether they collide.
    Total: it never raises, on any input.
    """
    closed = _ClosedVocabulary(vocabulary)
    if not isinstance(frame, dict):
        return _empty_fingerprint()
    status = frame.get("status")
    clauses = _as_list(frame.get(KEY_CLAUSES))

    relations: list[str] = []
    clause_roles: list[str] = []
    speech_acts: list[str] = []
    binding_kinds: list[str] = []
    proof_kinds: list[str] = []
    projected = unsupported = atoms = dependencies = edges = 0
    readings_total = 0
    alternatives = 0
    max_dependencies = 0
    spans: list[tuple[Any, Any]] = []
    clause_total = 0

    def account(body: Any) -> None:
        nonlocal atoms, dependencies, edges, max_dependencies
        body = _as_dict(body)
        closed.collect(body.get("clause_role"), clause_roles)
        clause_dependencies = _as_list(body.get(KEY_DEPENDENCIES))
        max_dependencies = max(max_dependencies, len(clause_dependencies))
        for dependency in clause_dependencies:
            if not isinstance(dependency, dict):
                continue
            atoms += 1
            dependencies += 1
            closed.collect(dependency.get("relation"), relations)
            binding_kinds.extend(_binding_kinds(dependency, closed))
            edges += _raw_binding_kinds(dependency).count(BINDING_FROM_PRIOR)
        projection = body.get(KEY_PROJECTION)
        if isinstance(projection, dict):
            atoms += 1
            closed.collect(projection.get("relation"), relations)
            closed.collect(projection.get("speech_act"), speech_acts)
            binding_kinds.extend(_binding_kinds(projection, closed))
            edges += _raw_binding_kinds(projection).count(BINDING_FROM_PRIOR)

    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        clause_total += 1
        spans.append((clause.get(SPAN_START), clause.get(SPAN_END)))
        if KEY_READINGS in clause:
            clause_readings = _as_list(clause.get(KEY_READINGS))
            alternatives = max(alternatives, len(clause_readings))
            readings_total += len(clause_readings)
            projected += 1
            for body in clause_readings:
                account(body)
            continue
        if _is_projected(clause):
            projected += 1
            account(clause)
            continue
        unsupported += 1
        closed.collect(clause.get("clause_role"), clause_roles)
    _proof_kinds(frame, proof_kinds, closed)

    hashable_spans = [item for item in spans if all(isinstance(v, int) for v in item)]
    return {
        "status": closed.keep(status),
        "well_formed": isinstance(status, str),
        "clause_count": clause_total,
        "projected_clause_count": projected,
        "unsupported_clause_count": unsupported,
        "alternative_count": alternatives,
        "reading_count": readings_total,
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
        "out_of_vocabulary_strings": closed.rejected,
    }
