"""calendar_resolver — risoluzione uniforme del CALENDARIO target per gli
executor di eventi (gemello di backend_resolver ADR 0165 / self_recipient_resolver).

Principio (§7.9, model-independent): QUALE calendario è CONFIGURAZIONE risolta
dal runtime in modo deterministico, non un arg che l'LLM deve indovinare. Il
planner non sceglie il calendar_id: lo risolve qui il runtime dal linguaggio.

Politica (decisa con l'utente 3/6):
  - DEFAULT = `primary` (il calendario del proprietario, es. «the owner»).
  - TARGETING NL: se la frase NOMINA un calendario di cui l'utente è OWNER
    (es. «fissa nel calendario lavoro»), risolve a quel calendar_id. Solo OWNED:
    mai scrivere su calendari condivisi/iscritti (festività, compleanni).
  - read_events «tutti i calendari» esplicito → `all` (override del default primary).
  - calendar_id ESPLICITO non-alias nell'arg → rispettato (l'utente sa cosa fa).

Costo (§7.4): `list_calendars` (1 chiamata Google) parte SOLO se la frase
contiene la parola «calendar*»; altrimenti default primary, zero overhead.
Cache TTL per non ripetere la list nello stesso giro/sessione.
"""
from __future__ import annotations

import time
from typing import Optional

import detection_lexicon as _detlex
import detection_lexicon_seed_resolvers as _resolver_seed

# Tool di evento su cui il calendario è un asse di configurazione.
_EVENT_TOOLS = frozenset({"create_events", "read_events", "delete_events"})

# Alias che significano "primary" (coerente con google_workspace._CAL_ID_ALIASES):
# se l'arg vale uno di questi NON è un target esplicito → si applica la policy.
_PRIMARY_CANONICAL = frozenset({"", "primary"})
_ALL_CANONICAL = frozenset({"all"})

_CACHE: dict = {"ts": 0.0, "owned": None}
_TTL_S = 120.0


def _owned_calendars() -> list[dict]:
    """Calendari di PROPRIETÀ (accessRole=='owner'), con cache TTL.

    Ritorna lista di {id, summary, primary}. Su errore (no OAuth) → [].
    """
    now = time.time()
    if _CACHE["owned"] is not None and (now - _CACHE["ts"]) < _TTL_S:
        return _CACHE["owned"]
    owned: list[dict] = []
    try:
        from backends.events import google_workspace
        lst = google_workspace.list_calendars({})
        if lst.get("ok"):
            for e in (lst.get("entries") or []):
                if e.get("id") and (e.get("access_role") or "").lower() == "owner":
                    owned.append({"id": e["id"],
                                   "summary": (e.get("summary") or ""),
                                   "primary": bool(e.get("primary"))})
    except Exception:
        owned = []
    _CACHE["owned"] = owned
    _CACHE["ts"] = now
    return owned


def _match_owned_in_query(query_lc: str) -> Optional[str]:
    """Cerca, fra i calendari OWNED, quello il cui NOME compare nella frase.

    Ritorna il calendar_id del primo match NON-primario (target esplicito);
    None se nessun nome owned compare. Il primario non va trattato come target
    (è già il default). Match substring case-insensitive sul nome (≥3 char).
    """
    for c in _owned_calendars():
        name = (c.get("summary") or "").strip().lower()
        if len(name) < 3:
            continue
        if name in query_lc and not c.get("primary"):
            return c["id"]  # target owned esplicito
    return None


def _match_owned_name(name_lc: str) -> Optional[str]:
    """ID del calendario OWNED il cui nome combacia ESATTAMENTE (case-insensitive)
    con `name_lc`, o None. Usato per un calendar_id bare-name emesso dall'LLM."""
    for c in _owned_calendars():
        if (c.get("summary") or "").strip().lower() == name_lc and not c.get("primary"):
            return c["id"]
    return None


def resolve_calendar(tool: str, args: dict, query: str) -> dict:
    """Inietta `calendar_id` secondo la policy. Idempotente, no-op fuori dagli
    event tool / client non-google. Non muta l'input (ritorna copia se cambia)."""
    if tool not in _EVENT_TOOLS or not isinstance(args, dict):
        return args
    # Solo Google ha il concetto di calendari multipli; local ICS = 1 calendario.
    client = args.get("client")
    if client and client != "google_workspace":
        return args

    cid = args.get("calendar_id")
    cl = str(cid).strip().lower() if cid is not None else ""
    # ID Google REALE (email / @group) → l'utente/planner sa cosa fa: rispetta.
    if "@" in cl:
        return args
    # calendar_id bare-name (es. LLM emette "lavoro" come id): candidato target
    # da risolvere fra gli owned, NON da passare grezzo al backend.
    _resolver_seed.ensure_registered()
    lexicon = _detlex.mapping("resolver.calendar")
    primary_aliases = _PRIMARY_CANONICAL | {
        str(form).casefold() for form in lexicon.get("primary_alias", ())
    }
    all_aliases = _ALL_CANONICAL | {
        str(form).casefold() for form in lexicon.get("all_alias", ())
    }
    explicit_name = cl if (cl and cl not in primary_aliases
                           and cl not in all_aliases) else None

    ql = (query or "").lower()
    # "tutti i calendari" (alias arg o frase) → aggregato, solo lettura.
    all_request = _detlex.match_any(
        lexicon.get("all_request", ()), ql, mode="word",
    )
    if tool == "read_events" and (cl in all_aliases or all_request):
        out = dict(args)
        out["calendar_id"] = "all"
        return out

    # Target owned: priorità al bare-name esplicito, poi al nome nella frase.
    # `list_calendars` (1 call) parte solo se c'è un candidato (§7.4).
    matched = None
    if explicit_name:
        matched = _match_owned_name(explicit_name)
    if not matched and _detlex.match_any(
            lexicon.get("calendar_word", ()), ql, mode="word"):
        matched = _match_owned_in_query(ql)

    out = dict(args)
    if matched:
        out["calendar_id"] = matched
    elif tool == "read_events":
        out["calendar_id"] = "primary"  # default: primary, non 'all'
    else:
        # create/delete: nessun target valido → rimuovi un eventuale bare-name
        # non risolto così il backend usa il default primary.
        out.pop("calendar_id", None)
    return out
