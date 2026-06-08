"""github_watch_state — state diff sqlite per il watcher GitHub (Fase D).

Tabella `watch_state` traccia per ogni (repo, kind, number):
- `last_event_id`: id ultimo commento/evento osservato (per dedup polling)
- `last_check_ts`: timestamp ultima verifica
- `snoozed_until`: epoch entro cui ignorare (Stage 2 snooze 1h/4h/24h)

Determinismo §7.9: niente LLM, schema fisso, init idempotente.
Storage: `~/.local/share/metnos/github_state.sqlite`.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import config as _C  # §7.11 — rispetta METNOS_USER_DATA
DB_PATH = _C.PATH_USER_DATA / "github_state.sqlite"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_state (
    repo TEXT NOT NULL,
    kind TEXT NOT NULL,
    number INTEGER NOT NULL,
    last_event_id TEXT,
    last_check_ts INTEGER,
    snoozed_until INTEGER,
    PRIMARY KEY (repo, kind, number)
);
CREATE INDEX IF NOT EXISTS idx_watch_repo ON watch_state(repo);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Idempotent CREATE TABLE + INDEX. Safe to call at every job fire."""
    con = _connect()
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


def get_state(repo: str, kind: str, number: int) -> dict[str, Any] | None:
    """Ritorna row dict o None se assente. kind in {'issue','pr'}."""
    init_db()
    con = _connect()
    try:
        cur = con.execute(
            "SELECT repo,kind,number,last_event_id,last_check_ts,snoozed_until "
            "FROM watch_state WHERE repo=? AND kind=? AND number=?",
            (repo, kind, int(number)),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def set_state(
    repo: str,
    kind: str,
    number: int,
    *,
    last_event_id: str | None = None,
    last_check_ts: int | None = None,
    snoozed_until: int | None = None,
) -> None:
    """UPSERT. last_check_ts default = now() se non passato."""
    init_db()
    ts = int(last_check_ts) if last_check_ts is not None else int(time.time())
    con = _connect()
    try:
        # Read existing (preserve fields not in this call).
        cur = con.execute(
            "SELECT last_event_id, snoozed_until FROM watch_state "
            "WHERE repo=? AND kind=? AND number=?",
            (repo, kind, int(number)),
        )
        row = cur.fetchone()
        prev_ev = row["last_event_id"] if row else None
        prev_sn = row["snoozed_until"] if row else None
        new_ev = last_event_id if last_event_id is not None else prev_ev
        new_sn = snoozed_until if snoozed_until is not None else prev_sn
        con.execute(
            "INSERT INTO watch_state (repo,kind,number,last_event_id,last_check_ts,snoozed_until) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(repo,kind,number) DO UPDATE SET "
            "last_event_id=excluded.last_event_id, "
            "last_check_ts=excluded.last_check_ts, "
            "snoozed_until=excluded.snoozed_until",
            (repo, kind, int(number), new_ev, ts, new_sn),
        )
        con.commit()
    finally:
        con.close()


def set_snooze(repo: str, kind: str, number: int, until_ts: int) -> None:
    """Snooze fino a epoch `until_ts`. Convenienza chiamata da callback dialog."""
    set_state(repo, kind, number, snoozed_until=int(until_ts))


def expired_snooze_cleanup() -> int:
    """Azzera `snoozed_until` per le righe il cui snooze e' scaduto.
    Ritorna il numero di righe aggiornate. Idempotente."""
    init_db()
    now = int(time.time())
    con = _connect()
    try:
        cur = con.execute(
            "UPDATE watch_state SET snoozed_until=NULL "
            "WHERE snoozed_until IS NOT NULL AND snoozed_until <= ?",
            (now,),
        )
        n = cur.rowcount or 0
        con.commit()
        return n
    finally:
        con.close()
