#!/usr/bin/env python3
"""time_read — executor di Metnos v1.1."""
import json
import sys
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


def invoke(args):
    tz_name = args.get("timezone", "UTC")
    try:
        if tz_name == "UTC":
            tz = timezone.utc
        else:
            if ZoneInfo is None:
                return {"ok": False, "error": "ZoneInfo non disponibile (Python < 3.9)"}
            tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        return {"ok": False, "error": f"unknown timezone '{tz_name}': {e}"}
    except Exception as e:
        return {"ok": False, "error": f"unknown timezone '{tz_name}': {e}"}

    now = datetime.now(tz)
    return {
        "ok": True,
        "content": now.isoformat(),
        "metadata": {
            "timezone": tz_name,
            "iso8601": now.isoformat(),
            "epoch": now.timestamp(),
        },
    }


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        args = {}
    else:
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
            return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
