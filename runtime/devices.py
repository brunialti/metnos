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
    last_poll TEXT,
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
    # Separato dal heartbeat dedicato: un processo puo' essere vivo mentre il
    # worker sequenziale e' bloccato in un executor e non accetta nuovo lavoro.
    last_poll: str | None = None


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
    if "last_poll" not in cols:
        conn.execute("ALTER TABLE devices ADD COLUMN last_poll TEXT")
    _migrate_owner_to_users_id(conn)


def _migrate_owner_to_users_id(conn: sqlite3.Connection) -> None:
    """Predisposizione multi-utente (2026-07-04): `owner_user_id` deve
    riferire un VERO `users.id` del registro, non il sentinel legacy 'host'
    (che non e' un id). Rimappa le righe 'host' all'id reale dell'utente host.

    Guardato: gira solo se esistono righe legacy → no-op a regime. Non
    distruttivo (host→host, solo id canonico). Best-effort: se il registro
    utenti non e' disponibile lascia il sentinel (l'owner_user resolver lo
    tollera comunque)."""
    has_legacy = conn.execute(
        "SELECT 1 FROM devices WHERE owner_user_id='host' "
        "UNION ALL SELECT 1 FROM device_tokens WHERE owner_user_id='host' "
        "LIMIT 1"
    ).fetchone()
    if not has_legacy:
        return
    hid = host_user_id()
    if not hid or hid == "host":
        return
    conn.execute("UPDATE devices SET owner_user_id=? WHERE owner_user_id='host'", (hid,))
    conn.execute("UPDATE device_tokens SET owner_user_id=? WHERE owner_user_id='host'", (hid,))


def host_user_id() -> str:
    """users.id reale dell'utente host — owner di default del pairing quando
    l'admin non sceglie. Sentinel-safe: ritorna 'host' se il registro utenti
    non e' raggiungibile (bootstrap non ancora avvenuto)."""
    try:
        import users as _users
        hosts = _users.list_users(role="host")
        if hosts:
            return hosts[0]["id"]
    except Exception:
        pass
    return "host"


def owner_user(owner_user_id: str) -> dict | None:
    """Risolve `owner_user_id` → record utente del registro. E' l'aggancio di
    IDENTIFICAZIONE: quando un device si connette, da qui si risale all'utente
    (e in futuro ai suoi profili di sicurezza/autonomia). Tollera il sentinel
    legacy 'host' (→ utente host reale). None se non risolvibile."""
    if not owner_user_id:
        return None
    try:
        import users as _users
        u = _users.get_user(owner_user_id)
        if u:
            return u
        if owner_user_id == "host":
            hosts = _users.list_users(role="host")
            return hosts[0] if hosts else None
    except Exception:
        pass
    return None


# Sentinel FAIL-CLOSED: un actor non riconosciuto non deve ricadere nel
# perimetro host (rilievo #1 multi-utente). Nessun device ha mai questo owner
# (gli owner sono users.id reali o il legacy 'host') → perimetro VUOTO.
NO_OWNER = "__no_owner__"


def owner_id_for_actor(actor: str | None) -> str:
    """Resolver centrale actor→owner_user_id per il filtro device (A3 review).

    - actor = device_id di un device pairato → owner di QUEL device
      (identificazione: i turni originati da un device girano come il suo
      proprietario);
    - actor = id o name di un utente del registro → quell'utente;
    - vuoto / sentinel 'host' → utente host (default mono-utente);
    - actor NON riconosciuto → `NO_OWNER` (FAIL-CLOSED, rilievo #1): NON ricade
      nel perimetro host, il filtro device restituisce vuoto.

    Necessario dopo la migrazione owner→users.id: il vecchio confronto
    `owner_user_id == (actor or 'host')` non regge piu' (owner ora e' un uuid,
    actor puo' essere 'host'/device_id). Sentinel-safe."""
    a = (actor or "").strip()
    if not a or a == "host":
        return host_user_id()
    try:
        d = get_device(a)
        if d:
            return d.owner_user_id
    except Exception:
        pass
    u = owner_user(a)
    if u:
        return u["id"]
    return NO_OWNER


def list_by_owner(owner_user_id: str, *, include_revoked: bool = False,
                  db_path: Path | None = None) -> list["Device"]:
    """Device di proprieta' di uno user — profilo utente in UI + (futuro)
    filtro placement per owner. Risolve il sentinel legacy 'host' all'id host
    reale prima del confronto, cosi' un DB non ancora migrato non nasconde i
    device dell'host."""
    hid = None
    if owner_user_id == "host":
        hid = host_user_id()
    return [d for d in list_devices(include_revoked=include_revoked, db_path=db_path)
            if d.owner_user_id == owner_user_id
            or (d.owner_user_id == "host" and owner_user_id == hid)
            or (hid and d.owner_user_id == hid and owner_user_id == "host")]


def fingerprint_of(public_key_b64: str) -> str:
    raw = _b64u_decode(public_key_b64)
    return hashlib.sha256(raw).hexdigest()


# --- token issue / consume -----------------------------------------------

def _canonical_owner(owner_user_id: str | None) -> str:
    """Canonicalizza+valida l'owner al momento della coniazione del token
    (rilievo #3): il token e' l'UNICO punto di conio (firmato), quindi qui e'
    la sorgente. Vuoto/sentinel 'host' → id host reale; altrimenti DEVE essere
    un utente del registro (id o name) → il suo id. Owner sconosciuto = errore
    (niente device orfani/non-filtrabili anche da CLI/API diretta)."""
    s = (owner_user_id or "").strip()
    if not s or s == "host":
        return host_user_id()
    u = owner_user(s)
    if not u:
        raise TokenError(f"owner_user_id sconosciuto: {s!r}")
    return u["id"]


def generate_token(name: str, *, owner_user_id: str = "host",
                   ttl_seconds: int = DEFAULT_TOKEN_TTL_S,
                   issued_by: str = "author",
                   db_path: Path | None = None) -> str:
    """Genera un token effimero firmato per il pairing di un device.

    Il record `device_tokens` e' creato in stato non-consumato. Il token
    contiene: token_id, name, owner_user_id, exp, version. La firma garantisce
    che il token venga davvero da Roberto (chiave 'author' in keys/).
    L'owner e' canonicalizzato a un vero users.id (`_canonical_owner`).
    """
    if not name or not DEVICE_NAME_RE.match(name):
        raise TokenError(
            "nome device non valido (ammessi lettere, cifre, . _ -, max 40)")
    owner_user_id = _canonical_owner(owner_user_id)
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

        # registra nuovo device (o recupera per fingerprint coincidente: re-pair
        # di un client che ha conservato la sua identita' Ed25519)
        existing = conn.execute(
            "SELECT * FROM devices WHERE public_key_fingerprint = ?", (fp,)
        ).fetchone()
        if existing is not None:
            # Il token fresco one-shot emesso dall'admin E' la
            # ri-autorizzazione esplicita: la riga si aggiorna (nome, owner,
            # OS, paired_at) e un'eventuale revoca DECADE. Senza questo, un
            # device revocato che si ri-appaia resta revocato per sempre:
            # register 200 ma ogni poll/heartbeat respinto 403 in silenzio
            # (osservato live 3/7: join fermo a 'registered', client di
            # aprile con la stessa chiave e revoca del pomeriggio).
            device_id = existing["id"]
            if existing["revoked_at"] is not None:
                log.warning(
                    "re-pair di device REVOCATO %s ('%s'->'%s'): revoca "
                    "rimossa dal token fresco", device_id[:12],
                    existing["name"], name)
            conn.execute(
                """UPDATE devices SET name = ?, owner_user_id = ?,
                       os_family = COALESCE(?, os_family),
                       os_arch = COALESCE(?, os_arch),
                       paired_at = ?, revoked_at = NULL
                   WHERE id = ?""",
                (name, owner, os_family, os_arch, _now_iso(), device_id),
            )
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


def poll_seen(device_id: str, *, db_path: Path | None = None) -> None:
    """Registra che il loop executor sta realmente chiedendo lavoro.

    Aggiorna anche la liveness per compatibilita' con i client che non hanno
    un heartbeat dedicato. Il reciproco non vale: il task heartbeat puo'
    continuare mentre il worker e' bloccato, quindi ``heartbeat()`` non deve
    mai avanzare ``last_poll``.
    """
    now = _now_iso()
    conn = _open_db(db_path)
    try:
        conn.execute(
            "UPDATE devices SET last_poll = ?, last_heartbeat = ? "
            "WHERE id = ? AND revoked_at IS NULL",
            (now, now, device_id),
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
        last_poll=row["last_poll"] if "last_poll" in row.keys() else None,
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
