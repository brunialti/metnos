"""Stateless thin wrapper around SchedulerStorage for non-daemon callers.

Used by code outside the asyncio loop to register/cancel/toggle/list jobs and
timers. Each call opens a fresh SchedulerStorage, performs the op, closes.
After every mutation, attempts a best-effort `kick()` of a co-located daemon
via `daemon_handle.get_active()`. Out-of-process callers (CLI) see no handle
and the write is still durable in DB; the daemon picks it up at next iteration
or via PR6's HTTP-level kick endpoint.

§7.9 deterministico: no LLM, pure dataclass + sqlite.
§7.2 semplicita': zero connection pooling; sqlite WAL is fine for our load.
"""
from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import ScheduleEntry
from .schedule_parser import next_fire_at as compute_next_fire
from .storage import DEFAULT_DB_PATH as _BUILTIN_DEFAULT
from .storage import SchedulerStorage


def _resolve_db_path() -> Path:
    """Lazy resolution: env override `METNOS_SCHEDULER_V2_DB` wins.

    Read at every call so tests can monkeypatch via env without import-order
    races. Cheap (one os.environ lookup + Path()).
    """
    override = os.environ.get("METNOS_SCHEDULER_V2_DB")
    if override:
        return Path(override).expanduser()
    return _BUILTIN_DEFAULT


def get_storage() -> SchedulerStorage:
    """Returns a SchedulerStorage instance bound to the resolved DB path.

    Each call returns a fresh connection; the caller is responsible for close().
    """
    return SchedulerStorage(_resolve_db_path())


def _try_kick_local_daemon() -> None:
    """Best-effort kick of an in-process daemon. No-op if absent.

    Imported lazily to avoid circular import when `daemon.py` itself imports
    from `client` (none today, but cheap insurance).
    """
    try:
        from .daemon_handle import get_active
        d = get_active()
        if d is not None:
            try:
                d.kick()
            except Exception:
                # Best-effort: a kick failure must never block a write.
                pass
    except Exception:
        pass


def _entry_to_dict(e: ScheduleEntry) -> dict:
    """Serialize a ScheduleEntry to a plain dict for callers (CLI/HTTP)."""
    d = dataclasses.asdict(e)
    # `payload` is already a dict in the dataclass; ensure types are JSON-safe
    return d


# --- jobs (recurring) ----------------------------------------------------


def add_job(
    *,
    name: str,
    trigger: str,
    callback_key: str,
    payload: dict | None = None,
    origin: str = "user",
    timeout_s: int | None = None,
    weekdays: str = "",
    expires_at: str = "",
    grace_window_s: int | None = None,
    label: str = "",
    description: str = "",
    tz_name: str = "Europe/Rome",
) -> dict:
    """Register a recurring job. UPSERT on `name`. Returns the persisted entry.

    Computes `next_fire_at` from the trigger at call time. After upsert, kicks
    a local daemon if any.
    """
    nxt = compute_next_fire(trigger, time.time(), tz_name)
    entry = ScheduleEntry(
        name=name,
        trigger=trigger,
        next_fire_at=nxt,
        recurring=True,
        callback_key=callback_key,
        payload=payload or {},
        weekdays=weekdays,
        expires_at=expires_at,
        timeout_s=timeout_s,
        grace_window_s=grace_window_s,
        origin=origin,
        label=label,
        description=description,
    )
    storage = get_storage()
    try:
        storage.upsert(entry)
        # Re-read so callers see the persisted state with id assigned.
        persisted = storage.get_by_name(name)
    finally:
        storage.close()
    _try_kick_local_daemon()
    return _entry_to_dict(persisted) if persisted else _entry_to_dict(entry)


def add_timer(
    *,
    callback_key: str,
    fire_at_iso: str | None = None,
    delay_s: float | None = None,
    payload: dict | None = None,
    label: str = "",
    source_command: str = "",
    origin: str = "user",
    timeout_s: int | None = None,
    tz_name: str = "Europe/Rome",
) -> dict:
    """Register a one-shot timer. Returns the persisted entry as dict.

    Provide EITHER `fire_at_iso` (UTC ISO 8601) OR `delay_s` (seconds from now),
    not both. The internal trigger is normalized to `at:<ISO8601>` so the
    daemon's parser treats it uniformly with other triggers.
    """
    if (fire_at_iso is None) == (delay_s is None):
        raise ValueError(
            "add_timer: provide exactly one of fire_at_iso or delay_s"
        )
    if delay_s is not None:
        if delay_s < 0:
            raise ValueError(f"add_timer: delay_s must be >=0, got {delay_s}")
        target = datetime.fromtimestamp(time.time() + float(delay_s), tz=timezone.utc)
        fire_at_iso = target.isoformat(timespec="seconds")
    assert fire_at_iso is not None
    trigger = f"at:{fire_at_iso}"
    nxt = compute_next_fire(trigger, time.time(), tz_name)
    # Anonymous timer name: stable per (callback,fire_at) pair so re-registration
    # is naturally idempotent. Caller can pass label as a hint but we always
    # disambiguate by the firing instant.
    safe_label = label or callback_key
    name = f"timer_{safe_label}_{int(nxt)}"
    entry = ScheduleEntry(
        name=name,
        trigger=trigger,
        next_fire_at=nxt,
        recurring=False,
        callback_key=callback_key,
        payload=payload or {},
        timeout_s=timeout_s,
        origin=origin,
        label=label,
        source_command=source_command,
    )
    storage = get_storage()
    try:
        storage.upsert(entry)
        persisted = storage.get_by_name(name)
    finally:
        storage.close()
    _try_kick_local_daemon()
    return _entry_to_dict(persisted) if persisted else _entry_to_dict(entry)


def cancel_job(name: str) -> bool:
    """Delete a recurring (or one-shot) entry by name. Returns True if removed."""
    storage = get_storage()
    try:
        ok = storage.delete(name)
    finally:
        storage.close()
    if ok:
        _try_kick_local_daemon()
    return ok


def cancel_timer(timer_id: str) -> bool:
    """Cancel a one-shot timer by entry name (same key as cancel_job).

    Distinct function for callsite clarity; identical semantics to cancel_job
    today since one-shot timers live in `schedule_entries` with recurring=False.
    """
    return cancel_job(timer_id)


def toggle_job(name: str, enabled: bool) -> bool:
    """Enable/disable a job by name. Returns True if the row existed.

    Usa gli UPDATE targeted di storage (no upsert): `enable()` riabilita +
    azzera la streak + RICALCOLA next_fire_at dal trigger (niente catch-up
    immediato per il tempo trascorso da disabilitato)."""
    storage = get_storage()
    try:
        entry = storage.get_by_name(name)
        if entry is None or entry.id is None:
            return False
        if bool(enabled):
            storage.enable(entry.id)
        else:
            storage.disable(entry.id)
    finally:
        storage.close()
    _try_kick_local_daemon()
    return True


def resume_job(name: str) -> bool:
    """Riattiva un task auto-disabilitato dal circuit-breaker: enabled=1 +
    azzera consecutive_failures + ricalcola next_fire_at dal trigger (riparte
    dal prossimo slot, non spara subito). Returns True se esisteva.
    L'invariante vive in `storage.enable()`."""
    storage = get_storage()
    try:
        entry = storage.get_by_name(name)
        if entry is None or entry.id is None:
            return False
        storage.enable(entry.id)
    finally:
        storage.close()
    _try_kick_local_daemon()
    return True


def list_jobs(
    *,
    origin: str | None = None,
    enabled: bool | None = None,
) -> list[dict]:
    """List recurring entries (recurring=True), optionally filtered."""
    storage = get_storage()
    try:
        all_entries = storage.list_all()
    finally:
        storage.close()
    out = []
    for e in all_entries:
        if not e.recurring:
            continue
        if origin is not None and e.origin != origin:
            continue
        if enabled is not None and e.enabled != bool(enabled):
            continue
        out.append(_entry_to_dict(e))
    return out


def list_timers() -> list[dict]:
    """List active one-shot entries (recurring=False, enabled=True)."""
    storage = get_storage()
    try:
        all_entries = storage.list_all()
    finally:
        storage.close()
    return [
        _entry_to_dict(e)
        for e in all_entries
        if not e.recurring and e.enabled
    ]


def history(name: str | None = None, limit: int = 20) -> list[dict]:
    """Return the last N runs, optionally filtered by entry name."""
    n = max(1, int(limit))
    storage = get_storage()
    try:
        runs = storage.list_runs(limit=n, entry_name=name)
    finally:
        storage.close()
    return [dataclasses.asdict(r) for r in runs]


def run_now(name: str) -> dict:
    """Synthesize an immediate fire by setting next_fire_at = now() and kick().

    For diagnostics only; in v2 the loop will catch it on the next iteration
    (or immediately if kick lands). Returns the entry post-update, or
    {ok:False, error:...} if the name is unknown.
    """
    storage = get_storage()
    try:
        entry = storage.get_by_name(name)
        if entry is None:
            return {"ok": False, "error": f"unknown job: {name!r}"}
        if entry.id is None:
            return {"ok": False, "error": f"entry {name!r} has no id (storage corruption?)"}
        # Se disabilitato, riabilita: altrimenti fetch_due (WHERE enabled=1) non
        # lo raccoglie mai e il fire richiesto non avviene (§2.8: niente "lo
        # eseguira'" se in realta' resta fermo).
        if not entry.enabled:
            storage.enable(entry.id)
        storage.update_next_fire(entry.id, time.time())
        # Re-read for fresh next_fire_at.
        entry = storage.get_by_name(name)
    finally:
        storage.close()
    _try_kick_local_daemon()
    out = _entry_to_dict(entry) if entry else {}
    out["ok"] = True
    return out
