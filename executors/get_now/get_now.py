#!/usr/bin/env python3
"""time_read — executor di Metnos v1.1."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

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
                return {"ok": False, "error": _msg("ERR_ZONEINFO_MISSING")}
            tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="timezone", reason=str(tz_name))}
    except Exception as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="timezone", reason=str(tz_name))}

    now = datetime.now(tz)
    return {
        "ok": True,
        "content": now.isoformat(),
        "metadata": {
            "timezone": tz_name,
            "iso8601": now.isoformat(),
            "epoch": now.timestamp(),
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%Y-%m-%d"),
        },
    }


def main():
    run_stdio(invoke, allow_empty=True)


if __name__ == "__main__":
    main()
