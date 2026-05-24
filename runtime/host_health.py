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

import config as _C  # §7.11

STATE_DIR = _C.PATH_USER_DATA
HEALTH_PATH = STATE_DIR / "host_health.json"
CONFIG_DIR = _C.PATH_USER_CONFIG
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


# ── System health sensors (riusable cross-executor) ──────────────────
# Single source of truth per la lettura di sensori HW (thermal/power/...).
# Pattern §7.3 generale: una sola funzione `collect_*` per family, riusata
# da get_processes (mostra), scheduler builtin_callbacks (alert), e
# eventuali futuri consumer (monitor dashboards).


def collect_thermal() -> dict:
    """Termiche da `/sys/class/hwmon/<hN>/temp*_input` (millicelsius).

    Estratto da `executors/get_processes/get_processes.py::_read_thermal`
    (24/5/2026) per condivisione con `scheduler_v2.builtin_callbacks
    .task_temp_threshold_alert` (alert deterministic). Determinismo §7.9:
    solo I/O sysfs, nessun comando esterno, nessun LLM.

    Targeting AMD Strix Halo + NVMe: sensori canonici `k10temp` (CPU AMD),
    `amdgpu` (GPU edge), `nvme` (Composite). Su altre piattaforme alcuni
    mancheranno: ritorna `available: false` solo se nessun sensore noto
    e' presente.

    Returns:
      dict {available: bool, cpu_c?: int, gpu_c?: int, nvme_c?: int}
    """
    targets = {"k10temp": "cpu_c", "amdgpu": "gpu_c", "nvme": "nvme_c"}
    out: dict = {"available": False}
    base = "/sys/class/hwmon"
    if not os.path.isdir(base):
        return out
    try:
        for hwmon_dir in sorted(os.listdir(base)):
            hpath = os.path.join(base, hwmon_dir)
            try:
                with open(os.path.join(hpath, "name")) as f:
                    name = f.read().strip()
            except OSError:
                continue
            key = targets.get(name)
            if not key or key in out:
                continue
            inputs = [p for p in os.listdir(hpath) if p.endswith("_input")]
            if not inputs:
                continue
            chosen = "temp1_input" if "temp1_input" in inputs else sorted(inputs)[0]
            try:
                with open(os.path.join(hpath, chosen)) as f:
                    raw = f.read().strip()
                if raw and raw.lstrip("-").isdigit():
                    out[key] = int(raw) // 1000
                    out["available"] = True
            except OSError:
                continue
    except OSError:
        return out
    return out
