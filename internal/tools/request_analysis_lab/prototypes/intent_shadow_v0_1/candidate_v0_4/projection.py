"""Coverage-before-root prompt overlay; schema and IR core remain unchanged."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from ..candidate_v0_2.canonical import canonical_json_bytes, pretty_json_bytes
from ..candidate_v0_2_1.language_tag import normalize_language_tag
from ..candidate_v0_2.registry_projection import (
    CONTRACT_VERSION,
    projection_sha256,
    validate_projection,
)
from ..candidate_v0_3.projection import (
    LANGUAGE_TAG_PLACEHOLDER,
    SCHEMA_ID,
    SCHEMA_NAME,
    build_schema,
)


OVERLAY_VERSION = "metnos.intent-ir-prompt-overlay/0.4"


def _uniform_description(item: dict[str, Any], language: str) -> str | None:
    languages = item.get("languages")
    selected = languages.get(language) if type(languages) is dict else None
    if type(selected) is not dict:
        return None
    scope = selected.get("scope")
    if type(scope) is not str or not scope.strip():
        full = selected.get("full")
        if type(full) is not str or not full.strip():
            return None
        scope = full.split(". ", 1)[0].strip()[:360]
    negative = selected.get("not")
    if type(negative) is not str or not negative.strip():
        negative = "-"
    return f"SCOPE: {scope.strip()} NOT: {negative.strip()}"


def _authority_suffix(metadata: dict[str, Any]) -> str:
    result: list[str] = []
    seen_languages: set[str] = set()
    descriptions = metadata.get("descriptions", [])
    if type(descriptions) is not list:
        return ""
    for item in descriptions:
        languages = item.get("languages") if type(item) is dict else None
        if type(languages) is not dict:
            continue
        for tag in sorted(languages):
            if tag in seen_languages:
                continue
            text = _uniform_description(item, tag)
            if text:
                result.append(f"[{tag}] {text}")
                seen_languages.add(tag)
    return " | ".join(result)


def build_prompt(registry: dict[str, Any], language_tag: str | None = None) -> str:
    """Build one locale-neutral prompt from registry authority data only."""
    validate_projection(registry)
    selected_language = (
        LANGUAGE_TAG_PLACEHOLDER
        if language_tag is None
        else normalize_language_tag(language_tag)
    )
    lines = [
        f"INPUT_LANGUAGE_TAG: {selected_language}",
        "DECISION ORDER: CONTROL -> COVERAGE -> CHECK -> GRAPH -> EDGES -> OUTPUT",
        "1 CONTROL: Match an exact whole-request registry control. Emit exclusive system_control; stop. Otherwise continue.",
        "2 COVERAGE: Identify every indispensable capability and clause of the whole request. Map each to an exact registry route.",
        "3 CHECK: If any indispensable item is unmapped, emit whole-compound unrepresentable/outside_registry. Never partial, approximate, or omitted coverage.",
        "3 CHECK: Treat missing executor-time arguments as outside this IR when root and route are decidable; do not convert them to missing_required_information.",
        "3 CHECK: Apply every other unrepresentable reason by its registry meaning. Add no reason precedence.",
        "4 GRAPH: When coverage is complete, emit only indispensable operations. No alternatives or registry enumeration. Put approval-gated operations in a registry barrier body.",
        "5 EDGES: Add from only for real data consumption from a strictly previous visible operation. Step 0 omits from. Require source < current ordinal.",
        "6 OUTPUT: Select one root. Emit one strict JSON object with kind first. No explanation or extra key.",
        "ROOT TEMPLATES — EQUIVALENT:",
        '- {"kind":"operation_graph","steps":[{"route":"<route>"}]}',
        '- {"kind":"system_control","control":"<control>"}',
        '- {"kind":"unrepresentable","reason":"<reason>"}',
        "OUTPUT FORBIDS: ports, input, output, path, ordinal, outcome, continuation, free inputs.",
        "",
        "REGISTRY DATA — EXACT IDENTIFIERS; LANGUAGE-TAGGED SCOPE/NOT RECORDS:",
        "OPERATIONS:",
    ]
    for route, metadata in sorted(registry["operations"].items()):
        suffix = _authority_suffix(metadata)
        lines.append(f"- {route}" + (f" | {suffix}" if suffix else " | SCOPE: - NOT: -"))
    lines.append("SYSTEM CONTROLS:")
    for name, metadata in sorted(registry["system_controls"].items()):
        suffix = _authority_suffix(metadata)
        lines.append(f"- {name}" + (f" | {suffix}" if suffix else " | SCOPE: - NOT: -"))
    lines.append("BARRIERS:")
    for name, metadata in sorted(registry["barriers"].items()):
        suffix = _authority_suffix(metadata)
        prefix = f"- {name} | BODY: approved | EMPTY: rejected"
        lines.append(prefix + (f" | {suffix}" if suffix else " | SCOPE: - NOT: -"))
    lines.append("UNREPRESENTABLE REASONS:")
    for reason, description in sorted(registry["unrepresentable_reasons"].items()):
        lines.append(f"- {reason} | SCOPE: {description} NOT: -")
    return "\n".join(lines) + "\n"


def contract_hashes(registry: dict[str, Any]) -> dict[str, str]:
    schema = build_schema(registry)
    prompt = build_prompt(registry)
    return {
        "contract_version": CONTRACT_VERSION,
        "overlay_version": OVERLAY_VERSION,
        "registry_sha256": projection_sha256(registry),
        "schema_sha256": sha256(pretty_json_bytes(schema)).hexdigest(),
        "schema_semantic_sha256": sha256(canonical_json_bytes(schema)).hexdigest(),
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
    }
