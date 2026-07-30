"""Owner-scoped notices delivered on the next visit.

Un evento server-side fuori-turno (es. invocazione remota ABBANDONATA dal
turno che il device completa più tardi — A.0) deve raggiungere l'utente:
telegram/web non hanno un push server-iniziato, quindi v1 = coda per
destinatario, drenata e ANTEPOSTA al final del PRIMO turno successivo dello
stesso (channel, actor). Append-only jsonl, flock, TTL.

API: append(..., owner_user_id=UUID) · drain(..., owner_user_id=UUID).
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


def _key(channel: str, owner_user_id: str) -> str:
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("notice owner_user_id is required")
    digest = __import__("hashlib").sha256(
        ("metnos-notice-owner-v1\0" + owner).encode("utf-8")
    ).hexdigest()[:20]
    raw = f"{channel or 'any'}_{digest}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:80]


def _path(channel: str, owner_user_id: str) -> Path:
    return NOTICES_DIR / f"{_key(channel, owner_user_id)}.jsonl"


def append(channel: str, actor: str, text: str, *,
           owner_user_id: str) -> None:
    """Accoda un avviso per il destinatario. Fail-open (mai bloccare il
    chiamante: è un canale best-effort)."""
    owner = str(owner_user_id or "").strip()
    if not text or not owner:
        return
    try:
        NOTICES_DIR.mkdir(parents=True, exist_ok=True)
        p = _path(channel, owner)
        line = json.dumps({"ts": time.time(), "text": text,
                           "owner_user_id": owner,
                           "actor": str(actor or "")},
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


def drain(channel: str, actor: str, *, owner_user_id: str) -> list[str]:
    """Ritorna e SVUOTA gli avvisi pendenti del destinatario (entro TTL).
    Atomico via flock + unlink. Fail-open: errore → lista vuota."""
    owner = str(owner_user_id or "").strip()
    if not owner:
        return []
    p = _path(channel, owner)
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
            if (str(rec.get("owner_user_id") or "") == owner
                    and now - float(rec.get("ts") or 0) <= TTL_S
                    and rec.get("text")):
                out.append(str(rec["text"]))
    except FileNotFoundError:
        return []
    except Exception:
        return out
    return out


def purge_owner(owner_user_id: str) -> int:
    """Physically remove every notice file for one immutable owner."""

    owner = str(owner_user_id or "").strip()
    if not owner or not NOTICES_DIR.exists():
        return 0
    removed = 0
    suffix = _key("any", owner).split("_", 1)[-1]
    for path in tuple(NOTICES_DIR.glob(f"*_{suffix}.jsonl")):
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def purge_unscoped() -> int:
    """Retire legacy actor-keyed files that carry no immutable owner."""

    if not NOTICES_DIR.exists():
        return 0
    removed = 0
    for path in tuple(NOTICES_DIR.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line.strip()]
        except (OSError, json.JSONDecodeError):
            records = []
        if not records or any(not row.get("owner_user_id") for row in records):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed
