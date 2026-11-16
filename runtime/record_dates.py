# SPDX-License-Identifier: MIT
"""The date of a record: one rule for every object (15/11/2026).

A record's date is when it was last changed; when nothing says that, when it
was made; when nothing says either, the date it carries. The names a producer
may use for each of the three are the documented variants of `field_synonyms`,
so a file, a message, an event, a contact and a sheet row are read alike and
no domain is named here.

A field counts only where its value reads as an instant: `created: true` is a
flag, an elapsed time is not a date. Values are compared as instants whatever
their form (epoch number, ISO text with or without a zone, a mail-style date,
a written date), never as text.
"""
from __future__ import annotations

from datetime import date, datetime, time

from field_synonyms import field_group

# Reading order of a record's date: last change, making, the date it carries.
DATE_ROLES = (
    ("modified", field_group("modified_at")),
    ("created", field_group("created_at")),
    ("dated", tuple(name for name in field_group("date")
                    if name not in field_group("modified_at")
                    and name not in field_group("created_at"))),
)
DATE_FIELDS = tuple(dict.fromkeys(
    name for _role, names in DATE_ROLES for name in names))

# Seconds since the epoch between 1973 and 5138; the coarser units a producer
# may expose (milli, micro, nano) are told apart by magnitude.
_EPOCH_UNITS = ((1e8, 1e11, 1.0), (1e11, 1e14, 1e3), (1e14, 1e17, 1e6),
                (1e17, 1e20, 1e9))


def _epoch(number: float) -> float | None:
    for low, high, unit in _EPOCH_UNITS:
        if low <= number < high:
            return number / unit
    return None


def instant(value) -> float | None:
    """Seconds since the epoch for a value that is a date, else None.

    A day is its first instant and a month its first day, on the instance's
    clock; an instant that names no zone is read on the same clock. A bare
    number is a date only by its magnitude; a boolean never is.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _epoch(float(value))
    if not isinstance(value, str) or not value.strip():
        return None
    from executor_helpers import date_text
    from time_window_parser import normalize_observed_date, temporal_now

    text = str(date_text(value)).strip()
    try:
        return _epoch(float(text))
    except ValueError:
        pass
    if not any(ch.isdigit() for ch in text):
        return None
    canonical = normalize_observed_date(text)
    if not isinstance(canonical, str):
        return None
    canonical = canonical.strip()
    try:
        if len(canonical) == 7 and canonical[4] == "-":
            canonical += "-01"
        if "T" in canonical or " " in canonical:
            moment = datetime.fromisoformat(canonical)
        else:
            moment = datetime.combine(date.fromisoformat(canonical), time())
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=temporal_now().tzinfo)
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


# Records looked at to tell what a field holds; the comparison reads them all.
_SAMPLE = 200


def _field_names(entries) -> list[str]:
    names: dict[str, None] = {}
    for entry in entries[:_SAMPLE]:
        if isinstance(entry, dict):
            for key in entry:
                if isinstance(key, str) and not key.startswith("_"):
                    names.setdefault(key)
    return list(names)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def holds_dates(entries, field: str) -> bool:
    """These records hold dates in this field: at least one value reads as a
    date and none reads as anything else. In a field no producer documents as
    a date, a number is a quantity: only written dates count there."""
    documented = field in DATE_FIELDS
    seen = False
    for entry in (entries or [])[:_SAMPLE]:
        if not isinstance(entry, dict):
            continue
        value = entry.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not documented and (not isinstance(value, str) or _is_number(value)):
            return False
        if instant(value) is None:
            return False
        seen = True
    return seen


def date_fields(entries) -> list[tuple[str, str]]:
    """Every field of these records that holds dates, as (field, role), in
    the reading order of the rule; fields no producer documents come last,
    with the role "dated"."""
    entries = entries if isinstance(entries, list) else []
    names = _field_names(entries)
    found = [(field, role) for role, group in DATE_ROLES for field in group
             if field in names and holds_dates(entries, field)]
    found += [(field, "dated") for field in names
              if field not in DATE_FIELDS and holds_dates(entries, field)]
    return found


def date_field(entries) -> str | None:
    """The field that holds the date of these records, by the rule. None when
    they carry no date, or only date columns no producer documents and more
    than one: which of them is meant is the person's to say."""
    found = date_fields(entries)
    documented = [field for field, _role in found if field in DATE_FIELDS]
    if documented:
        return documented[0]
    return found[0][0] if len(found) == 1 else None
