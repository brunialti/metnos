"""Deterministic registry-to-schema/prompt projection.

The projector receives a registry object.  It contains no query strings, gold
records, linguistic trigger lists, route exceptions or privileged control and
barrier names.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from intent_shadow_io import canonical_json_bytes, freeze_json
from intent_shadow_registry import CONTRACT_VERSION, registry_payload_sha256, validate_registry_document
from intent_shadow_types import FrozenObject, ModelContract


class ProjectionError(ValueError):
    pass


def _closed_object(
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _data_edge_schema() -> dict[str, Any]:
    return _closed_object(
        {
            "from": {"type": "integer", "minimum": 0},
            "output": {"type": "string", "minLength": 1},
            "input": {"type": "string", "minLength": 1},
        },
        ["from"],
    )


def _system_control_schema(name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    model_inputs = metadata["model_facing_inputs"]
    if model_inputs:
        raise ProjectionError(
            "registry lists model-facing control inputs without JSON type contracts"
        )
    return _closed_object(
        {
            "kind": {"const": "system_control"},
            "control": {"const": name},
        },
        ["kind", "control"],
    )


def _barrier_schema(name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata["model_facing_inputs"]:
        raise ProjectionError(
            "registry lists model-facing barrier inputs outside the 0.1 node contract"
        )
    case_variants = [
        _closed_object(
            {
                "outcome": {"const": outcome},
                "body": {"$ref": "#/$defs/nonempty_body"},
            },
            ["outcome", "body"],
        )
        for outcome in metadata["outcomes"]
    ]
    properties: dict[str, Any] = {
        "kind": {"const": "barrier"},
        "barrier": {"const": name},
        "cases": {
            "type": "array",
            "minItems": 1,
            "maxItems": len(case_variants),
            "items": {"oneOf": case_variants},
        },
    }
    if metadata["input_ports"]:
        properties["data_from"] = {"$ref": "#/$defs/data_edges"}
    return _closed_object(properties, ["kind", "barrier", "cases"])


def build_schema(registry: dict[str, Any]) -> dict[str, Any]:
    validate_registry_document(registry)
    routes = list(registry["operations"])
    operation = _closed_object(
        {
            "kind": {"const": "operation"},
            "route": {"type": "string", "enum": routes},
            "data_from": {"$ref": "#/$defs/data_edges"},
        },
        ["kind", "route"],
    )
    barrier_variants = [
        _barrier_schema(name, metadata)
        for name, metadata in registry["barriers"].items()
    ]
    node_variants: list[dict[str, Any]] = [operation]
    node_variants.extend(barrier_variants)

    operation_graph = _closed_object(
        {
            "kind": {"const": "operation_graph"},
            "body": {"$ref": "#/$defs/nonempty_body"},
        },
        ["kind", "body"],
    )
    controls = [
        _system_control_schema(name, metadata)
        for name, metadata in registry["system_controls"].items()
    ]
    unrepresentable = _closed_object(
        {
            "kind": {"const": "unrepresentable"},
            "reason": {
                "type": "string",
                "enum": list(registry["unrepresentable_reasons"]),
            },
        },
        ["kind", "reason"],
    )
    root_variants: list[dict[str, Any]] = [operation_graph]
    root_variants.extend(controls)
    root_variants.append(unrepresentable)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:metnos:intent-shadow:model-document:0.1",
        "title": "Metnos intent shadow 0.1",
        "oneOf": root_variants,
        "$defs": {
            "data_edge": _data_edge_schema(),
            "data_edges": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/data_edge"},
            },
            "node": {"oneOf": node_variants},
            "nonempty_body": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/node"},
            },
        },
    }


def build_prompt(registry: dict[str, Any]) -> str:
    validate_registry_document(registry)
    lines = [
        "Emit exactly one JSON document accepted by the supplied schema.",
        "Select one exclusive root kind. Do not emit explanations or extra fields.",
        "Use only registry keys. Preserve operation order and indispensable clauses.",
        "A meaning outside the registry uses a registered unrepresentable reason; malformed output is not an abstention.",
        "A barrier owns every node in each emitted outcome body. Omitted outcomes are empty.",
        "A data edge may reference only a dominating prior operation ordinal; omit uniquely derivable ports.",
        "Ordinary operation registry:",
    ]
    for route, metadata in registry["operations"].items():
        verb = metadata.get("verb")
        obj = metadata.get("object")
        lines.append(f"- {route} | verb={verb} | object={obj}")
    lines.append("System-control registry:")
    for name, metadata in registry["system_controls"].items():
        inputs = ",".join(metadata["model_facing_inputs"]) or "none"
        lines.append(f"- {name} | model_inputs={inputs}")
    lines.append("Barrier registry:")
    for name, metadata in registry["barriers"].items():
        outcomes = ",".join(metadata["outcomes"])
        lines.append(f"- {name} | outcomes={outcomes}")
    lines.append("Unrepresentable-reason registry:")
    for reason in registry["unrepresentable_reasons"]:
        lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"


def materialize_schema(schema: dict[str, Any]) -> bytes:
    return (json.dumps(schema, ensure_ascii=False, allow_nan=False, indent=1) + "\n").encode(
        "utf-8"
    )


def compile_model_contract(registry: dict[str, Any]) -> ModelContract:
    schema = build_schema(registry)
    prompt = build_prompt(registry)
    frozen = freeze_json(schema)
    if not isinstance(frozen, FrozenObject):
        raise AssertionError("schema root is not an object")
    return ModelContract(
        contract_version=CONTRACT_VERSION,
        registry_sha256=registry_payload_sha256(registry),
        schema=frozen,
        prompt=prompt,
        schema_sha256=sha256(materialize_schema(schema)).hexdigest(),
        prompt_sha256=sha256(prompt.encode("utf-8")).hexdigest(),
    )


def schema_semantic_sha256(schema: dict[str, Any]) -> str:
    return sha256(canonical_json_bytes(schema)).hexdigest()

