# SPDX-License-Identifier: AGPL-3.0-only
"""self_recipient_resolver.py — risoluzione DETERMINISTICA del destinatario "self".

Problema generale: "inviami / alla mia email / mandami / a me" non è un INTENTO
che l'LLM debba esprimere come arg — è l'IDENTITÀ dell'actor, contesto già noto
al runtime. Il planner (qualunque modello) emette `send_messages` con args di
instradamento INAFFIDABILI e in forme incoerenti (osservato su Qwen):
  - senza `to`/`to_user`;            (turn f5caaf4f)
  - senza `via_channel` (None);       (turn 805ba95c)
  - `messages` come dict/stringa, non lista;
  - `to`/`to_user` = placeholder non risolto `${FILLER:email}` / `${RUNTIME:..}`.
Tutte → l'executor fallisce e la mail non parte. Model-independent.

Soluzione (gemello di `backend_resolver` ADR 0165, stessa filosofia di
`${RUNTIME:actor}` ADR 0163): il segnale robusto è la QUERY (non gli arg che il
planner emette a caso). Quando la query chiede un invio EMAIL e non nomina un
destinatario ESTERNO, il runtime CANONICALIZZA gli args del send: `messages` →
lista di dict, `via_channel` → email, e il destinatario → email dell'actor
(rimpiazzando vuoti/placeholder). Deterministico (§7.9), §2.4 (tolleranza al
confine NL→determinismo), single-point (engine/executor.py accanto a
resolve_backend_arg).

Sicurezza (§2.8 + ADR 0155, no misroute): solo intento email (parola-email in
query o via già email); solo se la query NON nomina un destinatario esterno
(`_send_has_explicit_recipient` False, bias-sicuro); solo se l'actor è noto. Un
destinatario REALE già presente (email/utente, non placeholder) è preservato.
"""
from __future__ import annotations

import re

_EMAIL_VIA = ("email", "mail")
_EMAIL_QUERY = re.compile(r"e-?mail|\bmail\b|posta\s+elettronica", re.I)
_PLACEHOLDER = re.compile(r"\$\{[^}]*\}")  # ${FILLER:..}, ${RUNTIME:..}, ${stepN..}


def _real_recipient(v) -> bool:
    """True se `v` è un destinatario REALE: stringa non vuota e NON un
    placeholder non risolto (`${...}`). Una lista non vuota di tali conta."""
    if isinstance(v, list):
        return any(_real_recipient(x) for x in v)
    return isinstance(v, str) and v.strip() != "" and not _PLACEHOLDER.search(v)


def _has_recipient(args: dict) -> bool:
    if _real_recipient(args.get("to_user")) or _real_recipient(args.get("to")):
        return True
    for m in (args.get("messages") or []):
        if isinstance(m, dict) and (_real_recipient(m.get("to"))
                                    or _real_recipient(m.get("to_user"))):
            return True
    return False


def resolve_self_recipient(tool: str, args: dict, query: str) -> dict:
    """Canonicalizza gli args di un send-email self-targeted. Ritorna `args`
    (eventualmente copia). Mai solleva."""
    if tool != "send_messages" or not isinstance(args, dict):
        return args
    if _has_recipient(args):
        return args
    q = query or ""
    via = str(args.get("via_channel") or "").strip().lower()
    if not (via in _EMAIL_VIA or _EMAIL_QUERY.search(q)):
        return args  # chat/auto senza parola-email → path "mandami=chat" (ea1ba7e)
    try:
        from compound_decomposer import _send_has_explicit_recipient
        if _send_has_explicit_recipient(q):
            return args  # esterno nominato → no misroute
    except Exception:
        pass
    actor_email = args.get("_actor_email")
    if not actor_email:
        return args  # identità ignota → l'executor segnali (§2.8), niente invenzioni
    out = dict(args)
    if via not in _EMAIL_VIA:
        out["via_channel"] = "email"
    # to_user placeholder a livello top → rimuovi (usa il path 'to' classico).
    if out.get("to_user") and not _real_recipient(out.get("to_user")):
        out.pop("to_user", None)
    # §2.4: coerci `messages` a lista di dict, poi inietta un destinatario REALE.
    msgs = out.get("messages")
    if isinstance(msgs, dict):
        msgs = [msgs]
    elif isinstance(msgs, str):
        msgs = [{"body": msgs}]
    elif not isinstance(msgs, list):
        msgs = []
    norm = [(m if isinstance(m, dict) else {"body": str(m)}) for m in msgs] or [{}]

    def _fix(m: dict) -> dict:
        m = dict(m)
        if not _real_recipient(m.get("to")):
            m["to"] = actor_email
        if m.get("to_user") and not _real_recipient(m.get("to_user")):
            m.pop("to_user", None)
        return m

    out["messages"] = [_fix(m) for m in norm]
    return out
