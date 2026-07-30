"""photo_fields_resolver — §7.9 deterministico, i18n-compliant.

Il planner locale emette spesso NOMI NL per i metadata foto ('date', 'camera',
'location') invece dei valori enum canonici di `get_files` ('dates.semantic',
'device', 'place'). Questo resolver mappa i sinonimi -> canonico via
`detection_lexicon` (lessico traducibile: seed IT+EN, altre lingue dal daemon),
NON via lista hardcoded nell'executor (non i18n-compliant, §7.13). La chiave
speciale "all" espande a tutti i campi. Un termine NON risolvibile resta
invariato: `get_files` lo rifiuta onestamente (§2.8, backstop) — niente
scarto silenzioso (il vecchio comportamento perdeva 'date'/'camera'/'location'
tenendo solo 'gps').

Gemello di `calendar_resolver`/`junk_mail_resolver`: firma uniforme
`resolve_X(tool, args, query) -> dict`, best-effort (fallimento = no-op).
Query-deterministico e idempotente (canonico -> canonico = no-op) → sicuro
nella catena `resolve_query_canonical_args` (esecuzione E record L0).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_CONCEPT = "photo.metadata_fields"
_ALL_KEY = "all"


def resolve_photo_fields(tool: str, args: dict, query: str) -> dict:
    """Normalizza `args['fields']` di get_files da forme NL a valori canonici
    via detection_lexicon. No-op per altri tool o se il lessico e' assente."""
    if tool != "get_files" or not isinstance(args, dict) or "fields" not in args:
        return args
    raw = args.get("fields")
    if raw is None:
        return args
    items = raw if isinstance(raw, list) else [raw]
    try:
        import detection_lexicon as _dl
        mp = _dl.mapping(_CONCEPT)  # {canonical: [forme]}
    except Exception as ex:  # best-effort §7.9
        log.debug("photo_fields_resolver noop: %r", ex)
        return args
    if not mp:
        return args
    form_to_canon = {
        str(f).strip().lower(): canon
        for canon, forms in mp.items()
        for f in (forms or [])
    }
    enum_values = [k for k in mp if k != _ALL_KEY]

    resolved: list = []
    seen: set = set()
    changed = False
    for f in items:
        canon = form_to_canon.get(str(f).strip().lower())
        if canon == _ALL_KEY:
            # 'metadata'/'tutto'/'all'... -> tutti i campi (utente vuole tutto).
            return {**args, "fields": list(enum_values)}
        if canon is None:
            # Sconosciuto: lascialo com'e' → get_files erra onestamente (§2.8).
            if f not in seen:
                resolved.append(f)
                seen.add(f)
            continue
        changed = changed or (canon != f)
        if canon not in seen:
            resolved.append(canon)
            seen.add(canon)
    if not changed:
        return args
    out = dict(args)
    out["fields"] = resolved
    return out
