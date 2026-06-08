# SPDX-License-Identifier: AGPL-3.0-only
"""backend_resolver.py — risoluzione UNIFORME del backend/provider.

Problema generale (lessons_learned.md §B): per gli OBJECT con backend multipli
la SELEZIONE del provider è CONFIGURAZIONE, non intento. Chiederla all'LLM (arg
`client`/`provider`) genera bias verso il pattern (enum) → scelta sbagliata.

Una sola modalità per tutti i casi simili (richiesta Roberto 31/5):
  - DEFINE: registry `OBJECT_BACKENDS` (provider + disponibilità + alias NL).
  - IDENTIFY: `resolve_backend_arg` — provider nominato esplicito nella query
    (match alias deterministico) → quello; altrimenti default = primo provider
    DISPONIBILE per ordine di preferenza.
  - INJECT: il caller (dispatch engine) inietta il valore nell'arg. L'LLM NON
    vede né sceglie il provider ("facile per l'LLM": emette solo intento).

§7.9 deterministico (zero LLM). §7.3 generale. Rispetta ADR 0155: il backend non
è scelta del planner → il runtime ne è il proprietario, non un override.
"""
from __future__ import annotations


def _gw_creds() -> bool:
    """OAuth Google Workspace presente? (events/contacts/files google)."""
    try:
        from backends.events import google_workspace as _e
        return bool(_e._has_creds())
    except Exception:
        return False


# ── DEFINE: registry per-object ───────────────────────────────────────────────
# arg        : nome dell'arg di backend nel manifest
# providers  : ordine di PREFERENZA (il primo disponibile vince come default)
# available  : provider -> bool (creds/config). True = sempre disponibile.
# aliases    : provider -> tuple di token NL (IT+EN); match esplicito sulla query.
#              Usare frasi specifiche per evitare falsi positivi.
OBJECT_BACKENDS: dict[str, dict] = {
    "events": {
        "arg": "client",
        "providers": ["google_workspace", "local"],
        "available": lambda p: _gw_creds() if p == "google_workspace" else True,
        "aliases": {
            "local": ("calendario locale", "calendar locale", "local calendar",
                      "in locale", "sul locale", "in local", "localmente"),
            "google_workspace": ("google calendar", "calendario google",
                                  "su google", "on google", "gmail calendar"),
        },
    },
    "files": {
        "arg": "client",
        "providers": ["local"],
        "available": lambda p: True,
        "aliases": {},
    },
    "contacts": {
        "arg": "client",
        "providers": ["google_workspace"],
        "available": lambda p: _gw_creds(),
        "aliases": {},
    },
}


def object_of(tool_name: str) -> str | None:
    """Object canonico dal nome `verbo_oggetto[_qual]`, ristretto al registry.
    Es: create_events→events, move_files→files, read_contacts→contacts."""
    if not tool_name:
        return None
    for seg in tool_name.lower().split("_"):
        if seg in OBJECT_BACKENDS:
            return seg
    return None


def resolve(object_name: str, query: str = "") -> str | None:
    """Provider deterministico per (object, query). Esplicito>default. None se
    object non gestito o nessun provider disponibile."""
    spec = OBJECT_BACKENDS.get(object_name)
    if not spec:
        return None
    q = (query or "").lower()
    # IDENTIFY esplicito: provider nominato nella query (match alias)
    for prov, toks in spec.get("aliases", {}).items():
        if any(t in q for t in toks):
            return prov
    # default: primo provider DISPONIBILE per ordine di preferenza
    avail = spec.get("available", lambda p: True)
    for prov in spec.get("providers", []):
        try:
            if avail(prov):
                return prov
        except Exception:
            continue
    # fallback: primo dichiarato (l'executor darà errore onesto se non usabile)
    provs = spec.get("providers") or []
    return provs[0] if provs else None


def resolve_backend_arg(tool_name: str, args: dict, query: str = "") -> dict:
    """INJECT: se il tool appartiene a un object multi-backend, risolve il
    provider e lo scrive nell'arg di backend (OVERRIDE: il runtime ne è il
    proprietario, l'LLM non lo sceglie). No-op per tool non gestiti."""
    if not isinstance(args, dict):
        return args
    obj = object_of(tool_name)
    if obj is None:
        return args
    spec = OBJECT_BACKENDS[obj]
    chosen = resolve(obj, query)
    if chosen is None:
        return args
    out = dict(args)
    out[spec["arg"]] = chosen
    return out
