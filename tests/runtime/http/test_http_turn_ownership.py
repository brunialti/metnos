"""Pure authorization tests for HTTP surfaces addressed by turn id."""

from __future__ import annotations

import active_sessions
import http_routes_agent as routes


def test_explicit_owner_is_authoritative() -> None:
    record = {
        "owner_user_id": "user-a",
        "actor": "shared-legacy-actor",
        "conversation_id": "shared-legacy-conversation",
    }

    assert routes._turn_record_belongs_to_user(
        record, user_id="user-a", actor="device-a",
    )
    assert not routes._turn_record_belongs_to_user(
        record, user_id="user-b", actor="shared-legacy-actor",
    )


def test_legacy_conversation_uses_registry_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        active_sessions,
        "get_conversation",
        lambda _conversation_id: {
            "conversation_id": "conv-a",
            "user_id": "user-a",
            "channel": "http",
        },
    )
    record = {"conversation_id": "conv-a", "actor": "shared-actor"}

    assert routes._turn_record_belongs_to_user(
        record, user_id="user-a", actor="device-a",
    )
    assert not routes._turn_record_belongs_to_user(
        record, user_id="user-b", actor="shared-actor",
    )


def test_identity_failure_does_not_fall_back_to_actor(monkeypatch) -> None:
    def unavailable(_conversation_id):
        raise OSError("identity store unavailable")

    monkeypatch.setattr(active_sessions, "get_conversation", unavailable)

    assert not routes._turn_record_belongs_to_user(
        {"conversation_id": "conv-a", "actor": "device-a"},
        user_id="user-a",
        actor="device-a",
    )


def test_actor_fallback_is_only_for_unbound_legacy_records(monkeypatch) -> None:
    monkeypatch.setattr(
        active_sessions, "get_conversation", lambda _conversation_id: None,
    )

    assert routes._turn_record_belongs_to_user(
        {"actor": "device-a"}, user_id="user-a", actor="device-a",
    )
    assert not routes._turn_record_belongs_to_user(
        {"actor": "device-a"}, user_id="user-b", actor="device-b",
    )
