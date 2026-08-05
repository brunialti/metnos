"""Handler dei bottoni della notifica circuit-breaker scheduler nel
channels daemon: `sched:<azione>:<entry_name>` (cont|susp|canc).

Verifica il dispatch verso scheduler_v2.client (resume/toggle/cancel) + la
pulizia del record recurring_tasks su 'canc', con reply all'utente.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))


class _MockChannel:
    name = "telegram"
    default_chat_id = "1000"

    def __init__(self):
        self.sent = []

    def send(self, recipient, message):
        self.sent.append((recipient, message.text))
        return {"ok": True, "sent_message_id": "fake-1"}

    def poll(self):
        return []


def _make_daemon():
    from channels import daemon as _daemon
    ch = _MockChannel()
    d = _daemon.ChannelDaemon(
        ch,
        run_turn=lambda *a, **kw: None,
        dry_run=False,
        bootstrap_default_sender=False,
    )
    return d, ch


def _cb_msg(data: str, sender_id: str = "1000"):
    from channels import InboundMessage
    return InboundMessage(
        channel="telegram",
        sender_id=sender_id,
        text=data,
        message_id="m-1",
        received_at=time.time(),
        extra={"kind": "callback", "callback_id": "cbq-1"},
    )


@pytest.fixture
def _calls(monkeypatch):
    """Sostituisce le funzioni del client scheduler con spie."""
    from scheduler_v2 import client as sched_client
    import recurring_tasks
    import pairing
    import users
    principal = pairing.Pairing(
        channel="telegram", sender_id="1000", autonomy_level="Full",
        paired_at="2026-01-01T00:00:00Z", paired_by="test", actor="host")
    monkeypatch.setattr(pairing, "get_pairing", lambda *_a: principal)
    monkeypatch.setattr(pairing, "touch_last_seen", lambda *_a: None)
    monkeypatch.setattr(
        users, "find_user_by_recipient",
        lambda *_a: {"id": "host-id", "name": "host", "role": "host"})
    calls = {"set_enabled": [], "purge": [], "cancel_user": []}
    monkeypatch.setattr(
        recurring_tasks, "get_user_task_by_id",
        lambda owner, task_id: (
            {"id": 17, "scheduler_name": "user_gh_monitor"}
            if owner == "host-id" and int(task_id) == 17 else None),
    )
    monkeypatch.setattr(
        sched_client, "list_jobs_readonly",
        lambda _names: [{
            "name": "user_gh_monitor", "origin": "user",
            "payload": {"owner_user_id": "host-id",
                        "scheduler_name": "user_gh_monitor"},
        }],
    )
    monkeypatch.setattr(
        recurring_tasks, "set_user_scheduler_enabled",
        lambda n, enabled, **_kw: calls["set_enabled"].append((n, enabled)) or True,
    )
    monkeypatch.setattr(
        sched_client, "purge_jobs",
        lambda names: calls["purge"].append(tuple(names)) or {"entries": 1},
    )
    monkeypatch.setattr(recurring_tasks, "cancel_user_task",
                        lambda n, **kw: calls["cancel_user"].append(n) or True)
    return calls


def test_continue_calls_resume_job(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:cont:17"))
    assert res["ok"] is True
    assert res["callback"] == "sched_resume"
    assert _calls["set_enabled"] == [("user_gh_monitor", True)]
    # i18n-robust: una reply NON vuota e' stata inviata (il testo esatto vive
    # nel DB i18n MSG_SCHED_RESUMED, lang-dipendente — non lo asseriamo).
    assert ch.sent and ch.sent[-1][1]


def test_suspend_calls_toggle_off(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:susp:17"))
    assert res["ok"] is True
    assert res["callback"] == "sched_suspend"
    assert _calls["set_enabled"] == [("user_gh_monitor", False)]
    assert ch.sent and ch.sent[-1][1]


def test_cancel_calls_cancel_job_and_user_task(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:canc:17"))
    assert res["ok"] is True
    assert res["callback"] == "sched_cancel"
    assert _calls["purge"] == [("user_gh_monitor",)]
    assert _calls["cancel_user"] == [17]
    assert ch.sent and ch.sent[-1][1]


def test_bad_callback_data_no_dispatch(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:onlytwo"))
    assert res["ok"] is False
    assert res["reason"] == "bad_callback_data"
    assert _calls["set_enabled"] == [] and _calls["cancel_user"] == []


def test_unknown_action_no_dispatch(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:frobnicate:17"))
    assert res["ok"] is False
    assert res["reason"] == "unknown_action"


def test_unpaired_callback_never_reaches_scheduler(_calls, monkeypatch):
    import pairing
    import users
    monkeypatch.setattr(pairing, "get_pairing", lambda *_a: None)
    monkeypatch.setattr(users, "find_user_by_recipient", lambda *_a: None)
    d, _ch = _make_daemon()

    result = d.handle_message(_cb_msg("sched:cont:17", "9999"))

    assert result["reason"] == "sender_not_paired"
    assert _calls["set_enabled"] == []


def test_guest_callback_cannot_mutate_scheduler(_calls, monkeypatch):
    import pairing
    import users
    guest = pairing.Pairing(
        channel="telegram", sender_id="2000", autonomy_level="Supervised",
        paired_at="2026-01-01T00:00:00Z", paired_by="test", actor="guest")
    monkeypatch.setattr(pairing, "get_pairing", lambda *_a: guest)
    monkeypatch.setattr(
        users, "find_user_by_recipient",
        lambda *_a: {"id": "guest-id", "name": "guest", "role": "guest"})
    d, _ch = _make_daemon()

    result = d.handle_message(_cb_msg("sched:cont:17", "2000"))

    assert result["reason"] == "scheduler_not_owned"
    assert _calls["set_enabled"] == []
