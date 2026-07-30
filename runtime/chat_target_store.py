"""runtime.chat_target_store — destinazione appiccicosa per (utente, canale).

Ricorda l'ultima destinazione (`server` o un device_id) scelta in una chat, così
un turno senza riferimento esplicito riusa l'ultima destinazione (ADR 0034,
chat-driven placement). Chiave = `sender_id` (`<channel>:<actor>`), la stessa di
`dialog_pending`. Minimale: una tabella sqlite, nessuna migrazione, pre-1.0.

Co-locato con `devices.db` (segue l'isolamento dei test via METNOS_DEVICES_DB).
"""
from __future__ import annotations

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


def get_last_target(sender_id: str) -> str | None:
    """Ritorna l'ultima destinazione ('server' | device_id) o None se mai scelta."""
    if not sender_id:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT target FROM chat_target WHERE sender_id = ?", (sender_id,)
        ).fetchone()
    return row["target"] if row else None


def set_last_target(sender_id: str, target: str, device_name: str | None = None) -> None:
    """Registra la destinazione per il sender. `target` = 'server' | device_id."""
    if not sender_id or not target:
        return
    from datetime import datetime, timezone
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
