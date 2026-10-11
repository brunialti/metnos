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


__all__ = ["TEXT_PREFIX", "active_cell_count", "is_active_text", "neutralized",
           "neutralized_rows"]
