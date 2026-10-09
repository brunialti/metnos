# SPDX-License-Identifier: MIT
"""Lettura amministrativa Monitor; indice e aggregazioni fuori dal loop HTTP."""
from __future__ import annotations

import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from aiohttp import web

from http_render import _error, render_template, wants_html
from monitor_metrics import call_rates
from monitor_requests import derive_request
from monitor_store import MonitorIndex
from monitor_views import adapt_request, build_timeline, build_view

log = logging.getLogger(__name__)
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
READ_CAP = 10_000
FILTERS = ("channel", "origin", "status", "provider", "level", "physical_model_key", "owner_id")


def _iso(instant):
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _filters(query, *, now=None):
    now = now or datetime.now(timezone.utc)
    period = query.get("period", "custom" if query.get("from") or query.get("to") else "24h")
    if period == "custom":
        start, end = query.get("from"), query.get("to")
    elif period in {"today", "24h", "7d"}:
        start = _iso(now.replace(hour=0, minute=0, second=0, microsecond=0)
                     if period == "today" else now - timedelta(days=7 if period == "7d" else 1))
        end = _iso(now)
    else:
        raise ValueError("invalid_period")
    filters = {"from": start, "to": end}
    filters.update({name: query[name] for name in FILTERS if query.get(name)})
    return filters, period


def _options(source):
    import messages
    requests, calls = source["requests"], source["calls"]
    def choices(rows, name):
        return [{"value": value, "label": value} for value in sorted({
            row.get(name) for row in rows if row.get(name)})]
    return {"channels": choices(requests, "channel"),
            "origins": [{"value": name, "label": messages.get("UI_MONITOR_ORIGIN_" + name.upper())}
                        for name in ("interactive", "background", "test")],
            "statuses": [{"value": name, "label": messages.get("UI_MONITOR_STATUS_" + name.upper())} for name in (
                "completed", "in_progress", "awaiting_input", "interrupted", "expired", "unknown")],
            "providers": choices(calls, "provider"), "levels": choices(calls, "level"),
            "physical_models": [{"value": key, "label": label} for key, label in sorted({
                (row["physical_model_key"], row.get("physical_model_reported") or
                 row.get("physical_model_requested") or row["physical_model_key"])
                for row in calls if row.get("physical_model_key")})]}


def _read(query, view, request_id=None):
    filters, period = _filters(query)
    limit = int(query.get("limit", "50"))
    if not 1 <= limit <= 200:
        raise ValueError("invalid_page_limit")
    selected = None
    grouping = query.get("model_grouping", "identity")
    if grouping not in {"identity", "physical"}:
        raise ValueError("invalid_model_grouping")
    if "series_keys" in query:
        try:
            selected = json.loads(query["series_keys"])
        except (ValueError, TypeError):
            raise ValueError("invalid_series_selection") from None
        if (not isinstance(selected, list) or len(selected) > 12 or
                not all(isinstance(key, str) and len(key) <= 1024 for key in selected)):
            raise ValueError("invalid_series_selection")
    with MonitorIndex() as index:
        files = sorted(index.directory.glob("events-*.jsonl*"))
        ingest = index.ingest(files)
        source = index.aggregate_source(filters, cap=READ_CAP)
        page = index.list_requests(filters, limit=limit, cursor=query.get("cursor"))
        result = build_view(source["requests"], source["calls"], filters["from"], filters["to"],
                            series_keys=selected, model_grouping=grouping)
        result["filters"] = {**source["filters"], "period": period,
                             "origin": source["filters"].get("origin", ""),
                             "model_grouping": grouping}
        result["health"] = index.health()
        result["health"]["ingest"] = ingest
        result["health"]["read_limit"] = READ_CAP
        result["health"]["truncated"] = source["truncated"]
        result["latest_event_at"] = result["health"]["latest_event_at"]
        result["next_cursor"] = page["next_cursor"]
        result["revision"] = page["revision"]
        # Il cursore conserva la revisione: i dati correnti arricchiscono solo
        # i nomi dei modelli, senza sostituire stato o metriche della pagina.
        result["items"] = [adapt_request(row, source["calls"]) for row in page["items"]]
        if view == "models":
            result["items"] = result["model_items"]
            for field in ("input_tokens", "output_tokens"):
                result["summary"][field] = result["summary"][f"call_{field}"]
        if request_id:
            detail = index.get_request(request_id)
            if detail is None:
                return None
            result["request"] = build_view([detail], detail["calls"], filters["from"], filters["to"])["items"][0]
            result["calls"] = [{**call, **call_rates(call)} for call in detail["calls"]]
            result["timeline"] = build_timeline(detail["calls"])
            result["segments"] = [{**segment, "status": derive_request(
                segment["segment_id"], [segment], [], chain_complete=False,
                alive=index.alive)["status"],
                "turn_url": "/agent/turns/" + quote(segment["turn_id"], safe="")
                if segment.get("turn_id") else None} for segment in detail["segments"]]
        result["filter_options"] = _options(source)
        return result


async def _serve(request, *, data=False, export=False):
    import messages
    if request.get("role") != "admin":
        return _error(403, "monitor_admin_required", messages.get("UI_MONITOR_READ_ERROR"))
    view = request.query.get("view", "overview") if data or export else (
        "models" if request.path.endswith("/models") else
        "detail" if request.match_info.get("request_id") else
        "requests" if request.path.endswith("/requests") else "overview")
    if view not in {"overview", "requests", "models", "detail"}:
        return _error(400, "monitor_invalid_view", messages.get("UI_MONITOR_READ_ERROR"))
    request_id = request.match_info.get("request_id") or request.query.get("request_id")
    if view == "detail" and not request_id:
        return _error(400, "monitor_request_required", messages.get("UI_MONITOR_READ_ERROR"))
    try:
        payload = await asyncio.to_thread(_read, dict(request.query), view, request_id)
    except ValueError as exc:
        return _error(400, str(exc), messages.get("UI_MONITOR_READ_ERROR"))
    except Exception:
        log.exception("Monitor reader failed")
        return _error(503, "monitor_unavailable", messages.get("UI_MONITOR_READ_ERROR"))
    if payload is None:
        return _error(404, "monitor_request_not_found", messages.get("UI_MONITOR_NO_REQUESTS"))
    generated = _iso(datetime.now(timezone.utc))
    parameters = {**payload["filters"], "period": "custom", "limit": request.query.get("limit", "50")}
    page_path = "/admin/monitor/requests" if view == "requests" else "/admin/monitor"
    if request.query.get("series_keys"):
        parameters["series_keys"] = request.query["series_keys"]
    payload["next_url"] = (page_path + "?" + urlencode({**parameters, "cursor": payload["next_cursor"]})
                           if payload["next_cursor"] and view != "models" else None)
    payload["export_url"] = "/admin/monitor/export?" + urlencode({**parameters, "view": view,
        **({"cursor": request.query["cursor"]} if request.query.get("cursor") else {}),
        **({"request_id": request_id} if request_id else {})})
    context = {"view": view, "monitor": payload, "filter_options": payload.pop("filter_options")}
    if export:
        # Dettaglio e pagina esportano esattamente la vista consultata.
        exported = ({name: payload[name] for name in ("request", "calls", "segments", "timeline")}
                    if view == "detail" else
                    {"items": payload["items"], "next_cursor": payload["next_cursor"]})
        return web.json_response({"schema_version": "metnos.monitor/1", "generated_at": generated,
            "filters": payload["filters"], **exported, "coverage": payload["coverage"],
            "health": payload["health"]}, headers={**HEADERS,
            "Content-Disposition": 'attachment; filename="monitor.json"'})
    if data:
        payload["html"] = {"summary": render_template("_monitor_summary.html", **context)
                           if view != "detail" else "",
                           "requests": render_template("_monitor_table.html", **context)}
    elif wants_html(request):
        return web.Response(text=render_template("monitor.html", **context),
                            content_type="text/html", headers=HEADERS)
    return web.json_response({"schema_version": "metnos.monitor/1", "generated_at": generated, **payload},
                             headers=HEADERS)


async def monitor(request):
    return await _serve(request)


async def monitor_data(request):
    return await _serve(request, data=True)


async def monitor_export(request):
    return await _serve(request, export=True)


ROUTES = (
    ("GET", "/admin/monitor", monitor),
    ("GET", "/admin/monitor/data", monitor_data),
    ("GET", "/admin/monitor/requests", monitor),
    ("GET", "/admin/monitor/models", monitor),
    ("GET", "/admin/monitor/export", monitor_export),
    ("GET", "/admin/monitor/requests/{request_id}", monitor),
)
