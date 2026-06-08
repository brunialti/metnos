"""Dataclasses for scheduler v2.

ScheduleEntry mirrors a row of `schedule_entries`. Run mirrors `runs`.
Both are plain values: serialize/deserialize is keyed by column name so
storage layer can map cleanly to sqlite3.Row.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ScheduleEntry:
    name: str
    trigger: str
    next_fire_at: float
    recurring: bool
    callback_key: str
    payload: dict = field(default_factory=dict)
    weekdays: str = ""              # CSV of {mon,tue,wed,thu,fri,sat,sun}, empty = any
    expires_at: str = ""            # ISO8601 UTC; empty = never
    remaining_runs: int = 0          # 0 = unlimited (only meaningful if recurring)
    enabled: bool = True
    timeout_s: int | None = None
    is_async: bool = False
    max_concurrent: int = 1
    grace_window_s: int | None = None
    origin: str = "system"
    label: str = ""
    source_command: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str | None = None
    last_status: str | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None
    total_runs: int = 0
    total_failures: int = 0
    # Streak di fallimenti CONSECUTIVI (azzerato al primo success). Alimenta il
    # circuit-breaker: a soglia, il task ricorrente viene disabilitato e l'owner
    # notificato (continua/sospendi/cancella). Distinto da total_failures
    # (cumulativo, mai resettato).
    consecutive_failures: int = 0
    description: str = ""
    id: int | None = None

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["payload"] = json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
        d["recurring"] = 1 if self.recurring else 0
        d["enabled"] = 1 if self.enabled else 0
        d["is_async"] = 1 if self.is_async else 0
        return d

    @classmethod
    def from_row(cls, row: Any) -> "ScheduleEntry":
        # row is sqlite3.Row or dict-like
        d = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
        try:
            payload = json.loads(d.get("payload") or "{}")
        except Exception:
            payload = {}
        return cls(
            id=d.get("id"),
            name=d["name"],
            trigger=d["trigger"],
            next_fire_at=float(d["next_fire_at"]),
            recurring=bool(d["recurring"]),
            callback_key=d["callback_key"],
            payload=payload,
            weekdays=d.get("weekdays") or "",
            expires_at=d.get("expires_at") or "",
            remaining_runs=int(d.get("remaining_runs") or 0),
            enabled=bool(d.get("enabled", 1)),
            timeout_s=d.get("timeout_s"),
            is_async=bool(d.get("is_async") or 0),
            max_concurrent=int(d.get("max_concurrent") or 1),
            grace_window_s=d.get("grace_window_s"),
            origin=d.get("origin") or "system",
            label=d.get("label") or "",
            source_command=d.get("source_command") or "",
            created_at=d.get("created_at") or "",
            updated_at=d.get("updated_at") or "",
            last_run_at=d.get("last_run_at"),
            last_status=d.get("last_status"),
            last_duration_ms=d.get("last_duration_ms"),
            last_error=d.get("last_error"),
            total_runs=int(d.get("total_runs") or 0),
            total_failures=int(d.get("total_failures") or 0),
            consecutive_failures=int(d.get("consecutive_failures") or 0),
            description=d.get("description") or "",
        )


@dataclass
class Run:
    entry_id: int | None
    entry_name: str
    started_at: str
    status: str
    finished_at: str | None = None
    duration_ms: int | None = None
    output: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Run":
        d = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
        return cls(
            id=d.get("id"),
            entry_id=d.get("entry_id"),
            entry_name=d.get("entry_name") or "",
            started_at=d["started_at"],
            finished_at=d.get("finished_at"),
            status=d["status"],
            duration_ms=d.get("duration_ms"),
            output=d.get("output") or "",
        )


