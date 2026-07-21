# SPDX-License-Identifier: AGPL-3.0-only
"""Composite, non-secret health surface for the local Metnos stack.

The ordinary ``/agent/health`` endpoint remains a cheap liveness probe.  This
admin-only endpoint is the readiness authority used by ``stack_reconcile``:
it observes in-flight turns, the Playwright broker and the content-derived
browser contract from the HTTP process and sidecar process at the same time.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from aiohttp import web

from http_app_state import CATALOG_PROVIDER, app_get
from playwright_sidecar import contract as _contract
from turn_events import TurnEventLog


SIDECAR_HEALTH_URL = "http://127.0.0.1:8771/health"


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


def _catalog_names(request: web.Request) -> list[str]:
    provider = app_get(request.app, CATALOG_PROVIDER)
    if not callable(provider):
        return []
    names: set[str] = set()
    for item in provider() or []:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", "")
        if isinstance(name, str) and name:
            names.add(name)
    return sorted(names)


def _probe_sidecar() -> dict:
    request = urllib.request.Request(
        SIDECAR_HEALTH_URL,
        headers={_contract.HEADER_NAME: _contract.LOADED_FINGERPRINT},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
            peer_header = response.headers.get(_contract.HEADER_NAME, "")
            status = int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(64 * 1024).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        return {
            "available": True,
            "ok": False,
            "http_status": exc.code,
            "error_class": payload.get("error_class", "sidecar_http_error"),
        }
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "available": False,
            "ok": False,
            "error_class": type(exc).__name__,
        }
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "available": True,
            "ok": False,
            "http_status": status,
            "error_class": type(exc).__name__,
        }

    broker = payload.get("broker") if isinstance(payload.get("broker"), dict) else {}
    loaded = payload.get("contract_loaded", "")
    current = payload.get("contract_current", "")
    aligned = bool(
        payload.get("contract_aligned")
        and _contract.same_fingerprint(loaded, current)
        and _contract.same_fingerprint(loaded, _contract.LOADED_FINGERPRINT)
        and _contract.same_fingerprint(peer_header, _contract.LOADED_FINGERPRINT)
    )
    return {
        "available": True,
        "ok": bool(status == 200 and payload.get("ok") and aligned),
        "http_status": status,
        "contract_loaded": loaded,
        "contract_current": current,
        "contract_aligned": aligned,
        "browser_connected": bool(broker.get("browser_connected")),
        "reaper_running": bool(broker.get("reaper_running")),
        "active_sessions": int(broker.get("active_sessions") or 0),
        "approval_pending_sessions": int(broker.get("approval_pending_sessions") or 0),
        "factor_pending_sessions": int(broker.get("factor_pending_sessions") or 0),
        "pending_opens": int(broker.get("pending_opens") or 0),
    }


async def stack_health(request: web.Request) -> web.Response:
    """GET /agent/stack/health — readiness evidence, restricted to admin."""
    if request.get("role", "anonymous") != "admin":
        return _error(403, "forbidden", "admin role required")

    local_contract = _contract.source_status()
    sidecar = await asyncio.to_thread(_probe_sidecar)
    turns = TurnEventLog.get().stats()
    catalog_names = _catalog_names(request)
    broker_quiescent = all(
        int(sidecar.get(key) or 0) == 0
        for key in (
            "active_sessions", "approval_pending_sessions",
            "factor_pending_sessions", "pending_opens",
        )
    )
    contract_aligned = bool(
        local_contract.get("contract_aligned")
        and sidecar.get("contract_aligned")
    )
    return web.json_response({
        "ok": True,
        "ready": bool(sidecar.get("ok") and contract_aligned and catalog_names),
        "quiescent": bool(turns.get("active", 0) == 0 and broker_quiescent),
        "http": {
            "ok": True,
            "active_turns": int(turns.get("active", 0)),
            "contract_loaded": local_contract.get("contract_loaded", ""),
            "contract_current": local_contract.get("contract_current", ""),
            "contract_aligned": bool(local_contract.get("contract_aligned")),
        },
        "sidecar": sidecar,
        "catalog": {
            "count": len(catalog_names),
            "names": catalog_names,
        },
    })


ROUTES = (("GET", "/agent/stack/health", stack_health),)
