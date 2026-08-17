#!/usr/bin/env python3
"""V26.5.6.5 clause-owned projection of the frozen typed registry.

Pure, offline, deterministic. No network, no model, no filesystem writes.

This module is the single source of truth for the V26.5.6.5 request contract.
Both the response schema and the system prompt are *derived* from the frozen
V26.4.1 typed registry, so relation signatures, reference types, proof families
and limits cannot drift between the two. Nothing here contains a natural
language token of any source language: every literal is either a fixed
structural key or a technical denotation taken from the registry.

Design (S1 of the V26.5.6.4 post mortem): clause identity lives in the
*document structure*, not in an inferred key. A clause owns exactly one
projection and its own dependencies. The source span is demoted to pure
evidence: it is never a key, never unique-by-contract, never compared for
equality between atoms.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

VERSION = "metnos.v26.5.6.5-registry-projection/1.0"
EXPECTED_REGISTRY_SHA256 = (
    "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f"
)

# Structural keys of the clause-owned normal form. Fixed, technical, and
# language independent; they are the only free-form strings this module emits.
KEY_STATUS = "status"
KEY_CLAUSES = "clauses"
KEY_ALTERNATIVES = "alternatives"
KEY_PROJECTION = "projection"
KEY_DEPENDENCIES = "dependencies"
KEY_REASON = "reason"
REASON_OUT_OF_REGISTRY = "out_of_registry"
STATUS_SUPPORTED = "supported"
STATUS_TYPED_AMBIGUITY = "typed_ambiguity"
STATUS_UNSUPPORTED = "unsupported"
SPAN_START = "clause_start_segment_id"
SPAN_END = "clause_end_segment_id"
BINDING_FROM_PRIOR = "from_prior_atom"
BINDING_SOURCE_ORDINAL = "source_ordinal"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    )


def load_registry(registry_bytes: bytes) -> dict[str, Any]:
    """Load the frozen registry from exact bytes, fail closed on drift."""
    if type(registry_bytes) is not bytes:
        raise TypeError("registry must be exact bytes")
    if sha_bytes(registry_bytes) != EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("registry hash mismatch")
    registry = json.loads(registry_bytes)
    if type(registry) is not dict:
        raise RuntimeError("registry is not a JSON object")
    for required in ("relations", "reference_registry", "proof_families",
                     "limits", "clause_roles", "speech_acts"):
        if required not in registry:
            raise RuntimeError(f"registry lacks {required}")
    return registry


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def _proof_variant(kind: str) -> dict[str, Any]:
    """Mirrors the frozen validator's proof variant shape exactly."""
    props: dict[str, Any] = {"kind": {"const": kind}}
    required = ["kind"]
    if kind == "explicit_segment":
        props.update({
            "start_segment_id": {"type": "integer", "minimum": 1},
            "end_segment_id": {"type": "integer", "minimum": 1},
        })
        required.extend(["start_segment_id", "end_segment_id"])
    elif kind in {"predicate_morphology", "tense_morphology"}:
        props["predicate_segment_id"] = {"type": "integer", "minimum": 1}
        required.append("predicate_segment_id")
    return {
        "type": "object", "additionalProperties": False,
        "properties": props, "required": required,
    }


def all_proof_kinds(registry: dict[str, Any]) -> list[str]:
    kinds = [k for family in registry["proof_families"].values() for k in family]
    kinds.extend(
        kind
        for metadata in registry["reference_registry"].values()
        for kind in metadata.get("proof_families", [])
    )
    return list(dict.fromkeys(kinds))


def _binding_definition(*, dependency: bool, references: list[str]) -> dict[str, Any]:
    proof_ref = {"$ref": "#/$defs/proof"}
    branches: list[dict[str, Any]] = [{
        "type": "object", "additionalProperties": False,
        "properties": {
            "kind": {"const": "bound"},
            "ref": {"type": "string", "enum": references},
            "proof": proof_ref,
        },
        "required": ["kind", "ref", "proof"],
    }]
    if dependency:
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {"kind": {"const": "output"}, "proof": proof_ref},
            "required": ["kind", "proof"],
        })
    else:
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {"kind": {"const": "unknown"}, "proof": proof_ref},
            "required": ["kind", "proof"],
        })
    # Inter-clause edge. It carries no atom label: source_ordinal indexes the
    # deterministic atom flattening defined by the clause array itself.
    branches.append({
        "type": "object", "additionalProperties": False,
        "properties": {
            "kind": {"const": BINDING_FROM_PRIOR},
            BINDING_SOURCE_ORDINAL: {"type": "integer", "minimum": 1},
            "proof": proof_ref,
        },
        "required": ["kind", BINDING_SOURCE_ORDINAL, "proof"],
    })
    return {"oneOf": branches}


def build_schema(registry: dict[str, Any]) -> dict[str, Any]:
    """Derive the clause-owned response schema from the frozen registry."""
    relations = registry["relations"]
    limits = registry["limits"]
    references = list(registry["reference_registry"])
    max_arity = max(len(meta["slots"]) for meta in relations.values())
    max_atoms = limits["max_atoms_per_analysis"]
    max_clauses = limits["max_clauses_per_analysis"]

    span_props = {
        SPAN_START: {"type": "integer", "minimum": 1},
        SPAN_END: {"type": "integer", "minimum": 1},
    }
    clause_head = {
        **span_props,
        "clause_role": {"type": "string", "enum": list(registry["clause_roles"])},
        "clause_role_proof": {"$ref": "#/$defs/proof"},
    }

    projection_body = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "relation": {"type": "string", "enum": list(relations)},
            "relation_proof": {"$ref": "#/$defs/proof"},
            "speech_act": {"type": "string", "enum": list(registry["speech_acts"])},
            "speech_act_proof": {"$ref": "#/$defs/proof"},
            "arguments": {
                "type": "array", "minItems": 1, "maxItems": max_arity,
                "items": {"$ref": "#/$defs/projection_binding"},
            },
        },
        "required": ["relation", "relation_proof", "speech_act",
                     "speech_act_proof", "arguments"],
    }
    dependency_body = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "relation": {"type": "string", "enum": list(relations)},
            "relation_proof": {"$ref": "#/$defs/proof"},
            "arguments": {
                "type": "array", "minItems": 1, "maxItems": max_arity,
                "items": {"$ref": "#/$defs/dependency_binding"},
            },
        },
        "required": ["relation", "relation_proof", "arguments"],
    }

    # A projected clause OWNS exactly one projection: a second one is not
    # forbidden by prose, it is unrepresentable. Dependencies are nested, so a
    # dependency cannot belong to another clause and no span equality is ever
    # needed to attach it.
    projected_clause = {
        "type": "object", "additionalProperties": False,
        "properties": {
            **clause_head,
            KEY_PROJECTION: {"$ref": "#/$defs/projection"},
            KEY_DEPENDENCIES: {
                "type": "array", "minItems": 1, "maxItems": max_atoms - 1,
                "items": {"$ref": "#/$defs/dependency"},
            },
        },
        "required": [SPAN_START, SPAN_END, "clause_role", "clause_role_proof",
                     KEY_PROJECTION],
    }
    unsupported_clause = {
        "type": "object", "additionalProperties": False,
        "properties": {
            **clause_head,
            KEY_REASON: {"const": REASON_OUT_OF_REGISTRY},
        },
        "required": [SPAN_START, SPAN_END, "clause_role", "clause_role_proof",
                     KEY_REASON],
    }

    # One positional array carries both kinds, so clause identity is the array
    # index for every clause without exception.
    clause_array = {
        "type": "array", "minItems": 1, "maxItems": max_clauses,
        "items": {"oneOf": [
            {"$ref": "#/$defs/projected_clause"},
            {"$ref": "#/$defs/unsupported_clause"},
        ]},
        # at least one projected clause: structural, so the frozen validator's
        # projection_missing can never fire on a schema-valid frame
        "contains": {"type": "object", "required": [KEY_PROJECTION]},
        "minContains": 1,
    }

    definitions = {
        "proof": {"oneOf": [_proof_variant(k) for k in all_proof_kinds(registry)]},
        "projection_binding": _binding_definition(dependency=False, references=references),
        "dependency_binding": _binding_definition(dependency=True, references=references),
        "projection": projection_body,
        "dependency": dependency_body,
        "projected_clause": projected_clause,
        "unsupported_clause": unsupported_clause,
        "clause_array": clause_array,
    }

    supported = {
        "type": "object", "additionalProperties": False,
        "properties": {
            KEY_STATUS: {"const": STATUS_SUPPORTED},
            KEY_CLAUSES: {"$ref": "#/$defs/clause_array"},
        },
        "required": [KEY_STATUS, KEY_CLAUSES],
    }
    ambiguity = {
        "type": "object", "additionalProperties": False,
        "properties": {
            KEY_STATUS: {"const": STATUS_TYPED_AMBIGUITY},
            KEY_ALTERNATIVES: {
                "type": "array",
                "minItems": 2, "maxItems": limits["max_alternatives"],
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {KEY_CLAUSES: {"$ref": "#/$defs/clause_array"}},
                    "required": [KEY_CLAUSES],
                },
            },
        },
        "required": [KEY_STATUS, KEY_ALTERNATIVES],
    }
    unsupported = {
        "type": "object", "additionalProperties": False,
        "properties": {
            KEY_STATUS: {"const": STATUS_UNSUPPORTED},
            KEY_CLAUSES: {
                "type": "array", "minItems": 1,
                "maxItems": limits["max_unsupported_clauses"],
                "items": {"$ref": "#/$defs/unsupported_clause"},
            },
        },
        "required": [KEY_STATUS, KEY_CLAUSES],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "$defs": definitions,
        "oneOf": [supported, ambiguity, unsupported],
    }


# --------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------

_PROSE = """You are a multilingual phase-1 relational analyzer.
Return only JSON conforming to the supplied schema. Analyze the untouched \
request through the supplied Unicode default-word-boundary segments. Do not \
rewrite the request and do not choose product routes, tools, capabilities, \
product objects, or execution plans. Technical enum labels are denotations, \
never source-language tokens or lexical triggers.

Emit semantic clauses, not a flat atom list. Each element of the clause array \
is one semantic clause and carries its own source segment range. A projected \
clause holds exactly one projection, the decision-bearing primary relation, \
and may hold the auxiliary producers that feed it. A clause that no registered \
relation covers is emitted with the out-of-registry reason instead. Do not \
emit numeric labels for clauses, atoms, alternatives, or output slots: clause \
identity is the position in the clause array and nothing else.

The source segment range is evidence, not identity. Two clauses may carry the \
same range, and a dependency does not need the range of the clause that owns \
it. Give every clause the range its own evidence supports.

Atom order is derived from the structure: within each clause the dependencies \
come first in array order, then that clause's projection, and clauses are \
taken in array order. A from_prior_atom edge names an earlier atom in that \
derived order with source_ordinal, counting from one across the whole \
analysis. Its output slot is derived and must be that source's dependency \
output or projection unknown. Emit clauses in source order.

The ordered argument slots, roles, and types come exclusively from the \
relation signature. Do not repeat role, type, adapter, coercion, or \
output-slot number. Binding kind bound names a typed reference; unknown marks \
a projection output; output occurs exactly once in a dependency at the \
registry-declared output slot; from_prior_atom consumes the source ordinal's \
output. The consumer slot must accept the source type; coercion is derived, \
never emitted. Every binding carries a compatible proof.

Coverage branches are disjoint. The supported status contains one complete \
clause array and has no missing or none proof; it may mix projected and \
out-of-registry clauses so representable work is preserved. The \
typed_ambiguity status contains every materially distinct complete clause \
array as alternatives; every alternative preserves the same semantic clauses. \
The unsupported status is only when no clause is representable. A registered \
relation must never be hidden as out-of-registry, and opaque ambiguity without \
alternatives is forbidden."""

_PROOF_CONTRACT = """Proof contract is claim-specific. clause_role uses \
discourse_structure or clause_boundary. speech_act uses explicit_segment, \
predicate_morphology, clause_construction, or discourse_structure. A \
projection relation uses explicit_segment, predicate_morphology, or \
clause_construction. A dependency relation, its output, and every output edge \
use relation_composition. A bound non-time argument uses its \
reference-specific families when present, otherwise explicit_segment, \
predicate_morphology, or discourse_context. actor.quoted additionally permits \
quotation_context. An unknown argument uses explicit_segment, \
predicate_morphology, or interrogative_construction. A time slot uses \
explicit_segment, tense_morphology, utterance_context, or discourse_context, \
narrowed by its reference contract.

Only explicit_segment carries a positive inclusive segment range. \
predicate_morphology and tense_morphology carry a predicate segment inside the \
clause that owns the fact. All other proof anchors are local and implicit. \
Overlapping proof spans are allowed. Do not invent a proof and do not \
substitute context against explicit contrary evidence.

A primary action relation is imperative and contains no unknown. A primary \
queryable open question or result-bearing imperative contains exactly one \
unknown; a polar question or assertion contains none. Scoped speech_act=none \
may preserve zero or one embedded unknown without making it a current \
request. One valid output may feed multiple later compatible slots, and a \
requested projection output may also feed a later clause. An output is not the \
syntactic or semantic subject unless its consumer slot says so."""


def _relation_line(name: str, metadata: dict[str, Any]) -> str:
    slots = ", ".join(
        f"{slot['role']}:{slot['value_type']}" for slot in metadata["slots"]
    )
    parts = [f"- {name}({slots}) [{metadata['kind']}]"]
    output = metadata.get("dependency_output")
    if output:
        parts.append(
            f"dependency_output=slot={output['slot_index']},"
            f"type={output['value_type']}"
        )
    else:
        parts.append("dependency_output=none")
    accepted = []
    for index, slot in enumerate(metadata["slots"], 1):
        for item in slot.get("accepted_outputs", []) or []:
            accepted.append(
                f"slot={index}<-{item['value_type']}:{item['coercion']}"
            )
    parts.append("accepted_outputs=" + (",".join(accepted) if accepted else "none"))
    return "; ".join(parts) + f": {metadata['description']}"


def build_prompt(registry: dict[str, Any]) -> str:
    limits = registry["limits"]
    lines = [_PROSE, ""]

    lines.append("Clause role contract:")
    for name, meta in registry["clause_roles"].items():
        lines.append(
            f"- {name}: {meta['description']} "
            f"Allowed speech acts: {', '.join(meta['speech_acts'])}."
        )
    lines.append("")

    lines.append("Speech-act contract:")
    for name, description in registry["speech_acts"].items():
        lines.append(f"- {name}: {description}")
    lines.append("")

    lines.append("Relation registry with ordered signatures:")
    for name, meta in registry["relations"].items():
        lines.append(_relation_line(name, meta))
    lines.append("")

    lines.append("Reference registry:")
    for name, meta in registry["reference_registry"].items():
        lines.append(f"- {name}:{meta['value_type']}")
    lines.append("")

    lines.append(_PROOF_CONTRACT)
    lines.append("")

    lines.append(
        "Resource bounds: at most {atoms} atoms, {clauses} semantic clauses, "
        "{alternatives} alternatives, {depth} dependency edges in one path, "
        "{proofs} proofs per analysis, and {total} proofs in the complete "
        "output.".format(
            atoms=limits["max_atoms_per_analysis"],
            clauses=limits["max_clauses_per_analysis"],
            alternatives=limits["max_alternatives"],
            depth=limits["max_dependency_depth"],
            proofs=limits["max_proofs_per_analysis"],
            total=limits["max_total_proofs"],
        )
    )
    lines.append(
        "Before output verify status branch; one clause per semantic clause; "
        "clause array in source order; relation arity; role and speech "
        "agreement; output and adapter compatibility; proof compatibility; one "
        "consumed output per dependency; every source_ordinal points backward "
        "to a compatible producer; depth bound; complete distinct alternatives."
    )
    return "\n".join(lines) + "\n"
