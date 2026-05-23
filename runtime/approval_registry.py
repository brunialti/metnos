"""approval_registry.py — registro delle richieste di approval pendenti.

Quando il Vaglio o la Policy decidono che un'azione critica richiede
approvazione esplicita di Roberto (oltre la guardia binaria, oltre il
giudice graduato), creano una pending request qui dentro: token UUID,
TTL breve, una carta di approval (vedi `channels.approval.render_approval_card`)
viene inviata sul canale di pairing dell'utente. La risposta dell'utente
(callback_query Telegram) risolve il token a "approved" o "rejected".

In v1.1 il Vaglio non solleva ancora approval (decide binario o graduato);
questo modulo e' l'infrastruttura pronta perche', quando il vaglio reale
generera' richieste di approval, il dispatcher possa registrarle e
risolverle. Vedi cap.5 della roadmap (fase 5).

Schema SQLite:
    pending(token PK, channel, sender_id, capability_class, action_verb,
            target_summary, created_at, expires_at, status, decision_at,
            decision_by_channel, decision_by_sender, request_extra)
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import config as _C  # §7.11

DEFAULT_DB_PATH = _C.PATH_USER_STATE / "approvals.db"
DEFAULT_TTL_S = 600  # 10 min: oltre, la richiesta scade

VALID_STATUS = ("pending", "approved", "rejected", "expired")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    token              TEXT PRIMARY KEY,
    channel            TEXT NOT NULL,
    sender_id          TEXT NOT NULL,
    capability_class   TEXT NOT NULL,
    action_verb        TEXT NOT NULL,
    target_summary     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    decision_at        TEXT,
    decision_by_channel TEXT,
    decision_by_sender  TEXT,
    request_extra      TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending(status);
CREATE INDEX IF NOT EXISTS idx_pending_expires ON pending(expires_at);
"""


@dataclass
class PendingRequest:
    token: str
    channel: str
    sender_id: str
    capability_class: str
    action_verb: str
    target_summary: str
    created_at: str
    expires_at: str
    status: str = "pending"
    decision_at: str | None = None
    decision_by_channel: str | None = None
    decision_by_sender: str | None = None
    request_extra: str | None = None


class ApprovalError(Exception):
    pass


# --- helpers ---------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch_to_iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _open_db(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path or os.environ.get("METNOS_APPROVALS_DB") or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row_to_pending(row: sqlite3.Row) -> PendingRequest:
    return PendingRequest(**{k: row[k] for k in row.keys()})


# --- API -------------------------------------------------------------------

def create_pending(
    *,
    channel: str,
    sender_id: str,
    capability_class: str,
    action_verb: str,
    target_summary: str,
    ttl_seconds: int = DEFAULT_TTL_S,
    request_extra: str | None = None,
    db_path: Path | None = None,
) -> PendingRequest:
    """Crea una richiesta pending. Ritorna il record con token UUID."""
    token = uuid.uuid4().hex[:16]
    now = time.time()
    rec = PendingRequest(
        token=token,
        channel=channel,
        sender_id=sender_id,
        capability_class=capability_class,
        action_verb=action_verb,
        target_summary=target_summary,
        created_at=_epoch_to_iso(now),
        expires_at=_epoch_to_iso(now + ttl_seconds),
        request_extra=request_extra,
    )
    conn = _open_db(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO pending (token, channel, sender_id, capability_class,
                       action_verb, target_summary, created_at, expires_at,
                       status, request_extra)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (rec.token, rec.channel, rec.sender_id, rec.capability_class,
                 rec.action_verb, rec.target_summary, rec.created_at,
                 rec.expires_at, "pending", rec.request_extra),
            )
        return rec
    finally:
        conn.close()


def get_pending(token: str, *, db_path: Path | None = None) -> PendingRequest | None:
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM pending WHERE token = ?", (token,),
        ).fetchone()
        return _row_to_pending(row) if row else None
    finally:
        conn.close()


def resolve(
    token: str,
    decision: str,
    *,
    by_channel: str,
    by_sender: str,
    db_path: Path | None = None,
) -> PendingRequest:
    """Applica la decisione (approved/rejected) a una pending request.

    Atomico: se la richiesta non e' piu' pending (gia' risolta o scaduta),
    solleva ApprovalError. Verifica anche che il sender che decide sia lo
    stesso del pairing che ha generato la richiesta &mdash; chi ha richiesto
    e' chi puo' approvare.
    """
    if decision not in ("approved", "rejected"):
        raise ApprovalError(f"decisione non valida: {decision}")
    now = _now_iso()
    conn = _open_db(db_path)
    try:
        # Conn aperta con isolation_level=None (autocommit). Niente `with conn`:
        # le mutazioni che precedono un raise devono restare persistite (es. la
        # transizione a 'expired' deve sopravvivere all'eccezione).
        row = conn.execute(
            "SELECT * FROM pending WHERE token = ?", (token,),
        ).fetchone()
        if not row:
            raise ApprovalError(f"token sconosciuto: {token}")
        if row["status"] != "pending":
            raise ApprovalError(
                f"token gia' risolto: status={row['status']}, decision_at={row['decision_at']}"
            )
        # Scadenza: marca come expired e solleva (UPDATE persistito)
        if row["expires_at"] < now:
            conn.execute(
                "UPDATE pending SET status='expired', decision_at=? WHERE token=?",
                (now, token),
            )
            raise ApprovalError(
                f"token scaduto: expires_at={row['expires_at']} < ora={now}"
            )
        # Verifica che il decisore sia lo stesso del richiedente
        if row["channel"] != by_channel or row["sender_id"] != by_sender:
            raise ApprovalError(
                f"decisore non autorizzato: pending da {row['channel']}/{row['sender_id']}, "
                f"decision da {by_channel}/{by_sender}"
            )
        conn.execute(
            """UPDATE pending SET status=?, decision_at=?,
                   decision_by_channel=?, decision_by_sender=?
                   WHERE token=?""",
            (decision, now, by_channel, by_sender, token),
        )
        row = conn.execute("SELECT * FROM pending WHERE token=?", (token,)).fetchone()
        return _row_to_pending(row)
    finally:
        conn.close()


def cleanup_expired(*, db_path: Path | None = None) -> int:
    """Marca come 'expired' tutte le pending request con expires_at < now.
    Ritorna il numero di richieste marcate."""
    now = _now_iso()
    conn = _open_db(db_path)
    try:
        with conn:
            cur = conn.execute(
                """UPDATE pending SET status='expired', decision_at=?
                       WHERE status='pending' AND expires_at < ?""",
                (now, now),
            )
            return cur.rowcount
    finally:
        conn.close()


def list_pending(
    *,
    include_resolved: bool = False,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[PendingRequest]:
    conn = _open_db(db_path)
    try:
        if include_resolved:
            rows = conn.execute(
                "SELECT * FROM pending ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pending WHERE status='pending' ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_pending(r) for r in rows]
    finally:
        conn.close()


# --- CLI -------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="Metnos approval registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="Lista pending requests")
    p_list.add_argument("--all", action="store_true", help="Include anche risolte/scadute")
    p_list.add_argument("--limit", type=int, default=20)
    p_cleanup = sub.add_parser("cleanup", help="Marca expired le pending oltre TTL")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for r in list_pending(include_resolved=args.all, limit=args.limit):
            print(_json.dumps(asdict(r), ensure_ascii=False))
        return 0
    if args.cmd == "cleanup":
        n = cleanup_expired()
        print(f"expired: {n}")
        return 0
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
