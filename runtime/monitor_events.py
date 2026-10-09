# SPDX-License-Identifier: MIT
"""Contratto dei dati Monitor. Usato dal raccoglitore, fuori dalla query."""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from monitor_metrics import model_identity, normalize_metrics

SCHEMA_VERSION = "metnos.monitor/1"
MAX_CALLS = 1024
EVENTS = {"process_started", "segment_started", "segment_finished",
          "job_started", "job_finished", "call_late", "unattributed"}
_IDENTIFIER = re.compile(r"[\w.:/@+\-]{1,256}", re.ASCII)


def identifier(value: Any, *, required: bool = False) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or "://" in value:
        raise ValueError("invalid_identifier")
    return value


def timestamp(value: Any, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone_required")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid_timestamp") from exc


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _retained_reasons(raw, names):
    previous = raw.get("metadata_reasons")
    if not isinstance(previous, Mapping):
        return {}
    return {name: reason for name, reason in previous.items() if name in names and (
        reason == "invalid_metadata" or name == "finished_at" and reason == "clock_rollback")}


def normalize_call(raw: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    fields = (
        "call_id", "parent_call_id", "provider", "level", "workload",
        "physical_model_requested", "physical_model_reported", "binding_revision",
        "kind", "origin_process", "error_code",
    )
    reasons = _retained_reasons(raw, fields + ("started_at", "finished_at"))
    for name in fields:
        value = raw.get(name)
        try:
            if name.startswith("physical_model_") and isinstance(value, str):
                if not 0 < len(value) <= 256 or "://" in value or any(ord(c) < 32 for c in value):
                    raise ValueError("invalid_model_label")
                result[name] = value
            else:
                result[name] = identifier(value, required=name == "call_id")
        except ValueError:
            if name == "call_id":
                raise
            result[name] = None
            reasons[name] = "invalid_metadata"
    result["metadata_reasons"] = reasons
    result.update(model_identity(result["provider"] or "unknown",
                                 result["physical_model_requested"],
                                 result["physical_model_reported"]))
    for name in ("started_at", "finished_at"):
        try:
            result[name] = timestamp(raw.get(name))
        except ValueError:
            result[name] = None
            reasons[name] = "invalid_metadata"
    if result["started_at"] and result["finished_at"] and result["finished_at"] < result["started_at"]:
        reasons["finished_at"] = "clock_rollback"
    result["status"] = raw.get("status") if raw.get("status") in {
        "ok", "error", "timeout", "cancelled", "late",
    } else "unknown"
    result["attempt_no"] = count(raw.get("attempt_no"))
    metrics = raw.get("metrics")
    result.update(normalize_metrics(metrics if isinstance(metrics, Mapping) else raw))
    return result


def normalize_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Lista chiusa: testi, risposte grezze e indirizzi non passano sul disco."""
    if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid_schema")
    event = raw.get("event")
    if event not in EVENTS:
        raise ValueError("invalid_event")
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "event": event}
    fields = ("segment_id", "turn_id", "parent_turn_id", "job_ref", "owner_id",
              "channel", "process")
    result["metadata_reasons"] = _retained_reasons(raw, fields + (
        "origin", "started_at", "finished_at", "wait_expires_at", "recorded_at",
        "outcome", "process_epoch"))
    for name in fields:
        try:
            result[name] = identifier(raw.get(name), required=(
                name == "segment_id" and event != "process_started"))
        except ValueError:
            if name == "segment_id":
                raise
            result[name] = None
            result["metadata_reasons"][name] = "invalid_metadata"
    origin = raw.get("origin")
    if origin is not None and origin not in {"interactive", "background", "test"}:
        origin = None
        result["metadata_reasons"]["origin"] = "invalid_metadata"
    result["origin"] = origin
    for name in ("started_at", "finished_at", "wait_expires_at", "recorded_at"):
        try:
            result[name] = timestamp(raw.get(name), required=(
                name == "started_at" and event.endswith("started")))
        except ValueError:
            if name in {"started_at", "finished_at"}:
                raise
            result[name] = None
            result["metadata_reasons"][name] = "invalid_metadata"
    if event in {"segment_finished", "job_finished"} and result["finished_at"] is None:
        raise ValueError("finish_required")
    if result["started_at"] and result["finished_at"] and (
        result["finished_at"] < result["started_at"]
    ):
        result["metadata_reasons"]["finished_at"] = "clock_rollback"
    result["active_ms"] = normalize_metrics({"call_wall_ms": raw.get("active_ms")})[
        "call_wall_ms"]
    try:
        result["outcome"] = identifier(raw.get("outcome"))
    except ValueError:
        result["outcome"] = None
        result["metadata_reasons"]["outcome"] = "invalid_metadata"
    for name in ("calls_started", "calls_closed", "dropped_calls", "dropped_events"):
        result[name] = count(raw.get(name))
    epoch = raw.get("process_epoch")
    result["process_epoch"] = None
    if isinstance(epoch, Mapping):
        pid, ticks = count(epoch.get("pid")), count(epoch.get("start_ticks"))
        if pid and ticks is not None:
            try:
                result["process_epoch"] = {"pid": pid, "start_ticks": ticks,
                                           "boot_id": identifier(epoch.get("boot_id"), required=True)}
            except ValueError:
                result["metadata_reasons"]["process_epoch"] = "invalid_metadata"
    calls = raw.get("calls", [])
    if not isinstance(calls, (list, tuple)):
        raise ValueError("invalid_calls")
    result["calls"] = []
    for call in calls[:MAX_CALLS]:
        if isinstance(call, Mapping):
            try:
                result["calls"].append(normalize_call(call))
            except (ValueError, TypeError):
                pass
    omitted = len(calls) - len(result["calls"])
    if omitted:
        result["dropped_calls"] = (result["dropped_calls"] or 0) + omitted
    return result
