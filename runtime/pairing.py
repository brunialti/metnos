#!/usr/bin/env python3
"""pairing.py — riconoscimento channel+sender via codici firmati Ed25519.

Cap. 12 dell'Architettura: il pairing identifica un *channel+sender*, non una
persona fisica. Lo stesso familiare via Telegram e via Signal sono due
pairing distinti, ciascuno con il proprio livello di autonomia.

Flusso:
    1. Roberto genera un codice (CLI: `python3 -m pairing generate ReadOnly 5m`).
    2. Lo passa a chi vuole pairare (Telegram, voce, mail).
    3. La controparte invia `/pair PAIR.<...>.<...>` sul canale.
    4. Il daemon del canale chiama `consume_code(code, channel, sender_id)`.
    5. Il pairing viene registrato con il livello di autonomia del codice.

Il codice e' usabile una volta sola (registry `consumed_codes`) e ha TTL
breve di default (5 minuti). La firma Ed25519 garantisce che il codice
provenga davvero dalla chiave 'author' di Roberto (non auto-generato dal
sender stesso o da un attaccante).
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sign import KEYS_DIR, list_trusted_publics, load_private  # noqa: E402
import config as _C  # §7.11

DEFAULT_DB_PATH = _C.PATH_USER_STATE / "pairings.db"
DEFAULT_TTL_S = 300
VALID_LEVELS = ("ReadOnly", "Supervised", "Full")
CODE_PREFIX = "PAIR."
PROTOCOL_VERSION = 1


SCHEMA = """
CREATE TABLE IF NOT EXISTS pairings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    autonomy_level TEXT NOT NULL,
    paired_at TEXT NOT NULL,
    paired_by TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT,
    actor TEXT,
    display_name TEXT,
    UNIQUE(channel, sender_id)
);
CREATE TABLE IF NOT EXISTS consumed_codes (
    code_id TEXT PRIMARY KEY,
    consumed_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    sender_id TEXT NOT NULL
);
"""


@dataclass
class Pairing:
    channel: str
    sender_id: str
    autonomy_level: str
    paired_at: str
    paired_by: str
    last_seen: str | None = None
    revoked_at: str | None = None
    # Multi-user (1/5/2026): identificatore logico utente. Diverso da sender_id
    # (id Telegram) e da channel. Esempio: "host" (Roberto), "guest_iacopo".
    # Tutti i record runtime per-actor (locations.jsonl, undo.jsonl, mnestoma
    # events, ...) usano questo nome. Fallback se NULL: vedi actor_resolver.
    actor: str | None = None
    display_name: str | None = None


class PairingError(Exception):
    pass


# --- helpers --------------------------------------------------------------

def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


from timefmt import now_iso_z as _now_iso


def _open_db(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path or os.environ.get("METNOS_PAIRINGS_DB") or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migration idempotente: aggiungi colonne actor/display_name su DB
    # esistenti pre-multi-user (1/5/2026). ALTER TABLE ADD COLUMN e' non
    # destructive e veloce. La risoluzione iniziale del valore (host/guest_*)
    # avviene lazy in actor_resolver.resolve_actor.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pairings)").fetchall()}
    if "actor" not in cols:
        conn.execute("ALTER TABLE pairings ADD COLUMN actor TEXT")
    if "display_name" not in cols:
        conn.execute("ALTER TABLE pairings ADD COLUMN display_name TEXT")
    return conn


# --- generate / consume ---------------------------------------------------

def generate_code(autonomy_level: str, *, ttl_seconds: int = DEFAULT_TTL_S,
                  issued_by: str = "author") -> str:
    """Genera un codice firmato. Ritorna la stringa `PAIR.<payload>.<sig>`."""
    if autonomy_level not in VALID_LEVELS:
        raise ValueError(f"livello non valido: {autonomy_level} (uno di {VALID_LEVELS})")
    payload = {
        "v": PROTOCOL_VERSION,
        "id": uuid.uuid4().hex[:12],
        "autonomy": autonomy_level,
        "exp": int(time.time()) + ttl_seconds,
        "iss": issued_by,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    priv = load_private(issued_by)
    signature = priv.sign(payload_bytes)
    return f"{CODE_PREFIX}{_b64u_encode(payload_bytes)}.{_b64u_encode(signature)}"


def _verify_payload(code: str) -> dict:
    """Parse + verify firma. Ritorna il payload se valido, altrimenti solleva."""
    if not code.startswith(CODE_PREFIX):
        raise PairingError("formato codice invalido (atteso prefisso PAIR.)")
    body = code[len(CODE_PREFIX):]
    parts = body.split(".")
    if len(parts) != 2:
        raise PairingError("formato codice invalido (atteso PAIR.<payload>.<sig>)")
    try:
        payload_bytes = _b64u_decode(parts[0])
        sig = _b64u_decode(parts[1])
    except Exception as e:
        raise PairingError(f"decodifica base64 fallita: {e}")

    trusted = list_trusted_publics()
    if not trusted:
        raise PairingError(f"nessuna chiave trusted in {KEYS_DIR}")
    verified = False
    for _name, pub in trusted:
        try:
            pub.verify(sig, payload_bytes)
            verified = True
            break
        except Exception:
            continue
    if not verified:
        raise PairingError("firma codice non verificata")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise PairingError(f"payload non JSON valido: {e}")
    if payload.get("v") != PROTOCOL_VERSION:
        raise PairingError(f"versione protocollo non supportata: {payload.get('v')}")
    if payload.get("autonomy") not in VALID_LEVELS:
        raise PairingError(f"livello autonomia invalido: {payload.get('autonomy')}")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise PairingError("codice scaduto")
    return payload


def consume_code(code: str, channel: str, sender_id: str,
                 *, db_path: Path | None = None) -> Pairing:
    """Verifica codice, registra pairing, marca codice come consumato.

    Atomico: se il codice e' gia' stato consumato (anche da altri), solleva.
    """
    payload = _verify_payload(code)
    code_id = payload["id"]
    autonomy = payload["autonomy"]
    issued_by = payload.get("iss", "author")
    now = _now_iso()

    conn = _open_db(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT 1 FROM consumed_codes WHERE code_id = ?", (code_id,)).fetchone()
            if row:
                raise PairingError("codice gia' consumato")
            conn.execute(
                "INSERT INTO consumed_codes (code_id, consumed_at, channel, sender_id) VALUES (?,?,?,?)",
                (code_id, now, channel, sender_id),
            )
            # upsert pairing
            conn.execute(
                """INSERT INTO pairings (channel, sender_id, autonomy_level, paired_at, paired_by, revoked_at)
                   VALUES (?,?,?,?,?,NULL)
                   ON CONFLICT(channel, sender_id) DO UPDATE SET
                     autonomy_level=excluded.autonomy_level,
                     paired_at=excluded.paired_at,
                     paired_by=excluded.paired_by,
                     revoked_at=NULL""",
                (channel, sender_id, autonomy, now, issued_by),
            )
        row = conn.execute(
            "SELECT * FROM pairings WHERE channel = ? AND sender_id = ?",
            (channel, sender_id),
        ).fetchone()
        return Pairing(**{k: row[k] for k in row.keys() if k != "id"})
    finally:
        conn.close()


# --- query / mutate -------------------------------------------------------

def get_pairing(channel: str, sender_id: str, *,
                db_path: Path | None = None) -> Pairing | None:
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM pairings WHERE channel=? AND sender_id=? AND revoked_at IS NULL",
            (channel, sender_id),
        ).fetchone()
        if not row:
            return None
        return Pairing(**{k: row[k] for k in row.keys() if k != "id"})
    finally:
        conn.close()


def is_paired(channel: str, sender_id: str, *, db_path: Path | None = None) -> bool:
    return get_pairing(channel, sender_id, db_path=db_path) is not None


def get_autonomy(channel: str, sender_id: str, *,
                 db_path: Path | None = None) -> str | None:
    p = get_pairing(channel, sender_id, db_path=db_path)
    return p.autonomy_level if p else None


def touch_last_seen(channel: str, sender_id: str, *,
                    db_path: Path | None = None) -> None:
    conn = _open_db(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE pairings SET last_seen=? WHERE channel=? AND sender_id=?",
                (_now_iso(), channel, sender_id),
            )
    finally:
        conn.close()


def list_pairings(*, include_revoked: bool = False,
                  db_path: Path | None = None) -> list[Pairing]:
    conn = _open_db(db_path)
    try:
        if include_revoked:
            rows = conn.execute("SELECT * FROM pairings ORDER BY paired_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pairings WHERE revoked_at IS NULL ORDER BY paired_at DESC"
            ).fetchall()
        return [Pairing(**{k: r[k] for k in r.keys() if k != "id"}) for r in rows]
    finally:
        conn.close()


def revoke(channel: str, sender_id: str, *,
           db_path: Path | None = None) -> bool:
    conn = _open_db(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE pairings SET revoked_at=? WHERE channel=? AND sender_id=? AND revoked_at IS NULL",
                (_now_iso(), channel, sender_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def bootstrap_default_chat_id(channel: str, sender_id: str, *,
                              db_path: Path | None = None) -> Pairing:
    """Auto-pair come Full il default_chat_id alla prima volta che scrive,
    SE non esistono altri pairing per quel canale. Per il bootstrap dev:
    Roberto non deve generarsi un codice da solo per il proprio chat."""
    conn = _open_db(db_path)
    try:
        existing = conn.execute(
            "SELECT 1 FROM pairings WHERE channel=? LIMIT 1", (channel,),
        ).fetchone()
        if existing:
            raise PairingError("bootstrap rifiutato: esistono gia' pairing per questo canale")
        now = _now_iso()
        with conn:
            conn.execute(
                """INSERT INTO pairings (channel, sender_id, autonomy_level, paired_at, paired_by)
                   VALUES (?,?,?,?,?)""",
                (channel, sender_id, "Full", now, "bootstrap"),
            )
        row = conn.execute(
            "SELECT * FROM pairings WHERE channel=? AND sender_id=?",
            (channel, sender_id),
        ).fetchone()
        return Pairing(**{k: row[k] for k in row.keys() if k != "id"})
    finally:
        conn.close()


# --- CLI ------------------------------------------------------------------

def _parse_ttl(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)


def _cli(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        print("\nComandi: generate <level> [ttl] | consume <code> <channel> <sender_id> | "
              "list [--include-revoked] | revoke <channel> <sender_id>")
        return 2
    cmd = argv[0]
    if cmd == "generate":
        if len(argv) < 2:
            print("Usage: generate <ReadOnly|Supervised|Full> [ttl=5m]"); return 2
        level = argv[1]
        ttl = _parse_ttl(argv[2]) if len(argv) > 2 else DEFAULT_TTL_S
        code = generate_code(level, ttl_seconds=ttl)
        print(code)
        return 0
    if cmd == "consume":
        if len(argv) < 4:
            print("Usage: consume <code> <channel> <sender_id>"); return 2
        p = consume_code(argv[1], argv[2], argv[3])
        print(json.dumps(asdict(p), ensure_ascii=False))
        return 0
    if cmd == "list":
        include = "--include-revoked" in argv
        for p in list_pairings(include_revoked=include):
            print(json.dumps(asdict(p), ensure_ascii=False))
        return 0
    if cmd == "revoke":
        if len(argv) < 3:
            print("Usage: revoke <channel> <sender_id>"); return 2
        ok = revoke(argv[1], argv[2])
        print("revoked" if ok else "no-op (pairing inesistente o gia' revocato)")
        return 0 if ok else 1
    print(f"comando sconosciuto: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
