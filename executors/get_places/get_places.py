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
import math
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


def _failure(error_code, error, *, error_class="invalid_input"):
    return {
        "ok": False,
        "ok_count": 0,
        "fail_count": 0,
        "entries": [],
        "failed": [],
        "places_resolved": 0,
        "places_unknown": 0,
        "places_failed": 0,
        "error_class": error_class,
        "error_code": error_code,
        "error": error,
    }


def invoke(args):
    if not isinstance(args, dict):
        return _failure(
            "args_not_object",
            _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
        )
    coords = args.get("coords")
    entries_in = args.get("entries")
    if coords is None and entries_in is None:
        return _failure(
            "coordinates_missing",
            _msg("ERR_ARG_MISSING_ONE_OF", options="coords, entries"),
        )
    if coords is not None and entries_in is not None:
        return _failure(
            "coordinates_conflict",
            _msg("ERR_INVALID_ARGS", detail="coords XOR entries"),
        )
    use_entries = entries_in is not None
    items = entries_in if use_entries else coords
    if not isinstance(items, list):
        arg = "entries" if use_entries else "coords"
        return _failure(
            f"{arg}_not_list",
            _msg("ERR_ARG_NOT_LIST", arg=arg),
        )

    out, failed = [], []
    p_resolved = p_unknown = p_failed = 0

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            failed.append({
                "index": i,
                "error_class": "invalid_input",
                "error_code": "coordinate_item_not_object",
                "error": _msg("ERR_ARG_NOT_DICT", arg="item"),
            })
            continue
        gps = (item.get("gps") or {}) if use_entries else item
        try:
            lat = float(gps.get("lat"))
            lon = float(gps.get("lon"))
            valid = (math.isfinite(lat) and math.isfinite(lon)
                     and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0)
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            if use_entries:
                enriched = dict(item)
                enriched["place"] = "unknown"
                out.append(enriched)
                p_unknown += 1
            else:
                failed.append({
                    "index": i,
                    "error_class": "invalid_input",
                    "error_code": "coordinates_invalid",
                    "error": _msg(
                        "ERR_ARG_INVALID", arg="lat/lon",
                        reason="finite coordinates in geographic range required",
                    ),
                })
            continue
        try:
            place = reverse_geocode(lat, lon)
        except Exception:
            place = None
        if not place:
            p_failed += 1
            failed.append({
                "index": i,
                "lat": lat,
                "lon": lon,
                "error_class": "dependency_unavailable",
                "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": _msg("ERR_EXT_SVC_UNAVAILABLE"),
            })
            continue
        p_resolved += 1
        if use_entries:
            enriched = dict(item)
            enriched["place"] = place
            out.append(enriched)
        else:
            out.append({"lat": lat, "lon": lon, "place": place})

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
    if out and failed:
        response["partial"] = True
    if failed:
        primary = failed[0]
        response["error_class"] = primary["error_class"]
        response["error_code"] = primary["error_code"]
        response["error"] = primary["error"]
    if p_failed > 0:
        response["place_warning"] = msg("WARN_EXT_SVC_DEGRADED")
    return response


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
