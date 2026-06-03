"""Storage sidecar per gli stati del promoter daemon.

Synth proposals vivono come JSON in `~/.local/share/metnos/synt_proposals/`
(field `final_state` = `synthesized|abandoned|...`). Il promoter aggiunge
uno stato di lifecycle SEPARATO tracciato in sqlite, una row per
proposal_id, cosi' i JSON restano immutati e i campi di lifecycle non
mescolano il dominio synt con quello del promoter.

Schema (idempotente via `CREATE TABLE IF NOT EXISTS`):

    CREATE TABLE proposal_promote (
        proposal_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        state TEXT NOT NULL,          -- pending | promoted_grace |
                                       --  promoted_finalized | rolled_back |
                                       --  archived | review_needed
        promoted_at TEXT,             -- ISO UTC
        grace_until TEXT,             -- ISO UTC (end of grace window)
        rollback_blob_path TEXT,
        evaluator_verdict TEXT,       -- JSON serializzato del verdict
        practical_example TEXT,       -- markdown rendering deterministico
        notified_at TEXT,             -- ISO, popolato dal digest job
        notified_ack TEXT,            -- ISO, popolato da callback Telegram ok
        rolled_back_at TEXT,          -- ISO
        finalized_at TEXT,            -- ISO
        archived_at TEXT,             -- ISO
        created_at TEXT NOT NULL,     -- ISO (insert time)
        needs_human_review INTEGER NOT NULL DEFAULT 0
    );

Determinismo §7.9: pure sqlite + datetime, niente LLM.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11


_DEFAULT_DB = _C.PATH_USER_DATA / "promoter.sqlite"
_DEFAULT_AUDIT_DIR = _C.PATH_USER_DATA / "synth_audit"


def _db_path() -> Path:
    env = os.environ.get("METNOS_PROMOTER_DB")
    return Path(env) if env else _DEFAULT_DB


def _audit_dir() -> Path:
    env = os.environ.get("METNOS_PROMOTER_AUDIT_DIR")
    return Path(env) if env else _DEFAULT_AUDIT_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_iso_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_promote (
    proposal_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    promoted_at TEXT,
    grace_until TEXT,
    rollback_blob_path TEXT,
    evaluator_verdict TEXT,
    practical_example TEXT,
    notified_at TEXT,
    notified_ack TEXT,
    rolled_back_at TEXT,
    finalized_at TEXT,
    archived_at TEXT,
    created_at TEXT NOT NULL,
    needs_human_review INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_promote_state ON proposal_promote(state);
"""

# Colonne attese dallo schema, per migration idempotente sui DB legacy.
_REQUIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("promoted_at", "TEXT"),
    ("grace_until", "TEXT"),
    ("rollback_blob_path", "TEXT"),
    ("evaluator_verdict", "TEXT"),
    ("practical_example", "TEXT"),
    ("notified_at", "TEXT"),
    ("notified_ack", "TEXT"),
    ("rolled_back_at", "TEXT"),
    ("finalized_at", "TEXT"),
    ("archived_at", "TEXT"),
    ("needs_human_review", "INTEGER NOT NULL DEFAULT 0"),
)


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    """Crea schema + migrate colonne mancanti. Ritorna lista colonne aggiunte."""
    conn.executescript(SCHEMA)
    conn.commit()
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(proposal_promote)"
    ).fetchall()}
    added: list[str] = []
    for col_name, col_type in _REQUIRED_COLUMNS:
        if col_name not in cols:
            conn.execute(
                f"ALTER TABLE proposal_promote ADD COLUMN {col_name} {col_type}"
            )
            added.append(col_name)
    if added:
        conn.commit()
    return added


def _open() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def load_proposal_state(proposal_id: str) -> dict | None:
    if not proposal_id:
        return None
    conn = _open()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM proposal_promote WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_pending(proposal_id: str, name: str) -> None:
    """Crea row in stato pending (per proposte non ancora promote)."""
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO proposal_promote "
            "(proposal_id, name, state, created_at) VALUES (?, ?, 'pending', ?)",
            (proposal_id, name, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_promoted_grace(
    *, proposal_id: str, name: str, blob_path: str,
    verdict: dict, practical_example: str, grace_hours: int,
) -> str:
    """Marca proposta come `promoted_grace` con grace_until = now+grace_hours.

    Se `grace_hours == 0` → state='promoted_finalized' direttamente
    (modalita' full-auto, niente notifica admin grace).
    Ritorna l'ISO grace_until calcolato (o "" se finalized direttamente).
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    if grace_hours <= 0:
        state = "promoted_finalized"
        grace_iso = ""
        finalized_iso = _now_iso()
    else:
        state = "promoted_grace"
        grace_iso = (now + timedelta(hours=grace_hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        finalized_iso = None
    verdict_json = json.dumps(verdict, ensure_ascii=False, default=str)
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO proposal_promote
                (proposal_id, name, state, promoted_at, grace_until,
                 rollback_blob_path, evaluator_verdict, practical_example,
                 finalized_at, created_at, needs_human_review)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(proposal_id) DO UPDATE SET
                name = excluded.name,
                state = excluded.state,
                promoted_at = excluded.promoted_at,
                grace_until = excluded.grace_until,
                rollback_blob_path = excluded.rollback_blob_path,
                evaluator_verdict = excluded.evaluator_verdict,
                practical_example = excluded.practical_example,
                finalized_at = excluded.finalized_at,
                needs_human_review = 0
            """,
            (proposal_id, name, state, _now_iso(), grace_iso,
             blob_path, verdict_json, practical_example,
             finalized_iso, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return grace_iso


def upsert_review_needed(
    *, proposal_id: str, name: str,
    verdict: dict, practical_example: str,
) -> None:
    verdict_json = json.dumps(verdict, ensure_ascii=False, default=str)
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO proposal_promote
                (proposal_id, name, state, evaluator_verdict,
                 practical_example, created_at, needs_human_review)
            VALUES (?, ?, 'review_needed', ?, ?, ?, 1)
            ON CONFLICT(proposal_id) DO UPDATE SET
                state = 'review_needed',
                evaluator_verdict = excluded.evaluator_verdict,
                practical_example = excluded.practical_example,
                needs_human_review = 1
            """,
            (proposal_id, name, verdict_json, practical_example, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_archived(
    *, proposal_id: str, name: str, verdict: dict,
) -> None:
    verdict_json = json.dumps(verdict, ensure_ascii=False, default=str)
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO proposal_promote
                (proposal_id, name, state, evaluator_verdict,
                 archived_at, created_at)
            VALUES (?, ?, 'archived', ?, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                state = 'archived',
                evaluator_verdict = excluded.evaluator_verdict,
                archived_at = excluded.archived_at
            """,
            (proposal_id, name, verdict_json, _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def mark_rolled_back(proposal_id: str) -> None:
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            "UPDATE proposal_promote SET state = 'rolled_back', "
            "rolled_back_at = ? WHERE proposal_id = ?",
            (_now_iso(), proposal_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_finalized(proposal_id: str) -> bool:
    """Marca una row `promoted_grace` come `promoted_finalized`.

    Usato dalla review form admin (E3) quando l'admin conferma una
    promozione esplicitamente prima della grace expiry. Idempotente:
    no-op se la row e' gia' finalized; ritorna False se la row e' in
    altri stati o non esiste.
    """
    if not proposal_id:
        return False
    conn = _open()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT state FROM proposal_promote WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return False
        cur = row["state"] or ""
        if cur == "promoted_finalized":
            return True
        if cur != "promoted_grace":
            return False
        conn.execute(
            "UPDATE proposal_promote SET state = 'promoted_finalized', "
            "finalized_at = ? WHERE proposal_id = ?",
            (_now_iso(), proposal_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def resurrect_from_archive(proposal_id: str) -> bool:
    """Riporta una proposta `archived` o `rolled_back` in stato `review_needed`.

    Usato dalla review form admin (E3) quando l'admin contesta un archive
    (evaluator reject discutibile) OPPURE un ritiro del kill-switch grace (L3.5,
    falso positivo: executor buono ritirato per errori transienti). Idempotente:
    False se la row non esiste o non e' in (`archived`|`rolled_back`).
    NB: rimette in review; il re-add al catalog avviene via re-promozione.
    """
    if not proposal_id:
        return False
    conn = _open()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT state FROM proposal_promote WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return False
        if (row["state"] or "") not in ("archived", "rolled_back"):
            return False
        conn.execute(
            "UPDATE proposal_promote SET state = 'review_needed', "
            "needs_human_review = 1, archived_at = NULL, rolled_back_at = NULL "
            "WHERE proposal_id = ?",
            (proposal_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def archive_review_needed(proposal_id: str) -> bool:
    """Marca una row `review_needed` come `archived` (admin decisione).

    Usato dalla review form admin (E3) quando l'admin sceglie di
    archiviare una proposta in attesa di revisione. Idempotente.
    """
    if not proposal_id:
        return False
    conn = _open()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT state FROM proposal_promote WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return False
        cur = row["state"] or ""
        if cur == "archived":
            return True
        if cur != "review_needed":
            return False
        conn.execute(
            "UPDATE proposal_promote SET state = 'archived', "
            "archived_at = ?, needs_human_review = 0 "
            "WHERE proposal_id = ?",
            (_now_iso(), proposal_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def archived_within_days(days: int, *, limit: int = 200) -> list[dict]:
    """Lista row `archived` con `archived_at >= now - days`.

    Usato dalla review form per la sezione "Bocciati recenti".
    """
    if days <= 0:
        return []
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _open()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM proposal_promote "
            "WHERE state = 'archived' AND archived_at IS NOT NULL "
            "AND archived_at >= ? "
            "ORDER BY archived_at DESC LIMIT ?",
            (cutoff_iso, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def expire_grace(now_iso: str | None = None) -> list[str]:
    """Promuove a `promoted_finalized` le proposte con `grace_until` < now.

    Ritorna lista dei proposal_id finalizzati.
    """
    target_now = now_iso or _now_iso()
    finalized: list[str] = []
    conn = _open()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT proposal_id FROM proposal_promote "
            "WHERE state = 'promoted_grace' AND grace_until IS NOT NULL "
            "AND grace_until < ?",
            (target_now,),
        ).fetchall()
        for row in rows:
            finalized.append(row["proposal_id"])
        if finalized:
            conn.executemany(
                "UPDATE proposal_promote SET state = 'promoted_finalized', "
                "finalized_at = ? WHERE proposal_id = ?",
                [(target_now, pid) for pid in finalized],
            )
            conn.commit()
    finally:
        conn.close()
    return finalized


def list_by_state(states: list[str], *, limit: int = 200) -> list[dict]:
    """Lista row in uno degli stati elencati, newest-first."""
    if not states:
        return []
    conn = _open()
    try:
        ensure_schema(conn)
        placeholders = ", ".join(["?"] * len(states))
        rows = conn.execute(
            f"SELECT * FROM proposal_promote WHERE state IN ({placeholders}) "
            f"ORDER BY COALESCE(promoted_at, created_at) DESC LIMIT ?",
            (*states, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def pending_notification() -> list[dict]:
    """Row in `promoted_grace` con `notified_at IS NULL`. Per digest task."""
    conn = _open()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM proposal_promote "
            "WHERE state = 'promoted_grace' AND notified_at IS NULL "
            "ORDER BY promoted_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_notified(proposal_id: str) -> None:
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            "UPDATE proposal_promote SET notified_at = ? "
            "WHERE proposal_id = ?",
            (_now_iso(), proposal_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_acked(proposal_id: str) -> None:
    conn = _open()
    try:
        ensure_schema(conn)
        conn.execute(
            "UPDATE proposal_promote SET notified_ack = ? "
            "WHERE proposal_id = ?",
            (_now_iso(), proposal_id),
        )
        conn.commit()
    finally:
        conn.close()


def audit_append(event: dict) -> Path:
    """JSONL append-only su `<audit_dir>/promoter_<YYYY-MM-DD>.jsonl`."""
    d = _audit_dir()
    d.mkdir(parents=True, exist_ok=True)
    audit_path = d / f"promoter_{_today_iso_date()}.jsonl"
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True,
                            default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return audit_path


__all__ = [
    "ensure_schema",
    "load_proposal_state",
    "insert_pending",
    "upsert_promoted_grace",
    "upsert_review_needed",
    "upsert_archived",
    "mark_rolled_back",
    "expire_grace",
    "list_by_state",
    "pending_notification",
    "mark_notified",
    "mark_acked",
    "audit_append",
    "mark_finalized",
    "resurrect_from_archive",
    "archive_review_needed",
    "archived_within_days",
]
