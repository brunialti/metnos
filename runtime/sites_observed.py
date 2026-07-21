"""Codici osservativi di navigazione (ADR 0191 P4 / §6.1).

`reason_code` = slug STABILE interno (audit/logica), MAI tradotto. Il testo utente
e' una chiave `MSG_SITES_RC_*` SEPARATA (seed IT+EN nel DB i18n, non in git —
dominio Fable). Qui: la mappa codice→chiave e la funzione di PRECEDENZA
deterministica (status prima del contenuto, primo match vince).

Regola d'onesta' (§2.8): MAI dedurre `automation_blocked` dal solo status. 403
prova un rifiuto, 429 un limite, 5xx un guasto — non la CAUSA.
"""
from __future__ import annotations

# 5xx che indicano indisponibilita' (non 501/505 che sono semantici del metodo).
_UNAVAILABLE_STATUS = frozenset({500, 502, 503, 504})

# Sotto questa soglia (caratteri di testo visibile) + zero controlli = superficie
# vuota (near-empty), tipica del blocco a monte del rendering.
EMPTY_SURFACE_MAX_CHARS = 32

# Slug STABILE interno -> chiave i18n user-facing (seed IT+EN nel DB, dominio Fable).
REASON_MSG = {
    "rate_limited": "MSG_SITES_RC_RATE_LIMITED",
    "http_forbidden": "MSG_SITES_RC_FORBIDDEN",
    "page_unavailable": "MSG_SITES_RC_UNAVAILABLE",
    "challenge_observed": "MSG_SITES_RC_CHALLENGE",
    "empty_surface": "MSG_SITES_RC_EMPTY_SURFACE",
}


def observational_reason(*, status: int | None = None,
                         retry_after: bool = False,
                         net_error: bool = False,
                         challenge: bool = False,
                         body_len: int | None = None,
                         control_count: int | None = None) -> str | None:
    """Precedenza deterministica (§6.1), primo match vince. Ritorna uno slug
    STABILE o None (pagina usabile). Status server-autoritativo PRIMA del
    contenuto.

    Nota (deviazione onesta dal §6.1): `response is None` NON e' trattato come
    `page_unavailable` — Playwright ritorna None anche per navigazioni same-doc
    (hash-route SPA) legittime. L'indisponibilita' si rileva col `net_error`
    (eccezione di `goto`) o con uno status 5xx, mai da None-senza-errore.
    """
    if status == 429 or retry_after:
        return "rate_limited"
    if status == 403:
        return "http_forbidden"
    if net_error or (status is not None and status in _UNAVAILABLE_STATUS):
        return "page_unavailable"
    if challenge:
        return "challenge_observed"
    if (body_len is not None and body_len < EMPTY_SURFACE_MAX_CHARS
            and control_count == 0):
        return "empty_surface"
    return None


def response_signals(response) -> dict:
    """Estrae (status, retry_after) da una `Response` Playwright in modo
    difensivo. `response` None (same-doc/cache) -> nessun segnale di status."""
    if response is None:
        return {"status": None, "retry_after": False}
    status = None
    try:
        status = int(response.status)
    except Exception:
        status = None
    retry_after = False
    try:
        headers = response.headers or {}
        retry_after = any(k.lower() == "retry-after" for k in headers)
    except Exception:
        retry_after = False
    return {"status": status, "retry_after": retry_after}
