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
    hhmm = now.strftime("%H:%M")
    ymd = now.strftime("%Y-%m-%d")
    # Campi human-utili LIFTATI a top-level (§2.4 al confine OUTPUT): il
    # PLANNER/engine costruisce final_message con placeholder `${step1.<campo>}`
    # e indovina nomi naturali (`now`, `time`, `date`). Se il campo vive solo
    # dentro `metadata`, il placeholder NON risolve e renderizza BLANK in
    # silenzio (bug live «Sono le  (UTC).», turn 35fddbcc + fastpath appreso
    # id=203). Esporli a top-level rende risolvibili le forme che l'LLM produce.
    # `timezone`/`iso` restano SOLO in `metadata` di proposito: esporre il fuso
    # in cima lo rende prominente e il proposer lo appiccica alla risposta
    # («(UTC)»), rumore indesiderato sull'ora corrente. `metadata` resta per i
    # consumer che il fuso lo vogliono davvero (retro-compat).
    return {
        "ok": True,
        "content": now.isoformat(),
        "now": hhmm,
        "time": hhmm,
        "date": ymd,
        "metadata": {
            "timezone": tz_name,
            "iso8601": now.isoformat(),
            "epoch": now.timestamp(),
            "time": hhmm,
            "date": ymd,
        },
    }


def main():
    run_stdio(invoke, allow_empty=True)


if __name__ == "__main__":
    main()
