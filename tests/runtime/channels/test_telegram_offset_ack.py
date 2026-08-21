from __future__ import annotations

import json
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from channels import InboundMessage, OutboundMessage
from channels.telegram import TelegramChannel


def _message(update_id: int) -> InboundMessage:
    return InboundMessage(
        channel="telegram", sender_id="42", text=f"m{update_id}",
        message_id=str(update_id), received_at=time.time(),
        extra={"update_id": update_id},
    )


def test_send_does_not_repeat_an_ambiguous_transport_call(tmp_path, monkeypatch):
    channel = TelegramChannel(
        token="test-token", default_chat_id="42",
        state_path=tmp_path / "offset",
    )
    calls = []

    def ambiguous(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "ok": False,
            "error": "connection dropped",
            "delivery_ambiguous": True,
        }

    monkeypatch.setattr(channel, "_call", ambiguous)

    outcome = channel.send("42", OutboundMessage(text="status"))

    assert outcome["delivery_ambiguous"] is True
    assert len(calls) == 1


def test_send_stops_and_marks_a_partial_multichunk_delivery_ambiguous(
    tmp_path,
    monkeypatch,
):
    channel = TelegramChannel(
        token="test-token", default_chat_id="42",
        state_path=tmp_path / "offset",
    )
    monkeypatch.setattr(
        "channels.telegram_format.format_for_telegram",
        lambda _text: ["first", "second", "must-not-run"],
    )
    outcomes = iter((
        {"ok": True, "delivery_ambiguous": False},
        {
            "ok": False,
            "retryable": True,
            "delivery_ambiguous": False,
        },
        {
            "ok": False,
            "retryable": True,
            "delivery_ambiguous": False,
        },
        {"ok": True, "delivery_ambiguous": False},
    ))
    calls = []

    def send_chunk(*args, **kwargs):
        calls.append((args, kwargs))
        return next(outcomes)

    monkeypatch.setattr(channel, "_call", send_chunk)

    outcome = channel.send("42", OutboundMessage(text="long notice"))

    assert outcome["ok"] is False
    assert outcome["delivery_ambiguous"] is True
    assert outcome["partial_delivery"] is True
    assert len(calls) == 3


def test_poll_persists_offset_only_after_ack(tmp_path, monkeypatch):
    state_path = tmp_path / "state" / "telegram_offset"
    channel = TelegramChannel(
        token="test-token", default_chat_id="42", state_path=state_path)
    response = {"ok": True, "result": [{
        "update_id": 17,
        "message": {"message_id": 3, "date": 1,
                    "chat": {"id": 42}, "text": "ciao"},
    }]}
    monkeypatch.setattr(channel, "_call", lambda *_a, **_kw: response)

    messages = channel.poll(timeout_s=0)

    assert [m.extra["update_id"] for m in messages] == [17]
    assert channel._last_update_id is None
    assert not state_path.exists()
    assert channel.ack(messages[0]) is True
    assert channel._last_update_id == 17
    assert state_path.read_text(encoding="utf-8") == "17"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_non_actionable_update_is_ackable(tmp_path, monkeypatch):
    channel = TelegramChannel(
        token="test-token", default_chat_id="42",
        state_path=tmp_path / "offset")
    monkeypatch.setattr(channel, "_call", lambda *_a, **_kw: {
        "ok": True, "result": [{"update_id": 9, "chat_member": {}}]})

    messages = channel.poll(timeout_s=0)

    assert len(messages) == 1
    assert messages[0].extra == {"kind": "transport_noop", "update_id": 9}
    assert channel.ack(messages[0]) is True


def test_daemon_stops_batch_before_ack_after_handler_exception(monkeypatch):
    from channels.daemon import ChannelDaemon

    class BatchChannel:
        name = "telegram"
        default_chat_id = "42"

        def __init__(self):
            self.acks = []
            self.polled = False

        def poll(self):
            if self.polled:
                return []
            self.polled = True
            return [_message(1), _message(2), _message(3)]

        def ack(self, message):
            self.acks.append(message.extra["update_id"])
            return True

    channel = BatchChannel()
    daemon = object.__new__(ChannelDaemon)
    daemon.channel = channel
    daemon.dry_run = True
    daemon.bootstrap_default_sender = False
    daemon._stop = False
    daemon._push_pending_notices = lambda: 0
    seen = []

    def handle(message):
        seen.append(message.extra["update_id"])
        if message.extra["update_id"] == 2:
            raise RuntimeError("boom")
        return {"ok": True}

    daemon.handle_message = handle
    monkeypatch.setattr("channels.daemon.time.sleep", lambda *_a: None)

    assert daemon.run_forever(max_iterations=1) == 1
    assert seen == [1, 2]
    assert channel.acks == [1]


def test_durable_telegram_cycle_is_inert_off_and_delivers_once_on(
    tmp_path,
    monkeypatch,
):
    """Exercise the production daemon gate and Telegram formatting without network I/O."""

    import config
    import pairing
    import users
    from channels.daemon import ChannelDaemon
    from durable_workloads.models import EventType
    from durable_workloads.storage import DurableWorkloadStore

    owner = "owner-f12-telegram"
    database = tmp_path / "durable" / "state.sqlite3"
    monkeypatch.setattr(config, "DB_DURABLE_WORKLOADS", database)
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            owner,
            "telegram-gate-turn",
            redacted_request={"summary": "fixture"},
        )
        with store._transaction() as connection:
            event = store.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=draft.workload_id,
                event_type=EventType.COMPLETED,
                payload={"version": draft.version},
            )
            store.enqueue_outbox_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=draft.workload_id,
                event_id=event.event_id,
                channel="telegram",
                recipient_key=owner,
            )

    monkeypatch.setattr(
        pairing,
        "list_pairings",
        lambda: [SimpleNamespace(channel="telegram", sender_id="42")],
    )
    monkeypatch.setattr(
        users,
        "find_user_by_recipient",
        lambda channel, sender: {"id": owner}
        if (channel, sender) == ("telegram", "42") else None,
    )
    monkeypatch.setattr(users, "get_pref", lambda *_args: "it")

    channel = TelegramChannel(
        token="test-token",
        default_chat_id="42",
        state_path=False,
    )
    sends = []

    def send_without_network(method, params, **_kwargs):
        sends.append((method, dict(params), datetime.now(timezone.utc)))
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(channel, "_call", send_without_network)
    daemon = object.__new__(ChannelDaemon)
    daemon.channel = channel

    monkeypatch.setenv("METNOS_DURABLE_WORKLOADS_ENABLED", "0")
    assert daemon._push_durable_workload_notices() == 0
    assert sends == []

    monkeypatch.setenv("METNOS_DURABLE_WORKLOADS_ENABLED", "1")
    assert daemon._push_durable_workload_notices() == 1
    assert len(sends) == 1
    assert sends[0][0] == "sendMessage"
    assert sends[0][1]["chat_id"] == "42"
    assert sends[0][1]["text"]

    assert daemon._push_durable_workload_notices() == 0
    assert len(sends) == 1
    with DurableWorkloadStore.open(database) as store:
        row = store._connection.execute(
            "SELECT state, attempt_count FROM outbox WHERE owner_user_id=?",
            (owner,),
        ).fetchone()
        assert tuple(row) == ("sent", 1)
