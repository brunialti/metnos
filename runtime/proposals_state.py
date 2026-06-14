"""proposals_state.py — quiescenza e re-emersione delle proposte introvertive.

Cura il ciclo `pending → dormant → reawaken` documentato in
`runtime/safety_seeds/proposals_quiescence.md`.

Storage: SQLite single-file in `~/.local/state/metnos/proposals_state.db`.

API pubblica:
  - touch_or_insert(sig_key, kind, last_uses)  — chiama al fire del job
  - is_dormant(sig_key)                        — query per get_proposals
  - mark_action(sig_key, action)               — futuro set_proposals
  - prune_old(days=180)                        — housekeeping (raro)
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config as _C  # §7.11 — rispetta METNOS_USER_STATE
DB_PATH = Path(
    os.environ.get(
        "METNOS_PROPOSALS_STATE_DB",
        str(_C.PATH_USER_STATE / "proposals_state.db"),
    )
)

DORMANCY_NIGHTS = int(os.environ.get("METNOS_PROPOSALS_DORMANCY_NIGHTS", "3"))
REEMERGE_FACTOR = float(os.environ.get("METNOS_PROPOSALS_REEMERGE_FACTOR", "1.30"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals_state (
    sig_key        TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'pending',
    first_seen     TEXT NOT NULL DEFAULT (
                       strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen      TEXT NOT NULL DEFAULT (
                       strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_uses      INTEGER NOT NULL DEFAULT 0,
    n_seen         INTEGER NOT NULL DEFAULT 1,
    dormant_since  TEXT,
    dormant_uses   INTEGER,
    last_action    TEXT,
    last_action_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_state ON proposals_state(state);
CREATE INDEX IF NOT EXISTS idx_proposals_kind  ON proposals_state(kind);
"""


def _open() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _canonical(sig_key) -> str:
    """Normalizza la chiave canonica in stringa serializzabile."""
    if isinstance(sig_key, str):
        return sig_key
    return json.dumps(list(sig_key) if isinstance(sig_key, tuple) else sig_key,
                       sort_keys=True, default=str)


@dataclass
class StateRow:
    sig_key: str
    kind: str
    state: str            # pending | dormant | applied | rejected | blocked
    first_seen: str
    last_seen: str
    last_uses: int
    n_seen: int
    dormant_since: str | None
    dormant_uses: int | None
    last_action: str | None
    last_action_at: str | None


def _row(r) -> StateRow:
    return StateRow(
        sig_key=r["sig_key"], kind=r["kind"], state=r["state"],
        first_seen=r["first_seen"], last_seen=r["last_seen"],
        last_uses=int(r["last_uses"] or 0), n_seen=int(r["n_seen"] or 0),
        dormant_since=r["dormant_since"], dormant_uses=r["dormant_uses"],
        last_action=r["last_action"], last_action_at=r["last_action_at"],
    )


def lookup(sig_key) -> StateRow | None:
    key = _canonical(sig_key)
    conn = _open()
    try:
        r = conn.execute(
            "SELECT * FROM proposals_state WHERE sig_key = ?", (key,)
        ).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def is_dormant(sig_key) -> bool:
    row = lookup(sig_key)
    return bool(row) and row.state == "dormant"


def touch_or_insert(sig_key, kind: str, last_uses: int) -> StateRow:
    """Chiamare al fire del task notturno per ogni candidato.

    Comportamento per stato corrente:
    - assente: INSERT con state='pending', n_seen=1.
    - 'pending': UPDATE n_seen+=1, last_uses, last_seen. Se n_seen ≥
      DORMANCY_NIGHTS e nessuna last_action presa, transition → 'dormant'.
    - 'dormant': UPDATE last_seen, last_uses. Se last_uses sale oltre
      dormant_uses * REEMERGE_FACTOR → torna 'pending'.
    - altri stati ('applied'/'rejected'/'blocked'): solo touch del
      last_seen e last_uses; nessuna transizione automatica.

    Restituisce la row aggiornata.
    """
    key = _canonical(sig_key)
    conn = _open()
    try:
        r = conn.execute(
            "SELECT * FROM proposals_state WHERE sig_key = ?", (key,)
        ).fetchone()
        if r is None:
            conn.execute(
                "INSERT INTO proposals_state "
                "(sig_key, kind, state, last_uses, n_seen) "
                "VALUES (?, ?, 'pending', ?, 1)",
                (key, kind, int(last_uses)),
            )
            conn.commit()
            r = conn.execute(
                "SELECT * FROM proposals_state WHERE sig_key = ?", (key,)
            ).fetchone()
            return _row(r)

        row = _row(r)
        new_state = row.state
        new_dormant_since = row.dormant_since
        new_dormant_uses = row.dormant_uses

        if row.state == "pending":
            new_n = row.n_seen + 1
            if new_n >= DORMANCY_NIGHTS and not row.last_action:
                new_state = "dormant"
                new_dormant_since = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                new_dormant_uses = int(last_uses)
            conn.execute(
                "UPDATE proposals_state SET "
                " last_seen   = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
                " last_uses   = ?, "
                " n_seen      = ?, "
                " state       = ?, "
                " dormant_since = COALESCE(?, dormant_since), "
                " dormant_uses  = COALESCE(?, dormant_uses) "
                "WHERE sig_key = ?",
                (int(last_uses), new_n, new_state,
                 new_dormant_since, new_dormant_uses, key),
            )
        elif row.state == "dormant":
            # Se il dato si rinforza oltre soglia, riemerge.
            # La riemersione e' un reset: n_seen torna a 1, cosi' la proposta
            # ha di nuovo DORMANCY_NIGHTS notti di visibilita' prima di
            # essere risopita. Senza il reset, andrebbe immediatamente
            # in dormant alla notte successiva (n_seen ereditato).
            base = max(int(row.dormant_uses or 0), 1)
            if last_uses > base * REEMERGE_FACTOR:
                conn.execute(
                    "UPDATE proposals_state SET "
                    " last_seen   = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
                    " last_uses   = ?, "
                    " n_seen      = 1, "
                    " state       = 'pending', "
                    " dormant_since = NULL, "
                    " dormant_uses  = NULL "
                    "WHERE sig_key = ?",
                    (int(last_uses), key),
                )
            else:
                conn.execute(
                    "UPDATE proposals_state SET "
                    " last_seen   = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
                    " last_uses   = ? "
                    "WHERE sig_key = ?",
                    (int(last_uses), key),
                )
        else:
            # Stati terminali (applied/rejected/blocked): solo touch.
            conn.execute(
                "UPDATE proposals_state SET "
                " last_seen = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
                " last_uses = ? "
                "WHERE sig_key = ?",
                (int(last_uses), key),
            )
        conn.commit()
        r = conn.execute(
            "SELECT * FROM proposals_state WHERE sig_key = ?", (key,)
        ).fetchone()
        return _row(r)
    finally:
        conn.close()


def mark_action(sig_key, action: str) -> StateRow | None:
    """Registra un'azione esplicita dell'utente (approve/reject/block).
    Setta `state` di conseguenza:
      approve → 'applied'
      reject  → 'dormant'  (sopisce, non rifiuta per sempre)
      block   → 'blocked'  (mai più riproposta)
    """
    if action not in ("approve", "reject", "block"):
        raise ValueError(f"action must be approve|reject|block, got {action!r}")
    target_state = {
        "approve": "applied",
        "reject":  "dormant",
        "block":   "blocked",
    }[action]
    key = _canonical(sig_key)
    conn = _open()
    try:
        conn.execute(
            "UPDATE proposals_state SET "
            " state = ?, "
            " last_action = ?, "
            " last_action_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
            " dormant_since = CASE WHEN ?='reject' "
            "                       THEN strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "                       ELSE dormant_since END, "
            " dormant_uses  = CASE WHEN ?='reject' THEN last_uses "
            "                       ELSE dormant_uses END "
            "WHERE sig_key = ?",
            (target_state, action, action, action, key),
        )
        conn.commit()
        r = conn.execute(
            "SELECT * FROM proposals_state WHERE sig_key = ?", (key,)
        ).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


