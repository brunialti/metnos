# SPDX-License-Identifier: AGPL-3.0-only
"""self_recipient_resolver.py — risoluzione DETERMINISTICA del destinatario "self".

Problema generale: "inviami / alla mia email / mandami / a me" non è un INTENTO
che l'LLM debba esprimere come arg — è l'IDENTITÀ dell'actor, contesto già noto
al runtime. Il planner (qualunque modello) spesso emette `send_messages` con
canale email + corpo + allegato ma SENZA `to`/`to_user` → l'executor fallisce
"manca destinatario" e la mail non parte (bug live 3/6 su Qwen, ma model-
independent).

Soluzione (gemello di `backend_resolver` ADR 0165, stessa filosofia di
`${RUNTIME:actor}` ADR 0163): quando un send via EMAIL non ha destinatario
esplicito ESTERNO, il runtime risolve il destinatario all'actor. Deterministico
(§7.9), model-independent, single-point (wired accanto a resolve_backend_arg in
engine/executor.py).

Sicurezza (§2.8 + ADR 0155, no misroute):
  - si attiva SOLO con canale email esplicito (chat/auto → path "mandami=chat",
    ea1ba7e, gestito altrove);
  - SOLO se nessun destinatario è già presente;
  - SOLO se la query NON nomina un destinatario esterno
    (`_send_has_explicit_recipient` False, bias-sicuro). Se un esterno è nominato
    ma non risolto NON si auto-instrada all'actor (sarebbe un misroute) →
    l'executor segnala "manca destinatario" come prima;
  - SOLO se l'identità dell'actor è nota (`_actor_email` iniettato dal runtime).
    Se assente, non si inventa nulla (no silent).
"""
from __future__ import annotations

_EMAIL_VIA = ("email", "mail")


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
    self-targeted, inietta `to = <email dell'actor>` in ogni messaggio.
    Ritorna `args` (eventualmente una copia modificata). Mai solleva."""
    if tool != "send_messages" or not isinstance(args, dict):
        return args
    via = str(args.get("via_channel") or "").strip().lower()
    if via not in _EMAIL_VIA:
        return args
    if _has_recipient(args):
        return args
    # Destinatario esterno nominato (email o "a/to <NomeProprio>") → NON è self:
    # non auto-instradare all'actor (no misroute). Riusa il rilevatore esistente.
    try:
        from compound_decomposer import _send_has_explicit_recipient
        if _send_has_explicit_recipient(query or ""):
            return args
    except Exception:
        pass
    actor_email = args.get("_actor_email")
    if not actor_email:
        return args  # identità ignota → lascia che l'executor segnali (§2.8)
    out = dict(args)
    msgs = out.get("messages")
    if isinstance(msgs, list) and msgs:
        out["messages"] = [
            ({**m, "to": (m.get("to") or actor_email)} if isinstance(m, dict) else m)
            for m in msgs
        ]
    else:
        out["to"] = actor_email
    return out
