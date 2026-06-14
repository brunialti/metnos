"""location_store.py — storage append-only delle posizioni condivise via canale.

Quando l'utente preme "📎 Posizione" su Telegram (o equivalente sui futuri
canali), il channel daemon riceve un evento `location` invece di un testo.
Questa libreria persiste le coordinate in un file JSONL, indicizzabile per
actor/channel/timestamp.

Format file: ~/.local/share/metnos/locations.jsonl (append-only, fsync).
Ogni record: {ts, actor, channel, lat, lon, accuracy?, source?}.

API:
  record_location(actor, channel, lat, lon, accuracy=None, source=None)
  get_last_location(actor="host") -> dict | None
"""
import fcntl
import json
import os
import time

import config as _C  # §7.11

DEFAULT_LOG = _C.PATH_USER_DATA / "locations.jsonl"


def _path():
    p = DEFAULT_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.touch()
    return p


def record_location(actor: str, channel: str, lat: float, lon: float,
                    accuracy: float | None = None, source: str | None = None) -> None:
    record = {
        "ts": time.time(),
        "actor": actor or "host",
        "channel": channel or "",
        "lat": float(lat),
        "lon": float(lon),
    }
    if accuracy is not None:
        record["accuracy"] = float(accuracy)
    if source:
        record["source"] = source
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def get_last_location(actor: str = "host") -> dict | None:
    p = DEFAULT_LOG
    if not p.exists():
        return None
    last = None
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("actor") == actor:
                last = rec
    return last
