"""Durable notification delivery for workload events.

The SQLite outbox is the source of truth.  This module resolves a Telegram
association only while a row is leased, sends only a short localized workload
notice, and confirms the row afterwards.  It never accepts browser paths,
artifact bytes or request text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .models import EventRecord, EventType, OutboxRecord
from .storage import DurableWorkloadStore


log = logging.getLogger("metnos.durable_workloads.events")


class TelegramSender(Protocol):
    """Narrow channel boundary used by the durable outbox adapter."""

    def send(self, recipient: str, message: Any) -> Mapping[str, Any]:
        """Deliver one text-only message to the resolved provider address."""


RecipientResolver = Callable[[str], str | None]
LanguageResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    claimed: int = 0
    sent: int = 0
    deferred: int = 0


def resolve_telegram_recipient(owner_user_id: str) -> str | None:
    """Return a currently paired, verified Telegram recipient for one owner.

    Pairings and user-channel verification are independent records.  Both are
    checked at send time so a revoked or rebound chat can never receive an old
    workload notification.
    """

    try:
        import pairing
        import users

        for binding in pairing.list_pairings():
            if binding.channel != "telegram" or not binding.sender_id:
                continue
            current = users.find_user_by_recipient("telegram", binding.sender_id)
            if current is not None and str(current.get("id") or "") == owner_user_id:
                return str(binding.sender_id)
    except Exception:
        log.warning("durable_outbox_telegram_association_unavailable")
    return None


def owner_language(owner_user_id: str) -> str | None:
    """Read an optional user preference only at the delivery boundary."""

    try:
        import users

        return users.get_pref(owner_user_id, "lang", None)
    except Exception:
        return None


def terminal_notice(event: EventRecord, *, language: str | None = None) -> str | None:
    """Render a closed, non-sensitive message for a visible workload event.

    The name is retained as the adapter's stable boundary while the initial
    contract also covers admission and a request for owner attention.
    """

    message_keys = {
        EventType.REVISION_ADMITTED: "MSG_DURABLE_WORKLOAD_ADMITTED",
        EventType.NEEDS_ATTENTION: "MSG_DURABLE_WORKLOAD_NEEDS_ATTENTION",
        EventType.FAILED: "MSG_DURABLE_WORKLOAD_FAILED",
        EventType.COMPLETED: "MSG_DURABLE_WORKLOAD_COMPLETED",
        EventType.COMPLETED_WITH_ERRORS: "MSG_DURABLE_WORKLOAD_COMPLETED_WITH_ERRORS",
    }
    message_key = message_keys.get(event.event_type)
    if message_key is None:
        return None
    import i18n
    from messages import get as message

    with i18n.language_context(language):
        return message(message_key)


class TelegramOutboxAdapter:
    """Lease and deliver durable Telegram notices without provider state in SQL."""

    def __init__(
        self,
        store: DurableWorkloadStore,
        sender: TelegramSender,
        *,
        worker_id: str = "durable-telegram-outbox",
        recipient_resolver: RecipientResolver = resolve_telegram_recipient,
        language_resolver: LanguageResolver = owner_language,
        retry_delay: timedelta = timedelta(seconds=30),
    ) -> None:
        if not isinstance(store, DurableWorkloadStore):
            raise TypeError("store must be DurableWorkloadStore")
        if not callable(getattr(sender, "send", None)):
            raise TypeError("sender must expose send(recipient, message)")
        if not callable(recipient_resolver) or not callable(language_resolver):
            raise TypeError("outbox resolvers must be callable")
        if not isinstance(retry_delay, timedelta) or not timedelta() <= retry_delay <= timedelta(hours=24):
            raise ValueError("retry_delay must be between zero and 24 hours")
        self._store = store
        self._sender = sender
        self._worker_id = worker_id
        self._recipient_resolver = recipient_resolver
        self._language_resolver = language_resolver
        self._retry_delay = retry_delay

    @staticmethod
    def _outbound_message(text: str) -> Any:
        # The shared channel DTO keeps this adapter usable by the ordinary
        # Telegram daemon while avoiding a Telegram-specific object in storage.
        from channels import OutboundMessage

        return OutboundMessage(text=text)

    def _defer(self, record: OutboxRecord, *, now: datetime) -> None:
        self._store.release_outbox(
            record,
            worker_id=self._worker_id,
            retry_at=now + self._retry_delay,
            now=now,
        )

    def deliver_once(
        self,
        *,
        limit: int = 50,
        now: datetime | None = None,
    ) -> DeliveryReport:
        """Deliver a bounded batch; every failed row remains retryable."""

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        records = self._store.claim_outbox(
            channel="telegram",
            worker_id=self._worker_id,
            limit=limit,
            now=current,
        )
        sent = 0
        deferred = 0
        for record in records:
            recipient = self._recipient_resolver(record.owner_user_id)
            if not recipient:
                self._defer(record, now=current)
                deferred += 1
                continue
            try:
                event = self._store.get_event(
                    record.owner_user_id, record.workload_id, record.event_id,
                )
                text = terminal_notice(
                    event, language=self._language_resolver(record.owner_user_id),
                )
                if text is None:
                    # The row is valid but has no text-only Telegram contract.
                    # Keep it for a later compatible adapter rather than leak a
                    # payload or silently discard an event.
                    self._defer(record, now=current)
                    deferred += 1
                    continue
                outcome = self._sender.send(recipient, self._outbound_message(text))
                if not isinstance(outcome, Mapping) or outcome.get("ok") is not True:
                    self._defer(record, now=current)
                    deferred += 1
                    continue
                if self._store.confirm_outbox(
                    record,
                    worker_id=self._worker_id,
                    acknowledgement={"delivery": "sent"},
                    now=current,
                ):
                    sent += 1
                else:
                    # A lost lease is intentionally not retried by this worker:
                    # the current fence owner owns the next decision.
                    deferred += 1
            except Exception:
                log.warning("durable_outbox_telegram_delivery_failed")
                self._defer(record, now=current)
                deferred += 1
        return DeliveryReport(claimed=len(records), sent=sent, deferred=deferred)


__all__ = [
    "DeliveryReport",
    "TelegramOutboxAdapter",
    "owner_language",
    "resolve_telegram_recipient",
    "terminal_notice",
]
