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

from .promoter_state import audit_append, mark_notified, pending_notification


CAP_PER_FIRE = 10
TELEGRAM_MESSAGE_MAX = 4000  # margin vs limite 4096 di Telegram

# Soglia per modalita' aggregata: se ci sono >= N decisioni pending in
# TOTALE (grace + review_needed + archived_recent), il digest manda UN
# messaggio con bottone "Apri form" invece di N messaggi per-item.
# E3 11/5/2026 Roberto: forma aggregata preferibile per ridurre flood.
AGGREGATED_THRESHOLD = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notify_enabled() -> bool:
    raw = os.environ.get("METNOS_PROMOTER_NOTIFY_ADMIN", "true")
    return raw.lower() not in ("0", "false", "no")


def _aggregated_enabled() -> bool:
    """Override env per testing: METNOS_PROMOTER_DIGEST_AGGREGATED."""
    raw = os.environ.get("METNOS_PROMOTER_DIGEST_AGGREGATED", "true")
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


def _count_pending_decisions() -> int:
    """Conta TUTTE le decisioni in attesa (grace + review_needed + archived 7g).

    Usato dalla modalita' aggregata: se >= AGGREGATED_THRESHOLD, manda UN
    messaggio con bottone "Apri form review" invece di N messaggi per-item.
    """
    try:
        from .promoter_state import (
            archived_within_days, list_by_state, pending_notification,
        )
    except ImportError:
        return 0
    try:
        grace = len(pending_notification())
        review = len([r for r in list_by_state(["review_needed"], limit=500)
                      if (r.get("needs_human_review") or 0) == 1])
        archived = len(archived_within_days(7, limit=500))
    except Exception:  # noqa: BLE001
        return 0
    return grace + review + archived


def _format_aggregated_body(n_total: int, n_grace: int,
                             n_review: int, n_archived: int) -> str:
    """Body del messaggio aggregato che propone il form review.

    Stile §6 prescrittivo. Niente markdown headings (Telegram-friendly).
    """
    lines = [
        "*Promoter — decisioni in attesa*",
        "",
        f"Hai *{n_total}* decisioni da prendere:",
        f"  - {n_grace} in grazia",
        f"  - {n_review} da decidere",
        f"  - {n_archived} bocciati recenti",
        "",
        "Apri il form review per applicarle tutte in una sessione.",
    ]
    return "\n".join(lines)


def _build_aggregated_keyboard() -> list[list[dict]]:
    """Inline keyboard col solo bottone `Apri form review`.

    callback_data formato `promoter:_aggregated:open_form`. Il prefisso
    `_aggregated` (no proposal_id) viene gestito dal daemon channel come
    caso speciale: redirige al form HTTP via deep-link.
    """
    return [
        [
            {"text": "Apri form review",
             "data": "promoter:_aggregated:open_form"},
        ],
    ]


def _task_aggregated(grace_rows: list[dict], *,
                       n_grace: int, n_review: int,
                       n_archived: int, n_total: int) -> dict:
    """Esegue il flow `aggregated`: UN messaggio + bottone form.

    Tutte le row grace vengono marcate `notified_at` (anti-flood).
    """
    recipient, err = _resolve_admin_recipient()
    if recipient is None:
        for r in grace_rows:
            audit_append({
                "ts": _now_iso(),
                "proposal_id": r.get("proposal_id"),
                "action": "notify_skipped_aggregated",
                "reason": err,
            })
        return {
            "ok": True,
            "ok_count": 0,
            "error_count": n_total,
            "metadata": {
                "mode": "aggregated",
                "candidates_seen": n_total,
                "reason": err,
            },
        }

    body = _format_aggregated_body(n_total, n_grace, n_review, n_archived)
    keyboard = _build_aggregated_keyboard()
    sent_ok, send_err = _send_to_admin(recipient, body, keyboard)
    if not sent_ok:
        audit_append({
            "ts": _now_iso(),
            "action": "notify_failed_aggregated",
            "error": send_err,
        })
        return {
            "ok": True,
            "ok_count": 0,
            "error_count": 1,
            "metadata": {
                "mode": "aggregated",
                "candidates_seen": n_total,
                "recipient": recipient,
                "send_error": send_err,
            },
        }

    # Marca tutte le grace come notified (no re-flood al prossimo fire).
    for r in grace_rows:
        pid = r.get("proposal_id") or ""
        if pid:
            mark_notified(pid)
    audit_append({
        "ts": _now_iso(),
        "action": "notified_aggregated",
        "recipient": recipient,
        "n_total": n_total,
        "n_grace": n_grace,
        "n_review": n_review,
        "n_archived": n_archived,
    })
    return {
        "ok": True,
        "ok_count": n_total,
        "error_count": 0,
        "metadata": {
            "mode": "aggregated",
            "candidates_seen": n_total,
            "recipient": recipient,
            "n_grace": n_grace,
            "n_review": n_review,
            "n_archived": n_archived,
        },
    }


def task_promoter_digest(payload: dict | None = None) -> dict:
    """Callback scheduler v2 `promoter_digest` (daily@07:00).

    Payload ignorato. Ritorna shape RunResult.

    Modalita':
    - **aggregated** (default se >= AGGREGATED_THRESHOLD decisioni
      totali): UN messaggio con bottone "Apri form review" → redirect a
      `/admin/promotions/review` via callback. Marca tutte le row grace
      come `notified_at` (anti-flood al prossimo fire).
    - **per-item** (fallback): N messaggi (cap CAP_PER_FIRE) con
      keyboard ok/rollback per ogni promote in grace. Comportamento
      pre-E3, preservato per coverage del callback `promoter:<id>:ok`.
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

    # E3: prova modalita' aggregata se ci sono abbastanza decisioni.
    if _aggregated_enabled():
        n_grace = len(rows)
        try:
            from .promoter_state import (
                archived_within_days, list_by_state,
            )
            n_review = len([r for r in list_by_state(["review_needed"],
                                                       limit=500)
                            if (r.get("needs_human_review") or 0) == 1])
            n_archived = len(archived_within_days(7, limit=500))
        except Exception:  # noqa: BLE001
            n_review = 0
            n_archived = 0
        n_total = n_grace + n_review + n_archived
        if n_total >= AGGREGATED_THRESHOLD:
            return _task_aggregated(
                rows, n_grace=n_grace, n_review=n_review,
                n_archived=n_archived, n_total=n_total,
            )

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
