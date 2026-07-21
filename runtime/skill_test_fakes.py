"""Offline subprocess fakes used by generated provider-executor birth tests."""
from __future__ import annotations

import json


def empty(_argv, _env, _timeout_s):
    return 0, json.dumps({"ok": True, "entries": []}), ""


def success(_argv, _env, _timeout_s):
    return 0, json.dumps({"ok": True, "status": "done", "id": "test-1"}), ""


def auth_required(_argv, _env, _timeout_s):
    return 1, "", "NOT_AUTHENTICATED: offline contract fixture"
