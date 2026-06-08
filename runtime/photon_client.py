#!/usr/bin/env python3
"""photon_client — adapter REST per il geocoder Photon (suprastructure/geo).

Photon e' self-hosted su .33:2322. Vantaggio rispetto a Nominatim:
distance_sort=true nativo + niente rate limit.

API simmetrica a `nominatim_client` per swap drop-in:
- forward_search(query, max_results, near?, radius_km?, bounded?)
- reverse_geocode(lat, lon)

Backend selection (1/5/2026): env METNOS_GEO_BACKEND=photon|nominatim
(default photon se Photon raggiungibile, altrimenti fallback nominatim).
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request

PHOTON_BASE = os.environ.get("METNOS_PHOTON_URL", "http://192.0.2.10:2322")
PHOTON_TIMEOUT = 7.0

# Auto-mapping query token → osm_tag per categorie standard.
# Photon `osm_tag=amenity:pharmacy` filtra esattamente; combinato col bias
# geografico + post-sort haversine = vera kNN per categoria.
# IT+EN. Long-tail (es. "negozio musica vintage") resta senza filtro =
# fallback ranking importance.
_AUTO_OSM_TAG = {
    # IT
    "farmacia": "amenity:pharmacy", "farmacie": "amenity:pharmacy",
    "ristorante": "amenity:restaurant", "ristoranti": "amenity:restaurant",
    "bar": "amenity:bar", "pub": "amenity:pub",
    "pizzeria": "amenity:fast_food", "pizzerie": "amenity:fast_food",
    "gelateria": "amenity:ice_cream", "gelaterie": "amenity:ice_cream",
    "banca": "amenity:bank", "banche": "amenity:bank",
    "atm": "amenity:atm", "bancomat": "amenity:atm",
    "ospedale": "amenity:hospital", "ospedali": "amenity:hospital",
    "distributore": "amenity:fuel", "benzinaio": "amenity:fuel",
    "parcheggio": "amenity:parking", "parcheggi": "amenity:parking",
    "supermercato": "shop:supermarket", "supermercati": "shop:supermarket",
    "panetteria": "shop:bakery", "panetterie": "shop:bakery",
    "posta": "amenity:post_office", "poste": "amenity:post_office",
    "fermata": "highway:bus_stop",
    "stazione": "railway:station",
    "hotel": "tourism:hotel", "albergo": "tourism:hotel", "alberghi": "tourism:hotel",
    "museo": "tourism:museum", "musei": "tourism:museum",
    "cinema": "amenity:cinema",
    "teatro": "amenity:theatre", "teatri": "amenity:theatre",
    "palestra": "leisure:fitness_centre", "palestre": "leisure:fitness_centre",
    "parrucchiere": "shop:hairdresser",
    "ottico": "shop:optician",
    "libreria": "shop:books", "librerie": "shop:books",
    "scuola": "amenity:school", "scuole": "amenity:school",
    "biblioteca": "amenity:library", "biblioteche": "amenity:library",
    "parco": "leisure:park", "parchi": "leisure:park",
    "chiesa": "amenity:place_of_worship", "chiese": "amenity:place_of_worship",
    "comune": "amenity:townhall", "municipio": "amenity:townhall",
    # EN
    "pharmacy": "amenity:pharmacy", "pharmacies": "amenity:pharmacy", "drugstore": "amenity:pharmacy",
    "restaurant": "amenity:restaurant", "restaurants": "amenity:restaurant",
    "bank": "amenity:bank", "banks": "amenity:bank",
    "hospital": "amenity:hospital", "hospitals": "amenity:hospital",
    "gas": "amenity:fuel", "fuel": "amenity:fuel", "petrol": "amenity:fuel",
    "parking": "amenity:parking",
    "supermarket": "shop:supermarket", "supermarkets": "shop:supermarket",
    "bakery": "shop:bakery",
    "post": "amenity:post_office",
    "museum": "tourism:museum", "museums": "tourism:museum",
    "library": "amenity:library",
    "school": "amenity:school",
    "park": "leisure:park", "parks": "leisure:park",
    "church": "amenity:place_of_worship",
    "town": "amenity:townhall",
}


def _autotag_for_query(query: str) -> str | None:
    """Cerca categoria standard nei token della query → osm_tag.
    Match case-insensitive sul primo token noto. Long-tail → None."""
    if not query:
        return None
    tokens = query.lower().replace(",", " ").replace(".", " ").split()
    for t in tokens:
        tag = _AUTO_OSM_TAG.get(t)
        if tag:
            return tag
    return None


def _haversine_km(lat1, lon1, lat2, lon2):
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    return round(2 * 6371.0 * math.asin(math.sqrt(a)), 3)


def _slugify(s: str) -> str:
    if not s:
        return ""
    out = []
    for ch in s.lower():
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    return "".join(out).strip("_-")[:80]


def is_available(timeout=2.0) -> bool:
    """Probe rapido health Photon. Usato dal selector backend."""
    try:
        req = urllib.request.Request(f"{PHOTON_BASE}/status")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _photon_params(query, max_results, lat=None, lon=None, radius_km=None,
                    lang="it", osm_tag=None, with_bbox=False):
    p = {"q": query.strip(), "limit": str(min(max(max_results, 1), 50)), "lang": lang}
    if osm_tag:
        p["osm_tag"] = osm_tag
    if lat is not None and lon is not None:
        p["lat"] = str(lat); p["lon"] = str(lon); p["location_bias_scale"] = "1.0"
        if with_bbox and radius_km:
            r = float(radius_km)
            dlat = r / 111.0
            dlon = r / (111.0 * max(0.01, math.cos(math.radians(lat))))
            p["bbox"] = f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"
    return p


def _photon_call(params, center_lat=None, center_lon=None):
    """Esegue una HTTP call a Photon, parse features → matches list. Calcola
    distance_km haversine se center disponibile."""
    qs = urllib.parse.urlencode(params)
    url = f"{PHOTON_BASE}/api/?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=PHOTON_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return [], f"error_http_{e.code}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return [], f"error_{type(e).__name__}"
    matches = []
    for feat in data.get("features", []) or []:
        try:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if len(coords) < 2: continue
            lon = float(coords[0]); lat = float(coords[1])
        except (TypeError, ValueError):
            continue
        props = feat.get("properties") or {}
        name = props.get("name") or props.get("street") or props.get("city")
        parts = []
        for k in ("name", "street", "housenumber", "postcode", "city",
                  "county", "state", "country"):
            v = props.get(k)
            if v: parts.append(str(v))
        address = ", ".join(parts) or props.get("display_name", "")
        entry = {
            "name": name or (address.split(",")[0].strip() if address else None),
            "lat": round(lat, 6), "lon": round(lon, 6),
            "address": address,
            "place_slug": _slugify(name or address) or "unknown",
            "osm_type": props.get("osm_type"), "osm_id": props.get("osm_id"),
        }
        if center_lat is not None and center_lon is not None:
            entry["distance_km"] = _haversine_km(center_lat, center_lon, lat, lon)
        matches.append(entry)
    return matches, "photon"


def forward_search(query: str, max_results: int = 5, near: dict | None = None,
                    radius_km: float | None = None, bounded: bool = False,
                    lang: str = "it"):
    """Forward search via Photon. Ritorna (matches, source) come nominatim_client.

    Differenze chiave vs Nominatim:
    - distance_sort=true → kNN reale (top-K = veri piu' vicini)
    - bbox per bounded (Photon non ha equivalente esatto del Nominatim bounded
      flag; useremo bbox calcolato da near+radius)
    - lang nativo (multi-lingua dei nomi POI/strade)
    """
    if not isinstance(query, str) or not query.strip():
        return [], "error_invalid_query"
    auto_tag = _autotag_for_query(query) if near else None

    # === STRATEGIA ADATTIVA shrink-then-grow per kNN robusto (1/5/2026) ===
    # Photon hard-cap limit=50 + ranking importance pre-fetch. Su corpus
    # grande (Italia 8M POI), una query "farmacia" con bbox larga ritorna
    # i top-50 per importance, MAI le farmacie low-importance vicine.
    # Soluzione: bbox stretto progressivamente crescente. Photon locale ~50ms,
    # 4-5 fetch ≈ 250ms tot.
    if near and isinstance(near, dict) and "lat" in near and "lon" in near:
        try:
            center_lat = float(near["lat"]); center_lon = float(near["lon"])
        except (TypeError, ValueError):
            center_lat = center_lon = None
    else:
        center_lat = center_lon = None

    if center_lat is not None and center_lon is not None and bounded:
        # Sequenza raggi (km): inizia stretto, raddoppia. Stop quando trovati
        # ≥max_results POI (sufficiente densita') OPPURE arrivati al cap utente.
        # Adattivo shrink-then-grow: parti molto stretto (0.1km = 100m) e
        # raddoppia fino a trovare ≥max_results POI o cap. Cosi' garantiamo
        # che la VERA piu' vicina venga trovata se esiste in OSM dataset.
        # Roberto 1/5/2026: "metti sempre 100 metri".
        cap_radius = float(radius_km) if radius_km else 50.0
        radii = []
        r = 0.1
        while r <= cap_radius:
            radii.append(r); r *= 2
        if not radii or radii[-1] < cap_radius:
            radii.append(cap_radius)
        best_matches = []
        last_source = "photon"
        for r in radii:
            params = _photon_params(query, max_results=50, lat=center_lat, lon=center_lon,
                                     radius_km=r, lang=lang, osm_tag=auto_tag, with_bbox=True)
            matches, src = _photon_call(params, center_lat, center_lon)
            last_source = src
            if matches:
                # accumula (potrebbe duplicare: dedup per osm_id+coords)
                seen = {(m.get("osm_type"), m.get("osm_id"), m.get("lat"), m.get("lon")) for m in best_matches}
                for m in matches:
                    k = (m.get("osm_type"), m.get("osm_id"), m.get("lat"), m.get("lon"))
                    if k not in seen:
                        best_matches.append(m); seen.add(k)
            if len(best_matches) >= max_results:
                break
        # sort haversine + tronco
        best_matches.sort(key=lambda m: m.get("distance_km", 1e9))
        return best_matches[:max_results], last_source

    # === Path semplice: niente near OR bounded=False ===
    fetch_limit = 50 if near else max(max_results, 1)
    fetch_limit = min(fetch_limit, 50)
    params: dict = {
        "q": query.strip(),
        "limit": str(fetch_limit),
        "lang": lang,
    }
    if auto_tag:
        params["osm_tag"] = auto_tag
    center_lat = center_lon = None
    if isinstance(near, dict) and "lat" in near and "lon" in near:
        try:
            center_lat = float(near["lat"])
            center_lon = float(near["lon"])
        except (TypeError, ValueError):
            center_lat = center_lon = None
    if center_lat is not None and center_lon is not None:
        # lat/lon sono solo bias geografico (importance score domina). Per
        # vera kNN: post-sort haversine sotto + bbox per restringere.
        # NB: Photon v1.1 ha rimosso `distance_sort` parametro.
        params["lat"] = str(center_lat)
        params["lon"] = str(center_lon)
        # location_bias_scale alto rinforza il bias geografico (default 0.2).
        params["location_bias_scale"] = "1.0"
        if bounded:
            r = float(radius_km) if radius_km else 10.0
            dlat = r / 111.0
            dlon = r / (111.0 * max(0.01, math.cos(math.radians(center_lat))))
            params["bbox"] = (
                f"{center_lon - dlon},{center_lat - dlat},"
                f"{center_lon + dlon},{center_lat + dlat}"
            )
    qs = urllib.parse.urlencode(params)
    url = f"{PHOTON_BASE}/api/?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=PHOTON_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            return [], "rate_limited"
        return [], f"error_http_{e.code}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return [], f"error_{type(e).__name__}"

    matches = []
    for feat in data.get("features", []) or []:
        try:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            lon = float(coords[0])
            lat = float(coords[1])
        except (TypeError, ValueError):
            continue
        props = feat.get("properties") or {}
        name = props.get("name") or props.get("street") or props.get("city")
        # Build address string Nominatim-like
        parts = []
        for k in ("name", "street", "housenumber", "postcode", "city",
                  "county", "state", "country"):
            v = props.get(k)
            if v:
                parts.append(str(v))
        address = ", ".join(parts) or props.get("display_name", "")
        entry = {
            "name": name or address.split(",")[0].strip() if address else None,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "address": address,
            "place_slug": _slugify(name or address) or "unknown",
            "osm_type": props.get("osm_type"),
            "osm_id": props.get("osm_id"),
        }
        if center_lat is not None and center_lon is not None:
            entry["distance_km"] = _haversine_km(center_lat, center_lon, lat, lon)
        matches.append(entry)

    # Sort haversine + tronco al max_results (fetch interno e' 50)
    if center_lat is not None and center_lon is not None:
        matches.sort(key=lambda m: m.get("distance_km", 1e9))
        matches = matches[:max_results]

    return matches, "photon"


def reverse_geocode(lat: float, lon: float, lang: str = "it"):
    """Reverse geocode via Photon: coords → place_slug.
    Stessa firma di nominatim_client.reverse_geocode."""
    qs = urllib.parse.urlencode({
        "lat": str(lat), "lon": str(lon), "lang": lang, "limit": "1",
    })
    url = f"{PHOTON_BASE}/reverse?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=PHOTON_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    feats = data.get("features") or []
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    primary = (props.get("name") or props.get("street") or props.get("city")
               or props.get("county") or props.get("country"))
    return _slugify(primary) if primary else None
