"""Bounded local classifier for F2 forms outside the conservative help gate."""

from __future__ import annotations

from types import SimpleNamespace


MODES = frozenset({"EXPLAIN", "ACT", "MIXED", "UNKNOWN"})
_MODE = SimpleNamespace(
    name="tutor_mode",
    execution_policy={
        "effect": "read_only",
        "parallelism_class": 0,
        "resource_class": "llm",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    },
)
def classify_mode(query: str, lang: str, *,
                  conversation_context: str = "") -> str:
    """Return a closed mode; any malformed/failing result is ``UNKNOWN``."""

    from executor_scheduler import invoke_scheduled
    from llm_helpers import call_llm
    from prompt_loader import get as get_prompt

    prompt = get_prompt("tutor_mode", lang)

    def _call() -> dict:
        try:
            text, _metadata = call_llm(
                {
                    "language": lang,
                    "user_query": query,
                    "conversation_context": conversation_context,
                },
                prompt,
                tier="fast",
                max_tokens=12,
                max_query_chars=4000,
            )
            return {"ok": True, "text": text}
        except Exception as exc:
            return {"ok": False, "error_type": type(exc).__name__}

    result = invoke_scheduled(_MODE, _call)
    if not isinstance(result, dict) or not result.get("ok"):
        return "UNKNOWN"
    value = str(result.get("text") or "").strip().upper()
    return value if value in MODES else "UNKNOWN"
