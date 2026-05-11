#!/usr/bin/env python3
"""find_places — executor di Metnos v1.1.

Cerca POI per query testuale. Vettoriale per costruzione: una sola call
processa una lista di queries. Ogni query restituisce fino a `max_results`
match.

Backend: Nominatim (default pubblico, override via env METNOS_NOMINATIM_URL
per self-hostato). Cache locale in ~/.local/share/metnos/geo_cache.sqlite.
Throttle ≥1.1s fra request consecutive.

Contratto:
    stdin:  JSON {queries: list[str], max_results?: int}
    stdout: JSON {ok, ok_count, fail_count, entries, failed,
                  rate_limited?: bool, place_warning?: str}
    `entries` ha forma list[{query, matches: list[{name,lat,lon,address,place_slug}]}]
"""
import json
import sys

sys.path.insert(0, "/opt/myclaw/runtime")
from messages import get as msg  # noqa: E402
# Geo provider unico via wrapper (1/5/2026 v0.6.0): chain configurabile via
# env METNOS_GEO_PROVIDERS. Niente conoscenza del backend specifico qui.
from geo_provider import forward_search as _geo_forward  # noqa: E402


def invoke(args):
    queries = args.get("queries")
    max_results = int(args.get("max_results", 5))
    if not isinstance(queries, list):
        return {"ok": False, "error": "missing or invalid required arg 'queries' (must be a list of strings)"}
    if max_results <= 0 or max_results > 50:
        return {"ok": False, "error": "max_results must be in 1..50"}

    # Normalizza `near`: accetta dict {lat, lon}, lista/tupla [lat, lon],
    # oppure il record completo di get_location {location: {lat, lon, ...}}.
    near_raw = args.get("near")
    near = None
    if isinstance(near_raw, dict):
        if "lat" in near_raw and "lon" in near_raw:
            near = {"lat": near_raw["lat"], "lon": near_raw["lon"]}
        elif isinstance(near_raw.get("location"), dict):
            loc = near_raw["location"]
            if "lat" in loc and "lon" in loc:
                near = {"lat": loc["lat"], "lon": loc["lon"]}
    elif isinstance(near_raw, (list, tuple)) and len(near_raw) == 2:
        near = {"lat": near_raw[0], "lon": near_raw[1]}
    radius_km = args.get("radius_km")
    # bounded default: TRUE quando near e' presente (1/5/2026 fix).
    # Senza bounded, Nominatim viewbox e' solo bias di ranking debole
    # → top match puo' essere a 150km (caso "Farmacia, Correzzola, Padova"
    # che vince la query globale "farmacia" anche con bias Brescia).
    # Con bounded=True restringe stretto al viewbox (~radius_km). L'utente
    # passa esplicito bounded=False se vuole search globale con bias.
    bounded_arg = args.get("bounded")
    if bounded_arg is None:
        bounded = near is not None
    else:
        bounded = bool(bounded_arg)

    entries, failed = [], []
    rate_streak = 0
    aborted = False
    backend_used = "unknown"
    try:
        for i, q in enumerate(queries):
            if not isinstance(q, str) or not q.strip():
                failed.append({"index": i, "query": q, "error": "query must be a non-empty string"})
                continue
            matches, source = _geo_forward(
                q.strip(), max_results=max_results, near=near,
                radius_km=radius_km, bounded=bounded, lang="it",
            )
            backend_used = source
            if source == "rate_limited":
                rate_streak += 1
                failed.append({"index": i, "query": q, "error_code": "ERR_EXT_SVC_LIMIT", "error": msg("ERR_EXT_SVC_LIMIT")})
                if rate_streak >= 3:
                    aborted = True
                    break
                continue
            if source == "error":
                failed.append({"index": i, "query": q, "error_code": "WARN_EXT_SVC_DEGRADED", "error": msg("WARN_EXT_SVC_DEGRADED")})
                continue
            rate_streak = 0
            entries.append({"query": q, "matches": matches})
    finally:
        pass  # Photon: no cache locale da chiudere

    response = {
        "ok": len(failed) == 0,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
        "backend": backend_used,
    }
    if aborted:
        response["aborted"] = True
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
