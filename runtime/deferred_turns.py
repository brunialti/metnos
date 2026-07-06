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
from pathlib import Path

import config as _C  # §7.11

DB_PATH = _C.PATH_USER_DATA / "deferred_turns.jsonl"


def _ttl_s() -> float:
    try:
        return float(os.environ.get("METNOS_DEFER_TTL_H", "24")) * 3600
    except (TypeError, ValueError):
        return 24 * 3600


def _append(rec: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    fd = os.open(DB_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load() -> dict[str, dict]:
    """Stato corrente per id (ultimo record vince — event-sourcing minimo)."""
    out: dict[str, dict] = {}
    if not DB_PATH.exists():
        return out
    for line in DB_PATH.read_text(encoding="utf-8").splitlines():
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
        channel: str, conversation_id: str = "") -> str:
    """Accoda un turno differito. Ritorna l'id."""
    rid = uuid.uuid4().hex[:16]
    _append({
        "id": rid, "state": "pending",
        "device_id": device_id, "device_name": device_name,
        "query": query, "actor": actor or "host",
        "channel": channel or "", "conversation_id": conversation_id or "",
        "created_at": time.time(),
        "expires_at": time.time() + _ttl_s(),
    })
    return rid


def mark(rid: str, state: str, note: str = "") -> None:
    _append({"id": rid, "state": state, "note": note, "ts": time.time()})


def pending_for_device(device_id: str) -> list[dict]:
    """I differiti PENDING (non scaduti) per il device. I record scaduti
    vengono marcati `expired` qui (lazy) — il chiamante notifica."""
    now = time.time()
    out = []
    for rec in _load().values():
        if rec.get("state") != "pending":
            continue
        if rec.get("device_id") != device_id:
            continue
        if now > float(rec.get("expires_at") or 0):
            mark(rec["id"], "expired")
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
            mark(rec["id"], "expired")
            out.append({**rec, "state": "expired"})
    return out
