"""SQLite storage for scheduler v2.

Single table `schedule_entries` (recurring + one-shot share the schema; the
`recurring` flag distinguishes), plus `runs` for execution history.
WAL mode for concurrent readers (HTTP server + scheduler loop both use it).

All DB I/O is funnelled here. Daemon code never touches sqlite directly.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .models import Run, ScheduleEntry


# §7.11: rispetta METNOS_USER_STATE per isolamento test/e2e via config.
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C
DEFAULT_DB_PATH = _C.PATH_USER_STATE / "scheduler_v2.sqlite"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_entries (
  id              INTEGER PRIMARY KEY,
  name            TEXT UNIQUE,
  trigger         TEXT NOT NULL,
  next_fire_at    REAL NOT NULL,
  recurring       INTEGER NOT NULL,
  callback_key    TEXT NOT NULL,
  payload         TEXT NOT NULL DEFAULT '{}',
  weekdays        TEXT NOT NULL DEFAULT '',
  expires_at      TEXT NOT NULL DEFAULT '',
  remaining_runs  INTEGER NOT NULL DEFAULT 0,
  enabled         INTEGER NOT NULL DEFAULT 1,
  timeout_s       INTEGER,
  is_async        INTEGER NOT NULL DEFAULT 0,
  max_concurrent  INTEGER NOT NULL DEFAULT 1,
  grace_window_s  INTEGER,
  origin          TEXT NOT NULL DEFAULT 'system',
  label           TEXT NOT NULL DEFAULT '',
  source_command  TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  last_run_at     TEXT,
  last_status     TEXT,
  last_duration_ms INTEGER,
  last_error      TEXT,
  total_runs      INTEGER NOT NULL DEFAULT 0,
  total_failures  INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  description     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS entries_due_idx ON schedule_entries(enabled, next_fire_at);

CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY,
  entry_id      INTEGER,
  entry_name    TEXT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL,
  duration_ms   INTEGER,
  output        TEXT
);
CREATE INDEX IF NOT EXISTS runs_started_idx ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS runs_entry_idx ON runs(entry_name, started_at DESC);
CREATE INDEX IF NOT EXISTS runs_running_idx ON runs(status) WHERE status='running';
"""


from timefmt import now_iso_offset as _utc_iso


class SchedulerStorage:
    """Thread-safe SQLite wrapper for scheduler v2.

    One connection per instance (sqlite3 is thread-safe in serialized mode by
    default since Python 3.12; we still hold a Lock for atomic compound ops).
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # WAL ammette UN solo writer: daemon (conn long-lived) e client (conn
        # fresca per chiamata, es. resume_job/toggle_job dal processo telegram)
        # scrivono lo stesso DB. Senza busy_timeout sqlite alza subito
        # "database is locked"; con esso il writer attende invece di fallire.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_additive()

    def _migrate_additive(self) -> None:
        """Migrazioni additive idempotenti per DB pre-esistenti (CREATE TABLE
        IF NOT EXISTS non aggiunge colonne nuove). Controlla le colonne ESISTENTI
        una volta (PRAGMA) e ALTER solo quelle mancanti: nessun ALTER-che-fallisce
        ad ogni open (get_storage apre una conn fresca per ogni chiamata client)."""
        _add_cols = (
            ("consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
        )
        existing = {
            r[1] for r in self._conn.execute(
                "PRAGMA table_info(schedule_entries)")
        }
        for col, decl in _add_cols:
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE schedule_entries ADD COLUMN {col} {decl}"
                )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # --- entries -----------------------------------------------------

    def upsert(self, entry: ScheduleEntry) -> int:
        """Insert if name absent, otherwise update in place. Returns id."""
        now = _utc_iso()
        if not entry.created_at:
            entry.created_at = now
        entry.updated_at = now
        row = entry.to_row()
        # Drop None id so AUTOINCREMENT handles it
        row.pop("id", None)
        cols = list(row.keys())
        placeholders = ",".join(":" + c for c in cols)
        update_assigns = ",".join(f"{c}=excluded.{c}" for c in cols if c != "name")
        sql = (
            f"INSERT INTO schedule_entries ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(name) DO UPDATE SET {update_assigns}"
        )
        with self._lock:
            cur = self._conn.execute(sql, row)
            cur = self._conn.execute(
                "SELECT id FROM schedule_entries WHERE name=?", (entry.name,)
            )
            r = cur.fetchone()
            entry.id = int(r["id"])
            return entry.id

    def delete(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM schedule_entries WHERE name=?", (name,)
            )
            return cur.rowcount > 0

    def get_by_name(self, name: str) -> ScheduleEntry | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM schedule_entries WHERE name=?", (name,)
            )
            r = cur.fetchone()
        return ScheduleEntry.from_row(r) if r else None

    def get_by_id(self, entry_id: int) -> ScheduleEntry | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM schedule_entries WHERE id=?", (entry_id,)
            )
            r = cur.fetchone()
        return ScheduleEntry.from_row(r) if r else None

    def list_all(self) -> list[ScheduleEntry]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM schedule_entries ORDER BY next_fire_at"
            )
            rows = cur.fetchall()
        return [ScheduleEntry.from_row(r) for r in rows]

    def fetch_due(self, now_epoch: float, limit: int = 100) -> list[ScheduleEntry]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM schedule_entries "
                "WHERE enabled=1 AND next_fire_at<=? "
                "ORDER BY next_fire_at LIMIT ?",
                (now_epoch, limit),
            )
            rows = cur.fetchall()
        return [ScheduleEntry.from_row(r) for r in rows]

    def next_fire_at_min(self) -> float | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT MIN(next_fire_at) AS m FROM schedule_entries WHERE enabled=1"
            )
            r = cur.fetchone()
        return float(r["m"]) if r and r["m"] is not None else None

    def update_next_fire(self, entry_id: int, next_fire_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedule_entries SET next_fire_at=?, updated_at=? WHERE id=?",
                (next_fire_at, _utc_iso(), entry_id),
            )

    def disable(self, entry_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedule_entries SET enabled=0, updated_at=? WHERE id=?",
                (_utc_iso(), entry_id),
            )

    def enable(self, entry_id: int) -> None:
        """Riabilita un timer e RICALCOLA next_fire_at dal trigger, così non
        spara un catch-up immediato per il tempo trascorso da disabilitato.
        Azzera anche `consecutive_failures` (riabilitare = ripartenza pulita:
        evita che il circuit-breaker riscatti dopo un solo fallimento residuo).
        Invariante centralizzato qui, riusato da client.{enable,resume_job}."""
        import time as _time
        from .schedule_parser import next_fire_at as _nf
        with self._lock:
            cur = self._conn.execute(
                "SELECT trigger FROM schedule_entries WHERE id=?", (entry_id,)
            )
            r = cur.fetchone()
            nf = None
            if r and r["trigger"]:
                try:
                    nf = _nf(r["trigger"], _time.time())
                except Exception:
                    nf = None
            if nf is not None:
                self._conn.execute(
                    "UPDATE schedule_entries SET enabled=1, consecutive_failures=0, "
                    "next_fire_at=?, updated_at=? WHERE id=?",
                    (nf, _utc_iso(), entry_id),
                )
            else:
                self._conn.execute(
                    "UPDATE schedule_entries SET enabled=1, consecutive_failures=0, "
                    "updated_at=? WHERE id=?", (_utc_iso(), entry_id),
                )

    def record_outcome(
        self,
        entry_id: int,
        *,
        status: str,
        duration_ms: int,
        error: str | None,
        next_fire_at: float | None,
        decrement_remaining: bool,
        disable: bool,
    ) -> None:
        """Update entry stats post-fire. Optionally schedule next or disable."""
        sets = [
            "last_run_at=?",
            "last_status=?",
            "last_duration_ms=?",
            "last_error=?",
            "total_runs=total_runs+1",
            "updated_at=?",
        ]
        params: list = [_utc_iso(), status, duration_ms, error, _utc_iso()]
        if status != "success":
            sets.append("total_failures=total_failures+1")
            # Streak consecutiva: incrementa su error/timeout.
            sets.append("consecutive_failures=consecutive_failures+1")
        else:
            # Un solo success azzera la streak (LWW).
            sets.append("consecutive_failures=0")
        if decrement_remaining:
            sets.append("remaining_runs=MAX(0, remaining_runs-1)")
        if next_fire_at is not None:
            sets.append("next_fire_at=?")
            params.append(next_fire_at)
        if disable:
            sets.append("enabled=0")
        params.append(entry_id)
        sql = f"UPDATE schedule_entries SET {','.join(sets)} WHERE id=?"
        with self._lock:
            self._conn.execute(sql, params)

    # --- runs --------------------------------------------------------

    def begin_run(self, entry_id: int | None, entry_name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (entry_id, entry_name, started_at, status) "
                "VALUES (?, ?, ?, 'running')",
                (entry_id, entry_name, _utc_iso()),
            )
            return int(cur.lastrowid)

    def end_run(
        self,
        run_id: int,
        *,
        status: str,
        duration_ms: int,
        output: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at=?, status=?, duration_ms=?, output=? "
                "WHERE id=?",
                (_utc_iso(), status, duration_ms, output, run_id),
            )

    def mark_crashed_runs(self) -> int:
        """Any run still in 'running' at boot = crashed (process died mid-fire)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status='crashed', finished_at=? "
                "WHERE status='running'",
                (_utc_iso(),),
            )
            return cur.rowcount

    def list_runs(self, limit: int = 100,
                  entry_name: str | None = None) -> list[Run]:
        """Ultimi N run, opz. filtrati per entry_name lato SQL (con indice):
        evita il fetch-then-filter di 500 righe + il cap silenzioso quando il
        chiamante chiede limit>500 per un nome (§2.7)."""
        with self._lock:
            if entry_name is not None:
                cur = self._conn.execute(
                    "SELECT * FROM runs WHERE entry_name=? "
                    "ORDER BY started_at DESC LIMIT ?", (entry_name, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )
            rows = cur.fetchall()
        return [Run.from_row(r) for r in rows]

    def is_wal_mode(self) -> bool:
        with self._lock:
            cur = self._conn.execute("PRAGMA journal_mode")
            r = cur.fetchone()
        return (r[0] or "").lower() == "wal"
