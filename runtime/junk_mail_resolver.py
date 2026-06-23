"""junk_mail_resolver — §7.9 deterministico.

«sposta/filtra/cancella le email di spam / la posta indesiderata» → filtro su
`category_hints` (il segnale NEUTRO bulk/newsletter/noreply/auto/marketing che
`read_messages` gia' produce per ogni mail), NON su `type='spam'` (campo
inesistente -> 0) ne' su `classify_entries(junk)` (junk LLM = spam EVIDENTE,
~0 in un inbox gia' filtrato dal server; controprova 23/6).

L'utente che dice «spam» qui intende il RUMORE bulk (newsletter/promo), non il
phishing — scelta Roberto (opzione b, 23/6). Generale §7.3: vale per qualsiasi
filter_entries su mail con intento «posta indesiderata», via detection_lexicon.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Marker category_hints che indicano davvero NEWSLETTER/PROMO (SoT dei valori:
# runtime/mail_client.py::_category_hints). Raffinato 23/6 sui dati reali di
# Roberto: `list` (List-Unsubscribe/List-Id = mailing list, ti puoi disiscrivere
# = newsletter/promo) + `esp` (email service provider di marketing). NON
# `noreply`/`auto`/`bulk` da soli: troppo larghi, catturano l'automatico-ma-
# IMPORTANTE (conferme d'ordine, bollette, prenotazioni/host Airbnb, reset
# password) che e' `['noreply']`-puro. Precisione > recall: meglio mancare
# qualche promo che spostare una fattura.
_JUNK_MARKERS = ["list", "esp"]


def resolve_junk_mail(tool: str, args: dict, query: str) -> dict:
    """Se la query esprime intento «posta indesiderata» e il tool e'
    filter_entries, imposta il filtro su `category_hints` ∋ marker-bulk e
    rimuove il filtro-spam inventato dal proposer. No-op altrimenti."""
    if tool != "filter_entries" or not isinstance(args, dict) or not query:
        return args
    try:
        import detection_lexicon as _dl
        if not _dl.match("mail.junk_terms", query):
            return args
    except Exception as ex:  # best-effort §7.9
        log.debug("junk_mail_resolver noop: %r", ex)
        return args
    out = dict(args)
    out["where_field"] = "category_hints"
    out["where_in"] = list(_JUNK_MARKERS)
    # Rimuovi i filtri-spam inventati dal proposer che NON matchano le mail e
    # azzererebbero il risultato: `type='spam'`/`where_value='spam'` (campo
    # inesistente) e soprattutto `kind='mail'` — le entries di read_messages
    # NON hanno il campo `kind`, quindi `kind='mail'` scarta TUTTO (bug live
    # 0f1fe504: filtro a 0). Sostituiamo l'intero intento-spam con il predicato
    # preciso su category_hints.
    for k in ("type", "where_value", "kind"):
        out.pop(k, None)
    log.info("junk_mail_resolver: filter mail -> category_hints in %s",
             _JUNK_MARKERS)
    return out
