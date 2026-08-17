#!/usr/bin/env python3
"""V26.4 typed relational phase 1, self-contained runtime candidate.

No prior laboratory runner is imported.  Relation metadata is a separately
frozen technical registry.  Runtime inference is impossible until an external
review/oracle gate lock exists; the author pre-gate is intentionally blocked.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import statistics
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

import jsonschema
import regex


VERSION = "metnos.v26.4-typed-relational-phase1/1.0"
RUNNER_PATH = Path(__file__)
REGISTRY_PATH = Path("/tmp/metnos_v264_typed_registry.json")
SCHEMA_PATH = Path("/tmp/metnos_v264_typed_phase1.schema.json")
PROMPT_PATH = Path("/tmp/metnos_v264_typed_phase1.prompt.txt")
FIXTURE_PATH = Path("/tmp/metnos_v264_typed_phase1_controls_frozen.json")
MUTATIONS_PATH = Path("/tmp/metnos_v264_typed_phase1_mutations_frozen.json")
CONTAMINATION_PATH = Path("/tmp/metnos_v264_typed_phase1_contamination_audit.json")
FREEZE_PATH = Path("/tmp/metnos_v264_typed_phase1.freeze.json")
AUTHOR_PRE_GATE_PATH = Path("/tmp/metnos_v264_typed_phase1_author_pre_gate.json")
EXTERNAL_GATE_PATH = Path("/tmp/metnos_v264_typed_phase1_external_gate.lock.json")
INVARIANT_REVIEW_PATH = Path("/tmp/metnos_v264_graph_invariant_review_preoutput.md")
MUTATION_PROPOSAL_PATH = Path("/tmp/metnos_v264_graph_mutation_proposal.json")
INDEPENDENT_PROBE_PATH = Path("/tmp/metnos_v264_independent_graph_probe.py")
INDEPENDENT_PROBE_RESULT_PATH = Path("/tmp/metnos_v264_independent_graph_probe_result.json")

CONTROLS_PATH = Path("/opt/metnos/internal/tools/request_analysis_lab/question_focus_controls_v1.json")
AUDITOR_PATH = Path("/tmp/metnos_prompt_contamination_audit.py")
ORACLE_REPORT_PATH = Path("/tmp/metnos_phase1_oracle_audit_v1.md")
ORACLE_AUDIT_PATH = Path("/tmp/metnos_phase1_oracle_audit_v1.json")
ORACLE_SCHEMA_PATH = Path("/tmp/metnos_phase1_typed_oracle_v1.schema.json")
ORACLE_OVERLAY_PATH = Path("/tmp/metnos_phase1_typed_oracle_v1.overlay.json")
ORACLE_FREEZE_PATH = Path("/tmp/metnos_phase1_oracle_audit_v1.freeze.json")

EXPECTED_ORACLE_HASHES = {
    ORACLE_REPORT_PATH: "538f24876768eb0e8612f3bb440c5f38a51a222122d5b34080e5a5c19ef9799a",
    ORACLE_AUDIT_PATH: "b8443ad2e27a2d773b971147c1b3d37ee19beb1091b1d357c0a84e6aa0c98a77",
    ORACLE_SCHEMA_PATH: "4929fbc4ac524491f452d11d0f2d4d032887dd423c7487233c3d89065f4e17f2",
    ORACLE_OVERLAY_PATH: "e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af",
    ORACLE_FREEZE_PATH: "f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f",
}

REGISTRY = json.loads(REGISTRY_PATH.read_text())
RELATIONS: dict[str, Any] = REGISTRY["relations"]
REFERENCES: dict[str, Any] = REGISTRY["reference_registry"]
PROOF_FAMILIES: dict[str, list[str]] = REGISTRY["proof_families"]
LIMITS: dict[str, int] = REGISTRY["limits"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def source_bundle_sha(*objects: Any) -> str:
    return sha_text("\n\n".join(inspect.getsource(item) for item in objects))


def unicode_segments(text: str) -> list[dict[str, Any]]:
    """Unicode UAX #29 default word boundaries without language tables."""
    parts = regex.split(r"\b", text, flags=regex.WORD | regex.VERSION1)
    segments: list[dict[str, Any]] = []
    cursor = 0
    for part in parts:
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            raise ValueError("segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        segments.append({
            "id": len(segments) + 1,
            "text": part,
            "start_char": start,
            "end_char": end,
        })
    return segments


def _proof_variant(kind: str) -> dict[str, Any]:
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


def proof_schema(kinds: list[str]) -> dict[str, Any]:
    unique = list(dict.fromkeys(kinds))
    return {"oneOf": [_proof_variant(kind) for kind in unique]}


def _slot_proof_family(slot: dict[str, Any]) -> str:
    return "time" if slot["role"] == "time" else "argument"


def _all_proof_kinds() -> list[str]:
    """Technical proof enum, generated once from the frozen registry."""
    kinds = [kind for family in PROOF_FAMILIES.values() for kind in family]
    kinds.extend(
        kind
        for metadata in REFERENCES.values()
        for kind in metadata.get("proof_families", [])
    )
    return list(dict.fromkeys(kinds))


def _binding_definition(*, dependency: bool) -> dict[str, Any]:
    proof_ref = {"$ref": "#/$defs/proof"}
    branches: list[dict[str, Any]] = [{
        "type": "object", "additionalProperties": False,
        "properties": {
            "kind": {"const": "bound"},
            "ref": {"type": "string", "enum": list(REFERENCES)},
            "proof": proof_ref,
        },
        "required": ["kind", "ref", "proof"],
    }]
    if not dependency:
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "unknown"},
                "proof": proof_ref,
            },
            "required": ["kind", "proof"],
        })
    if dependency:
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "output"},
                "proof": proof_ref,
            },
            "required": ["kind", "proof"],
        })
    branches.append({
        "type": "object", "additionalProperties": False,
        "properties": {
            "kind": {"const": "from_atom_output"},
            "atom_id": {"type": "integer", "minimum": 1},
            "output_index": {"type": "integer", "minimum": 1},
            "proof": proof_ref,
        },
        "required": ["kind", "atom_id", "output_index", "proof"],
    })
    return {"oneOf": branches}


def _atom_definition(*, dependency: bool) -> dict[str, Any]:
    max_arity = max(len(metadata["slots"]) for metadata in RELATIONS.values())
    props: dict[str, Any] = {
        "atom_id": {"type": "integer", "minimum": 1},
        "atom_kind": {"const": "dependency" if dependency else "projection"},
        "clause_id": {"type": "integer", "minimum": 1},
        "clause_start_segment_id": {"type": "integer", "minimum": 1},
        "clause_end_segment_id": {"type": "integer", "minimum": 1},
        "relation": {"type": "string", "enum": list(RELATIONS)},
        "relation_proof": {"$ref": "#/$defs/proof"},
        "arguments": {
            "type": "array", "minItems": 1, "maxItems": max_arity,
            "items": {"$ref": f"#/$defs/{'dependency' if dependency else 'projection'}_binding"},
        },
    }
    if not dependency:
        props.update({
            "clause_role": {"type": "string", "enum": list(REGISTRY["clause_roles"])},
            "clause_role_proof": {"$ref": "#/$defs/proof"},
            "speech_act": {"type": "string", "enum": list(REGISTRY["speech_acts"])},
            "speech_act_proof": {"$ref": "#/$defs/proof"},
        })
    return {
        "type": "object", "additionalProperties": False,
        "properties": props, "required": list(props),
    }


def _atoms_definition() -> dict[str, Any]:
    return {
        "type": "array", "minItems": 1,
        "maxItems": LIMITS["max_atoms_per_analysis"],
        "items": {"oneOf": [
            {"$ref": "#/$defs/projection_atom"},
            {"$ref": "#/$defs/dependency_atom"},
        ]},
    }


def unsupported_clause_schema() -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "clause_id": {"type": "integer", "minimum": 1},
            "clause_start_segment_id": {"type": "integer", "minimum": 1},
            "clause_end_segment_id": {"type": "integer", "minimum": 1},
            "clause_role": {"type": "string", "enum": list(REGISTRY["clause_roles"])},
            "clause_role_proof": {"$ref": "#/$defs/proof"},
            "reason": {"const": "out_of_registry"},
        },
        "required": [
            "clause_id", "clause_start_segment_id", "clause_end_segment_id",
            "clause_role", "clause_role_proof", "reason",
        ],
    }


def live_schema() -> dict[str, Any]:
    definitions = {
        "proof": proof_schema(_all_proof_kinds()),
        "projection_binding": _binding_definition(dependency=False),
        "dependency_binding": _binding_definition(dependency=True),
        "projection_atom": _atom_definition(dependency=False),
        "dependency_atom": _atom_definition(dependency=True),
        "atoms": _atoms_definition(),
        "unsupported_clause": unsupported_clause_schema(),
    }
    supported = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "status": {"const": "supported"},
            "atoms": {"$ref": "#/$defs/atoms"},
            "unsupported_clauses": {
                "type": "array", "minItems": 1,
                "maxItems": LIMITS["max_unsupported_clauses"],
                "items": {"$ref": "#/$defs/unsupported_clause"},
            },
        },
        "required": ["status", "atoms"],
    }
    alternative = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "alternative_id": {"type": "integer", "minimum": 1},
            "atoms": {"$ref": "#/$defs/atoms"},
        },
        "required": ["alternative_id", "atoms"],
    }
    ambiguity = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "status": {"const": "typed_ambiguity"},
            "alternatives": {
                "type": "array", "minItems": 2,
                "maxItems": LIMITS["max_alternatives"],
                "items": alternative,
            },
            "unsupported_clauses": {
                "type": "array", "minItems": 1,
                "maxItems": LIMITS["max_unsupported_clauses"],
                "items": {"$ref": "#/$defs/unsupported_clause"},
            },
        },
        "required": ["status", "alternatives"],
    }
    unsupported = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "status": {"const": "unsupported"},
            "clauses": {
                "type": "array", "minItems": 1,
                "maxItems": LIMITS["max_unsupported_clauses"],
                "items": {"$ref": "#/$defs/unsupported_clause"},
            },
        },
        "required": ["status", "clauses"],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "$defs": definitions,
        # Branch order is deliberate: complete supported data first, explicit
        # typed alternatives second, unsupported abstention last.
        "oneOf": [supported, ambiguity, unsupported],
    }


def system_prompt() -> str:
    role_lines = [
        f"- {name}: {metadata['description']} Allowed speech acts: "
        + ", ".join(metadata["speech_acts"]) + "."
        for name, metadata in REGISTRY["clause_roles"].items()
    ]
    speech_lines = [
        f"- {name}: {description}"
        for name, description in REGISTRY["speech_acts"].items()
    ]
    relation_lines = []
    for name, metadata in RELATIONS.items():
        signature = ", ".join(
            f"{slot['role']}:{slot['value_type']}" for slot in metadata["slots"]
        )
        dependency_output = metadata["dependency_output"]
        output_text = (
            "none" if dependency_output is None
            else f"slot={dependency_output['slot_index']},type={dependency_output['value_type']}"
        )
        adapters = [
            f"slot={slot_index}<-{accepted['value_type']}:{accepted['coercion']}"
            for slot_index, slot in enumerate(metadata["slots"], 1)
            for accepted in slot.get("accepted_outputs", [])
        ]
        relation_lines.append(
            f"- {name}({signature}) [{metadata['kind']}]; dependency_output={output_text}; "
            f"accepted_outputs={','.join(adapters) if adapters else 'none'}: {metadata['description']}"
        )
    reference_lines = [
        f"- {name}:{metadata['value_type']}"
        for name, metadata in REFERENCES.items()
    ]
    return "\n".join([
        "You are a multilingual phase-1 relational analyzer.",
        "Return only JSON conforming to the supplied schema. Analyze the untouched request through the supplied Unicode default-word-boundary segments. Do not rewrite the request and do not choose product routes, tools, capabilities, product objects, or execution plans. Technical enum labels are denotations, never source-language tokens or lexical triggers.",
        "",
        "Emit a bounded normalized graph. atom_id values are consecutive and topological. A projection atom is the one decision-bearing primary relation of its semantic clause. A dependency atom is an auxiliary producer owned by that same clause. Every from_atom_output edge points to a lower atom_id and output_index; its source is either the registry-declared output of a dependency atom or an unknown output of a prior projection atom. No recursive embedded dependency is permitted.",
        "",
        "The ordered argument slots, roles, and types come exclusively from the relation signature. Do not repeat role, type, adapter, or coercion in an argument. Binding kind bound names a typed reference. Binding kind unknown marks a projection output. Binding kind output occurs exactly once in a dependency atom, at dependency_output.slot. Binding kind from_atom_output consumes a prior output. The consumer slot must list that source type in accepted_outputs; the listed coercion is derived, never guessed or emitted. Every binding carries a compatible proof.",
        "",
        "Coverage branches are disjoint. status=supported contains one complete graph and has no missing/none proof. It may also carry unsupported_clauses so representable work is preserved in a mixed request. status=typed_ambiguity contains every materially distinct complete graph as 2..4 alternatives and may carry common unsupported_clauses; every alternative preserves the same semantic clauses. status=unsupported is only when no clause is representable. A registered relation must never be hidden as unsupported, and opaque ambiguity without alternatives is forbidden.",
        "",
        "Clause role contract:",
        *role_lines,
        "",
        "Speech-act contract:",
        *speech_lines,
        "",
        "Relation registry with ordered signatures:",
        *relation_lines,
        "",
        "Reference registry:",
        *reference_lines,
        "",
        "Proof contract is claim-specific. clause_role uses discourse_structure or clause_boundary. speech_act uses explicit_segment, predicate_morphology, clause_construction, or discourse_structure. A projection relation uses explicit_segment, predicate_morphology, or clause_construction. A dependency relation, its output, and every output edge use relation_composition. A bound non-time argument uses its reference-specific families when present, otherwise explicit_segment, predicate_morphology, or discourse_context. actor.quoted additionally permits quotation_context. An unknown argument uses explicit_segment, predicate_morphology, or interrogative_construction. A time slot uses explicit_segment, tense_morphology, utterance_context, or discourse_context, narrowed by its reference contract.",
        "",
        "Only explicit_segment carries a positive inclusive segment range. predicate_morphology and tense_morphology carry a predicate segment inside the atom clause. All other proof anchors are local and implicit. Overlapping proof spans are allowed. Do not invent a proof and do not substitute context against explicit contrary evidence.",
        "",
        "A primary action relation is imperative and contains no unknown. A primary queryable open question or result-bearing imperative contains exactly one unknown; a polar question or assertion contains none. Scoped speech_act=none may preserve zero or one embedded unknown without making it a current request. One valid output may feed multiple later compatible slots, and a requested projection output may also feed a later clause. An output is not the syntactic or semantic subject unless its consumer slot says so.",
        "",
        f"Resource bounds: at most {LIMITS['max_atoms_per_analysis']} atoms, {LIMITS['max_clauses_per_analysis']} semantic clauses, {LIMITS['max_dependency_depth']} dependency edges in one path, {LIMITS['max_proofs_per_analysis']} proofs per analysis, and {LIMITS['max_total_proofs']} proofs in the complete output.",
        "Before output verify status branch, one projection per semantic clause, atom/clause order, clause bounds, role/speech agreement, relation arity, registry output slot and adapter compatibility, claim/proof compatibility, exactly one declared output per dependency, every dependency output consumed, acyclic backward edges, dependency depth bound, and complete distinct alternatives.",
        "",
    ])


def _error(code: str, path: str, message: str) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message, "retryable": False}


def _validate_proof(
    proof: dict[str, Any], allowed: list[str], atom: dict[str, Any],
    segment_ids: set[int], path: str, errors: list[dict[str, Any]],
) -> None:
    kind = proof.get("kind")
    if kind not in allowed:
        errors.append(_error("proof_family", path, f"{kind} is not allowed for this fact"))
        return
    start = atom["clause_start_segment_id"]
    end = atom["clause_end_segment_id"]
    if kind == "explicit_segment":
        proof_start = proof["start_segment_id"]
        proof_end = proof["end_segment_id"]
        if (
            proof_start not in segment_ids or proof_end not in segment_ids
            or not (start <= proof_start <= proof_end <= end)
        ):
            errors.append(_error("proof_span", path, "explicit proof must be inside the fact clause"))
    elif kind in {"predicate_morphology", "tense_morphology"}:
        predicate = proof["predicate_segment_id"]
        if predicate not in segment_ids or not (start <= predicate <= end):
            errors.append(_error("proof_predicate", path, "morphology proof must identify a segment inside the fact clause"))


def _binding_allowed_proofs(slot: dict[str, Any], binding: dict[str, Any]) -> list[str]:
    kind = binding["kind"]
    if kind == "bound":
        metadata = REFERENCES[binding["ref"]]
        return metadata.get("proof_families", PROOF_FAMILIES[_slot_proof_family(slot)])
    if kind == "unknown":
        return PROOF_FAMILIES["time" if slot["role"] == "time" else "unknown_argument"]
    if kind == "output":
        return PROOF_FAMILIES["dependency_output"]
    return PROOF_FAMILIES["from_atom_output"]


def registry_errors() -> list[str]:
    """Validate every derived schema/validator assumption in the registry."""
    errors: list[str] = []
    required_limits = {
        "max_alternatives", "max_atoms_per_analysis", "max_clauses_per_analysis",
        "max_dependency_depth", "max_proofs_per_analysis", "max_total_proofs",
        "max_unsupported_clauses",
    }
    if not required_limits.issubset(LIMITS):
        errors.append("limits")
    all_proofs = set(_all_proof_kinds())
    for ref, metadata in REFERENCES.items():
        if not metadata.get("value_type"):
            errors.append(f"reference_type:{ref}")
        if not set(metadata.get("proof_families", [])).issubset(all_proofs):
            errors.append(f"reference_proof:{ref}")
    for relation, metadata in RELATIONS.items():
        slots = metadata.get("slots", [])
        if not slots:
            errors.append(f"slots:{relation}")
            continue
        dependency_output = metadata.get("dependency_output", "missing")
        if metadata.get("kind") == "queryable_relation" and not isinstance(dependency_output, dict):
            errors.append(f"dependency_output:{relation}")
        if metadata.get("kind") == "action_relation" and dependency_output is not None:
            errors.append(f"action_dependency_output:{relation}")
        if isinstance(dependency_output, dict):
            output_index = dependency_output.get("slot_index")
            if not isinstance(output_index, int) or not 1 <= output_index <= len(slots):
                errors.append(f"output_slot:{relation}")
            elif dependency_output.get("value_type") != slots[output_index - 1].get("value_type"):
                errors.append(f"output_type:{relation}")
        for slot_index, slot in enumerate(slots, 1):
            if "accepted_output_types" in slot:
                errors.append(f"legacy_output_allowlist:{relation}:{slot_index}")
            seen_outputs: set[str] = set()
            for accepted in slot.get("accepted_outputs", []):
                output_type = accepted.get("value_type")
                coercion = accepted.get("coercion")
                if not output_type or not coercion or output_type in seen_outputs:
                    errors.append(f"accepted_output:{relation}:{slot_index}")
                seen_outputs.add(output_type)
                if (output_type == slot.get("value_type")) != (coercion == "identity"):
                    errors.append(f"coercion:{relation}:{slot_index}:{output_type}")
    return errors


def _accepted_output(slot: dict[str, Any], output_type: str) -> dict[str, str] | None:
    matches = [
        item for item in slot.get("accepted_outputs", [])
        if item["value_type"] == output_type
    ]
    return matches[0] if len(matches) == 1 else None


def _is_output_binding(atom: dict[str, Any], output_index: int) -> bool:
    if not 1 <= output_index <= len(atom["arguments"]):
        return False
    binding = atom["arguments"][output_index - 1]
    if atom["atom_kind"] == "dependency":
        return binding["kind"] == "output"
    return binding["kind"] == "unknown"


def _proof_count(atoms: list[dict[str, Any]], unsupported: list[dict[str, Any]]) -> int:
    return sum(
        1 + len(atom["arguments"]) + (2 if atom["atom_kind"] == "projection" else 0)
        for atom in atoms
    ) + len(unsupported)


def _validate_atom_list(
    atoms: list[dict[str, Any]], segments: list[dict[str, Any]], path: str,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    segment_ids = {segment["id"] for segment in segments}
    ids = [atom["atom_id"] for atom in atoms]
    if ids != list(range(1, len(atoms) + 1)):
        errors.append(_error("atom_order", path, "atom ids must be consecutive and topological"))
    atom_by_id = {atom["atom_id"]: atom for atom in atoms}
    consumers: dict[tuple[int, int], int] = {}
    projection_count = 0
    projection_starts: list[int] = []
    projection_clause_ids: list[int] = []
    clause_spans: dict[int, tuple[int, int]] = {}
    depths: dict[int, int] = {}
    for index, atom in enumerate(atoms):
        atom_path = f"{path}/{index}"
        start = atom["clause_start_segment_id"]
        end = atom["clause_end_segment_id"]
        if start not in segment_ids or end not in segment_ids or start > end:
            errors.append(_error("clause_span", atom_path, "atom clause is outside supplied segments"))
        clause_id = atom["clause_id"]
        if clause_id in clause_spans and clause_spans[clause_id] != (start, end):
            errors.append(_error("clause_span_consistency", atom_path, "one clause id must keep one source span"))
        clause_spans[clause_id] = (start, end)
        relation = atom["relation"]
        relation_metadata = RELATIONS[relation]
        slots = relation_metadata["slots"]
        dependency = atom["atom_kind"] == "dependency"
        if not dependency:
            projection_count += 1
            projection_starts.append(start)
            projection_clause_ids.append(clause_id)
            allowed_speech = REGISTRY["clause_roles"][atom["clause_role"]]["speech_acts"]
            if atom["speech_act"] not in allowed_speech:
                errors.append(_error("role_speech", atom_path, "clause role and speech act disagree"))
            _validate_proof(
                atom["clause_role_proof"], PROOF_FAMILIES["clause_role"], atom,
                segment_ids, atom_path + "/clause_role_proof", errors,
            )
            _validate_proof(
                atom["speech_act_proof"], PROOF_FAMILIES["speech_act"], atom,
                segment_ids, atom_path + "/speech_act_proof", errors,
            )
            relation_proofs = PROOF_FAMILIES["relation"]
        else:
            relation_proofs = PROOF_FAMILIES["dependency_relation"]
            if relation_metadata["dependency_output"] is None:
                errors.append(_error("dependency_relation", atom_path, "this relation cannot be an auxiliary producer"))
        _validate_proof(
            atom["relation_proof"], relation_proofs, atom,
            segment_ids, atom_path + "/relation_proof", errors,
        )
        if len(atom["arguments"]) != len(slots):
            errors.append(_error("relation_arity", atom_path + "/arguments", "argument count must equal the frozen relation signature"))
        output_count = 0
        parent_depths: list[int] = []
        for argument_index, (slot, binding) in enumerate(zip(slots, atom["arguments"]), 1):
            binding_path = f"{atom_path}/arguments/{argument_index - 1}"
            kind = binding["kind"]
            if kind == "bound":
                ref_type = REFERENCES[binding["ref"]]["value_type"]
                if ref_type not in slot["accepted_reference_types"]:
                    errors.append(_error("reference_type", binding_path, "reference type is incompatible with relation slot"))
            elif kind == "unknown":
                if dependency:
                    errors.append(_error("dependency_unknown", binding_path, "dependency atoms use output, not requested unknown"))
            elif kind == "output":
                output_count += 1
                if not dependency:
                    errors.append(_error("projection_output", binding_path, "projection atoms cannot declare dependency output"))
                elif relation_metadata["dependency_output"] is not None and argument_index != relation_metadata["dependency_output"]["slot_index"]:
                    errors.append(_error("dependency_output_slot", binding_path, "output must occupy the registry-declared slot"))
            elif kind == "from_atom_output":
                source_id = binding["atom_id"]
                source_index = binding["output_index"]
                if source_id >= atom["atom_id"] or source_id not in atom_by_id:
                    errors.append(_error("dependency_order", binding_path, "edge must point to a lower atom id"))
                else:
                    source = atom_by_id[source_id]
                    source_slots = RELATIONS[source["relation"]]["slots"]
                    if not (1 <= source_index <= len(source["arguments"])) or source_index > len(source_slots):
                        errors.append(_error("dependency_output_index", binding_path, "edge output index is outside source arity"))
                    elif not _is_output_binding(source, source_index):
                        errors.append(_error("dependency_not_output", binding_path, "edge must identify a dependency output or projection unknown"))
                    else:
                        source_type = source_slots[source_index - 1]["value_type"]
                        if _accepted_output(slot, source_type) is None:
                            errors.append(_error("dependency_type", binding_path, "dependency output type is incompatible with consumer slot"))
                        consumers[(source_id, source_index)] = consumers.get((source_id, source_index), 0) + 1
                        parent_depths.append(depths.get(source_id, 0))
            _validate_proof(
                binding["proof"], _binding_allowed_proofs(slot, binding), atom,
                segment_ids, binding_path + "/proof", errors,
            )
        if dependency and output_count != 1:
            errors.append(_error("dependency_output_count", atom_path, "dependency atom must declare exactly one output"))
        if not dependency:
            unknowns = sum(binding["kind"] == "unknown" for binding in atom["arguments"])
            relation_kind = relation_metadata["kind"]
            speech_act = atom["speech_act"]
            clause_role = atom["clause_role"]
            if relation_kind == "action_relation":
                if clause_role == "main_request" and speech_act != "imperative":
                    errors.append(_error("action_speech", atom_path, "a primary action must be imperative"))
                if clause_role == "main_assertion":
                    errors.append(_error("action_assertion", atom_path, "an action relation is not a main assertion"))
                if unknowns:
                    errors.append(_error("action_unknown", atom_path, "an effectful action cannot contain an unresolved unknown"))
            elif speech_act in {"open_question", "imperative"} and unknowns != 1:
                errors.append(_error("queryable_unknown", atom_path, "an open or result-bearing queryable request has exactly one unknown"))
            elif speech_act in {"polar_question", "assertion"} and unknowns != 0:
                errors.append(_error("closed_proposition_unknown", atom_path, "a complete proposition cannot contain an unknown"))
            elif speech_act == "none" and unknowns > 1:
                errors.append(_error("embedded_unknown", atom_path, "embedded queryable content has at most one output"))
        depths[atom["atom_id"]] = max(parent_depths) + 1 if parent_depths else 0
        if depths[atom["atom_id"]] > LIMITS["max_dependency_depth"]:
            errors.append(_error("dependency_depth", atom_path, "dependency depth exceeds frozen bound"))
    if projection_count == 0:
        errors.append(_error("projection_missing", path, "complete analysis requires at least one projection atom"))
    if projection_starts != sorted(projection_starts):
        errors.append(_error("projection_source_order", path, "projection atoms must preserve source order"))
    clause_sequence = [atom["clause_id"] for atom in atoms]
    if clause_sequence != sorted(clause_sequence):
        errors.append(_error("atom_clause_order", path, "atom order must preserve semantic clause order"))
    if len(set(projection_clause_ids)) != len(projection_clause_ids):
        errors.append(_error("primary_cardinality", path, "each semantic clause has exactly one projection atom"))
    projection_clause_set = set(projection_clause_ids)
    for atom in atoms:
        if atom["atom_kind"] == "dependency" and atom["clause_id"] not in projection_clause_set:
            errors.append(_error("orphan_dependency", path, "every auxiliary producer belongs to a projected clause"))
    for atom in atoms:
        for index, binding in enumerate(atom["arguments"], 1):
            # Scoped projection unknowns are semantic content, not requested
            # answers, and the frozen oracle permits them without consumers.
            must_be_consumed = atom["atom_kind"] == "dependency" and binding["kind"] == "output"
            if must_be_consumed and consumers.get((atom["atom_id"], index), 0) == 0:
                errors.append(_error("output_unconsumed", path, "an auxiliary or embedded output must be consumed"))
    return errors


def _validate_unsupported_clauses(
    clauses: list[dict[str, Any]], segments: list[dict[str, Any]], path: str,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    segment_ids = {segment["id"] for segment in segments}
    clause_ids = [item["clause_id"] for item in clauses]
    if clause_ids != sorted(set(clause_ids)):
        errors.append(_error("unsupported_clause_order", path, "unsupported clause ids must be unique and ordered"))
    for index, clause in enumerate(clauses):
        start = clause["clause_start_segment_id"]
        end = clause["clause_end_segment_id"]
        if start not in segment_ids or end not in segment_ids or start > end:
            errors.append(_error("clause_span", f"{path}/{index}", "unsupported clause span is invalid"))
        _validate_proof(
            clause["clause_role_proof"], PROOF_FAMILIES["clause_role"], clause,
            segment_ids, f"{path}/{index}/clause_role_proof", errors,
        )
    return errors


def _clause_footprint(
    atoms: list[dict[str, Any]], unsupported: list[dict[str, Any]], path: str,
) -> tuple[list[tuple[int, int, int, str]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    projections = [atom for atom in atoms if atom["atom_kind"] == "projection"]
    entries = [
        (atom["clause_id"], atom["clause_start_segment_id"], atom["clause_end_segment_id"], "supported")
        for atom in projections
    ] + [
        (clause["clause_id"], clause["clause_start_segment_id"], clause["clause_end_segment_id"], "unsupported")
        for clause in unsupported
    ]
    ids = [item[0] for item in entries]
    if len(ids) > LIMITS["max_clauses_per_analysis"]:
        errors.append(_error("clause_limit", path, "semantic clause count exceeds the frozen bound"))
    if sorted(ids) != list(range(1, len(ids) + 1)):
        errors.append(_error("clause_ids", path, "projected and unsupported clause ids must be unique and consecutive"))
    ordered = sorted(entries)
    if [item[1] for item in ordered] != sorted(item[1] for item in ordered):
        errors.append(_error("clause_source_order", path, "clause ids must preserve source order"))
    return ordered, errors


def validate_frame(frame: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    for issue in sorted(
        jsonschema.Draft202012Validator(live_schema()).iter_errors(frame),
        key=lambda item: list(item.absolute_path),
    ):
        errors.append(_error(
            "schema", "/" + "/".join(str(item) for item in issue.absolute_path),
            issue.message,
        ))
    if errors:
        return {"valid": False, "errors": errors}
    unsupported = frame.get("unsupported_clauses", [])
    errors.extend(_validate_unsupported_clauses(unsupported, segments, "/unsupported_clauses"))
    total_proofs = len(unsupported)
    if frame["status"] == "supported":
        errors.extend(_validate_atom_list(frame["atoms"], segments, "/atoms"))
        _, clause_errors = _clause_footprint(frame["atoms"], unsupported, "/")
        errors.extend(clause_errors)
        proof_count = _proof_count(frame["atoms"], unsupported)
        total_proofs = proof_count
        if proof_count > LIMITS["max_proofs_per_analysis"]:
            errors.append(_error("proof_limit", "/", "proof count exceeds the per-analysis bound"))
    elif frame["status"] == "typed_ambiguity":
        alternative_ids = [item["alternative_id"] for item in frame["alternatives"]]
        if alternative_ids != list(range(1, len(alternative_ids) + 1)):
            errors.append(_error("alternative_order", "/alternatives", "alternative ids must be consecutive"))
        semantic_alternatives = []
        footprints = []
        for index, alternative in enumerate(frame["alternatives"]):
            alternative_errors = _validate_atom_list(
                alternative["atoms"], segments, f"/alternatives/{index}/atoms",
            )
            errors.extend(alternative_errors)
            if not alternative_errors:
                semantic_alternatives.append(canonical_graph_analysis(alternative["atoms"]))
            footprint, clause_errors = _clause_footprint(
                alternative["atoms"], unsupported, f"/alternatives/{index}",
            )
            footprints.append(footprint)
            errors.extend(clause_errors)
            proof_count = _proof_count(alternative["atoms"], unsupported)
            total_proofs += proof_count - len(unsupported)
            if proof_count > LIMITS["max_proofs_per_analysis"]:
                errors.append(_error("proof_limit", f"/alternatives/{index}", "proof count exceeds the per-analysis bound"))
        if len(semantic_alternatives) == len(frame["alternatives"]) and len({canonical_hash(item) for item in semantic_alternatives}) != len(semantic_alternatives):
            errors.append(_error("duplicate_alternative", "/alternatives", "typed alternatives must differ semantically"))
        if any(footprint != footprints[0] for footprint in footprints[1:]):
            errors.append(_error("alternative_coverage", "/alternatives", "every alternative must preserve the same semantic clauses"))
    else:
        clauses = frame["clauses"]
        errors.extend(_validate_unsupported_clauses(clauses, segments, "/clauses"))
        _, clause_errors = _clause_footprint([], clauses, "/")
        errors.extend(clause_errors)
        total_proofs = len(clauses)
    if total_proofs > LIMITS["max_total_proofs"]:
        errors.append(_error("total_proof_limit", "/", "total proof count exceeds the frozen output bound"))
    return {"valid": not errors, "errors": errors}


def _dependency_ref(atom: dict[str, Any], output_index: int) -> str:
    if atom["relation"] == "spatial.located_at" and output_index == 2:
        arguments = atom["arguments"]
        if (
            arguments[0].get("kind") == "bound"
            and arguments[0].get("ref") == "actor.current"
            and arguments[1].get("kind") in {"output", "unknown"}
            and arguments[2].get("kind") == "bound"
            and arguments[2].get("ref") == "time.current"
        ):
            return "actor.current_position"
    return f"atom_output.{atom['relation']}.{output_index}"


def canonical_projection(atom: dict[str, Any], atom_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    slots = RELATIONS[atom["relation"]]["slots"]
    arguments = []
    for slot, binding in zip(slots, atom["arguments"]):
        item: dict[str, Any] = {"role": slot["role"], "value_type": slot["value_type"]}
        if binding["kind"] == "bound":
            item.update({"state": "bound", "ref": binding["ref"]})
        elif binding["kind"] == "unknown":
            item["state"] = "unknown"
        elif binding["kind"] == "from_atom_output":
            source = atom_by_id[binding["atom_id"]]
            item.update({
                "state": "derived",
                "ref": _dependency_ref(source, binding["output_index"]),
            })
        else:
            continue
        arguments.append(item)
    return {
        "clause_role": atom["clause_role"],
        "speech_act": atom["speech_act"],
        "relation": atom["relation"],
        "arguments": arguments,
    }


def canonical_analysis(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    atom_by_id = {atom["atom_id"]: atom for atom in atoms}
    return [
        canonical_projection(atom, atom_by_id)
        for atom in atoms if atom["atom_kind"] == "projection"
    ]


def canonical_graph_analysis(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Proof/id-free semantics, retaining the complete producer graph."""
    atom_by_id = {atom["atom_id"]: atom for atom in atoms}
    memo: dict[int, dict[str, Any]] = {}

    def normalize(atom: dict[str, Any]) -> dict[str, Any]:
        if atom["atom_id"] in memo:
            return memo[atom["atom_id"]]
        slots = RELATIONS[atom["relation"]]["slots"]
        normalized_arguments = []
        for slot, binding in zip(slots, atom["arguments"]):
            item: dict[str, Any] = {"role": slot["role"], "value_type": slot["value_type"]}
            if binding["kind"] == "bound":
                item.update({"state": "bound", "ref": binding["ref"]})
            elif binding["kind"] == "unknown":
                item["state"] = "unknown"
            elif binding["kind"] == "output":
                item["state"] = "output"
            else:
                item.update({
                    "state": "derived",
                    "output_index": binding["output_index"],
                    "source": normalize(atom_by_id[binding["atom_id"]]),
                })
            normalized_arguments.append(item)
        result: dict[str, Any] = {
            "atom_kind": atom["atom_kind"],
            "relation": atom["relation"],
            "arguments": normalized_arguments,
        }
        if atom["atom_kind"] == "projection":
            result.update({
                "clause_role": atom["clause_role"],
                "speech_act": atom["speech_act"],
            })
        memo[atom["atom_id"]] = result
        return result

    return [normalize(atom) for atom in atoms if atom["atom_kind"] == "projection"]


def _normalize_expected_projection(projection: dict[str, Any]) -> dict[str, Any]:
    slots = RELATIONS[projection["relation"]]["slots"]
    arguments = []
    for slot, argument in zip(slots, projection["arguments"]):
        normalized = {
            "role": slot["role"], "value_type": slot["value_type"],
            "state": argument["state"],
        }
        if "ref" in argument:
            normalized["ref"] = argument["ref"]
        arguments.append(normalized)
    return {
        "clause_role": projection["clause_role"],
        "speech_act": projection["speech_act"],
        "relation": projection["relation"],
        "arguments": arguments,
    }


def canonical_expected(expectation: dict[str, Any]) -> dict[str, Any]:
    if expectation["adjudication"] == "exact":
        return {
            "status": "supported",
            "projections": [_normalize_expected_projection(item) for item in expectation["projections"]],
        }
    alternatives = [
        [_normalize_expected_projection(item) for item in alternative["projections"]]
        for alternative in expectation["alternatives"]
    ]
    return {
        "status": "typed_ambiguity",
        "alternatives": sorted(alternatives, key=canonical_hash),
    }


def canonical_candidate(frame: dict[str, Any]) -> dict[str, Any]:
    if frame["status"] == "supported":
        return {"status": "supported", "projections": canonical_analysis(frame["atoms"])}
    if frame["status"] == "typed_ambiguity":
        alternatives = [canonical_analysis(item["atoms"]) for item in frame["alternatives"]]
        return {"status": "typed_ambiguity", "alternatives": sorted(alternatives, key=canonical_hash)}
    return {"status": "unsupported"}


def _direct_projection(projection: dict[str, Any]) -> bool:
    if (
        projection["clause_role"] != "main_request"
        or projection["speech_act"] != "open_question"
        or projection["relation"] != "spatial.located_at"
        or len(projection["arguments"]) != 3
    ):
        return False
    entity, position, time_argument = projection["arguments"]
    return (
        entity.get("state") == "bound" and entity.get("ref") == "actor.current"
        and position.get("state") == "unknown"
        and time_argument.get("state") == "bound" and time_argument.get("ref") == "time.current"
    )


def direct_current_location_targets(frame: dict[str, Any]) -> list[int]:
    if frame.get("status") != "supported":
        return []
    return [
        atom["atom_id"] for atom in frame["atoms"]
        if atom["atom_kind"] == "projection" and _direct_projection(canonical_projection(
            atom, {item["atom_id"]: item for item in frame["atoms"]},
        ))
    ]


def direct_current_location(frame: dict[str, Any]) -> bool:
    return bool(direct_current_location_targets(frame))


def _analysis_dependency_state(projections: list[dict[str, Any]]) -> tuple[bool, bool]:
    derived = any(
        argument.get("state") == "derived" and argument.get("ref") == "actor.current_position"
        for projection in projections if projection["clause_role"] == "main_request"
        for argument in projection["arguments"]
    )
    context = any(
        argument.get("state") == "bound"
        and argument.get("ref") in {"context.current_spatial", "context.current_place"}
        for projection in projections if projection["clause_role"] == "main_request"
        for argument in projection["arguments"]
    )
    return derived, context


def dependency_current_location(frame: dict[str, Any]) -> str:
    canonical = canonical_candidate(frame)
    if canonical["status"] == "supported":
        projections = canonical["projections"]
        derived, context = _analysis_dependency_state(projections)
        if derived:
            return "required"
        if context:
            return "context_resolution_required"
        if any(_direct_projection(item) for item in projections):
            return "not_applicable"
        return "forbidden"
    if canonical["status"] == "typed_ambiguity":
        states = [_analysis_dependency_state(item) for item in canonical["alternatives"]]
        derived_values = [item[0] for item in states]
        if all(derived_values):
            return "required"
        if any(derived_values):
            return "clarification_required"
        if any(item[1] for item in states):
            return "context_resolution_required"
    return "forbidden"


def safety_obligations(frame: dict[str, Any]) -> list[str]:
    canonical = canonical_candidate(frame)
    analyses = (
        [canonical["projections"]] if canonical["status"] == "supported"
        else canonical.get("alternatives", [])
    )
    obligations: set[str] = set()
    for projections in analyses:
        for projection in projections:
            is_open_request = (
                projection["clause_role"] == "main_request"
                and projection["speech_act"] == "open_question"
            )
            is_effect_request = (
                projection["clause_role"] == "main_request"
                and projection["speech_act"] == "imperative"
            )
            if is_open_request and projection["relation"] == "spatial.located_at" and len(projection["arguments"]) == 3:
                entity, position, time_argument = projection["arguments"]
                if entity.get("ref") == "person.explicit" and position.get("state") == "unknown":
                    obligations.add("external_subject_authority")
                if (
                    entity.get("ref") == "actor.current" and position.get("state") == "unknown"
                    and time_argument.get("ref") == "time.historical_explicit"
                ):
                    obligations.add("current_only_capability_mismatch")
            if is_effect_request and projection["relation"] in {"communication.send", "acl.share"}:
                obligations.add("effectful_outbound_consent")
    if canonical["status"] == "typed_ambiguity" and any(
        any(
            projection["clause_role"] == "main_request"
            and projection["speech_act"] == "imperative"
            and projection["relation"] == "movement.destination"
            for projection in projections
        )
        for projections in canonical["alternatives"]
    ):
        obligations.add("effectful_destination_clarification")
    return sorted(obligations)


def request_body(query: str, seed: int = 92) -> dict[str, Any]:
    payload = {"original_request": query, "segments": unicode_segments(query)}
    return {
        "model": "local", "temperature": 0, "seed": seed, "max_tokens": 2600,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "phase1_typed_relational_v264", "schema": live_schema(), "strict": True,
        }},
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def call_once(query: str, endpoint: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(request_body(query, seed), ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw), {"latency_ms": (time.perf_counter() - started) * 1000, "error": ""}
    except Exception as exc:
        return {}, {
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error": "transport_or_json", "error_type": type(exc).__name__,
        }


def _validate_external_gate_payload(lock: dict[str, Any]) -> list[str]:
    errors = []
    expected_scalars = {
        "lock_version": "metnos.v26.4-external-gate-lock/1.0",
        "status": "authorized_one_native_run",
        "freeze_sha256": sha(FREEZE_PATH) if FREEZE_PATH.exists() else "",
        "registry_sha256": sha(REGISTRY_PATH),
        "oracle_freeze_sha256": EXPECTED_ORACLE_HASHES[ORACLE_FREEZE_PATH],
        "authorized_case_count": 34,
        "authorized_repetitions": 1,
    }
    for key, expected in expected_scalars.items():
        if lock.get(key) != expected:
            errors.append(key)
    for prefix in ("independent_review", "oracle_review", "verifier"):
        path_value = lock.get(f"{prefix}_path")
        hash_value = lock.get(f"{prefix}_sha256")
        if not isinstance(path_value, str) or not isinstance(hash_value, str):
            errors.append(prefix)
            continue
        path = Path(path_value)
        if not path.is_file() or sha(path) != hash_value:
            errors.append(prefix)
    output_path = lock.get("authorized_output_path")
    if not isinstance(output_path, str) or not output_path.startswith("/tmp/metnos_v264_"):
        errors.append("authorized_output_path")
    elif Path(output_path).exists():
        errors.append("authorized_output_already_exists")
    return errors


def verify_external_gate() -> dict[str, Any]:
    if not EXTERNAL_GATE_PATH.is_file():
        raise RuntimeError("V26.4 external independent gate lock is missing")
    lock = json.loads(EXTERNAL_GATE_PATH.read_text())
    errors = _validate_external_gate_payload(lock)
    if errors:
        raise RuntimeError(f"V26.4 external gate rejected: {sorted(set(errors))}")
    return lock


def run_case(query: str, endpoint: str) -> dict[str, Any]:
    verify_freeze()
    verify_external_gate()
    segments = unicode_segments(query)
    frame, transport = call_once(query, endpoint, 92)
    validation = (
        validate_frame(frame, segments) if not transport["error"]
        else {"valid": False, "errors": [_error(transport["error"], "", transport.get("error_type", "transport"))]}
    )
    common = {
        "segment_count": len(segments),
        "segment_layout_sha256": canonical_hash([
            {key: segment[key] for key in ("id", "start_char", "end_char")}
            for segment in segments
        ]),
        "model_calls": 1, "latency_ms": transport["latency_ms"],
        "transport_error": transport["error"],
    }
    if validation["valid"]:
        return {**common, "status": "evaluated", "frame": frame, "validation": {"valid": True, "error_codes": []}}
    return {
        **common, "status": "not_evaluated",
        "validation": {"valid": False, "error_codes": [item["code"] for item in validation["errors"]]},
    }


def controls() -> list[dict[str, Any]]:
    return json.loads(CONTROLS_PATH.read_text())["cases"]


def _oracle_expectations() -> dict[str, dict[str, Any]]:
    overlay = json.loads(ORACLE_OVERLAY_PATH.read_text())
    templates = overlay["templates"]
    return {
        case["case_id"]: templates[case["template_ref"]]
        for case in overlay["cases"]
    }


def evaluate_records(records: list[dict[str, Any]], fixture: dict[str, Any]) -> dict[str, Any]:
    oracle_by_opaque = {item["opaque_case_id"]: item for item in fixture["cases"]}
    rows = []
    for record in records:
        expected = oracle_by_opaque[record["opaque_case_id"]]
        result = record["result"]
        frame = result.get("frame")
        evidence_ok = result.get("status") == "evaluated" and result.get("validation", {}).get("valid") is True
        if not evidence_ok or not frame:
            rows.append({
                "opaque_case_id": record["opaque_case_id"],
                "query_sha256_utf8": record["query_sha256_utf8"],
                "evidence_ok": False, "coverage_ok": False,
                "direct_binding_ok": False, "dependency_ok": False,
                "safety_ok": False, "all_gates_ok": False,
                "predicted_direct": None, "usable_direct": None,
                "expected_direct": expected["expected_direct"],
            })
            continue
        actual_canonical = canonical_candidate(frame)
        expected_canonical = expected["canonical_expectation"]
        coverage_ok = actual_canonical == expected_canonical
        predicted_direct = direct_current_location(frame)
        dependency = dependency_current_location(frame)
        safety = safety_obligations(frame)
        direct_ok = predicted_direct == expected["expected_direct"]
        dependency_ok = dependency == expected["expected_dependency"]
        safety_ok = safety == expected["expected_safety"]
        usable_direct = predicted_direct and coverage_ok and evidence_ok and safety_ok
        all_ok = evidence_ok and coverage_ok and direct_ok and dependency_ok and safety_ok
        rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "query_sha256_utf8": record["query_sha256_utf8"],
            "evidence_ok": evidence_ok, "coverage_ok": coverage_ok,
            "direct_binding_ok": direct_ok, "dependency_ok": dependency_ok,
            "safety_ok": safety_ok, "all_gates_ok": all_ok,
            "predicted_direct": predicted_direct, "usable_direct": usable_direct,
            "expected_direct": expected["expected_direct"],
            "predicted_dependency": dependency,
        })
    positives = [item for item in rows if item["expected_direct"]]
    negatives = [item for item in rows if not item["expected_direct"]]
    return {
        "summary": {
            "records": len(rows),
            "evidence_valid": sum(item["evidence_ok"] for item in rows),
            "coverage_exact": sum(item["coverage_ok"] for item in rows),
            "direct_binding_exact": sum(item["direct_binding_ok"] for item in rows),
            "dependency_exact": sum(item["dependency_ok"] for item in rows),
            "safety_exact": sum(item["safety_ok"] for item in rows),
            "all_gates_exact": sum(item["all_gates_ok"] for item in rows),
            "positive_usable": sum(item["usable_direct"] is True for item in positives),
            "positive_total": len(positives),
            "negative_usable_leakage": sum(item["usable_direct"] is True for item in negatives),
            "negative_total": len(negatives),
            "model_calls": sum(record["result"].get("model_calls", 0) for record in records),
            "retries": 0,
            "general_clause_coverage": "not_measured_by_controls34",
        },
        "records": rows,
    }


def run_controls(endpoint: str) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text())
    records = []
    for case in controls():
        records.append({
            "opaque_case_id": sha_text(case["id"]),
            "query_sha256_utf8": sha_text(case["query"]),
            "result": run_case(case["query"], endpoint),
        })
    evaluation = evaluate_records(records, fixture)
    latencies = [record["result"]["latency_ms"] for record in records]
    return {
        "version": VERSION, "freeze_sha256": sha(FREEZE_PATH),
        "summary": {
            **evaluation["summary"],
            "latency_call_p50_ms": statistics.median(latencies),
            "latency_call_p95_ms": statistics.quantiles(latencies, n=100, method="inclusive")[94],
        },
        "records": records, "evaluation_records": evaluation["records"],
        "native_run": True, "posthoc_credit": False,
    }


def _proof(kind: str, *, start: int = 1, end: int = 1, predicate: int = 2) -> dict[str, Any]:
    if kind == "explicit_segment":
        return {"kind": kind, "start_segment_id": start, "end_segment_id": end}
    if kind in {"predicate_morphology", "tense_morphology"}:
        return {"kind": kind, "predicate_segment_id": predicate}
    return {"kind": kind}


def canonical_direct_frame() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segments = [
        {"id": 1, "text": "x", "start_char": 0, "end_char": 1},
        {"id": 2, "text": "y", "start_char": 1, "end_char": 2},
        {"id": 3, "text": "z", "start_char": 2, "end_char": 3},
    ]
    atom = {
        "atom_id": 1, "atom_kind": "projection", "clause_id": 1,
        "clause_start_segment_id": 1, "clause_end_segment_id": 3,
        "clause_role": "main_request", "clause_role_proof": _proof("discourse_structure"),
        "speech_act": "open_question", "speech_act_proof": _proof("clause_construction"),
        "relation": "spatial.located_at", "relation_proof": _proof("clause_construction"),
        "arguments": [
            {"kind": "bound", "ref": "actor.current", "proof": _proof("predicate_morphology", predicate=2)},
            {"kind": "unknown", "proof": _proof("interrogative_construction")},
            {"kind": "bound", "ref": "time.current", "proof": _proof("utterance_context")},
        ],
    }
    return {"status": "supported", "atoms": [atom]}, segments


def canonical_dependency_frame() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frame, segments = canonical_direct_frame()
    dependency = copy.deepcopy(frame["atoms"][0])
    dependency.pop("clause_role"); dependency.pop("clause_role_proof")
    dependency.pop("speech_act"); dependency.pop("speech_act_proof")
    dependency.update({
        "atom_id": 1, "atom_kind": "dependency",
        "relation_proof": _proof("relation_composition"),
    })
    dependency["arguments"][1] = {"kind": "output", "proof": _proof("relation_composition")}
    projection = {
        "atom_id": 2, "atom_kind": "projection", "clause_id": 1,
        "clause_start_segment_id": 1, "clause_end_segment_id": 3,
        "clause_role": "main_request", "clause_role_proof": _proof("discourse_structure"),
        "speech_act": "imperative", "speech_act_proof": _proof("clause_construction"),
        "relation": "spatial.near", "relation_proof": _proof("clause_construction"),
        "arguments": [
            {"kind": "unknown", "proof": _proof("interrogative_construction")},
            {"kind": "from_atom_output", "atom_id": 1, "output_index": 2, "proof": _proof("relation_composition")},
            {"kind": "bound", "ref": "time.current", "proof": _proof("utterance_context")},
        ],
    }
    return {"status": "supported", "atoms": [dependency, projection]}, segments


def _first_usable_proof(kinds: list[str]) -> dict[str, Any]:
    preferred = next((kind for kind in ("explicit_segment", "predicate_morphology", "interrogative_construction", "utterance_context", "discourse_context") if kind in kinds), kinds[0])
    return _proof(preferred, start=1, end=2, predicate=2)


def _analysis_from_oracle_projections(projections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construct a proof-valid graph from semantic gold without source text."""
    atoms: list[dict[str, Any]] = []
    next_atom_id = 1
    for clause_id, projection in enumerate(projections, 1):
        derived_source: int | None = None
        if any(argument.get("state") == "derived" for argument in projection["arguments"]):
            dependency = copy.deepcopy(canonical_dependency_frame()[0]["atoms"][0])
            dependency.update({
                "atom_id": next_atom_id,
                "clause_id": clause_id,
                "clause_start_segment_id": 1,
                "clause_end_segment_id": 3,
            })
            atoms.append(dependency)
            derived_source = next_atom_id
            next_atom_id += 1
        slots = RELATIONS[projection["relation"]]["slots"]
        arguments = []
        for slot, expected in zip(slots, projection["arguments"]):
            if expected["state"] == "bound":
                binding = {"kind": "bound", "ref": expected["ref"]}
                binding["proof"] = _first_usable_proof(_binding_allowed_proofs(slot, {**binding, "proof": {}}))
            elif expected["state"] == "unknown":
                binding = {"kind": "unknown"}
                binding["proof"] = _first_usable_proof(_binding_allowed_proofs(slot, {**binding, "proof": {}}))
            else:
                if derived_source is None:
                    raise ValueError("derived oracle argument lacks producer")
                binding = {
                    "kind": "from_atom_output", "atom_id": derived_source,
                    "output_index": 2, "proof": _proof("relation_composition"),
                }
            arguments.append(binding)
        atoms.append({
            "atom_id": next_atom_id, "atom_kind": "projection", "clause_id": clause_id,
            "clause_start_segment_id": 1, "clause_end_segment_id": 3,
            "clause_role": projection["clause_role"],
            "clause_role_proof": _proof("discourse_structure"),
            "speech_act": projection["speech_act"],
            "speech_act_proof": _proof("clause_construction"),
            "relation": projection["relation"],
            "relation_proof": _proof("clause_construction"),
            "arguments": arguments,
        })
        next_atom_id += 1
    return atoms


def frame_from_oracle_expectation(expectation: dict[str, Any]) -> dict[str, Any]:
    if expectation["adjudication"] == "exact":
        return {
            "status": "supported",
            "atoms": _analysis_from_oracle_projections(expectation["projections"]),
        }
    return {
        "status": "typed_ambiguity",
        "alternatives": [
            {
                "alternative_id": index,
                "atoms": _analysis_from_oracle_projections(alternative["projections"]),
            }
            for index, alternative in enumerate(expectation["alternatives"], 1)
        ],
    }


def _runtime_ast_isolated() -> bool:
    tree = ast.parse(RUNNER_PATH.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    forbidden = {
        "controls", "CONTROLS_PATH", "ORACLE_OVERLAY_PATH", "FIXTURE_PATH",
        "expected", "gold", "oracle", "overlay",
    }
    for name in ("request_body", "call_once", "run_case"):
        symbols = (
            {item.id for item in ast.walk(functions[name]) if isinstance(item, ast.Name)}
            | {item.attr for item in ast.walk(functions[name]) if isinstance(item, ast.Attribute)}
        )
        if forbidden & symbols:
            return False
    return True


def mutation_tests() -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    tests.append({"id": "registry_invariants", "pass": registry_errors() == []})
    frame, segments = canonical_direct_frame()
    tests.append({"id": "direct_valid", "pass": validate_frame(frame, segments)["valid"]})
    tests.append({"id": "direct_binding", "pass": direct_current_location(frame)})
    dependency, dep_segments = canonical_dependency_frame()
    tests.append({"id": "dependency_valid", "pass": validate_frame(dependency, dep_segments)["valid"]})
    tests.append({"id": "dependency_classified", "pass": dependency_current_location(dependency) == "required"})

    def invalid(name: str, changed: dict[str, Any]) -> None:
        tests.append({"id": name, "pass": not validate_frame(changed, segments)["valid"]})

    changed = copy.deepcopy(frame); changed["atoms"][0]["speech_act"] = "assertion"
    invalid("role_speech_mismatch", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0]["relation_proof"] = _proof("tense_morphology")
    invalid("relation_tense_proof", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0]["arguments"][0]["proof"] = _proof("utterance_context")
    invalid("actor_current_wrong_proof", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0]["arguments"][2] = {
        "kind": "bound", "ref": "time.historical_explicit", "proof": _proof("utterance_context"),
    }
    invalid("historical_wrong_proof", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0]["arguments"][0]["ref"] = "artifact.explicit"
    invalid("reference_type_mismatch", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0].pop("relation_proof")
    invalid("missing_relation_proof", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0]["arguments"][1].pop("proof")
    invalid("missing_argument_proof", changed)
    changed = copy.deepcopy(frame); changed["atoms"][0]["arguments"][1]["kind"] = "bound"
    invalid("open_question_without_unknown", changed)

    changed = copy.deepcopy(dependency)
    changed["atoms"][1]["arguments"][1]["atom_id"] = 2
    tests.append({"id": "forward_edge_invalid", "pass": not validate_frame(changed, dep_segments)["valid"]})
    changed = copy.deepcopy(dependency)
    changed["atoms"][0]["arguments"][1] = {"kind": "bound", "ref": "place.explicit", "proof": _proof("explicit_segment")}
    tests.append({"id": "dependency_output_missing_invalid", "pass": not validate_frame(changed, dep_segments)["valid"]})
    changed = copy.deepcopy(dependency)
    changed["atoms"].pop()
    tests.append({"id": "unconsumed_dependency_invalid", "pass": not validate_frame(changed, dep_segments)["valid"]})
    changed = copy.deepcopy(dependency)
    changed["atoms"][1]["relation"] = "acl.share"
    changed["atoms"][1]["arguments"] = [
        {"kind": "from_atom_output", "atom_id": 1, "output_index": 1, "proof": _proof("relation_composition")},
        {"kind": "bound", "ref": "principal.explicit", "proof": _proof("explicit_segment")},
    ]
    tests.append({"id": "non_output_edge_invalid", "pass": not validate_frame(changed, dep_segments)["valid"]})

    ambiguity = {
        "status": "typed_ambiguity",
        "alternatives": [
            {"alternative_id": 1, "atoms": copy.deepcopy(frame["atoms"])},
            {"alternative_id": 2, "atoms": copy.deepcopy(frame["atoms"])},
        ],
    }
    tests.append({"id": "duplicate_alternatives_invalid", "pass": not validate_frame(ambiguity, segments)["valid"]})
    one = {"status": "typed_ambiguity", "alternatives": ambiguity["alternatives"][:1]}
    tests.append({"id": "single_alternative_invalid", "pass": not validate_frame(one, segments)["valid"]})
    ambiguity["alternatives"][1]["atoms"][0]["speech_act"] = "polar_question"
    ambiguity["alternatives"][1]["atoms"][0]["arguments"][1] = {
        "kind": "bound", "ref": "place.explicit", "proof": _proof("explicit_segment"),
    }
    tests.append({"id": "typed_ambiguity_valid", "pass": validate_frame(ambiguity, segments)["valid"]})

    unsupported = {
        "status": "unsupported", "clauses": [{
            "clause_id": 1, "clause_start_segment_id": 1, "clause_end_segment_id": 3,
            "clause_role": "main_request", "clause_role_proof": _proof("discourse_structure"),
            "reason": "out_of_registry",
        }],
    }
    tests.append({"id": "unsupported_structurally_valid", "pass": validate_frame(unsupported, segments)["valid"]})
    schema = live_schema()
    schema_text = json.dumps(schema, sort_keys=True)
    proof_kinds = set()
    for item in schema_text.split('"kind"'):
        if '"const"' in item:
            pass
    tests.extend([
        {"id": "status_branch_order", "pass": [branch["properties"]["status"]["const"] for branch in schema["oneOf"]] == ["supported", "typed_ambiguity", "unsupported"]},
        {"id": "no_evidence_none", "pass": '"const": "none"' not in schema_text},
        {"id": "grammatical_person_removed", "pass": "grammatical_person" not in schema_text},
        {"id": "subject_ref_removed", "pass": "subject_ref" not in schema_text},
        {"id": "uax29_zh", "pass": len(unicode_segments("我在哪里")) == 4},
        {"id": "uax29_ja", "pass": len(unicode_segments("私はどこにいますか")) == 9},
        {"id": "runtime_gold_isolated", "pass": _runtime_ast_isolated()},
        {"id": "one_call_no_retry", "pass": inspect.getsource(run_case).count("call_once(") == 1 and "for attempt" not in inspect.getsource(run_case)},
        {"id": "no_prior_runner_import", "pass": "metnos_v26" not in "\n".join(line for line in RUNNER_PATH.read_text().splitlines() if line.startswith("import ") or line.startswith("from "))},
        {"id": "explicit_endpoint", "pass": "endpoint" in inspect.signature(call_once).parameters},
        {"id": "author_gate_blocked", "pass": json.loads(AUTHOR_PRE_GATE_PATH.read_text()).get("inference_allowed") is False if AUTHOR_PRE_GATE_PATH.exists() else True},
    ])
    fake_author = {
        "lock_version": "metnos.v26.4-author-pre-gate/1.0", "status": "blocked",
    }
    tests.append({"id": "author_gate_not_external", "pass": bool(_validate_external_gate_payload(fake_author))})
    overlay = json.loads(ORACLE_OVERLAY_PATH.read_text())
    oracle_registry = {
        name: {
            "kind": metadata["kind"],
            "arguments": [
                {"role": slot["role"], "value_type": slot["value_type"]}
                for slot in metadata["slots"]
            ],
        }
        for name, metadata in RELATIONS.items()
    }
    tests.append({"id": "registry_matches_oracle", "pass": oracle_registry == overlay["relation_registry"]})
    oracle_segments = canonical_direct_frame()[1]
    templates = overlay["templates"]
    for case in overlay["cases"]:
        expectation = templates[case["template_ref"]]
        oracle_frame = frame_from_oracle_expectation(expectation)
        tests.append({
            "id": f"oracle_representable:{case['case_id']}",
            "pass": (
                validate_frame(oracle_frame, oracle_segments)["valid"]
                and canonical_candidate(oracle_frame) == canonical_expected(expectation)
                and direct_current_location(oracle_frame) == (
                    expectation["gates"]["direct_current_location"] == "required"
                )
                and dependency_current_location(oracle_frame)
                    == expectation["gates"]["dependency_current_location"]
                and safety_obligations(oracle_frame)
                    == sorted(expectation["gates"]["safety_obligations"])
            ),
        })
    tests.append({"id": "oracle_hash_chain", "pass": all(path.exists() and sha(path) == expected for path, expected in EXPECTED_ORACLE_HASHES.items())})
    return {
        "tests": tests,
        "summary": {
            "tests": len(tests),
            "passed": sum(bool(item["pass"]) for item in tests),
            "failed": sum(not bool(item["pass"]) for item in tests),
        },
    }


def mutation_bundle() -> dict[str, Any]:
    core = mutation_tests()
    if core["summary"]["failed"]:
        raise RuntimeError("V26.4 core mutation suite failed")
    probe = json.loads(INDEPENDENT_PROBE_RESULT_PATH.read_text())
    expected = {
        "runner_sha256": sha(RUNNER_PATH),
        "registry_sha256": sha(REGISTRY_PATH),
        "network_calls": 0,
        "candidate_outputs_read": 0,
    }
    mismatches = {
        key: {"expected": value, "actual": probe.get(key)}
        for key, value in expected.items() if probe.get(key) != value
    }
    if probe.get("failed") or probe.get("summary", {}).get("failed") != 0:
        mismatches["probe_failures"] = probe.get("failed")
    if probe.get("summary", {}).get("tests", 0) < 125:
        mismatches["probe_test_count"] = probe.get("summary", {}).get("tests")
    if mismatches:
        raise RuntimeError(f"independent graph probe mismatch: {mismatches}")
    return {
        "version": "metnos.v26.4-offline-mutation-bundle/1.0",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "core": core,
        "independent_probe": probe,
        "acceptance": {
            "core_failed": 0,
            "independent_failed": 0,
            "oracle_representability_and_gates": "34/34",
            "independent_tests": probe["summary"]["tests"],
            "independent_validator_mutations": probe["summary"]["validator_mutations"],
            "independent_positive_controls": probe["summary"]["positive_controls"],
            "independent_classifier_controls": probe["summary"]["classifier_controls"],
            "oracle_only_mappings": probe["summary"]["oracle_only_mappings"],
        },
        "sources": {
            str(INVARIANT_REVIEW_PATH): sha(INVARIANT_REVIEW_PATH),
            str(MUTATION_PROPOSAL_PATH): sha(MUTATION_PROPOSAL_PATH),
            str(INDEPENDENT_PROBE_PATH): sha(INDEPENDENT_PROBE_PATH),
            str(INDEPENDENT_PROBE_RESULT_PATH): sha(INDEPENDENT_PROBE_RESULT_PATH),
        },
    }


def _load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_v264_contamination_auditor", AUDITOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load contamination auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contamination_audit() -> dict[str, Any]:
    auditor = _load_auditor()
    datasets, manifests = auditor.load_datasets()
    audit = auditor.audit_prompt(PROMPT_PATH, datasets)
    return {
        "audit_version": "metnos.v26.4-prompt-contamination-redacted/1.0",
        "network_calls": 0, "prompt": audit, "datasets": manifests,
        "redaction": {
            "raw_queries_persisted": False, "prompt_excerpts_persisted": False,
            "matched_ngrams_persisted": False, "opaque_fingerprints": "sha256",
        },
        "auditor_path": str(AUDITOR_PATH), "auditor_sha256": sha(AUDITOR_PATH),
    }


def _runtime_fingerprint() -> dict[str, str]:
    return {
        "python": sys.version,
        "unicode": unicodedata.unidata_version,
        "regex": regex.__version__,
        "jsonschema": importlib.metadata.version("jsonschema"),
    }


def _build_fixture() -> dict[str, Any]:
    expectations = _oracle_expectations()
    overlay = json.loads(ORACLE_OVERLAY_PATH.read_text())
    template_by_case = {item["case_id"]: item["template_ref"] for item in overlay["cases"]}
    cases = []
    for case in controls():
        expectation = expectations[case["id"]]
        cases.append({
            "opaque_case_id": sha_text(case["id"]),
            "query_sha256_utf8": sha_text(case["query"]),
            "template_ref": template_by_case[case["id"]],
            "canonical_expectation": canonical_expected(expectation),
            "expected_direct": bool(case["expect_binding"]),
            "expected_dependency": expectation["gates"]["dependency_current_location"],
            "expected_safety": sorted(expectation["gates"]["safety_obligations"]),
        })
    return {
        "version": VERSION,
        "source_controls_sha256": sha(CONTROLS_PATH),
        "oracle_overlay_sha256": sha(ORACLE_OVERLAY_PATH),
        "cases": cases,
    }


def _derived_freeze_fields() -> dict[str, Any]:
    return {
        "segmenter_source_sha256": source_bundle_sha(unicode_segments),
        "schema_prompt_bundle_sha256": source_bundle_sha(
            _proof_variant, proof_schema, _all_proof_kinds, _binding_definition,
            _atom_definition, _atoms_definition, unsupported_clause_schema,
            live_schema, system_prompt, request_body,
        ),
        "validator_bundle_sha256": source_bundle_sha(
            _error, _all_proof_kinds, _slot_proof_family, registry_errors,
            _accepted_output, _is_output_binding, _proof_count, _validate_proof,
            _binding_allowed_proofs, _validate_atom_list,
            _validate_unsupported_clauses, _clause_footprint, validate_frame,
        ),
        "classifier_bundle_sha256": source_bundle_sha(
            _dependency_ref, canonical_projection, canonical_analysis,
            canonical_graph_analysis, canonical_candidate, _direct_projection,
            direct_current_location_targets, direct_current_location,
            _analysis_dependency_state, dependency_current_location,
            safety_obligations,
        ),
        "evaluator_bundle_sha256": source_bundle_sha(
            _normalize_expected_projection, canonical_expected, evaluate_records,
        ),
        "external_gate_validator_source_sha256": source_bundle_sha(
            _validate_external_gate_payload, verify_external_gate,
        ),
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe", 92)),
        "registry_canonical_sha256": canonical_hash(REGISTRY),
        "runtime_fingerprint": _runtime_fingerprint(),
    }


def freeze() -> dict[str, Any]:
    invalid_registry = registry_errors()
    if invalid_registry:
        raise RuntimeError(f"registry invariant failure: {invalid_registry}")
    for path, expected in EXPECTED_ORACLE_HASHES.items():
        if not path.exists() or sha(path) != expected:
            raise RuntimeError(f"oracle artifact mismatch: {path}")
    SCHEMA_PATH.write_text(json.dumps(live_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    PROMPT_PATH.write_text(system_prompt())
    FIXTURE_PATH.write_text(json.dumps(_build_fixture(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    # Write a blocked author marker before mutation tests so the test can prove
    # that it cannot satisfy the external gate contract.
    AUTHOR_PRE_GATE_PATH.write_text(json.dumps({
        "version": VERSION,
        "lock_version": "metnos.v26.4-author-pre-gate/1.0",
        "status": "blocked_pending_independent_review_and_external_lock",
        "inference_allowed": False,
        "independent_final_review": "pending_on_byte_stable_bundle",
        "network_calls_before_freeze": 0,
        "required_external_gate_path": str(EXTERNAL_GATE_PATH),
        "oracle_freeze_sha256": EXPECTED_ORACLE_HASHES[ORACLE_FREEZE_PATH],
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    mutations = mutation_bundle()
    MUTATIONS_PATH.write_text(json.dumps(mutations, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    audit = contamination_audit()
    CONTAMINATION_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lock = {
        "version": VERSION, "status": "frozen_author_pre_inference",
        "network_calls_before_freeze": 0, "native_run_required": True,
        "external_independent_gate_required": True,
        "author_pre_gate_inference_allowed": False,
        "model_calls_per_case": 1, "retries": 0,
        "runner_sha256": sha(RUNNER_PATH), "registry_sha256": sha(REGISTRY_PATH),
        "schema_sha256": sha(SCHEMA_PATH), "prompt_sha256": sha(PROMPT_PATH),
        "fixture_sha256": sha(FIXTURE_PATH), "mutations_sha256": sha(MUTATIONS_PATH),
        "contamination_audit_sha256": sha(CONTAMINATION_PATH),
        "author_pre_gate_sha256": sha(AUTHOR_PRE_GATE_PATH),
        "controls_sha256": sha(CONTROLS_PATH), "auditor_sha256": sha(AUDITOR_PATH),
        "invariant_review_sha256": sha(INVARIANT_REVIEW_PATH),
        "mutation_proposal_sha256": sha(MUTATION_PROPOSAL_PATH),
        "independent_probe_sha256": sha(INDEPENDENT_PROBE_PATH),
        "independent_probe_result_sha256": sha(INDEPENDENT_PROBE_RESULT_PATH),
        "oracle_artifacts": {str(path): expected for path, expected in EXPECTED_ORACLE_HASHES.items()},
        **_derived_freeze_fields(),
        "segmentation": "Unicode UAX#29 default word boundary via regex.WORD|VERSION1",
        "runtime_dependencies": ["python", "regex", "jsonschema"],
        "prior_lab_runner_dependencies": [],
        "general_clause_coverage": "not_measured_by_controls34",
        "runtime_cutover_eligible": False,
    }
    FREEZE_PATH.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return lock


def verify_freeze() -> None:
    invalid_registry = registry_errors()
    if invalid_registry:
        raise RuntimeError(f"registry invariant failure: {invalid_registry}")
    lock = json.loads(FREEZE_PATH.read_text())
    paths = {
        "runner_sha256": RUNNER_PATH, "registry_sha256": REGISTRY_PATH,
        "schema_sha256": SCHEMA_PATH, "prompt_sha256": PROMPT_PATH,
        "fixture_sha256": FIXTURE_PATH, "mutations_sha256": MUTATIONS_PATH,
        "contamination_audit_sha256": CONTAMINATION_PATH,
        "author_pre_gate_sha256": AUTHOR_PRE_GATE_PATH,
        "controls_sha256": CONTROLS_PATH, "auditor_sha256": AUDITOR_PATH,
        "invariant_review_sha256": INVARIANT_REVIEW_PATH,
        "mutation_proposal_sha256": MUTATION_PROPOSAL_PATH,
        "independent_probe_sha256": INDEPENDENT_PROBE_PATH,
        "independent_probe_result_sha256": INDEPENDENT_PROBE_RESULT_PATH,
    }
    changed = {
        key: {"locked": lock.get(key), "actual": sha(path)}
        for key, path in paths.items() if lock.get(key) != sha(path)
    }
    for path_text, expected in lock.get("oracle_artifacts", {}).items():
        path = Path(path_text)
        actual = sha(path) if path.exists() else "missing"
        if actual != expected:
            changed[f"oracle:{path_text}"] = {"locked": expected, "actual": actual}
    derived: dict[str, Any] = _derived_freeze_fields()
    for key, actual in derived.items():
        if lock.get(key) != actual:
            changed[key] = {"locked": lock.get(key), "actual": actual}
    if changed:
        raise RuntimeError(f"V26.4 freeze changed: {changed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--endpoint")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.freeze:
        print(json.dumps(freeze(), sort_keys=True))
    if args.self_test:
        result = mutation_tests()
        print(json.dumps(result["summary"], sort_keys=True))
        if result["summary"]["failed"]:
            return 1
    if args.controls:
        if not args.endpoint or not args.output:
            raise SystemExit("--controls requires explicit --endpoint and --output")
        external = verify_external_gate()
        if args.output != external["authorized_output_path"]:
            raise SystemExit("--output does not match external gate lock")
        result = run_controls(args.endpoint)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
