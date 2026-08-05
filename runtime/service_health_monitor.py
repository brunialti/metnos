"""Transition-based health notifications for the central service catalog."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

import config as _C
import services_registry


SCHEMA_VERSION = 1
_HARD_DOWN = frozenset({"failed", "missing", "stopped"})
_SOFT_DOWN = frozenset({"degraded"})
_STATUS_I18N_KEYS = {
    "degraded": "UI_SERVICES_STATUS_DEGRADED",
    "failed": "UI_SERVICES_STATUS_FAILED",
    "missing": "UI_SERVICES_STATUS_MISSING",
    "running": "UI_SERVICES_STATUS_RUNNING",
    "stopped": "UI_SERVICES_STATUS_STOPPED",
    "transitioning": "UI_SERVICES_STATUS_TRANSITIONING",
}


def _state_path() -> Path:
    return Path(_C.PATH_USER_STATE) / "service_health_monitor.json"


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"schema_version": SCHEMA_VERSION, "services": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), dict):
        return {"schema_version": SCHEMA_VERSION, "services": {}}
    return payload


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _localized_service(row: dict, lang: str) -> str:
    if str(lang).lower().startswith("it"):
        return str(row.get("label") or row.get("key") or "?")
    return str(row.get("label_en") or row.get("label") or row.get("key") or "?")


def _notification(
    row: dict,
    *,
    transition: str,
    incident_id: str,
    notifier: Callable[..., dict],
) -> dict:
    import i18n
    import messages
    import notify_admin

    lang = notify_admin.admin_language()
    service = _localized_service(row, lang)
    status = str(row.get("status") or "unknown")
    with i18n.language_context(lang):
        status_key = _STATUS_I18N_KEYS.get(status)
        localized_status = messages.get(status_key) if status_key else status
        if localized_status.startswith("<missing:"):
            localized_status = status
        if transition == "down":
            title = messages.get("MSG_SERVICE_ALERT_DOWN_TITLE")
            body = messages.get(
                "MSG_SERVICE_ALERT_DOWN_BODY",
                service=service,
                status=localized_status,
            )
            severity = "critical"
            event_key = f"service-down:{incident_id}"
            kind = "service_down"
        elif transition == "stopped":
            title = messages.get("MSG_SERVICE_ALERT_RESOLVED_TITLE")
            body = messages.get(
                "MSG_SERVICE_ALERT_STOPPED_BODY", service=service,
            )
            severity = "info"
            event_key = f"service-stopped:{incident_id}"
            kind = "service_resolved"
        else:
            title = messages.get("MSG_SERVICE_ALERT_RECOVERED_TITLE")
            body = messages.get(
                "MSG_SERVICE_ALERT_RECOVERED_BODY", service=service,
            )
            severity = "info"
            event_key = f"service-recovered:{incident_id}"
            kind = "service_recovered"
    return notifier(
        body,
        title=title,
        kind=kind,
        severity=severity,
        event_key=event_key,
        metadata={
            "service": str(row.get("key") or ""),
            "status": status,
            "unit": str(row.get("unit") or ""),
            "scope": str(row.get("scope") or ""),
        },
    )


def run(
    *,
    rows: list[dict] | None = None,
    notifier: Callable[..., dict] | None = None,
    state_path: Path | None = None,
    now: float | None = None,
) -> dict:
    """Observe all catalog services and notify only on durable transitions.

    Hard systemd failures notify immediately.  Endpoint-only degradation must
    be observed twice, preventing one busy HTTP slot from becoming an alert.
    A service deliberately stopped through the central controller is not a
    failure; if it resolves an open incident, the administrator is informed.
    """
    import notify_admin

    observed = rows if rows is not None else services_registry.snapshots(timeout_s=20)
    notify = notifier or notify_admin.notify
    path = state_path or _state_path()
    timestamp = float(now if now is not None else time.time())
    state = _load(path)
    entries = state.setdefault("services", {})
    pending: list[tuple[dict, str, str]] = []

    for row in observed:
        key = str(row.get("key") or "")
        if not key or services_registry.get(key) is None:
            continue
        previous = entries.get(key) if isinstance(entries.get(key), dict) else {}
        entry = dict(previous)
        desired = str(row.get("desired_state") or services_registry.desired_state(key))
        status = str(row.get("status") or "missing")
        down = desired == "running" and status in (_HARD_DOWN | _SOFT_DOWN)

        if desired == "stopped":
            if entry.get("incident_id") and entry.get("down_notified"):
                pending.append((row, "stopped", str(entry["incident_id"])))
            entry.update({
                "consecutive_down": 0,
                "incident_id": "",
                "down_notified": False,
            })
        elif down:
            consecutive = int(entry.get("consecutive_down") or 0) + 1
            incident_id = str(entry.get("incident_id") or f"{key}:{int(timestamp)}")
            threshold = 1 if status in _HARD_DOWN else 2
            notified = bool(entry.get("down_notified"))
            if consecutive >= threshold and not notified:
                pending.append((row, "down", incident_id))
                notified = True
            entry.update({
                "consecutive_down": consecutive,
                "incident_id": incident_id,
                "down_notified": notified,
            })
        elif status == "running":
            if entry.get("incident_id") and entry.get("down_notified"):
                pending.append((row, "recovered", str(entry["incident_id"])))
            entry.update({
                "consecutive_down": 0,
                "incident_id": "",
                "down_notified": False,
            })
        # ``transitioning`` preserves the previous incident for the next tick.
        entry.update({
            "desired_state": desired,
            "last_status": status,
            "last_observed_at": timestamp,
        })
        entries[key] = entry

    state.update({
        "schema_version": SCHEMA_VERSION,
        "updated_at": timestamp,
    })
    _write(path, state)

    sent: list[dict] = []
    errors: list[dict] = []
    for row, transition, incident_id in pending:
        try:
            event = _notification(
                row,
                transition=transition,
                incident_id=incident_id,
                notifier=notify,
            )
            sent.append({
                "service": row.get("key"),
                "transition": transition,
                "event_id": event.get("id") if isinstance(event, dict) else None,
            })
        except Exception as exc:  # monitoring must not take down the watchdog
            key = str(row.get("key") or "")
            entry = entries.get(key) if isinstance(entries.get(key), dict) else {}
            if transition == "down":
                entry["incident_id"] = incident_id
                entry["down_notified"] = False
            else:
                # Preserve the open incident so recovery/resolution delivery
                # is retried with the same dedupe key on the next tick.
                entry["incident_id"] = incident_id
                entry["down_notified"] = True
            entries[key] = entry
            errors.append({
                "service": row.get("key"),
                "transition": transition,
                "error": type(exc).__name__,
            })

    if errors:
        state["updated_at"] = time.time()
        _write(path, state)

    return {
        "ok": not errors,
        "observed": len(observed),
        "notifications": sent,
        "notification_errors": errors,
        "down": sorted(
            str(row.get("key"))
            for row in observed
            if str(row.get("desired_state") or "running") == "running"
            and str(row.get("status")) in (_HARD_DOWN | _SOFT_DOWN)
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
