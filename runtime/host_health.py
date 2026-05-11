#!/usr/bin/env python3
"""host_health — tracker per-host di response code 429/503 (ADR 0108).

Auto-degrade T2→T1 quando un host risponde 3+ volte 429 o 503 negli ultimi
60 minuti: il runtime aggiunge automaticamente l'host a
`~/.config/metnos/blocked_origins.json` con TTL 24h. Trascorse le 24h, il
TTL scade e l'host torna in T2 senza intervento.

Determinismo §7.9: nessun LLM, contatori puri sliding-window.

Storage:
- Stato volatile: `~/.local/share/metnos/host_health.json`
  shape: {"hosts": {<host>: {"events": [{"ts":float,"code":int}, ...]}}}
  Solo eventi 429/503 vengono persistiti; 200 reset solo dei contatori.

- blocked_origins: `~/.config/metnos/blocked_origins.json`
  shape: {"hosts": [<host>, ...], "ttl": {<host>: <expire_epoch>}}

API:
    record_response(host, code) -> None
    maybe_block_host(host) -> bool          # True se aggiunto blocked
    is_blocked(host) -> bool                # consulta blocked + TTL expire
    cleanup_expired() -> int                # rimuove TTL scaduti
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "share" / "metnos"
HEALTH_PATH = STATE_DIR / "host_health.json"
CONFIG_DIR = Path.home() / ".config" / "metnos"
BLOCKED_PATH = CONFIG_DIR / "blocked_origins.json"

# Sliding-window: eventi piu' vecchi vengono potati.
WINDOW_S = 60 * 60          # 60 min
ERROR_THRESHOLD = 3         # 3 eventi 429/503 in window
BLOCK_TTL_S = 24 * 3600     # 24h

_LOCK = threading.Lock()


def _read_json(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_json_atomic(p: Path, data: dict) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    tmp = p.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.rename(tmp, p)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _prune_events(events: list, now: float) -> list:
    """Mantieni solo eventi nella sliding-window."""
    return [e for e in events if (now - float(e.get("ts", 0))) <= WINDOW_S]


def record_response(host: str, code: int) -> None:
    """Registra una response. Solo 429/503 vengono persistiti.

    code=200 (success) → potatura della finestra (resetta backoff
    progressivo se l'host e' tornato sano).
    """
    if not host:
        return
    code = int(code)
    with _LOCK:
        data = _read_json(HEALTH_PATH)
        hosts = data.setdefault("hosts", {})
        entry = hosts.setdefault(host, {"events": []})
        now = time.time()
        if code in (429, 503):
            entry["events"].append({"ts": now, "code": code})
        # potatura comunque (anche su 200): garbage collection sliding-window
        entry["events"] = _prune_events(entry.get("events", []), now)
        if not entry["events"]:
            # rimuovi host se finestra vuota (compatta lo state)
            hosts.pop(host, None)
        _write_json_atomic(HEALTH_PATH, data)


def _count_errors(host: str) -> int:
    data = _read_json(HEALTH_PATH)
    entry = (data.get("hosts") or {}).get(host) or {}
    events = _prune_events(entry.get("events", []), time.time())
    return len(events)


def maybe_block_host(host: str) -> bool:
    """Se host ha >= ERROR_THRESHOLD eventi in window, aggiungilo a
    blocked_origins.json con TTL 24h. Idempotente. Ritorna True se
    aggiunto/refresh, False altrimenti."""
    if not host:
        return False
    if _count_errors(host) < ERROR_THRESHOLD:
        return False
    with _LOCK:
        blocked = _read_json(BLOCKED_PATH)
        hosts = list(blocked.get("hosts") or [])
        ttl_map = dict(blocked.get("ttl") or {})
        expire = time.time() + BLOCK_TTL_S
        # cleanup expired prima di scrivere
        ttl_map = {h: t for h, t in ttl_map.items() if float(t) > time.time()}
        hosts = [h for h in hosts if (h in ttl_map) or (h == host)]
        if host not in hosts:
            hosts.append(host)
        ttl_map[host] = expire
        blocked["hosts"] = sorted(set(hosts))
        blocked["ttl"] = ttl_map
        _write_json_atomic(BLOCKED_PATH, blocked)
    return True


def is_blocked(host: str) -> bool:
    """Consulta blocked_origins + TTL. Cleanup expired inline."""
    if not host:
        return False
    blocked = _read_json(BLOCKED_PATH)
    ttl_map = blocked.get("ttl") or {}
    expire = ttl_map.get(host)
    if expire is None:
        # Host listato manualmente senza TTL: rispetta come permanente.
        return host in (blocked.get("hosts") or [])
    if float(expire) <= time.time():
        # scaduto: cleanup lazy
        cleanup_expired()
        return False
    return True


def cleanup_expired() -> int:
    """Rimuovi entries con TTL scaduto. Ritorna count rimossi.

    Non tocca host listati manualmente (senza TTL).
    """
    with _LOCK:
        blocked = _read_json(BLOCKED_PATH)
        ttl_map = dict(blocked.get("ttl") or {})
        hosts = list(blocked.get("hosts") or [])
        now = time.time()
        expired = [h for h, t in ttl_map.items() if float(t) <= now]
        if not expired:
            return 0
        for h in expired:
            ttl_map.pop(h, None)
            if h in hosts:
                hosts.remove(h)
        blocked["hosts"] = sorted(set(hosts))
        blocked["ttl"] = ttl_map
        _write_json_atomic(BLOCKED_PATH, blocked)
        return len(expired)


if __name__ == "__main__":
    # smoke
    record_response("example.com", 429)
    record_response("example.com", 503)
    record_response("example.com", 429)
    print("blocked:", maybe_block_host("example.com"))
    print("is_blocked:", is_blocked("example.com"))
    print("cleanup:", cleanup_expired())
