#!/usr/bin/env python3
"""
get_files — executor di Metnos v1.1.

Estrae metadata da file (immagini per ora). Vettoriale per costruzione:
una sola call processa una lista di entries. Copre anche le date semantiche
EXIF/birth (fields=['dates.semantic']).

Fields supportati (selezionabili via `fields`, default ['dates.semantic']):
  dates.semantic      date_epoch + date_source ('exif' | 'mtime')
  dates.created       date_created_epoch (solo EXIF DateTimeOriginal)
  dates.modified      date_modified_epoch (mtime)
  gps                 gps: {lat, lon} (None se assente nel file)
  place               place: str slug (Nominatim reverse-geocode);
                      'unknown' se gps assente; 'rate_limited' se 429.
  device              device: {make, model}
  image_dimensions    image_dimensions: {width, height}

Reverse-geocoding via Nominatim public:
  - User-Agent identificativo, throttle ≥1.1s tra chiamate,
    cache locale SQLite in ~/.local/share/metnos/geo_cache.sqlite
    (chiave: lat/lon arrotondati a 5 decimali, ~1m).
  - Limite TOS: ~1000 req/giorno; alla 5a 429 consecutiva l'executor
    si ferma e suggerisce di rivolgersi all'amministratore.

Contratto:
    stdin:  JSON con args (entries: list[dict], fields?: list[str] | 'all')
    stdout: JSON {ok, ok_count, fail_count, entries, failed,
                  places_resolved?, places_unknown?, places_failed?,
                  place_warning?}
            `entries` e' la lista di INPUT arricchita coi field richiesti
            (mantiene il nome dell'input, convenzione "executor che
            arricchisce entries ritorna entries").
"""
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as msg
_msg = msg  # alias: alcuni rami di validazione usano _msg (unifica i nomi)

ALL_FIELDS =["dates.semantic", "dates.created", "dates.modified", "gps", "place", "device", "image_dimensions", "size"]


def _exif(path):
    try:
        from PIL import Image
        return (Image.open(path)._getexif() or {})
    except Exception:
        return {}


def _read_exif_date_created_epoch(exif):
    try:
        from PIL.ExifTags import TAGS
    except ImportError:
        return None
    for tid, v in exif.items():
        if TAGS.get(tid) == "DateTimeOriginal":
            try:
                dt = datetime.datetime.strptime(v, "%Y:%m:%d %H:%M:%S")
                return dt.timestamp()
            except (ValueError, TypeError):
                return None
    return None


def _read_gps(exif):
    try:
        from PIL.ExifTags import TAGS
    except ImportError:
        return None
    gps_raw = None
    for tid, v in exif.items():
        if TAGS.get(tid) == "GPSInfo":
            gps_raw = v
            break
    if not gps_raw:
        return None
    def _to_dd(coord, ref):
        if not coord:
            return None
        try:
            d, m, s = coord
            dd = float(d) + float(m) / 60.0 + float(s) / 3600.0
        except (ValueError, TypeError):
            return None
        if ref in ("S", "W"):
            dd = -dd
        return dd
    lat = _to_dd(gps_raw.get(2), gps_raw.get(1))
    lon = _to_dd(gps_raw.get(4), gps_raw.get(3))
    if lat is None or lon is None:
        return None
    return {"lat": round(lat, 6), "lon": round(lon, 6)}


def _read_device(exif):
    try:
        from PIL.ExifTags import TAGS
    except ImportError:
        return None
    out = {}
    for tid, v in exif.items():
        tag = TAGS.get(tid)
        if tag == "Make":
            s = (str(v) if v else "").strip()
            if s:
                out["make"] = s
        elif tag == "Model":
            s = (str(v) if v else "").strip()
            if s:
                out["model"] = s
    return out or None


def _read_image_dimensions(path):
    try:
        from PIL import Image
        img = Image.open(path)
        return {"width": img.width, "height": img.height}
    except Exception:
        return None


def _slugify_place(name):
    if not name:
        return None
    first = name.split(",")[0].strip().lower()
    out = []
    for c in first:
        if c.isalnum():
            out.append(c)
        elif c in (" ", "-", "/"):
            out.append("_")
    s = "".join(out).strip("_")
    return s or None


def invoke(args):
    entries = args.get("entries")
    paths = args.get("paths")
    fields = args.get("fields")
    if fields is None:
        fields = ["dates.semantic"]
    if fields == "all":
        fields = list(ALL_FIELDS)
    # Forma B (literal letterale, the design guide §4.2): se entries non e' fornito ma
    # paths si', costruisci entries=[{path:p} for p in paths]. Caso atomico
    # "metadata di /tmp/foo.txt".
    if entries is None and isinstance(paths, list):
        entries = [{"path": p} for p in paths if isinstance(p, str)]
    if not isinstance(entries, list):
        return {"ok": False, "error": _msg("ERR_ARG_MISSING_ONE_OF", options="entries, paths")}
    if not isinstance(fields, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="fields", of="strings | 'all'")}
    # §2.8/§2.4: un campo inventato dall'LLM (es. 'exif_datetime'/'date' invece
    # del canonico 'dates.semantic') NON deve far fallire l'arricchimento. Scarta
    # gli sconosciuti e prosegui coi validi; se nessuno resta, default a
    # 'dates.semantic' (l'intento "dammi i metadata/data della foto" è comunque
    # soddisfatto). Bug q33 5/6: la lista foto si perdeva per un nome inventato.
    known = [f for f in fields if f in ALL_FIELDS]
    unknown_fields = [f for f in fields if f not in ALL_FIELDS]
    fields = known or ["dates.semantic"]

    fset = set(fields)
    need_geo = "place" in fset
    need_gps = need_geo or "gps" in fset
    need_device = "device" in fset
    need_dims = "image_dimensions" in fset
    need_size = "size" in fset
    need_d_sem = "dates.semantic" in fset
    need_d_cre = "dates.created" in fset
    need_d_mod = "dates.modified" in fset
    need_exif = need_d_sem or need_d_cre or need_gps or need_device

    enriched, failed = [], []
    p_resolved = p_unknown = p_failed = 0

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            failed.append({"index": i, "error": _msg("ERR_ARG_NOT_DICT", arg="entry")})
            continue
        src = entry.get("path") or entry.get("src")
        if not isinstance(src, str) or not src:
            failed.append({"index": i, "error": _msg("ERR_ARG_MISSING", arg="path")})
            continue
        path = Path(os.path.expanduser(src)).resolve()
        if not path.exists():
            failed.append({"index": i, "path": str(path), "error": _msg("ERR_PATH_NOT_FOUND", path=str(path))})
            continue
        out = dict(entry)
        out["path"] = str(path)

        exif = _exif(path) if need_exif else {}

        if need_d_sem:
            de = _read_exif_date_created_epoch(exif)
            if de is not None:
                out["date_epoch"] = de
                out["date_source"] = "exif"
            else:
                try:
                    out["date_epoch"] = path.stat().st_mtime
                    out["date_source"] = "mtime"
                except OSError:
                    out["date_epoch"] = 0
                    out["date_source"] = "unknown"
        if need_d_cre:
            out["date_created_epoch"] = _read_exif_date_created_epoch(exif)
        if need_d_mod:
            try:
                out["date_modified_epoch"] = path.stat().st_mtime
            except OSError:
                out["date_modified_epoch"] = None

        gps = None
        if need_gps:
            gps = _read_gps(exif)
            out["gps"] = gps

        if need_geo:
            if gps is None:
                out["place"] = "unknown"
                p_unknown += 1
            else:
                # Geo provider unico via wrapper (1/5/2026): chain
                # configurabile via env METNOS_GEO_PROVIDERS.
                from geo_provider import reverse_geocode as _geo_rev
                place = _geo_rev(gps["lat"], gps["lon"])
                out["place"] = place or "unknown"
                if place:
                    p_resolved += 1
                else:
                    p_failed += 1

        if need_device:
            out["device"] = _read_device(exif)
        if need_dims:
            out["image_dimensions"] = _read_image_dimensions(path)
        if need_size:
            try:
                out["size_bytes"] = path.stat().st_size
            except OSError:
                out["size_bytes"] = None

        enriched.append(out)

    # geo_conn cleanup non piu' necessario (Photon cache server-side)

    response = {
        "ok": len(failed) == 0,
        "ok_count": len(enriched),
        "fail_count": len(failed),
        "entries": enriched,
        "failed": failed,
    }
    if need_geo:
        response["places_resolved"] = p_resolved
        response["places_unknown"] = p_unknown
        response["places_failed"] = p_failed
        if p_failed > 0:
            response["warn_code"] = "WARN_EXT_SVC_DEGRADED"
            response["place_warning"] = msg("WARN_EXT_SVC_DEGRADED")
    return response


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
