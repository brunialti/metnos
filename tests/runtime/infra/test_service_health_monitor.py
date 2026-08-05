"""Administrator service alerts are localized and transition-based."""
from __future__ import annotations

import pytest

import notify_admin
import service_health_monitor as monitor


def _row(status: str, *, desired_state: str = "running") -> dict:
    return {
        "key": "http",
        "label": "Server HTTP",
        "label_en": "HTTP server",
        "status": status,
        "desired_state": desired_state,
        "unit": "metnos-http.service",
        "scope": "system",
    }


def _capture(events: list[dict]):
    def notifier(body: str, **kwargs) -> dict:
        event = {"body": body, **kwargs, "id": f"event-{len(events) + 1}"}
        events.append(event)
        return event

    return notifier


@pytest.mark.parametrize(
    ("lang", "title", "service", "status"),
    (
        ("it", "Servizio Metnos non disponibile", "Server HTTP", "Errore"),
        ("en", "Metnos service unavailable", "HTTP server", "Failed"),
    ),
)
def test_down_alert_is_complete_and_localized(
        monkeypatch, lang, title, service, status):
    events: list[dict] = []
    monkeypatch.setattr(notify_admin, "admin_language", lambda: lang)

    monitor._notification(
        _row("failed"), transition="down", incident_id="http:1",
        notifier=_capture(events),
    )

    assert len(events) == 1
    assert events[0]["title"] == title
    assert service in events[0]["body"]
    assert status in events[0]["body"]
    assert "<missing:" not in events[0]["title"] + events[0]["body"]


def test_down_then_recovery_notifies_once_per_transition(tmp_path, monkeypatch):
    events: list[dict] = []
    state_path = tmp_path / "service-health.json"
    monkeypatch.setattr(notify_admin, "admin_language", lambda: "it")
    notifier = _capture(events)

    down = monitor.run(
        rows=[_row("failed")], notifier=notifier,
        state_path=state_path, now=10,
    )
    duplicate = monitor.run(
        rows=[_row("failed")], notifier=notifier,
        state_path=state_path, now=20,
    )
    recovered = monitor.run(
        rows=[_row("running")], notifier=notifier,
        state_path=state_path, now=30,
    )

    assert [item["transition"] for item in down["notifications"]] == ["down"]
    assert duplicate["notifications"] == []
    assert [item["transition"] for item in recovered["notifications"]] == [
        "recovered",
    ]
    assert [event["title"] for event in events] == [
        "Servizio Metnos non disponibile",
        "Servizio Metnos ripristinato",
    ]
    assert "di nuovo operativo" in events[1]["body"]
    assert all("<missing:" not in event["title"] + event["body"]
               for event in events)


def test_intentional_stop_resolves_open_incident(tmp_path, monkeypatch):
    events: list[dict] = []
    state_path = tmp_path / "service-health.json"
    monkeypatch.setattr(notify_admin, "admin_language", lambda: "it")
    notifier = _capture(events)

    monitor.run(
        rows=[_row("failed")], notifier=notifier,
        state_path=state_path, now=10,
    )
    result = monitor.run(
        rows=[_row("stopped", desired_state="stopped")], notifier=notifier,
        state_path=state_path, now=20,
    )

    assert [item["transition"] for item in result["notifications"]] == [
        "stopped",
    ]
    assert events[-1]["title"] == "Arresto del servizio registrato"
    assert "arrestato intenzionalmente" in events[-1]["body"]
    assert "<missing:" not in events[-1]["title"] + events[-1]["body"]
