"""Scheduler state is authoritative and circuit-breaks are never invisible."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace


def _legacy_task(*, enabled: int = 1) -> dict:
    return {
        "id": 17,
        "name": "daily_probe",
        "owner_user_id": "host-id",
        "scheduler_name": "user_host_daily_probe",
        "schedule": "daily@08:00",
        "query": "check the provider",
        "actor": "host",
        "channel": "http",
        "chat_id": None,
        "label": "daily probe",
        "enabled": enabled,
        "times": None,
        "fired_count": 3,
    }


def test_list_tasks_exposes_authoritative_suspension_and_last_error(monkeypatch):
    import recurring_tasks
    from scheduler_v2 import client as scheduler_client

    monkeypatch.setenv("METNOS_LANG", "it")
    monkeypatch.setattr(recurring_tasks, "list_user_tasks",
                        lambda owner_user_id=None: [_legacy_task(enabled=1)])
    monkeypatch.setattr(scheduler_client, "list_jobs", lambda: [{
        "name": "user_host_daily_probe",
        "enabled": False,
        "last_run_at": "2026-07-14T06:00:18+00:00",
        "last_status": "error",
        "last_error": "temporary DNS failure",
        "consecutive_failures": 3,
    }])

    result = recurring_tasks.handle_list_tasks(
        {}, actor="host", owner_user_id="host-id")

    task = result["tasks"][0]
    assert task["registry_enabled"] is True
    assert task["scheduler_enabled"] is False
    assert task["enabled"] is False
    assert task["last_error"] == "temporary DNS failure"
    assert "sospeso" in result["final_message_hint"].lower()
    assert "temporary DNS failure" in result["final_message_hint"]


def test_circuit_break_falls_back_to_verified_owner_telegram(monkeypatch):
    import recurring_tasks
    import users
    from channels import telegram as telegram_module

    sent = []
    mirror = []

    class FakeTelegram:
        default_chat_id = None

        def __init__(self):
            pass

        def send(self, recipient, message):
            sent.append((recipient, message))
            return {"ok": True}

    monkeypatch.setattr(recurring_tasks, "_set_user_task_enabled",
                        lambda name, enabled, **_kw:
                        mirror.append((name, enabled)) or True)
    monkeypatch.setattr(recurring_tasks, "get_user_task_by_scheduler_name",
                        lambda owner, name: (
                            {"id": 17} if (owner, name) ==
                            ("host-id", "user_host_daily_probe") else None))
    monkeypatch.setattr(users, "get_user",
                        lambda actor: {"id": "host-id", "name": "host"})
    import user_lifecycle
    monkeypatch.setattr(user_lifecycle, "owner_session",
                        lambda _owner: nullcontext())
    monkeypatch.setattr(users, "get_channel", lambda user_id, channel: {
        "recipient_id": "4242", "verified_at": "2026-01-01T00:00:00Z",
    })
    monkeypatch.setattr(recurring_tasks, "_live_telegram_recipient",
                        lambda owner_user_id: (
                            "4242" if owner_user_id == "host-id" else None))
    monkeypatch.setattr(telegram_module, "TelegramChannel", FakeTelegram)
    entry = SimpleNamespace(
        name="user_host_daily_probe", origin="user",
        payload={"actor": "host", "channel": "http", "chat_id": None,
                 "label": "daily probe", "owner_user_id": "host-id",
                 "scheduler_name": "user_host_daily_probe"},
    )

    recurring_tasks._notify_circuit_break(entry, "provider unavailable")

    assert mirror == [("user_host_daily_probe", False)]
    assert sent and sent[0][0] == "4242"
    assert "provider unavailable" in sent[0][1].text
