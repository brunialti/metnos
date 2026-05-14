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
import sys
from typing import Any

_RUNTIME = "/opt/myclaw/runtime"
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def _new_channel():
    """Istanzia TelegramChannel senza persistenza dell'offset (per send)."""
    from channels.telegram import TelegramChannel
    return TelegramChannel(state_path=False)


def send(args: dict) -> dict:
    """Invia 1+ messaggi via Telegram Bot API.

    Args:
        messages: list[{recipient_id|chat_id, body|text, subject?, target?,
                        recipient_user_id?, recipient_name?}]

    `subject` viene prefisso a body con doppia newline (mail-like UX).
    Ritorna {ok, ok_count, fail_count, results[], failed[]}.
    """
    from channels import OutboundMessage

    messages = args.get("messages") or []
    if not isinstance(messages, list):
        return {"ok": False, "error": "messages must be a list"}
    if not messages:
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": [], "failed": []}

    try:
        ch = _new_channel()
    except Exception as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"telegram channel init failed: {e}"}

    results, failed = [], []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            failed.append({"index": i, "error": "message must be a dict"})
            continue
        rid = m.get("recipient_id") or m.get("chat_id")
        if not rid:
            failed.append({"index": i, "error": "missing recipient_id/chat_id"})
            continue
        body_text = m.get("body") or m.get("text") or m.get("body_html") or ""
        subject = m.get("subject")
        full = f"{subject}\n\n{body_text}".strip() if subject else body_text
        try:
            res = ch.send_to(str(rid), OutboundMessage(text=full))
        except Exception as e:
            failed.append({"index": i, "recipient_id": str(rid),
                           "error": f"{type(e).__name__}: {e}"})
            continue
        if not res.get("ok"):
            failed.append({"index": i, "recipient_id": str(rid),
                           "error": res.get("error", "telegram send failed")})
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
            "error": "telegram bot read is owned by ChannelDaemon long-poll; "
                     "no synchronous inquiry API yet"}


def find(args: dict) -> dict:
    """Telegram non e' una mailbox cercabile in inquiry sincrona."""
    return {"ok": False, "error_code": "ERR_NOT_IMPLEMENTED",
            "error": "telegram find not applicable (no message store)"}


def delete(args: dict) -> dict:
    """Telegram bot puo' deleteMessage solo entro 48h e solo se inviato dal
    bot. Non implementato per ora."""
    return {"ok": False, "error_code": "ERR_NOT_IMPLEMENTED",
            "error": "telegram delete not implemented (use Telegram client app)"}


def move(args: dict) -> dict:
    """Telegram non ha folder."""
    return {"ok": False, "error_code": "ERR_NOT_IMPLEMENTED",
            "error": "telegram has no folders, move not applicable"}
