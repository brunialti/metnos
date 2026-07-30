#!/usr/bin/env python3
"""location_request — UX dialog per richiedere posizione utente quando manca.

Modulo runtime infrastructure (NON LLM-callable se non via il tool builtin
`request_location_from_user` registrato in agent_runtime.py).

Pattern parallelo a `cap_pending`:
- Salva state pending file-based (`~/.local/state/metnos/location_pending/<turn_id>.json`)
- Channel-agnostico: il rendering UI e' delegato al channel adapter
  (oggi solo Telegram). Per canali futuri (web/CLI/voice) basta aggiungere
  un'altra implementazione del prompt.

API:
    request(turn_id, actor, channel, original_query, goal, chat_id?, timeout_s)
        -> dict {pending_id, status:'awaiting'}
        Salva pending state, invoca channel adapter per UI.

    resolve(pending_id, lat, lon, source) -> dict {status:'resolved'}
        Rimuove pending, scrive locations.jsonl.

    cancel(pending_id) -> dict {status:'cancelled'}
        Rimuove pending senza salvare location.

    get_pending_for(owner_user_id, channel) -> dict | None
        Lookup attivo: il daemon lo chiama prima del normal dispatch per
        decidere se l'input utente va instradato al pending.

    sweep_expired() -> int
        Cleanup pending oltre timeout.

Sicurezza: niente effetti collaterali distruttivi. cancel/timeout sono
idempotenti. resolve scrive in locations.jsonl via location_store.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import fcntl
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import config as _C  # §7.11
PENDING_DIR = _C.PATH_USER_STATE / "location_pending"
LOCK_PATH = _C.PATH_USER_STATE / "location_pending.lock"
DEFAULT_TIMEOUT_S = 300


def _ensure_dir():
    _C.ensure_private_dir(PENDING_DIR)


def _path(pending_id: str) -> Path:
    return PENDING_DIR / f"{pending_id}.json"


@contextmanager
def _pending_lock(*, exclusive: bool):
    _C.ensure_private_dir(LOCK_PATH.parent)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(
            descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def request(*, turn_id: str, actor: str, owner_user_id: str,
            channel: str, original_query: str, goal: str,
            chat_id: Optional[str] = None,
            timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Salva pending state. NON invia messaggi al canale (separation of
    concerns: il channel adapter del daemon vede l'esito del turno e fa il
    rendering). Ritorna metadata che il runtime propaga al daemon via
    TurnLog.pending_location.
    """
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("location pending owner_user_id is required")
    _ensure_dir()
    pending_id = uuid.uuid4().hex[:16]
    now = time.time()
    record = {
        "pending_id": pending_id,
        "turn_id": turn_id,
        "owner_user_id": owner,
        "actor": actor,
        "channel": channel,
        "chat_id": chat_id,
        "original_query": original_query,
        "goal": goal,
        "ts_created": now,
        "ts_expires": now + timeout_s,
        "status": "awaiting",
    }
    with _pending_lock(exclusive=True):
        _C.write_private_text(
            _path(pending_id), json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "pending_id": pending_id,
        "status": "awaiting",
        "channel": channel,
        "chat_id": chat_id,
        "goal": goal,
        "original_query": original_query,
        "expires_in_s": timeout_s,
    }


def resolve(pending_id: str, lat: float, lon: float, *,
            owner_user_id: str, source: str,
            accuracy: Optional[float] = None) -> dict:
    """Risolve pending: scrive locations.jsonl + rimuove file."""
    owner = str(owner_user_id or "").strip()
    if not owner:
        return {"status": "unknown", "error": "logical owner unavailable"}
    p = _path(pending_id)
    with _pending_lock(exclusive=True):
        try:
            record = json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {"status": "unknown",
                    "error": "pending_id not found or expired"}
        if str(record.get("owner_user_id") or "") != owner:
            return {"status": "unknown",
                    "error": "pending_id not found or expired"}
        try:
            from location_store import record_location
            record_location(
                owner_user_id=owner, actor=record.get("actor") or "",
                lat=lat, lon=lon, accuracy=accuracy,
                channel=record["channel"], source=source)
        except Exception as e:
            return {"status": "error",
                    "error": f"record_location failed: {e}"}
        p.unlink(missing_ok=True)
    return {
        "status": "resolved",
        "pending_id": pending_id,
        "turn_id": record["turn_id"],
        "actor": record["actor"],
        "channel": record["channel"],
        "chat_id": record.get("chat_id"),
        "original_query": record["original_query"],
        "goal": record["goal"],
        "lat": lat, "lon": lon, "source": source,
    }


def cancel(pending_id: str, *, owner_user_id: str) -> dict:
    """Rimuove pending senza salvare location."""
    owner = str(owner_user_id or "").strip()
    if not owner:
        return {"status": "unknown", "error": "logical owner unavailable"}
    p = _path(pending_id)
    with _pending_lock(exclusive=True):
        try:
            record = json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {"status": "unknown",
                    "error": "pending_id not found or expired"}
        if str(record.get("owner_user_id") or "") != owner:
            return {"status": "unknown",
                    "error": "pending_id not found or expired"}
        p.unlink(missing_ok=True)
    return {
        "status": "cancelled",
        "pending_id": pending_id,
        "turn_id": record["turn_id"],
        "actor": record["actor"],
        "channel": record["channel"],
        "chat_id": record.get("chat_id"),
        "original_query": record["original_query"],
    }


def get_pending_for(owner_user_id: str, channel: str) -> Optional[dict]:
    """Lookup pending attivo per (owner UUID, channel). Ritorna il più recente.
    Usato dal daemon prima del normal dispatch per intercettare la risposta
    dell'utente al prompt di location."""
    owner = str(owner_user_id or "").strip()
    if not owner or not PENDING_DIR.exists():
        return None
    candidates = []
    now = time.time()
    with _pending_lock(exclusive=False):
        for p in PENDING_DIR.glob("*.json"):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (str(r.get("owner_user_id") or "") != owner
                    or r.get("channel") != channel):
                continue
            if r.get("ts_expires", 0) < now:
                continue
            candidates.append((r["ts_created"], r))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def sweep_expired() -> int:
    """Rimuove pending scaduti. Ritorna n pulizie."""
    if not PENDING_DIR.exists():
        return 0
    now = time.time()
    n = 0
    with _pending_lock(exclusive=True):
        for p in PENDING_DIR.glob("*.json"):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                p.unlink(missing_ok=True)
                n += 1
                continue
            if r.get("ts_expires", 0) < now:
                p.unlink(missing_ok=True)
                n += 1
    return n


def _purge(*, owner_user_id: str | None = None,
           unscoped: bool = False) -> int:
    if not PENDING_DIR.exists():
        return 0
    owner = str(owner_user_id or "").strip()
    removed = 0
    with _pending_lock(exclusive=True):
        for path in tuple(PENDING_DIR.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = {}
            if ((owner and str(record.get("owner_user_id") or "") == owner)
                    or (unscoped and not record.get("owner_user_id"))):
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
    return removed


def purge_owner(owner_user_id: str) -> int:
    owner = str(owner_user_id or "").strip()
    return _purge(owner_user_id=owner) if owner else 0


def purge_unscoped() -> int:
    return _purge(unscoped=True)


# ---- Forward geocoding fallback (testo libero -> coords) ----------------

def try_geocode_text(text: str) -> Optional[dict]:
    """Tenta forward_geocode di testo libero (indirizzo/CAP/citta') a coords
    via wrapper geo_provider (chain configurabile). Ritorna
    {lat, lon, address, source} oppure None se nessun match."""
    try:
        from geo_provider import forward_search
        matches, src = forward_search(text.strip(), max_results=1)
    except Exception:
        return None
    if not matches:
        return None
    m = matches[0]
    return {
        "lat": float(m["lat"]),
        "lon": float(m["lon"]),
        "address": m.get("address"),
        "source": f"{src}_text:{text.strip()[:80]}",
    }
