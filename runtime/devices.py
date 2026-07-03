"""runtime.devices — registry dei device remoti pairati con metnos-server.

Distinto da runtime.pairing (channel+sender Telegram). Qui un device e' un
host remoto (laptop, VPS, smart speaker) che esegue executor Python via il
client Rust `metnos-client`.

Modello (host + guest, KIS):
- ogni device ha owner_user_id (default 'host').
- pairing via token effimero a uso singolo, firmato Ed25519 dalla chiave 'author'.
- al consume, il client presenta la propria public_key Ed25519. Il server registra
  device_id <-> public_key fingerprint. Idempotenza: stesso token+pubkey ritorna
  lo stesso device_id; token+altra-pubkey solleva ConsumedError.

A prova di bomba:
- consume e' atomico (transazione SQLite con UNIQUE).
- token consumato resta in tabella per audit (no cleanup aggressivo).
- record device immutabile su pubkey: cambia pubkey = nuovo device.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sign import KEYS_DIR, list_trusted_publics, load_private  # noqa: E402
import config as _C  # §7.11

from logging_setup import get_logger
log = get_logger(__name__)

DEFAULT_DB_PATH = _C.PATH_USER_STATE / "devices.db"
DEFAULT_TOKEN_TTL_S = 600
# Il flusso join include un umano che scarica ed esegue con l'attrito
# Windows in mezzo (avvisi, SmartScreen, ripensamenti): 10' bruciati dal
# vivo il 3/7 (token scaduto prima del register). 30' resta effimero.
DEFAULT_JOIN_TTL_S = 1800
TOKEN_PREFIX = "DEV."
PROTOCOL_VERSION = 1

# Dominio CHIUSO (§2.4): il nome device e' uno slug. Finisce in pagine HTML
# (join page, console), in unit systemd e in log: charset stretto alla
# SORGENTE, l'escaping al render e' il secondo strato.
import re as _re
DEVICE_NAME_RE = _re.compile(r"^[A-Za-z0-9._-]{1,40}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL DEFAULT 'host',
    public_key_b64 TEXT NOT NULL,
    public_key_fingerprint TEXT NOT NULL UNIQUE,
    os_family TEXT,
    os_arch TEXT,
    paired_at TEXT NOT NULL,
    last_heartbeat TEXT,
    revoked_at TEXT,
    profile_json TEXT
);
CREATE TABLE IF NOT EXISTS device_tokens (
    token_id TEXT PRIMARY KEY,
    device_id TEXT,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL DEFAULT 'host',
    issued_at TEXT NOT NULL,
    consumed_at TEXT,
    consumed_fingerprint TEXT
);
CREATE TABLE IF NOT EXISTS device_join_sessions (
    join_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    token_id TEXT NOT NULL,
    device_name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'auto',
    server_url TEXT,
    state TEXT NOT NULL DEFAULT 'created',
    client_hint TEXT,
    device_id TEXT,
    created_at TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    updated_at TEXT
);
"""


class DeviceError(Exception):
    pass


class TokenError(DeviceError):
    pass


class ConsumedError(DeviceError):
    """Token gia' consumato con un'altra chiave."""


@dataclass
class Device:
    id: str
    name: str
    owner_user_id: str
    public_key_b64: str
    public_key_fingerprint: str
    os_family: str | None
    os_arch: str | None
    paired_at: str
    last_heartbeat: str | None
    revoked_at: str | None
    profile_json: str | None = None


# --- helpers --------------------------------------------------------------

def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


from timefmt import now_iso_z as _now_iso


def _open_db(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path or os.environ.get("METNOS_DEVICES_DB") or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Migrazioni additive per DB pre-esistenti (CREATE IF NOT EXISTS non
    aggiorna le colonne). Idempotente."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(devices)")}
    if "profile_json" not in cols:
        conn.execute("ALTER TABLE devices ADD COLUMN profile_json TEXT")


def fingerprint_of(public_key_b64: str) -> str:
    raw = _b64u_decode(public_key_b64)
    return hashlib.sha256(raw).hexdigest()


# --- token issue / consume -----------------------------------------------

def generate_token(name: str, *, owner_user_id: str = "host",
                   ttl_seconds: int = DEFAULT_TOKEN_TTL_S,
                   issued_by: str = "author",
                   db_path: Path | None = None) -> str:
    """Genera un token effimero firmato per il pairing di un device.

    Il record `device_tokens` e' creato in stato non-consumato. Il token
    contiene: token_id, name, owner_user_id, exp, version. La firma garantisce
    che il token venga davvero da Roberto (chiave 'author' in keys/).
    """
    if not name or not DEVICE_NAME_RE.match(name):
        raise TokenError(
            "nome device non valido (ammessi lettere, cifre, . _ -, max 40)")
    token_id = uuid.uuid4().hex
    payload = {
        "v": PROTOCOL_VERSION,
        "tid": token_id,
        "name": name,
        "owner": owner_user_id,
        "exp": int(time.time()) + ttl_seconds,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    priv = load_private(issued_by)
    signature = priv.sign(payload_bytes)
    token = f"{TOKEN_PREFIX}{_b64u_encode(payload_bytes)}.{_b64u_encode(signature)}"

    conn = _open_db(db_path)
    try:
        conn.execute(
            "INSERT INTO device_tokens (token_id, name, owner_user_id, issued_at) VALUES (?,?,?,?)",
            (token_id, name, owner_user_id, _now_iso()),
        )
    finally:
        conn.close()
    return token


def _verify_token(token: str) -> dict:
    if not token.startswith(TOKEN_PREFIX):
        raise TokenError("formato token invalido (atteso prefisso DEV.)")
    body = token[len(TOKEN_PREFIX):]
    parts = body.split(".")
    if len(parts) != 2:
        raise TokenError("formato token invalido (atteso DEV.<payload>.<sig>)")
    try:
        payload_bytes = _b64u_decode(parts[0])
        sig = _b64u_decode(parts[1])
    except Exception as e:
        raise TokenError(f"decodifica base64 fallita: {e}")

    trusted = list_trusted_publics()
    if not trusted:
        raise TokenError(f"nessuna chiave trusted in {KEYS_DIR}")
    verified = any(_safe_verify(pub, sig, payload_bytes) for _, pub in trusted)
    if not verified:
        raise TokenError("firma token non verificata")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise TokenError(f"payload non JSON valido: {e}")
    if payload.get("v") != PROTOCOL_VERSION:
        raise TokenError(f"versione protocollo non supportata: {payload.get('v')}")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise TokenError("token scaduto")
    return payload


def _safe_verify(pub, sig: bytes, payload_bytes: bytes) -> bool:
    try:
        pub.verify(sig, payload_bytes)
        return True
    except Exception:
        return False


def consume_token(token: str, public_key_b64: str, *,
                  os_family: str | None = None, os_arch: str | None = None,
                  db_path: Path | None = None) -> Device:
    """Consuma il token e registra il device. Idempotente per (token,pubkey).

    Atomico:
    - se token non esiste in DB -> TokenError (mai emesso o cancellato).
    - se token gia' consumato con stessa fingerprint -> ritorna il device esistente.
    - se token gia' consumato con altra fingerprint -> ConsumedError.
    - altrimenti, marca token consumato + crea/recupera device.
    """
    payload = _verify_token(token)
    token_id = payload["tid"]
    name = payload["name"]
    owner = payload.get("owner", "host")
    fp = fingerprint_of(public_key_b64)

    conn = _open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT consumed_at, consumed_fingerprint, device_id FROM device_tokens WHERE token_id = ?",
            (token_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise TokenError("token non riconosciuto (mai emesso o cancellato)")
        if row["consumed_at"] is not None:
            if row["consumed_fingerprint"] == fp:
                # idempotente
                conn.execute("COMMIT")
                dev_row = conn.execute(
                    "SELECT * FROM devices WHERE id = ?", (row["device_id"],)
                ).fetchone()
                return _row_to_device(dev_row)
            conn.execute("ROLLBACK")
            raise ConsumedError("token gia' consumato con un'altra chiave")

        # registra nuovo device (o recupera per fingerprint coincidente, raro caso re-pair)
        existing = conn.execute(
            "SELECT * FROM devices WHERE public_key_fingerprint = ?", (fp,)
        ).fetchone()
        if existing is not None:
            device_id = existing["id"]
        else:
            device_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO devices
                   (id, name, owner_user_id, public_key_b64, public_key_fingerprint,
                    os_family, os_arch, paired_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (device_id, name, owner, public_key_b64, fp,
                 os_family, os_arch, _now_iso()),
            )

        conn.execute(
            "UPDATE device_tokens SET device_id = ?, consumed_at = ?, consumed_fingerprint = ? WHERE token_id = ?",
            (device_id, _now_iso(), fp, token_id),
        )
        conn.execute("COMMIT")
        dev_row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return _row_to_device(dev_row)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
        raise
    finally:
        conn.close()


# --- join sessions (§5.3 design doc: install-at-the-fly dalla UI) ----------
#
# Una join session rende OSSERVABILE l'installazione del client su un device:
# la UI genera un link /agent/client/join/<join_id>, il PC target lo apre,
# scarica l'installer personalizzato, registra; ogni passo avanza `state`.
# Il segreto e' il join_id effimero: punta a un token DEV. one-shot (TTL 10').

JOIN_STATES = ("created", "opened", "downloaded", "registered", "heartbeat")
_JOIN_ORDER = {s: i for i, s in enumerate(JOIN_STATES)}


def create_join_session(name: str, *, platform: str = "auto",
                        server_url: str | None = None,
                        owner_user_id: str = "host",
                        ttl_seconds: int = DEFAULT_JOIN_TTL_S,
                        db_path: Path | None = None) -> dict:
    """Genera token effimero + join session osservabile. Ritorna il record."""
    if platform not in ("auto", "linux", "windows"):
        raise TokenError(f"platform non valida: {platform}")
    token = generate_token(name, owner_user_id=owner_user_id,
                           ttl_seconds=ttl_seconds, db_path=db_path)
    token_id = _token_id_of(token)
    join_id = uuid.uuid4().hex[:16]
    now = _now_iso()
    expires_at = int(time.time()) + ttl_seconds
    conn = _open_db(db_path)
    try:
        conn.execute(
            """INSERT INTO device_join_sessions
               (join_id, token, token_id, device_name, platform, server_url,
                state, created_at, expires_at, updated_at)
               VALUES (?,?,?,?,?,?,'created',?,?,?)""",
            (join_id, token, token_id, name, platform, server_url,
             now, expires_at, now),
        )
    finally:
        conn.close()
    return get_join_session(join_id, db_path=db_path)


def _token_id_of(token: str) -> str:
    """Estrae il token_id (tid) dal payload del token DEV. gia' emesso."""
    body = token[len(TOKEN_PREFIX):].split(".")[0]
    return json.loads(_b64u_decode(body).decode("utf-8"))["tid"]


def get_join_session(join_id: str, *, db_path: Path | None = None) -> dict | None:
    """Ritorna la sessione con lo stato EFFETTIVO (expired se il token e'
    scaduto prima di arrivare a registered)."""
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM device_join_sessions WHERE join_id = ?", (join_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    sess = dict(row)
    if (sess["state"] in ("created", "opened", "downloaded")
            and int(sess["expires_at"]) < time.time()):
        sess["state"] = "expired"
    return sess


def mark_join_state(join_id: str, state: str, *,
                    client_hint: dict | None = None,
                    device_id: str | None = None,
                    db_path: Path | None = None) -> bool:
    """Avanza lo stato della sessione. MONOTONO: mai regressioni (un secondo
    GET della pagina join non riporta 'registered' a 'opened')."""
    if state not in _JOIN_ORDER:
        raise DeviceError(f"stato join non valido: {state}")
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT state FROM device_join_sessions WHERE join_id = ?",
            (join_id,),
        ).fetchone()
        if row is None:
            return False
        if _JOIN_ORDER[state] <= _JOIN_ORDER.get(row["state"], -1):
            return False
        sets, params = ["state = ?", "updated_at = ?"], [state, _now_iso()]
        if client_hint is not None:
            sets.append("client_hint = ?")
            params.append(json.dumps(client_hint, ensure_ascii=False))
        if device_id is not None:
            sets.append("device_id = ?")
            params.append(device_id)
        params.append(join_id)
        conn.execute(
            f"UPDATE device_join_sessions SET {', '.join(sets)} WHERE join_id = ?",
            params,
        )
        return True
    finally:
        conn.close()


def purge_join_sessions(*, older_than_days: int = 7,
                        db_path: Path | None = None) -> int:
    """GC (§12): elimina le join session il cui token e' scaduto da piu' di
    `older_than_days`. Sono artefatti TRANSIENTI della UI (lo stato durevole
    e' in devices/device_tokens, che restano per audit): oltre la finestra
    non osservano piu' nulla. Ritorna il numero di righe rimosse."""
    cutoff = int(time.time()) - older_than_days * 86400
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM device_join_sessions WHERE expires_at < ?", (cutoff,))
        return cur.rowcount
    finally:
        conn.close()


def mark_join_registered_by_token(token: str, device_id: str, *,
                                  db_path: Path | None = None) -> bool:
    """Aggancio register→sessione: chiamato da agent_server.register dopo il
    consume riuscito. Il token e' gia' verificato a monte."""
    try:
        token_id = _token_id_of(token)
    except Exception:
        return False
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT join_id FROM device_join_sessions WHERE token_id = ?",
            (token_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    return mark_join_state(row["join_id"], "registered",
                           device_id=device_id, db_path=db_path)


# --- query / lifecycle ----------------------------------------------------

def list_devices(*, include_revoked: bool = False,
                 db_path: Path | None = None) -> list[Device]:
    conn = _open_db(db_path)
    try:
        if include_revoked:
            rows = conn.execute("SELECT * FROM devices ORDER BY paired_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM devices WHERE revoked_at IS NULL ORDER BY paired_at"
            ).fetchall()
        return [_row_to_device(r) for r in rows]
    finally:
        conn.close()


def get_device(device_id: str, *, db_path: Path | None = None) -> Device | None:
    conn = _open_db(db_path)
    try:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return _row_to_device(row) if row else None
    finally:
        conn.close()


def revoke_device(device_id: str, *, db_path: Path | None = None) -> bool:
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now_iso(), device_id),
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def heartbeat(device_id: str, *, profile: dict | None = None,
              db_path: Path | None = None) -> None:
    """Aggiorna liveness + profilo carico del device (§10 L2: cpu_bench,
    ram_free, net_*, has_gpu, current_load). Il profilo e' opaco qui: lo
    interpreta placement.choose_placement."""
    conn = _open_db(db_path)
    try:
        if profile is not None:
            conn.execute(
                "UPDATE devices SET last_heartbeat = ?, profile_json = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (_now_iso(), json.dumps(profile, ensure_ascii=False), device_id),
            )
        else:
            conn.execute(
                "UPDATE devices SET last_heartbeat = ? WHERE id = ? AND revoked_at IS NULL",
                (_now_iso(), device_id),
            )
        # Join session (§5.3): il primo heartbeat chiude il flusso di install
        # (registered → heartbeat). UPDATE mirato, no-op se nessuna sessione.
        conn.execute(
            "UPDATE device_join_sessions SET state = 'heartbeat', updated_at = ? "
            "WHERE device_id = ? AND state = 'registered'",
            (_now_iso(), device_id),
        )
    finally:
        conn.close()


def _row_to_device(row) -> Device:
    return Device(
        id=row["id"],
        name=row["name"],
        owner_user_id=row["owner_user_id"],
        public_key_b64=row["public_key_b64"],
        public_key_fingerprint=row["public_key_fingerprint"],
        os_family=row["os_family"],
        os_arch=row["os_arch"],
        paired_at=row["paired_at"],
        last_heartbeat=row["last_heartbeat"],
        revoked_at=row["revoked_at"],
        profile_json=row["profile_json"] if "profile_json" in row.keys() else None,
    )


# --- CLI ------------------------------------------------------------------

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Metnos device registry CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate-token", help="Emetti un token di pairing per un device")
    g.add_argument("name")
    g.add_argument("--owner", default="host")
    g.add_argument("--ttl", type=int, default=DEFAULT_TOKEN_TTL_S)

    sub.add_parser("list", help="Elenca i device registrati")
    rev = sub.add_parser("revoke", help="Revoca un device")
    rev.add_argument("device_id")

    args = p.parse_args()
    if args.cmd == "generate-token":
        tok = generate_token(args.name, owner_user_id=args.owner, ttl_seconds=args.ttl)
        print(tok)
    elif args.cmd == "list":
        for d in list_devices():
            print(f"{d.id[:12]}  {d.name:<20} owner={d.owner_user_id:<10} fp={d.public_key_fingerprint[:16]}  paired={d.paired_at}")
    elif args.cmd == "revoke":
        ok = revoke_device(args.device_id)
        print("revoked" if ok else "not found or already revoked")


if __name__ == "__main__":
    _cli()
