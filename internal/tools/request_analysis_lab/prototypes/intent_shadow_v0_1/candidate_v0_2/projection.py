"""Build the compact JSON Schema and locale-neutral, query-free prompt."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from .canonical import canonical_json_bytes, pretty_json_bytes
from .language_tag import normalize_language_tag
from .registry_projection import CONTRACT_VERSION, projection_sha256, validate_projection


SCHEMA_ID = "urn:metnos:intent-ir:model-document:0.2"
SCHEMA_NAME = "metnos_intent_ir_v0_2"
LANGUAGE_TAG_PLACEHOLDER = "{input_language_tag}"


def _closed(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def build_schema(registry: dict[str, Any]) -> dict[str, Any]:
    validate_projection(registry)
    operation = _closed(
        {
            "route": {"type": "string", "enum": sorted(registry["operations"])},
            "from": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 0},
            },
        },
        ["route"],
    )
    barrier_variants = [
        _closed(
            {
                "barrier": {"const": name},
                "body": {"$ref": "#/$defs/nonempty_steps"},
            },
            ["barrier", "body"],
        )
        for name in sorted(registry["barriers"])
    ]
    operation_graph = _closed(
        {
            "kind": {"const": "operation_graph"},
            "steps": {"$ref": "#/$defs/nonempty_steps"},
        },
        ["kind", "steps"],
    )
    system_control = _closed(
        {
            "kind": {"const": "system_control"},
            "control": {
                "type": "string",
                "enum": sorted(registry["system_controls"]),
            },
        },
        ["kind", "control"],
    )
    unrepresentable = _closed(
        {
            "kind": {"const": "unrepresentable"},
            "reason": {
                "type": "string",
                "enum": sorted(registry["unrepresentable_reasons"]),
            },
        },
        ["kind", "reason"],
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Metnos intent IR 0.2",
        "oneOf": [operation_graph, system_control, unrepresentable],
        "$defs": {
            "step": {"oneOf": [operation, *barrier_variants]},
            "nonempty_steps": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/step"},
            },
        },
    }


def _compact_description(item: dict[str, Any], language: str) -> str | None:
    languages = item.get("languages")
    selected = languages.get(language) if type(languages) is dict else None
    if type(selected) is not dict:
        return None
    scope = selected.get("scope")
    negative = selected.get("not")
    pieces = []
    if type(scope) is str:
        pieces.append(f"SCOPE: {scope}")
    if type(negative) is str:
        pieces.append(f"NOT: {negative}")
    if pieces:
        return " ".join(pieces)
    full = selected.get("full")
    if type(full) is str:
        # Old snapshot descriptions can be very long and were not bilingual.
        # The full text remains in the frozen projection; the prompt carries a
        # bounded first statement so its size cannot grow without limit.
        first = full.split(". ", 1)[0].strip()
        return first[:360]
    return None


def build_prompt(registry: dict[str, Any], language_tag: str | None = None) -> str:
    validate_projection(registry)
    selected_language = (
        LANGUAGE_TAG_PLACEHOLDER
        if language_tag is None
        else normalize_language_tag(language_tag)
    )
    lines = [
        f"INPUT_LANGUAGE_TAG: {selected_language}",
        "OUTPUT: exactly one JSON object; root key kind comes first and selects exactly one root.",
        "FORBIDDEN: explanations, extra keys, ports, path, ordinal, outcome, continuation, or free inputs.",
        'VALID: {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
        'INVALID: {"steps":[],"kind":"operation_graph"}',
        "",
        "FROM: only integer indexes of previous operations visible on the same path.",
        "FORBIDDEN: input/output; the compiler derives unique registry ports.",
        'VALID: {"route":"<consumer-route>","from":[0]}',
        'INVALID: {"route":"<route>","from":[0],"input":"primary"}',
        "",
        "BARRIER: body contains every operation that requires approval.",
        "FORBIDDEN: outcomes or continuations; body means approved and the compiler creates an empty rejection.",
        'VALID: {"barrier":"<registry-barrier>","body":[{"route":"<route>"}]}',
        'INVALID: {"barrier":"<barrier>","outcome":"approved","body":[]}',
        "",
        "OPERATION REGISTRY (exact identifiers; descriptions are language-tagged authority data):",
    ]
    for route, metadata in sorted(registry["operations"].items()):
        descriptions = metadata.get("descriptions", [])
        suffix: list[str] = []
        seen_languages: set[str] = set()
        for item in descriptions:
            languages = item.get("languages")
            if type(languages) is not dict:
                continue
            for tag in sorted(languages):
                if tag in seen_languages:
                    continue
                text = _compact_description(item, tag)
                if text:
                    suffix.append(f"[{tag}] {text}")
                    seen_languages.add(tag)
        lines.append(f"- {route}" + (" | " + " | ".join(suffix) if suffix else ""))
    lines.append("SYSTEM CONTROL REGISTRY:")
    for name in sorted(registry["system_controls"]):
        lines.append(f"- {name}")
    lines.append("BARRIER REGISTRY:")
    for name in sorted(registry["barriers"]):
        lines.append(f"- {name} | body=approved | rejected=empty")
    lines.append("UNREPRESENTABLE REASONS:")
    for reason, description in sorted(registry["unrepresentable_reasons"].items()):
        lines.append(f"- {reason} | {description}")
    return "\n".join(lines) + "\n"


def contract_hashes(registry: dict[str, Any]) -> dict[str, str]:
    schema = build_schema(registry)
    prompt = build_prompt(registry)
    return {
        "contract_version": CONTRACT_VERSION,
        "registry_sha256": projection_sha256(registry),
        "schema_sha256": sha256(pretty_json_bytes(schema)).hexdigest(),
        "schema_semantic_sha256": sha256(canonical_json_bytes(schema)).hexdigest(),
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
    }
