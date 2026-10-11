# SPDX-License-Identifier: MIT
"""Deterministic records -> typed workbook; no provider or filesystem I/O."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import math
import re

from tabular_projection import compile_projection, _cell, _transform
from tabular.model import Capabilities, Cell, Column, Sheet, TableError, Workbook
from tabular_records import SOURCE_METADATA_FIELDS, merge_source_metadata


@dataclass(frozen=True)
class CompiledTable:
    workbook: Workbook
    notices: tuple[dict, ...]
    data_rows: int
    available_total: int
    truncations: tuple[dict, ...]
    source_metadata: tuple[dict, ...] = ()


def _clean(value, sensitive: set[str]):
    # Nested records can otherwise leak private metadata through JSON cells.
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TableError("invalid_record_key")
        if value.get("sensitive") is True:
            return None
        return {key: _clean(item, sensitive) for key, item in value.items()
                if isinstance(key, str) and not key.startswith("_") and key not in sensitive}
    if isinstance(value, (list, tuple)):
        return [_clean(item, sensitive) for item in value]
    return value


def _sensitive_fields(*declarations: dict) -> set[str]:
    fields = set()
    for declaration in declarations:
        items = declaration.get("sensitive_fields", [])
        if not isinstance(items, (list, tuple)) or any(not isinstance(item, str) for item in items):
            raise TableError("invalid_sensitive_fields")
        fields.update(items)
    return fields


def _source_metadata(table: dict, source: dict, sensitive: set[str]) -> dict:
    declared = {key: table[key] if key in table else source[key]
                for key in SOURCE_METADATA_FIELDS if key in table or key in source}
    return _clean(declared, sensitive)


def _source_is_partial(metadata: dict) -> bool:
    return (any(metadata.get(key) is True for key in
                ("partial", "collection_partial", "truncated"))
            or bool(metadata.get("partial_reason")))


def _text(value) -> str:
    value = _cell(value)
    return "" if value is None else str(value)


def _number(value, locale: str, currency: str, *, monetary: bool):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        raw = str(value)
    elif isinstance(value, str):
        raw = value.strip()
        if monetary:
            # Only currency symbols and the explicitly declared code are syntax.
            if currency:
                raw = re.sub(rf"^(?:{currency})\s*|\s*(?:{currency})$", "", raw)
            raw = raw.strip()
            symbol = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}.get(currency)
            if raw and symbol and raw[0] == symbol:
                raw = raw[1:].strip()
            if raw and symbol and raw[-1] == symbol:
                raw = raw[:-1].strip()
        language = locale.lower().replace("_", "-").split("-")[0]
        if language == "it":
            if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?", raw):
                return None
            raw = raw.replace(".", "").replace(",", ".")
        elif language == "en":
            if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", raw):
                return None
            raw = raw.replace(",", "")
        elif locale or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
            return None
    else:
        return None
    try:
        number = Decimal(raw)
        # Excel/Sheets only preserve 15 significant decimal digits. Do not
        # silently round identifiers or high precision amounts into numbers.
        digits = number.as_tuple().digits
        significant = len(digits)
        while significant > 1 and digits[significant - 1] == 0:
            significant -= 1
        if not number.is_finite() or significant > 15:
            return None
        if number == number.to_integral() and abs(number) <= 999_999_999_999_999:
            return int(number)
        result = float(number)
        return result if math.isfinite(result) and not (number and result == 0) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _convert(value, kind: str | None, locale: str, currency: str, month_names: dict) -> tuple[Cell, bool]:
    if value is None or value == "":
        return Cell("blank", None), False
    if kind is None:
        # Legacy primitive values remain primitive; no inference from strings.
        kind = ("boolean" if isinstance(value, bool) else "number"
                if isinstance(value, (int, float, Decimal)) else "datetime"
                if isinstance(value, datetime) else "date" if isinstance(value, date) else "text")
    if kind in {"number", "currency"}:
        parsed = _number(value, locale, currency, monetary=kind == "currency")
        if parsed is not None:
            return Cell(kind, parsed), False
    elif kind in {"date", "datetime"}:
        # Reuse the observed-date normalizer; month/year precision and assumed
        # years stay text. No invented day, timezone or midnight.
        from observed_dates import normalize_observed_date
        raw = value.isoformat() if isinstance(value, (date, datetime)) else _text(value)
        normalized = normalize_observed_date(raw, month_names=month_names)
        try:
            if kind == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
                return Cell(kind, date.fromisoformat(normalized).isoformat()), False
            if kind == "datetime" and "T" in normalized:
                parsed = datetime.fromisoformat(normalized)
                # openpyxl/Excel persist milliseconds. Preserve finer precision
                # as text rather than rounding a declared datetime silently.
                if parsed.tzinfo is None and parsed.microsecond % 1000 == 0:
                    return Cell(kind, parsed.isoformat()), False
        except (ValueError, TypeError):
            pass
    elif kind == "boolean":
        if isinstance(value, bool):
            return Cell("boolean", value), False
    elif kind in {"text", "url"}:
        return Cell(kind, _text(value)), False
    return Cell("text", _text(value)), True


def _sheet_name(raw: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\\x00-\x1f]", "_", str(raw)).strip(" '")[:31] or "Sheet1"
    name = base
    number = 2
    while name.casefold() in used:
        suffix = f"_{number}"
        name = base[:31-len(suffix)] + suffix
        number += 1
    used.add(name.casefold())
    return name


def _positive_limit(spec: dict, key: str, maximum: int) -> int:
    value = spec.get(key, maximum)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise TableError(f"invalid_{key}")
    return value or maximum


def compile_workbook(source, spec: dict | None, capabilities: Capabilities, *, month_names: dict | None = None) -> CompiledTable:
    """Compile one or several tables; preserve identity and every duplicate.

    ``source`` is a record/matrix list or an envelope with entries/values,
    provenance, declared producer limits and sensitive_fields. Multi-sheet sources use
    ``sheets`` envelopes paired with the corresponding ``spec.sheets``.
    The explicit ``locale`` governs numeric strings; no value guesses it.
    """
    spec = spec or {}
    if not isinstance(spec, dict):
        raise TableError("invalid_spec")
    source = {"entries": source} if isinstance(source, list) else source
    if not isinstance(source, dict):
        raise TableError("invalid_source")
    sources = source.get("sheets", [source])
    specs = spec.get("sheets", [spec] * len(sources)) if isinstance(sources, list) else []
    if not sources or not isinstance(specs, list) or len(sources) != len(specs):
        raise TableError("invalid_sheets")
    if not capabilities.multi_sheet and len(sources) > 1:
        raise TableError("backend_does_not_support_multiple_data_sheets")
    locale = spec.get("locale", "")
    if not isinstance(locale, str):
        raise TableError("invalid_locale")
    notices: list[dict] = []
    truncations: list[dict] = []
    source_metadata: list[dict] = []
    sheets = []
    names: set[str] = set()
    available_total = data_rows = cells_used = 0
    for index, (table, table_spec) in enumerate(zip(sources, specs), 1):
        if not isinstance(table, dict) or not isinstance(table_spec, dict):
            raise TableError("invalid_sheet_source")
        if table_spec.get("locale", locale) != locale:
            raise TableError("sheet_locale_not_supported")
        name = _sheet_name(table_spec.get("name", table_spec.get("sheet_name", f"Sheet{index}")), names)
        sensitive = _sensitive_fields(source, spec, table, table_spec)
        raw = table.get("values", table.get("entries", []))
        if not isinstance(raw, list):
            raise TableError("invalid_rows")
        explicit = table_spec.get("column_specs") or []
        if not isinstance(explicit, list) or any(not isinstance(item, dict) for item in explicit):
            raise TableError("invalid_column_specs")
        sensitive.update(item.get("source") for item in explicit if item.get("sensitive") is True)
        if any(item.get("source") in sensitive for item in explicit):
            raise TableError("sensitive_column_requested")
        columns_arg = table_spec.get("columns", [])
        if not isinstance(columns_arg, list):
            raise TableError("invalid_columns")
        has_header = table_spec.get("has_header", True)
        if not isinstance(has_header, bool):
            raise TableError("invalid_has_header")
        is_records = all(isinstance(row, dict) for row in raw)
        is_matrix = all(isinstance(row, (list, tuple)) for row in raw)
        if raw and not is_records and not is_matrix:
            raise TableError("mixed_record_and_matrix_rows")
        if is_records and raw:
            records = [_clean(row, sensitive) for row in raw]
            if any(not isinstance(record, dict) for record in records):
                raise TableError("sensitive_record_requested")
            if columns_arg and not explicit:
                # Semantic resolution may consult the runtime lexicon. The API
                # resolves it first; this pure layer consumes exact sources.
                raise TableError("column_specs_required_for_record_headers")
            projections = compile_projection(records, columns_arg, explicit, table_spec.get("field_roles"))
            columns = []
            kinds = []
            for position, projection in enumerate(projections):
                declaration = explicit[position] if explicit else {}
                kinds.append(declaration.get("type"))
                columns.append(Column(projection.header, projection.source, projection.transform,
                                      declaration.get("type", "text"), declaration.get("currency", "")))
            values = [[_cell(_transform(record.get(col.source), col.transform)) for col in columns]
                      for record in records]
        else:
            headers = [item.get("header", "") for item in explicit] if explicit else list(columns_arg)
            values = [_clean(list(row), sensitive) for row in raw]
            if not headers and values:
                headers = [f"{position + 1}" for position in range(max(map(len, values)))]
            if any(not isinstance(header, str) or not header for header in headers):
                raise TableError("invalid_headers")
            columns = [Column(header, str(position),
                              type=explicit[position].get("type", "text") if explicit else "text",
                              currency=explicit[position].get("currency", "") if explicit else "")
                       for position, header in enumerate(headers)]
            kinds = [item.get("type") for item in explicit] if explicit else [None] * len(columns)
            # Every supplied matrix row is data. Header equality is not
            # authority to discard a row; legacy callers adapt at their edge.
            if any(len(row) != len(columns) for row in values):
                raise TableError("invalid_row_width")
        if values and not columns:
            raise TableError("empty_rows_without_schema")
        max_cols = _positive_limit(table_spec, "max_columns", capabilities.max_columns)
        header_rows = int(has_header and bool(columns))
        max_rows = _positive_limit(table_spec, "max_rows", capabilities.max_rows - header_rows)
        max_chars = _positive_limit(table_spec, "max_cell_chars", capabilities.max_cell_chars)
        available_total += len(values)
        original_width = len(columns)
        if original_width > max_cols:
            truncations.append({"sheet": name, "truncated_what": "columns", "available_total": original_width,
                                "used": max_cols, "cap_field": "max_columns", "cap_value": max_cols})
            columns, kinds = columns[:max_cols], kinds[:max_cols]
        cell_row_cap = max(0, (capabilities.max_cells - cells_used) // max(1, len(columns)) - header_rows)
        row_cap = min(max_rows, cell_row_cap)
        if len(values) > row_cap:
            truncations.append({"sheet": name, "truncated_what": "rows", "available_total": len(values),
                                "used": row_cap, "cap_field": "max_rows" if row_cap == max_rows else "max_cells",
                                "cap_value": max_rows if row_cap == max_rows else capabilities.max_cells})
        rows = []
        failures: Counter = Counter()
        long_cells = 0
        for row in values[:row_cap]:
            cells = []
            for position, col in enumerate(columns):
                cell, failed = _convert(row[position], kinds[position], locale, col.currency, month_names or {})
                if failed:
                    failures[col.header] += 1
                if isinstance(cell.value, str) and len(cell.value) > max_chars:
                    long_cells += 1
                    cell = Cell("text", cell.value[:max_chars])
                cells.append(cell)
            rows.append(tuple(cells))
        # Headers are text data and obey the same physical cell limit.
        long_headers = sum(len(col.header) > max_chars for col in columns) if has_header else 0
        long_cells += long_headers
        columns = [Column(col.header[:max_chars], col.source, col.transform, col.type, col.currency)
                   for col in columns]
        for header, count in failures.items():
            notices.append({"code": "WARN_TABLE_UNINTERPRETED", "sheet": name, "column": header, "count": count})
        for position, col in enumerate(columns):
            if rows and all(row[position].type == "blank" for row in rows):
                notices.append({"code": "WARN_TABLE_EMPTY_COLUMN", "sheet": name, "column": col.header, "count": len(rows)})
        duplicates = len(rows) - len(set(json.dumps([cell.__dict__ for cell in row], sort_keys=True) for row in rows))
        if duplicates:
            notices.append({"code": "WARN_TABLE_IDENTICAL_ROWS", "sheet": name, "count": duplicates})
        if long_cells:
            truncations.append({"sheet": name, "truncated_what": "cell_characters", "used": max_chars,
                                "available_total": None, "count": long_cells,
                                "cap_field": "max_cell_chars", "cap_value": max_chars})
        try:
            evidence = merge_source_metadata(source.get("source_metadata"),
                                             table.get("source_metadata"))
        except ValueError:
            raise TableError("invalid_source_metadata") from None
        has_runtime_evidence = bool(evidence)
        metadata = _source_metadata(table, source, sensitive)
        if metadata:
            evidence.insert(0, metadata)
        for item in evidence:
            source_metadata.append({"sheet": name, **item})
            if has_runtime_evidence and item is metadata:
                # Flat compatibility fields remain in the receipt, but the
                # runtime envelope owns user-facing evidence for this source.
                continue
            if _source_is_partial(item):
                # Closed runtime evidence remains separate from row data and
                # from the writer's counts/limits. A complete child operation
                # can never cancel an incomplete upstream collection.
                runtime_evidence = "producer" in item
                detail = ("" if runtime_evidence else item
                          if any(key != "partial_reason" for key in item) else item["partial_reason"])
                notice = {"code": "WARN_TABLE_PARTIAL_SOURCE", "sheet": name,
                          "detail": _text(detail)}
                if runtime_evidence:
                    notice["reason_code"] = item.get("reason_code", "")
                notices.append(notice)
            if item.get("provenance"):
                notices.append({"code": "MSG_TABLE_PROVENANCE", "sheet": name,
                                "detail": item["provenance"]["url"]})
        provenance = table.get("provenance", source.get("provenance"))
        if provenance:
            notices.append({"code": "MSG_TABLE_PROVENANCE", "sheet": name,
                            "detail": _text(_clean(provenance, sensitive))})
        if capabilities.types == frozenset({"text"}) and any(cell.type not in {"text", "url", "blank"} for row in rows for cell in row):
            notices.append({"code": "WARN_TABLE_TEXT_BACKEND", "sheet": name, "count": len(rows)})
        if not raw:
            notices.append({"code": "WARN_TABLE_EMPTY_SOURCE", "sheet": name, "count": 0})
        data_rows += len(rows)
        cells_used += (len(rows) + header_rows) * len(columns)
        sheets.append(Sheet(name, tuple(columns), tuple(rows), has_header=has_header))
    for truncation in truncations:
        notices.append({"code": "WARN_TABLE_TRUNCATED", **truncation})
    return CompiledTable(Workbook(str(spec.get("title", "spreadsheet")), tuple(sheets)),
                         tuple(notices), data_rows, available_total, tuple(truncations),
                         tuple(source_metadata))
