"""Trigger grammar + next_fire_at computation for scheduler v2.

Supported:
  daily@HH:MM           local TZ (default Europe/Rome)
  every_Ns / Nm / Nh    seconds / minutes / hours
  at:<ISO8601>          absolute UTC fire time, one-shot
  cron:<5-field>        croniter optional

next_fire_at returns UTC epoch float, strictly > after_epoch.
ValueError on malformed input. RuntimeError if cron requested but croniter missing.

DST policy (daily@HH:MM in tz_name):
  - Use zoneinfo localtime; if HH:MM does not exist (spring-forward gap),
    move to next existing instant by adding 1h iteratively.
  - If HH:MM exists twice (fall-back), pick fold=0 (first occurrence).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

try:
    from croniter import croniter as _croniter  # type: ignore
    _HAS_CRONITER = True
except Exception:
    _HAS_CRONITER = False
    _croniter = None  # type: ignore


_DAILY_RE = re.compile(r"^daily@([01]\d|2[0-3]):([0-5]\d)$")
_EVERY_RE = re.compile(r"^every_(\d+)([smh])$")
_AT_RE = re.compile(r"^at:(.+)$")
_CRON_RE = re.compile(r"^cron:(.+)$")


def parse_trigger(trigger: str) -> dict[str, Any]:
    """Return shape dict {kind: 'daily'|'every'|'at'|'cron', ...}.

    Raises ValueError on malformed input.
    """
    if not isinstance(trigger, str) or not trigger:
        raise ValueError(f"empty trigger")
    m = _DAILY_RE.match(trigger)
    if m:
        return {"kind": "daily", "hh": int(m.group(1)), "mm": int(m.group(2))}
    m = _EVERY_RE.match(trigger)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            raise ValueError(f"every_ must be positive: {trigger}")
        secs = {"s": 1, "m": 60, "h": 3600}[unit] * n
        return {"kind": "every", "seconds": secs}
    m = _AT_RE.match(trigger)
    if m:
        raw = m.group(1)
        try:
            # accept Z suffix (Python 3.11+ handles it; for safety strip)
            iso = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
        except Exception as exc:
            raise ValueError(f"at: malformed ISO8601 {raw!r}: {exc}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return {"kind": "at", "epoch": dt.timestamp()}
    m = _CRON_RE.match(trigger)
    if m:
        expr = m.group(1).strip()
        # validate field count
        if len(expr.split()) != 5:
            raise ValueError(f"cron expression must have 5 fields: {expr!r}")
        return {"kind": "cron", "expr": expr}
    raise ValueError(f"unknown trigger form: {trigger!r}")


def _next_daily(after_epoch: float, hh: int, mm: int, tz_name: str) -> float:
    tz = ZoneInfo(tz_name)
    after_local = datetime.fromtimestamp(after_epoch, tz=tz)

    def _build(year: int, month: int, day: int) -> datetime:
        # Build wall-clock then attach tz with fold=0. Timestamp() handles
        # ambiguity (fold-back: first occurrence) and gap (spring-forward:
        # interpreted with the pre-gap offset, which when round-tripped lands
        # on the post-gap wall clock, e.g. 02:30 -> 03:30).
        return datetime(year, month, day, hh, mm, 0, 0, tzinfo=tz, fold=0)

    cand = _build(after_local.year, after_local.month, after_local.day)
    if cand.timestamp() <= after_epoch:
        # Advance one calendar day (use timedelta on naive components).
        nd = (after_local + timedelta(days=1))
        cand = _build(nd.year, nd.month, nd.day)
    return cand.timestamp()


def _next_every(after_epoch: float, seconds: int) -> float:
    # "every_N" is "after_epoch + N" — we don't anchor to an arbitrary origin
    return after_epoch + seconds


def _next_cron(after_epoch: float, expr: str, tz_name: str) -> float:
    if not _HAS_CRONITER:
        raise RuntimeError("croniter not installed")
    tz = ZoneInfo(tz_name)
    base = datetime.fromtimestamp(after_epoch, tz=tz)
    it = _croniter(expr, base)
    nxt = it.get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=tz)
    return nxt.timestamp()


def next_fire_at(trigger: str, after_epoch: float, tz_name: str = "Europe/Rome") -> float:
    """Return next fire as UTC epoch, strictly > after_epoch.

    For `at:` triggers, returns the original timestamp (caller decides whether
    to fire-now-or-skip if it's in the past).
    """
    spec = parse_trigger(trigger)
    kind = spec["kind"]
    if kind == "daily":
        return _next_daily(after_epoch, spec["hh"], spec["mm"], tz_name)
    if kind == "every":
        return _next_every(after_epoch, spec["seconds"])
    if kind == "at":
        return float(spec["epoch"])  # may be in the past
    if kind == "cron":
        return _next_cron(after_epoch, spec["expr"], tz_name)
    raise ValueError(f"unhandled kind {kind!r}")
