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

    get_pending_for(actor, channel) -> dict | None
        Lookup attivo: il daemon lo chiama prima del normal dispatch per
        decidere se l'input utente va instradato al pending.

    sweep_expired() -> int
        Cleanup pending oltre timeout.

Sicurezza: niente effetti collaterali distruttivi. cancel/timeout sono
idempotenti. resolve scrive in locations.jsonl via location_store.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

import config as _C  # §7.11
PENDING_DIR = _C.PATH_USER_STATE / "location_pending"
DEFAULT_TIMEOUT_S = 300


def _ensure_dir():
    PENDING_DIR.mkdir(parents=True, exist_ok=True)


def _path(pending_id: str) -> Path:
    return PENDING_DIR / f"{pending_id}.json"


def request(*, turn_id: str, actor: str, channel: str, original_query: str,
            goal: str, chat_id: Optional[str] = None,
            timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Salva pending state. NON invia messaggi al canale (separation of
    concerns: il channel adapter del daemon vede l'esito del turno e fa il
    rendering). Ritorna metadata che il runtime propaga al daemon via
    TurnLog.pending_location.
    """
    _ensure_dir()
    pending_id = uuid.uuid4().hex[:16]
    now = time.time()
    record = {
        "pending_id": pending_id,
        "turn_id": turn_id,
        "actor": actor,
        "channel": channel,
        "chat_id": chat_id,
        "original_query": original_query,
        "goal": goal,
        "ts_created": now,
        "ts_expires": now + timeout_s,
        "status": "awaiting",
    }
    _path(pending_id).write_text(json.dumps(record, ensure_ascii=False) + "\n")
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
            source: str, accuracy: Optional[float] = None) -> dict:
    """Risolve pending: scrive locations.jsonl + rimuove file."""
    p = _path(pending_id)
    if not p.exists():
        return {"status": "unknown", "error": "pending_id not found or expired"}
    record = json.loads(p.read_text())
    # Persisti in locations.jsonl via location_store
    try:
        from location_store import record_location
        record_location(actor=record["actor"], lat=lat, lon=lon,
                        accuracy=accuracy, channel=record["channel"])
    except Exception as e:
        return {"status": "error", "error": f"record_location failed: {e}"}
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


def cancel(pending_id: str) -> dict:
    """Rimuove pending senza salvare location."""
    p = _path(pending_id)
    if not p.exists():
        return {"status": "unknown", "error": "pending_id not found or expired"}
    record = json.loads(p.read_text())
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


def get_pending_for(actor: str, channel: str) -> Optional[dict]:
    """Lookup pending attivo per (actor, channel). Ritorna il piu' recente.
    Usato dal daemon prima del normal dispatch per intercettare la risposta
    dell'utente al prompt di location."""
    if not PENDING_DIR.exists():
        return None
    candidates = []
    now = time.time()
    for p in PENDING_DIR.glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("actor") != actor or r.get("channel") != channel:
            continue
        if r.get("ts_expires", 0) < now:
            continue  # scaduto, ignora (sweep separato lo elimina)
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
    for p in PENDING_DIR.glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            p.unlink(missing_ok=True)
            n += 1
            continue
        if r.get("ts_expires", 0) < now:
            p.unlink(missing_ok=True)
            n += 1
    return n


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
