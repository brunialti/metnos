"""Thin HTTP adapters for the owner-scoped durable-workload control façade."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from aiohttp import web

from durable_workloads.control import DurableControlError, DurableWorkloadControl
from durable_workloads.storage import DurableWorkloadStore
from http_render import render_template, wants_html
from http_app_state import (
    ADMIN_KEY,
    DURABLE_ARTIFACT_DOWNLOADS,
    DURABLE_ARTIFACT_STORE_FACTORY,
    DURABLE_SSE_COUNTS,
    DURABLE_WORKLOAD_STORE_FACTORY,
    SSE_RESPONSES,
    app_get,
    app_setdefault,
)
from logging_setup import get_logger
from messages import get as message


log = get_logger(__name__)

_SSE_RECHECK_S = 1.0
_SSE_KEEPALIVE_S = 15.0
_DOWNLOAD_CAPABILITY_LIFETIME_S = 300
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_MAX_COMMAND_BODY_BYTES = 4096
_SSE_MAX_PER_OWNER = 4
_SSE_MAX_TOTAL = 128
_DURABLE_ERROR_MESSAGE_KEYS = {
    "budget_exhausted": "UI_DURABLE_ERROR_BUDGET_EXHAUSTED",
    "cancelled": "UI_DURABLE_ERROR_CANCELLED",
    "capability_unavailable": "UI_DURABLE_ERROR_CAPABILITY_UNAVAILABLE",
    "contract_violation": "UI_DURABLE_ERROR_CONTRACT_VIOLATION",
    "dependency_fan_in_exceeded": "UI_DURABLE_ERROR_DEPENDENCY_FAN_IN",
    "dependency_input_too_large": "UI_DURABLE_ERROR_DEPENDENCY_INPUT_SIZE",
    "dependency_payload_invalid": "UI_DURABLE_ERROR_DEPENDENCY_PAYLOAD",
    "dependency_result_missing": "UI_DURABLE_ERROR_DEPENDENCY_MISSING",
    "dependency_result_unavailable": "UI_DURABLE_ERROR_DEPENDENCY_UNAVAILABLE",
    "duplicate_entry_identity": "UI_DURABLE_ERROR_DUPLICATE_ENTRY",
    "entry_identity_invalid": "UI_DURABLE_ERROR_ENTRY_IDENTITY",
    "executor_permanent": "UI_DURABLE_ERROR_EXECUTOR_PERMANENT",
    "executor_transient": "UI_DURABLE_ERROR_EXECUTOR_TRANSIENT",
    "invalid_plan": "UI_DURABLE_ERROR_INVALID_PLAN",
    "inventory_unstable": "UI_DURABLE_ERROR_INVENTORY_UNSTABLE",
    "lease_lost": "UI_DURABLE_ERROR_LEASE_LOST",
    "publication_ambiguous": "UI_DURABLE_ERROR_PUBLICATION_AMBIGUOUS",
    "reduction_not_converging": "UI_DURABLE_ERROR_REDUCTION_NOT_CONVERGING",
    "result_digest_conflict": "UI_DURABLE_ERROR_RESULT_CONFLICT",
    "reusable_result_invalid": "UI_DURABLE_ERROR_REUSE_INVALID",
    "source_missing": "UI_DURABLE_ERROR_SOURCE_MISSING",
    "stage_unit_cap_exceeded": "UI_DURABLE_ERROR_STAGE_CAP",
}


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


def _artifact_error(exc: Exception) -> DurableControlError:
    from durable_workloads.artifacts import (
        ArtifactConflictError,
        ArtifactNotFoundError,
        ArtifactSecurityError,
    )

    if isinstance(exc, (ArtifactNotFoundError, ArtifactConflictError, ArtifactSecurityError)):
        return DurableControlError("durable_workload.not_found", 404)
    return DurableControlError("durable_workload.unavailable", 503)


async def _with_artifact_store(request: web.Request, operation: Callable[[Any], Any]) -> Any:
    factory = app_get(request.app, DURABLE_ARTIFACT_STORE_FACTORY)
    if not callable(factory):
        raise DurableControlError("durable_workload.unavailable", 503)

    def run() -> Any:
        store = factory()
        try:
            return operation(store)
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()

    try:
        return await asyncio.to_thread(run)
    except DurableControlError:
        raise
    except Exception as exc:
        raise _artifact_error(exc) from None


def _download_filename(logical_name: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    cleaned = "".join(character if character in allowed else "_" for character in logical_name)
    return (cleaned.strip("._") or "artifact")[:96]


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


def _last_event_id(request: web.Request) -> int:
    raw = request.headers.get("Last-Event-ID", "").strip()
    if raw == "":
        raw = request.query.get("after_event_id", "").strip()
    if raw == "":
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise DurableControlError("durable_workload.invalid_last_event_id", 400) from None
    if value < 0:
        raise DurableControlError("durable_workload.invalid_last_event_id", 400)
    return value


def _recent_events(request: web.Request) -> bool:
    value = request.query.get("recent")
    if value in (None, "0"):
        return False
    if value == "1":
        return True
    raise DurableControlError("durable_workload.invalid_request", 400)


async def _command_body(request: web.Request) -> tuple[int, str]:
    try:
        encoded = bytearray()
        while len(encoded) <= _MAX_COMMAND_BODY_BYTES:
            chunk = await request.content.read(
                _MAX_COMMAND_BODY_BYTES + 1 - len(encoded)
            )
            if not chunk:
                break
            encoded.extend(chunk)
        if len(encoded) > _MAX_COMMAND_BODY_BYTES:
            raise ValueError("command body exceeds its boundary")
        body = json.loads(bytes(encoded).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise DurableControlError("durable_workload.invalid_request", 400) from None
    if not isinstance(body, Mapping):
        raise DurableControlError("durable_workload.invalid_request", 400)
    if any(key in body for key in ("owner", "owner_id", "owner_user_id")):
        raise DurableControlError("durable_workload.owner_in_body_rejected", 400)
    if set(body) != {"expected_version", "idempotency_key"}:
        raise DurableControlError("durable_workload.invalid_request", 400)
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


def _reserve_sse(app: Any, owner_user_id: str) -> dict[str, int] | None:
    """Reserve one bounded LRE stream before a response is prepared."""

    counts = app_setdefault(app, DURABLE_SSE_COUNTS, {})
    if not isinstance(counts, dict) or any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        for key, value in counts.items()
    ):
        return None
    owner_count = int(counts.get(owner_user_id, 0))
    if owner_count >= _SSE_MAX_PER_OWNER or sum(counts.values()) >= _SSE_MAX_TOTAL:
        return None
    counts[owner_user_id] = owner_count + 1
    return counts


def _release_sse(counts: dict[str, int], owner_user_id: str) -> None:
    current = counts.get(owner_user_id)
    if current is None:
        return
    if current <= 1:
        counts.pop(owner_user_id, None)
    else:
        counts[owner_user_id] = current - 1


async def workloads(request: web.Request) -> web.Response:
    """GET /agent/workloads — bounded, owner-scoped workload list."""

    if wants_html(request):
        return await workload_console(request)
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


async def workload_console(request: web.Request) -> web.Response:
    """Render the compact F10 control surface; reads remain on the F9 API."""

    try:
        await _owner(request)
    except DurableControlError as error:
        return _error_response(error)
    state_keys = (
        "draft", "admitted", "queued", "running", "pause_requested",
        "paused", "cancel_requested", "cancelled", "needs_attention",
        "failed", "completed_with_errors", "completed",
    )
    priority_keys = ("low", "normal", "high")
    stage_type_keys = ("inventory", "map", "reduce", "validate", "publish")
    runner_kind_keys = ("internal", "executor", "workload")
    artifact_state_keys = (
        "prepared", "committed", "published", "needs_attention", "expired",
    )
    resource_keys = ("cpu", "device", "llm", "local_io", "network_io", "vlm")
    copy = {
        "empty": message("UI_DURABLE_EMPTY"),
        "state": message("UI_DURABLE_STATE"),
        "priority": message("UI_DURABLE_PRIORITY"),
        "updated": message("UI_DURABLE_UPDATED"),
        "created": message("UI_DURABLE_CREATED"),
        "progress": message("UI_DURABLE_PROGRESS"),
        "open": message("UI_DURABLE_OPEN"),
        "revision": message("UI_DURABLE_REVISION"),
        "plan": message("UI_DURABLE_PLAN"),
        "planDigest": message("UI_DURABLE_PLAN_DIGEST"),
        "inventoryDigest": message("UI_DURABLE_INVENTORY_DIGEST"),
        "budget": message("UI_DURABLE_BUDGET"),
        "stages": message("UI_DURABLE_STAGES"),
        "errors": message("UI_DURABLE_ERROR_CATEGORIES"),
        "errorUnknown": message("UI_DURABLE_ERROR_UNKNOWN"),
        "unknown": message("UI_DURABLE_VALUE_UNKNOWN"),
        "errorLabels": {
            code: message(key)
            for code, key in _DURABLE_ERROR_MESSAGE_KEYS.items()
        },
        "materializationAttention": message(
            "UI_DURABLE_MATERIALIZATION_ATTENTION"
        ),
        "warnings": message("UI_DURABLE_WARNINGS"),
        "runner": message("UI_DURABLE_RUNNER"),
        "resources": message("UI_DURABLE_RESOURCES"),
        "timeout": message("UI_DURABLE_TIMEOUT"),
        "required": message("UI_DURABLE_REQUIRED"),
        "events": message("UI_DURABLE_EVENTS"),
        "units": message("UI_DURABLE_UNITS"),
        "artifacts": message("UI_DURABLE_ARTIFACTS"),
        "loadUnits": message("UI_DURABLE_LOAD_UNITS"),
        "loadAttention": message("UI_DURABLE_LOAD_ATTENTION"),
        "loadFailed": message("UI_DURABLE_LOAD_FAILED"),
        "loadMore": message("UI_DURABLE_LOAD_MORE"),
        "pause": message("UI_DURABLE_PAUSE"),
        "resume": message("UI_DURABLE_RESUME"),
        "cancel": message("UI_DURABLE_CANCEL"),
        "retry": message("UI_DURABLE_RETRY"),
        "download": message("UI_DURABLE_DOWNLOAD"),
        "unavailable": message("UI_DURABLE_UNAVAILABLE"),
        "cancelConfirm": message("UI_DURABLE_CANCEL_CONFIRM"),
        "actionFailed": message("UI_DURABLE_ACTION_FAILED"),
        "live": message("UI_DURABLE_LIVE"),
        "reconnecting": message("UI_DURABLE_RECONNECTING"),
        "warningsByCode": {
            "inventory_truncated": message("UI_DURABLE_WARNING_INVENTORY_TRUNCATED"),
            "partial_output_accepted": message("UI_DURABLE_WARNING_PARTIAL_OUTPUT"),
            "declared_failures_allowed": message("UI_DURABLE_WARNING_DECLARED_FAILURES"),
            "source_coverage_incomplete": message("UI_DURABLE_WARNING_SOURCE_COVERAGE"),
        },
        "budgetLabels": {
            "max_units": message("UI_DURABLE_BUDGET_MAX_UNITS"),
            "max_attempts_per_unit": message("UI_DURABLE_BUDGET_MAX_ATTEMPTS"),
            "max_wall_time_s": message("UI_DURABLE_BUDGET_MAX_TIME"),
            "max_bytes_read": message("UI_DURABLE_BUDGET_MAX_READ"),
            "max_bytes_written": message("UI_DURABLE_BUDGET_MAX_WRITTEN"),
            "max_tokens": message("UI_DURABLE_BUDGET_MAX_TOKENS"),
            "max_cost_micros": message("UI_DURABLE_BUDGET_MAX_COST"),
            "max_artifacts": message("UI_DURABLE_BUDGET_MAX_ARTIFACTS"),
            "max_concurrency": message("UI_DURABLE_BUDGET_MAX_CONCURRENCY"),
        },
        "states": {
            state: message("UI_DURABLE_STATE_" + state.upper())
            for state in state_keys
        },
        "priorities": {
            priority: message("UI_DURABLE_PRIORITY_" + priority.upper())
            for priority in priority_keys
        },
        "stageTypes": {
            stage_type: message("UI_DURABLE_STAGE_TYPE_" + stage_type.upper())
            for stage_type in stage_type_keys
        },
        "runnerKinds": {
            runner_kind: message("UI_DURABLE_RUNNER_KIND_" + runner_kind.upper())
            for runner_kind in runner_kind_keys
        },
        "artifactStates": {
            state: message("UI_DURABLE_ARTIFACT_STATE_" + state.upper())
            for state in artifact_state_keys
        },
        "resourceLabels": {
            resource: message("UI_DURABLE_RESOURCE_" + resource.upper())
            for resource in resource_keys
        },
    }
    return web.Response(
        text=render_template("durable_workloads.html", copy=copy),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


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
                owner,
                workload_id,
                cursor=_cursor(request),
                limit=_limit(request),
                recent=_recent_events(request),
            ),
        )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})
    except DurableControlError as error:
        return _error_response(error)


async def workload_event_stream(request: web.Request) -> web.StreamResponse | web.Response:
    """SSE replay backed by persistent events, never by ``TurnEventLog``.

    The bounded database read happens once per subscribed workload, not once
    per unit or table row.  ``Last-Event-ID`` makes refresh, a new device and a
    process restart converge on the same monotonic event history.
    """

    try:
        owner = await _owner(request)
        workload_id = request.match_info["workload_id"]
        after_event_id = _last_event_id(request)
        # Authorize and prove the owner-scoped workload exists before opening
        # an SSE response: errors retain the normal localized JSON shape.
        first = await _invoke(
            request,
            lambda control: control.stream_events(
                owner, workload_id, after_event_id=after_event_id,
            ),
        )
    except DurableControlError as error:
        return _error_response(error)

    counts = _reserve_sse(request.app, owner)
    if counts is None:
        return _error_response(DurableControlError(
            "durable_workload.stream_limit", 429,
        ))
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    active = app_setdefault(request.app, SSE_RESPONSES, set())
    last_keepalive = time.monotonic()
    batch = first
    prepared = False
    try:
        await response.prepare(request)
        prepared = True
        active.add(response)
        while True:
            transport = request.transport
            if transport is None or transport.is_closing():
                break
            if batch:
                for event in batch:
                    payload = json.dumps(
                        {
                            "schema_version": "metnos.durable-sse-event/1",
                            "event": event.to_dict(),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    await response.write(
                        f"id: {event.event_id}\nevent: workload\ndata: {payload}\n\n".encode(
                            "utf-8"
                        )
                    )
                    after_event_id = event.event_id
                # A full façade batch may have more DB history immediately;
                # drain it before waiting for a new database observation.
                batch = await _invoke(
                    request,
                    lambda control: control.stream_events(
                        owner, workload_id, after_event_id=after_event_id,
                    ),
                )
                continue

            now = time.monotonic()
            if now - last_keepalive >= _SSE_KEEPALIVE_S:
                # Transport comment only: no event row is ever persisted.
                await response.write(b": keepalive\n\n")
                last_keepalive = now
            await asyncio.sleep(_SSE_RECHECK_S)
            batch = await _invoke(
                request,
                lambda control: control.stream_events(
                    owner, workload_id, after_event_id=after_event_id,
                ),
            )
    except (asyncio.CancelledError, ConnectionError, ConnectionResetError):
        pass
    except DurableControlError:
        # The owner or store can disappear after a stream was opened.  The
        # connection closes without disclosing an exception or stored content;
        # reconnecting through the ordinary read model is authoritative.
        log.warning("durable_workload_sse_closed")
    finally:
        active.discard(response)
        _release_sse(counts, owner)
        if prepared:
            try:
                await response.write_eof()
            except (ConnectionError, ConnectionResetError, RuntimeError):
                pass
    return response


async def issue_artifact_download(request: web.Request) -> web.Response:
    """Issue a brief owner-bound capability for one registered artifact."""

    try:
        owner = await _owner(request)
        workload_id = request.match_info["workload_id"]
        artifact_id = request.match_info["artifact_id"]
        artifact = await _with_artifact_store(
            request,
            lambda store: store.get_downloadable_artifact(owner, artifact_id),
        )
        if artifact.workload_id != workload_id:
            raise DurableControlError("durable_workload.not_found", 404)
        registry = app_get(request.app, DURABLE_ARTIFACT_DOWNLOADS)
        if registry is None or not callable(getattr(registry, "issue", None)):
            raise DurableControlError("durable_workload.unavailable", 503)
        capability = registry.issue(
            owner,
            artifact.artifact_id,
            lifetime=timedelta(seconds=_DOWNLOAD_CAPABILITY_LIFETIME_S),
        )
        return web.json_response(
            {
                "schema_version": "metnos.durable-artifact-download/1",
                "download_url": f"/agent/workload-downloads/{capability.token}",
                "expires_at": capability.expires_at.isoformat(
                    timespec="seconds"
                ).replace("+00:00", "Z"),
            },
            headers={"Cache-Control": "no-store"},
        )
    except DurableControlError as error:
        return _error_response(error)
    except Exception as exc:
        return _error_response(_artifact_error(exc))


async def workload_artifacts(request: web.Request) -> web.Response:
    """List safe registry metadata; blob references and paths stay private."""

    try:
        owner = await _owner(request)
        workload_id = request.match_info["workload_id"]
        await _invoke(request, lambda control: control.detail(owner, workload_id))
        records = await _with_artifact_store(
            request,
            lambda store: store.list_workload_artifacts(owner, workload_id),
        )
        return web.json_response(
            {
                "schema_version": "metnos.durable-artifacts/1",
                "items": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "logical_name": artifact.logical_name,
                        "mime_type": artifact.mime_type,
                        "size_bytes": artifact.size_bytes,
                        "digest": artifact.digest,
                        "state": artifact.state.value,
                        "retention_until": artifact.retention_until,
                    }
                    for artifact in records
                ],
            },
            headers={"Cache-Control": "no-store"},
        )
    except DurableControlError as error:
        return _error_response(error)


async def artifact_download(request: web.Request) -> web.StreamResponse | web.Response:
    """Serve bytes from a validated registry entry, never from a browser path."""

    try:
        owner = await _owner(request)
        registry = app_get(request.app, DURABLE_ARTIFACT_DOWNLOADS)
        token = request.match_info["capability"]
        capability = (
            registry.resolve(token, owner_user_id=owner)
            if registry is not None and callable(getattr(registry, "resolve", None))
            else None
        )
        if capability is None:
            raise DurableControlError("durable_workload.not_found", 404)
        factory = app_get(request.app, DURABLE_ARTIFACT_STORE_FACTORY)
        if not callable(factory):
            raise DurableControlError("durable_workload.unavailable", 503)

        def open_stream():
            store = factory()
            try:
                return store.open_registered_download(owner, capability.artifact_id)
            finally:
                # The descriptor returned by ArtifactStore is independent of
                # SQLite.  Closing metadata here preserves SQLite thread
                # affinity while the async response streams only that FD.
                store.close()

        artifact, stream = await asyncio.to_thread(open_stream)
    except DurableControlError as error:
        return _error_response(error)
    except Exception as exc:
        return _error_response(_artifact_error(exc))

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": artifact.mime_type,
            "Content-Disposition": (
                "attachment; filename=\"" + _download_filename(artifact.logical_name) + "\""
            ),
            "Content-Length": str(artifact.size_bytes),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
    try:
        await response.prepare(request)
        while True:
            chunk = await asyncio.to_thread(stream.read, _DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            await response.write(chunk)
    except (ConnectionError, ConnectionResetError):
        pass
    finally:
        await asyncio.to_thread(stream.close)
        try:
            await response.write_eof()
        except (ConnectionError, ConnectionResetError, RuntimeError):
            pass
    return response


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


async def workload_attention_retry(request: web.Request) -> web.Response:
    """POST /agent/workloads/{workload_id}/attention/retry — closed decision."""

    return await _resolve_attention(request, "retry")


async def workload_attention_cancel(request: web.Request) -> web.Response:
    """POST /agent/workloads/{workload_id}/attention/cancel — closed decision."""

    return await _resolve_attention(request, "cancel")


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


async def _resolve_attention(request: web.Request, decision: str) -> web.Response:
    try:
        owner = await _owner(request)
        expected_version, idempotency_key = await _command_body(request)
        workload_id = request.match_info["workload_id"]
        payload = await _invoke(
            request,
            lambda control: control.resolve_attention(
                owner,
                workload_id,
                decision=decision,
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
    ("GET", "/agent/workloads/{workload_id}/stream", workload_event_stream),
    ("GET", "/agent/workloads/{workload_id}/units", workload_units),
    ("GET", "/agent/workloads/{workload_id}/artifacts", workload_artifacts),
    ("POST", "/agent/workloads/{workload_id}/artifacts/{artifact_id}/download", issue_artifact_download),
    ("GET", "/agent/workload-downloads/{capability}", artifact_download),
    ("POST", "/agent/workloads/{workload_id}/pause", workload_pause),
    ("POST", "/agent/workloads/{workload_id}/resume", workload_resume),
    ("POST", "/agent/workloads/{workload_id}/cancel", workload_cancel),
    ("POST", "/agent/workloads/{workload_id}/attention/retry", workload_attention_retry),
    ("POST", "/agent/workloads/{workload_id}/attention/cancel", workload_attention_cancel),
)


__all__ = ["ROUTES"]
