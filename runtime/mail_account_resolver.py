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

Account NOMINATO (estensione 12/6/2026, faglia arg-leakage L1): un piano
SERVITO da un layer di cache (L1 champion / L0 0b) porta l'account literal
della SUA query d'origine («mail di metnos» → account='metnos_system');
servito a «controlla la mail di account_work» leakerebbe l'account sbagliato.
Quando la query nomina UN solo account configurato (word-match esatto),
il resolver lo IMPONE: l'account e' uno slot query-specific che si
ri-riempie dalla query ATTUALE, non si eredita.

Sicurezza (§2.8):
- solo `read_messages` (lettura, idempotente — mai allargare azioni mutating);
- solo canale email (via_channel assente o email/mail);
- account gia' multi (lista esplicita) → noop;
- query che nomina 2+ account → noop (scelta ambigua: decide il planner);
- query senza quantificatore ne' account nominato → noop (il default
  resta dell'executor/piano).
"""
from __future__ import annotations

import re

# «tutta/tutte/tutti + (0-3 parole) + parola-mail» IT, «all + (0-3 parole) +
# parola-mail» EN. Inoltre il POSSESSIVO PLURALE «(le) mie/miei + ... + parola-
# mail» (IT) e «my + ... + mail» (EN): «le mie email» = TUTTE le mie caselle
# (≠ «la mia mail» singolare, escluso da mie/miei). Word-boundary, case-insens.
_ALL_MAIL_QUERY = re.compile(
    r"\btutt[aei]\b(?:\s+\S+){0,3}?\s+"
    r"(?:e-?mail\w*|mail\w*|posta\b|casell\w*|account\w*|messagg\w*)"
    r"|\bmie[i]?\b(?:\s+\S+){0,3}?\s+"
    r"(?:e-?mail\w*|mail\w*|posta\b|casell\w*|account\w*|messagg\w*)"
    r"|\b(?:all|my)\b(?:\s+\S+){0,3}?\s+"
    r"(?:e-?mails?\b|mails?\b|inbox(?:es)?\b|accounts?\b|"
    r"mailbox(?:es)?\b|messages?\b)",
    re.IGNORECASE,
)

_EMAIL_VIA = ("", "email", "mail")


def _named_accounts(query_lower: str, known: list[str]) -> list[str]:
    """Account configurati citati per nome (word-match esatto) nella query,
    nell'ordine deterministico di `list_known_accounts()`."""
    out = []
    for name in known:
        if not name:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", query_lower):
            out.append(name)
    return out


def resolve_mail_account(tool: str, args: dict, query: str) -> dict:
    """Ri-risolve `account` su read_messages dalla query ATTUALE (§7.9):
    account nominato → quello; quantificatore «tutta/all» → "all"; altrimenti
    noop. Ritorna args (copia se modificati). Mai eccezioni: su dubbio, noop."""
    if not isinstance(args, dict) or not query:
        return args
    # Mail MUTATING (move/delete/set): impone SOLO un account NOMINATO singolo
    # (mai "all" su un'azione mutating §7.9). Senza, il proposer non mette
    # `account` e move() default a metnos_system → mailbox SBAGLIATA → gli uid
    # (di account_work) non esistono lì → 0 spostate (bug live 1f3dcc7e). Se
    # l'account è già esplicito, rispetta. Se la query non nomina un account,
    # noop (la validazione-uid in move() resta onesta §2.8).
    if tool in ("move_messages", "delete_messages", "set_messages"):
        acct_m = args.get("account")
        if isinstance(acct_m, str) and acct_m.strip():
            return args
        try:
            from mail_client import list_known_accounts
            named_m = _named_accounts(query.lower(), list_known_accounts())
        except Exception:
            return args
        if len(named_m) == 1:
            out = dict(args)
            out["account"] = named_m[0]
            return out
        return args
    if tool != "read_messages":
        return args
    via = str(args.get("via_channel") or "").strip().lower()
    if via not in _EMAIL_VIA:
        return args
    acct = args.get("account")
    if isinstance(acct, list):
        return args  # gia' multi-account esplicito
    try:
        from mail_client import list_known_accounts
        known = list_known_accounts()
    except Exception:
        known = []
    named = _named_accounts(query.lower(), known)
    if len(named) == 1:
        # L'utente ha scelto: l'account nominato VINCE su qualsiasi valore
        # ereditato dal piano (champion L1 / piano cachato di un'altra query).
        if isinstance(acct, str) and acct.strip() == named[0]:
            return args
        out = dict(args)
        out["account"] = named[0]
        return out
    if named:
        return args  # 2+ account nominati: scelta ambigua, decide il planner
    if not _ALL_MAIL_QUERY.search(query):
        return args
    if isinstance(acct, str) and acct.strip().lower() == "all":
        return args  # gia' canonico
    out = dict(args)
    out["account"] = "all"
    return out
