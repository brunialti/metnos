# SPDX-License-Identifier: AGPL-3.0-only
"""self_recipient_resolver.py — risoluzione DETERMINISTICA del destinatario "self".

Problema generale: "inviami / alla mia email / mandami / a me" non è un INTENTO
che l'LLM debba esprimere come arg — è l'IDENTITÀ dell'actor, contesto già noto
al runtime. Il planner (qualunque modello) emette `send_messages` con corpo +
allegato ma con args di instradamento INAFFIDABILI: a volte senza `to`/`to_user`,
a volte senza nemmeno `via_channel` (osservato su Qwen, turn 805ba95c) → l'executor
fallisce "manca destinatario" e la mail non parte. Model-independent.

Soluzione (gemello di `backend_resolver` ADR 0165, stessa filosofia di
`${RUNTIME:actor}` ADR 0163): il segnale robusto NON è l'arg `via_channel`
(che il planner spesso omette) ma la QUERY. Quando la query chiede un invio via
EMAIL e non nomina un destinatario ESTERNO, il runtime risolve il destinatario
all'actor e fissa il canale email. Deterministico (§7.9), single-point (wired
accanto a resolve_backend_arg in engine/executor.py).

Sicurezza (§2.8 + ADR 0155, no misroute):
  - SOLO se la query indica email (parola-email) o via_channel è già email —
    chat/auto senza parola-email → path "mandami=chat" (ea1ba7e), non si tocca;
  - SOLO se nessun destinatario è già presente;
  - SOLO se la query NON nomina un destinatario esterno
    (`_send_has_explicit_recipient` False, bias-sicuro) → no misroute;
  - SOLO se l'identità dell'actor è nota (`_actor_email`). Altrimenti niente
    invenzioni: l'executor segnala "manca destinatario" come prima.
"""
from __future__ import annotations

import re

_EMAIL_VIA = ("email", "mail")
# Parola-email nella QUERY (IT+EN): segnale d'intento robusto, indipendente dal
# fatto che il planner abbia o meno valorizzato via_channel.
_EMAIL_QUERY = re.compile(r"e-?mail|\bmail\b|posta\s+elettronica", re.I)


def _has_recipient(args: dict) -> bool:
    """True se un destinatario (to/to_user) è già presente, top-level o per-msg."""
    if args.get("to_user") or args.get("to"):
        return True
    for m in (args.get("messages") or []):
        if isinstance(m, dict) and (m.get("to") or m.get("to_user")):
            return True
    return False


def resolve_self_recipient(tool: str, args: dict, query: str) -> dict:
    """Se `tool` è un send via email senza destinatario esplicito e la query è
    self-targeted, inietta `to=<email actor>` (e `via_channel=email` se assente)
    in ogni messaggio. Ritorna `args` (eventualmente copia modificata). Mai solleva."""
    if tool != "send_messages" or not isinstance(args, dict):
        return args
    if _has_recipient(args):
        return args
    q = query or ""
    via = str(args.get("via_channel") or "").strip().lower()
    # Intento email dalla QUERY (robusto) o da via_channel se già email.
    if not (via in _EMAIL_VIA or _EMAIL_QUERY.search(q)):
        return args
    # Destinatario esterno nominato (email o "a/to <NomeProprio>") → non è self:
    # no auto-instradamento all'actor (no misroute). Rilevatore esistente.
    try:
        from compound_decomposer import _send_has_explicit_recipient
        if _send_has_explicit_recipient(q):
            return args
    except Exception:
        pass
    actor_email = args.get("_actor_email")
    if not actor_email:
        return args  # identità ignota → lascia che l'executor segnali (§2.8)
    out = dict(args)
    if via not in _EMAIL_VIA:
        out["via_channel"] = "email"  # il planner l'aveva omesso → esplicitalo
    msgs = out.get("messages")
    if isinstance(msgs, list) and msgs:
        out["messages"] = [
            ({**m, "to": (m.get("to") or actor_email)} if isinstance(m, dict) else m)
            for m in msgs
        ]
    else:
        out["to"] = actor_email
    return out
