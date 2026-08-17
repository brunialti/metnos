#!/usr/bin/env python3
"""Self-contained technical schema and validator for V26.5.1.

Mechanically extracted from the hash-pinned V26.4.1 semantic runner.  This
module contains no prompt, model request, retry, or network code.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema


VERSION = "metnos.v26.5.1-self-contained-validator/0.1"
SOURCE_PARENT_SHA256 = "6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459"
EXPECTED_REGISTRY_SHA256 = "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f"
EXTRACTED_FUNCTIONS = ['sha_text', 'canonical_hash', '_proof_variant', 'proof_schema', '_slot_proof_family', '_all_proof_kinds', '_binding_definition', '_atom_definition', '_atoms_definition', 'unsupported_clause_schema', 'live_schema', '_error', '_validate_proof', '_binding_allowed_proofs', 'registry_errors', '_accepted_output', '_is_output_binding', '_proof_count', '_validate_atom_list', '_validate_unsupported_clauses', '_clause_footprint', 'validate_frame', 'canonical_graph_analysis']
REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "v2641/metnos_v2641_typed_registry.json"
)
_REGISTRY_BYTES = REGISTRY_PATH.read_bytes()
if hashlib.sha256(_REGISTRY_BYTES).hexdigest() != EXPECTED_REGISTRY_SHA256:
    raise RuntimeError("registry hash mismatch before validator initialization")
REGISTRY: dict[str, Any] = json.loads(_REGISTRY_BYTES)
RELATIONS: dict[str, Any] = REGISTRY["relations"]
REFERENCES: dict[str, Any] = REGISTRY["reference_registry"]
PROOF_FAMILIES: dict[str, list[str]] = REGISTRY["proof_families"]
LIMITS: dict[str, int] = REGISTRY["limits"]

def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def canonical_hash(value: Any) -> str:
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

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
