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
import sys
import time

sys.path.insert(0, "/opt/myclaw/runtime")
from location_store import get_last_location  # noqa: E402


def invoke(args):
    actor = args.get("actor") or "host"
    if not isinstance(actor, str):
        return {"ok": False, "error": "arg 'actor' must be a string"}
    rec = get_last_location(actor)
    if rec is None:
        return {
            "ok": False,
            "error": f"no location received yet for actor='{actor}'. Condividi una posizione su Telegram con il bottone 📎 e riprova.",
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
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
