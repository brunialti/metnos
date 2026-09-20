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
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from hashutil import sha256_prefixed

log = logging.getLogger("metnos.llm")

# Extra observers: each called with the call record dict. Must never raise.
_sinks: list[Callable[[dict], None]] = []
_current_tier: ContextVar[str | None] = ContextVar(
    "metnos_llm_tier", default=None)
_current_attempt: ContextVar[dict[str, str] | None] = ContextVar(
    "metnos_llm_attempt", default=None)
_current_attempt_sink: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "metnos_llm_attempt_sink", default=None)
_current_transport_sink: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "metnos_llm_transport_sink", default=None)

TRANSPORT_USAGE_KEY = "_metnos_model_usage_v2"
TRANSPORT_USAGE_SCHEMA_VERSION = "metnos.model-usage-transport/2"
_DURABLE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def _bounded_counter(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 10**12
    ):
        return None
    return value


def _durable_label(
    value: object,
    *,
    maximum: int,
    fallback: str,
    allow_empty: bool = False,
) -> str:
    text = str(value or "")[:maximum]
    if not text and allow_empty:
        return ""
    return text if _DURABLE_LABEL_RE.fullmatch(text) else fallback


@dataclass(slots=True)
class _BoundedUsageBuffer:
    """Shared, bounded call bookkeeping for local and transported usage."""

    max_records: int = 64
    _records: list[dict] = field(default_factory=list, init=False)
    _dropped: int = field(default=0, init=False)
    _calls_started: int = field(default=0, init=False)
    _verified_zero_calls: bool = field(default=False, init=False)
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
            accounted_calls = len(self._records) + self._dropped
            if self._calls_started <= accounted_calls:
                self._calls_started += 1
            if len(self._records) >= self.max_records:
                self._dropped += 1
                return
            self._records.append(dict(record))

    def mark_call_started(self) -> None:
        """Record a provider attempt before it can consume work."""

        with self._lock:
            if self._calls_started < 10**12:
                self._calls_started += 1

    def _snapshot(self) -> tuple[list[dict], int, int, bool]:
        with self._lock:
            return (
                [dict(item) for item in self._records],
                self._dropped,
                self._calls_started,
                self._verified_zero_calls,
            )


class BoundedUsageSink(_BoundedUsageBuffer):
    """Content-free attempt usage buffer, persisted by the execution bridge."""

    __slots__ = ()

    def ingest_transport(
        self,
        value: object,
        *,
        workload_id: str,
        stage_id: str,
        unit_key: str,
        attempt_id: str,
    ) -> None:
        """Attach one content-free child-process usage envelope to an attempt."""

        identities = {
            "workload_id": workload_id,
            "stage_id": stage_id,
            "unit_key": unit_key,
            "attempt_id": attempt_id,
        }
        if any(
            not isinstance(item, str) or not item or len(item) > 256
            for item in identities.values()
        ):
            raise ValueError("LLM attempt context identities are invalid")
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "records", "dropped", "calls_started",
        }:
            raise ValueError("model usage transport envelope is invalid")
        records = value.get("records")
        dropped = value.get("dropped")
        calls_started = value.get("calls_started")
        if (
            value.get("schema_version") != TRANSPORT_USAGE_SCHEMA_VERSION
            or not isinstance(records, list)
            or len(records) > 256
            or isinstance(dropped, bool)
            or not isinstance(dropped, int)
            or not 0 <= dropped <= 10**12
            or isinstance(calls_started, bool)
            or not isinstance(calls_started, int)
            or not 0 <= calls_started <= 10**12
            or calls_started < len(records) + dropped
        ):
            raise ValueError("model usage transport envelope is invalid")
        expected = {
            "schema_version", "provider", "model_digest", "tier", "kind",
            "in_tokens", "out_tokens", "latency_ms", "cost_micros",
        }
        if any(not isinstance(raw, dict) or set(raw) != expected for raw in records):
            raise ValueError("model usage transport record is invalid")
        with self._lock:
            if self._calls_started > 10**12 - calls_started:
                raise ValueError("model usage call counter overflows")
            self._calls_started += calls_started
            self._dropped += dropped
            if calls_started == 0:
                self._verified_zero_calls = True
            for raw in records:
                if len(self._records) >= self.max_records:
                    self._dropped += 1
                    continue
                self._records.append({**raw, **identities})

    def summary(self) -> dict:
        records, dropped, calls_started, verified_zero_calls = self._snapshot()
        unreported = max(0, calls_started - len(records) - dropped)
        dropped += unreported
        zero_calls_verified = verified_zero_calls and not records and dropped == 0
        usage_missing = (
            (not records and not zero_calls_verified)
            or dropped > 0
            or any(
                item.get("in_tokens") is None
                or item.get("out_tokens") is None
                for item in records
            )
        )
        cost_unknown = any(
            item.get("cost_micros") is None for item in records
        )
        input_tokens = sum(
            int(item["in_tokens"])
            for item in records
            if item.get("in_tokens") is not None
        )
        output_tokens = sum(
            int(item["out_tokens"])
            for item in records
            if item.get("out_tokens") is not None
        )
        cost_micros = sum(
            int(item["cost_micros"])
            for item in records
            if item.get("cost_micros") is not None
        )
        return {
            "schema_version": "metnos.durable-model-usage/2",
            "records": records,
            "dropped": dropped,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_micros": cost_micros,
            "usage_missing": usage_missing,
            "cost_unknown": cost_unknown,
            "zero_calls_verified": zero_calls_verified,
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


class BoundedTransportUsageSink(_BoundedUsageBuffer):
    """Bounded, content-free usage captured inside an executor process."""

    __slots__ = ()

    def export(self) -> dict:
        records, dropped, calls_started, _verified_zero_calls = self._snapshot()
        return {
            "schema_version": TRANSPORT_USAGE_SCHEMA_VERSION,
            "records": records,
            "dropped": dropped,
            "calls_started": calls_started,
        }


@contextmanager
def transport_usage_context(sink: Callable[[dict], None]):
    """Capture provider usage for the parent runtime, never prompt content."""

    if not callable(sink):
        raise TypeError("LLM transport sink must be callable")
    token = _current_transport_sink.set(sink)
    try:
        yield
    finally:
        _current_transport_sink.reset(token)


def mark_call_started() -> None:
    """Mark a model transport attempt without recording prompt content."""

    for sink in (_current_attempt_sink.get(), _current_transport_sink.get()):
        marker = getattr(sink, "mark_call_started", None)
        if callable(marker):
            try:
                marker()
            except Exception:
                pass


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
        transport_sink = _current_transport_sink.get()
        if _sinks or (attempt is not None and attempt_sink is not None) or transport_sink:
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
            if (attempt is not None and attempt_sink is not None) or transport_sink:
                model_text = str(model or "")[:256]
                provider_text = _durable_label(
                    provider, maximum=64, fallback="unknown",
                )
                reported_cost = _bounded_counter(
                    getattr(result, "cost_micros", None)
                )
                from llm_pricing import cost_policy

                if (
                    reported_cost is None
                    and cost_policy(provider_text, model_text) == "zero"
                ):
                    reported_cost = 0
                usage = {
                    "schema_version": "metnos.durable-llm-call/1",
                    "provider": provider_text,
                    "model_digest": sha256_prefixed(model_text),
                    "tier": _durable_label(
                        tier if tier is not None else _current_tier.get(),
                        maximum=32,
                        fallback="unknown",
                        allow_empty=True,
                    ),
                    "kind": _durable_label(
                        kind, maximum=32, fallback="unknown",
                    ),
                    "in_tokens": _bounded_counter(getattr(result, "in_tokens", None)),
                    "out_tokens": _bounded_counter(getattr(result, "out_tokens", None)),
                    "latency_ms": _bounded_counter(getattr(result, "latency_ms", None)),
                    "cost_micros": reported_cost,
                }
                if attempt is not None and attempt_sink is not None:
                    try:
                        attempt_sink({**usage, **attempt})
                    except Exception:  # noqa: BLE001 — telemetry never breaks a call
                        pass
                if transport_sink is not None:
                    try:
                        transport_sink(usage)
                    except Exception:  # noqa: BLE001 — telemetry never breaks a call
                        pass
    except Exception:  # noqa: BLE001 — telemetry must never break an LLM call
        pass
