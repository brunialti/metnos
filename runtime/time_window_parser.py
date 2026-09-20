"""Shared deterministic temporal arithmetic; no model calls in this module.

Dates denote calendar periods, ``now-1h`` an instant, ``last-1h`` an interval.
Natural-language interpretation belongs to ``time_window_resolver``. All
domains use the configured IANA timezone and the same injectable clock.
The string compatibility API retains second precision; native bounds include
the final microsecond of each calendar period.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

import config as _C
import detection_lexicon_seed_parsers as _parser_lex


class TemporalError(ValueError):
    """Machine-readable failure, localized by the caller at the UI boundary."""

    def __init__(self, code: str, expression: str):
        self.code = code
        self.expression = expression
        super().__init__(f"{code}: {expression!r}")


_MONTHS_IMAP = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(min|s|h|d|w|m|y)")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_IMAP_DAY = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\Z")


def temporal_now(now=None, *, tz=None):
    """An explicit zone wins; an injected aware clock otherwise keeps its zone."""
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    zone = zone or ZoneInfo(_C.DEFAULT_TIMEZONE)
    if now is None:
        return datetime.now(zone)
    if isinstance(now, date) and not isinstance(now, datetime):
        now = datetime.combine(now, time())
    if not isinstance(now, datetime):
        raise TypeError("now must be a date or datetime")
    if now.tzinfo is None:
        return _localize(now, zone)
    return now.astimezone(zone) if tz is not None else now


def _localize(value, zone):
    """Refuse nonexistent or ambiguous wall times at daylight-saving changes."""
    candidates = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        back = candidate.astimezone(timezone.utc).astimezone(zone)
        if back.replace(tzinfo=None) == value:
            if not any(item.utcoffset() == candidate.utcoffset() for item in candidates):
                candidates.append(candidate)
    if len(candidates) != 1:
        code = "ambiguous_local_time" if candidates else "nonexistent_local_time"
        raise TemporalError(code, value.isoformat())
    return candidates[0]


def _full_day(day, zone):
    return (_localize(datetime.combine(day, time()), zone),
            _localize(datetime.combine(day, time.max), zone))


def _duration_parts(spec):
    matches = list(_DURATION.finditer(spec))
    if not matches or "".join(m.group(0) for m in matches) != spec:
        raise TemporalError("invalid_duration", spec)
    values = {}
    for match in matches:
        amount, unit = float(match.group(1)), match.group(2)
        if amount <= 0 or amount > 9999 or unit in values:
            raise TemporalError("invalid_duration", spec)
        if unit in {"m", "y"} and not amount.is_integer():
            raise TemporalError("invalid_duration", spec)
        values[unit] = amount
    return values


def _shift(anchor, duration, sign):
    parts = _duration_parts(duration)
    calendar = relativedelta(years=int(parts.get("y", 0)) * sign,
                             months=int(parts.get("m", 0)) * sign)
    shifted = anchor + calendar if calendar else anchor
    if calendar:
        shifted = _localize(shifted.replace(tzinfo=None), anchor.tzinfo)
    seconds = sum(parts.get(unit, 0) * scale for unit, scale in (
        ("w", 7 * 86400), ("d", 86400), ("h", 3600), ("min", 60), ("s", 1)))
    return (shifted.astimezone(timezone.utc) + timedelta(seconds=sign * seconds)).astimezone(anchor.tzinfo)


def _point(spec, now):
    """Resolve an endpoint to (start, end), preserving its precision."""
    if "@" in spec:
        base, clock = spec.rsplit("@", 1)
        if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", clock):
            raise TemporalError("invalid_time", spec)
        start, end = _point(base, now)
        if start.date() != end.date():
            raise TemporalError("ambiguous_date", spec)
        value = _localize(datetime.combine(start.date(), time.fromisoformat(clock)), now.tzinfo)
        return value, value
    if spec == "now":
        return now, now
    match = re.fullmatch(r"now([+-])(.+)", spec)
    if match:
        value = _shift(now, match.group(2), 1 if match.group(1) == "+" else -1)
        return value, value
    day_offset = {"today": 0, "yesterday": -1, "tomorrow": 1}.get(spec)
    if day_offset is not None:
        return _full_day(now.date() + timedelta(days=day_offset), now.tzinfo)
    match = re.fullmatch(r"today([+-])(\d+)([dwmy])", spec)
    if match:
        sign, amount, unit = match.groups()
        n = int(amount) * (1 if sign == "+" else -1)
        if abs(n) > 9999:
            raise TemporalError("invalid_duration", spec)
        delta = relativedelta(**{{"d": "days", "w": "weeks", "m": "months", "y": "years"}[unit]: n})
        return _full_day(now.date() + delta, now.tzinfo)
    match = re.fullmatch(r"weekday-([0-6])(?:-(last|next|this))?", spec)
    if match:
        weekday, direction = int(match.group(1)), match.group(2)
        if direction is None:
            raise TemporalError("ambiguous_weekday", spec)
        offset = weekday - now.weekday()
        if direction == "last":
            offset = -((now.weekday() - weekday - 1) % 7 + 1)
        elif direction == "next":
            offset = (offset - 1) % 7 + 1
        return _full_day(now.date() + timedelta(days=offset), now.tzinfo)
    if _ISO_DAY.fullmatch(spec):
        return _full_day(date.fromisoformat(spec), now.tzinfo)
    match = re.fullmatch(r"date-(\d{2})-(\d{2})", spec)
    if match:
        return _full_day(date(now.year, *map(int, match.groups())), now.tzinfo)
    match = _IMAP_DAY.fullmatch(spec)
    if match:
        day, month, year = match.groups()
        months = [value.casefold() for value in _MONTHS_IMAP]
        if month.casefold() not in months:
            raise TemporalError("invalid_date", spec)
        return _full_day(date(int(year), months.index(month.casefold()) + 1, int(day)), now.tzinfo)
    if re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}", spec):
        value = datetime.fromisoformat(spec.replace("z", "+00:00").replace("Z", "+00:00"))
        value = value.astimezone(now.tzinfo) if value.tzinfo else _localize(value, now.tzinfo)
        return value, value
    raise TemporalError("unknown time_window", spec)


def _calendar_period(spec, now):
    match = re.fullmatch(r"(last|this|next)-(week|month|year)", spec)
    if match:
        direction, unit = match.groups()
        offset = {"last": -1, "this": 0, "next": 1}[direction]
        day = now.date()
        if unit == "week":
            first = day - timedelta(days=day.weekday()) + timedelta(weeks=offset)
            after = first + timedelta(weeks=1)
        elif unit == "month":
            first = day.replace(day=1) + relativedelta(months=offset)
            after = first + relativedelta(months=1)
        else:
            first = date(day.year + offset, 1, 1)
            after = date(first.year + 1, 1, 1)
    elif re.fullmatch(r"\d{4}(?:-\d{2})?", spec):
        parts = [int(part) for part in spec.split("-")]
        first = date(parts[0], parts[1] if len(parts) == 2 else 1, 1)
        after = first + (relativedelta(months=1) if len(parts) == 2 else relativedelta(years=1))
    else:
        return None
    return _full_day(first, now.tzinfo)[0], _full_day(after - timedelta(days=1), now.tzinfo)[1]


def _phrase_alt(forms):
    return "|".join(re.escape(str(form)).replace(r"\ ", r"\s+")
                    for form in sorted(set(forms or ()), key=lambda item: (-len(item), item))
                    if str(form).strip())


def _localized_numeric(spec, now):
    lexicon = _parser_lex.load_family("time_parser")
    if lexicon is None:
        return None
    point = r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?"
    single = re.fullmatch(point, spec)
    connectors = lexicon["parser.time.range_connector"]
    left, right = (_phrase_alt(connectors.get(key, ())) for key in ("from", "to"))
    pair = re.fullmatch(point + "-" + point, spec)
    if pair is None and left and right:
        pair = re.fullmatch(rf"(?:{left})\s+{point}\s+(?:{right})\s+{point}", spec, re.I)
    def as_date(day, month, year, fallback):
        y = int(year) if year else fallback
        if year and len(year) == 2:
            y += 2000
        return date(y, int(month), int(day))
    if single:
        return _full_day(as_date(*single.groups(), now.year), now.tzinfo)
    if pair:
        d1, m1, y1, d2, m2, y2 = pair.groups()
        a = as_date(d1, m1, y1 or y2, now.year)
        b = as_date(d2, m2, y2 or y1, now.year)
        a, b = sorted((a, b))  # established numeric-range compatibility
        return _full_day(a, now.tzinfo)[0], _full_day(b, now.tzinfo)[1]
    return None


def _normalize_llm_spec(spec):
    value = re.sub(r"(?<=\d)t(?=\d{2}:)", "T", spec.strip().casefold())
    # Protocol spellings, not a privileged natural language.
    match = re.fullmatch(r"now[_ -]*(minus|plus)[_ -]*(.+)", value)
    if match:
        return ("last-" if match.group(1) == "minus" else "next-") + match.group(2)
    match = re.fullmatch(r"(last|next)[_ -]?(\d+)[_ -]?([a-z]+)", value)
    if match and match.group(3) in {"s", "min", "h", "d", "w", "m", "y"}:
        return f"{match.group(1)}-{match.group(2)}{match.group(3)}"
    from time_window_resolver import parse_query_time_window
    canonical = parse_query_time_window(value, exact=True)
    if canonical:
        return canonical
    # Compatibility surfaces from the former mail parser are now shared.
    # Only the versioned lexicon supplies words; it never supplies arithmetic.
    import detection_lexicon_seed_residual_am as legacy
    mapping = legacy.ready_mapping(legacy.MAIL_TIME_WINDOW)
    for key, target in (("today", "today"), ("yesterday", "yesterday"),
                        ("preset_week", "last-week"),
                        ("preset_month", "last-month"), ("preset_year", "last-year")):
        if value in [str(form).casefold() for form in mapping.get(key, ())]:
            return target
    units = {str(form).casefold(): target
             for key, target in (("day_unit", "d"), ("hour_unit", "h"),
                                 ("week_unit", "w"), ("month_unit", "m"), ("year_unit", "y"))
             for form in mapping.get(key, ())}
    markers = _phrase_alt(mapping.get("relative_marker", ()))
    if units:
        quantity = rf"(?P<count>\d+)\s*[-_ ]?\s*(?P<unit>{_phrase_alt(units)})"
        match = re.fullmatch(rf"(?:(?:{markers})[-_ ]?)?{quantity}(?:[-_ ]?(?:{markers}))?", value)
        if match and int(match.group("count")) > 0:
            return f"last-{match.group('count')}{units[match.group('unit')]}"
    return value


def resolve_time_bounds(spec, now=None, *, tz=None):
    """Aware bounds, or (None, None) for the explicit unbounded window 'all'."""
    if not isinstance(spec, str) or not spec.strip() or len(spec) > 512:
        raise TemporalError("invalid_time_window", str(spec))
    anchor = temporal_now(now, tz=tz)
    value = _normalize_llm_spec(spec)
    if value == "all":
        return None, None
    localized = _localized_numeric(value, anchor)
    if localized is not None:
        return localized
    calendar = _calendar_period(value, anchor)
    if calendar is not None:
        return calendar
    rolling = re.fullmatch(r"(last|next)-(.+)", value)
    if rolling:
        other = _shift(anchor, rolling.group(2), -1 if rolling.group(1) == "last" else 1)
        return (other, anchor) if rolling.group(1) == "last" else (anchor, other)
    if value.count("/") == 1:
        left, right = value.split("/", 1)
        left, right = _normalize_llm_spec(left), _normalize_llm_spec(right)
        start = (_calendar_period(left, anchor) or _point(left, anchor))[0]
        end = (_calendar_period(right, anchor) or _point(right, anchor))[1]
        if start.astimezone(timezone.utc) > end.astimezone(timezone.utc):
            raise TemporalError("reversed_time_window", spec)
        return start, end
    return _point(value, anchor)


def parse_time_window(spec, now=None, *, tz=None):
    """Compatibility adapter: ISO bounds with explicit timezone offsets."""
    start, end = resolve_time_bounds(spec, now, tz=tz)
    return (start.isoformat(timespec="seconds") if start is not None else None,
            end.isoformat(timespec="seconds") if end is not None else None)


def parse_datetime(spec, now=None, *, tz=None):
    """Resolve a date/time field; do not silently collapse an interval."""
    start, end = resolve_time_bounds(spec, now, tz=tz)
    if start is None or end is None:
        raise TemporalError("date_time_requires_an_instant", spec)
    if start != end and (start.date() != end.date()
                         or start.time() != time()
                         or end.time() != time.max):
        raise TemporalError("interval_not_instant", spec)
    return start
