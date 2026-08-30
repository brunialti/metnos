"""Ricava dalla richiesta la direzione di un'operazione, invece di dedurla.

Un executor puo' portare la direzione in un argomento booleano: la stessa
capacita' mette e toglie, e cio' che cambia e' un flag. E' il disegno giusto
per il vocabolario — un verbo solo invece di due — ma lascia al modello una
decisione binaria che la richiesta contiene gia' per intero.

Misurato prima di scrivere questo file (17/8/2026): «installa PowerToys su
pc-roberto» produceva `uninstall=true` in **12 prove su 12**, con tre stesure
diverse della descrizione del manifest — testa che afferma, testa che definisce
entrambe le direzioni, testa senza esempio letterale. Il testo non era la leva.
Una decisione che la richiesta determina non si affida a un modello (§7.9).

Il resolver non conosce nessun executor per nome: agisce su qualunque schema
dichiari `uninstall: boolean`, e prende il segnale dal lessico i18n comune,
cosi' vale in ogni lingua di Metnos e non solo dove qualcuno ha scritto le
parole a mano.

Differenza dai resolver fratelli: questo **corregge** anche un valore gia'
scritto. `unique_rows` aggiunge un'opzione che il planner puo' aver omesso, e
un `true` di troppo sarebbe al massimo una riga in meno; qui un `true` di
troppo disinstalla un programma al posto di installarlo. La richiesta e'
l'autorita', non il valore che il modello ha proposto.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_CONCEPT = "residual_am.packages.direction_alias"
_ARG = "uninstall"


def _supports_direction(args_schema: dict | None) -> bool:
    if not isinstance(args_schema, dict):
        return False
    properties = args_schema.get("properties")
    if not isinstance(properties, dict):
        return False
    option = properties.get(_ARG)
    return isinstance(option, dict) and option.get("type") == "boolean"


def _direction_from_query(query: str) -> bool | None:
    """Resolve an unambiguous reviewed direction, or decline authority."""

    try:
        import detection_lexicon as _dl
        import detection_lexicon_seed_residual_am as _residual_lexicon

        mapping = _residual_lexicon.ready_complete_mapping(_CONCEPT)
        matches = [
            direction
            for direction, forms in mapping.items()
            if _dl.match_any(forms, query, "word")
        ]
    except Exception as ex:
        log.debug("install direction lexicon unavailable: %r", ex)
        return None
    if len(matches) != 1:
        return None
    return matches[0] == "uninstall"


def resolve_install_direction(tool: str, args: dict, query: str, *,
                              args_schema: dict | None = None) -> dict:
    """Allinea `uninstall` a cio' che la richiesta dice davvero.

    ``tool`` resta inutilizzato di proposito: il contratto lo determina lo
    schema, quindi un executor nuovo con lo stesso argomento funziona senza
    aggiungere nomi da nessuna parte.

    Senza query non si tocca niente: una ripresa dopo un consenso porta gli
    argomenti gia' decisi, e reinterpretarli sarebbe cambiare l'operazione
    che la persona ha approvato.
    """
    del tool  # API uniforme con gli altri resolver
    if not isinstance(args, dict) or not _supports_direction(args_schema):
        return args
    if not (query or "").strip():
        return args
    wants_uninstall = _direction_from_query(query)
    if wants_uninstall is None:
        # Missing, pending, incomplete or ambiguous linguistic evidence never
        # authorizes this resolver to rewrite a mutating operation.
        return args
    if bool(args.get(_ARG)) == wants_uninstall:
        return args
    log.info("[install_direction] %s=%s dalla richiesta (era %r)",
             _ARG, wants_uninstall, args.get(_ARG))
    return {**args, _ARG: wants_uninstall}
