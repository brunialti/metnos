"""Durable, best-effort notifications for the Metnos instance administrator.

Every notification is persisted locally before delivery is attempted.  A
verified Telegram channel is used when available; the admin Services page is
the durable fallback and therefore never depends on an external provider.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

import config as _C


log = logging.getLogger("metnos.notify_admin")
_MAX_EVENTS = 200
_PROCESS_LOCK = threading.Lock()


def _events_path() -> Path:
    return Path(_C.PATH_USER_STATE) / "admin_notifications.json"


def _lock_path() -> Path:
    return Path(_C.PATH_USER_STATE) / "admin_notifications.lock"


@contextlib.contextmanager
def _file_lock(*, exclusive: bool) -> Iterator[None]:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_unlocked() -> list[dict]:
    try:
        payload = json.loads(_events_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    rows = payload.get("notifications") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _write_unlocked(rows: list[dict]) -> None:
    path = _events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "notifications": rows[-_MAX_EVENTS:],
        "updated_at": time.time(),
    }
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


def admin_language() -> str:
    """Resolve the host user's language independently of any HTTP request."""
    import i18n

    try:
        import users
        hosts = users.list_users(role="host")
        if hosts:
            return str(
                users.get_pref(hosts[0]["id"], "lang", i18n.current_lang())
                or i18n.current_lang()
            )
    except Exception:  # notification fallback must remain available
        pass
    return i18n.current_lang()


def _telegram_recipient() -> tuple[str | None, str]:
    try:
        import users
        hosts = users.list_users(role="host")
        if not hosts:
            return None, "no_host_user"
        channel = users.get_channel(hosts[0]["id"], "telegram")
        if not channel or not channel.get("verified_at"):
            return None, "telegram_not_verified"
        recipient = channel.get("recipient_id")
        if not recipient:
            return None, "recipient_id_missing"
        return str(recipient), ""
    except Exception as exc:  # noqa: BLE001 - durable local fallback
        return None, f"recipient_lookup_failed:{type(exc).__name__}"


def _deliver_telegram(title: str, body: str) -> tuple[bool, str]:
    recipient, error = _telegram_recipient()
    if not recipient:
        return False, error
    try:
        from channels import OutboundMessage
        from channels.telegram import TelegramChannel
        TelegramChannel().send(
            recipient=recipient,
            message=OutboundMessage(text=f"{title}\n\n{body}"),
        )
        return True, ""
    except Exception as exc:  # noqa: BLE001 - local event is already durable
        return False, f"telegram_send_failed:{type(exc).__name__}"


def notify(
    body: str,
    *,
    title: str = "Metnos",
    kind: str = "system",
    severity: str = "warning",
    event_key: str = "",
    metadata: dict | None = None,
) -> dict:
    """Persist one event and attempt Telegram delivery exactly once per key."""
    clean_key = str(event_key or "").strip()
    with _PROCESS_LOCK, _file_lock(exclusive=True):
        rows = _read_unlocked()
        if clean_key:
            duplicate = next(
                (row for row in rows if row.get("event_key") == clean_key), None,
            )
            if duplicate is not None:
                return {**duplicate, "duplicate": True}
        event = {
            "id": uuid.uuid4().hex,
            "event_key": clean_key,
            "kind": str(kind or "system"),
            "severity": str(severity or "warning"),
            "title": str(title or "Metnos"),
            "body": str(body or ""),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "delivery": "pending",
            "delivery_error": "",
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
        rows.append(event)
        _write_unlocked(rows)

    delivered, error = _deliver_telegram(event["title"], event["body"])
    event["delivery"] = "telegram" if delivered else "local"
    event["delivery_error"] = error
    with _PROCESS_LOCK, _file_lock(exclusive=True):
        rows = _read_unlocked()
        for index, row in enumerate(rows):
            if row.get("id") == event["id"]:
                rows[index] = event
                break
        _write_unlocked(rows)
    if not delivered:
        log.warning(
            "admin notification persisted locally kind=%s delivery=%s",
            event["kind"], error or "unavailable",
        )
    return dict(event)


def recent(*, limit: int = 20, kind_prefix: str = "") -> list[dict]:
    cap = max(0, min(int(limit), 100))
    with _PROCESS_LOCK, _file_lock(exclusive=False):
        rows = _read_unlocked()
    if kind_prefix:
        rows = [row for row in rows if str(row.get("kind", "")).startswith(kind_prefix)]
    return list(reversed(rows[-cap:])) if cap else []

