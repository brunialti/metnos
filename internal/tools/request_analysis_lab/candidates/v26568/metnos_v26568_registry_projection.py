#!/usr/bin/env python3
"""V26.5.6.8 relation-typed projection of the frozen typed registry.

Pure, offline, deterministic. No network, no model, no filesystem writes.

Why this exists. The V26.5.6.7 live run of 10 August was an unintended
controlled experiment, and it settled the argument:

* what V26.5.6.5 moved INTO the schema -- clause identity -- the model got
  right on 34 cases out of 34: zero expansion codes, zero identity codes;
* what stayed in the PROSE of the prompt -- which proof family each claim may
  carry, how many arguments a relation takes, which references a slot accepts
  -- the model got wrong on 34 cases out of 34.

72 of the 80 proof errors were a single obligation the prose stated and the
model ignored: the clause role must be proved by discourse structure, not by
pointing at a segment.

So this projection applies the project's own rule -- an invariant is either
expressible in the schema or it is not fatal -- to everything the registry
already determines:

* argument COUNT is fixed per relation with prefixItems, so wrong arity is
  unrepresentable rather than reported (`relation_arity`);
* every argument POSITION is typed on its own: the references admissible there
  are exactly those whose value type the slot accepts, so an incompatible
  reference is unrepresentable (`reference_type`);
* every proof carries the closed family the registry allows FOR THAT CLAIM --
  clause role, speech act, relation, each argument state, each reference --
  so a misplaced proof family is unrepresentable (`proof_family`);
* a dependency's output sits at the registry-declared slot, and that slot
  carries nothing else, so both a misplaced output and a missing one are
  unrepresentable (`dependency_output_slot`, `dependency_output_count`);
* clause role and speech act agree by construction (`role_speech`).

Nothing here is a new vocabulary, a linguistic list or a prompt trick: every
enum is read out of the frozen V26.4.1 registry. The document SHAPE is exactly
the clause-owned shape of V26.5.6.6, so the frozen expander, the frozen
validator and the pipeline are reused unchanged.

What deliberately stays reported and non-fatal: counting rules that span
positions (an open question carries exactly one unknown), source order, span
containment, and the atom budget. A JSON schema cannot express a sum or a
comparison across siblings, and pretending otherwise is what killed V26.5.6.4.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

VERSION = "metnos.v26.5.6.8-registry-projection/1.0"
EXPECTED_REGISTRY_SHA256 = (
    "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f"
)

KEY_STATUS = "status"
KEY_CLAUSES = "clauses"
KEY_READINGS = "readings"
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


def all_proof_kinds(registry: dict[str, Any]) -> list[str]:
    kinds = [k for family in registry["proof_families"].values() for k in family]
    kinds.extend(
        kind
        for metadata in registry["reference_registry"].values()
        for kind in metadata.get("proof_families", [])
    )
    return list(dict.fromkeys(kinds))


def fingerprint_vocabulary(registry: dict[str, Any]) -> frozenset[str]:
    """The closed set of strings a structural fingerprint may contain."""
    return frozenset(
        set(registry["relations"])
        | set(registry["clause_roles"])
        | set(registry["speech_acts"])
        | set(all_proof_kinds(registry))
        | {"bound", "unknown", "output", BINDING_FROM_PRIOR,
           STATUS_SUPPORTED, STATUS_TYPED_AMBIGUITY, STATUS_UNSUPPORTED}
    )


# --------------------------------------------------------------------------
# proofs: one closed family per claim, straight out of the registry
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


class _ProofSets:
    """Interns one $def per distinct closed proof family, named by its members."""

    def __init__(self) -> None:
        self.definitions: dict[str, Any] = {}

    def reference(self, kinds: list[str]) -> dict[str, Any]:
        unique = list(dict.fromkeys(kinds))
        if not unique:
            raise RuntimeError("a claim cannot have an empty proof family")
        name = "proof_of_" + "_or_".join(sorted(unique))
        if name not in self.definitions:
            variants = [_proof_variant(kind) for kind in unique]
            self.definitions[name] = (
                variants[0] if len(variants) == 1 else {"oneOf": variants}
            )
        return {"$ref": f"#/$defs/{name}"}


def _slot_argument_family(registry: dict[str, Any], slot: dict[str, Any]) -> list[str]:
    families = registry["proof_families"]
    return families["time"] if slot["role"] == "time" else families["argument"]


def _slot_unknown_family(registry: dict[str, Any], slot: dict[str, Any]) -> list[str]:
    families = registry["proof_families"]
    return families["time"] if slot["role"] == "time" else families["unknown_argument"]


def _references_for_slot(
    registry: dict[str, Any], slot: dict[str, Any],
) -> list[str]:
    accepted = slot.get("accepted_reference_types") or []
    return [
        name for name, metadata in registry["reference_registry"].items()
        if metadata.get("value_type") in accepted
    ]


# --------------------------------------------------------------------------
# bindings: one $def per (relation, position, projection|dependency)
# --------------------------------------------------------------------------

def _binding_definition(
    registry: dict[str, Any], proofs: _ProofSets, relation: str,
    index: int, slot: dict[str, Any], *, dependency: bool, max_atoms: int,
) -> dict[str, Any]:
    metadata = registry["relations"][relation]
    branches: list[dict[str, Any]] = []

    for reference in _references_for_slot(registry, slot):
        families = registry["reference_registry"][reference].get("proof_families")
        kinds = families if families else _slot_argument_family(registry, slot)
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "bound"},
                "ref": {"const": reference},
                "proof": proofs.reference(list(kinds)),
            },
            "required": ["kind", "ref", "proof"],
        })

    output = metadata.get("dependency_output")
    if dependency:
        # The produced value sits at the registry-declared slot, and that slot
        # carries NOTHING else: the frozen validator demands exactly one output
        # per dependency, so making the position anything but the output would
        # only produce a frame it will reject.
        if output and output.get("slot_index") == index:
            return {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "output"},
                    "proof": proofs.reference(
                        list(registry["proof_families"]["dependency_output"])),
                },
                "required": ["kind", "proof"],
            }
    else:
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": "unknown"},
                "proof": proofs.reference(_slot_unknown_family(registry, slot)),
            },
            "required": ["kind", "proof"],
        })

    # an edge may only feed a slot that declares it accepts a produced value
    if slot.get("accepted_outputs"):
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"const": BINDING_FROM_PRIOR},
                BINDING_SOURCE_ORDINAL: {
                    "type": "integer", "minimum": 1, "maximum": max_atoms,
                },
                "proof": proofs.reference(
                    list(registry["proof_families"]["from_atom_output"])),
            },
            "required": ["kind", BINDING_SOURCE_ORDINAL, "proof"],
        })

    if not branches:
        raise RuntimeError(f"slot {relation}[{index}] admits nothing")
    return branches[0] if len(branches) == 1 else {"oneOf": branches}


def _slug(value: str) -> str:
    return value.replace(".", "_")


def build_schema(registry: dict[str, Any]) -> dict[str, Any]:
    """Derive the relation-typed clause-owned response schema."""
    relations = registry["relations"]
    limits = registry["limits"]
    max_atoms = limits["max_atoms_per_analysis"]
    max_clauses = limits["max_clauses_per_analysis"]
    max_alternatives = limits["max_alternatives"]
    proofs = _ProofSets()
    definitions: dict[str, Any] = {}

    # --- one typed argument list per relation, for both atom kinds ---------
    for relation, metadata in relations.items():
        slots = metadata["slots"]
        for dependency in (False, True):
            if dependency and metadata.get("dependency_output") is None:
                continue          # this relation cannot be an auxiliary producer
            prefix = "dependency" if dependency else "projection"
            items = []
            for index, slot in enumerate(slots, 1):
                name = f"{prefix}_binding_{_slug(relation)}_{index}"
                definitions[name] = _binding_definition(
                    registry, proofs, relation, index, slot,
                    dependency=dependency, max_atoms=max_atoms,
                )
                items.append({"$ref": f"#/$defs/{name}"})
            # prefixItems fixes the arity AND types every position on its own
            definitions[f"{prefix}_arguments_{_slug(relation)}"] = {
                "type": "array",
                "prefixItems": items,
                "minItems": len(items), "maxItems": len(items),
            }

    # --- dependencies: relation + its own proof family ---------------------
    dependency_branches = []
    for relation, metadata in relations.items():
        if metadata.get("dependency_output") is None:
            continue
        dependency_branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "relation": {"const": relation},
                "relation_proof": proofs.reference(
                    list(registry["proof_families"]["dependency_relation"])),
                "arguments": {
                    "$ref": f"#/$defs/dependency_arguments_{_slug(relation)}"},
            },
            "required": ["relation", "relation_proof", "arguments"],
        })
    if not dependency_branches:
        raise RuntimeError("the registry declares no auxiliary producer")
    definitions["dependency"] = (
        dependency_branches[0] if len(dependency_branches) == 1
        else {"oneOf": dependency_branches}
    )

    # --- projections: relation, and the speech acts the role allows --------
    for role, role_metadata in registry["clause_roles"].items():
        allowed_speech = list(role_metadata["speech_acts"])
        branches = []
        for relation in relations:
            branches.append({
                "type": "object", "additionalProperties": False,
                "properties": {
                    "relation": {"const": relation},
                    "relation_proof": proofs.reference(
                        list(registry["proof_families"]["relation"])),
                    "speech_act": (
                        {"const": allowed_speech[0]} if len(allowed_speech) == 1
                        else {"type": "string", "enum": allowed_speech}
                    ),
                    "speech_act_proof": proofs.reference(
                        list(registry["proof_families"]["speech_act"])),
                    "arguments": {
                        "$ref": f"#/$defs/projection_arguments_{_slug(relation)}"},
                },
                "required": ["relation", "relation_proof", "speech_act",
                             "speech_act_proof", "arguments"],
            })
        definitions[f"projection_for_{role}"] = {"oneOf": branches}

    span_props = {
        SPAN_START: {"type": "integer", "minimum": 1},
        SPAN_END: {"type": "integer", "minimum": 1},
    }
    clause_role_proof = proofs.reference(
        list(registry["proof_families"]["clause_role"]))

    # --- one analysis per clause role: role, its proof, and its projection --
    for role in registry["clause_roles"]:
        analysis_props = {
            "clause_role": {"const": role},
            "clause_role_proof": clause_role_proof,
            KEY_PROJECTION: {"$ref": f"#/$defs/projection_for_{role}"},
            KEY_DEPENDENCIES: {
                "type": "array", "minItems": 1, "maxItems": max_atoms - 1,
                "items": {"$ref": "#/$defs/dependency"},
            },
        }
        required = ["clause_role", "clause_role_proof", KEY_PROJECTION]
        definitions[f"analysis_{role}"] = {
            "type": "object", "additionalProperties": False,
            "properties": dict(analysis_props), "required": list(required),
        }
        definitions[f"projected_clause_{role}"] = {
            "type": "object", "additionalProperties": False,
            "properties": {**span_props, **analysis_props},
            "required": [SPAN_START, SPAN_END, *required],
        }

    roles = list(registry["clause_roles"])
    definitions["analysis"] = {
        "oneOf": [{"$ref": f"#/$defs/analysis_{role}"} for role in roles]}
    definitions["projected_clause"] = {
        "oneOf": [{"$ref": f"#/$defs/projected_clause_{role}"} for role in roles]}
    definitions["ambiguous_clause"] = {
        "type": "object", "additionalProperties": False,
        "properties": {
            **span_props,
            KEY_READINGS: {
                "type": "array", "minItems": 2, "maxItems": max_alternatives,
                "items": {"$ref": "#/$defs/analysis"},
            },
        },
        "required": [SPAN_START, SPAN_END, KEY_READINGS],
    }
    definitions["unsupported_clause"] = {
        "type": "object", "additionalProperties": False,
        "properties": {
            **span_props,
            "clause_role": {"type": "string", "enum": roles},
            "clause_role_proof": clause_role_proof,
            KEY_REASON: {"const": REASON_OUT_OF_REGISTRY},
        },
        "required": [SPAN_START, SPAN_END, "clause_role", "clause_role_proof",
                     KEY_REASON],
    }
    definitions["clause_array"] = {
        "type": "array", "minItems": 1, "maxItems": max_clauses,
        "items": {"oneOf": [
            {"$ref": "#/$defs/projected_clause"},
            {"$ref": "#/$defs/unsupported_clause"},
        ]},
        "contains": {"type": "object", "required": [KEY_PROJECTION]},
        "minContains": 1,
    }
    definitions["ambiguous_clause_array"] = {
        "type": "array", "minItems": 1, "maxItems": max_clauses,
        "items": {"oneOf": [
            {"$ref": "#/$defs/ambiguous_clause"},
            {"$ref": "#/$defs/unsupported_clause"},
        ]},
        "contains": {"type": "object", "required": [KEY_READINGS]},
        "minContains": 1,
    }
    definitions.update(proofs.definitions)

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
            KEY_CLAUSES: {"$ref": "#/$defs/ambiguous_clause_array"},
        },
        "required": [KEY_STATUS, KEY_CLAUSES],
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

The schema types every position for you. Argument count, admissible \
references, and the proof family of each claim follow from the relation you \
pick, so choose the relation first and let the schema narrow the rest. Do not \
try to widen a position the schema has closed.

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
derived order with source_ordinal, counting from one. Under typed_ambiguity \
each alternative is flattened on its own and its ordinals restart at one. Emit \
clauses in source order.

A proof states HOW a fact is known, not WHERE the words are. The clause role \
is known from discourse structure or a clause boundary; only an explicit \
segment proof carries a segment range at all. Every other proof is local and \
implicit. Overlapping proof spans are allowed. Do not invent a proof and do \
not substitute context against explicit contrary evidence.

A primary action relation is imperative and contains no unknown. A primary \
queryable open question or result-bearing imperative contains exactly one \
unknown; a polar question or assertion contains none. Scoped speech_act=none \
may preserve zero or one embedded unknown without making it a current \
request. One valid output may feed multiple later compatible slots, and a \
requested projection output may also feed a later clause. An output is not the \
syntactic or semantic subject unless its consumer slot says so.

Coverage branches are disjoint. The supported status contains one complete \
clause array; it may mix projected and out-of-registry clauses so \
representable work is preserved. The typed_ambiguity status contains one \
clause array in which every projected clause carries one reading per \
alternative, in the same alternative order in every clause: reading one \
everywhere is the first alternative, reading two everywhere is the second. A \
clause whose analysis does not change repeats the same reading in every \
alternative, and an out-of-registry clause is shared by all of them. \
Alternatives must differ materially somewhere. The unsupported status is only \
when no clause is representable. A registered relation must never be hidden \
as out-of-registry, and opaque ambiguity without alternatives is forbidden."""


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

    lines.append(
        "Resource bounds: at most {atoms} atoms in one flattening, {clauses} "
        "semantic clauses, {alternatives} alternatives, {depth} dependency "
        "edges in one path, {proofs} proofs per analysis, and {total} proofs "
        "in the complete output.".format(
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
        "clause array in source order; one reading per alternative in every "
        "projected clause of an ambiguous analysis; unknown count against the "
        "speech act; one consumed output per dependency; every source_ordinal "
        "points backward to a compatible producer inside the same flattening; "
        "explicit proof spans inside their own clause; atom budget; depth "
        "bound; complete distinct alternatives."
    )
    return "\n".join(lines) + "\n"
