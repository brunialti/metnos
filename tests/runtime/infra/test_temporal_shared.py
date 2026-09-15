"""Cross-domain contract: instants, intervals, calendar periods, zones and NL."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from time_window_parser import (TemporalError, parse_datetime, parse_time_window,
                                resolve_time_bounds)
from time_window_resolver import parse_query_time_window


NOW = datetime(2026, 9, 15, 12, 30, tzinfo=ZoneInfo("Europe/Rome"))


@pytest.mark.parametrize("text,canonical", [
    ("oggi", "today"), ("today", "today"),
    ("ieri", "yesterday"), ("yesterday", "yesterday"),
    ("domani", "tomorrow"), ("tomorrow", "tomorrow"),
    ("dopodomani", "today+2d"), ("day after tomorrow", "today+2d"),
    ("avantieri", "today-2d"), ("day before yesterday", "today-2d"),
    ("ultime due ore", "last-2h"), ("last two hours", "last-2h"),
    ("una ora fa", "now-1h"), ("an hour ago", "now-1h"),
    ("2 giorni fa", "today-2d"), ("two days ago", "today-2d"),
    ("tra due giorni", "today+2d"), ("in two days", "today+2d"),
    ("prossimi due giorni", "next-2d"), ("next two days", "next-2d"),
    ("giovedì prossimo", "weekday-3-next"), ("next Thursday", "weekday-3-next"),
    ("giovedi scorso", "weekday-3-last"), ("last Thursday", "weekday-3-last"),
    ("questa settimana", "this-week"), ("this week", "this-week"),
    ("mese scorso", "last-month"), ("last month", "last-month"),
    ("ultimi cinque minuti", "last-5min"), ("last five minutes", "last-5min"),
])
def test_reviewed_languages_share_one_arithmetic(text, canonical):
    assert parse_query_time_window(text, exact=True) == canonical
    assert parse_time_window(text, NOW) == parse_time_window(canonical, NOW)


@pytest.mark.parametrize("query,canonical", [
    ("nelle ultime due ore", "last-2h"),
    ("di una ora fa", "now-1h"),
    ("le mail di due giorni fa", "today-2d"),
])
def test_query_extraction_does_not_collapse_point_into_window(query, canonical):
    assert parse_query_time_window(query) == canonical


@pytest.mark.parametrize("zone", ["UTC", "Europe/Rome", "America/New_York", "Asia/Tokyo", "Pacific/Auckland"])
@pytest.mark.parametrize("day_offset", [-730, -30, -2, -1, 0, 1, 2, 30, 730])
def test_calendar_days_in_every_zone(zone, day_offset):
    now = NOW.astimezone(ZoneInfo(zone))
    spec = "today" if not day_offset else f"today{day_offset:+}d"
    start, end = resolve_time_bounds(spec, now)
    assert start.date() == (now + timedelta(days=day_offset)).date()
    assert end.date() == start.date()
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)
    assert start.tzinfo == now.tzinfo


@pytest.mark.parametrize("weekday", range(7))
@pytest.mark.parametrize("today", range(7))
@pytest.mark.parametrize("direction", ["last", "next", "this"])
def test_weekday_all_relative_positions(weekday, today, direction):
    now = NOW.replace(day=14 + today)  # Monday through Sunday
    start, _ = resolve_time_bounds(f"weekday-{weekday}-{direction}", now)
    offset = (start.date() - now.date()).days
    assert start.weekday() == weekday
    if direction == "last":
        assert -7 <= offset <= -1
    elif direction == "next":
        assert 1 <= offset <= 7
    else:
        assert start.date().isocalendar()[:2] == now.date().isocalendar()[:2]


@pytest.mark.parametrize("text", ["giovedi", "giovedì", "Thursday", "weekday-3"])
def test_unqualified_weekday_is_not_silently_guessed(text):
    with pytest.raises(TemporalError, match="ambiguous_weekday"):
        resolve_time_bounds(text, NOW)


@pytest.mark.parametrize("day", [datetime(2026, 3, 29, 4, 30), datetime(2026, 10, 25, 4, 30)])
@pytest.mark.parametrize("hours", [1, 2, 4, 24, 48])
def test_elapsed_hours_do_not_change_at_dst(day, hours):
    now = day.replace(tzinfo=ZoneInfo("Europe/Rome"))
    start, end = resolve_time_bounds(f"last-{hours}h", now)
    assert (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() == hours * 3600


@pytest.mark.parametrize("text,code", [
    ("2026-03-29@02:30", "nonexistent_local_time"),
    ("2026-10-25@02:30", "ambiguous_local_time"),
])
def test_dst_wall_times_require_a_unique_instant(text, code):
    with pytest.raises(TemporalError, match=code):
        resolve_time_bounds(text, NOW)


def test_explicit_offset_disambiguates_dst_fold():
    a = parse_datetime("2026-10-25T02:30:00+02:00", NOW)
    b = parse_datetime("2026-10-25T02:30:00+01:00", NOW)
    assert b.timestamp() - a.timestamp() == 3600


def test_point_interval_and_compound_duration_are_different():
    point = resolve_time_bounds("now-1h", NOW)
    window = resolve_time_bounds("last-1h", NOW)
    assert point[0] == point[1] == window[0]
    assert window[1] == NOW
    start, end = resolve_time_bounds("last-1h30min", NOW)
    assert end - start == timedelta(minutes=90)


def test_unbounded_window_is_not_an_instant_or_an_open_interval_endpoint():
    assert resolve_time_bounds("all", NOW) == (None, None)
    assert parse_time_window("all", NOW) == (None, None)
    with pytest.raises(TemporalError, match="date_time_requires_an_instant"):
        parse_datetime("all", NOW)
    for expression in ("all/today", "yesterday/all"):
        with pytest.raises(TemporalError):
            resolve_time_bounds(expression, NOW)


def test_complex_interval_endpoints_use_one_clock():
    start, end = resolve_time_bounds("today-1d@10:00/today@11:00", NOW)
    assert start.isoformat() == "2026-09-14T10:00:00+02:00"
    assert end.isoformat() == "2026-09-15T11:00:00+02:00"


def test_month_end_and_leap_year_are_calendar_arithmetic():
    now = NOW.replace(year=2024, month=3, day=31)
    assert resolve_time_bounds("last-1m", now)[0].date().isoformat() == "2024-02-29"
    leap = now.replace(month=2, day=29)
    assert resolve_time_bounds("today+1y", leap)[0].date().isoformat() == "2025-02-28"


@pytest.mark.parametrize("value", ["last-0h", "last--2h", "last-2h2h", "last-10000d",
    "last-1.5m", "2026-02-30", "31-Feb-2026", "today@25:00", "tomorrow/yesterday",
    "yesterday OR ALL", "today\r\nALL", "", "no date here"])
def test_invalid_values_never_widen_a_query(value):
    with pytest.raises(ValueError):
        resolve_time_bounds(value, NOW)


@pytest.mark.parametrize("value", ["last-1h", "today@10:00/today@11:00", "this-week"])
def test_datetime_field_never_silently_takes_first_interval_endpoint(value):
    with pytest.raises(TemporalError, match="interval_not_instant"):
        parse_datetime(value, NOW)


def test_legacy_argument_extraction_uses_the_shared_local_calendar(monkeypatch):
    import args_extractor
    import time_window_parser
    local = datetime(2026, 9, 16, 0, 30, tzinfo=ZoneInfo("Europe/Rome"))
    monkeypatch.setattr(time_window_parser, "temporal_now", lambda *_args, **_kwargs: local)
    assert args_extractor._extract_date_keyword("eventi oggi") == "2026-09-16"
    assert args_extractor._extract_date_keyword("eventi due giorni fa") == "2026-09-14"
    assert args_extractor._extract_date_keyword("eventi da ieri a domani") is None
    assert args_extractor._extract_time_window("last two hours") == "last-2h"


def test_imap_iso_and_relative_since_share_validation():
    assert resolve_time_bounds("15-Sep-2026", NOW) == resolve_time_bounds("2026-09-15", NOW)
    assert resolve_time_bounds("now_minus_48h", NOW) == resolve_time_bounds("last-48h", NOW)
