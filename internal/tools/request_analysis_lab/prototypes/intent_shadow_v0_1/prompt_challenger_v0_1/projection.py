"""Single, exact prompt delta over the frozen S0 current prompt."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from ..candidate_v0_2.canonical import canonical_json_bytes, pretty_json_bytes
from ..candidate_v0_2.registry_projection import projection_sha256, validate_projection
from ..candidate_v0_3.projection import (
    build_prompt as build_current_prompt,
    build_schema,
)


CHALLENGER_VERSION = "metnos.intent-prompt-challenger/0.1"
BASE_PROMPT_SHA256 = "3251da0495ca72a5e138f2ca9693eb3d128ff2e4648ccef474cb0a49d7288d5c"
CHALLENGER_PROMPT_SHA256 = "29cf1d9faba4977bf6bf641ab28cbbcc37d4f7f4a1483a9b4b9c290ebe909c5d"

BASE_ROOT_LINE = (
    "CHOOSE ROOT FIRST: select exactly one root that matches the request, "
    "then emit it as one JSON object with kind first."
)
COVERAGE_ROOT_LINES = (
    "COVERAGE BEFORE ROOT: identify every indispensable requested intent in the complete input; do not omit or approximate any.",
    "COVERAGE CHECK: if an understood indispensable intent lacks a reviewed registry entry, the whole request is unrepresentable with reason outside_registry.",
    "CHOOSE ROOT AFTER COVERAGE: apply the existing exclusive-root contract to the complete request, select exactly one matching root, then emit it as one JSON object with kind first.",
)
FINAL_CHECK_HEADER = "FINAL MINI-CHECK:"
FINAL_CHECK_COVERAGE_BULLET = (
    "- Every indispensable requested intent was accounted for before root selection; "
    "none was omitted or approximated."
)


def apply_challenger_delta(current_prompt: str) -> str:
    """Apply only the approved three-line replacement and one bullet insertion."""
    if type(current_prompt) is not str:
        raise TypeError("current_prompt must be a string")
    root_anchor = BASE_ROOT_LINE + "\n"
    check_anchor = FINAL_CHECK_HEADER + "\n"
    if current_prompt.count(root_anchor) != 1:
        raise ValueError("S0 root anchor must occur exactly once")
    if current_prompt.count(check_anchor) != 1:
        raise ValueError("S0 final-check anchor must occur exactly once")
    replacement = "\n".join(COVERAGE_ROOT_LINES) + "\n"
    first_pass = current_prompt.replace(root_anchor, replacement, 1)
    return first_pass.replace(
        check_anchor,
        check_anchor + FINAL_CHECK_COVERAGE_BULLET + "\n",
        1,
    )


def build_prompt(registry: dict[str, Any], language_tag: str | None = None) -> str:
    """Render the challenger through the same locale-neutral, registry-derived path as S0."""
    validate_projection(registry)
    return apply_challenger_delta(build_current_prompt(registry, language_tag))


def contract_hashes(registry: dict[str, Any]) -> dict[str, str]:
    validate_projection(registry)
    current = build_current_prompt(registry)
    challenger = apply_challenger_delta(current)
    schema = build_schema(registry)
    return {
        "challenger_version": CHALLENGER_VERSION,
        "registry_sha256": projection_sha256(registry),
        "schema_sha256": sha256(pretty_json_bytes(schema)).hexdigest(),
        "schema_semantic_sha256": sha256(canonical_json_bytes(schema)).hexdigest(),
        "source_prompt_sha256": sha256(current.encode("utf-8")).hexdigest(),
        "challenger_prompt_sha256": sha256(challenger.encode("utf-8")).hexdigest(),
    }
