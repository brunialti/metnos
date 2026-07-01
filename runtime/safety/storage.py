"""storage.py — SQLite-backed safety signatures store (ADR 0071).

Two tables:
  safety_signatures  — one row per signature, with kind/severity/source/uses.
  safety_meta        — one row per applied seed bootstrap (audit trail).

Public class `SafetyStore` exposes a small CRUD surface plus the query helpers
needed by `find_signatures_*` and `write_signatures_*` executors.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11 — rispetta METNOS_USER_STATE
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "SAFETY_DB_PATH",
        str(_C.PATH_USER_STATE / "safety.db"),
    )
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS safety_signatures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signature     TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK (kind IN
                  ('whitelist','blacklist','graylist','forbidden')),
    severity      TEXT CHECK (severity IN
                  ('forbidden','irreversible','dangerous','reversible')),
    source        TEXT NOT NULL CHECK (source IN
                  ('seed','user','auto-promoted')),
    uses          INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT,
    weight        REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL DEFAULT (
                      strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by    TEXT,
    reason        TEXT,
    seed_version  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_safety_kind   ON safety_signatures(kind);
CREATE INDEX IF NOT EXISTS idx_safety_source ON safety_signatures(source);
CREATE INDEX IF NOT EXISTS idx_safety_used   ON safety_signatures(last_used_at);
CREATE INDEX IF NOT EXISTS idx_safety_binary ON safety_signatures(signature);

CREATE TABLE IF NOT EXISTS safety_meta (
    seed_version  INTEGER PRIMARY KEY,
    applied_at    TEXT NOT NULL DEFAULT (
                      strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    applied_count INTEGER,
    skipped_count INTEGER
);
"""


@dataclass
class SignatureRow:
    id: int
    signature: str
    kind: str            # whitelist | blacklist | graylist | forbidden
    severity: str | None
    source: str          # seed | user | auto-promoted
    uses: int
    last_used_at: str | None
    weight: float
    created_at: str
    created_by: str | None
    reason: str | None
    seed_version: int | None


class SafetyStore:
    """Thin CRUD wrapper around the SQLite-backed safety store."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ── lookups ───────────────────────────────────────────────────────

    def find_by_signature(self, signature: str) -> SignatureRow | None:
        row = self.conn.execute(
            "SELECT * FROM safety_signatures WHERE signature = ?",
            (signature,),
        ).fetchone()
        return _row_to_dc(row) if row else None

    def find_by_kind(self, kind: str) -> list[SignatureRow]:
        rows = self.conn.execute(
            "SELECT * FROM safety_signatures WHERE kind = ? ORDER BY signature",
            (kind,),
        ).fetchall()
        return [_row_to_dc(r) for r in rows]

    def find_promotion_candidates(
        self,
        *,
        min_uses: int = 5,
        max_age_days: int = 30,
    ) -> list[SignatureRow]:
        """Graylist entries with uses >= min_uses recently used.

        Used by `find_signatures_promotion_candidates` to surface graylist
        signatures ready for whitelist promotion.
        """
        rows = self.conn.execute(
            """
            SELECT * FROM safety_signatures
             WHERE kind = 'graylist'
               AND uses >= ?
               AND (last_used_at IS NULL OR
                    julianday('now') - julianday(last_used_at) <= ?)
             ORDER BY uses DESC
            """,
            (min_uses, max_age_days),
        ).fetchall()
        return [_row_to_dc(r) for r in rows]

    # ── mutations (user actions) ──────────────────────────────────────

    def upsert_user(
        self,
        signature: str,
        kind: str,
        *,
        severity: str | None = None,
        reason: str | None = None,
        created_by: str = "host",
    ) -> SignatureRow:
        """Insert or replace a signature with source='user'.

        User actions always override seed entries (intent: user curation
        wins). Subsequent seed bootstrap will skip this row.
        """
        self.conn.execute(
            """
            INSERT INTO safety_signatures
                (signature, kind, severity, source, reason, created_by)
            VALUES (?, ?, ?, 'user', ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
                kind = excluded.kind,
                severity = excluded.severity,
                source = 'user',
                reason = excluded.reason,
                created_by = excluded.created_by,
                created_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (signature, kind, severity, reason, created_by),
        )
        row = self.find_by_signature(signature)
        assert row is not None
        return row

    def upsert_seed(
        self,
        signature: str,
        kind: str,
        *,
        severity: str | None = None,
        reason: str | None = None,
        seed_version: int,
    ) -> bool:
        """Insert a seed entry. Returns False if existing row is source='user'.

        This is the only path that respects the user-curation invariant.
        """
        existing = self.find_by_signature(signature)
        if existing and existing.source == "user":
            return False
        self.conn.execute(
            """
            INSERT INTO safety_signatures
                (signature, kind, severity, source, reason,
                 created_by, seed_version)
            VALUES (?, ?, ?, 'seed', ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
                kind = excluded.kind,
                severity = excluded.severity,
                source = 'seed',
                reason = excluded.reason,
                created_by = excluded.created_by,
                seed_version = excluded.seed_version
            """,
            (signature, kind, severity, reason,
             f"seed_v{seed_version}", seed_version),
        )
        return True

    def upsert_auto_promoted(
        self,
        signature: str,
        kind: str,
        *,
        reason: str = "auto-promoted by Synt",
        severity: str | None = None,
    ) -> SignatureRow:
        """Synt-driven promotion (graylist → whitelist with uses≥5)."""
        self.conn.execute(
            """
            INSERT INTO safety_signatures
                (signature, kind, severity, source, reason, created_by)
            VALUES (?, ?, ?, 'auto-promoted', ?, 'synt')
            ON CONFLICT(signature) DO UPDATE SET
                kind = excluded.kind,
                source = 'auto-promoted',
                reason = excluded.reason
            """,
            (signature, kind, severity, reason),
        )
        row = self.find_by_signature(signature)
        assert row is not None
        return row

    def delete(self, signature: str) -> bool:
        """Remove a signature row entirely. Returns True if a row was removed."""
        cur = self.conn.execute(
            "DELETE FROM safety_signatures WHERE signature = ?",
            (signature,),
        )
        return cur.rowcount > 0

    def record_use(self, signature: str) -> int:
        """Increment uses + bump last_used_at on a hit. Returns new uses count."""
        self.conn.execute(
            """
            UPDATE safety_signatures
               SET uses = uses + 1,
                   last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
             WHERE signature = ?
            """,
            (signature,),
        )
        row = self.conn.execute(
            "SELECT uses FROM safety_signatures WHERE signature = ?",
            (signature,),
        ).fetchone()
        return int(row["uses"]) if row else 0

    # ── meta (seed bootstrap audit) ──────────────────────────────────

    def latest_seed_version(self) -> int:
        row = self.conn.execute(
            "SELECT MAX(seed_version) AS v FROM safety_meta"
        ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    def record_seed_application(
        self, *, seed_version: int, applied: int, skipped: int
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO safety_meta "
            "(seed_version, applied_count, skipped_count) "
            "VALUES (?, ?, ?)",
            (seed_version, applied, skipped),
        )

    # ── housekeeping ─────────────────────────────────────────────────

    def decay_inactive_graylist(self, *, max_inactivity_days: int = 30) -> int:
        """Demote graylist entries inactive for too long back to «unknown».

        Implementation note: «unknown» is the absence of a row, so demotion
        means deletion. We delete only entries that were graylist (never
        forbidden, never user-blacklist) AND are not source='user' (user
        curation wins always). Returns the number deleted.
        """
        cur = self.conn.execute(
            """
            DELETE FROM safety_signatures
             WHERE kind = 'graylist'
               AND source != 'user'
               AND (last_used_at IS NULL OR
                    julianday('now') - julianday(last_used_at) > ?)
            """,
            (max_inactivity_days,),
        )
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()

    # iteration support for diff tools
    def all_signatures(self) -> Iterator[SignatureRow]:
        for r in self.conn.execute(
            "SELECT * FROM safety_signatures ORDER BY signature"
        ):
            yield _row_to_dc(r)


def _row_to_dc(row: sqlite3.Row) -> SignatureRow:
    return SignatureRow(
        id=int(row["id"]),
        signature=str(row["signature"]),
        kind=str(row["kind"]),
        severity=row["severity"],
        source=str(row["source"]),
        uses=int(row["uses"]),
        last_used_at=row["last_used_at"],
        weight=float(row["weight"]),
        created_at=str(row["created_at"]),
        created_by=row["created_by"],
        reason=row["reason"],
        seed_version=row["seed_version"],
    )
