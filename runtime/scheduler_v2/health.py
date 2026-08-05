"""Canonical runtime health for the scheduler co-hosted by Metnos HTTP.

The active daemon handle is the authority.  Callers outside the HTTP process
receive an explicit ``daemon_not_registered`` result instead of guessing from
systemd units, database visibility, or the time of the latest scheduled run.
"""
from __future__ import annotations

from datetime import datetime
import json
import time

import config


HEALTH_PATH = config.PATH_USER_STATE / "scheduler_v2_health.json"
_PUBLISHED_MAX_AGE_S = 90.0
_RUNNING_HEARTBEAT_MAX_AGE_S = 70.0


def publish(value: dict) -> None:
    """Atomically expose a bounded snapshot to isolated local observers."""

    payload = {
        "published_at": time.time(),
        "snapshot": dict(value),
    }
    config.write_private_text(
        HEALTH_PATH,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True),
    )


def _unavailable(reason_code: str) -> dict:
    return {
        "component": "scheduler_v2",
        "cohost": "http",
        "state": "unavailable",
        "healthy": False,
        "reason_code": reason_code,
        "started_at": "",
        "stopped_at": "",
        "heartbeat_at": "",
        "heartbeat_age_s": None,
        "jobs_total": 0,
        "jobs_enabled": 0,
        "jobs_running": 0,
        "last_run_at": "",
        "last_run_status": "",
        "error_class": "",
        "error_summary": "",
    }


def _published() -> dict | None:
    try:
        raw = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        published_at = float(raw["published_at"])
        value = dict(raw["snapshot"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    now = time.time()
    published_age = now - published_at
    if published_at > now + 5.0 or published_age > _PUBLISHED_MAX_AGE_S:
        return None
    expected = set(_unavailable("schema").keys())
    if set(value) != expected:
        return None
    heartbeat = str(value.get("heartbeat_at") or "")
    if heartbeat:
        try:
            heartbeat_epoch = datetime.fromisoformat(
                heartbeat.replace("Z", "+00:00")).timestamp()
            value["heartbeat_age_s"] = round(
                max(0.0, now - heartbeat_epoch), 3)
        except ValueError:
            return None
    if (value.get("state") == "running" and value.get("healthy") is True
            and (published_age > _RUNNING_HEARTBEAT_MAX_AGE_S
                 or value.get("heartbeat_age_s") is None
                 or float(value["heartbeat_age_s"])
                 > _RUNNING_HEARTBEAT_MAX_AGE_S)):
        stale = _unavailable("stale_heartbeat")
        for key in (
                "started_at", "heartbeat_at", "heartbeat_age_s",
                "jobs_total", "jobs_enabled", "jobs_running",
                "last_run_at", "last_run_status"):
            stale[key] = value.get(key, stale[key])
        return stale
    return value


def snapshot() -> dict:
    """Return one bounded, JSON-safe scheduler health observation."""

    from . import daemon_handle

    daemon = daemon_handle.get_active()
    if daemon is None:
        return _published() or _unavailable("daemon_not_registered")
    try:
        return daemon.runtime_status()
    except Exception as exc:
        value = _unavailable("health_probe_failed")
        value["error_class"] = type(exc).__name__
        return value
