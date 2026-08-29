#!/usr/bin/env python3
"""geo_provider — wrapper sottile geocoding/POI search, provider-agnostic.

I consumer chiamano SOLO `geo_provider.forward_search` / `reverse_geocode`.
Niente import diretti a Google/Photon/altro nei consumer.

Chain via env METNOS_GEO_PROVIDERS (CSV ordine = priorita').
Default: "google,photon". Cambio provider = cambio env, no codice.

Per aggiungere un provider occorre implementare il modulo
`runtime/<nome>_client.py`, aggiungere la voce immutabile in `PROVIDERS` e il
corrispondente ramo di import letterale in `_load`. Il doppio aggiornamento e'
intenzionale: la build chiusa non ammette nomi di modulo scelti a runtime.
"""
from __future__ import annotations

import os
from types import MappingProxyType

PROVIDERS = MappingProxyType({
    "google":  "google_places_client",
    "photon":  "photon_client",
})
DEFAULT_CHAIN = "google,photon"


def _canonical_failure(status: str) -> str | None:
    value = str(status or "").strip().lower()
    if value == "rate_limited" or value.startswith("error_http_429"):
        return "rate_limited"
    if value.startswith("error_"):
        return "error"
    return None


def _chain():
    return [p.strip().lower() for p in
            os.environ.get("METNOS_GEO_PROVIDERS", DEFAULT_CHAIN).split(",")
            if p.strip()]


def _load(name):
    module_name = PROVIDERS.get(name)
    try:
        if module_name == "google_places_client":
            import google_places_client
            return google_places_client
        if module_name == "photon_client":
            import photon_client
            return photon_client
        return None
    except ImportError:
        return None


def forward_search(query, max_results=5, near=None, radius_km=None,
                    bounded=False, lang="it"):
    """Itera la chain. Primo provider con ≥1 match wins."""
    last = "no_provider_match"
    failure = None
    unavailable = None
    completed_empty = None
    for name in _chain():
        m = _load(name)
        if not m:
            continue
        try:
            matches, provider_status = m.forward_search(
                query, max_results=max_results, near=near,
                radius_km=radius_km, bounded=bounded, lang=lang)
        except Exception:
            failure = failure or f"{name}_provider_error"
            continue
        if matches:
            return matches, name
        status = str(provider_status or name)
        last = status
        canonical = _canonical_failure(status)
        if canonical:
            failure = failure or canonical
        elif status == "no_api_key":
            unavailable = unavailable or "error"
        elif status in {name, "no_match", "no_results"}:
            completed_empty = completed_empty or name
    return [], failure or completed_empty or unavailable or last


def reverse_geocode(lat, lon, lang="it"):
    """Itera la chain. Primo provider con risultato non-None wins."""
    for name in _chain():
        m = _load(name)
        if not m or not hasattr(m, "reverse_geocode"):
            continue
        try:
            r = m.reverse_geocode(lat, lon, lang=lang)
        except Exception:
            continue
        if r:
            return r
    return None
