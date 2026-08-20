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
import hashlib
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("metnos.llm")

# Extra observers: each called with the call record dict. Must never raise.
_sinks: list[Callable[[dict], None]] = []
_current_tier: ContextVar[str | None] = ContextVar(
    "metnos_llm_tier", default=None)
_current_attempt: ContextVar[dict[str, str] | None] = ContextVar(
    "metnos_llm_attempt", default=None)
_current_attempt_sink: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "metnos_llm_attempt_sink", default=None)


def _bounded_counter(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, 10**12)


@dataclass(slots=True)
class BoundedUsageSink:
    """Content-free attempt usage buffer, persisted by the execution bridge."""

    max_records: int = 64
    _records: list[dict] = field(default_factory=list, init=False)
    _dropped: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_records, bool)
            or not isinstance(self.max_records, int)
            or not 1 <= self.max_records <= 256
        ):
            raise ValueError("max_records must be an integer in 1..256")

    def __call__(self, record: dict) -> None:
        with self._lock:
            if len(self._records) >= self.max_records:
                self._dropped += 1
                return
            self._records.append(dict(record))

    def summary(self) -> dict:
        with self._lock:
            records = [dict(item) for item in self._records]
            dropped = self._dropped
        return {
            "schema_version": "metnos.durable-llm-usage/1",
            "records": records,
            "dropped": dropped,
            "usage_missing": (
                not records
                or any(
                    item.get("in_tokens") is None
                    or item.get("out_tokens") is None
                    for item in records
                )
            ),
        }


def add_sink(fn: Callable[[dict], None]) -> None:
    """Register an additional observer ``fn(record_dict)`` (metrics/cost/…)."""
    _sinks.append(fn)


@contextmanager
def tier_context(tier: str | None):
    """Attach the logical tier to provider-level telemetry for one call.

    Providers deliberately know only their physical model.  Router/gateway
    code establishes this context so the shared telemetry hook can retain the
    logical binding without adding a non-portable ``tier`` argument to every
    provider implementation.
    """

    token = _current_tier.set(str(tier) if tier else None)
    try:
        yield
    finally:
        _current_tier.reset(token)


@contextmanager
def attempt_context(
        *, workload_id: str, stage_id: str, unit_key: str, attempt_id: str,
        sink: Callable[[dict], None]):
    """Attach bounded durable identities and a fail-soft usage sink."""

    values = {
        "workload_id": workload_id,
        "stage_id": stage_id,
        "unit_key": unit_key,
        "attempt_id": attempt_id,
    }
    if any(
        not isinstance(value, str) or not value or len(value) > 256
        for value in values.values()
    ):
        raise ValueError("LLM attempt context identities are invalid")
    if not callable(sink):
        raise TypeError("LLM attempt sink must be callable")
    context_token = _current_attempt.set(values)
    sink_token = _current_attempt_sink.set(sink)
    try:
        yield
    finally:
        _current_attempt_sink.reset(sink_token)
        _current_attempt.reset(context_token)


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
        attempt = _current_attempt.get()
        attempt_sink = _current_attempt_sink.get()
        if _sinks or (attempt is not None and attempt_sink is not None):
            rec = {
                "provider": provider, "model": model,
                "tier": tier if tier is not None else _current_tier.get(),
                "kind": kind,
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
            if attempt is not None and attempt_sink is not None:
                model_text = str(model or "")[:256]
                usage = {
                    "schema_version": "metnos.durable-llm-call/1",
                    **attempt,
                    "provider": str(provider or "")[:64],
                    "model_digest": (
                        "sha256:" + hashlib.sha256(model_text.encode("utf-8")).hexdigest()
                    ),
                    "tier": str(
                        tier if tier is not None else _current_tier.get() or ""
                    )[:32],
                    "kind": str(kind or "")[:32],
                    "in_tokens": _bounded_counter(getattr(result, "in_tokens", None)),
                    "out_tokens": _bounded_counter(getattr(result, "out_tokens", None)),
                    "latency_ms": _bounded_counter(getattr(result, "latency_ms", None)),
                }
                try:
                    attempt_sink(usage)
                except Exception:  # noqa: BLE001 — telemetry never breaks a call
                    pass
    except Exception:  # noqa: BLE001 — telemetry must never break an LLM call
        pass
