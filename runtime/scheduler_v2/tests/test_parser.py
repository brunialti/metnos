"""Trigger grammar + next_fire_at coverage."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scheduler_v2.schedule_parser import next_fire_at, parse_trigger


def test_parse_daily():
    spec = parse_trigger("daily@08:00")
    assert spec == {"kind": "daily", "hh": 8, "mm": 0}


def test_parse_daily_edges():
    assert parse_trigger("daily@00:00")["hh"] == 0
    assert parse_trigger("daily@23:59")["mm"] == 59


@pytest.mark.parametrize("bad", [
    "daily@24:00", "daily@8:00", "daily@08:60", "daily@-1:00", "daily@", "DAILY@08:00",
])
def test_parse_daily_malformed(bad):
    with pytest.raises(ValueError):
        parse_trigger(bad)


def test_parse_every():
    assert parse_trigger("every_30s") == {"kind": "every", "seconds": 30}
    assert parse_trigger("every_5m") == {"kind": "every", "seconds": 300}
    assert parse_trigger("every_2h") == {"kind": "every", "seconds": 7200}


@pytest.mark.parametrize("bad", ["every_0s", "every_5d", "every_-1m", "every_", "every_5"])
def test_parse_every_malformed(bad):
    with pytest.raises(ValueError):
        parse_trigger(bad)


def test_parse_at():
    spec = parse_trigger("at:2030-01-01T12:00:00+00:00")
    assert spec["kind"] == "at"
    assert spec["epoch"] == datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def test_parse_at_z_suffix():
    spec = parse_trigger("at:2030-01-01T12:00:00Z")
    assert spec["epoch"] == datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def test_parse_at_naive_assumed_utc():
    spec = parse_trigger("at:2030-01-01T12:00:00")
    assert spec["epoch"] == datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def test_parse_at_malformed():
    with pytest.raises(ValueError):
        parse_trigger("at:not-a-date")


def test_parse_unknown():
    with pytest.raises(ValueError):
        parse_trigger("manual")
    with pytest.raises(ValueError):
        parse_trigger("")


def test_next_fire_daily_advances():
    tz = ZoneInfo("Europe/Rome")
    # Pick a winter date so DST doesn't interfere.
    base = datetime(2026, 1, 15, 10, 0, tzinfo=tz)
    nxt = next_fire_at("daily@08:00", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    # 10:00 base, 08:00 already past today, so should be tomorrow 08:00
    assert nxt_local.year == 2026 and nxt_local.month == 1 and nxt_local.day == 16
    assert nxt_local.hour == 8 and nxt_local.minute == 0


def test_next_fire_daily_today_future():
    tz = ZoneInfo("Europe/Rome")
    base = datetime(2026, 1, 15, 6, 0, tzinfo=tz)
    nxt = next_fire_at("daily@08:00", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert nxt_local.day == 15 and nxt_local.hour == 8


def test_next_fire_every_adds_seconds():
    base = 1_700_000_000.0
    assert next_fire_at("every_60s", base) == base + 60
    assert next_fire_at("every_2m", base) == base + 120
    assert next_fire_at("every_1h", base) == base + 3600


def test_next_fire_at_returns_original_even_if_past():
    # at: triggers honestly return the requested epoch; caller decides.
    target = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()
    nxt = next_fire_at("at:2020-01-01T00:00:00Z", time.time())
    assert nxt == target


def test_next_fire_at_strict_gt_sentinel():
    # at: explicitly does not enforce strict-greater (one-shot semantics);
    # the caller is responsible for "fire now or skip".
    target_dt = datetime(2030, 6, 15, 12, 0, tzinfo=timezone.utc)
    nxt = next_fire_at(f"at:{target_dt.isoformat()}", target_dt.timestamp() - 1)
    assert nxt == target_dt.timestamp()


def test_dst_spring_forward_rome_2026():
    """Europe/Rome 2026-03-29: 02:00 -> 03:00. Wall-clock 02:30 doesn't exist.

    daily@02:30 on a date AT/BEFORE the spring-forward day should advance to
    03:30 of the spring-forward day (next existing instant).
    """
    tz = ZoneInfo("Europe/Rome")
    # base just before midnight on the spring-forward day
    base = datetime(2026, 3, 28, 23, 0, tzinfo=tz)
    nxt = next_fire_at("daily@02:30", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert nxt_local.year == 2026 and nxt_local.month == 3 and nxt_local.day == 29
    # 02:30 doesn't exist; we expect 03:30 (next existing instant)
    assert nxt_local.hour == 3 and nxt_local.minute == 30


def test_dst_fall_back_rome_2026_picks_first_occurrence():
    """Europe/Rome 2026-10-25: 03:00 -> 02:00. Wall-clock 02:30 occurs twice.

    daily@02:30 on/before fall-back day should pick fold=0 (first occurrence,
    pre-rollback in CEST).
    """
    tz = ZoneInfo("Europe/Rome")
    base = datetime(2026, 10, 24, 23, 0, tzinfo=tz)
    nxt = next_fire_at("daily@02:30", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert nxt_local.month == 10 and nxt_local.day == 25
    assert nxt_local.hour == 2 and nxt_local.minute == 30
    # First occurrence -> still in CEST (utcoffset = +2h)
    assert nxt_local.utcoffset() == timedelta(hours=2)


def test_dst_normal_day_after_spring_forward():
    tz = ZoneInfo("Europe/Rome")
    base = datetime(2026, 3, 30, 0, 0, tzinfo=tz)
    nxt = next_fire_at("daily@08:00", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert nxt_local.day == 30 and nxt_local.hour == 8


def test_cron_skip_if_not_installed():
    pytest.importorskip("croniter")
    # If we got here, croniter exists; verify cron path works.
    base = 1_700_000_000.0
    nxt = next_fire_at("cron:0 8 * * *", base, "Europe/Rome")
    assert nxt > base


def test_cron_runtime_error_when_missing():
    """If croniter is absent, calling cron-trigger raises RuntimeError clearly."""
    try:
        import croniter  # noqa: F401
        pytest.skip("croniter installed; cannot test missing branch")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="croniter"):
        next_fire_at("cron:0 8 * * *", 1_700_000_000.0)


def test_cron_malformed_field_count():
    with pytest.raises(ValueError):
        parse_trigger("cron:0 8 * *")
