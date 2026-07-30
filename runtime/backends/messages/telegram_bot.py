"""Telegram bot backend (outbound notification).

Builtin backend per il `client="metnos"`/None dei verbi messaging quando
`via_channel="telegram"`. Riusa `runtime/channels/telegram.TelegramChannel`
(stesso bot del daemon). Token + chat_id da
`~/.config/metnos/credentials.env` (vedi channel module).

Verbi esposti:
- `send(args)`: vettoriale, accetta `messages=[{recipient_id|chat_id, body|text, subject?}]`.
- `read(args)`: stub (getUpdates non implementato qui; il daemon
  `ChannelDaemon` consuma updates via long-poll. Lasciato non-implementato
  per evitare race con il daemon).
- `find/delete/move`: non-applicabili (Telegram non e' una mailbox).
"""
from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from messages import get as _msg


def _new_channel():
    """Istanzia TelegramChannel senza persistenza dell'offset (per send)."""
    from channels.telegram import TelegramChannel
    return TelegramChannel(state_path=False)


def _attachment_paths(args: dict) -> list:
    """Normalizza `args['attachments']` → [(path, basename)]. Accetta stringhe
    (path) o dict {path|file, basename}. §2.4 tolleranza al confine NL."""
    import os
    raw = args.get("attachments_top") or args.get("attachments") or []
    out = []
    for a in (raw if isinstance(raw, list) else [raw]):
        if isinstance(a, str) and a.strip():
            out.append((a, os.path.basename(a)))
        elif isinstance(a, dict):
            p = a.get("path") or a.get("file") or ""
            if isinstance(p, str) and p.strip():
                out.append((p, a.get("basename") or os.path.basename(p)))
    return out


def send(args: dict) -> dict:
    """Invia 1+ messaggi via Telegram Bot API.

    Args:
        messages: list[{recipient_id|chat_id, body|text, subject?, target?,
                        recipient_user_id?, recipient_name?}]

    `subject` viene prefisso a body con doppia newline (mail-like UX).
    Ritorna {ok, ok_count, fail_count, results[], failed[]}.

    Mock mode (`METNOS_TELEGRAM_MOCK=1`, ADR seed): bypassa la chiamata
    reale aiogram, ritorna ok=True con message_id placeholder. Necessario
    per test e2e isolati (no telegram-daemon, no token reale) e per dry-run
    pre-deploy. Determinismo §7.9.
    """
    from channels import OutboundMessage

    messages = args.get("messages") or []
    if not isinstance(messages, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="messages", reason="must be a list")}
    if not messages:
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": [], "failed": []}

    if os.environ.get("METNOS_TELEGRAM_MOCK", "0") == "1":
        results: list[dict] = []
        _mock_atts = [n for _, n in _attachment_paths(args)]
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            rid = str(m.get("recipient_id") or m.get("chat_id") or "")
            if not rid:
                continue
            rec = {
                "channel": "telegram",
                "recipient_id": rid,
                "sent_message_id": f"mock_{i}",
                "sent_at_iso": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(timespec="seconds"),
                "ok": True,
                "_mock": True,
            }
            if _mock_atts:
                rec["attachments_sent"] = list(_mock_atts)
            for k in ("recipient_user_id", "recipient_name", "target"):
                if k in m:
                    rec[k] = m[k]
            results.append(rec)
        return {
            "ok": True,
            "ok_count": len(results),
            "fail_count": 0,
            "results": results,
            "failed": [],
        }

    try:
        ch = _new_channel()
    except Exception as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": _msg("ERR_EXT_SVC_UNAVAILABLE"),
                "detail": f"telegram channel init failed: {e}"}

    results, failed = [], []
    _atts = _attachment_paths(args)  # file deliverable → sendDocument (turn 6772053c)
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            failed.append({"index": i, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg=f"messages[{i}]", reason="must be a dict")})
            continue
        rid = m.get("recipient_id") or m.get("chat_id")
        if not rid:
            failed.append({"index": i, "error_code": "ERR_ARG_MISSING",
                           "error": _msg("ERR_ARG_MISSING", arg="recipient_id/chat_id")})
            continue
        body_text = m.get("body") or m.get("text") or m.get("body_html") or ""
        subject = m.get("subject")
        full = f"{subject}\n\n{body_text}".strip() if subject else body_text
        try:
            res = ch.send_to(str(rid), OutboundMessage(text=full))
        except Exception as e:
            failed.append({"index": i, "recipient_id": str(rid),
                           "error_code": "ERR_OP_FAILED",
                           "error": _msg("ERR_OP_FAILED", reason=f"{type(e).__name__}: {e}")})
            continue
        if not res.get("ok"):
            failed.append({"index": i, "recipient_id": str(rid),
                           "error_code": "ERR_OP_FAILED",
                           "error": _msg("ERR_OP_FAILED",
                                          reason=res.get("error", "telegram send failed"))})
            continue
        sent_id = ""
        if isinstance(res.get("result"), dict):
            sent_id = str(res["result"].get("message_id", ""))
        rec = {
            "channel": "telegram",
            "recipient_id": str(rid),
            "sent_message_id": sent_id,
            "sent_at_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "ok": True,
        }
        for k in ("recipient_user_id", "recipient_name", "target"):
            if k in m:
                rec[k] = m[k]
        # Allegati come DOCUMENTI Telegram (turn 6772053c: «crea file ma non lo
        # invia come allegato»). Telegram non raggiunge la LAN → upload binario
        # via sendDocument. Onesto §2.8: un allegato fallito entra in `failed`
        # (ok complessivo False) → l'utente non riceve un «inviato» bugiardo.
        for apath, aname in _atts:
            dres = ch.send_document(chat_id=str(rid), path=apath, basename=aname)
            if dres.get("ok"):
                rec.setdefault("attachments_sent", []).append(aname)
            else:
                rec.setdefault("attachments_failed", []).append(
                    {"name": aname, "error": dres.get("error")})
                failed.append({"index": i, "recipient_id": str(rid),
                               "error_code": "ERR_OP_FAILED",
                               "error": _msg("ERR_OP_FAILED",
                                             reason=f"sendDocument {aname}: {dres.get('error')}")})
        results.append(rec)
    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
    }


def read(args: dict) -> dict:
    """Stub: Telegram bot getUpdates non e' supportato in inquiry sincrona.

    Il channel daemon `runtime/channel_daemon.py` consuma gli update via
    long-poll persistente; tirare updates qui creerebbe race + perdita di
    eventi. Quando servira', il daemon esporra' un buffer interrogabile
    (es. via sqlite). Per ora ritorna `ok:false` esplicito (no silent
    failure, §2.8).
    """
    return {"ok": False, "error_code": "ERR_NOT_IMPLEMENTED",
            "error": _msg("ERR_NOT_IMPLEMENTED",
                           what="telegram bot read (gestito da ChannelDaemon long-poll)")}


def find(args: dict) -> dict:
    """Telegram non e' una mailbox cercabile in inquiry sincrona."""
    return {"ok": False, "error_code": "ERR_NOT_APPLICABLE",
            "error": _msg("ERR_NOT_APPLICABLE",
                           what="telegram find (nessun message store)")}


def delete(args: dict) -> dict:
    """Telegram bot puo' deleteMessage solo entro 48h e solo se inviato dal
    bot. Non implementato per ora."""
    return {"ok": False, "error_code": "ERR_NOT_IMPLEMENTED",
            "error": _msg("ERR_NOT_IMPLEMENTED",
                           what="telegram delete (usa client Telegram)")}


def move(args: dict) -> dict:
    """Telegram non ha folder."""
    return {"ok": False, "error_code": "ERR_NOT_APPLICABLE",
            "error": _msg("ERR_NOT_APPLICABLE",
                           what="telegram move (nessuna cartella)")}
