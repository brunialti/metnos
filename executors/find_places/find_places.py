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
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from executor_helpers import coerce_cap  # noqa: E402
_msg = msg  # alias: alcuni rami di validazione usano _msg (unifica i nomi)
# Geo provider unico via wrapper (1/5/2026 v0.6.0): chain configurabile via
# env METNOS_GEO_PROVIDERS. Niente conoscenza del backend specifico qui.
from geo_provider import forward_search as _geo_forward  # noqa: E402


def invoke(args):
    queries = args.get("queries")
    max_results = coerce_cap(args, "max_results", 5, maximum=50)
    # §2.4 robustezza NL→determinismo: l'LLM passa spesso un singolo string per
    # un arg-lista (queries="ospedali" invece di ["ospedali"]). Coalesce a lista
    # (caso degenere N=1, §2.1). Senza questo "trova gli ospedali" falliva con
    # "Pipeline malformata" (q22 4/6).
    if isinstance(queries, str):
        queries = [queries]
    if not isinstance(queries, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="queries", of="strings")}

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
    # `near` come STRINGA (nome città/zona, non coordinate): l'LLM lo passa per
    # query compound "<POI> a <city>" (es. "ospedali" + near="Padova"). Non
    # scartarlo in silenzio (§2.8: si perderebbe il vincolo geografico) né
    # geocodificarlo qui: foldalo nel testo di OGNI query → Nominatim risolve
    # "ospedali Padova" nativamente (§2.4, deterministico). Se near è già
    # coords (sopra), salta.
    if near is None and isinstance(near_raw, str) and near_raw.strip():
        _near_s = near_raw.strip()
        queries = [
            (f"{q} {_near_s}" if isinstance(q, str)
             and _near_s.lower() not in q.lower() else q)
            for q in queries
        ]
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
                failed.append({"index": i, "query": q, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="query")})
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
            # §2.1/§2.6/§2.10 output FLAT pipeable: ogni MATCH è una entry (con
            # attribuzione `query`), NON un wrapper {query, matches:[...]} — il
            # from_step/scratchpad espande il wrapper a 1-entry-per-query e i POI
            # nidificati si perdono (consumer a valle scriveva lista vuota, bug
            # q22 4/6). Una query con 0 match contribuisce 0 entries (find onesto).
            for _m in (matches or []):
                if isinstance(_m, dict):
                    entries.append({**_m, "query": q})
                else:
                    entries.append({"match": _m, "query": q})
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
    run_stdio(invoke)


if __name__ == "__main__":
    main()
