#!/usr/bin/env python3
"""send_messages — dispatcher canonical (Q1 canonical+args, 13/5/2026).

Invia uno o piu' messaggi su mail (SMTPS) o Telegram. Vettoriale: una call
accetta una lista di messaggi.

Architettura (refactor 13/5/2026, ADR pending):
- Dispatcher sottile: parse args + risoluzione multi-user + cross-user
  policy + routing al backend giusto in base a (via_channel, client).
- Backend builtin in `runtime/backends/messaging/<channel>_<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

Predisposizione plugin esterni:
- Quando arrivera' l'ADR plugin esterni, `_HANDLERS` sara' arricchito da
  loader scan di `~/.local/share/metnos/plugins/messaging-*/backends/`.
  Per ora: solo builtin import statici.

Modalita':
- **mail (back-compat)**: `to=email|list` su ogni message.
- **multi-user `to_user` + `via_channel`**: resolution via
  `runtime/users.py` (ADR 0083), policy cross-user via vaglio (ADR 0084).
- **reply Gmail**: `in_reply_to=<message id>` dentro il message; usa lo
  stesso verbo canonico `send` e conserva thread/References nel backend.

Contratto:
    args:
      messages: list[{to|to_user, subject?, body, ...}]
      account?: 'metnos_system'|'metnos_roberto'|'mykleos'|...
      to_user?: str | list[str]    # top-level fallback
      via_channel?: 'email' | 'telegram' | 'mail' | 'http' | 'auto'
      client?: 'metnos'            # backend implementation, default per channel
      actor?: str                  # default 'host'
    returns:
      {ok, ok_count, fail_count, results, failed}

NB: `via_channel='mail'` resta alias storico di `'email'` per
back-compat (vedi `_VIA_CHANNEL_ALIAS`).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Indirizzo email "vero" (fix q10 4/6/2026): l'LLM mette spesso un'email in
# `to_user`; va trattata come destinatario email diretto, NON cercata nel
# registro utenti (→ user_not_found). Deterministico §7.9.
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio, vector_result  # noqa: E402
from backends.messages import email_metnos, telegram_bot  # noqa: E402
from backends.messages import gmail_google_workspace  # noqa: E402

# --- dispatch table (CLAUDE.md §7.9 codice deterministico) -----------------
# Predisposizione plugin: questa mappa sara' arricchita dal plugin loader
# quando ADR plugin esterni sara' scritto. Per ora: SOLO builtin import
# statici (esplicito, no magia).
#
# I valori sono moduli (non funzioni dirette): la dispatch fa attribute
# lookup `module.send` a call-time, cosi' i test possono patchare il
# metodo `send` del modulo senza preoccuparsi di binding statici.
_HANDLERS = {
    ("email",    "metnos"):           email_metnos,
    ("email",    "google_workspace"): gmail_google_workspace,
    ("telegram", "metnos"):           telegram_bot,
    ("telegram", None):               telegram_bot,  # default-no-client alias
}

# Alias di `via_channel` per back-compat. 'mail' veniva usato storicamente
# (ADR 0083) prima dell'introduzione del canonico 'email'.
_VIA_CHANNEL_ALIAS = {"mail": "email"}

# Default uniforme per ogni canale: backend builtin Metnos
# (SMTP Migadu per email, Telegram bot per telegram). Per altri
# client (es. google_workspace gmail) passare esplicito `client=...`.
# Nessun probing OAuth a runtime — il PLANNER puo' decidere il client
# in base a hint linguistici o config user.
_DEFAULT_CLIENT = "metnos"



_PREFERRED_CHANNELS_AUTO = ("telegram", "email")


# --- helpers ---------------------------------------------------------------

def _to_list(x):
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return [str(v) for v in x if v]
    return []


def _import_users():
    try:
        import users as _users  # type: ignore
        return _users
    except ImportError as e:
        raise RuntimeError(f"users module not available: {e}") from e


def _check_cross_user_send(actor: str, target_user: dict | None, channel: str) -> dict:
    """Vaglio cross-user (ADR 0084). Policy:
    - actor=host → sempre permesso (host e' gatekeeper).
    - actor=guest, target=stesso guest → permesso.
    - actor=guest, target=altro user → vaglio inline pending (deny + reason).
    """
    if not actor or actor == "host":
        return {"allowed": True, "reason": None}
    if target_user is None:
        return {"allowed": False, "reason": "guest_cannot_send_to_unbound_recipient"}
    if target_user.get("name") == actor or target_user.get("id") == actor:
        return {"allowed": True, "reason": None}
    try:
        from vaglio import check_cross_user_send  # type: ignore
        return check_cross_user_send(actor, target_user.get("id"), channel)
    except ImportError:
        return {"allowed": False, "reason": "guest_to_other_user_requires_vaglio"}


def _resolve_via_channel(user: dict, requested: str) -> str | None:
    """Per `auto` ritorna il primo canale verificato in ordine
    _PREFERRED_CHANNELS_AUTO; per esplicito ritorna se verificato."""
    users = _import_users()
    chans = users.list_channels(user["id"])
    # users.db usa 'mail'/'telegram'; normalizziamo a 'email'/'telegram'
    by_name: dict[str, dict] = {}
    for c in chans:
        if not (c.get("verified_at") and c.get("recipient_id")):
            continue
        nm = c["channel"]
        if nm == "mail":
            by_name["email"] = c
        else:
            by_name[nm] = c
    if requested == "auto":
        for c in _PREFERRED_CHANNELS_AUTO:
            if c in by_name:
                return c
        return None
    return requested if requested in by_name else None


# --- main ------------------------------------------------------------------

def _normalize_via(via: str) -> str:
    return _VIA_CHANNEL_ALIAS.get(via, via)


def invoke(args):
    messages = args.get("messages")
    # Default account configurabile (config-hierarchy env>default §11): permette
    # di dirottare l'uscita su un account con quota quando il provider di default
    # è esaurito (es. quota esaurita → METNOS_DEFAULT_MAIL_ACCOUNT=account_work),
    # senza che l'LLM/planner scelga l'account (resta config, non intento).
    account = (args.get("account")
               or os.environ.get("METNOS_DEFAULT_MAIL_ACCOUNT")
               or "metnos_system")
    actor = args.get("actor") or "host"
    via_channel = _normalize_via(args.get("via_channel") or "auto")
    client = args.get("client")  # None => default per channel
    top_to_user = args.get("to_user")
    top_level_attachments = args.get("attachments")

    if not isinstance(messages, list):
        return {"ok": False,
                "error": _msg("ERR_ARG_NOT_LIST", arg="messages")}
    if not isinstance(account, str) or not account.strip():
        return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="account")}
    # Intercetta SUBITO un account INESISTENTE (decisione 2/6): un account che
    # non risolve a uno noto e' una probabile ALLUCINAZIONE dell'LLM → errore.
    # Va PRIMA del no-op su messages=[] (§2.1: lista vuota = "niente da fare",
    # non un errore — ma solo se l'account e' valido). Riservati esclusi.
    if account.strip().lower() not in ("all", "auto", "noreply", "dyn"):
        try:
            from mail_client import resolve_account as _resolve_acc
            if _resolve_acc(account) is None:
                return {"ok": False, "error_code": "ERR_UNKNOWN_ACCOUNT",
                        "error": _msg("ERR_UNKNOWN_ACCOUNT", account=account),
                        "error_class": "invalid_args",
                        "results": [], "failed": [], "ok_count": 0, "fail_count": 0}
        except Exception:
            pass  # mail_client non disponibile → degradazione sicura, non bloccare
    if len(messages) > 50:
        return {"ok": False,
                "error": _msg("ERR_SEND_RATE_LIMIT", max=50)}

    # Risolvi ogni messaggio in una "request" {channel, client, msg_normalized}.
    # Una entry input puo' espandere in N entry output (multi-target/multi-user).
    requests: list[dict] = []  # ciascuna: {"channel","client","msg",index,...}
    failed_pre: list[dict] = []

    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            failed_pre.append({"index": i, "error": _msg("ERR_ARG_NOT_DICT", arg="message")})
            continue
        per_msg_to_user = m.get("to_user") or top_to_user
        per_msg_via = _normalize_via(m.get("via_channel") or via_channel or "auto")

        # Una risposta in-thread e' una specializzazione dell'azione canonica
        # `send`, non un verbo pubblico separato. Il destinatario e gli header
        # vengono ricavati dal messaggio originale dal backend Gmail.
        in_reply_to = m.get("in_reply_to")
        if in_reply_to is not None:
            reply_client = client or "google_workspace"
            reply_channel = "email" if per_msg_via == "auto" else per_msg_via
            if not isinstance(in_reply_to, str) or not in_reply_to.strip():
                failed_pre.append({"index": i, "error_code": "ERR_ARG_INVALID",
                                   "error": _msg("ERR_ARG_INVALID", arg="in_reply_to", reason="non-empty string")})
                continue
            if not isinstance(m.get("body"), str) or not m["body"].strip():
                failed_pre.append({"index": i, "error_code": "ERR_ARG_MISSING",
                                   "error": _msg("ERR_ARG_MISSING", arg="body")})
                continue
            if reply_channel != "email" or reply_client != "google_workspace":
                failed_pre.append({"index": i,
                                   "error": _msg("ERR_NOT_APPLICABLE", what=f"reply via {reply_channel}/{reply_client}")})
                continue
            requests.append({"operation": "reply", "channel": "email",
                             "client": "google_workspace", "msg": dict(m),
                             "index": i, "recipient_user": None})
            continue

        if per_msg_to_user is not None:
            # Multi-user path: resolve to recipient_id + concrete channel.
            try:
                users = _import_users()
            except RuntimeError as e:
                failed_pre.append({"index": i, "error": str(e)})
                continue
            target_list = _to_list(per_msg_to_user) or [per_msg_to_user]
            if isinstance(per_msg_to_user, list):
                target_list = [str(x) for x in per_msg_to_user if x]
            for tgt in target_list:
                s = str(tgt).strip()
                if not s:
                    failed_pre.append({"index": i, "target": tgt,
                                       "error": "empty_target"})
                    continue
                if s.startswith("@"):
                    # Direct chat_id (no user lookup)
                    chan = "telegram" if per_msg_via == "auto" else per_msg_via
                    if chan != "telegram":
                        failed_pre.append({"index": i, "target": tgt,
                                           "error": _msg("ERR_CHATID_TELEGRAM_ONLY", chan=chan)})
                        continue
                    msg_n = dict(m)
                    msg_n["recipient_id"] = s[1:]
                    msg_n["target"] = tgt
                    msg_n.pop("to", None)
                    msg_n.pop("to_user", None)
                    requests.append({"channel": "telegram",
                                     "client": client or _DEFAULT_CLIENT,
                                     "msg": msg_n, "index": i,
                                     "recipient_user": None})
                    continue
                if _RE_EMAIL.match(s):
                    # `to_user` e' un INDIRIZZO EMAIL → invio diretto via email,
                    # niente lookup nel registro utenti (fix q10 4/6/2026).
                    chan = "email" if per_msg_via == "auto" else per_msg_via
                    if chan not in ("email", "mail"):
                        failed_pre.append({"index": i, "target": tgt,
                                           "error": _msg("ERR_NOT_APPLICABLE", what=f"email via {chan}")})
                        continue
                    msg_n = dict(m)
                    msg_n["to"] = s
                    msg_n.pop("to_user", None)
                    msg_n["target"] = tgt
                    _subj = msg_n.get("subject")
                    if not isinstance(_subj, str) or not _subj.strip():
                        _body = str(msg_n.get("body") or "")
                        _first = next((ln.strip() for ln in _body.splitlines() if ln.strip()), "")
                        msg_n["subject"] = _first[:78] if _first else "Metnos"
                    requests.append({"channel": "email",
                                     "client": client or _DEFAULT_CLIENT,
                                     "msg": msg_n, "index": i,
                                     "recipient_user": None})
                    continue
                # Self-send: destinatario = l'attore stesso ("mia mail" → il
                # PLANNER emette il token canonico ${RUNTIME:actor}; a runtime
                # puo' arrivare gia' sostituito con l'actor id, es. 'host').
                # Risolvi attore→utente per NOME o per RUOLO (host e' un ruolo,
                # non un nome → get_user('host') fallisce, list_users(role=...)
                # lo trova). Generale §7.3: nessun match su pronomi/stringhe.
                if s in (actor, "${RUNTIME:actor}"):
                    user = (users.get_user(actor)
                            or (users.list_users(role=actor) or [None])[0])
                else:
                    user = users.get_user(s)
                if not user:
                    failed_pre.append({"index": i, "target": tgt,
                                       "error": "user_not_found"})
                    continue
                chosen = _resolve_via_channel(user, per_msg_via)
                if chosen is None:
                    err = ("no_verified_channel" if per_msg_via == "auto"
                           else f"channel_not_paired:{per_msg_via}")
                    failed_pre.append({"index": i, "target": tgt,
                                       "user": user.get("name"), "error": err})
                    continue
                # Lookup recipient_id sull'user (chiave originale users.db = 'mail'/'telegram')
                chans = users.list_channels(user["id"])
                # cerca usando chiave originaria
                src_chan_key = "mail" if chosen == "email" else chosen
                rid = next(
                    (c["recipient_id"] for c in chans
                     if c["channel"] == src_chan_key and c.get("verified_at")),
                    None,
                )
                if not rid:
                    failed_pre.append({"index": i, "target": tgt,
                                       "error": "channel_not_paired"})
                    continue
                # Vaglio cross-user
                chk = _check_cross_user_send(actor, user, chosen)
                if not chk["allowed"]:
                    failed_pre.append({
                        "index": i,
                        "target": tgt,
                        "channel": chosen,
                        "recipient_user_id": user.get("id"),
                        "recipient_name": user.get("name"),
                        "error_code": "ERR_VAGLIO_REQUIRED",
                        "error": chk["reason"] or "cross_user_send_blocked",
                    })
                    continue
                msg_n = dict(m)
                msg_n["recipient_id"] = rid
                msg_n["recipient_user_id"] = user.get("id")
                msg_n["recipient_name"] = user.get("name")
                msg_n["target"] = tgt
                msg_n.pop("to", None)
                msg_n.pop("to_user", None)
                requests.append({"channel": chosen,
                                 "client": client or _DEFAULT_CLIENT,
                                 "msg": msg_n, "index": i,
                                 "recipient_user": user})
        else:
            # Classic mail path (back-compat): m.get('to') = email/list.
            # Resta su email/SMTP.
            if not m.get("to"):
                failed_pre.append({"index": i,
                                   "error": _msg("ERR_ARG_MISSING_ONE_OF", options="to, to_user")})
                continue
            msg_n = dict(m)
            # Subject defaultabile (§2.8): un send con destinatario+corpo NON deve
            # fallire per subject mancante (il planner lo omette spesso). Derivalo
            # dal corpo (prima riga non vuota, troncata) o usa un generico.
            # Universale, model-independent.
            _subj = msg_n.get("subject")
            if not isinstance(_subj, str) or not _subj.strip():
                _body = str(msg_n.get("body") or "")
                _first = next((ln.strip() for ln in _body.splitlines() if ln.strip()), "")
                msg_n["subject"] = _first[:78] if _first else "Metnos"
            requests.append({"channel": "email",
                             "client": client or _DEFAULT_CLIENT,
                             "msg": msg_n, "index": i,
                             "recipient_user": None})

    # Raggruppa per (operation, channel, client) e dispatch al backend builtin.
    grouped: dict[tuple[str, str, str | None], list[dict]] = {}
    for r in requests:
        key = (r.get("operation", "send"), r["channel"], r["client"])
        grouped.setdefault(key, []).append(r["msg"])

    results, failed = [], list(failed_pre)
    for key, msgs in grouped.items():
        operation, channel, selected_client = key
        backend = _HANDLERS.get((channel, selected_client))
        if backend is None:
            avail = sorted({k[0] for k in _HANDLERS})
            for m in msgs:
                failed.append({"index": -1,
                               "error": _msg("ERR_NOT_APPLICABLE", what=str(key))})
            continue
        if operation == "reply":
            for m in msgs:
                reply_args = {
                    "message_id": m["in_reply_to"],
                    "body": m["body"],
                }
                if m.get("from_header"):
                    reply_args["from_header"] = m["from_header"]
                res = backend.reply(reply_args)
                if res.get("decision") == "needs_inputs":
                    return res
                results.extend(res.get("results", []))
                failed.extend(res.get("failed", []))
                if (not res.get("ok") and res.get("error")
                        and not res.get("failed")):
                    failed.append({"index": -1, "channel": channel,
                                   "error_code": res.get("error_code"),
                                   "error": res["error"]})
            continue
        backend_args = {"messages": msgs}
        if channel == "email":
            backend_args["account"] = account
        # Allegati top-level → ai backend che li consegnano (email + telegram).
        # Turn 6772053c: erano inoltrati SOLO a email → su telegram il file
        # creato non partiva mai come documento. §2.8 (l'utente lo chiedeva).
        if top_level_attachments is not None and channel in ("email", "telegram"):
            backend_args["attachments_top"] = top_level_attachments
        # Attribute lookup a call-time: i test possono patchare `backend.send`.
        res = backend.send(backend_args)
        if not res.get("ok") and res.get("error") and not res.get("results") and not res.get("failed"):
            # Backend-level error (connect failed, etc.): tag the originating messages.
            for m in msgs:
                failed.append({"index": -1, "channel": channel,
                               "error_code": res.get("error_code"),
                               "error": res["error"]})
            continue
        for r in res.get("results", []):
            results.append(r)
        for f in res.get("failed", []):
            failed.append(f)

    return vector_result(results, failed, entry_key="results")


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
