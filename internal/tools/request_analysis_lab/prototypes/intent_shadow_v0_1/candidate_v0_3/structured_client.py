"""Structured request builder for the v0.3 prompt-only overlay."""
from __future__ import annotations

from typing import Any

from ..candidate_v0_2_1.language_tag import normalize_language_tag
from ..candidate_v0_2.registry_projection import validate_projection
from .projection import SCHEMA_NAME, build_prompt, build_schema


def build_request(query: str, registry: dict[str, Any], language_tag: str) -> dict[str, Any]:
    """Build the same strict request as v0.2 with only the prompt replaced."""
    if type(query) is not str or not query.strip():
        raise ValueError("query must be a nonempty string")
    try:
        query.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("query contains an invalid Unicode scalar") from exc
    normalized_language = normalize_language_tag(language_tag)
    validate_projection(registry)
    request = {
        "messages": [
            {"role": "system", "content": build_prompt(registry, normalized_language)},
            {"role": "user", "content": query},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": SCHEMA_NAME,
                "strict": True,
                "schema": build_schema(registry),
            },
        },
        "temperature": 0,
    }
    if "grammar" in request or "tools" in request:
        raise AssertionError("structured request must not mix grammar or tools")
    return request
