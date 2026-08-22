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

from .coordinator import normalize_instant
from .models import EventRecord, EventType, OutboxRecord
from .storage import DurableWorkloadStore


log = logging.getLogger("metnos.durable_workloads.events")

_TELEGRAM_MESSAGE_KEYS = {
    EventType.REVISION_ADMITTED: "MSG_DURABLE_WORKLOAD_ADMITTED",
    EventType.NEEDS_ATTENTION: "MSG_DURABLE_WORKLOAD_NEEDS_ATTENTION",
    EventType.FAILED: "MSG_DURABLE_WORKLOAD_FAILED",
    EventType.COMPLETED: "MSG_DURABLE_WORKLOAD_COMPLETED",
    EventType.COMPLETED_WITH_ERRORS: "MSG_DURABLE_WORKLOAD_COMPLETED_WITH_ERRORS",
}


class TelegramSender(Protocol):
    """Narrow channel boundary used by the durable outbox adapter."""

    def send(self, recipient: str, message: Any) -> Mapping[str, Any]:
        """Deliver text and report whether a failed call is unambiguously unsent."""


RecipientResolver = Callable[[str], str | None]
LanguageResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    claimed: int = 0
    sent: int = 0
    deferred: int = 0
    cancelled: int = 0


class RecipientResolutionError(RuntimeError):
    """The current association authority could not be read reliably."""


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
    except Exception as exc:
        raise RecipientResolutionError(
            "telegram association authority is unavailable"
        ) from exc
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

    message_key = _TELEGRAM_MESSAGE_KEYS.get(event.event_type)
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
        max_retry_delay: timedelta = timedelta(hours=24),
        max_attempts: int = 12,
    ) -> None:
        if not isinstance(store, DurableWorkloadStore):
            raise TypeError("store must be DurableWorkloadStore")
        if not callable(getattr(sender, "send", None)):
            raise TypeError("sender must expose send(recipient, message)")
        if not callable(recipient_resolver) or not callable(language_resolver):
            raise TypeError("outbox resolvers must be callable")
        if not isinstance(retry_delay, timedelta) or not timedelta() <= retry_delay <= timedelta(hours=24):
            raise ValueError("retry_delay must be between zero and 24 hours")
        if (
            not isinstance(max_retry_delay, timedelta)
            or not retry_delay <= max_retry_delay <= timedelta(days=7)
        ):
            raise ValueError(
                "max_retry_delay must be between retry_delay and seven days"
            )
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 100
        ):
            raise ValueError("max_attempts must be an integer in 1..100")
        self._store = store
        self._sender = sender
        self._worker_id = worker_id
        self._recipient_resolver = recipient_resolver
        self._language_resolver = language_resolver
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._max_attempts = max_attempts

    @staticmethod
    def _outbound_message(text: str) -> Any:
        # The shared channel DTO keeps this adapter usable by the ordinary
        # Telegram daemon while avoiding a Telegram-specific object in storage.
        from channels import OutboundMessage

        return OutboundMessage(text=text)

    @staticmethod
    def _delivery_ack(outcome: Mapping[str, Any]) -> dict[str, Any]:
        """Keep only the non-sensitive Telegram receipt needed for audit."""

        acknowledgement: dict[str, Any] = {"delivery": "sent"}
        result = outcome.get("result")
        raw_message_id = (
            result.get("message_id")
            if isinstance(result, Mapping)
            else outcome.get("message_id")
        )
        if (
            isinstance(raw_message_id, int)
            and not isinstance(raw_message_id, bool)
            and raw_message_id >= 0
        ):
            acknowledgement["provider_message_id"] = raw_message_id
        return acknowledgement

    def _retry_or_cancel(self, record: OutboxRecord, *, now: datetime) -> str:
        """Bound a definitely-unsent retry loop and report the actual CAS."""

        if record.attempt_count >= self._max_attempts:
            cancelled = self._store.cancel_outbox(
                record,
                worker_id=self._worker_id,
                reason_code="retry_exhausted",
                now=now,
            )
            return "cancelled" if cancelled else "deferred"
        exponent = min(max(record.attempt_count - 1, 0), 30)
        delay_seconds = min(
            self._retry_delay.total_seconds() * (2 ** exponent),
            self._max_retry_delay.total_seconds(),
        )
        self._store.release_outbox(
            record,
            worker_id=self._worker_id,
            retry_at=now + timedelta(seconds=delay_seconds),
            now=now,
        )
        return "deferred"

    def _cancel(
        self,
        record: OutboxRecord,
        *,
        reason_code: str,
        now: datetime,
    ) -> bool:
        return self._store.cancel_outbox(
            record,
            worker_id=self._worker_id,
            reason_code=reason_code,
            now=now,
        )

    def deliver_once(
        self,
        *,
        limit: int = 50,
        now: datetime | None = None,
    ) -> DeliveryReport:
        """Deliver a bounded batch and never retry an ambiguous provider effect."""

        current = normalize_instant(
            now or datetime.now(timezone.utc),
            name="now",
        )
        records = self._store.claim_outbox(
            channel="telegram",
            worker_id=self._worker_id,
            limit=limit,
            now=current,
        )
        sent = 0
        deferred = 0
        cancelled = 0

        def count_cancel(record: OutboxRecord, reason_code: str) -> None:
            nonlocal cancelled, deferred
            if self._cancel(record, reason_code=reason_code, now=current):
                cancelled += 1
            else:
                deferred += 1

        def count_retry(record: OutboxRecord) -> None:
            nonlocal cancelled, deferred
            disposition = self._retry_or_cancel(record, now=current)
            if disposition == "cancelled":
                cancelled += 1
            else:
                deferred += 1

        for record in records:
            delivery_started = False
            try:
                recipient = self._recipient_resolver(record.owner_user_id)
            except Exception:
                log.warning("durable_outbox_telegram_association_unavailable")
                count_retry(record)
                continue
            if not recipient:
                count_cancel(record, "recipient_unavailable")
                continue
            try:
                event = self._store.get_event(
                    record.owner_user_id, record.workload_id, record.event_id,
                )
                text = terminal_notice(
                    event, language=self._language_resolver(record.owner_user_id),
                )
                if text is None:
                    count_cancel(record, "event_not_supported")
                    continue
                outbound = self._outbound_message(text)
                if not self._store.mark_outbox_delivery_started(
                    record,
                    worker_id=self._worker_id,
                    now=current,
                ):
                    deferred += 1
                    continue
                delivery_started = True
                try:
                    outcome = self._sender.send(recipient, outbound)
                except Exception:
                    count_cancel(record, "delivery_ambiguous")
                    continue
                if not isinstance(outcome, Mapping):
                    count_cancel(record, "delivery_ambiguous")
                    continue
                if outcome.get("ok") is not True:
                    if outcome.get("delivery_ambiguous") is True:
                        count_cancel(record, "delivery_ambiguous")
                    elif outcome.get("retryable") is False:
                        count_cancel(record, "provider_rejected")
                    else:
                        count_retry(record)
                    continue
                if self._store.confirm_outbox(
                    record,
                    worker_id=self._worker_id,
                    acknowledgement=self._delivery_ack(outcome),
                    now=current,
                ):
                    sent += 1
                else:
                    # A lost lease is intentionally not retried by this worker:
                    # the current fence owner owns the next decision.
                    deferred += 1
            except Exception:
                log.warning("durable_outbox_telegram_delivery_failed")
                if delivery_started:
                    count_cancel(record, "delivery_ambiguous")
                else:
                    count_retry(record)
        return DeliveryReport(
            claimed=len(records),
            sent=sent,
            deferred=deferred,
            cancelled=cancelled,
        )


__all__ = [
    "DeliveryReport",
    "RecipientResolutionError",
    "TelegramOutboxAdapter",
    "owner_language",
    "resolve_telegram_recipient",
    "terminal_notice",
]
