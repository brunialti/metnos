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

# What a pushed notice says for each visible event. A notice names the job and
# how it ended; an event that brings no news is not pushed at all.
_NOTICE_KEYS = {
    EventType.NEEDS_ATTENTION: "MSG_DURABLE_NOTICE_NEEDS_ATTENTION",
    EventType.FAILED: "MSG_DURABLE_NOTICE_FAILED",
    EventType.COMPLETED: "MSG_DURABLE_NOTICE_COMPLETED",
    # The same sentence, followed by how many items were lost and why.
    EventType.COMPLETED_WITH_ERRORS: "MSG_DURABLE_NOTICE_COMPLETED",
}
_STATE_EVENTS = {
    "needs_attention": EventType.NEEDS_ATTENTION,
    "failed": EventType.FAILED,
}
_MAX_QUOTED_REQUEST = 120
_MAX_CAUSES = 3


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
    """Return the signed instance language for workload delivery."""
    del owner_user_id
    import i18n
    return i18n.current_lang()


def job_origin(owner_user_id: str, workload_id: str) -> Mapping[str, Any] | None:
    """Who asked for the job in a conversation; None for a scheduled one."""
    import lre_answers

    return lre_answers.origin(workload_id, owner_user_id=owner_user_id)


def _facts(store: DurableWorkloadStore, owner_user_id: str, workload_id: str) -> dict:
    """The bounded, redacted facts the console also shows for one job."""
    from durable_runtime_registry import describe_plan

    description = store.descriptions_many(
        owner_user_id, (workload_id,), projector=describe_plan)[workload_id]
    summary = store.execution_summary(owner_user_id, workload_id)
    return {"description": description, "summary": summary}


def _label(code: object) -> str | None:
    """The console's own text for a closed error or cause code, if it has one."""
    import i18n
    from messages import get as message

    if not isinstance(code, str) or not code.replace("_", "").isalnum():
        return None
    for family in ("UI_DURABLE_CAUSE_", "UI_DURABLE_ERROR_"):
        key = family + code.upper()
        if i18n.key_exists(key):
            return message(key)
    return None


def _what(description: Mapping[str, Any], origin: Mapping[str, Any] | None) -> str:
    from messages import get as message

    request = " ".join(str((origin or {}).get("query") or "").split())
    if request:
        if len(request) > _MAX_QUOTED_REQUEST:
            request = request[:_MAX_QUOTED_REQUEST - 1].rstrip() + "…"
        return message("MSG_DURABLE_WHAT_REQUEST", query=request)
    if description.get("kind") == "image_indexing":
        return message("MSG_DURABLE_WHAT_PHOTO_INDEX")
    operation = description.get("operation")
    if isinstance(operation, str) and operation:
        return message("MSG_DURABLE_WHAT_TOOL", tool=operation)
    return message("MSG_DURABLE_WHAT_JOB")


def _cause_sentence(cause: str) -> str:
    from messages import get as message

    # A label may be a sentence of its own: the template ends it once.
    return message("MSG_DURABLE_NOTICE_CAUSE", cause=cause.rstrip(" ."))


def _lost_items(summary: Mapping[str, Any]) -> str:
    """How many items the job lost and why, in one line; empty when none."""
    from messages import get as message

    domain = summary.get("domain_errors") or {}
    count = int(domain.get("nitems") or 0)
    if count <= 0:
        return ""
    causes = [text for text in (
        _label(item.get("error_code"))
        for item in (domain.get("categories") or [])[:_MAX_CAUSES]
    ) if text]
    parts = [message("MSG_DURABLE_LOST_ITEMS", count=count)]
    if causes:
        parts.append(_cause_sentence(", ".join(dict.fromkeys(causes))))
    return " ".join(parts)


def _stop_cause(summary: Mapping[str, Any]) -> str:
    """Why the job stopped, in the console's words; empty when it has none."""
    for item in (summary.get("attempt_errors") or {}).get("categories") or []:
        text = _label(item.get("cause_code"))
        if text:
            return text
    for item in summary.get("error_categories") or []:
        text = _label(item.get("error_code"))
        if text:
            return text
    return ""


def _compose(event_type: EventType, facts: Mapping[str, Any],
             origin: Mapping[str, Any] | None, *, answer_follows: bool) -> str | None:
    from messages import get as message

    key = _NOTICE_KEYS.get(event_type)
    if key is None:
        return None
    what = _what(facts["description"], origin)
    summary = facts["summary"]
    parts = [message(key, what=what)]
    if event_type is EventType.COMPLETED_WITH_ERRORS:
        lost = _lost_items(summary)
        if lost:
            parts.append(lost)
    elif event_type is not EventType.COMPLETED:
        cause = _stop_cause(summary)
        if cause:
            parts.append(_cause_sentence(cause))
    if answer_follows:
        parts.append(message("MSG_DURABLE_NOTICE_ANSWER_IN_CHAT"))
    return " ".join(parts)


def silence_reason(store: DurableWorkloadStore, event: EventRecord,
                   origin: Mapping[str, Any] | None) -> str | None:
    """Why this event is not pushed, or None when it is news.

    Accepting a job is not news: who asked has just been told, and nobody
    asked for a scheduled one. A job that changed nothing is not news either.
    A job asked on Telegram is told about in that same chat by its answer.
    """
    if event.event_type is EventType.REVISION_ADMITTED:
        return "no_news"
    if event.event_type not in _NOTICE_KEYS:
        return None
    if (origin is not None and origin.get("channel") == "telegram"
            and origin.get("expects_answer")):
        return "told_in_conversation"
    if event.event_type is EventType.COMPLETED:
        progress = store.progress_many(
            event.owner_user_id, (event.workload_id,))[event.workload_id]
        if progress.get("completion_outcome") == "no_changes":
            return "no_news"
    return None


def terminal_notice(event: EventRecord, *, language: str | None = None,
                    store: DurableWorkloadStore | None = None,
                    origin: Mapping[str, Any] | None = None) -> str | None:
    """Render the notice of a visible workload event: which job, how it ended.

    Only closed facts enter the text: the person's own request (when a
    conversation asked for the job), counts and the console's labels.
    """
    del language
    if event.event_type not in _NOTICE_KEYS or store is None:
        return None
    import i18n

    with i18n.instance_language_context():
        return _compose(
            event.event_type,
            _facts(store, event.owner_user_id, event.workload_id), origin,
            answer_follows=bool(origin and origin.get("expects_answer"))
            and event.event_type in (
                EventType.COMPLETED, EventType.COMPLETED_WITH_ERRORS))


def lost_items_note(store: DurableWorkloadStore, owner_user_id: str,
                    workload_id: str) -> str:
    """What the answer of a job must add when some items were lost (§2.8)."""
    import i18n

    with i18n.instance_language_context():
        return _lost_items(store.execution_summary(owner_user_id, workload_id))


def stopped_notice(store: DurableWorkloadStore, owner_user_id: str,
                   workload_id: str, *, state: Any,
                   origin: Mapping[str, Any] | None) -> str:
    """The same words, for the conversation that asked for a job that stopped."""
    import i18n

    event_type = _STATE_EVENTS[getattr(state, "value", state)]
    with i18n.instance_language_context():
        return _compose(event_type, _facts(store, owner_user_id, workload_id),
                        origin, answer_follows=False) or ""


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
        origin_resolver: Callable[[str, str], Mapping[str, Any] | None] = job_origin,
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
        self._origin_resolver = origin_resolver
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
                origin = self._origin_resolver(record.owner_user_id, record.workload_id)
                silent = silence_reason(self._store, event, origin)
                if silent is not None:
                    count_cancel(record, silent)
                    continue
                text = terminal_notice(
                    event, language=self._language_resolver(record.owner_user_id),
                    store=self._store, origin=origin,
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
                    elif outcome.get("delivery_ambiguous") is False:
                        count_retry(record)
                    else:
                        # Retryability alone does not prove that the provider
                        # sent nothing. Missing/malformed delivery evidence
                        # must not duplicate an externally visible notice.
                        count_cancel(record, "delivery_ambiguous")
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
    "job_origin",
    "lost_items_note",
    "owner_language",
    "resolve_telegram_recipient",
    "silence_reason",
    "stopped_notice",
    "terminal_notice",
]
