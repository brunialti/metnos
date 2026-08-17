"""Arm B: direct extraction against the frozen intent-shadow model contract."""
from __future__ import annotations

from typing import Any

from intent_shadow_extract import envelope_json, extract_raw_json
from intent_shadow_registry import load_frozen_registry
from live_protocol import LIVE_LIMITS, MODEL_PROMPT_PATH, MODEL_SCHEMA_PATH, REGISTRY_PATH, openai_request


ARM_ID = "B"


def system_prompt() -> str:
    prompt = MODEL_PROMPT_PATH.read_text(encoding="utf-8")
    schema = MODEL_SCHEMA_PATH.read_text(encoding="utf-8")
    return prompt + "\nJSON Schema autoritativo (emetti un solo valore JSON):\n" + schema


def build_request(query: str, language: str) -> dict[str, Any]:
    if type(language) is not str:
        raise TypeError("language must be exact string")
    return openai_request(system_prompt(), query)


def extract_response(raw_content: bytes) -> dict[str, Any]:
    registry, _identity = load_frozen_registry(REGISTRY_PATH)
    return envelope_json(extract_raw_json(raw_content, registry, limits=LIVE_LIMITS))
