"""F10 proofs for durable notification leases and the Telegram adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from durable_workloads.events import TelegramOutboxAdapter
from durable_workloads.models import EventType, OutboxState, WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan


OWNER = "owner-outbox-a"
NOW = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    repository = DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3")
    try:
        yield repository
    finally:
        repository.close()


def _event(store: DurableWorkloadStore, *, number: int, event_type: EventType = EventType.QUEUED):
    draft = store.create_draft(
        OWNER,
        f"outbox-request-{number}",
        redacted_request={"summary": "synthetic"},
        workload_id=f"wrk_outbox_{number:08d}",
    )
    with store._transaction() as connection:
        return store.append_event_in_transaction(
            connection,
            owner_user_id=OWNER,
            workload_id=draft.workload_id,
            event_type=event_type,
            payload={"version": draft.version},
        )


def _enqueue(store: DurableWorkloadStore, event, *, coalesce: bool = False):
    with store._transaction() as connection:
        if coalesce:
            return store.enqueue_progress_outbox_in_transaction(
                connection,
                owner_user_id=OWNER,
                workload_id=event.workload_id,
                event_id=event.event_id,
                channel="telegram",
                recipient_key=OWNER,
                minimum_interval_s=60,
                now=NOW,
            )
        return store.enqueue_outbox_in_transaction(
            connection,
            owner_user_id=OWNER,
            workload_id=event.workload_id,
            event_id=event.event_id,
            channel="telegram",
            recipient_key=OWNER,
        )


def test_outbox_lease_fence_and_retry_keep_the_same_row(store):
    event = _event(store, number=1)
    row = _enqueue(store, event)

    first = store.claim_outbox(
        channel="telegram", worker_id="outbox-test-a", now=NOW,
    )
    assert len(first) == 1
    assert first[0].outbox_id == row.outbox_id
    assert first[0].state is OutboxState.LEASED

    assert store.release_outbox(
        first[0],
        worker_id="outbox-test-a",
        retry_at=NOW + timedelta(seconds=10),
        now=NOW,
    )
    assert not store.claim_outbox(
        channel="telegram", worker_id="outbox-test-b", now=NOW,
    )
    second = store.claim_outbox(
        channel="telegram",
        worker_id="outbox-test-b",
        now=NOW + timedelta(seconds=10),
    )
    assert len(second) == 1
    assert second[0].outbox_id == row.outbox_id
    assert second[0].fence > first[0].fence
    assert not store.confirm_outbox(first[0], worker_id="outbox-test-a", now=NOW)
    assert store.confirm_outbox(second[0], worker_id="outbox-test-b", now=NOW)
    stored = store._connection.execute(
        "SELECT state, ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert (stored["state"], stored["ack_json"]) == ("sent", '{"delivery":"sent"}')


def test_progress_coalesces_one_thousand_events_without_a_notification_storm(store):
    draft = store.create_draft(
        OWNER,
        "outbox-progress-request",
        redacted_request={"summary": "synthetic"},
        workload_id="wrk_outbox_progress",
    )
    with store._transaction() as connection:
        last = None
        for _ in range(1_000):
            last = store.append_event_in_transaction(
                connection,
                owner_user_id=OWNER,
                workload_id=draft.workload_id,
                event_type=EventType.QUEUED,
                payload={"version": draft.version},
            )
            row = store.enqueue_progress_outbox_in_transaction(
                connection,
                owner_user_id=OWNER,
                workload_id=draft.workload_id,
                event_id=last.event_id,
                channel="telegram",
                recipient_key=OWNER,
                minimum_interval_s=60,
                now=NOW,
            )
    assert last is not None
    count = store._connection.execute(
        """
        SELECT COUNT(*) FROM outbox
        WHERE owner_user_id=? AND workload_id=? AND coalesce_key='progress'
        """,
        (OWNER, draft.workload_id),
    ).fetchone()[0]
    assert count == 1
    assert row.event_id == last.event_id


def test_visible_state_events_enqueue_one_recoverable_notification(store):
    draft = store.create_draft(
        OWNER,
        "outbox-visible-events",
        redacted_request={"summary": "synthetic"},
        workload_id="wrk_outbox_visible",
    )
    store.admit_revision(
        OWNER,
        draft.workload_id,
        plan(),
        inventory(),
        expected_version=draft.version,
    )
    admitted = store.get_workload(OWNER, draft.workload_id)
    attention = store.transition_workload(
        OWNER,
        draft.workload_id,
        WorkloadState.NEEDS_ATTENTION,
        expected_version=admitted.version,
        payload={"reason": "fixture"},
    )
    store.transition_workload(
        OWNER,
        draft.workload_id,
        WorkloadState.FAILED,
        expected_version=attention.version,
        payload={"reason": "fixture"},
    )

    rows = store._connection.execute(
        """
        SELECT e.type, o.channel, o.state
        FROM events e
        JOIN outbox o
          ON o.owner_user_id=e.owner_user_id
         AND o.workload_id=e.workload_id
         AND o.event_id=e.event_id
        WHERE e.owner_user_id=? AND e.workload_id=? AND o.channel='telegram'
        ORDER BY e.event_id
        """,
        (OWNER, draft.workload_id),
    ).fetchall()
    assert [(row["type"], row["channel"], row["state"]) for row in rows] == [
        ("revision_admitted", "telegram", "pending"),
        ("needs_attention", "telegram", "pending"),
        ("failed", "telegram", "pending"),
    ]


class _Sender:
    def __init__(self, outcome):
        self.outcome = outcome
        self.messages = []

    def send(self, recipient, message):
        self.messages.append((recipient, message.text))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_telegram_adapter_never_retries_an_uncertain_send(store, monkeypatch):
    event = _event(store, number=3, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr("durable_workloads.events.terminal_notice", lambda *_args, **_kwargs: "done")

    uncertain = _Sender(RuntimeError("connection dropped after send"))
    adapter = TelegramOutboxAdapter(
        store,
        uncertain,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert adapter.deliver_once(now=NOW).cancelled == 1
    stored = store._connection.execute(
        "SELECT state, ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert stored["state"] == "cancelled"
    assert "delivery_ambiguous" in stored["ack_json"]

    replay = TelegramOutboxAdapter(
        store,
        _Sender({"ok": True}),
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert replay.deliver_once(now=NOW + timedelta(minutes=2)).claimed == 0
    assert len(uncertain.messages) == 1

    delivered_event = _event(store, number=30, event_type=EventType.COMPLETED)
    delivered_row = _enqueue(store, delivered_event)
    delivered = _Sender({
        "ok": True,
        "result": {
            "message_id": 4321,
            "chat": {"id": "must-not-enter-the-outbox"},
        },
    })
    adapter_delivered = TelegramOutboxAdapter(
        store,
        delivered,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert adapter_delivered.deliver_once(now=NOW).sent == 1
    assert len(delivered.messages) == 1
    receipt = store._connection.execute(
        "SELECT ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, delivered_row.outbox_id),
    ).fetchone()[0]
    assert receipt == '{"delivery":"sent","provider_message_id":4321}'
    assert "chat" not in receipt


def test_started_delivery_is_not_reclaimed_after_worker_crash(store):
    event = _event(store, number=31, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    leased = store.claim_outbox(
        channel="telegram", worker_id="outbox-crash-a", now=NOW,
        lease_duration=timedelta(seconds=10),
    )[0]
    assert store.mark_outbox_delivery_started(
        leased, worker_id="outbox-crash-a", now=NOW,
    )

    reclaimed = store.claim_outbox(
        channel="telegram", worker_id="outbox-crash-b",
        now=NOW + timedelta(seconds=11),
    )

    assert reclaimed == ()
    stored = store._connection.execute(
        "SELECT state, ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert stored["state"] == "cancelled"
    assert "delivery_ambiguous" in stored["ack_json"]


def test_explicit_retryable_rejection_can_be_sent_once_later(store, monkeypatch):
    event = _event(store, number=32, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr(
        "durable_workloads.events.terminal_notice",
        lambda *_args, **_kwargs: "done",
    )
    rejected = TelegramOutboxAdapter(
        store,
        _Sender({
            "ok": False,
            "retryable": True,
            "delivery_ambiguous": False,
        }),
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert rejected.deliver_once(now=NOW).deferred == 1
    pending = store._connection.execute(
        "SELECT state, ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert (pending["state"], pending["ack_json"]) == ("pending", None)

    sender = _Sender({"ok": True})
    delivered = TelegramOutboxAdapter(
        store,
        sender,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert delivered.deliver_once(now=NOW).sent == 1
    assert len(sender.messages) == 1


@pytest.mark.parametrize("outcome", [
    {"ok": False},
    {"ok": False, "retryable": True},
    {"ok": False, "retryable": True, "delivery_ambiguous": None},
    {"ok": False, "retryable": True, "delivery_ambiguous": "false"},
    {"ok": False, "retryable": True, "delivery_ambiguous": 0},
])
def test_retry_requires_explicit_evidence_that_nothing_was_sent(store, monkeypatch, outcome):
    event = _event(store, number=33, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr(
        "durable_workloads.events.terminal_notice", lambda *_args, **_kwargs: "done",
    )
    sender = _Sender(outcome)
    adapter = TelegramOutboxAdapter(
        store, sender, recipient_resolver=lambda _owner: "chat-a",
        retry_delay=timedelta(),
    )

    assert adapter.deliver_once(now=NOW).cancelled == 1
    assert adapter.deliver_once(now=NOW + timedelta(minutes=2)).claimed == 0
    assert len(sender.messages) == 1
    stored = store._connection.execute(
        "SELECT ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert "delivery_ambiguous" in stored["ack_json"]


def test_revoked_or_missing_telegram_association_never_sends(store, monkeypatch):
    event = _event(store, number=4, event_type=EventType.COMPLETED)
    _enqueue(store, event)
    monkeypatch.setattr("durable_workloads.events.terminal_notice", lambda *_args, **_kwargs: "done")
    sender = _Sender({"ok": True})
    adapter = TelegramOutboxAdapter(
        store,
        sender,
        recipient_resolver=lambda _owner: None,
        language_resolver=lambda _owner: "it",
        retry_delay=timedelta(),
    )
    report = adapter.deliver_once(now=NOW)
    assert (report.sent, report.deferred, report.cancelled) == (0, 0, 1)
    assert sender.messages == []
    assert store._connection.execute(
        "SELECT state FROM outbox WHERE owner_user_id=? AND channel='telegram'",
        (OWNER,),
    ).fetchone()[0] == "cancelled"


def test_telegram_adapter_distinguishes_authority_failure_from_revocation(
    store, monkeypatch,
):
    event = _event(store, number=5, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr(
        "durable_workloads.events.terminal_notice",
        lambda *_args, **_kwargs: "done",
    )

    def unavailable(_owner):
        raise RuntimeError("authority unavailable")

    adapter = TelegramOutboxAdapter(
        store,
        _Sender({"ok": True}),
        recipient_resolver=unavailable,
        language_resolver=lambda _owner: "it",
        retry_delay=timedelta(seconds=10),
    )
    report = adapter.deliver_once(now=NOW)
    assert (report.deferred, report.cancelled) == (1, 0)
    stored = store._connection.execute(
        "SELECT state, next_attempt_at FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert stored["state"] == "pending"
    assert stored["next_attempt_at"] == "2026-08-21T10:00:10.000000Z"


def test_telegram_adapter_retries_failures_before_delivery_boundary(
    store, monkeypatch,
):
    event = _event(store, number=51, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr(
        "durable_workloads.events.terminal_notice",
        lambda *_args, **_kwargs: "done",
    )
    original = store.get_event

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("temporary read failure")

    monkeypatch.setattr(store, "get_event", unavailable)
    adapter = TelegramOutboxAdapter(
        store, _Sender({"ok": True}),
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    report = adapter.deliver_once(now=NOW)
    assert (report.deferred, report.cancelled) == (1, 0)
    stored = store._connection.execute(
        "SELECT state, ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert tuple(stored) == ("pending", None)
    monkeypatch.setattr(store, "get_event", original)


def test_permanent_provider_rejection_is_not_retried_forever(store, monkeypatch):
    event = _event(store, number=6, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr(
        "durable_workloads.events.terminal_notice",
        lambda *_args, **_kwargs: "done",
    )
    adapter = TelegramOutboxAdapter(
        store,
        _Sender({"ok": False, "retryable": False}),
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "it",
    )
    report = adapter.deliver_once(now=NOW)
    assert (report.deferred, report.cancelled) == (0, 1)
    stored = store._connection.execute(
        "SELECT state, ack_json FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert stored["state"] == "cancelled"
    assert "provider_rejected" in stored["ack_json"]


def test_retryable_outbox_failure_has_a_hard_attempt_limit(store, monkeypatch):
    event = _event(store, number=60, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr(
        "durable_workloads.events.terminal_notice",
        lambda *_args, **_kwargs: "done",
    )
    sender = _Sender({
        "ok": False,
        "retryable": True,
        "delivery_ambiguous": False,
    })
    adapter = TelegramOutboxAdapter(
        store,
        sender,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "it",
        retry_delay=timedelta(),
        max_attempts=3,
    )

    reports = [adapter.deliver_once(now=NOW) for _attempt in range(3)]

    assert [(item.deferred, item.cancelled) for item in reports] == [
        (1, 0),
        (1, 0),
        (0, 1),
    ]
    assert len(sender.messages) == 3
    stored = store._connection.execute(
        "SELECT state, attempt_count, ack_json FROM outbox "
        "WHERE owner_user_id=? AND id=?",
        (OWNER, row.outbox_id),
    ).fetchone()
    assert (stored["state"], stored["attempt_count"]) == ("cancelled", 3)
    assert "retry_exhausted" in stored["ack_json"]
    assert adapter.deliver_once(now=NOW + timedelta(days=1)).claimed == 0


def test_outbox_retention_prunes_only_acknowledged_rows_in_bounded_batches(store):
    old = NOW - timedelta(days=31)
    sent_event = _event(store, number=7)
    sent = _enqueue(store, sent_event)
    leased = store.claim_outbox(
        channel="telegram", worker_id="retention-worker", now=old,
    )[0]
    assert store.confirm_outbox(
        leased, worker_id="retention-worker", now=old,
    )
    pending_event = _event(store, number=8)
    pending = _enqueue(store, pending_event)
    store._connection.execute(
        "UPDATE outbox SET updated_at=? WHERE owner_user_id=? AND id=?",
        ("2026-07-01T00:00:00.000000Z", OWNER, pending.outbox_id),
    )

    assert store.prune_outbox(limit=1, now=NOW) == 1
    assert store._connection.execute(
        "SELECT 1 FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, sent.outbox_id),
    ).fetchone() is None
    assert store._connection.execute(
        "SELECT state FROM outbox WHERE owner_user_id=? AND id=?",
        (OWNER, pending.outbox_id),
    ).fetchone()[0] == "pending"


def test_telegram_adapter_rejects_a_naive_delivery_time(store):
    adapter = TelegramOutboxAdapter(
        store,
        _Sender({"ok": True}),
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "it",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.deliver_once(now=datetime(2026, 8, 21, 10, 0))
