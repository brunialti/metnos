"""Task scheduler v2 `promoter_digest` — notifica admin via Telegram digest.

Trigger `daily@07:00`. Per ogni proposta in stato `promoted_grace` con
`notified_at IS NULL`:
- Invia messaggio Telegram all'admin (recipient = primo host con canale
  telegram verificato in `users.db`).
- Inline keyboard (ADR 0090 `telegram_inline`):
    [ok] callback_data='promoter:<id>:ok'
    [rollback] callback_data='promoter:<id>:rollback'
- Body = practical_example markdown (auto-split a 4000 char).
- UPDATE notified_at=now.

Cap N=10 per fire (anti-flood). Disabilitato via env
`METNOS_PROMOTER_NOTIFY_ADMIN=false`.

§7.9 deterministico. Fallback graceful se canale Telegram non disponibile:
events audit + skip (niente crash globale).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .promoter_state import audit_append, mark_notified, pending_notification


CAP_PER_FIRE = 10
TELEGRAM_MESSAGE_MAX = 4000  # margin vs limite 4096 di Telegram


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notify_enabled() -> bool:
    raw = os.environ.get("METNOS_PROMOTER_NOTIFY_ADMIN", "true")
    return raw.lower() not in ("0", "false", "no")


def _resolve_admin_recipient() -> tuple[str | None, str | None]:
    """Trova il chat_id dell'admin via users.db (primo host con canale
    telegram verificato).

    Ritorna `(recipient_id, error)` — entrambi None se ok+empty.
    """
    try:
        import users
    except ImportError as ex:
        return None, f"users_unavailable: {ex}"
    try:
        hosts = users.list_users(role="host")
    except Exception as ex:  # noqa: BLE001
        return None, f"list_users_failed: {ex}"
    if not hosts:
        return None, "no_host_user"
    host = hosts[0]
    try:
        ch = users.get_channel(host["id"], "telegram")
    except Exception as ex:  # noqa: BLE001
        return None, f"get_channel_failed: {ex}"
    if not ch or not ch.get("verified_at"):
        return None, "telegram_not_verified"
    rid = ch.get("recipient_id")
    if not rid:
        return None, "recipient_id_missing"
    return str(rid), None


def _split_text_for_telegram(text: str, *, max_len: int = TELEGRAM_MESSAGE_MAX,
                                ) -> list[str]:
    """Split su newline boundary; fallback hard-split a max_len."""
    if not text:
        return [""]
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _build_inline_keyboard(proposal_id: str) -> list[list[dict]]:
    """Inline keyboard ADR 0090 con due bottoni: ok / rollback.

    callback_data formato `promoter:<id>:ok|rollback`. Limite Telegram 64
    byte: proposal_id e' tipicamente `<ts>_<name>` < 50 char, OK.
    """
    return [
        [
            {"text": "ok", "data": f"promoter:{proposal_id}:ok"},
            {"text": "rollback",
             "data": f"promoter:{proposal_id}:rollback"},
        ],
    ]


def _send_to_admin(recipient: str, body: str,
                   keyboard: list[list[dict]] | None,
                   ) -> tuple[bool, str | None]:
    """Invia un messaggio via TelegramChannel. Ritorna `(ok, error)`."""
    try:
        from channels.telegram import TelegramChannel
        from channels import OutboundMessage
    except ImportError as ex:
        return False, f"telegram_unavailable: {ex}"
    try:
        ch = TelegramChannel()
    except Exception as ex:  # noqa: BLE001
        return False, f"telegram_init_failed: {ex}"
    try:
        ch.send(
            recipient=recipient,
            message=OutboundMessage(text=body, buttons=keyboard),
        )
    except Exception as ex:  # noqa: BLE001
        return False, f"telegram_send_failed: {ex}"
    return True, None


def _format_digest_body(row: dict) -> str:
    """Formatta il corpo Telegram di una notifica promote.

    Tre sezioni:
    - Header: "Promote: <name> (grace fino a <iso>)"
    - Practical example (markdown gia' deterministico)
    - Footer: "Rispondi: ok per confermare, rollback per annullare."
    """
    name = row.get("name") or "?"
    grace_until = row.get("grace_until") or ""
    example = row.get("practical_example") or "(nessun esempio disponibile)"
    header = f"**Promoter**: nuovo executor `{name}`"
    if grace_until:
        header += f"\nGrace fino a: `{grace_until}`"
    footer = (
        "\n---\nConferma con il bottone **ok** o ripristina con **rollback** "
        "entro la fine della grace window."
    )
    return header + "\n\n" + example + footer


def task_promoter_digest(payload: dict | None = None) -> dict:
    """Callback scheduler v2 `promoter_digest` (daily@07:00).

    Payload ignorato. Ritorna shape RunResult.
    """
    if not _notify_enabled():
        return {
            "ok": True,
            "ok_count": 0,
            "error_count": 0,
            "metadata": {"reason": "notify_disabled_via_env"},
        }

    rows = pending_notification()
    if not rows:
        return {
            "ok": True,
            "ok_count": 0,
            "error_count": 0,
            "metadata": {"reason": "no_pending"},
        }
    rows = rows[:CAP_PER_FIRE]

    recipient, err = _resolve_admin_recipient()
    if recipient is None:
        for r in rows:
            audit_append({
                "ts": _now_iso(),
                "proposal_id": r.get("proposal_id"),
                "action": "notify_skipped",
                "reason": err,
            })
        return {
            "ok": True,
            "ok_count": 0,
            "error_count": len(rows),
            "metadata": {
                "cap": CAP_PER_FIRE,
                "candidates_seen": len(rows),
                "reason": err,
            },
        }

    ok_count = 0
    error_count = 0
    for r in rows:
        proposal_id = r.get("proposal_id") or ""
        body = _format_digest_body(r)
        chunks = _split_text_for_telegram(body)
        keyboard = _build_inline_keyboard(proposal_id)
        all_ok = True
        first_err: str | None = None
        for i, chunk in enumerate(chunks):
            # Inline keyboard solo sull'ULTIMO chunk per non doppiare bottoni.
            kb = keyboard if i == len(chunks) - 1 else None
            sent_ok, send_err = _send_to_admin(recipient, chunk, kb)
            if not sent_ok:
                all_ok = False
                first_err = send_err
                break
        if all_ok:
            mark_notified(proposal_id)
            ok_count += 1
            audit_append({
                "ts": _now_iso(),
                "proposal_id": proposal_id,
                "action": "notified",
                "recipient": recipient,
                "chunks": len(chunks),
            })
        else:
            error_count += 1
            audit_append({
                "ts": _now_iso(),
                "proposal_id": proposal_id,
                "action": "notify_failed",
                "error": first_err,
            })
    return {
        "ok": True,
        "ok_count": ok_count,
        "error_count": error_count,
        "metadata": {
            "cap": CAP_PER_FIRE,
            "candidates_seen": len(rows),
            "recipient": recipient,
        },
    }


__all__ = [
    "task_promoter_digest",
    "_split_text_for_telegram",
    "_build_inline_keyboard",
    "_format_digest_body",
    "CAP_PER_FIRE",
    "TELEGRAM_MESSAGE_MAX",
]
