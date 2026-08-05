from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from channels import InboundMessage
from channels.telegram import TelegramChannel


def _message(update_id: int) -> InboundMessage:
    return InboundMessage(
        channel="telegram", sender_id="42", text=f"m{update_id}",
        message_id=str(update_id), received_at=time.time(),
        extra={"update_id": update_id},
    )


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

