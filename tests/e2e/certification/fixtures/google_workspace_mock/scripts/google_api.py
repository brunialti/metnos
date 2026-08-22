#!/usr/bin/env python3
"""Deterministic Google Workspace boundary used only by RM-0006 E2E.

The production backends still execute their normal subprocess boundary.  This
fixture replaces the remote provider, records calendar state under the
isolated skill home, and never contacts Google.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


STATE_ROOT = Path(os.environ.get("METNOS_SKILL_HOME", "/tmp"))
CALENDAR_STATE = STATE_ROOT / "rm0006-calendar.json"


def _value(argv: list[str], flag: str, default: str = "") -> str:
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return default


def _read_events() -> dict[str, dict]:
    try:
        value = json.loads(CALENDAR_STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_events(events: dict[str, dict]) -> None:
    CALENDAR_STATE.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_STATE.write_text(
        json.dumps(events, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if argv[:2] == ["contacts", "list"]:
        print(json.dumps([
            {"name": "Ada Cert", "emails": ["ada@cert.invalid"], "phones": []},
            {"name": "Bruno Cert", "emails": [], "phones": ["+39000000001"]},
            {"name": "Cora Cert", "emails": ["cora@cert.invalid"], "phones": []},
        ]))
        return 0

    if argv[:2] == ["calendar", "create"]:
        event_id = "rm0006-event-001"
        events = _read_events()
        record = {
            "id": event_id,
            "summary": _value(argv, "--summary", "METNOS-CERT-EVENT"),
            "start": _value(argv, "--start"),
            "end": _value(argv, "--end"),
            "calendar": _value(argv, "--calendar", "primary"),
            "htmlLink": "https://calendar.invalid/rm0006-event-001",
        }
        events[event_id] = record
        _write_events(events)
        print(json.dumps(record))
        return 0

    if argv[:2] == ["calendar", "delete"] and len(argv) >= 3:
        events = _read_events()
        events.pop(argv[2], None)
        _write_events(events)
        print("{}")
        return 0

    if argv[:2] == ["gmail", "search"]:
        sys.stderr.write(
            "credential revoked for revoked_cert; reconnect the test account\n"
        )
        return 23

    sys.stderr.write("unsupported RM-0006 provider operation: " + " ".join(argv) + "\n")
    return 22


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
