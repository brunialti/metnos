"""Cooldown anti-lockout del dominio sites (ADR 0191 P6 / §7).

Protegge l'ACCOUNT del proprietario: dopo N rifiuti credenziale VERIFICATI
consecutivi (o rate-limit espliciti) rallenta i tentativi con backoff
esponenziale — mai a raffica. Vive nel BROKER (choke-point che vede ogni
esposizione credenziale).

Conta SOLO gli esiti `credentials_rejected` e `rate_limited` (§6.2). NON
selector_missing / empty_surface / timeout / challenge / inconclusive.

Chiave `(owner, binding_id, credential_fp)`: `binding_id` = storage_domain esatto
del record vault; `credential_fp` = `credentials.fingerprint` (sha256[:16] della
pwd, irreversibile) — MAI il valore. Cambio pwd → nuovo fp → azzera naturalmente
il cooldown (voluto).
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import time

import config as C

_COOLDOWN_REASONS = frozenset({"credentials_rejected", "rate_limited"})
_RESET_IDLE_S = 24 * 3600  # record senza fallimenti da 24h → purgato

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites_cooldown(
  owner          TEXT    NOT NULL,
  binding_id     TEXT    NOT NULL,
  credential_fp  TEXT    NOT NULL,
  fail_count     INTEGER NOT NULL DEFAULT 0,
  last_reason    TEXT    NOT NULL DEFAULT '',
  cooldown_until INTEGER NOT NULL DEFAULT 0,
  updated_at     INTEGER NOT NULL,
  PRIMARY KEY (owner, binding_id, credential_fp)
);
"""


def _bounded_int(env: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.getenv(env, ""))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _params() -> tuple[int, int, int, int]:
    """(N, BASE_S, FACTOR, CAP_S) — decisi §7, env bounded."""
    return (
        _bounded_int("METNOS_SITES_COOLDOWN_N", 2, 1, 5),
        _bounded_int("METNOS_SITES_COOLDOWN_BASE_S", 900, 60, 3600),
        _bounded_int("METNOS_SITES_COOLDOWN_FACTOR", 3, 2, 5),
        _bounded_int("METNOS_SITES_COOLDOWN_CAP_S", 43200, 3600, 86400),
    )


def _db_path() -> str:
    override = os.getenv("METNOS_SITES_COOLDOWN_DB")
    if override:
        return override
    return str(C.PATH_USER_STATE / "sites_cooldown.sqlite")  # §7.11 da config


_initialized: set[str] = set()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None → controllo transazionale ESPLICITO (BEGIN IMMEDIATE).
    # timeout ampio = busy-handler: i writer concorrenti attendono il lock.
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    # Schema + WAL solo alla PRIMA apertura del path: evita che 12 writer
    # concorrenti contendano sul CREATE TABLE / journal_mode a ogni chiamata.
    if path not in _initialized:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_SCHEMA)
        _initialized.add(path)
    return conn


def _with_lock_retry(fn):
    """Riprova su `database is locked` (SQLITE_LOCKED non e' coperto dal
    busy-handler). Backoff breve, atomicita' preservata."""
    last = None
    for attempt in range(8):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last = exc
            time.sleep(0.02 * (attempt + 1))
    raise last


def _cooldown_seconds(fail_count: int, n: int, base: int,
                      factor: int, cap: int) -> int:
    """cd = 0 se fc<N, altrimenti min(CAP, BASE*FACTOR**(fc-N))."""
    if fail_count < n:
        return 0
    return int(min(cap, base * (factor ** (fail_count - n))))


def record_failure(owner: str, binding_id: str, credential_fp: str,
                   reason: str, *, now: float | None = None) -> int:
    """Incrementa il cooldown per un esito ELEGGIBILE (`credentials_rejected` /
    `rate_limited`). Ritorna `cooldown_until` (epoch) o 0. Upsert transazionale
    atomico (BEGIN IMMEDIATE): concorrenza sulla stessa chiave serializzata."""
    if reason not in _COOLDOWN_REASONS:
        return 0
    now_i = int(now if now is not None else time.time())
    n, base, factor, cap = _params()

    def _txn() -> int:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fail_count FROM sites_cooldown "
                "WHERE owner=? AND binding_id=? AND credential_fp=?",
                (owner, binding_id, credential_fp)).fetchone()
            fail_count = (row[0] if row else 0) + 1
            until = now_i + _cooldown_seconds(fail_count, n, base, factor, cap)
            conn.execute(
                "INSERT INTO sites_cooldown(owner,binding_id,credential_fp,"
                "fail_count,last_reason,cooldown_until,updated_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(owner,binding_id,credential_fp) DO UPDATE SET "
                "fail_count=excluded.fail_count, last_reason=excluded.last_reason, "
                "cooldown_until=excluded.cooldown_until, updated_at=excluded.updated_at",
                (owner, binding_id, credential_fp, fail_count, reason,
                 until, now_i))
            conn.execute("COMMIT")
            return until
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    return _with_lock_retry(_txn)


def reset(owner: str, binding_id: str, credential_fp: str) -> None:
    """Login verificato → azzera il cooldown (DELETE della riga)."""
    def _txn() -> None:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM sites_cooldown "
                "WHERE owner=? AND binding_id=? AND credential_fp=?",
                (owner, binding_id, credential_fp))
            conn.execute("COMMIT")
        finally:
            conn.close()

    _with_lock_retry(_txn)


def active_until(owner: str, binding_id: str, credential_fp: str,
                 *, now: float | None = None) -> int:
    """Epoch di fine cooldown se ATTIVO, altrimenti 0. Purga i record idle (TTL)."""
    now_i = int(now if now is not None else time.time())
    conn = _connect()
    try:
        conn.execute("DELETE FROM sites_cooldown WHERE updated_at < ?",
                     (now_i - _RESET_IDLE_S,))
        row = conn.execute(
            "SELECT cooldown_until FROM sites_cooldown "
            "WHERE owner=? AND binding_id=? AND credential_fp=?",
            (owner, binding_id, credential_fp)).fetchone()
        if row and int(row[0]) > now_i:
            return int(row[0])
        return 0
    finally:
        conn.close()


def retry_after_s(owner: str, binding_id: str, credential_fp: str,
                  *, now: float | None = None) -> int:
    """Secondi residui di cooldown (0 se non attivo)."""
    now_i = int(now if now is not None else time.time())
    until = active_until(owner, binding_id, credential_fp, now=now_i)
    return max(0, until - now_i) if until else 0
