#!/usr/bin/env python3
"""get_places — executor di Metnos v1.1.

Reverse-geocode: dato una lista di coordinate (o entries con campo `gps`),
arricchisce ogni elemento con `place: str` (slug del luogo).

Vettoriale per costruzione. Backend: Nominatim (default pubblico, override
via env METNOS_NOMINATIM_URL). Cache locale.

Due modalita' di input (mutuamente esclusive):
  A. coords=[{lat, lon}, ...] - lista pura di coordinate.
  B. entries=[{gps:{lat,lon}, ...}, ...] - lista di entries qualsiasi
     (es. da get_files) con campo `gps`. Le entries vengono
     ritornate ARRICCHITE col campo `place`. Le entries senza gps
     ricevono place='unknown'.

Contratto:
    stdin: JSON {coords?: [...], entries?: [...]}
    stdout: JSON {ok, ok_count, fail_count, entries, failed,
                  places_resolved, places_unknown, places_failed}
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
_msg = msg  # alias: alcuni rami di validazione usano _msg (unifica i nomi)
# Geo provider unico via wrapper (1/5/2026 v0.4.0): chain configurabile.
from geo_provider import reverse_geocode  # noqa: E402


def invoke(args):
    coords = args.get("coords")
    entries_in = args.get("entries")
    if coords is None and entries_in is None:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING_ONE_OF", options="coords, entries")}
    if coords is not None and entries_in is not None:
        return {"ok": False, "error": _msg("ERR_INVALID_ARGS", detail="coords XOR entries")}
    use_entries = entries_in is not None
    items = entries_in if use_entries else coords
    if not isinstance(items, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg=("entries" if use_entries else "coords"))}

    out, failed = [], []
    p_resolved = p_unknown = p_failed = 0
    aborted = False

    try:
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                failed.append({"index": i, "error": _msg("ERR_ARG_NOT_DICT", arg="item")})
                continue
            if use_entries:
                gps = item.get("gps") or {}
            else:
                gps = item
            try:
                lat = float(gps.get("lat"))
                lon = float(gps.get("lon"))
            except (TypeError, ValueError):
                if use_entries:
                    enriched = dict(item)
                    enriched["place"] = "unknown"
                    out.append(enriched)
                    p_unknown += 1
                else:
                    failed.append({"index": i, "error": _msg("ERR_ARG_INVALID", arg="lat/lon", reason="float")})
                continue
            place = reverse_geocode(lat, lon)
            if place:
                p_resolved += 1
            else:
                p_failed += 1
            if use_entries:
                enriched = dict(item)
                enriched["place"] = place or "unknown"
                out.append(enriched)
            else:
                out.append({"lat": lat, "lon": lon, "place": place or "unknown"})
    finally:
        pass  # Photon: no cache locale da chiudere (cache server-side)

    response = {
        "ok": len(failed) == 0,
        "ok_count": len(out),
        "fail_count": len(failed),
        "entries": out,
        "failed": failed,
        "places_resolved": p_resolved,
        "places_unknown": p_unknown,
        "places_failed": p_failed,
    }
    if p_failed > 0 and not aborted:
        response["place_warning"] = msg("WARN_EXT_SVC_DEGRADED")
    return response


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
