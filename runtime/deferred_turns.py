"""deferred_turns — coda di TURNI differiti «quando il device torna online».

Fase 7 A.1 (spec_fase7_disconnect_robustezza.md): un turno che bersaglia un
device OFFLINE, col CONSENSO esplicito dell'utente (§2.11, mai magia), viene
accodato qui e RI-ESEGUITO come run_turn completo al primo poll del device.
Niente meccanismi nuovi di consegna: il re-run passa dalla pipeline normale
(planning fresco, gate di massa, undo standard actor-isolato). L'esito
raggiunge l'utente via user_notices (A.2, prossima-visita).

Store: jsonl append-only + flock (stesso stile di user_notices/undo.jsonl).
Stati: pending → done | failed | expired. TTL default 24h
(`METNOS_DEFER_TTL_H`).
"""
from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager

import config as _C  # §7.11

DB_PATH = _C.PATH_USER_DATA / "deferred_turns.jsonl"
LOCK_PATH = _C.PATH_USER_DATA / "deferred_turns.lock"


@contextmanager
def _store_lock(*, exclusive: bool):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(
            descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_all(descriptor: int, body: bytes) -> None:
    view = memoryview(body)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write in deferred-turn store")
        view = view[written:]


def _ttl_s() -> float:
    try:
        return float(os.environ.get("METNOS_DEFER_TTL_H", "24")) * 3600
    except (TypeError, ValueError):
        return 24 * 3600


def _append(rec: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _store_lock(exclusive=True):
        fd = os.open(DB_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            _write_all(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


def _load() -> dict[str, dict]:
    """Stato corrente per id (ultimo record vince — event-sourcing minimo)."""
    out: dict[str, dict] = {}
    with _store_lock(exclusive=False):
        try:
            fd = os.open(DB_PATH, os.O_RDONLY)
        except FileNotFoundError:
            return out
        try:
            chunks = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
    for line in b"".join(chunks).decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = rec.get("id")
        if rid:
            out[rid] = {**out.get(rid, {}), **rec}
    return out


def add(*, device_id: str, device_name: str, query: str, actor: str,
        channel: str, owner_user_id: str,
        conversation_id: str = "") -> str:
    """Accoda un turno differito. Ritorna l'id."""
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("deferred turn owner_user_id is required")
    rid = uuid.uuid4().hex[:16]
    _append({
        "id": rid, "state": "pending",
        "device_id": device_id, "device_name": device_name,
        "query": query, "actor": actor or "host",
        "owner_user_id": owner,
        "channel": channel or "", "conversation_id": conversation_id or "",
        "created_at": time.time(),
        "expires_at": time.time() + _ttl_s(),
    })
    return rid


def mark(rid: str, state: str, *, owner_user_id: str,
         note: str = "") -> None:
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("deferred turn owner_user_id is required")
    current = _load().get(str(rid))
    if (current is None
            or str(current.get("owner_user_id") or "") != owner):
        raise ValueError("deferred turn owner mismatch")
    _append({"id": rid, "state": state, "note": note,
             "owner_user_id": owner, "ts": time.time()})


def pending_for_device(device_id: str, *, owner_user_id: str) -> list[dict]:
    """I differiti PENDING (non scaduti) per il device. I record scaduti
    vengono marcati `expired` qui (lazy) — il chiamante notifica."""
    now = time.time()
    owner = str(owner_user_id or "").strip()
    if not owner:
        return []
    out = []
    for rec in _load().values():
        if rec.get("state") != "pending":
            continue
        if rec.get("device_id") != device_id:
            continue
        if str(rec.get("owner_user_id") or "") != owner:
            continue
        if now > float(rec.get("expires_at") or 0):
            mark(rec["id"], "expired", owner_user_id=owner)
            rec = {**rec, "state": "expired"}
            out.append(rec)
            continue
        out.append(rec)
    return out


def expired_unnotified() -> list[dict]:
    """Pending SCADUTI di qualunque device (per lo sweep di notifica)."""
    now = time.time()
    out = []
    for rec in _load().values():
        if rec.get("state") == "pending" and \
                now > float(rec.get("expires_at") or 0):
            owner = str(rec.get("owner_user_id") or "")
            if not owner:
                continue
            mark(rec["id"], "expired", owner_user_id=owner)
            out.append({**rec, "state": "expired"})
    return out


def _rewrite_excluding(*, owner_user_id: str | None = None,
                       unscoped: bool = False) -> int:
    with _store_lock(exclusive=True):
        try:
            fd = os.open(DB_PATH, os.O_RDONLY)
        except FileNotFoundError:
            return 0
        try:
            chunks = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
        data = b"".join(chunks).decode("utf-8", "replace")
        parsed: list[tuple[str, dict]] = []
        final: dict[str, dict] = {}
        for line in data.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = str(record.get("id") or "")
            if not rid:
                continue
            parsed.append((rid, record))
            final[rid] = {**final.get(rid, {}), **record}
        target_owner = str(owner_user_id or "")
        remove_ids = {
            rid for rid, record in final.items()
            if ((target_owner and str(record.get("owner_user_id") or "")
                 == target_owner)
                or (unscoped and not record.get("owner_user_id")))
        }
        retained = [
            json.dumps(record, ensure_ascii=False) + "\n"
            for rid, record in parsed if rid not in remove_ids
        ]
        temporary = DB_PATH.with_name(DB_PATH.name + ".rewrite.tmp")
        tmp_fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            _write_all(tmp_fd, "".join(retained).encode("utf-8"))
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        os.replace(temporary, DB_PATH)
        return len(remove_ids)


def purge_owner(owner_user_id: str) -> int:
    """Physically remove all events for one immutable owner."""

    owner = str(owner_user_id or "").strip()
    return _rewrite_excluding(owner_user_id=owner) if owner else 0


def purge_unscoped() -> int:
    """Retire pre-owner deferred commands; they are never executable."""

    return _rewrite_excluding(unscoped=True)
