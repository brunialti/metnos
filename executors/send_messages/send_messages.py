#!/usr/bin/env python3
"""send_messages — executor di Metnos v1.1.

Invia uno o piu' messaggi su mail (SMTPS) o Telegram. Vettoriale: una call
accetta una lista di messaggi.

Modalita' (4/5/2026, ADR 0083 multi-user):
- **mail (default)**: ogni message ha `to=email|list` (back-compat).
- **multi-user `to_user` + `via_channel`**: il PLANNER passa nomi-utente
  (es. "lucia") al posto di email/chat_id; questo executor li risolve via
  `runtime/users.resolve_recipients`. via_channel='auto' = primo canale
  verificato dell'user (telegram > mail > http).

Best-effort: una send fallita non blocca le altre.

CRITICAL IRREVERSIBILE: una mail / un telegram inviato non si annulla.
revertible=false. La carta di approvazione del runtime (capability
`mail:send` o `messaging:send`) e' sempre mostrata in autonomia
Supervised.

Vaglio cross-user (ADR 0084): se l'invocatore (`actor` nel contesto turno
= host o nome di un guest) prova a inviare a un guest *diverso* da se',
applichiamo policy:
- actor host → permesso (puo' inviare ai propri guest).
- actor guest → richiede vaglio one-shot (placeholder MVP: emette
  warning in failed[]; il flusso interattivo sara' implementato quando
  il dialog manager supportera' inline cross-user prompts).

Contratto:
    args:
      messages: list[{to|to_user, subject?, body, ...}]
      account?: 'metnos_system'|'metnos_roberto'|'mykleos'
      to_user?: str | list[str]    # name o user_id (top-level fallback)
      via_channel?: 'telegram' | 'mail' | 'http' | 'auto'
      actor?: str                  # propagato da run_turn (default 'host')
    returns:
      {ok, ok_count, fail_count, results, failed}
"""
import datetime
import json
import mimetypes
import os
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, "/opt/myclaw/runtime")
from mail_client import open_smtp, _account_creds  # noqa: E402
from messages import get as msg  # noqa: E402

# Limite anti-abuso: dimensione totale degli allegati per messaggio.
# 25 MB e' il limite tipico SMTP-mainstream (Gmail/Migadu/register.it).
_MAX_ATTACH_BYTES_PER_MSG = 25 * 1024 * 1024

_PREFERRED_CHANNELS_AUTO = ("telegram", "mail", "http")


def _to_list(x):
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return [str(v) for v in x if v]
    return []


def _resolve_attachments(raw, *, max_total_bytes=_MAX_ATTACH_BYTES_PER_MSG):
    """Normalizza/valida la lista attachments. Accetta:
    - lista di stringhe (path)
    - lista di dict {path, filename?, content_type?}
    Ritorna (list[dict_normalized], errors_list).
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"attachments deve essere una lista, ricevuto {type(raw).__name__}"]
    out, errs, total = [], [], 0
    for i, item in enumerate(raw):
        if isinstance(item, str):
            path, fname, ctype = item, None, None
        elif isinstance(item, dict):
            path = item.get("path")
            fname = item.get("filename")
            ctype = item.get("content_type")
        else:
            errs.append(f"attachments[{i}]: type {type(item).__name__} non supportato (string o dict)")
            continue
        if not path or not isinstance(path, str):
            errs.append(f"attachments[{i}]: 'path' mancante o non stringa")
            continue
        p = Path(os.path.expanduser(path))
        if not p.is_file():
            errs.append(f"attachments[{i}]: file non trovato: {path}")
            continue
        try:
            size = p.stat().st_size
        except OSError as e:
            errs.append(f"attachments[{i}]: stat fallita per {path}: {e}")
            continue
        total += size
        if total > max_total_bytes:
            errs.append(
                f"attachments: dimensione totale supera {max_total_bytes} byte "
                f"(cap anti-abuso SMTP). Ridurre il numero/peso degli allegati."
            )
            return [], errs
        if not ctype:
            ctype, _ = mimetypes.guess_type(p.name)
            if not ctype:
                ctype = "application/octet-stream"
        if "/" not in ctype:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        out.append({"path": str(p), "filename": fname or p.name,
                    "maintype": maintype, "subtype": subtype, "size": size})
    return out, errs


# --- multi-user resolution -------------------------------------------------

def _import_users():
    """Lazy import del modulo users (single source per multi-user lookup)."""
    try:
        import users as _users  # type: ignore
        return _users
    except ImportError as e:
        raise RuntimeError(f"users module not available: {e}") from e


def _check_cross_user_send(actor: str, target_user: dict | None,
                           channel: str) -> dict:
    """Vaglio cross-user (ADR 0084).

    Politica MVP:
    - actor=host → SEMPRE permesso (host e' gatekeeper).
    - actor=guest, target=stesso guest → permesso.
    - actor=guest, target=altro user → richiede vaglio (per ora deny + msg).

    Ritorna dict {allowed: bool, reason: str|None}.
    Hook futuro: `vaglio.check_cross_user_send` per integrare il dialog
    manager con prompt one-shot Sì/No.
    """
    if not actor or actor == "host":
        return {"allowed": True, "reason": None}
    # actor e' un guest
    if target_user is None:
        # Direct chat_id (no user lookup): per un guest non e' permesso
        # inviare a un destinatario non identificato in users.db.
        return {"allowed": False,
                "reason": "guest_cannot_send_to_unbound_recipient"}
    if target_user.get("name") == actor or target_user.get("id") == actor:
        return {"allowed": True, "reason": None}
    # Cross-user: deny + reason. Hook al vaglio inline pending.
    try:
        # Best-effort: chiama vaglio se disponibile.
        from vaglio import check_cross_user_send  # type: ignore
        return check_cross_user_send(actor, target_user.get("id"), channel)
    except ImportError:
        return {"allowed": False,
                "reason": "guest_to_other_user_requires_vaglio"}


# --- backend dispatch -------------------------------------------------------

def _send_via_telegram(chat_id: str, body: str) -> dict:
    """Invia un messaggio Telegram al chat_id (recipient_id risolto).

    Usa `runtime/channels/telegram.TelegramChannel.send_to`. Se le
    credenziali Telegram non sono configurate, ritorna error_code
    ERR_EXT_SVC_UNAVAILABLE per uniformita' col branch SMTP.
    """
    try:
        sys.path.insert(0, "/opt/myclaw/runtime")
        from channels import OutboundMessage
        from channels.telegram import TelegramChannel
    except ImportError as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"telegram channel import failed: {e}"}
    try:
        ch = TelegramChannel(state_path=False)
    except Exception as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"telegram channel init failed: {e}"}
    res = ch.send_to(str(chat_id), OutboundMessage(text=body or ""))
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "telegram send failed")}
    sent_id = ""
    if isinstance(res.get("result"), dict):
        sent_id = str(res["result"].get("message_id", ""))
    return {"ok": True, "sent_message_id": sent_id}


def _resolve_via_channel(user: dict, requested: str) -> str | None:
    """Per `via_channel='auto'` ritorna il primo canale verificato dell'user
    nell'ordine `_PREFERRED_CHANNELS_AUTO`. Per channel esplicito, ritorna
    quello se l'user ce l'ha verificato, altrimenti None."""
    users = _import_users()
    chans = users.list_channels(user["id"])
    by_name = {c["channel"]: c for c in chans
               if c.get("verified_at") and c.get("recipient_id")}
    if requested == "auto":
        for c in _PREFERRED_CHANNELS_AUTO:
            if c in by_name:
                return c
        return None
    return requested if requested in by_name else None


# --- main ------------------------------------------------------------------

def invoke(args):
    messages = args.get("messages")
    account = args.get("account") or "metnos_system"
    actor = args.get("actor") or "host"
    via_channel = args.get("via_channel") or "auto"
    top_to_user = args.get("to_user")
    top_level_attachments = args.get("attachments")

    if not isinstance(messages, list):
        return {"ok": False,
                "error": "missing or invalid required arg 'messages' (must be a list)"}
    if not isinstance(account, str) or not account.strip():
        return {"ok": False, "error": "account must be a non-empty string"}
    if len(messages) > 50:
        return {"ok": False,
                "error": "send rate limit: max 50 messaggi per call (anti-spam guard)"}

    # Decidi quale modalita' usa OGNI messaggio: se ha `to_user` (per-msg)
    # o se top-level `to_user` e' presente, e' multi-user. Altrimenti mail.
    # Tipo channel calcolato per messaggio (un msg puo' essere multi-user
    # mentre un altro e' mail puro).

    # Pre-classify: nessuna apertura SMTP se non serve mail.
    needs_smtp = False
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("to") and not (m.get("to_user") or top_to_user):
            needs_smtp = True
            break
        # Multi-user: SMTP serve solo se via_channel risolvera' a 'mail'
        # ma non lo sappiamo prima del lookup. Per semplicita', apri SMTP
        # se *qualunque* via_channel candidato include 'mail'.
        eff_via = m.get("via_channel") or via_channel
        if eff_via in ("mail", "auto"):
            needs_smtp = True
            break

    smtp = None
    sender = None
    if needs_smtp:
        try:
            creds = _account_creds(account)
            sender = creds["user"]
            smtp = open_smtp(account)
        except Exception as e:
            # Se serviva SMTP e fallisce: ritorna errore. Niente downgrade
            # silente a telegram-only (CLAUDE.md §2.8 no silent failure).
            # Eccezione: se l'unica modalita' richiesta sara' telegram (es.
            # to_user con telegram verified) si potrebbe procedere; ma
            # senza lookup preventivo non lo sappiamo. Best-effort: se
            # tutti i messages hanno to_user con telegram verificato il
            # lookup successivo lo dimostrera'; intanto proviamo a
            # proseguire e lasciar fallire i singoli messages mail.
            smtp = None
            sender = None
            smtp_err = f"SMTP connect failed: {e}"
            # Ricontrolla: se NESSUN msg ha 'to' classico, possiamo
            # proseguire senza SMTP. Se invece ne esiste uno, chiudiamo.
            any_classic = any(
                isinstance(m, dict) and m.get("to") and not (m.get("to_user") or top_to_user)
                for m in messages
            )
            if any_classic:
                return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                        "error": smtp_err}

    results, failed = [], []
    try:
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                failed.append({"index": i, "error": "message must be a dict"})
                continue

            # Multi-user lookup (precede lo split mail-classic vs telegram).
            per_msg_to_user = m.get("to_user") or top_to_user
            eff_via = m.get("via_channel") or via_channel

            recipients_resolved: list[dict] = []
            if per_msg_to_user is not None:
                target_list = _to_list(per_msg_to_user) or [per_msg_to_user]
                if isinstance(per_msg_to_user, list):
                    target_list = [str(x) for x in per_msg_to_user if x]
                # Per via_channel='auto' ogni user puo' avere canale diverso:
                # il loop sotto risolve per-user; qui pre-flight per il
                # 'channel concreto' usato per il lookup di resolve_recipients
                # quando l'user-channel e' esplicito.
                try:
                    users = _import_users()
                except RuntimeError as e:
                    failed.append({"index": i, "error": str(e)})
                    continue
                for tgt in target_list:
                    s = str(tgt).strip()
                    if not s:
                        recipients_resolved.append(
                            {"target": tgt, "user": None, "recipient_id": None,
                             "channel": None, "error": "empty_target"})
                        continue
                    if s.startswith("@"):
                        # Direct chat_id, niente user
                        chan = "telegram" if eff_via == "auto" else eff_via
                        recipients_resolved.append(
                            {"target": tgt, "user": None,
                             "recipient_id": s[1:], "channel": chan,
                             "error": None if s[1:] else "empty_chat_id"})
                        continue
                    user = users.get_user(s)
                    if not user:
                        recipients_resolved.append(
                            {"target": tgt, "user": None, "recipient_id": None,
                             "channel": None, "error": "user_not_found"})
                        continue
                    chosen = _resolve_via_channel(user, eff_via)
                    if chosen is None:
                        err = ("no_verified_channel" if eff_via == "auto"
                               else f"channel_not_paired:{eff_via}")
                        recipients_resolved.append(
                            {"target": tgt, "user": user,
                             "recipient_id": None, "channel": None,
                             "error": err})
                        continue
                    chans = users.list_channels(user["id"])
                    rid = next(
                        (c["recipient_id"] for c in chans
                         if c["channel"] == chosen and c.get("verified_at")),
                        None,
                    )
                    if not rid:
                        recipients_resolved.append(
                            {"target": tgt, "user": user,
                             "recipient_id": None, "channel": None,
                             "error": "channel_not_paired"})
                        continue
                    recipients_resolved.append(
                        {"target": tgt, "user": user, "recipient_id": rid,
                         "channel": chosen, "error": None})

                if not recipients_resolved:
                    failed.append({"index": i, "error": "no recipients resolved"})
                    continue

                # Vaglio cross-user (ADR 0084) e dispatch per-recipient.
                for r in recipients_resolved:
                    if r["error"]:
                        failed.append({"index": i, "target": r["target"],
                                        "error": r["error"]})
                        continue
                    chk = _check_cross_user_send(actor, r["user"], r["channel"])
                    if not chk["allowed"]:
                        failed.append({
                            "index": i,
                            "target": r["target"],
                            "channel": r["channel"],
                            "recipient_user_id": (r["user"] or {}).get("id"),
                            "recipient_name": (r["user"] or {}).get("name"),
                            "error_code": "ERR_VAGLIO_REQUIRED",
                            "error": chk["reason"] or "cross_user_send_blocked",
                        })
                        continue
                    chan = r["channel"]
                    body_text = m.get("body") or m.get("body_html") or ""
                    if chan == "telegram":
                        sub = m.get("subject") or ""
                        text = (f"{sub}\n\n{body_text}".strip()
                                if sub else body_text)
                        send_res = _send_via_telegram(r["recipient_id"], text)
                        rec = {
                            "channel": "telegram",
                            "recipient_user_id": (r["user"] or {}).get("id"),
                            "recipient_name": (r["user"] or {}).get("name"),
                            "recipient_id": r["recipient_id"],
                            "sent_message_id": send_res.get("sent_message_id", ""),
                            "sent_at_iso": datetime.datetime.now(
                                datetime.timezone.utc).isoformat(timespec="seconds"),
                            "ok": bool(send_res.get("ok")),
                        }
                        if send_res.get("ok"):
                            results.append(rec)
                        else:
                            rec["error"] = send_res.get("error", "send failed")
                            failed.append({"index": i, **rec})
                    elif chan == "mail":
                        # mail via SMTP (riuso branch classico)
                        sub = m.get("subject") or "(no subject)"
                        body_t = m.get("body") or ""
                        body_h = m.get("body_html")
                        if not body_t and not body_h:
                            failed.append({"index": i, "target": r["target"],
                                            "error": "missing 'body' or 'body_html'"})
                            continue
                        if smtp is None:
                            failed.append({"index": i, "target": r["target"],
                                            "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                                            "error": "smtp not connected"})
                            continue
                        email_msg = EmailMessage()
                        email_msg["From"] = sender
                        email_msg["To"] = r["recipient_id"]
                        email_msg["Subject"] = sub
                        email_msg["Date"] = datetime.datetime.now(
                            datetime.timezone.utc).strftime(
                            "%a, %d %b %Y %H:%M:%S %z")
                        if body_t:
                            email_msg.set_content(body_t)
                        if body_h:
                            email_msg.add_alternative(body_h, subtype="html")
                        try:
                            smtp.send_message(email_msg, from_addr=sender,
                                               to_addrs=[r["recipient_id"]])
                            results.append({
                                "channel": "mail",
                                "recipient_user_id": (r["user"] or {}).get("id"),
                                "recipient_name": (r["user"] or {}).get("name"),
                                "recipient_id": r["recipient_id"],
                                "subject": sub,
                                "message_id": email_msg.get("Message-ID", ""),
                                "sent_at_iso": datetime.datetime.now(
                                    datetime.timezone.utc).isoformat(
                                    timespec="seconds"),
                                "account": account,
                                "ok": True,
                            })
                        except Exception as e:
                            failed.append({"index": i, "target": r["target"],
                                            "error": str(e)})
                    else:
                        failed.append({"index": i, "target": r["target"],
                                        "error": f"channel {chan} not supported"})
                continue  # next message

            # Branch classico mail (back-compat): m.get('to') = email/list
            to_list = _to_list(m.get("to"))
            cc_list = _to_list(m.get("cc"))
            bcc_list = _to_list(m.get("bcc"))
            subject = m.get("subject")
            body = m.get("body")
            body_html = m.get("body_html")
            if not to_list:
                failed.append({"index": i, "error": "missing 'to' (string or list) or 'to_user'"})
                continue
            if not subject or not isinstance(subject, str):
                failed.append({"index": i, "error": "missing 'subject' string"})
                continue
            if not body and not body_html:
                failed.append({"index": i, "error": "missing 'body' (text) or 'body_html'"})
                continue
            if smtp is None:
                failed.append({"index": i, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                               "error": "smtp not connected"})
                continue
            per_msg_attach = m.get("attachments")
            if per_msg_attach is None and top_level_attachments is not None:
                per_msg_attach = top_level_attachments
            attach_list, attach_errs = _resolve_attachments(per_msg_attach)
            if attach_errs:
                failed.append({"index": i, "to": to_list, "subject": subject,
                               "error": "; ".join(attach_errs)})
                continue
            email_msg = EmailMessage()
            email_msg["From"] = sender
            email_msg["To"] = ", ".join(to_list)
            if cc_list:
                email_msg["Cc"] = ", ".join(cc_list)
            email_msg["Subject"] = subject
            email_msg["Date"] = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
            if body:
                email_msg.set_content(body)
            if body_html:
                email_msg.add_alternative(body_html, subtype="html")
            for a in attach_list:
                try:
                    with open(a["path"], "rb") as fh:
                        data = fh.read()
                    email_msg.add_attachment(data, maintype=a["maintype"],
                                              subtype=a["subtype"],
                                              filename=a["filename"])
                except Exception as e:
                    failed.append({"index": i, "to": to_list, "subject": subject,
                                   "error": f"attachment read failed for {a['path']}: {e}"})
                    attach_list = None
                    break
            if attach_list is None:
                continue
            try:
                rcpts = to_list + cc_list + bcc_list
                smtp.send_message(email_msg, from_addr=sender, to_addrs=rcpts)
                results.append({
                    "channel": "mail",
                    "to": to_list,
                    "subject": subject,
                    "message_id": email_msg.get("Message-ID", ""),
                    "sent_at_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "account": account,
                    "attachments_count": len(attach_list),
                    "attachments_names": [a["filename"] for a in attach_list] if attach_list else [],
                    "ok": True,
                })
            except Exception as e:
                failed.append({"index": i, "to": to_list, "subject": subject, "error": str(e)})
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
