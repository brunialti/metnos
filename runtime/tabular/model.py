# SPDX-License-Identifier: MIT
"""Versioned, JSON-compatible table contract; no filesystem or provider I/O.

There is deliberately no formula cell. Neither a planner-supplied type nor a
dictionary extracted from a document can grant runtime formula authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from decimal import Decimal
from typing import Any

CONTRACT_VERSION = 1
COLUMN_TYPES = frozenset({"text", "number", "currency", "date", "datetime", "boolean", "url"})
CELL_TYPES = COLUMN_TYPES | {"blank"}
TRANSFORMS = frozenset({"identity", "basename", "dirname"})


class TableError(ValueError):
    """Machine-readable failure; the public API localizes it."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Cell:
    type: str
    value: str | int | float | bool | None

    def __post_init__(self) -> None:
        if self.type not in CELL_TYPES:
            raise TableError("invalid_cell_type")
        if self.type == "blank":
            valid = self.value is None
        elif self.type in {"number", "currency"}:
            try:
                valid = (isinstance(self.value, (int, float)) and not isinstance(self.value, bool)
                         and math.isfinite(self.value))
            except OverflowError:
                valid = False
            if valid:
                digits = Decimal(str(self.value)).as_tuple().digits
                significant = len(digits)
                while significant > 1 and digits[significant - 1] == 0:
                    significant -= 1
                valid = significant <= 15
        elif self.type == "boolean":
            valid = isinstance(self.value, bool)
        else:
            valid = isinstance(self.value, str)
        if not valid:
            raise TableError("invalid_cell_value")
        if self.type == "date":
            from datetime import date
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.value):
                raise TableError("invalid_date_cell")
            date.fromisoformat(self.value)
        if self.type == "datetime":
            from datetime import datetime
            if "T" not in self.value:
                raise TableError("invalid_datetime_cell")
            parsed = datetime.fromisoformat(self.value)
            if parsed.tzinfo is not None:
                raise TableError("timezone_requires_explicit_conversion")
            if parsed.microsecond % 1000:
                raise TableError("datetime_precision_not_supported")


@dataclass(frozen=True)
class Column:
    header: str
    source: str
    transform: str = "identity"
    type: str = "text"
    currency: str = ""

    def __post_init__(self) -> None:
        if (not isinstance(self.header, str) or not self.header
                or not isinstance(self.source, str) or not self.source or self.source.startswith("_")
                or self.transform not in TRANSFORMS or self.type not in COLUMN_TYPES):
            raise TableError("invalid_column")
        if self.currency and not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise TableError("invalid_currency")


@dataclass(frozen=True)
class Sheet:
    name: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...]
    notes: bool = False
    has_header: bool = True

    def __post_init__(self) -> None:
        if (not isinstance(self.name, str) or not self.name or len(self.name) > 31
                or re.search(r"[\[\]:*?/\\\x00-\x1f]", self.name)
                or not isinstance(self.notes, bool)
                or not isinstance(self.has_header, bool)
                or any(not isinstance(column, Column) for column in self.columns)
                or any(not isinstance(cell, Cell) for row in self.rows for cell in row)):
            raise TableError("invalid_sheet_name")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise TableError("invalid_row_width")


@dataclass(frozen=True)
class Workbook:
    title: str
    sheets: tuple[Sheet, ...]
    version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.version != CONTRACT_VERSION or not isinstance(self.title, str) or not self.sheets:
            raise TableError("unsupported_workbook")
        names = [sheet.name.casefold() for sheet in self.sheets]
        if len(set(names)) != len(names):
            raise TableError("duplicate_sheet_names")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Workbook:
        if not isinstance(raw, dict) or set(raw) - {"title", "sheets", "version"}:
            raise TableError("invalid_workbook")
        try:
            sheets = []
            for sheet in raw["sheets"]:
                if set(sheet) - {"name", "columns", "rows", "notes", "has_header"}:
                    raise TableError("invalid_sheet")
                sheets.append(Sheet(
                    sheet["name"], tuple(Column(**col) for col in sheet["columns"]),
                    tuple(tuple(Cell(**cell) for cell in row) for row in sheet["rows"]),
                    sheet.get("notes", False),
                    sheet.get("has_header", True),
                ))
            return cls(raw["title"], tuple(sheets), raw.get("version", 0))
        except (KeyError, TypeError) as exc:
            raise TableError("invalid_workbook") from exc


@dataclass(frozen=True)
class Capabilities:
    backend: str
    types: frozenset[str]
    multi_sheet: bool
    styles: bool
    max_rows: int
    max_columns: int
    max_cell_chars: int
    max_cells: int
    modes: frozenset[str]


CAPABILITIES = {
    "local_xlsx": Capabilities("local_xlsx", COLUMN_TYPES, True, True,
                               1_048_576, 16_384, 32_767, 5_000_000,
                               frozenset({"create", "overwrite", "append"})),
    "csv": Capabilities("csv", frozenset({"text"}), False, False,
                        1_048_576, 16_384, 32_767, 5_000_000,
                        frozenset({"create", "overwrite", "append"})),
    "google_sheets": Capabilities("google_sheets", COLUMN_TYPES, True, True,
                                  1_000_000, 18_278, 50_000, 10_000_000,
                                  frozenset({"create"})),
}
