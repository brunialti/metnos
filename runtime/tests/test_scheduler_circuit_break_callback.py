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

_RUNTIME = str(Path(__file__).resolve().parents[1])
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


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
    calls = {"resume": [], "toggle": [], "cancel": [], "cancel_user": []}
    monkeypatch.setattr(sched_client, "resume_job",
                        lambda n: calls["resume"].append(n) or True)
    monkeypatch.setattr(sched_client, "toggle_job",
                        lambda n, e: calls["toggle"].append((n, e)) or True)
    monkeypatch.setattr(sched_client, "cancel_job",
                        lambda n: calls["cancel"].append(n) or True)
    monkeypatch.setattr(recurring_tasks, "cancel_user_task",
                        lambda n, **kw: calls["cancel_user"].append(n) or True)
    return calls


def test_continue_calls_resume_job(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:cont:user_gh_monitor"))
    assert res["ok"] is True
    assert res["callback"] == "sched_resume"
    assert _calls["resume"] == ["user_gh_monitor"]
    # i18n-robust: una reply NON vuota e' stata inviata (il testo esatto vive
    # nel DB i18n MSG_SCHED_RESUMED, lang-dipendente — non lo asseriamo).
    assert ch.sent and ch.sent[-1][1]


def test_suspend_calls_toggle_off(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:susp:user_gh_monitor"))
    assert res["ok"] is True
    assert res["callback"] == "sched_suspend"
    assert _calls["toggle"] == [("user_gh_monitor", False)]
    assert ch.sent and ch.sent[-1][1]


def test_cancel_calls_cancel_job_and_user_task(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:canc:user_gh_monitor"))
    assert res["ok"] is True
    assert res["callback"] == "sched_cancel"
    assert _calls["cancel"] == ["user_gh_monitor"]
    # record recurring_tasks pulito con la chiave SENZA prefisso user_
    assert _calls["cancel_user"] == ["gh_monitor"]
    assert ch.sent and ch.sent[-1][1]


def test_bad_callback_data_no_dispatch(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:onlytwo"))
    assert res["ok"] is False
    assert res["reason"] == "bad_callback_data"
    assert _calls["resume"] == [] and _calls["cancel"] == []


def test_unknown_action_no_dispatch(_calls):
    d, ch = _make_daemon()
    res = d.handle_message(_cb_msg("sched:frobnicate:user_x"))
    assert res["ok"] is False
    assert res["reason"] == "unknown_action"
