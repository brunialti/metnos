"""Grounded local-LLM composition for retrieved Tutor context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal


_INSUFFICIENT = "TUTOR_CONTEXT_INSUFFICIENT"
_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.I | re.S)
_FORBIDDEN_OUTPUT = ("<think", "</think", "INLINE_FORM:")
_COMPOSER = SimpleNamespace(
    name="tutor_compose",
    execution_policy={
        "effect": "read_only",
        "parallelism_class": 0,
        "resource_class": "llm",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    },
)


@dataclass(frozen=True, slots=True)
class Composition:
    """Typed composer outcome; evidence gaps are not infrastructure errors."""

    status: Literal["answer", "insufficient", "unavailable"]
    text: str = ""


def compose_answer(
        *, query: str, context: str, lang: str,
        source_ids: tuple[str, ...],
        conversation_context: str = "",
        delivery_channel: str = "http") -> Composition:
    """Compose without tools or authority; failures remain an honest absence."""

    from executor_scheduler import invoke_scheduled
    from llm_helpers import call_llm
    from prompt_loader import get as get_prompt

    prompt = get_prompt("tutor_compose", lang)
    payload = {
        "language": lang,
        "user_query": query,
        "conversation_context": conversation_context,
        "delivery_channel": delivery_channel,
        "retrieved_context": context,
        "source_ids": list(source_ids),
    }

    def _call() -> dict:
        try:
            text, metadata = call_llm(
                payload,
                prompt,
                tier="wise",
                # Broad catalog questions need room to represent every
                # admitted area while retaining a natural example when asked.
                max_tokens=1536,
                max_query_chars=30000,
                output_policy="public",
            )
            return {"ok": True, "text": text, "meta": metadata}
        except Exception as exc:
            return {"ok": False, "error_type": type(exc).__name__}

    result = invoke_scheduled(_COMPOSER, _call)
    if not isinstance(result, dict) or not result.get("ok"):
        return Composition("unavailable")
    text = _THINK_BLOCK.sub("", str(result.get("text") or "")).strip()
    if text == _INSUFFICIENT:
        return Composition("insufficient")
    if not text:
        return Composition("unavailable")
    if any(marker.casefold() in text.casefold() for marker in _FORBIDDEN_OUTPUT):
        return Composition("unavailable")
    return Composition("answer", text)
