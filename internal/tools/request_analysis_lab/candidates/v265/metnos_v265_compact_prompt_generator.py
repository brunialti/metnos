#!/usr/bin/env python3
"""Derive V26.5 prompt by three exact structural substitutions."""
from __future__ import annotations

import hashlib
from pathlib import Path


PARENT = Path(
    "/opt/metnos/internal/tools/request_analysis_lab/candidates/v2641/"
    "metnos_v2641_typed_phase1.prompt.txt"
)
EXPECTED_PARENT_SHA256 = "2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f"

REPLACEMENTS = {
    "Emit a bounded normalized graph. atom_id values are consecutive and topological. A projection atom is the one decision-bearing primary relation of its semantic clause. A dependency atom is an auxiliary producer owned by that same clause. Every from_atom_output edge points to a lower atom_id and output_index; its source is either the registry-declared output of a dependency atom or an unknown output of a prior projection atom. No recursive embedded dependency is permitted.":
    "Emit a bounded normalized graph. Atoms are in topological array order; do not emit numeric labels for atoms, clauses, or output slots. Atoms in one semantic clause share exactly one source segment range; distinct clauses use distinct ranges. A projection is the clause's one decision-bearing primary relation; dependencies are auxiliary producers in that range. A from_prior_atom edge selects a prior array position with source_ordinal. Its output slot is derived and must be that source's dependency output or projection unknown. No recursive embedded dependency is permitted.",

    "The ordered argument slots, roles, and types come exclusively from the relation signature. Do not repeat role, type, adapter, or coercion in an argument. Binding kind bound names a typed reference. Binding kind unknown marks a projection output. Binding kind output occurs exactly once in a dependency atom, at dependency_output.slot. Binding kind from_atom_output consumes a prior output. The consumer slot must list that source type in accepted_outputs; the listed coercion is derived, never guessed or emitted. Every binding carries a compatible proof.":
    "The ordered argument slots, roles, and types come exclusively from the relation signature. Do not repeat role, type, adapter, coercion, or output-slot number. Binding kind bound names a typed reference; unknown marks a projection output; output occurs exactly once in a dependency at dependency_output.slot; from_prior_atom consumes the source_ordinal output. The consumer slot must accept the source type; coercion is derived, never emitted. Every binding carries a compatible proof.",

    "Before output verify status branch, one projection per semantic clause, atom/clause order, clause bounds, role/speech agreement, relation arity, registry output slot and adapter compatibility, claim/proof compatibility, exactly one declared output per dependency, every dependency output consumed, acyclic backward edges, dependency depth bound, and complete distinct alternatives.":
    "Before output verify status branch; one projection per distinct clause range; topological atom/range order; clause bounds; role/speech; relation arity; output/adapter and proof compatibility; one consumed output per dependency; every source_ordinal points backward to a compatible producer; depth bound; complete distinct alternatives.",
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def generate() -> str:
    parent_bytes = PARENT.read_bytes()
    if sha_bytes(parent_bytes) != EXPECTED_PARENT_SHA256:
        raise RuntimeError("parent prompt hash mismatch")
    text = parent_bytes.decode("utf-8")
    for old, new in REPLACEMENTS.items():
        if text.count(old) != 1:
            raise RuntimeError("prompt substitution is not unique")
        text = text.replace(old, new)
    for removed in ("atom_id", "clause_id", "output_index", "from_atom_output"):
        if removed in text:
            raise RuntimeError(f"removed output identifier remains: {removed}")
    if text.count("source_ordinal") != 3 or text.count("from_prior_atom") != 2:
        raise RuntimeError("compact edge contract count changed")
    return text


if __name__ == "__main__":
    print(generate(), end="")
