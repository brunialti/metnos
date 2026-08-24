"""runtime.chat_target_store — destinazione breve per conversazione.

Ricorda l'ultima destinazione realmente usata (`server` o un device_id) nel
contesto breve di una conversazione (ADR 0034 e DEV-001). Un record scaduto non
e' una preferenza e non puo' instradare una richiesta nuova.

Co-locato con `devices.db` (segue l'isolamento dei test via METNOS_DEVICES_DB).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import config as _C

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_target (
    sender_id  TEXT PRIMARY KEY,
    target     TEXT NOT NULL,        -- 'server' | <device_id>
    device_name TEXT,
    updated_at TEXT NOT NULL
);
"""


def _db_path() -> Path:
    dev = os.environ.get("METNOS_CHAT_TARGET_DB")
    if dev:
        return Path(dev)
    dev_devices = os.environ.get("METNOS_DEVICES_DB")
    if dev_devices:
        return Path(dev_devices).parent / "chat_target.db"
    return _C.PATH_USER_STATE / "chat_target.db"


def _conn() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.execute(_SCHEMA)
    return c


def scope_key(*, owner_user_id: str, actor: str, channel: str,
              conversation_id: str = "") -> str:
    """Return an opaque, unambiguous key for one placement context.

    The owner is authoritative; actor and channel keep legacy endpoints apart;
    a conversation id prevents two browser conversations from sharing a target.
    Length-prefix ambiguity and raw personal identifiers are avoided by hashing
    the canonical tuple.
    """

    fields = [
        str(owner_user_id or actor or "host"),
        str(actor or "host"),
        str(channel or ""),
        str(conversation_id or ""),
    ]
    material = json.dumps(
        fields, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return "scope-v2:" + hashlib.sha256(
        b"metnos:placement-context:2\x00" + material,
    ).hexdigest()


def get_last_target(sender_id: str, *, max_age_s: int | None = None,
                    now: datetime | None = None) -> str | None:
    """Return a fresh target, or ``None`` for missing/expired state."""
    if not sender_id:
        return None
    ttl = _C.TIMEOUT_TARGET_CONTEXT_S if max_age_s is None else max_age_s
    if not isinstance(ttl, int) or ttl <= 0:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT target, updated_at FROM chat_target WHERE sender_id = ?",
            (sender_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        updated = datetime.fromisoformat(str(row["updated_at"]))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = (current.astimezone(timezone.utc)
               - updated.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    if age < 0 or age > ttl:
        return None
    return row["target"]


def set_last_target(sender_id: str, target: str, device_name: str | None = None) -> None:
    """Registra la destinazione per il sender. `target` = 'server' | device_id."""
    if not sender_id or not target:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO chat_target(sender_id, target, device_name, updated_at) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(sender_id) DO UPDATE SET "
            "target=excluded.target, device_name=excluded.device_name, "
            "updated_at=excluded.updated_at",
            (sender_id, target, device_name, now))
        c.commit()
