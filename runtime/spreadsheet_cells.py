# SPDX-License-Identifier: MIT
"""Data never becomes a spreadsheet formula.

Spreadsheet programs evaluate text that starts with ``=``, ``+``, ``-`` or
``@`` (and a leading tab or carriage return) when they read CSV, and Google
Sheets does so for values sent as ``USER_ENTERED``. Values extracted from web
pages or files can therefore run a formula when the sheet is opened. Formats
that store typed text keep such values as text; formats that interpret what
they read receive them behind the standard text prefix. Typed numbers and
plain numeric text are left untouched. Pure: no I/O.
"""
from __future__ import annotations

import re
import unicodedata

TEXT_PREFIX = "'"
_ALWAYS_ACTIVE = ("=", "@", "\t", "\r")
_SIGNED = ("+", "-")
# A sign followed only by digits and digit-group separators is a number, not a
# formula, in every spreadsheet program; anything else after a sign may be one.
_PLAIN_NUMBER = re.compile(r"[+-]\d+(?:[.,]\d+)*")


def _after_format_characters(text: str) -> str:
    # A decoder or a program may drop leading invisible format characters
    # (Unicode category Cf: byte order mark, zero-width marks), exposing what
    # follows as the first character of the cell.
    index = 0
    while index < len(text) and unicodedata.category(text[index]) == "Cf":
        index += 1
    return text[index:]


def is_active_text(value) -> bool:
    """True when a spreadsheet program could evaluate this text as a formula."""
    if not isinstance(value, str) or not value:
        return False
    visible = _after_format_characters(value)
    if visible.startswith(_ALWAYS_ACTIVE):
        return True
    return visible.startswith(_SIGNED) and not _PLAIN_NUMBER.fullmatch(visible)


def neutralized(value):
    """The original text behind the prefix: shown, never evaluated."""
    return TEXT_PREFIX + value if is_active_text(value) else value


def active_cell_count(rows) -> int:
    """How many cells ``neutralized_rows`` changes, for an honest receipt."""
    return sum(is_active_text(value)
               for row in rows or []
               for value in (row if isinstance(row, (list, tuple)) else [row]))


def neutralized_rows(rows) -> list:
    """Rows for formats that interpret text: CSV and Google ``USER_ENTERED``."""
    return [[neutralized(value) for value in row]
            if isinstance(row, (list, tuple)) else neutralized(row)
            for row in rows or []]


def _is_label(value) -> bool:
    text = str(value).strip() if value is not None else ""
    return bool(text) and not re.fullmatch(r"[\d.,/:\-\s]+", text)


def header_records(rows) -> list | None:
    """Rows under a header row, as records keyed by the header's labels.

    Filters, classifiers and writers work on named fields, so a sheet whose
    first row names its columns is read as records; ``None`` for a sheet
    without such a row (empty, a label missing or repeated, a number or a
    date in it), which stays rows.  Rows with no value are not records.
    """
    rows = [list(row) for row in rows or [] if isinstance(row, (list, tuple))]
    if not rows or not all(_is_label(cell) for cell in rows[0]):
        return None
    header = [str(cell).strip() for cell in rows[0]]
    if len(set(header)) != len(header):
        return None
    return [dict(zip(header, row + [None] * (len(header) - len(row))))
            for row in rows[1:]
            if any(cell not in (None, "") for cell in row)]


__all__ = ["TEXT_PREFIX", "active_cell_count", "header_records", "is_active_text",
           "neutralized", "neutralized_rows"]
