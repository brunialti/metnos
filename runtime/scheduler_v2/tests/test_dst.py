"""DST behaviour for daily@HH:MM in Europe/Rome."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scheduler_v2.schedule_parser import next_fire_at


def test_daily_0230_advances_through_spring_forward():
    """A recurring daily@02:30 across the spring-forward day must produce
    a *plausible* fire time (03:30 on the gap day, 02:30 the following days).
    """
    tz = ZoneInfo("Europe/Rome")
    base = datetime(2026, 3, 28, 12, 0, tzinfo=tz)
    nxt1 = next_fire_at("daily@02:30", base.timestamp(), "Europe/Rome")
    nxt1_local = datetime.fromtimestamp(nxt1, tz=tz)
    # 2026-03-29: 02:30 doesn't exist -> 03:30
    assert (nxt1_local.month, nxt1_local.day) == (3, 29)
    assert (nxt1_local.hour, nxt1_local.minute) == (3, 30)

    # The next call (after firing) returns 02:30 on 2026-03-30 (post-DST).
    nxt2 = next_fire_at("daily@02:30", nxt1, "Europe/Rome")
    nxt2_local = datetime.fromtimestamp(nxt2, tz=tz)
    assert (nxt2_local.month, nxt2_local.day) == (3, 30)
    assert (nxt2_local.hour, nxt2_local.minute) == (2, 30)


def test_daily_0230_fall_back_picks_first():
    """On the fall-back day (2026-10-25 in Europe/Rome), 02:30 occurs twice
    (once in CEST, once in CET). next_fire_at must pick the first occurrence.
    """
    tz = ZoneInfo("Europe/Rome")
    base = datetime(2026, 10, 24, 12, 0, tzinfo=tz)
    nxt = next_fire_at("daily@02:30", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert (nxt_local.month, nxt_local.day) == (10, 25)
    # First occurrence is still in CEST
    assert nxt_local.utcoffset() == timedelta(hours=2)


def test_daily_0800_unaffected_by_dst():
    """08:00 is far from the DST transition window and should map to the
    expected wall clock on both sides.
    """
    tz = ZoneInfo("Europe/Rome")
    # Day of spring-forward
    base = datetime(2026, 3, 29, 0, 0, tzinfo=tz)
    nxt = next_fire_at("daily@08:00", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert (nxt_local.day, nxt_local.hour, nxt_local.minute) == (29, 8, 0)


def test_daily_2330_before_fall_back_advances_one_day():
    """daily@23:30 base at 2026-10-24 23:35 (already past) should produce
    2026-10-25 23:30 (after fall-back, in CET).
    """
    tz = ZoneInfo("Europe/Rome")
    base = datetime(2026, 10, 24, 23, 35, tzinfo=tz)
    nxt = next_fire_at("daily@23:30", base.timestamp(), "Europe/Rome")
    nxt_local = datetime.fromtimestamp(nxt, tz=tz)
    assert (nxt_local.month, nxt_local.day) == (10, 25)
    assert (nxt_local.hour, nxt_local.minute) == (23, 30)
    assert nxt_local.utcoffset() == timedelta(hours=1)  # CET (post fall-back)
