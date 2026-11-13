"""Offline subprocess fakes used by generated provider-executor birth tests."""
from __future__ import annotations

import json


def empty(_argv, _env, _timeout_s):
    return 0, json.dumps({"ok": True, "entries": []}), ""


def success(_argv, _env, _timeout_s):
    return 0, json.dumps({"ok": True, "status": "done", "id": "test-1"}), ""


def auth_required(_argv, _env, _timeout_s):
    return 1, "", "NOT_AUTHENTICATED: offline contract fixture"


def contacts(_argv, _env, _timeout_s):
    """Stable People-API payload for offline contact birth tests."""
    return 0, json.dumps([{
        "name": "Test Stub",
        "emails": ["stub@example.com"],
        "phones": ["+390000000000"],
    }]), ""


def contacts_write(argv, _env, _timeout_s):
    """The address book of `contacts`, which also takes a new card: the
    arguments decide, as with the real script (`contacts list|create`)."""
    words = [str(word) for word in argv]
    if "create" not in words:
        return contacts(argv, _env, _timeout_s)

    def values(flag):
        return [words[index + 1] for index, word in enumerate(words[:-1]) if word == flag]

    name = (values("--name") or [""])[0]
    return 0, json.dumps({
        "status": "created", "resource_name": "people/c0000000000000000001",
        "name": name, "emails": values("--email"), "phones": values("--phone"),
    }), ""

