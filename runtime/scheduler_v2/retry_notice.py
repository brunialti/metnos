"""Owner-scoped notice when a recurring task enters retry cooldown."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def notify_retry_cooldown(entry, error: str | None) -> None:
    """Report repeated failures without changing the task's enabled state."""
    payload = getattr(entry, "payload", None) or {}
    owner_user_id = str(payload.get("owner_user_id") or "").strip()
    entry_name = str(getattr(entry, "name", "") or "")
    if (not owner_user_id
            or payload.get("scheduler_name") != entry_name
            or getattr(entry, "origin", "") != "user"):
        log.warning("ownerless/non-user retry notice rejected: %s", entry_name)
        return

    from user_lifecycle import OwnerUnavailable, owner_session
    try:
        with owner_session(owner_user_id):
            import users
            from recurring_tasks import (
                _live_telegram_recipient,
                get_user_task_by_scheduler_name,
            )

            owner = users.get_user(owner_user_id)
            if owner is None or str(owner.get("id") or "") != owner_user_id:
                return
            binding = users.get_channel(owner_user_id, "telegram")
            if not binding or not binding.get("verified_at"):
                return
            task = get_user_task_by_scheduler_name(owner_user_id, entry_name)
            if task is None:
                log.warning("retry notice registry mapping unavailable for %s", entry_name)
                return
            recipient = _live_telegram_recipient(owner_user_id)
            if not recipient:
                return

            from messages import get as msg
            from channels import OutboundMessage
            from channels.telegram import TelegramChannel
            from .daemon import _CIRCUIT_BREAK_AFTER

            label = payload.get("label") or payload.get("name") or entry_name
            detail = str(error)[:300] if error else msg("MSG_ERR_UNKNOWN")
            text = msg(
                "MSG_SCHED_RETRY_COOLDOWN", label=label,
                n=_CIRCUIT_BREAK_AFTER, error=detail,
            )
            task_id = int(task["id"])
            buttons = [[
                {"text": msg("MSG_BTN_SCHED_CONTINUE"),
                 "data": f"sched:cont:{task_id}"},
                {"text": msg("MSG_BTN_SCHED_SUSPEND"),
                 "data": f"sched:susp:{task_id}"},
                {"text": msg("MSG_BTN_SCHED_CANCEL"),
                 "data": f"sched:canc:{task_id}"},
            ]]
            response = TelegramChannel().send(
                recipient, OutboundMessage(text=text, buttons=buttons))
            if isinstance(response, dict) and not response.get("ok", True):
                raise RuntimeError(response.get("error") or "send returned ok:false")
            log.info("retry cooldown notified for task %s", entry_name)
    except OwnerUnavailable:
        log.info("retry notice skipped for deleted owner %s", owner_user_id)
    except Exception:
        log.exception("retry notice failed for task %s", entry_name)
