"""Test per la semantica calendariale di `next-week` / `this-week`
(ADR 0129, 12/5/2026).

Trigger turn 058d1fc1 (12/5/2026 12:42, live): query "proponi 3 orari per
la prossima settimana" → find_events_empty(time_windows=["next-week"]) →
slot 13-19 maggio (rolling 7gg da today=12/5).

Roberto: "next-week" in italiano standard = settimana calendariale Mon-Sun
SEGUENTE a quella corrente, NON rolling 7gg. Stessa lettura in EN
("next week" = next calendar week Mon-Sun).

Invariante locked-in:
- "this-week" = [current_monday 00:00, current_sunday 23:59:59].
- "next-week" = [next_monday 00:00, next_sunday 23:59:59], dove
  next_monday = current_monday + 7 giorni.
- "last-week" = [prev_monday 00:00, prev_sunday 23:59:59].
- Per ogni giorno della settimana corrente (Mon..Sun) il calcolo deve
  produrre la stessa (next_monday, next_sunday): la finestra non scala con
  il giorno della settimana corrente.

Determinismo §7.9 (zero LLM): solo aritmetica datetime.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from time_window_parser import parse_time_window  # noqa: E402

ROME = ZoneInfo("Europe/Rome")


def _now(year, month, day, hour=8):
    """Inietta un now deterministico aware Europe/Rome."""
    return datetime(year, month, day, hour, 0, tzinfo=ROME)


def _parse_iso(s):
    """Parsa l'output del parser (ISO 8601 con offset esplicito)."""
    return datetime.fromisoformat(s)


def _weekday_name(d):
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()]


# --------------------------------------------------------------------------
# next-week: tutti i giorni della settimana corrente devono dare la stessa
# (next_monday, next_sunday). Test parametrizzato su tutti i 7 giorni.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("y,m,d,expected_next_monday,expected_next_sunday", [
    # 2026-05-11 e' un lunedi'. La settimana e' [Mon 11, Sun 17].
    # next-week = [Mon 18, Sun 24].
    (2026, 5, 11, date(2026, 5, 18), date(2026, 5, 24)),  # Mon
    (2026, 5, 12, date(2026, 5, 18), date(2026, 5, 24)),  # Tue (turn 058d1fc1)
    (2026, 5, 13, date(2026, 5, 18), date(2026, 5, 24)),  # Wed
    (2026, 5, 14, date(2026, 5, 18), date(2026, 5, 24)),  # Thu
    (2026, 5, 15, date(2026, 5, 18), date(2026, 5, 24)),  # Fri
    (2026, 5, 16, date(2026, 5, 18), date(2026, 5, 24)),  # Sat
    (2026, 5, 17, date(2026, 5, 18), date(2026, 5, 24)),  # Sun → next mon = 18
])
def test_next_week_calendar_aligned_every_weekday(
    y, m, d, expected_next_monday, expected_next_sunday
):
    """Per qualunque giorno della settimana 11-17/5, next-week e' [18,24]."""
    today = _now(y, m, d, hour=14)
    assert _weekday_name(today.date()) in (
        "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"
    )
    start_iso, end_iso = parse_time_window("next-week", now=today)
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    assert start.date() == expected_next_monday, \
        f"today={today.date()}: expected next_monday={expected_next_monday}, " \
        f"got {start.date()} ({_weekday_name(start.date())})"
    assert end.date() == expected_next_sunday
    # next_monday e' SEMPRE un lunedi'
    assert start.weekday() == 0
    # next_sunday e' SEMPRE una domenica
    assert end.weekday() == 6
    # Inizio giornata 00:00:00, fine 23:59:59 (parita' con _full_day)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)


# --------------------------------------------------------------------------
# Edge cases del task: today=Sunday → next-week = next Mon, NON today+1
# (today+1 sarebbe Mon ma il calcolo va validato apertamente).
# today=Monday → next-week = today+7 (NON today, NON rolling 7gg da today+1).
# --------------------------------------------------------------------------

def test_next_week_when_today_is_sunday():
    """today=Sunday 17/5 → next-week start = Mon 18/5 (today+1)."""
    today = _now(2026, 5, 17, hour=10)
    assert _weekday_name(today.date()) == "Sun"
    start_iso, _ = parse_time_window("next-week", now=today)
    start = _parse_iso(start_iso)
    assert start.date() == date(2026, 5, 18)
    assert (start.date() - today.date()).days == 1


def test_next_week_when_today_is_monday():
    """today=Monday 11/5 → next-week start = Mon 18/5 (today+7)."""
    today = _now(2026, 5, 11, hour=10)
    assert _weekday_name(today.date()) == "Mon"
    start_iso, _ = parse_time_window("next-week", now=today)
    start = _parse_iso(start_iso)
    assert start.date() == date(2026, 5, 18)
    assert (start.date() - today.date()).days == 7


# --------------------------------------------------------------------------
# Cross-year / cross-month edge.
# --------------------------------------------------------------------------

def test_next_week_crosses_year_boundary():
    """today=2026-12-29 (Tue) → next-week = Mon 2027-01-04, Sun 2027-01-10."""
    today = _now(2026, 12, 29, hour=10)
    assert _weekday_name(today.date()) == "Tue"
    start_iso, end_iso = parse_time_window("next-week", now=today)
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    assert start.date() == date(2027, 1, 4)
    assert end.date() == date(2027, 1, 10)


def test_next_week_crosses_month_boundary():
    """today=2026-05-28 (Thu) → next-week = Mon 2026-06-01, Sun 2026-06-07."""
    today = _now(2026, 5, 28, hour=10)
    assert _weekday_name(today.date()) == "Thu"
    start_iso, end_iso = parse_time_window("next-week", now=today)
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    assert start.date() == date(2026, 6, 1)
    assert end.date() == date(2026, 6, 7)


# --------------------------------------------------------------------------
# this-week: settimana corrente (Mon-Sun), stabile su tutta la settimana.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("y,m,d,expected_monday,expected_sunday", [
    (2026, 5, 11, date(2026, 5, 11), date(2026, 5, 17)),  # Mon
    (2026, 5, 12, date(2026, 5, 11), date(2026, 5, 17)),  # Tue
    (2026, 5, 17, date(2026, 5, 11), date(2026, 5, 17)),  # Sun
])
def test_this_week_calendar_aligned(y, m, d, expected_monday, expected_sunday):
    today = _now(y, m, d, hour=8)
    start_iso, end_iso = parse_time_window("this-week", now=today)
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    assert start.date() == expected_monday
    assert end.date() == expected_sunday
    assert start.weekday() == 0
    assert end.weekday() == 6


# --------------------------------------------------------------------------
# Anti-regression: next-week NON puo' essere rolling 7gg da today.
# Garantisce che this-week ⊕ next-week siano 14 giorni consecutivi (Mon-Sun
# di due settimane), non finestre sovrapposte/scorrevoli.
# --------------------------------------------------------------------------

def test_next_week_not_rolling_seven_days():
    """today=Tue 12/5 → next-week NON e' [13/5, 19/5] (rolling 7d).
    Deve essere [18/5, 24/5] (calendar week Mon-Sun)."""
    today = _now(2026, 5, 12, hour=12)
    start_iso, end_iso = parse_time_window("next-week", now=today)
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    # Forma rolling errata (vecchio comportamento speculato)
    rolling_start = today.date() + timedelta(days=1)
    rolling_end = today.date() + timedelta(days=7)
    assert start.date() != rolling_start, \
        f"REGRESSION: next-week e' tornato rolling 7gg ({rolling_start})"
    assert end.date() != rolling_end, \
        f"REGRESSION: next-week e' tornato rolling 7gg ({rolling_end})"
    # Forma calendar corretta
    assert start.date() == date(2026, 5, 18)
    assert end.date() == date(2026, 5, 24)


def test_this_and_next_week_are_consecutive_seven_day_blocks():
    """this-week ⊕ next-week = 14 giorni consecutivi Mon-Sun, no overlap."""
    today = _now(2026, 5, 14, hour=8)  # Thu

    this_s, this_e = parse_time_window("this-week", now=today)
    next_s, next_e = parse_time_window("next-week", now=today)

    this_s_d = _parse_iso(this_s).date()
    this_e_d = _parse_iso(this_e).date()
    next_s_d = _parse_iso(next_s).date()
    next_e_d = _parse_iso(next_e).date()

    # Niente overlap
    assert this_e_d < next_s_d
    # Esattamente 1 giorno di gap (sun → mon)
    assert (next_s_d - this_e_d).days == 1
    # 7 giorni ciascuna
    assert (this_e_d - this_s_d).days == 6
    assert (next_e_d - next_s_d).days == 6


# --------------------------------------------------------------------------
# last-week parita': settimana precedente Mon-Sun.
# --------------------------------------------------------------------------

def test_last_week_calendar_aligned():
    today = _now(2026, 5, 14, hour=8)  # Thu, settimana 11-17
    start_iso, end_iso = parse_time_window("last-week", now=today)
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    assert start.date() == date(2026, 5, 4)  # Mon prev week
    assert end.date() == date(2026, 5, 10)  # Sun prev week
