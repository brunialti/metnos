#!/usr/bin/env python3
"""
get_files_metadata — executor di Metnos v1.1.

Estrae metadata da file (immagini per ora). Vettoriale per costruzione:
una sola call processa una lista di entries. Sostituisce get_file_dates
(deprecato).

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
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/myclaw/runtime")
from messages import get as msg

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "Metnos/0.1 (roberto.brunialti@knowcastle.com)"
THROTTLE_SECONDS = 1.1
GEO_CACHE = Path.home() / ".local" / "share" / "metnos" / "geo_cache.sqlite"

ALL_FIELDS = ["dates.semantic", "dates.created", "dates.modified", "gps", "place", "device", "image_dimensions"]


def _open_geo_cache():
    GEO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(GEO_CACHE), timeout=10.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS geo_cache ("
        "lat_r REAL, lon_r REAL, place TEXT, ts REAL, "
        "PRIMARY KEY (lat_r, lon_r))"
    )
    return conn


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


_last_call = [0.0]


def _reverse_geocode(lat, lon, conn):
    """Returns (place, source). source: 'cache' | 'nominatim' | 'rate_limited' | 'error'."""
    lat_r, lon_r = round(lat, 5), round(lon, 5)
    row = conn.execute(
        "SELECT place FROM geo_cache WHERE lat_r = ? AND lon_r = ?",
        (lat_r, lon_r),
    ).fetchone()
    if row is not None:
        return (row[0] or "unknown"), "cache"
    elapsed = time.monotonic() - _last_call[0]
    if elapsed < THROTTLE_SECONDS:
        time.sleep(THROTTLE_SECONDS - elapsed)
    _last_call[0] = time.monotonic()
    qs = urllib.parse.urlencode({
        "format": "json",
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "zoom": 10,
        "accept-language": "it",
        "email": "roberto.brunialti@knowcastle.com",
    })
    req = urllib.request.Request(f"{NOMINATIM_URL}?{qs}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (429, 403):
            return "rate_limited", "rate_limited"
        return "unknown", "error"
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return "unknown", "error"
    addr = data.get("address") or {}
    place = None
    for key in ("town", "city", "village", "municipality", "hamlet", "county", "state"):
        if key in addr:
            place = _slugify_place(addr[key])
            if place:
                break
    if not place:
        place = _slugify_place(data.get("display_name") or "")
    place = place or "unknown"
    try:
        conn.execute(
            "INSERT OR REPLACE INTO geo_cache (lat_r, lon_r, place, ts) VALUES (?,?,?,?)",
            (lat_r, lon_r, place, time.time()),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    return place, "nominatim"


def invoke(args):
    entries = args.get("entries")
    paths = args.get("paths")
    fields = args.get("fields")
    if fields is None:
        fields = ["dates.semantic"]
    if fields == "all":
        fields = list(ALL_FIELDS)
    # Forma B (literal letterale, CLAUDE.md §4.2): se entries non e' fornito ma
    # paths si', costruisci entries=[{path:p} for p in paths]. Caso atomico
    # "metadata di /tmp/foo.txt".
    if entries is None and isinstance(paths, list):
        entries = [{"path": p} for p in paths if isinstance(p, str)]
    if not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid required arg 'entries' (must be a list) or 'paths' (list of strings)"}
    if not isinstance(fields, list):
        return {"ok": False, "error": "fields must be a list of strings or 'all'"}
    unknown_fields = [f for f in fields if f not in ALL_FIELDS]
    if unknown_fields:
        return {"ok": False, "error": f"unknown fields: {unknown_fields}; supported: {ALL_FIELDS}"}

    fset = set(fields)
    need_geo = "place" in fset
    need_gps = need_geo or "gps" in fset
    need_device = "device" in fset
    need_dims = "image_dimensions" in fset
    need_d_sem = "dates.semantic" in fset
    need_d_cre = "dates.created" in fset
    need_d_mod = "dates.modified" in fset
    need_exif = need_d_sem or need_d_cre or need_gps or need_device

    # Bonifica 1/5/2026: Photon ha cache server-side OpenSearch, niente cache
    # locale necessaria. _open_geo_cache + _reverse_geocode legacy lasciati nel
    # file come dead-code per ora (rimozione futura).
    geo_conn = None  # noqa

    enriched, failed = [], []
    p_resolved = p_unknown = p_failed = 0

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            failed.append({"index": i, "error": "entry must be a dict"})
            continue
        src = entry.get("path") or entry.get("src")
        if not isinstance(src, str) or not src:
            failed.append({"index": i, "error": "entry missing 'path' (or 'src') string"})
            continue
        path = Path(os.path.expanduser(src)).resolve()
        if not path.exists():
            failed.append({"index": i, "path": str(path), "error": "path does not exist"})
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
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
