# SPDX-License-Identifier: MIT
"""When a person last asked Metnos something, on any channel.

Automatic work that uses the same model as the conversation (building a
generated tool) must not make a person wait: it starts in a quiet moment and
steps aside between its stages. This module is the one place that answers
«is someone using Metnos now?», so every automatic job asks the same thing.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from logging_setup import get_logger

log = get_logger(__name__)


def _seconds_since_http_request() -> float | None:
    try:
        from turn_events import TurnEventLog
        return TurnEventLog.get().seconds_since_last_activity()
    except Exception as exc:  # noqa: BLE001 - an unknown source says nothing
        log.debug("person_activity: http activity unavailable: %r", exc)
        return None


def _seconds_since_channel_message() -> float | None:
    """The other channels run in their own process: they leave the moment of
    the last message in the pairing store, written when it arrives."""
    try:
        import pairing
        latest = None
        for item in pairing.list_pairings():
            seen = getattr(item, "last_seen", None)
            if not seen:
                continue
            moment = datetime.fromisoformat(str(seen).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            latest = moment if latest is None or moment > latest else latest
        if latest is None:
            return None
        return max(0.0, time.time() - latest.timestamp())
    except Exception as exc:  # noqa: BLE001
        log.debug("person_activity: channel activity unavailable: %r", exc)
        return None


def seconds_since_last_request() -> float | None:
    """Seconds since anyone last asked something; 0 while a request is being
    served; None when nothing is known (a fresh start)."""
    known = [value for value in (_seconds_since_http_request(),
                                 _seconds_since_channel_message())
             if value is not None]
    return min(known) if known else None


def person_is_active(quiet_s: float) -> bool:
    """True when someone asked something less than ``quiet_s`` seconds ago."""
    idle = seconds_since_last_request()
    return idle is not None and idle < float(quiet_s)
