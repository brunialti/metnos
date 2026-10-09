# SPDX-License-Identifier: MIT
"""Aggregazioni pure per il Monitor, senza query, orologi o configurazione.

Il chiamante passa tutte le righe già filtrate: richieste intere, chiamate del
modello selezionato. Il periodo limita i grafici, non i riepiloghi delle righe
passate. Un bucket attribuisce la misura all'istante di avvio, senza distribuire
token o durate su intervalli che il provider non ha osservato.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from monitor_metrics import _number, percentile, rate, token_total

MAX_BUCKETS = 500
MAX_SERIES = 12
MAX_TIMELINE_CALLS = 1024
_BUCKET_SECONDS = (1, 5, 10, 30, 60, 120, 300, 600, 900, 1800,
                   3600, 7200, 21600, 86400)
_CALL_METRICS = ("input_tokens", "output_tokens", "prefill_tps",
                 "generation_tps", "call_output_tps", "call_p50_ms", "call_p95_ms", "coverage_pct")


def _instant(value: Any) -> datetime | None:
    try:
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(
            value.replace("Z", "+00:00"))
        return instant.astimezone(timezone.utc) if instant.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def build_timeline(calls: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Posiziona solo intervalli osservati, conservando le sovrapposizioni.

    Le durate monotone delle chiamate restano nelle metriche: non diventano
    una posizione UTC quando manca la fine o l'orologio torna indietro.
    """
    observed = []
    total = observed_count = 0
    first = last = None
    for call in calls:
        total += 1
        start, end = _instant(call.get("started_at")), _instant(call.get("finished_at"))
        if start is None or end is None or end <= start:
            continue
        observed_count += 1
        first = min(first, start) if first else start
        last = max(last, end) if last else end
        if len(observed) < MAX_TIMELINE_CALLS:
            observed.append((start, end, call))
    observed.sort(key=lambda item: (item[0], item[1]))
    duration_ms = (last - first).total_seconds() * 1000 if first is not None else None
    items = []
    for start, end, call in observed[:MAX_TIMELINE_CALLS]:
        start_ms = (start - first).total_seconds() * 1000
        call_ms = (end - start).total_seconds() * 1000
        items.append({"call_id": call.get("call_id"), "provider": call.get("provider"),
                      "level": call.get("level"),
                      "physical_model": (call.get("physical_model_reported") or
                                         call.get("physical_model_requested")),
                      "started_at": start.isoformat(), "finished_at": end.isoformat(),
                      "duration_ms": call_ms, "start_pct": 100 * start_ms / duration_ms,
                      "width_pct": 100 * call_ms / duration_ms})
    return {"items": items, "started_at": first.isoformat() if first else None,
            "finished_at": last.isoformat() if last else None, "duration_ms": duration_ms,
            "observed_calls": observed_count, "total_calls": total,
            "omitted_calls": total - len(items), "truncated": observed_count > MAX_TIMELINE_CALLS}


def _cell(value: Any, known: int, total: int, **extra: Any) -> dict[str, Any]:
    return {"value": value, "known": known, "total": total, **extra}


def _counter(raw: Mapping[str, Any]) -> dict[str, Any]:
    known = raw.get("known_calls", 0)
    expected = raw.get("total_calls") if raw.get("total_calls_known", True) else None
    complete = bool(raw.get("complete")) and expected is not None and known == expected
    value = raw.get("value") if known or (complete and expected == 0) else None
    return {"value": value, "known": known, "expected": expected, "complete": complete}


def _sum_counters(counters: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(counters)
    known = sum(row["known"] for row in rows)
    expected = (sum(row["expected"] for row in rows)
                if all(row["expected"] is not None for row in rows) else None)
    complete = all(row["complete"] for row in rows)
    value = sum(row["value"] or 0 for row in rows) if known or complete else None
    return {"value": value, "known": known, "expected": expected, "complete": complete}


def _rate_cell(pairs: Iterable[tuple[Any, Any]], total: int) -> dict[str, Any]:
    numerator = 0
    denominator = 0.0
    known = 0
    for tokens, duration in pairs:
        if rate(tokens, duration) is not None:
            numerator += tokens
            denominator += duration
            known += 1
    return _cell(rate(numerator, denominator), known, total,
                 numerator=numerator, denominator_ms=denominator)


def _call_metrics(calls: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    total = len(calls)
    result = {}
    for name in ("input_tokens", "output_tokens"):
        counter = token_total(calls, name)
        known = counter["known_calls"]
        result[name] = _cell(counter["value"] if known else None, known, total,
                             complete=counter["complete"])
    for phase in ("prefill", "generation"):
        result[f"{phase}_tps"] = _rate_cell(
            ((row.get(f"{phase}_tokens"), row.get(f"{phase}_ms")) for row in calls), total)
    result["call_output_tps"] = _rate_cell(
        ((row.get("output_tokens"), row.get("call_wall_ms")) for row in calls), total)
    durations = [duration for row in calls
                 if (duration := _number(row.get("call_wall_ms"))) is not None]
    for name, fraction in (("call_p50_ms", .5), ("call_p95_ms", .95)):
        result[name] = _cell(percentile(durations, fraction), len(durations), total)
    known = _complete_calls(calls)
    result["coverage_pct"] = _cell(100 * known / total if total else None, known, total)
    return result


def _complete_request(row: Mapping[str, Any]) -> bool:
    coverage = row.get("coverage", {})
    counters = row.get("metrics", {}).get("tokens", {})
    return (row.get("status") == "completed" and bool(coverage.get("chain_complete")) and
            bool(coverage.get("calls_complete")) and
            all(_counter(counters.get(field, {}))["complete"]
                for field in ("input_tokens", "output_tokens")) and
            _number(row.get("e2e_active_ms")) is not None and
            _number(row.get("e2e_wall_ms")) is not None)


def _request_metrics(requests: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    total = len(requests)
    # La velocità e2e esclude richieste non finali o con numeratore incompleto.
    pairs = []
    for row in requests:
        metrics = row.get("metrics", {})
        counter = _counter(metrics.get("tokens", {}).get("output_tokens", {}))
        if row.get("status") == "completed" and counter["complete"] and (
            counter["expected"] or 0) > 0 and metrics.get("request_output_tps") is not None:
            pairs.append((counter["value"], row.get("e2e_wall_ms")))
    result = {"request_output_tps": _rate_cell(pairs, total)}
    durations = [duration for row in requests
                 if (duration := _number(row.get("e2e_wall_ms"))) is not None]
    for name, fraction in (("e2e_p50_ms", .5), ("e2e_p95_ms", .95)):
        result[name] = _cell(percentile(durations, fraction), len(durations), total)
    complete = sum(_complete_request(row) for row in requests)
    result["coverage_pct"] = _cell(100 * complete / total if total else None, complete, total)
    drops = [count for row in requests if (count := _number(
        row.get("coverage", {}).get("dropped_events"), integer=True)) is not None]
    result["dropped_events"] = _cell(sum(drops) if drops else None, len(drops), total)
    return result


def adapt_request(row: Mapping[str, Any],
                  calls: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Adatta una richiesta intera; le chiamate arricchiscono le sue identità.

    Un filtro sulle chiamate non deve cancellare gli altri modelli dalla riga.
    Una tripletta priva di metadati conserva la chiave, senza dedurne il nome.
    """
    metrics = row.get("metrics", {})
    coverage = row.get("coverage", {})
    observed = {}
    for call in calls:
        identity = _identity(call)
        observed.setdefault(identity["key"], identity)
    models = []
    for raw in row.get("models", ()):
        if isinstance(raw, Mapping):
            identity = _identity(raw)
        elif isinstance(raw, (list, tuple)) and len(raw) == 3:
            identity = _identity(dict(zip(("provider", "level", "physical_model_key"), raw)))
        else:
            continue
        models.append(observed.get(identity["key"], identity))
    if not models:
        models = list(observed.values())
    return {**row,
            "models": models,
            "input_tokens": _counter(metrics.get("tokens", {}).get("input_tokens", {})),
            "output_tokens": _counter(metrics.get("tokens", {}).get("output_tokens", {})),
            "active_ms": row.get("e2e_active_ms"),
            "calls_complete": coverage.get("calls_observed", 0),
            "calls_expected": (coverage.get("calls_expected")
                               if coverage.get("chain_complete") else None),
            "request_output_tps": metrics.get("request_output_tps"),
            "prefill_tps": metrics.get("prefill", {}).get("tps"),
            "generation_tps": metrics.get("generation", {}).get("tps")}


def _identity(row: Mapping[str, Any]) -> dict[str, Any]:
    identity = {name: row.get(name) for name in (
        "provider", "level", "physical_model_key", "physical_model_requested",
        "physical_model_reported", "physical_model_attested")}
    identity["key"] = json.dumps(
        [identity["provider"], identity["level"], identity["physical_model_key"]],
        ensure_ascii=False, separators=(",", ":"))
    identity["identity_source"] = ("reported" if identity["physical_model_attested"] else
                                   "configured" if identity["physical_model_requested"] else "unavailable")
    return identity


def _complete_calls(calls: Iterable[Mapping[str, Any]]) -> int:
    return sum(_number(row.get("input_tokens"), integer=True) is not None and
               _number(row.get("output_tokens"), integer=True) is not None and
               _number(row.get("call_wall_ms")) is not None for row in calls)


def _model_item(identity: Mapping[str, Any], calls: list[Mapping[str, Any]],
                metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    item = {**identity, "calls": len(calls),
            "errors": sum(row.get("status") in {"error", "timeout", "cancelled"} for row in calls),
            "coverage": {"known": _complete_calls(calls), "expected": len(calls)},
            "physical_models_requested": sorted({row["physical_model_requested"]
                for row in calls if row.get("physical_model_requested")}),
            "binding_revisions": sorted({row["binding_revision"]
                for row in calls if row.get("binding_revision")})}
    for name, cell in metrics.items():
        item[name] = ({"value": cell["value"], "known": cell["known"],
                       "expected": cell["total"], "complete": cell["known"] == cell["total"]}
                      if name in {"input_tokens", "output_tokens"} else cell["value"])
    return item


def build_view(
    requests: Iterable[Mapping[str, Any]], calls: Iterable[Mapping[str, Any]],
    from_at: datetime | str, to_at: datetime | str,
    series_keys: Iterable[str] | None = None,
    *, model_grouping: str = "identity",
) -> dict[str, Any]:
    """Riepilogo completo, righe adattate e grafici con <=500 bucket/12 serie.

    Periodo UTC [from_at,to_at). Serie: JSON compatto [provider,level,key fisico],
    oppure [provider,key fisico] nella vista aggregata. I livelli restano nei
    metadati e i rapporti sono ricalcolati sulle chiamate, non sulle serie.
    Le altre identità sono elencate senza dati in available_series e non entrano
    in una serie sintetica. Percentili al rango più vicino, come nella specifica.
    Nessun effetto sui record passati; nessuna dipendenza dal tempo corrente.
    """
    start, end = _instant(from_at), _instant(to_at)
    if start is None or end is None or start >= end:
        raise ValueError("invalid_monitor_period")
    if isinstance(series_keys, (str, bytes)):
        raise ValueError("invalid_monitor_series")
    if model_grouping not in {"identity", "physical"}:
        raise ValueError("invalid_monitor_grouping")
    requests, calls = list(requests), list(calls)
    calls_by_request: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for call in calls:
        if call.get("request_id") is not None:
            calls_by_request[call["request_id"]].append(call)
    items = [adapt_request(row, calls_by_request.get(row.get("request_id"), ()))
             for row in requests]
    call_metrics, request_metrics = _call_metrics(calls), _request_metrics(requests)
    incomplete = sum(not _complete_request(row) for row in requests)
    summary = {"request_count": len(requests),
               "completed_requests": sum(row.get("status") == "completed" for row in requests),
               "call_count": len(calls), "incomplete_requests": incomplete,
               "dropped_events": sum(row.get("coverage", {}).get("dropped_events", 0) or 0
                                     for row in requests),
               "unattributed_calls": sum(row.get("attribution") == "unattributed" or
                                          ("request_id" in row and row["request_id"] is None)
                                          for row in calls),
               "request_output_tps": request_metrics["request_output_tps"]["value"]}
    for name in ("input_tokens", "output_tokens"):
        summary[name] = _sum_counters(row[name] for row in items)
        cell = call_metrics[name]
        summary[f"call_{name}"] = {"value": cell["value"] if cell["known"] or calls else 0,
                                  "known": cell["known"], "expected": cell["total"],
                                  "complete": cell["known"] == cell["total"]}
    for name in ("prefill_tps", "generation_tps", "call_output_tps", "call_p50_ms", "call_p95_ms"):
        summary[name] = call_metrics[name]["value"]
    for prefix, field in (("active", "e2e_active_ms"), ("wall", "e2e_wall_ms")):
        for suffix, fraction in (("p50_ms", .5), ("p95_ms", .95)):
            summary[f"{prefix}_{suffix}"] = percentile((row.get(field) for row in requests), fraction)

    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    identities = {}
    for call in calls:
        identity = _identity(call)
        identity["model_grouping"] = model_grouping
        if model_grouping == "physical":
            identity["level"] = None
            identity["key"] = json.dumps(
                [identity["provider"], identity["physical_model_key"]],
                ensure_ascii=False, separators=(",", ":"))
        groups[identity["key"]].append(call)
        identities.setdefault(identity["key"], identity)
    if model_grouping == "physical":
        for key, identity in identities.items():
            identity["levels"] = sorted({row.get("level") for row in groups[key]},
                                        key=lambda value: (value is None, value or ""))
    available = [identities[key] for key in sorted(identities)]
    model_items = [_model_item(identity, groups[identity["key"]],
                               _call_metrics(groups[identity["key"]])) for identity in available]
    keys = (list(dict.fromkeys(series_keys)) if series_keys is not None else
            [identity["key"] for identity in available])
    requested = [key for key in keys if key in identities]
    selected = requested[:MAX_SERIES]

    seconds = (end - start).total_seconds()
    bucket_seconds = next((size for size in _BUCKET_SECONDS if seconds / size <= MAX_BUCKETS),
                          math.ceil(seconds / MAX_BUCKETS))
    bucket_count = math.ceil(seconds / bucket_seconds)
    request_buckets: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    call_buckets: dict[str, dict[int, list[Mapping[str, Any]]]] = {
        key: defaultdict(list) for key in selected}
    unbucketed_requests = unbucketed_calls = 0

    def bucket(row: Mapping[str, Any]) -> int | None:
        stamp = _instant(row.get("started_at"))
        return int((stamp - start).total_seconds() // bucket_seconds) if (
            stamp is not None and start <= stamp < end) else None

    for row in requests:
        index = bucket(row)
        if index is None:
            unbucketed_requests += 1
        else:
            request_buckets[index].append(row)
    for key, rows in groups.items():
        for row in rows:
            index = bucket(row)
            if index is None:
                unbucketed_calls += 1
            elif key in call_buckets:
                call_buckets[key][index].append(row)
    request_series = {name: [] for name in request_metrics}
    for index in range(bucket_count):
        for name, cell in _request_metrics(request_buckets.get(index, [])).items():
            request_series[name].append(cell)
    series = []
    for key in selected:
        metrics: dict[str, list[dict[str, Any]]] = {name: [] for name in _CALL_METRICS}
        for index in range(bucket_count):
            for name, cell in _call_metrics(call_buckets[key].get(index, [])).items():
                metrics[name].append(cell)
        series.append({**identities[key], "metrics": metrics})
    coverage = {"requests": {"known": len(requests) - incomplete, "total": len(requests)},
                "calls": {"known": _complete_calls(calls), "total": len(calls)},
                "unbucketed_requests": unbucketed_requests, "unbucketed_calls": unbucketed_calls}
    for name in ("prefill_tps", "generation_tps", "call_output_tps"):
        coverage[name] = {key: call_metrics[name][key] for key in ("known", "total")}
    coverage["request_output_tps"] = {key: request_metrics["request_output_tps"][key]
                                      for key in ("known", "total")}
    charts = {"model_grouping": model_grouping,
              "bucket_starts": [(start + timedelta(seconds=index * bucket_seconds))
                                .isoformat().replace("+00:00", "Z") for index in range(bucket_count)],
              "bucket_seconds": bucket_seconds, "series": series,
              "requests": {"metrics": request_series}, "available_series": available,
              "selected_series_keys": selected, "total_series": len(available),
              "truncated": len(requested) > MAX_SERIES, "omitted_series": len(available) - len(selected)}
    return {"model_grouping": model_grouping,
            "summary": summary, "items": items, "model_items": model_items,
            "charts": charts, "coverage": coverage}
