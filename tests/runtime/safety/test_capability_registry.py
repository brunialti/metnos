from __future__ import annotations

import sys
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from capabilities import effective_capabilities  # noqa: E402
from policy import CAPABILITY_REGISTRY, is_allowed  # noqa: E402


def test_registry_contains_standardized_executor_capabilities() -> None:
    assert len(CAPABILITY_REGISTRY) == 29
    assert {
        "compute:pure", "fs:read", "index:read", "metnos:read", "system:read",
        "provider:access", "dialog.user_input", "network:sites",
        "auth.password_storage", "drive:permissions",
        "metnos:write", "metnos:create", "metnos:cache",
        "metnos:credentials_metadata_only",
        "system:undo", "system:admin", "mail:write",
    } <= set(
        CAPABILITY_REGISTRY
    )


def test_new_read_capabilities_follow_readonly_policy() -> None:
    assert is_allowed("ReadOnly", "compute:pure") == "allowed"
    assert is_allowed("ReadOnly", "index:read") == "approval_required"
    assert is_allowed("ReadOnly", "metnos:read") == "approval_required"
    assert is_allowed("ReadOnly", "metnos:cache") == "allowed"
    assert is_allowed("ReadOnly", "system:read") == "approval_required"
    assert is_allowed("ReadOnly", "provider:access") == "approval_required"
    assert is_allowed("ReadOnly", "dialog.user_input") == "allowed"
    assert is_allowed("ReadOnly", "network:sites") == "approval_required"
    assert is_allowed("ReadOnly", "auth.password_storage") == "denied"
    assert is_allowed("ReadOnly", "drive:permissions") == "denied"
    assert is_allowed("ReadOnly", "system:undo") == "denied"
    assert is_allowed("ReadOnly", "system:admin") == "denied"
    assert is_allowed("Full", "system:admin") == "approval_required"
    assert is_allowed("Supervised", "system:undo") == "allowed"
    assert is_allowed("ReadOnly", "mail:write") == "denied"
    assert is_allowed("Full", "index:read") == "allowed"


def test_unknown_or_legacy_spelling_never_grants_authority() -> None:
    assert is_allowed("Full", "index.read") == "denied"
    assert is_allowed("Full", "network") == "denied"


def test_effective_capabilities_use_explicit_value_then_schema_default() -> None:
    schema = {
        "properties": {
            "client": {
                "type": "string",
                "enum": ["local", "google_workspace"],
                "default": "google_workspace",
            },
        },
    }
    capability = {
        "name": "provider:access",
        "hint": ["google-workspace"],
        "when": {"arg": "client", "values": ["google_workspace"]},
    }

    assert effective_capabilities([capability], schema, {}) == [capability]
    assert effective_capabilities(
        [capability], schema, {"client": "local"},
    ) == []


def test_effective_capabilities_fail_closed_on_invalid_conditions() -> None:
    schema = {
        "properties": {
            "client": {"enum": ["local", "google_workspace"]},
        },
    }
    invalid_conditions = [
        {"arg": "client"},
        {"arg": "missing", "values": ["google_workspace"]},
        {"arg": "client", "values": []},
        {"arg": "client", "values": ["github"]},
        {"arg": "client", "values": ["google_workspace"], "extra": True},
    ]

    for condition in invalid_conditions:
        capability = {
            "name": "provider:access",
            "hint": ["google-workspace"],
            "when": condition,
        }
        assert effective_capabilities(
            [capability], schema, {"client": "google_workspace"},
        ) == []


def test_effective_capabilities_support_closed_nonempty_predicate() -> None:
    schema = {
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "default": []},
        },
    }
    capability = {
        "name": "provider:access",
        "hint": ["google-workspace"],
        "when": {"arg": "paths", "nonempty": True},
    }

    assert effective_capabilities([capability], schema, {}) == []
    assert effective_capabilities(
        [capability], schema, {"paths": ["/tmp/photo.jpg"]},
    ) == [capability]


def test_effective_capabilities_support_typed_array_item_predicate() -> None:
    schema = {
        "properties": {
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"in_reply_to": {"type": "string"}},
                },
            },
        },
    }
    capability = {
        "name": "provider:access",
        "hint": ["google-workspace"],
        "when": {"arg": "messages", "any_item_has": "in_reply_to"},
    }

    assert effective_capabilities(
        [capability], schema, {"messages": [{"body": "hello"}]},
    ) == []
    assert effective_capabilities(
        [capability], schema,
        {"messages": [{"in_reply_to": "gmail-id", "body": "hello"}]},
    ) == [capability]


def test_new_capability_predicates_fail_closed_on_schema_mismatch() -> None:
    schema = {
        "properties": {
            "paths": {"type": "integer"},
            "messages": {
                "type": "array",
                "items": {"type": "object", "properties": {}},
            },
        },
    }
    conditions = [
        {"arg": "paths", "nonempty": False},
        {"arg": "paths", "nonempty": True},
        {"arg": "messages", "any_item_has": "in_reply_to"},
        {"arg": "messages", "any_item_has": ""},
    ]
    for condition in conditions:
        capability = {
            "name": "provider:access",
            "hint": ["google-workspace"],
            "when": condition,
        }
        assert effective_capabilities(
            [capability], schema,
            {"paths": 1, "messages": [{"in_reply_to": "gmail-id"}]},
        ) == []
