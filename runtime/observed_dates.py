# SPDX-License-Identifier: MIT
"""Pure normalization of observed dates; language data is supplied by callers.

This module is shared with device bundles and uses only the Python standard
library. It does not infer missing date components or load a runtime catalog.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime


# The source did not state the year: the mark must survive normalization so
# an assumed year is never promoted to observed evidence.
ASSUMED_YEAR_MARK = "*"


def _phrase_alt(forms):
    return "|".join(re.escape(str(form)).replace(r"\ ", r"\s+")
                    for form in sorted(set(forms or ()), key=lambda item: (-len(item), item))
                    if str(form).strip())


def normalize_observed_date(value, *, month_names: dict[str, int]):
    """Canonicalize an observed date without filling in missing components.

    Year/month literals retain month precision. Unknown or invalid values
    stay readable; an assumed-year mark is preserved, never promoted to fact.
    Month names are supplied by the caller as casefolded keys. An empty
    mapping disables named-month recognition; this helper performs no lookup.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    if raw.startswith(ASSUMED_YEAR_MARK):
        inner = normalize_observed_date(raw[len(ASSUMED_YEAR_MARK):], month_names=month_names)
        return ASSUMED_YEAR_MARK + inner.strip() if inner.strip() else ""
    try:
        if re.fullmatch(r"\d{4}", raw):
            date(int(raw), 1, 1)
            return raw
        month = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", raw)
        reverse = re.fullmatch(r"(\d{1,2})[-/.](\d{4})", raw)
        if month or reverse:
            year, number = map(int, month.groups() if month else reverse.groups()[::-1])
            date(year, number, 1)
            return f"{year:04d}-{number:02d}"
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        if "T" in candidate or " " in candidate:
            return datetime.fromisoformat(candidate).isoformat()
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
                "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M",
                "%d/%m/%Y %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.isoformat() if "%H" in fmt else parsed.date().isoformat()
        except ValueError:
            pass
    months = month_names
    if months:
        match = re.fullmatch(rf"(?:(\d{{1,2}})\s+)?({_phrase_alt(months)})\s+(\d{{4}})", raw, re.I)
        month_first = re.fullmatch(rf"({_phrase_alt(months)})\s+(\d{{1,2}}),?\s+(\d{{4}})", raw, re.I)
        if month_first:
            name, day, year = month_first.groups()
            try:
                return date(int(year), months[name.casefold()], int(day)).isoformat()
            except ValueError:
                return raw
        if match:
            day, name, year = match.groups()
            month = months[name.casefold()]
            try:
                if day:
                    return date(int(year), month, int(day)).isoformat()
                date(int(year), month, 1)
                return f"{int(year):04d}-{month:02d}"
            except ValueError:
                return raw
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.isoformat() if parsed is not None else raw
    except (TypeError, ValueError, OverflowError):
        return raw

