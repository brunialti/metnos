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
        # local = DEFAULT (self-hosted §10.3); google_workspace opt-in solo se
        # nominato esplicitamente (alias) — vale per find/read/write/... files.
        "providers": ["local", "google_workspace"],
        "available": lambda p: _gw_creds() if p == "google_workspace" else True,
        "aliases": {
            "google_workspace": ("google drive", "gdrive", "su drive",
                                  "in drive", "drive google", "google docs",
                                  "google doc", "google sheet", "google sheets",
                                  "google fogli", "google workspace"),
        },
    },
    "contacts": {
        "arg": "client",
        "providers": ["google_workspace"],
        "available": lambda p: _gw_creds(),
        "aliases": {},
    },
    "dirs": {
        "arg": "client",
        # Come `files` (7/7/2026, dirs mono→multi): local = DEFAULT (§10.3),
        # google_workspace opt-in solo se nominato — i 3 dispatcher
        # find/create/delete_dirs hanno il lazy-gw da C7 Area-2 CP4; qui si
        # registra l'OWNER runtime che mancava (prima gw era raggiungibile
        # solo via _GW_CLIENT_TOOLS su find_dirs). Alias = subset folder-
        # pertinente di files (niente docs/sheet).
        "providers": ["local", "google_workspace"],
        "available": lambda p: _gw_creds() if p == "google_workspace" else True,
        "aliases": {
            "google_workspace": ("google drive", "gdrive", "su drive",
                                  "in drive", "drive google",
                                  "google workspace"),
        },
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


def _explicit_provider(spec: dict, query: str) -> str | None:
    """Provider NOMINATO esplicitamente nella query (match alias), o None."""
    q = (query or "").lower()
    for prov, toks in spec.get("aliases", {}).items():
        if any(t in q for t in toks):
            return prov
    return None


def resolve(object_name: str, query: str = "") -> str | None:
    """Provider deterministico per (object, query). Esplicito>default. None se
    object non gestito o nessun provider disponibile."""
    spec = OBJECT_BACKENDS.get(object_name)
    if not spec:
        return None
    # IDENTIFY esplicito: provider nominato nella query (match alias)
    explicit = _explicit_provider(spec, query)
    if explicit:
        return explicit
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


def resolve_backend_arg(tool_name: str, args: dict, query: str = "",
                        args_schema: dict | None = None) -> dict:
    """INJECT: se il tool appartiene a un object multi-backend, risolve il
    provider e lo scrive nell'arg di backend (OVERRIDE: il runtime ne è il
    proprietario, l'LLM non lo sceglie). No-op per tool non gestiti.

    `args_schema` (lo schema del TOOL, dal chiamante): il DEFAULT per-object
    non scavalca la capacità del singolo tool — vedi clamp enum sotto."""
    if not isinstance(args, dict):
        return args
    obj = object_of(tool_name)
    if obj is None:
        return args
    spec = OBJECT_BACKENDS[obj]
    arg = spec["arg"]
    # RISPETTA un client GIA' esplicito e DISPONIBILE: se un guard clause-scoped
    # (o un executor ripreso) l'ha gia' risolto a un provider valido, il runtime
    # NON lo scavalca con la risoluzione whole-query. Chiude la contaminazione
    # provider: «cerca su google drive X e crea un foglio» → il create NON eredita
    # gw dal marker della clausola-PRODUTTRICE (il default sink resta local §10.3).
    cur = args.get(arg)
    if isinstance(cur, str) and cur in (spec.get("providers") or []):
        avail = spec.get("available", lambda p: True)
        try:
            if avail(cur):
                return args
        except Exception:  # noqa: BLE001
            pass
    # Tool che NON DICHIARA l'arg di backend nel suo schema tipizzato →
    # niente injection (§7.3, 7/7/2026): scrivergli `client` è junk che
    # l'executor ignora — caso reale list_dirs (object dirs, ma il tool è
    # pure-local senza dispatcher client). Vale per default ED esplicito.
    if isinstance(args_schema, dict):
        _props = args_schema.get("properties")
        if isinstance(_props, dict) and _props and arg not in _props:
            return args
    explicit = _explicit_provider(spec, query)
    chosen = explicit or resolve(obj, query)
    if chosen is None:
        return args
    # Clamp enum (§2.8/§7.3, misurato 6/7/2026): il DEFAULT per-object non deve
    # scavalcare la capacità del TOOL. share_files è gw-only ma l'object files
    # default local → l'injection «local» rompeva OGNI share senza marker drive
    # (ERR_NOT_APPLICABLE). Default fuori dall'enum dichiarato → NIENTE
    # injection: il default dell'executor è il proprietario onesto. Un provider
    # ESPLICITO nella query NON è clampato: se il tool non lo supporta,
    # l'executor risponde «client non applicabile» — più onesto di un fallback
    # silenzioso sul default («file non trovato» su un delete chiesto su Drive).
    if explicit is None and isinstance(args_schema, dict):
        decl = (args_schema.get("properties") or {}).get(arg)
        allowed = decl.get("enum") if isinstance(decl, dict) else None
        if isinstance(allowed, list) and allowed and chosen not in allowed:
            return args
    out = dict(args)
    out[arg] = chosen
    return out
