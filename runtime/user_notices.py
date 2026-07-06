"""user_notices — avvisi «prossima-visita» per (channel, actor). A.2 fase 7.

Un evento server-side fuori-turno (es. invocazione remota ABBANDONATA dal
turno che il device completa più tardi — A.0) deve raggiungere l'utente:
telegram/web non hanno un push server-iniziato, quindi v1 = coda per
destinatario, drenata e ANTEPOSTA al final del PRIMO turno successivo dello
stesso (channel, actor). Append-only jsonl, flock, TTL.

API: append(channel, actor, text) · drain(channel, actor) -> list[str].
Consumer: agent_runtime.TurnLog.write() (choke-point di ogni turno).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path

import config as _C  # §7.11

NOTICES_DIR = _C.PATH_USER_DATA / "user_notices"
TTL_S = 7 * 24 * 3600  # avvisi più vecchi scartati al drain


def _key(channel: str, actor: str) -> str:
    raw = f"{channel or 'any'}_{actor or 'host'}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:80]


def _path(channel: str, actor: str) -> Path:
    return NOTICES_DIR / f"{_key(channel, actor)}.jsonl"


def append(channel: str, actor: str, text: str) -> None:
    """Accoda un avviso per il destinatario. Fail-open (mai bloccare il
    chiamante: è un canale best-effort)."""
    if not text:
        return
    try:
        NOTICES_DIR.mkdir(parents=True, exist_ok=True)
        p = _path(channel, actor)
        line = json.dumps({"ts": time.time(), "text": text},
                          ensure_ascii=False) + "\n"
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception:
        pass


def drain(channel: str, actor: str) -> list[str]:
    """Ritorna e SVUOTA gli avvisi pendenti del destinatario (entro TTL).
    Atomico via flock + unlink. Fail-open: errore → lista vuota."""
    p = _path(channel, actor)
    if not p.exists():
        return []
    out: list[str] = []
    try:
        fd = os.open(p, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            data = os.read(fd, 4 * 1024 * 1024).decode("utf-8", "replace")
            os.unlink(p)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        now = time.time()
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if now - float(rec.get("ts") or 0) <= TTL_S and rec.get("text"):
                out.append(str(rec["text"]))
    except FileNotFoundError:
        return []
    except Exception:
        return out
    return out
