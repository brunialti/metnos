"""Thin HTTP adapters for the owner-scoped durable-workload control façade."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from aiohttp import web

from durable_workloads.control import DurableControlError, DurableWorkloadControl
from durable_workloads.storage import DurableWorkloadStore
from http_app_state import ADMIN_KEY, DURABLE_WORKLOAD_STORE_FACTORY, app_get
from logging_setup import get_logger
from messages import get as message


log = get_logger(__name__)


def _error_response(error: DurableControlError) -> web.Response:
    """Return stable codes plus a localized, non-sensitive explanation."""

    return web.json_response(
        {
            "schema_version": "metnos.durable-control-error/1",
            "error": {
                "code": error.code,
                "message_code": "ERR_OP_FAILED",
                "message": message("ERR_OP_FAILED", reason=error.code),
            },
        },
        status=error.status,
        headers={"Cache-Control": "no-store"},
    )


async def _owner(request: web.Request) -> str:
    # This resolver derives the immutable authenticated owner. Request bodies
    # never participate in authorization, even when they contain an owner key.
    from http_routes_agent import _resolve_session_user_id

    return await _resolve_session_user_id(request)


async def _invoke(request: web.Request, operation: Callable[[DurableWorkloadControl], dict[str, Any]]) -> dict[str, Any]:
    factory = app_get(request.app, DURABLE_WORKLOAD_STORE_FACTORY, DurableWorkloadStore.open)
    secret = app_get(request.app, ADMIN_KEY, "")
    if not callable(factory) or not isinstance(secret, str) or not secret:
        raise DurableControlError("durable_workload.unavailable", 503)

    def run() -> dict[str, Any]:
        store = factory()
        if not isinstance(store, DurableWorkloadStore):
            raise DurableControlError("durable_workload.unavailable", 503)
        try:
            return operation(DurableWorkloadControl(store, cursor_secret=secret))
        finally:
            store.close()

    try:
        return await asyncio.to_thread(run)
    except DurableControlError:
        raise
    except Exception:
        log.warning("durable_workload_control_unavailable")
        raise DurableControlError("durable_workload.unavailable", 503) from None


def _limit(request: web.Request) -> int | None:
    raw = request.query.get("limit")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise DurableControlError("durable_workload.invalid_limit", 400) from None


def _cursor(request: web.Request) -> str | None:
    value = request.query.get("cursor")
    if value is None or value == "":
        return None
    return value


async def _command_body(request: web.Request) -> tuple[int, str]:
    try:
        body = await request.json()
    except Exception:
        raise DurableControlError("durable_workload.invalid_request", 400) from None
    if not isinstance(body, Mapping):
        raise DurableControlError("durable_workload.invalid_request", 400)
    if any(key in body for key in ("owner", "owner_id", "owner_user_id")):
        raise DurableControlError("durable_workload.owner_in_body_rejected", 400)
    version = body.get("expected_version")
    key = body.get("idempotency_key")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(key, str)
        or not key
        or len(key) > 256
        or key != key.strip()
    ):
        raise DurableControlError("durable_workload.invalid_request", 400)
    return version, key


async def workloads(request: web.Request) -> web.Response:
    """GET /agent/workloads — bounded, owner-scoped workload list."""

    try:
        owner = await _owner(request)
        payload = await _invoke(
            request,
            lambda control: control.list_workloads(
                owner,
                cursor=_cursor(request),
                limit=_limit(request),
                state=request.query.get("state"),
            ),
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})
    except DurableControlError as error:
        return _error_response(error)


async def workload_detail(request: web.Request) -> web.Response:
    """GET /agent/workloads/{workload_id} — no raw plan or result payload."""

    try:
        owner = await _owner(request)
        payload = await _invoke(
            request,
            lambda control: control.detail(owner, request.match_info["workload_id"]),
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})
    except DurableControlError as error:
        return _error_response(error)


async def workload_events(request: web.Request) -> web.Response:
    """GET /agent/workloads/{workload_id}/events — persistent bounded timeline."""

    try:
        owner = await _owner(request)
        workload_id = request.match_info["workload_id"]
        payload = await _invoke(
            request,
            lambda control: control.list_events(
                owner, workload_id, cursor=_cursor(request), limit=_limit(request),
            ),
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})
    except DurableControlError as error:
        return _error_response(error)


async def workload_units(request: web.Request) -> web.Response:
    """GET /agent/workloads/{workload_id}/units — redacted, bounded units."""

    try:
        owner = await _owner(request)
        workload_id = request.match_info["workload_id"]
        payload = await _invoke(
            request,
            lambda control: control.list_units(
                owner,
                workload_id,
                cursor=_cursor(request),
                limit=_limit(request),
                state=request.query.get("state"),
            ),
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})
    except DurableControlError as error:
        return _error_response(error)


async def workload_pause(request: web.Request) -> web.Response:
    """POST /agent/workloads/{workload_id}/pause — exact closed command."""

    return await _command(request, "pause", DurableWorkloadControl.pause)


async def workload_resume(request: web.Request) -> web.Response:
    """POST /agent/workloads/{workload_id}/resume — exact closed command."""

    return await _command(request, "resume", DurableWorkloadControl.resume)


async def workload_cancel(request: web.Request) -> web.Response:
    """POST /agent/workloads/{workload_id}/cancel — exact closed command."""

    return await _command(request, "cancel", DurableWorkloadControl.cancel)


async def _command(
    request: web.Request,
    command: str,
    method: Callable[..., dict[str, Any]],
) -> web.Response:
    try:
        owner = await _owner(request)
        expected_version, idempotency_key = await _command_body(request)
        workload_id = request.match_info["workload_id"]
        payload = await _invoke(
            request,
            lambda control: method(
                control,
                owner,
                workload_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            ),
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})
    except DurableControlError as error:
        return _error_response(error)


ROUTES = (
    ("GET", "/agent/workloads", workloads),
    ("GET", "/agent/workloads/{workload_id}", workload_detail),
    ("GET", "/agent/workloads/{workload_id}/events", workload_events),
    ("GET", "/agent/workloads/{workload_id}/units", workload_units),
    ("POST", "/agent/workloads/{workload_id}/pause", workload_pause),
    ("POST", "/agent/workloads/{workload_id}/resume", workload_resume),
    ("POST", "/agent/workloads/{workload_id}/cancel", workload_cancel),
)


__all__ = ["ROUTES"]
