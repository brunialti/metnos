# SPDX-License-Identifier: AGPL-3.0-only
"""Universal LLM-call telemetry — a thin, PASS-THROUGH observability hook.

Every provider routes its ``(system, user, result)`` through ``record(...)``.
By design this NEVER mutates the prompt or the result (pure side-channel):
observability ONLY — logging, metrics, redaction. Cross-cutting CONTENT
injection (language, preambles) is deliberately NOT done here: it would break
routing determinism (§11) and llama-server prompt-prefix caching, and bloat
every call. The language/content belongs in the per-call / per-prompt layer.

Default behaviour: a gated prompt dump (env ``METNOS_LOG_PROMPTS=1``; OFF by
default → zero overhead in production). Extensible: register extra observers
via ``add_sink(fn)`` for metrics / cost accounting / secret redaction.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

log = logging.getLogger("metnos.llm")

# Extra observers: each called with the call record dict. Must never raise.
_sinks: list[Callable[[dict], None]] = []


def add_sink(fn: Callable[[dict], None]) -> None:
    """Register an additional observer ``fn(record_dict)`` (metrics/cost/…)."""
    _sinks.append(fn)


def record(*, provider: str, model: str | None = None, system: str = "",
           user: str = "", result: Any = None, kind: str = "chat",
           tier: str | None = None) -> None:
    """Observe ONE LLM call. Pure side-channel: never mutates, never raises."""
    try:
        text = getattr(result, "text", result)
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        if os.environ.get("METNOS_LOG_PROMPTS") == "1":
            log.info("CHAT[%s/%s] sys=%r | user=%r | -> %r",
                     provider, kind, (system or "")[:400],
                     (user or "")[:300], text[:200])
        if _sinks:
            rec = {
                "provider": provider, "model": model, "tier": tier, "kind": kind,
                "system": system, "user": user, "text": text,
                "in_tokens": getattr(result, "in_tokens", None),
                "out_tokens": getattr(result, "out_tokens", None),
                "latency_ms": getattr(result, "latency_ms", None),
            }
            for fn in _sinks:
                try:
                    fn(rec)
                except Exception:  # noqa: BLE001 — a sink must not break a call
                    pass
    except Exception:  # noqa: BLE001 — telemetry must never break an LLM call
        pass
