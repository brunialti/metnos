# SPDX-License-Identifier: MIT
"""Formule pure del Monitor; nessun accesso a modelli, disco o configurazione."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cached_input_tokens",
    "prefill_tokens", "generation_tokens",
)
DURATION_FIELDS = ("call_wall_ms", "prefill_ms", "generation_ms")
METRIC_FIELDS = TOKEN_FIELDS + DURATION_FIELDS


def binding_revision(spec: Mapping[str, Any]) -> str:
    """Impronta della specifica risolta, una volta alla costruzione.

    Lista chiusa: indirizzi, credenziali e opzioni libere non entrano mai
    nell'impronta né negli eventi. Non legge configurazioni o pesi.
    """
    safe = {name: spec[name] for name in (
        "provider", "model", "temperature", "think", "reasoning_budget",
        "reasoning_effort", "max_tokens", "context_window",
    ) if name in spec and type(spec[name]) in (str, int, float, bool)
        and (type(spec[name]) is not float or math.isfinite(spec[name]))}
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if value < 0 or not math.isfinite(value):
            return None
    except OverflowError:
        return None
    if integer and not isinstance(value, int):
        return None
    return value


def normalize_metrics(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Accetta solo metriche in token/ms; conserva validità e provenienza."""
    result: dict[str, Any] = {}
    sources, reasons = {}, {}
    supplied_sources = _mapping(raw.get("metric_sources"))
    supplied_reasons = _mapping(raw.get("missing_reasons"))
    for field in METRIC_FIELDS:
        value = _number(raw.get(field), integer=field in TOKEN_FIELDS)
        result[field] = value
        if value is None:
            sources[field] = "unavailable"
            reasons[field] = (
                "missing" if raw.get(field) is None else "invalid_number"
            )
            # Motivi enumerati, mai testo libero del provider.
            if supplied_reasons.get(field) in {
                "missing", "invalid_number", "not_reported", "transport_error",
                "unsupported_provider", "incomplete_cache_usage",
            }:
                reasons[field] = supplied_reasons[field]
        else:
            source = supplied_sources.get(field)
            sources[field] = source if source in {"provider", "observer"} else (
                "observer" if field == "call_wall_ms" else "provider"
            )
    result["metric_sources"] = sources
    result["missing_reasons"] = reasons
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_provider_metrics(
    provider: str, response: Mapping[str, Any] | None, *, call_wall_ms: Any,
) -> dict[str, Any]:
    """Legge soltanto contatori attestati dalla risposta, mai i testi.

    OpenAI/llama.cpp: schema Chat Completions. Anthropic: input non in cache
    più cache creation/read, solo quando tutti i contatori sono dichiarati.
    Nessun tempo di fase viene stimato dalla durata del trasporto.
    """
    response = _mapping(response)
    usage = _mapping(response.get("usage"))
    raw: dict[str, Any] = {"call_wall_ms": call_wall_ms}
    if provider in {"openai", "llamacpp"}:
        raw.update(input_tokens=usage.get("prompt_tokens"),
                   output_tokens=usage.get("completion_tokens"))
        details = _mapping(usage.get("prompt_tokens_details"))
        raw["cached_input_tokens"] = details.get("cached_tokens")
        if provider == "llamacpp":
            timings = _mapping(response.get("timings"))
            for target, source in (
                ("prefill_tokens", "prompt_n"), ("prefill_ms", "prompt_ms"),
                ("generation_tokens", "predicted_n"),
                ("generation_ms", "predicted_ms"),
            ):
                raw[target] = timings.get(source)
            if "cache_n" in timings:
                raw["cached_input_tokens"] = timings["cache_n"]
    elif provider == "anthropic":
        raw["output_tokens"] = usage.get("output_tokens")
        raw["cached_input_tokens"] = usage.get("cache_read_input_tokens")
        parts = [_number(usage.get(name), integer=True) for name in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
        )]
        if all(part is not None for part in parts):
            raw["input_tokens"] = sum(parts)
        else:
            raw["missing_reasons"] = {"input_tokens": "incomplete_cache_usage"}
    else:
        raw["missing_reasons"] = {
            name: "unsupported_provider" for name in METRIC_FIELDS
            if name != "call_wall_ms"
        }
    return normalize_metrics(raw)


def rate(tokens: Any, duration_ms: Any) -> float | None:
    numerator = _number(tokens, integer=True)
    denominator = _number(duration_ms)
    if numerator is None or denominator is None or denominator == 0:
        return None
    value = 1000.0 * numerator / denominator
    return value if math.isfinite(value) else None


def call_rates(metrics: Mapping[str, Any]) -> dict[str, float | None]:
    metrics = normalize_metrics(metrics)
    durations = [metrics[name] for name in DURATION_FIELDS]
    residual = None
    if all(duration is not None for duration in durations):
        residual = durations[0] - durations[1] - durations[2]
        # Campi validi singolarmente possono avere confini incompatibili.
        if residual < 0:
            residual = None
    return {
        "prefill_tps": rate(metrics["prefill_tokens"], metrics["prefill_ms"]),
        "generation_tps": rate(metrics["generation_tokens"], metrics["generation_ms"]),
        "call_output_tps": rate(metrics["output_tokens"], metrics["call_wall_ms"]),
        "queue_transport_ms": residual,
    }


def token_total(
    calls: Iterable[Mapping[str, Any]], field: str, *, expected_calls: int | None = None,
) -> dict[str, Any]:
    """Una somma parziale è un limite inferiore, non un totale esatto."""
    if field not in TOKEN_FIELDS:
        raise ValueError("unknown_token_field")
    calls = list(calls)
    if expected_calls is not None and (
        _number(expected_calls, integer=True) is None or expected_calls < len(calls)
    ):
        raise ValueError("invalid_expected_calls")
    total = len(calls) if expected_calls is None else expected_calls
    values = [_number(call.get(field), integer=True) for call in calls]
    known = [value for value in values if value is not None]
    return {"value": sum(known), "known_calls": len(known), "total_calls": total,
            "complete": len(known) == total}


def phase_rate(calls: Iterable[Mapping[str, Any]], phase: str) -> dict[str, Any]:
    if phase not in {"prefill", "generation"}:
        raise ValueError("unknown_phase")
    calls = list(calls)
    tokens = duration = 0
    known = 0
    for call in calls:
        count = _number(call.get(f"{phase}_tokens"), integer=True)
        milliseconds = _number(call.get(f"{phase}_ms"))
        if count is not None and milliseconds is not None and milliseconds > 0:
            tokens += count
            duration += milliseconds
            known += 1
    return {"tps": rate(tokens, duration), "tokens": tokens, "duration_ms": duration,
            "known_calls": known, "total_calls": len(calls)}


def request_metrics(
    calls: Iterable[Mapping[str, Any]], *, e2e_wall_ms: Any,
    e2e_active_ms: Any, expected_calls: int | None = None, final: bool = True,
) -> dict[str, Any]:
    calls = list(calls)
    counts = {field: token_total(calls, field, expected_calls=expected_calls)
              for field in TOKEN_FIELDS}
    output = counts["output_tokens"]
    complete = final and output["complete"] and output["total_calls"] > 0
    return {
        "tokens": counts,
        "request_output_tps": rate(output["value"], e2e_wall_ms) if complete else None,
        "request_active_output_tps": (
            rate(output["value"], e2e_active_ms) if complete else None
        ),
        "prefill": phase_rate(calls, "prefill"),
        "generation": phase_rate(calls, "generation"),
    }


def percentile(values: Iterable[Any], fraction: float = 0.95) -> float | None:
    if not 0 < fraction <= 1:
        raise ValueError("invalid_percentile")
    valid = sorted(value for raw in values if (value := _number(raw)) is not None)
    return valid[math.ceil(fraction * len(valid)) - 1] if valid else None


def model_identity(
    provider: str, requested: str | None, reported: str | None,
) -> dict[str, Any]:
    """La chiave non dipende dal ruolo logico né dalla configurazione corrente."""
    physical = reported or requested
    attested = bool(reported)
    key = None
    if physical:
        encoded = json.dumps([provider, physical, "reported" if attested else "configured"],
                             ensure_ascii=False, separators=(",", ":"))
        key = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {"physical_model_requested": requested, "physical_model_reported": reported,
            "physical_model_key": key, "physical_model_attested": attested}
