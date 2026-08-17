"""Request builder that changes only the S0 system prompt."""
from __future__ import annotations

from typing import Any

from ..candidate_v0_3.structured_client import build_request as build_current_request
from .projection import build_prompt


def build_request(query: str, registry: dict[str, Any], language_tag: str) -> dict[str, Any]:
    request = build_current_request(query, registry, language_tag)
    request["messages"][0]["content"] = build_prompt(registry, language_tag)
    return request
