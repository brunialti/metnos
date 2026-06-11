# SPDX-License-Identifier: AGPL-3.0-only
"""mail_account_resolver.py — risoluzione DETERMINISTICA di «tutta la posta».

Problema generale (bug live 10/6/2026): «controlla tutta la mia posta ultime
24h» leggeva UN solo account. Il backend `email_metnos.read` supporta gia'
`account="all"` (itera su `list_known_accounts()` e aggrega), ma il proposer
LLM copia la FORMA dal PATTERN del manifest (`account="metnos_system"`,
§2.5): il quantificatore «tutta/tutte/all» della query non arriva mai
all'arg. Model-independent.

Soluzione (gemello di `backend_resolver` ADR 0165 e
`self_recipient_resolver`, stessa filosofia): il segnale robusto e' la QUERY,
non gli arg che il planner emette. Quando la query chiede TUTTA la posta
(«tutta la mia posta», «tutte le mail», «all my email», «all accounts») e
NON nomina un account configurato, il runtime canonicalizza
`account="all"`. Deterministico (§7.9), §2.4 (tolleranza al confine
NL→determinismo), single-point (engine/executor.py accanto agli altri
resolver).

Sicurezza (§2.8, non rompere l'account nominato):
- solo `read_messages` (lettura, idempotente — mai allargare azioni mutating);
- solo canale email (via_channel assente o email/mail);
- account gia' multi ("all" o lista) → noop;
- la query nomina un account configurato (word-match esatto su
  `list_known_accounts()`) → l'utente ha scelto, nessun override.
"""
from __future__ import annotations

import re

# «tutta/tutte/tutti + (0-3 parole) + parola-mail» IT, «all + (0-3 parole) +
# parola-mail» EN. Word-boundary, case-insensitive.
_ALL_MAIL_QUERY = re.compile(
    r"\btutt[aei]\b(?:\s+\S+){0,3}?\s+"
    r"(?:e-?mail\w*|mail\w*|posta\b|casell\w*|account\w*|messagg\w*)"
    r"|\ball\b(?:\s+\S+){0,3}?\s+"
    r"(?:e-?mails?\b|mails?\b|inbox(?:es)?\b|accounts?\b|"
    r"mailbox(?:es)?\b|messages?\b)",
    re.IGNORECASE,
)

_EMAIL_VIA = ("", "email", "mail")


def _query_names_account(query_lower: str, known: list[str]) -> bool:
    """True se la query cita per nome (word-match esatto) un account
    configurato: l'utente ha scelto, il resolver non deve sovrascrivere."""
    for name in known:
        if not name:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", query_lower):
            return True
    return False


def resolve_mail_account(tool: str, args: dict, query: str) -> dict:
    """Canonicalizza `account="all"` su read_messages quando la query chiede
    TUTTA la posta senza nominare un account. Ritorna args (copia se
    modificati). Mai eccezioni: su dubbio, noop."""
    if tool != "read_messages" or not isinstance(args, dict) or not query:
        return args
    via = str(args.get("via_channel") or "").strip().lower()
    if via not in _EMAIL_VIA:
        return args
    if not _ALL_MAIL_QUERY.search(query):
        return args
    acct = args.get("account")
    if isinstance(acct, list):
        return args  # gia' multi-account esplicito
    if isinstance(acct, str) and acct.strip().lower() == "all":
        return args  # gia' canonico
    try:
        from mail_client import list_known_accounts
        known = list_known_accounts()
    except Exception:
        known = []
    if _query_names_account(query.lower(), known):
        return args
    out = dict(args)
    out["account"] = "all"
    return out
