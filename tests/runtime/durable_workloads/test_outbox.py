"""F10 proofs for durable notification leases and the Telegram adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from durable_workloads.events import TelegramOutboxAdapter
from durable_workloads.models import EventType, OutboxState, WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan, source


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


class _Sender:
    def __init__(self, outcome):
        self.outcome = outcome
        self.messages = []

    def send(self, recipient, message):
        self.messages.append((recipient, message.text))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_telegram_adapter_retries_before_and_after_an_uncertain_send(store, monkeypatch):
    event = _event(store, number=3, event_type=EventType.COMPLETED)
    row = _enqueue(store, event)
    monkeypatch.setattr("durable_workloads.events.terminal_notice", lambda *_args, **_kwargs: "done")

    before = _Sender(RuntimeError("transport unavailable"))
    adapter = TelegramOutboxAdapter(
        store,
        before,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert adapter.deliver_once(now=NOW).deferred == 1
    pending = store._connection.execute(
        "SELECT state FROM outbox WHERE owner_user_id=? AND id=?", (OWNER, row.outbox_id),
    ).fetchone()[0]
    assert pending == "pending"

    # An exception after the provider call is still uncertain: preserve the
    # row rather than pretending delivery succeeded or deleting evidence.
    after = _Sender(RuntimeError("connection dropped after send"))
    adapter_after = TelegramOutboxAdapter(
        store,
        after,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert adapter_after.deliver_once(now=NOW).deferred == 1
    assert len(after.messages) == 1

    delivered = _Sender({"ok": True})
    adapter_delivered = TelegramOutboxAdapter(
        store,
        delivered,
        recipient_resolver=lambda _owner: "chat-a",
        language_resolver=lambda _owner: "en",
        retry_delay=timedelta(),
    )
    assert adapter_delivered.deliver_once(now=NOW).sent == 1
    assert len(delivered.messages) == 1


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
    assert (report.sent, report.deferred) == (0, 1)
    assert sender.messages == []
