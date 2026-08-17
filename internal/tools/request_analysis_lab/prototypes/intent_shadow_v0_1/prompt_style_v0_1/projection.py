"""Render three style variants from one canonical authority inventory."""
from __future__ import annotations

from enum import Enum
from hashlib import sha256
import re
from typing import Any

from ..candidate_v0_2.canonical import canonical_json_bytes, pretty_json_bytes
from ..candidate_v0_2.registry_projection import (
    CONTRACT_VERSION,
    projection_sha256,
    validate_projection,
)
from ..candidate_v0_3.projection import (
    LANGUAGE_TAG_PLACEHOLDER,
    SCHEMA_NAME,
    build_prompt as build_current_prompt,
    build_schema,
)
from ..candidate_v0_2_1.language_tag import normalize_language_tag
from .authority_inventory import RULES, ROOT_TEMPLATES, split_current_prompt, validate_inventory


PACKAGE_VERSION = "metnos.intent-prompt-style/0.1"


class PromptStyle(str, Enum):
    S0_CURRENT = "S0_CURRENT"
    S1_METNOS_SHORT = "S1_METNOS_SHORT"
    S2_PROCEDURAL = "S2_PROCEDURAL"


def _language(language_tag: str | None) -> str:
    return LANGUAGE_TAG_PLACEHOLDER if language_tag is None else normalize_language_tag(language_tag)


def _shared_tail(registry: dict[str, Any]) -> list[str]:
    _header, templates, data = split_current_prompt(registry)
    if templates != ROOT_TEMPLATES:
        raise ValueError("root template authority drift")
    return ["ROOT TEMPLATES:", *templates, "", *data]


def build_s1_prompt(registry: dict[str, Any], language_tag: str | None = None) -> str:
    validate_projection(registry)
    validate_inventory(registry)
    lines = [f"INPUT_LANGUAGE_TAG: {_language(language_tag)}", "CONSTRAINTS:"]
    lines.extend(f"{rule.label}: {rule.constraint}" for rule in RULES)
    lines.extend(_shared_tail(registry))
    return "\n".join(lines) + "\n"


def build_s2_prompt(registry: dict[str, Any], language_tag: str | None = None) -> str:
    validate_projection(registry)
    validate_inventory(registry)
    lines = [f"INPUT_LANGUAGE_TAG: {_language(language_tag)}", "PROCEDURE:"]
    lines.extend(
        f"{ordinal} {rule.label} -> {rule.constraint}"
        for ordinal, rule in enumerate(RULES, 1)
    )
    lines.extend(_shared_tail(registry))
    return "\n".join(lines) + "\n"


def build_prompt(style: PromptStyle, registry: dict[str, Any], language_tag: str | None = None) -> str:
    if type(style) is not PromptStyle:
        raise TypeError("style must be PromptStyle")
    if style is PromptStyle.S0_CURRENT:
        return build_current_prompt(registry, language_tag)
    renderers = {
        PromptStyle.S1_METNOS_SHORT: build_s1_prompt,
        PromptStyle.S2_PROCEDURAL: build_s2_prompt,
    }
    return renderers[style](registry, language_tag)


def prompt_metrics(prompt: str) -> dict[str, int]:
    return {
        "utf8_bytes": len(prompt.encode("utf-8")),
        "lines": len(prompt.splitlines()),
        "whitespace_tokens": len(re.findall(r"\S+", prompt)),
    }


def contract_hashes(registry: dict[str, Any]) -> dict[str, Any]:
    schema = build_schema(registry)
    prompts = {style.value: build_prompt(style, registry) for style in PromptStyle}
    return {
        "package_version": PACKAGE_VERSION,
        "contract_version": CONTRACT_VERSION,
        "registry_sha256": projection_sha256(registry),
        "schema_sha256": sha256(pretty_json_bytes(schema)).hexdigest(),
        "schema_semantic_sha256": sha256(canonical_json_bytes(schema)).hexdigest(),
        "prompt_sha256": {
            style: sha256(prompt.encode("utf-8")).hexdigest()
            for style, prompt in prompts.items()
        },
        "prompt_metrics": {style: prompt_metrics(prompt) for style, prompt in prompts.items()},
    }
