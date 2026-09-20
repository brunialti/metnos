# SPDX-License-Identifier: AGPL-3.0-only
"""sites_url_scrub — redazione deterministica di URL sensibili (spec sites §3.2 FIX E).

Un token in un URL È un segreto: `?token=...`, `#access_token=...`, `?code=...`
(OAuth authorization code), ticket SAML, session id. Da OGNI `url`/`final_url`
che il dominio `sites` restituisce, logga o persiste (result, turn-record,
audit-log, prompt) i parametri sensibili vanno rimossi PRIMA che l'URL lasci il
broker.

Deterministico §7.9: nessun LLM, nessuna euristica probabilistica. Match ESATTO
case-insensitive sul NOME del parametro (query E fragment). Il valore è
sostituito con `REDACTED`, non rimosso, così l'utente vede che c'era un segreto
(onestà §2.8) senza vederne il contenuto.

Contratto:
    scrub_url(url) -> str          URL con i parametri sensibili redatti
    SENSITIVE_PARAMS               frozenset dei nomi-parametro sensibili
"""
from __future__ import annotations

import urllib.parse

# spec §3.2 FIX E — nomi-parametro il cui VALORE è un segreto trasportato in
# chiaro nell'URL. Match esatto case-insensitive sul nome (mai substring: `code`
# non deve matchare `zipcode`). Lista chiusa, estendibile con escalation.
SENSITIVE_PARAMS = frozenset({
    "token", "code", "access_token", "id_token", "refresh_token",
    "op_token", "auth_token", "oauth_token",
    "ticket", "sig", "signature", "saml", "samlresponse", "otp",
    "session", "sessionid", "session_id", "session-id", "sid",
    "jsessionid", "phpsessid",
    "auth", "authorization", "password", "passwd",
    "csrf", "csrf_token", "xsrf", "xsrf_token",
    "secret", "client_secret", "api_key", "apikey", "key",
})

_REDACTED = "REDACTED"


def _scrub_qs(raw: str) -> str:
    """Redige i parametri sensibili in una query-string / fragment
    `k=v&k2=v2`. Preserva ordine e chiavi non sensibili. Ritorna la stringa
    ri-encodata; stringa vuota resta vuota."""
    if not raw:
        return raw
    # keep_blank_values=True: non perdere `?flag=` senza valore.
    pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True)
    if not pairs:
        return raw
    out = []
    for k, v in pairs:
        if k.lower() in SENSITIVE_PARAMS:
            out.append((k, _REDACTED))
        else:
            out.append((k, v))
    return urllib.parse.urlencode(out)


def scrub_url(url: str) -> str:
    """Ritorna l'URL con i parametri sensibili (query E fragment) redatti a
    `REDACTED`. Idempotente. Non-URL o stringa vuota → ritornati invariati
    (fail-safe: mai sollevare, il caller logga comunque qualcosa)."""
    if not url or not isinstance(url, str):
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return _REDACTED  # URL malformato: meglio redigere tutto che leakare
    # Un fragment può a sua volta essere una query (implicit-flow OAuth:
    # `#access_token=...&token_type=...`). Scrub sia query sia fragment.
    new_query = _scrub_qs(parts.query)
    new_fragment = _scrub_qs(parts.fragment) if "=" in parts.fragment else parts.fragment
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path, new_query, new_fragment))
