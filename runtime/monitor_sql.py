# SPDX-License-Identifier: MIT
"""Statistiche Monitor in SQLite; solo risultati aggregati escono dal DB.

I parametri sono già validati da MonitorIndex.list_requests. Le SELECT non
hanno limiti sui campioni: finestre SQL per percentili esatti, SUM/AVG per
totali e medie. Solo i grafici sono limitati a 500 intervalli e 12 serie.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from monitor_views import MAX_BUCKETS, MAX_SERIES, _BUCKET_SECONDS, _identity


def _cell(value, known, total, **extra):
    return {"value": value, "known": known, "total": total, **extra}


def _rate(row, name):
    numerator, duration = row.get(name + "_tokens") or 0, row.get(name + "_ms") or 0
    return _cell(1000 * numerator / duration if duration else None,
                 row.get(name + "_known") or 0, row.get("total", 0),
                 numerator=numerator, denominator_ms=duration)


def _call_cells(row):
    total = row.get("total", 0)
    result = {name: _cell(row.get(name), row.get(name + "_known", 0), total,
                          complete=row.get(name + "_known", 0) == total)
              for name in ("input_tokens", "output_tokens")}
    result.update({name: _rate(row, name) for name in (
        "prefill_tps", "generation_tps", "call_output_tps")})
    result.update({name: _cell(row.get(name), row.get("duration_known", 0), total)
                   for name in ("call_mean_ms", "call_p50_ms", "call_p95_ms")})
    complete = row.get("complete", 0)
    result["coverage_pct"] = _cell(100 * complete / total if total else None, complete, total)
    return result


def _request_cells(row):
    total, complete = row.get("total", 0), row.get("complete", 0)
    return {"request_output_tps": _rate(row, "request_output_tps"),
            "e2e_p50_ms": _cell(row.get("wall_p50_ms"), row.get("wall_known", 0), total),
            "e2e_p95_ms": _cell(row.get("wall_p95_ms"), row.get("wall_known", 0), total),
            "coverage_pct": _cell(100 * complete / total if total else None, complete, total),
            "dropped_events": _cell(row.get("drops"), row.get("drops_known", 0), total)}


def _percentiles(field, rank, count, prefix):
    # Rango più vicino: ceil(n * p). I nulli sono ordinati dopo i valori.
    return (f"MAX(CASE WHEN {rank}=({count}+1)/2 THEN {field} END) AS {prefix}_p50_ms, "
            f"MAX(CASE WHEN {rank}=({count}*95+99)/100 THEN {field} END) AS {prefix}_p95_ms")


def _rate_columns(tokens, duration, name, condition="1"):
    valid = f"({condition}) AND {tokens} IS NOT NULL AND {duration}>0"
    return (f"COALESCE(SUM(CASE WHEN {valid} THEN {tokens} END),0) AS {name}_tokens, "
            f"COALESCE(SUM(CASE WHEN {valid} THEN {duration} END),0) AS {name}_ms, "
            f"COUNT(CASE WHEN {valid} THEN 1 END) AS {name}_known")


class StatisticalReader:
    def __init__(self, connection, filters, revision, *, model_grouping="identity"):
        self.connection, self.filters = connection, filters
        self.grouping = model_grouping
        self.start, self.end = (datetime.fromisoformat(filters[name]) for name in ("from", "to"))
        seconds = (self.end - self.start).total_seconds()
        self.bucket_seconds = next((size for size in _BUCKET_SECONDS if seconds / size <= MAX_BUCKETS),
                                   math.ceil(seconds / MAX_BUCKETS))
        self.bucket_count = math.ceil(seconds / self.bucket_seconds)
        self.params = {**filters, "revision": revision, "bucket_seconds": self.bucket_seconds}

    def _scope(self):
        model = {"provider": 0, "level": 1, "physical_model_key": 2}
        requests = ["r.started_at>=:from", "r.started_at<:to", "r.payload IS NOT NULL"]
        calls = ["c.started_at>=:from", "c.started_at<:to"]
        scope = [name for name in ("owner_id", "channel", "origin", "status") if name in self.filters]
        for name in scope:
            requests.append(f"r.{name}=:{name}")
            calls.append(f"r.{name}=:{name}")
        calls.append("r.payload IS NOT NULL" if scope else
                     "(r.payload IS NOT NULL OR json_extract(s.payload,'$.event')='unattributed')")
        selected = [name for name in model if name in self.filters]
        if selected:
            requests.append("EXISTS (SELECT 1 FROM json_each(r.payload,'$.models') m WHERE " +
                            " AND ".join(f"json_extract(m.value,'$[{model[name]}]')=:{name}"
                                         for name in selected) + ")")
            calls.extend(f"c.{name}=:{name}" for name in selected)
        return ("WITH latest AS (SELECT r.* FROM requests r WHERE r.revision="
                "(SELECT MAX(v.revision) FROM requests v WHERE v.request_id=r.request_id "
                "AND v.revision<=:revision)), "
                "scoped_requests AS (SELECT r.* FROM latest r WHERE " + " AND ".join(requests) + "), "
                "scoped_calls AS (SELECT c.*,s.request_id, "
                "json_extract(s.payload,'$.event')='unattributed' AS unattributed "
                "FROM calls c JOIN segments s ON s.segment_id=c.segment_id "
                "LEFT JOIN latest r ON r.request_id=s.request_id WHERE " + " AND ".join(calls) + ") ")

    def _rows(self, sql, **params):
        return [dict(row) for row in self.connection.execute(self._scope() + sql,
                                                            {**self.params, **params})]

    def _bucket(self, column):
        # strftime('%s') tronca le frazioni; julianday perde precisione ai bordi.
        # I timestamp persistiti sono UTC a sei decimali; aritmetica intera µs.
        def micros(value):
            return (f"(CAST(strftime('%s',substr({value},1,19)||'Z') AS INTEGER)*1000000 + "
                    f"CAST(substr({value},21,6) AS INTEGER))")
        return f"CAST(({micros(column)}-{micros(':from')})/(:bucket_seconds*1000000) AS INTEGER)"

    def _calls(self, selected, *, buckets=False):
        identity = "json_array(provider,level,physical_model_key)" if self.grouping == "identity" else (
            "json_array(provider,physical_model_key)")
        fields = ("input_tokens", "output_tokens", "call_wall_ms", "prefill_tokens", "prefill_ms",
                  "generation_tokens", "generation_ms", "status", "physical_model_requested",
                  "physical_model_reported", "physical_model_attested", "binding_revision")
        projections = ",".join(f"json_extract(payload,'$.{name}') AS {name}" for name in fields)
        rates = ",".join(_rate_columns(tokens, duration, name) for tokens, duration, name in (
            ("prefill_tokens", "prefill_ms", "prefill_tps"),
            ("generation_tokens", "generation_ms", "generation_tps"),
            ("output_tokens", "call_wall_ms", "call_output_tps")))
        groups = ("SELECT *,'bucket' AS scope,model_key AS series,bucket AS slot FROM analytical "
                  "WHERE NOT unattested_error AND model_key IN (SELECT value FROM json_each(:selected))"
                  if buckets else "SELECT *, 'all' AS scope,'' AS series,-1 AS slot FROM analytical UNION ALL "
                  "SELECT *,'model',model_key,-1 FROM analytical WHERE NOT unattested_error")
        return self._rows(
            ", projected AS (SELECT *," + projections + "," + identity + " AS model_key," +
            self._bucket("started_at") + " AS bucket FROM scoped_calls), "
            "analytical AS (SELECT *, (status IN ('error','timeout','cancelled') "
            "AND COALESCE(physical_model_reported,'')='') AS unattested_error FROM projected), "
            "grouped AS (" + groups + "), "
            "ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY scope,series,slot "
            "ORDER BY call_wall_ms IS NULL,call_wall_ms,call_id) AS duration_rank, "
            "COUNT(call_wall_ms) OVER (PARTITION BY scope,series,slot) AS duration_count, "
            "ROW_NUMBER() OVER (PARTITION BY scope,series,slot ORDER BY started_at,call_id) AS identity_rank "
            "FROM grouped) SELECT scope,series,slot,COUNT(*) AS total, "
            "COUNT(input_tokens) AS input_tokens_known,SUM(input_tokens) AS input_tokens, "
            "COUNT(output_tokens) AS output_tokens_known,SUM(output_tokens) AS output_tokens, "
            "COUNT(call_wall_ms) AS duration_known,AVG(call_wall_ms) AS call_mean_ms, " +
            _percentiles("call_wall_ms", "duration_rank", "duration_count", "call") + "," + rates + ","
            "SUM(input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND call_wall_ms IS NOT NULL) AS complete, "
            "SUM(status IN ('error','timeout','cancelled')) AS errors,SUM(unattributed) AS unattributed, "
            "SUM(unattested_error) AS unattested_errors, "
            "MAX(CASE WHEN identity_rank=1 THEN payload END) AS identity_payload, "
            "json_group_array(DISTINCT physical_model_requested) FILTER (WHERE scope='model' "
            "AND COALESCE(physical_model_requested,'')<>'') AS physical_models_requested, "
            "json_group_array(DISTINCT binding_revision) FILTER (WHERE scope='model' "
            "AND COALESCE(binding_revision,'')<>'') AS binding_revisions, "
            "json_group_array(DISTINCT level) FILTER (WHERE scope='model') AS levels "
            "FROM ranked GROUP BY scope,series,slot", selected=json.dumps(selected))

    def _requests(self):
        fields = {"wall": "e2e_wall_ms", "active": "e2e_active_ms", "drops": "coverage.dropped_events",
                  "chain_complete": "coverage.chain_complete", "calls_complete": "coverage.calls_complete",
                  "request_rate": "metrics.request_output_tps"}
        projections = [f"json_extract(payload,'$.{path}') AS {name}" for name, path in fields.items()]
        for name in ("input_tokens", "output_tokens"):
            path = f"metrics.tokens.{name}"
            projections.extend((f"json_extract(payload,'$.{path}.value') AS {name}",
                f"COALESCE(json_extract(payload,'$.{path}.known_calls'),0) AS {name}_known",
                f"CASE WHEN COALESCE(json_extract(payload,'$.{path}.total_calls_known'),1) "
                f"THEN json_extract(payload,'$.{path}.total_calls') END AS {name}_expected",
                f"json_extract(payload,'$.{path}.complete') AS {name}_raw_complete"))
        complete = " AND ".join(f"{name}_complete" for name in ("input_tokens", "output_tokens"))
        counter_columns = []
        for name in ("input_tokens", "output_tokens"):
            counter_columns.extend((f"SUM({name}) AS {name}", f"SUM({name}_known) AS {name}_known",
                f"CASE WHEN COUNT({name}_expected)=COUNT(*) THEN SUM({name}_expected) END AS {name}_expected",
                f"MIN({name}_complete) AS {name}_complete"))
        return self._rows(
            ", projected AS (SELECT *," + ",".join(projections) + "," + self._bucket("started_at") +
            " AS bucket FROM scoped_requests), counters AS (SELECT *," +
            ",".join(f"COALESCE({name}_raw_complete AND {name}_expected IS NOT NULL "
                     f"AND {name}_known={name}_expected,0) AS {name}_complete"
                     for name in ("input_tokens", "output_tokens")) + " FROM projected), "
            "grouped AS (SELECT *,-1 AS slot FROM counters UNION ALL SELECT *,bucket FROM counters), "
            "ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY slot ORDER BY wall IS NULL,wall,request_id) "
            "AS wall_rank,COUNT(wall) OVER (PARTITION BY slot) AS wall_count, "
            "ROW_NUMBER() OVER (PARTITION BY slot ORDER BY active IS NULL,active,request_id) AS active_rank, "
            "COUNT(active) OVER (PARTITION BY slot) AS active_count FROM grouped) "
            "SELECT slot,COUNT(*) AS total,SUM(status='completed') AS completed, "
            f"SUM(status='completed' AND chain_complete AND calls_complete AND {complete} "
            "AND active IS NOT NULL AND wall IS NOT NULL) AS complete, "
            "SUM(drops) AS drops,COUNT(drops) AS drops_known,COUNT(wall) AS wall_known," +
            _percentiles("wall", "wall_rank", "wall_count", "wall") + "," +
            _percentiles("active", "active_rank", "active_count", "active") + "," +
            _rate_columns("output_tokens", "wall", "request_output_tps",
                          "status='completed' AND output_tokens_complete AND "
                          "output_tokens_expected>0 AND request_rate IS NOT NULL") + "," +
            ",".join(counter_columns) + " FROM ranked GROUP BY slot")

    def options(self):
        result = {}
        for name, table in (("channel", "scoped_requests"), ("provider", "scoped_calls"),
                            ("level", "scoped_calls")):
            result[name] = [row["value"] for row in self._rows(
                f"SELECT DISTINCT {name} AS value FROM {table} WHERE COALESCE({name},'')<>'' ORDER BY value")]
        result["physical_models"] = self._rows(
            "SELECT DISTINCT physical_model_key AS value, "
            "COALESCE(NULLIF(json_extract(payload,'$.physical_model_reported'),''),"
            "NULLIF(json_extract(payload,'$.physical_model_requested'),''),physical_model_key) AS label "
            "FROM scoped_calls WHERE COALESCE(physical_model_key,'')<>'' ORDER BY value,label")
        return result

    def build(self, series_keys=None):
        # Il primo risultato contiene solo riepilogo e una riga per modello.
        rows = self._calls([])
        all_calls = next((row for row in rows if row["scope"] == "all"), {})
        models = {row["series"]: row for row in rows if row["scope"] == "model"}
        available, items = [], []
        for key, row in sorted(models.items()):
            identity = _identity(json.loads(row["identity_payload"]))
            identity.update(key=key, model_grouping=self.grouping)
            if self.grouping == "physical":
                identity.update(level=None, levels=sorted(json.loads(row["levels"]),
                                key=lambda value: (value is None, value or "")))
            available.append(identity)
            cells = _call_cells(row)
            item = {**identity, "calls": row["total"], "errors": row["errors"],
                    "coverage": {"known": row["complete"], "expected": row["total"]},
                    "input_tokens_mean": row["input_tokens"] / row["input_tokens_known"]
                        if row["input_tokens_known"] else None}
            for field in ("physical_models_requested", "binding_revisions"):
                item[field] = sorted(json.loads(row[field]))
            for name, cell in cells.items():
                item[name] = ({"value": cell["value"], "known": cell["known"], "expected": cell["total"],
                               "complete": cell["known"] == cell["total"]}
                              if name in {"input_tokens", "output_tokens"} else cell["value"])
            items.append(item)
        requested = [key for key in dict.fromkeys(series_keys if series_keys is not None else models)
                     if key in models]
        if series_keys is None:
            requested.sort()
        selected = requested[:MAX_SERIES]
        # Questa SELECT limita le serie, mai i campioni dei gruppi selezionati.
        bucket_calls = {(row["series"], row["slot"]): row for row in self._calls(selected, buckets=True)
                        if row["scope"] == "bucket"} if selected else {}
        request_rows = {row["slot"]: row for row in self._requests()}
        requests = request_rows.get(-1, {})
        call_cells, request_cells = _call_cells(all_calls), _request_cells(requests)
        summary = {"request_count": requests.get("total", 0), "completed_requests": requests.get("completed", 0),
                   "call_count": all_calls.get("total", 0),
                   "incomplete_requests": requests.get("total", 0) - requests.get("complete", 0),
                   "dropped_events": requests.get("drops") or 0,
                   "unattributed_calls": all_calls.get("unattributed") or 0,
                   "calls_without_attested_model_on_error": all_calls.get("unattested_errors") or 0,
                   "request_output_tps": request_cells["request_output_tps"]["value"]}
        for name in ("input_tokens", "output_tokens"):
            known = requests.get(name + "_known", 0)
            complete = bool(requests.get(name + "_complete", not requests))
            summary[name] = {"value": (requests.get(name) or 0) if known or complete else None,
                             "known": known, "expected": requests.get(name + "_expected", 0),
                             "complete": complete}
            cell = call_cells[name]
            summary["call_" + name] = {"value": cell["value"] if all_calls else 0,
                                       "known": cell["known"], "expected": cell["total"],
                                       "complete": cell["known"] == cell["total"]}
        for name in ("prefill_tps", "generation_tps", "call_output_tps", "call_mean_ms", "call_p50_ms", "call_p95_ms"):
            summary[name] = call_cells[name]["value"]
        for prefix in ("active", "wall"):
            for suffix in ("p50_ms", "p95_ms"):
                summary[prefix + "_" + suffix] = requests.get(prefix + "_" + suffix)
        series = []
        identities = {item["key"]: item for item in available}
        for key in selected:
            metrics = {name: [] for name in call_cells}
            for slot in range(self.bucket_count):
                for name, cell in _call_cells(bucket_calls.get((key, slot), {})).items():
                    if name in {"input_tokens", "output_tokens"} and cell["known"]:
                        cell["value"] /= cell["known"]
                    metrics[name].append(cell)
            series.append({**identities[key], "metrics": metrics})
        request_series = {name: [] for name in request_cells}
        for slot in range(self.bucket_count):
            for name, cell in _request_cells(request_rows.get(slot, {})).items():
                request_series[name].append(cell)
        coverage = {"requests": {"known": requests.get("complete", 0), "total": requests.get("total", 0)},
                    "calls": {"known": all_calls.get("complete", 0), "total": all_calls.get("total", 0)},
                    "unbucketed_requests": 0, "unbucketed_calls": 0}
        for name in ("prefill_tps", "generation_tps", "call_output_tps", "request_output_tps"):
            cell = request_cells[name] if name == "request_output_tps" else call_cells[name]
            coverage[name] = {field: cell[field] for field in ("known", "total")}
        return {"model_grouping": self.grouping, "summary": summary, "model_items": items, "coverage": coverage,
                "charts": {"model_grouping": self.grouping, "bucket_seconds": self.bucket_seconds,
                    "bucket_starts": [(self.start + timedelta(seconds=slot * self.bucket_seconds)).isoformat()
                                      .replace('+00:00', 'Z') for slot in range(self.bucket_count)],
                    "series": series, "requests": {"metrics": request_series}, "available_series": available,
                    "selected_series_keys": selected, "total_series": len(available),
                    "truncated": len(requested) > MAX_SERIES, "omitted_series": len(available) - len(selected)}}
