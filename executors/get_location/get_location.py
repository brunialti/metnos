#!/usr/bin/env python3
"""get_location — executor di Metnos v1.1 (eccezione singolare per op unica).

Ritorna l'ultima posizione condivisa dall'utente attraverso un canale
(Telegram 📎 Posizione, futuro: GPS smartphone, altri sensori).

Storage: `~/.local/share/metnos/locations.jsonl` (append-only). Il channel
daemon Telegram intercetta gli eventi `location` di Telegram e li scrive
qui via `runtime/location_store.record_location`.

Singolare per design: l'utente ha UNA posizione corrente per actor; la
storia (lista posizioni nel tempo) sara' un futuro `list_locations` se
servira'.

Contratto:
    stdin: JSON {actor?: str = "host"}
    stdout: JSON {ok, location: {lat, lon, ts, accuracy?, channel}, age_seconds}
            oppure {ok: false, error: "no location received yet ..."}.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from location_store import get_last_location  # noqa: E402


def invoke(args):
    actor = args.get("actor") or "host"
    if not isinstance(actor, str):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="actor")}
    rec = get_last_location(actor)
    if rec is None:
        return {
            "ok": False,
            "error": _msg("ERR_NO_LOCATION_YET"),
        }
    return {
        "ok": True,
        "location": {
            "lat": rec["lat"],
            "lon": rec["lon"],
            "ts": rec["ts"],
            "accuracy": rec.get("accuracy"),
            "channel": rec.get("channel"),
        },
        "age_seconds": int(time.time() - rec["ts"]),
        "actor": actor,
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
