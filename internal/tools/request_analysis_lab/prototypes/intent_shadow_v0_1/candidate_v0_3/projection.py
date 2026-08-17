"""Prompt-only projection overlay; the IR schema and registry remain v0.2."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from ..candidate_v0_2.canonical import canonical_json_bytes, pretty_json_bytes
from ..candidate_v0_2_1.language_tag import normalize_language_tag
from ..candidate_v0_2.projection import (
    LANGUAGE_TAG_PLACEHOLDER,
    SCHEMA_ID,
    SCHEMA_NAME,
    _compact_description,
    build_schema,
)
from ..candidate_v0_2.registry_projection import (
    CONTRACT_VERSION,
    projection_sha256,
    validate_projection,
)


OVERLAY_VERSION = "metnos.intent-ir-prompt-overlay/0.3"


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
        "CHOOSE ROOT FIRST: select exactly one root that matches the request, then emit it as one JSON object with kind first.",
        "EQUIVALENT ROOT TEMPLATES:",
        '- {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
        '- {"kind":"system_control","control":"<registry-control>"}',
        '- {"kind":"unrepresentable","reason":"<registry-reason>"}',
        "FORBIDDEN: explanations, extra keys, ports, path, ordinal, outcome, continuation, or free inputs.",
        "OPERATIONS: emit only operations required by the request; never enumerate the registry or alternatives.",
        "OPERATION ORDINAL 0 MUST OMIT FROM: it has no previous operation.",
        "FROM: omit from unless an operation has a real data dependency; each index must identify a strictly previous operation visible on the same path.",
        "A STEP MUST NEVER REFERENCE ITS OWN ORDINAL OR A LATER ORDINAL.",
        'VALID COMPLETE GRAPH: {"kind":"operation_graph","steps":[{"route":"<source-route>"},{"route":"<consumer-route>","from":[0]}]}',
        "FORBIDDEN: input/output; the compiler derives unique registry ports.",
        "BARRIER: body contains every operation that requires approval.",
        "FORBIDDEN: outcomes or continuations; body means approved and the compiler creates an empty rejection.",
        'VALID BARRIER: {"barrier":"<registry-barrier>","body":[{"route":"<route>"}]}',
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
    lines.extend([
        "FINAL MINI-CHECK:",
        "- Exactly one root was selected before writing details.",
        "- An operation graph contains only necessary operations; step 0 omits from.",
        "- Every from denotes a real dependency on a strictly previous visible operation.",
        "- No registry enumeration, alternative plan, forbidden field, or explanatory text is present.",
    ])
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
