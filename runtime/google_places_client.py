#!/usr/bin/env python3
"""google_places_client — adapter Google Places API v1 (Nearby Search).

Usato come backend primario di find_places per kNN POI (1/5/2026):
copertura urbana >> OSM, distance ranking nativo, free tier $200/mese
≈ 6000 calls. Photon resta come fallback OSS.

API key in `~/.config/metnos/google_maps.env` (env GOOGLE_MAPS_API_KEY).
NON committare la key, NON loggarla.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request

API_BASE = "https://places.googleapis.com/v1/places:searchNearby"
TIMEOUT = 8.0
import config as _C  # §7.11
ENV_FILE = _C.PATH_USER_CONFIG / "google_maps.env"


def _load_api_key() -> str | None:
    """Risolve `GOOGLE_MAPS_API_KEY` in 3 layer (ADR 0131 extended,
    14/5/2026):
      1. env var (override volatile),
      2. credentials store cifrato (domain `google_maps_api_key`),
      3. file `~/.config/metnos/google_maps.env` (legacy fallback).
    """
    k = os.environ.get("GOOGLE_MAPS_API_KEY")
    if k:
        return k
    try:
        import credentials as _cr  # type: ignore[import-not-found]
        payload = _cr.load("google_maps_api_key")
        if isinstance(payload, dict) and isinstance(payload.get("value"), str):
            v = payload["value"].strip()
            if v:
                return v
    except ImportError:
        pass
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("GOOGLE_MAPS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# Pure protocol projection: canonical OSM identity -> Google Places type.
# Natural-language surfaces live only in detection concept ``geo.osm_tag``.
# Lista Google canonica:
# https://developers.google.com/maps/documentation/places/web-service/place-types
_OSM_TO_GOOGLE_TYPE = {
    "amenity:pharmacy": "pharmacy",
    "amenity:restaurant": "restaurant",
    "amenity:bar": "bar",
    "amenity:pub": "bar",
    "amenity:fast_food": "pizza_restaurant",
    "amenity:ice_cream": "ice_cream_shop",
    "amenity:bank": "bank",
    "amenity:atm": "atm",
    "amenity:hospital": "hospital",
    "amenity:fuel": "gas_station",
    "amenity:parking": "parking",
    "shop:supermarket": "supermarket",
    "shop:bakery": "bakery",
    "amenity:post_office": "post_office",
    "highway:bus_stop": "bus_stop",
    "railway:station": "train_station",
    "tourism:hotel": "lodging",
    "tourism:museum": "museum",
    "amenity:cinema": "movie_theater",
    "amenity:theatre": "performing_arts_theater",
    "leisure:fitness_centre": "gym",
    "shop:hairdresser": "hair_care",
    "shop:optician": "store",
    "shop:books": "book_store",
    "amenity:school": "school",
    "amenity:library": "library",
    "leisure:park": "park",
    "amenity:place_of_worship": "church",
    "amenity:townhall": "city_hall",
}


def _google_surface_types() -> dict[str, str]:
    """Ready localized surface -> Google type, with no language fallback.

    Reviewed baselines are additive only after the active native resource has
    passed readiness. Thus IT/EN compatibility remains available for a ready
    installation, while a missing, pending or stale active language yields no
    positive type signal.
    """
    import detection_lexicon as _detlex

    localized = _detlex.native_ready_mapping(
        "geo.osm_tag", include_reviewed_baselines=True,
    )
    out: dict[str, str] = {}
    for osm_tag, forms in localized.items():
        google_type = _OSM_TO_GOOGLE_TYPE.get(str(osm_tag))
        if not google_type or not isinstance(forms, list):
            continue
        for form in forms:
            surface = str(form).strip().casefold()
            if not surface:
                continue
            previous = out.get(surface)
            if previous is not None and previous != google_type:
                return {}
            out[surface] = google_type
    return out


def _autotype_for_query(query: str) -> str | None:
    if not query:
        return None
    tokens = query.casefold().replace(",", " ").replace(".", " ").split()
    surface_types = _google_surface_types()
    for t in tokens:
        v = surface_types.get(t)
        if v:
            return v
    return None


def _haversine_km(lat1, lon1, lat2, lon2):
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    return round(2 * 6371.0 * math.asin(math.sqrt(a)), 3)


def is_available() -> bool:
    return _load_api_key() is not None


def forward_search(query: str, max_results: int = 5, near: dict | None = None,
                    radius_km: float | None = None, bounded: bool = False,
                    lang: str = "it"):
    """Forward search via Google Places Nearby. Ritorna (matches, source).

    Richiede `near` (Google Places non fa search senza posizione/radius).
    Se near assente o categoria non riconosciuta → torna ([], 'no_near'/'no_type')
    e il chiamante può fallback a Photon.
    """
    if not isinstance(query, str) or not query.strip():
        return [], "error_invalid_query"
    if not near or not isinstance(near, dict):
        return [], "no_near"
    try:
        lat = float(near["lat"])
        lon = float(near["lon"])
    except (TypeError, ValueError, KeyError):
        return [], "no_near"
    place_type = _autotype_for_query(query)
    if not place_type:
        return [], "no_type"
    api_key = _load_api_key()
    if not api_key:
        return [], "no_api_key"

    radius_m = float(radius_km) * 1000.0 if radius_km else 1500.0
    radius_m = min(max(radius_m, 1.0), 50000.0)  # Google max 50km

    body = {
        "includedTypes": [place_type],
        "maxResultCount": min(max(max_results, 1), 20),  # Google v1 cap 20
        "rankPreference": "DISTANCE",
        "languageCode": lang,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": radius_m,
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.location,places.formattedAddress,places.types,places.id,places.googleMapsUri",
    }
    req = urllib.request.Request(
        API_BASE, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
            err_msg = err_body.get("error", {}).get("message", "")[:120]
        except Exception:
            err_msg = ""
        return [], f"error_http_{e.code}:{err_msg}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return [], f"error_{type(e).__name__}"

    matches = []
    for p in data.get("places", []):
        loc = p.get("location") or {}
        try:
            plat = float(loc["latitude"])
            plon = float(loc["longitude"])
        except (TypeError, ValueError, KeyError):
            continue
        name = (p.get("displayName") or {}).get("text") or "?"
        addr = p.get("formattedAddress", "")
        entry = {
            "name": name,
            "lat": round(plat, 6),
            "lon": round(plon, 6),
            "address": addr,
            "place_slug": (p.get("id") or "").replace("places/", "")[:80],
            "google_maps_uri": p.get("googleMapsUri"),
            "types": p.get("types", []),
            "distance_km": _haversine_km(lat, lon, plat, plon),
        }
        matches.append(entry)
    # Google rankPreference=DISTANCE già ordina; ri-sort per sicurezza
    matches.sort(key=lambda m: m.get("distance_km", 1e9))
    return matches[:max_results], "google"


def reverse_geocode(lat: float, lon: float, lang: str = "it") -> str | None:
    """Reverse geocoding via Google Places searchText (cerca POI vicino punto)."""
    api_key = _load_api_key()
    if not api_key:
        return None
    url = (
        "https://maps.googleapis.com/maps/api/geocode/json"
        f"?latlng={lat},{lon}&language={lang}&key={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            d = json.loads(resp.read())
    except Exception:
        return None
    results = d.get("results") or []
    if not results:
        return None
    # Primo result: prendiamo address_components → trova locality
    for r in results:
        for comp in r.get("address_components", []):
            if "locality" in comp.get("types", []):
                return comp.get("short_name") or comp.get("long_name")
    # Fallback: formatted_address ultimo segmento
    return results[0].get("formatted_address", "").split(",")[-1].strip() or None
