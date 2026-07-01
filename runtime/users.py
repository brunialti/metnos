"""users — gestione multi-utente di Metnos (host + guest).

Sprint 4/5/2026, ADR 0083. Estende il modello "host + guest" del 27/4/2026
(`metnos_host_guest_model.md`) con un registro persistente degli utenti
logici e dei loro canali pairati.

Distinzione vs `pairing.py`:
- `pairing.py` mappa (channel, sender_id) → autonomy + actor_string. Sa di
  un canale specifico (Telegram chat_id), non di una persona.
- `users.py` mappa user logici (host + guest) → uno o piu' canali pairati.
  Sa di chi e' Lucia, indipendentemente dal canale.

Una stessa persona puo' avere piu' canali (Telegram + email + http remote
device); un name e' la chiave umana, un id uuid hex la chiave tecnica.

Storage: SQLite in `~/.local/share/metnos/users.db`.

Bootstrap (the design guide §2.4 robustezza al confine NL→determinismo):
Al primo `init_db()` se non esistono utenti viene creato lo user `host`
con `name = $USER` (o "host" come fallback), role='host', autonomy='full'.
Al primo poll del Telegram daemon che riconosce un `default_chat_id`
(esistente in `channels/telegram.py`), il bind avviene autobindato come
`add_channel(host.id, 'telegram', default_chat_id, verified=True)`.

Schema: vedi SCHEMA sotto.
"""
from __future__ import annotations

import calendar
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import config as _C  # §7.11

DEFAULT_DB_PATH = _C.PATH_USER_DATA / "users.db"

ROLES = ("host", "guest")
AUTONOMY_LEVELS = ("read_only", "restricted", "full")
CHANNELS = ("telegram", "mail", "http")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    role TEXT NOT NULL,
    owner_user_id TEXT,
    autonomy_level TEXT NOT NULL,
    created_at TEXT NOT NULL,
    notes TEXT,
    email TEXT
);
CREATE TABLE IF NOT EXISTS user_channels (
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    verified_at TEXT,
    pairing_token TEXT,
    pairing_expires_at TEXT,
    PRIMARY KEY (user_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_channels_recipient ON user_channels(channel, recipient_id);
"""


# --- helpers ----------------------------------------------------------------

from timefmt import now_iso_z as _now_iso


def _resolve_db_path() -> Path:
    return Path(os.environ.get("METNOS_USERS_DB") or DEFAULT_DB_PATH)


def _open_db() -> sqlite3.Connection:
    p = _resolve_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _normalize_name(name: str) -> str:
    """Handle utente: lowercase + alfanumerico+underscore. Resto rifiutato."""
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    n = name.strip().lower()
    if not n:
        raise ValueError("name must be non-empty")
    if not all(c.isalnum() or c == "_" for c in n):
        raise ValueError(
            f"name must be alphanumeric or underscore, got {name!r}"
        )
    return n


def _row_to_user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


# --- bootstrap + init -------------------------------------------------------

def init_db() -> None:
    """Crea tabelle + bootstrap host (idempotente).

    Bootstrap host: se non esistono user, crea uno user 'host' con
    `name = $USER` (lowercased) o 'host' come fallback. autonomy='full'.
    Migration idempotente: aggiunge colonne mancanti (es. `email` 7/5/2026)
    su DB pre-esistenti.
    """
    conn = _open_db()
    try:
        # Migration: colonne aggiunte post-genesi. SQLite non supporta
        # ALTER TABLE IF NOT EXISTS COLUMN; usiamo PRAGMA table_info.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if n > 0:
            return
        raw = (os.environ.get("USER") or "host").strip()
        try:
            host_name = _normalize_name(raw)
        except ValueError:
            host_name = "host"
        conn.execute(
            "INSERT INTO users (id, name, display_name, role, owner_user_id, "
            "autonomy_level, created_at, notes) VALUES (?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex[:16],
                host_name,
                host_name.capitalize(),
                "host",
                None,
                "full",
                _now_iso(),
                "host bootstrap",
            ),
        )
    finally:
        conn.close()


# --- create / read / list / delete -----------------------------------------

def create_user(
    name: str,
    *,
    display_name: str | None = None,
    role: str = "guest",
    owner_user_id: str | None = None,
    autonomy_level: str = "restricted",
    notes: str | None = None,
    email: str | None = None,
) -> dict:
    """Crea un nuovo user. Solleva ValueError se name non valido o duplicato.

    Vincoli:
    - name unico (case-insensitive, normalizzato a lowercase).
    - role in ROLES.
    - autonomy_level in AUTONOMY_LEVELS.
    - per role='guest', owner_user_id puo' essere None all'inizio (verra'
      eventualmente popolato post-creation).
    - per role='host' c'e' una sola istanza sensata; non lo blocchiamo a
      schema (ci possono essere migrazioni), lo enforciamo a livello
      applicativo: se gia' c'e' un host, non se ne crea un altro.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    if autonomy_level not in AUTONOMY_LEVELS:
        raise ValueError(
            f"autonomy_level must be one of {AUTONOMY_LEVELS}, got {autonomy_level!r}"
        )
    norm_name = _normalize_name(name)
    init_db()  # idempotente: garantisce schema + bootstrap host
    conn = _open_db()
    try:
        if role == "host":
            existing = conn.execute(
                "SELECT id FROM users WHERE role='host' LIMIT 1"
            ).fetchone()
            if existing:
                raise ValueError(
                    "an host user already exists; only one host per system"
                )
        if owner_user_id:
            o = conn.execute(
                "SELECT id FROM users WHERE id=?", (owner_user_id,)
            ).fetchone()
            if not o:
                raise ValueError(
                    f"owner_user_id {owner_user_id!r} not found"
                )
        try:
            uid = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT INTO users (id, name, display_name, role, owner_user_id, "
                "autonomy_level, created_at, notes, email) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    norm_name,
                    display_name,
                    role,
                    owner_user_id,
                    autonomy_level,
                    _now_iso(),
                    notes,
                    email,
                ),
            )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e).upper() or "unique" in str(e):
                raise ValueError(f"user name {norm_name!r} already exists") from e
            raise
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return _row_to_user(row)
    finally:
        conn.close()


def get_user(user_id_or_name: str) -> dict | None:
    """Match per id (uuid hex 16) o per name. None se non trovato."""
    if not user_id_or_name:
        return None
    init_db()
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? OR name=?",
            (user_id_or_name, user_id_or_name.lower()),
        ).fetchone()
        return _row_to_user(row)
    finally:
        conn.close()


def list_users(*, role: str | None = None, owner: str | None = None) -> list[dict]:
    """Elenco user, opzionalmente filtrati per role e/o owner_user_id."""
    init_db()
    conn = _open_db()
    try:
        sql = "SELECT * FROM users WHERE 1=1"
        params: list[Any] = []
        if role:
            sql += " AND role=?"
            params.append(role)
        if owner:
            sql += " AND owner_user_id=?"
            params.append(owner)
        sql += " ORDER BY created_at ASC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_user(user_id: str) -> bool:
    """Elimina user + cascade su user_channels. True se cancellato."""
    if not user_id:
        return False
    conn = _open_db()
    try:
        conn.execute("DELETE FROM user_channels WHERE user_id=?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def set_autonomy(user_id: str, level: str) -> bool:
    """Aggiorna autonomy_level. False se user inesistente o livello invalido."""
    if level not in AUTONOMY_LEVELS:
        raise ValueError(
            f"autonomy_level must be one of {AUTONOMY_LEVELS}, got {level!r}"
        )
    conn = _open_db()
    try:
        cur = conn.execute(
            "UPDATE users SET autonomy_level=? WHERE id=?", (level, user_id)
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def update_user(
    user_id: str,
    *,
    display_name: str | None = ...,
    email: str | None = ...,
    autonomy_level: str | None = ...,
    notes: str | None = ...,
) -> bool:
    """Update selettivo dei campi mutabili. Sentinel `...` = "non toccare".

    Per cancellare un campo, passare esplicitamente None (es. email=None).
    Ritorna True se il record esiste ed e' stato (eventualmente) aggiornato.
    Solleva ValueError se autonomy_level non e' in AUTONOMY_LEVELS.
    """
    sets: list[str] = []
    params: list = []
    if display_name is not ...:
        sets.append("display_name=?"); params.append(display_name)
    if email is not ...:
        sets.append("email=?"); params.append(email)
    if autonomy_level is not ...:
        if autonomy_level not in AUTONOMY_LEVELS:
            raise ValueError(
                f"autonomy_level must be one of {AUTONOMY_LEVELS}, got {autonomy_level!r}"
            )
        sets.append("autonomy_level=?"); params.append(autonomy_level)
    if notes is not ...:
        sets.append("notes=?"); params.append(notes)
    if not sets:
        # Niente da aggiornare: verifica solo che lo user esista.
        return get_user(user_id) is not None
    params.append(user_id)
    conn = _open_db()
    try:
        cur = conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id=?", params,
        )
        return cur.rowcount > 0
    finally:
        conn.close()


# --- channels ---------------------------------------------------------------

def add_channel(
    user_id: str,
    channel: str,
    recipient_id: str,
    *,
    verified: bool = False,
) -> dict:
    """Aggiunge (o aggiorna) un binding canale → recipient_id per user."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    if not recipient_id:
        raise ValueError("recipient_id must be non-empty")
    user = get_user(user_id)
    if not user:
        raise ValueError(f"user {user_id!r} not found")
    conn = _open_db()
    try:
        verified_at = _now_iso() if verified else None
        conn.execute(
            "INSERT INTO user_channels (user_id, channel, recipient_id, verified_at, "
            "pairing_token, pairing_expires_at) VALUES (?,?,?,?,NULL,NULL) "
            "ON CONFLICT(user_id, channel) DO UPDATE SET "
            "recipient_id=excluded.recipient_id, verified_at=excluded.verified_at, "
            "pairing_token=NULL, pairing_expires_at=NULL",
            (user["id"], channel, str(recipient_id), verified_at),
        )
        row = conn.execute(
            "SELECT * FROM user_channels WHERE user_id=? AND channel=?",
            (user["id"], channel),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_channel(user_id: str, channel: str) -> dict | None:
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM user_channels WHERE user_id=? AND channel=?",
            (user_id, channel),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_channels(user_id: str) -> list[dict]:
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT * FROM user_channels WHERE user_id=? ORDER BY channel",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_channel(user_id: str, channel: str) -> bool:
    conn = _open_db()
    try:
        cur = conn.execute(
            "DELETE FROM user_channels WHERE user_id=? AND channel=?",
            (user_id, channel),
        )
        return cur.rowcount > 0
    finally:
        conn.close()


# --- pairing flow -----------------------------------------------------------

def issue_pairing_token(user_id: str, channel: str, ttl_s: int = 3600) -> str:
    """Emette un token di pairing one-shot per (user_id, channel).

    Sovrascrive eventuali pairing precedenti per quel channel: il flusso
    `pair` parte da zero (token nuovo, recipient_id = "" placeholder finche'
    consume_pairing_token non lo binda).
    Ritorna il token in chiaro. Hex urlsafe, 32 char.
    """
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    user = get_user(user_id)
    if not user:
        raise ValueError(f"user {user_id!r} not found")
    token = secrets.token_hex(16)
    expires = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + max(60, ttl_s))
    )
    conn = _open_db()
    try:
        conn.execute(
            "INSERT INTO user_channels (user_id, channel, recipient_id, verified_at, "
            "pairing_token, pairing_expires_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id, channel) DO UPDATE SET "
            "recipient_id='', verified_at=NULL, "
            "pairing_token=excluded.pairing_token, "
            "pairing_expires_at=excluded.pairing_expires_at",
            (user["id"], channel, "", None, token, expires),
        )
    finally:
        conn.close()
    return token


def consume_pairing_token(channel: str, recipient_id: str, token: str) -> dict:
    """Verifica token + binda recipient_id allo user.

    Solleva ValueError per token sconosciuti, scaduti, gia' consumati.
    Ritorna user dict in caso di successo.
    """
    if not token or not recipient_id or channel not in CHANNELS:
        raise ValueError("invalid arguments")
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM user_channels WHERE channel=? AND pairing_token=?",
            (channel, token),
        ).fetchone()
        if not row:
            raise ValueError("token unknown or already consumed")
        # Scadenza (UTC: calendar.timegm e' la versione UTC di time.mktime)
        exp_iso = row["pairing_expires_at"] or ""
        if exp_iso:
            exp_epoch = calendar.timegm(
                time.strptime(exp_iso, "%Y-%m-%dT%H:%M:%SZ")
            )
            if exp_epoch < time.time():
                # Pulisci il token scaduto per evitare ambiguita'
                conn.execute(
                    "UPDATE user_channels SET pairing_token=NULL, "
                    "pairing_expires_at=NULL WHERE user_id=? AND channel=?",
                    (row["user_id"], channel),
                )
                raise ValueError("token expired")
        conn.execute(
            "UPDATE user_channels SET recipient_id=?, verified_at=?, "
            "pairing_token=NULL, pairing_expires_at=NULL "
            "WHERE user_id=? AND channel=?",
            (str(recipient_id), _now_iso(), row["user_id"], channel),
        )
        u = conn.execute(
            "SELECT * FROM users WHERE id=?", (row["user_id"],)
        ).fetchone()
        return dict(u) if u else {}
    finally:
        conn.close()


# --- lookup ----------------------------------------------------------------

def is_device_bound(channel: str, recipient_id: str) -> bool:
    """True se esiste un `user_channels` riga con channel+recipient_id
    verificato (non solo token emesso). Usato da `verify_user_cookie`
    per controllare che il device sia ancora attivo (non revocato)."""
    if not channel or not recipient_id or channel not in CHANNELS:
        return False
    init_db()
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM user_channels WHERE channel=? AND recipient_id=? "
            "AND verified_at IS NOT NULL LIMIT 1",
            (channel, str(recipient_id)),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def find_user_by_recipient(channel: str, recipient_id: str) -> dict | None:
    """Reverse lookup: dato un chat_id Telegram (o email, ...) trova lo user."""
    if not channel or not recipient_id:
        return None
    init_db()
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT u.* FROM users u JOIN user_channels c ON c.user_id=u.id "
            "WHERE c.channel=? AND c.recipient_id=? AND c.verified_at IS NOT NULL",
            (channel, str(recipient_id)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def resolve_recipients(targets: list[str], channel: str) -> list[dict]:
    """Mappa una lista di `targets` (user_id o name o "@<chat_id>" diretto)
    a {user, recipient_id, error}. Risolve in ordine:

    1. `@<id>` (es. "@123456789") → trattato come direct chat_id, nessun
       lookup user; ritorna user=None, recipient_id="<id>", error=None.
    2. user_id o name → lookup `get_user`, poi `get_channel(user, channel)`:
       - se user e channel verified → {user, recipient_id}
       - se user trovato ma channel non verified → error="channel_not_paired"
       - se user non trovato → error="user_not_found"

    Best-effort: la lista contiene un elemento per target con campo `error`
    valorizzato sui fallimenti. Il caller (send_messages) decide se vado o
    fail. Niente exception interna: la funzione ritorna struttura uniforme.
    """
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    init_db()
    out: list[dict] = []
    if not isinstance(targets, list):
        return out
    for t in targets:
        s = str(t).strip()
        if not s:
            out.append({"target": t, "user": None, "recipient_id": None,
                        "error": "empty_target"})
            continue
        if s.startswith("@"):
            rid = s[1:]
            if not rid:
                out.append({"target": t, "user": None, "recipient_id": None,
                            "error": "empty_chat_id"})
                continue
            out.append({"target": t, "user": None, "recipient_id": rid,
                        "error": None})
            continue
        u = get_user(s)
        if not u:
            out.append({"target": t, "user": None, "recipient_id": None,
                        "error": "user_not_found"})
            continue
        ch = get_channel(u["id"], channel)
        if not ch or not ch.get("verified_at") or not ch.get("recipient_id"):
            out.append({"target": t, "user": u, "recipient_id": None,
                        "error": "channel_not_paired"})
            continue
        out.append({"target": t, "user": u, "recipient_id": ch["recipient_id"],
                    "error": None})
    return out


# --- bootstrap helper per il telegram daemon -------------------------------

def autobind_host_telegram(default_chat_id: str) -> dict | None:
    """Il telegram daemon chiama questo al primo poll che riconosce il
    `default_chat_id` (sender che combacia col chat_id config). Se non e'
    gia' bindato a uno user, lo binda all'host.

    Idempotente: se gia' c'e' un binding verified per (telegram, chat_id)
    non fa nulla. Ritorna dict del channel se ha bindato (o gia' bindato);
    None se non c'era host (caso anomalo, init_db dovrebbe averlo creato).
    """
    if not default_chat_id:
        return None
    init_db()
    existing = find_user_by_recipient("telegram", default_chat_id)
    if existing:
        # Gia' bindato: nessuna azione, ritorna lo state attuale.
        return get_channel(existing["id"], "telegram")
    hosts = list_users(role="host")
    if not hosts:
        return None
    host = hosts[0]
    return add_channel(host["id"], "telegram", str(default_chat_id),
                       verified=True)


__all__ = [
    "ROLES", "AUTONOMY_LEVELS", "CHANNELS",
    "init_db", "create_user", "get_user", "list_users", "delete_user",
    "set_autonomy",
    "add_channel", "get_channel", "list_channels", "remove_channel",
    "issue_pairing_token", "consume_pairing_token",
    "find_user_by_recipient", "resolve_recipients",
    "autobind_host_telegram",
]
