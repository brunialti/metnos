"""Propaga deterministicamente una richiesta esplicita di righe uniche.

Il planner puo' omettere l'opzione booleana anche quando la query dice
"una sola riga per coppia". Questo resolver non conosce tool, colonne o
domini specifici: agisce su qualunque consumer il cui JSON Schema dichiari
``unique_rows: boolean`` e prende il segnale NL dal lessico i18n comune.

Non interpreta la sola parola "duplicati": "trova file duplicati" descrive
i dati da cercare e non implica che due righe finali uguali vadano fuse.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_CONCEPT = "tabular.unique_rows_request"


def _supports_unique_rows(args_schema: dict | None) -> bool:
    if not isinstance(args_schema, dict):
        return False
    properties = args_schema.get("properties")
    if not isinstance(properties, dict):
        return False
    option = properties.get("unique_rows")
    return isinstance(option, dict) and option.get("type") == "boolean"


def resolve_unique_rows(tool: str, args: dict, query: str, *,
                        args_schema: dict | None = None) -> dict:
    """Imposta ``unique_rows=True`` solo su richiesta esplicita e supportata.

    ``tool`` e' intenzionalmente inutilizzato: il contratto e' determinato
    dallo schema, quindi nuovi executor compatibili funzionano senza liste
    hardcoded. La query corrente prevale sul default ``false`` del planner.
    """
    del tool  # API uniforme con gli altri resolver
    if not isinstance(args, dict) or not _supports_unique_rows(args_schema):
        return args
    try:
        import detection_lexicon as _dl
        requested = _dl.match(_CONCEPT, query or "")
    except Exception as ex:  # best-effort: il validator resta il backstop
        log.debug("unique_rows_resolver noop: %r", ex)
        return args
    if not requested or args.get("unique_rows") is True:
        return args
    return {**args, "unique_rows": True}
