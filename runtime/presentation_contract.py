"""Optional manifest-declared presentation contracts.

The contract is metadata only in this phase: loaders expose it to consumers,
while executors without the section keep the historical renderer unchanged.
Malformed contracts fail closed (``{}``) instead of rejecting an otherwise
valid executor.
"""
from __future__ import annotations

from copy import deepcopy


_MODES = frozenset({"table", "cards", "bullet"})
_OVERFLOW = frozenset({"notice", "paginate", "download"})
_MAX_ROWS = 10_000
_MAX_CHARS = 1_000_000
_MAX_CELL = 10_000
_VIEWS = frozenset({"list"})
_COLUMN_TYPES = frozenset({"string", "number", "boolean", "json", "date", "datetime"})


def validate_presentation(manifest: dict | None = None) -> list[str]:
    """Return structural errors for an explicitly declared contract.

    Missing presentation remains a legacy migration state.  A present but
    malformed declaration is an admission error for standard executors.
    """
    raw = manifest.get("presentation") if isinstance(manifest, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["[presentation] must be a table"]
    default_view = raw.get("default_view")
    if default_view not in _VIEWS:
        return ["presentation.default_view must be one of: list"]
    if default_view not in raw or not isinstance(raw.get(default_view), dict):
        return [f"presentation.{default_view} must be declared"]
    return [] if normalize_presentation(manifest) else [
        "presentation.list has an invalid mode, column, or budget"]


def normalize_presentation(manifest: dict | None = None) -> dict:
    """Return a bounded presentation contract or an empty dict.

    This deliberately does not infer a contract from the executor name or
    output schema. Declaration is opt-in and therefore safe to roll out.
    """
    raw = manifest.get("presentation") if isinstance(manifest, dict) else None
    if not isinstance(raw, dict):
        return {}
    default_view = raw.get("default_view")
    if default_view not in _VIEWS:
        return {}
    listing = raw.get("list")
    if not isinstance(listing, dict):
        return {}
    mode = listing.get("mode", "table")
    if mode not in _MODES:
        return {}
    columns = listing.get("columns")
    if not isinstance(columns, list) or not columns:
        return {}
    normalized = []
    for column in columns:
        if not isinstance(column, dict):
            return {}
        key = column.get("key")
        source = column.get("source", key)
        if not isinstance(key, str) or not key.strip():
            return {}
        if not isinstance(source, (str, list)):
            return {}
        if isinstance(source, list) and not all(isinstance(x, str) for x in source):
            return {}
        item = {"key": key.strip(), "source": deepcopy(source)}
        for name in ("type", "fallback", "label_key", "derived_by"):
            value = column.get(name)
            if value is not None and not isinstance(value, str):
                return {}
            if name == "type" and value is not None and value not in _COLUMN_TYPES:
                return {}
            if value is not None:
                item[name] = value
        cell_max = column.get("cell_max", 180)
        if not isinstance(cell_max, int) or isinstance(cell_max, bool) \
                or not 1 <= cell_max <= _MAX_CELL:
            return {}
        item["cell_max"] = cell_max
        nowrap = column.get("nowrap", False)
        if not isinstance(nowrap, bool):
            return {}
        if nowrap:
            item["nowrap"] = True
        normalized.append(item)
    max_rows = listing.get("max_rows", 200)
    max_chars = listing.get("max_chars", 16_000)
    overflow = listing.get("overflow", "notice")
    if (not isinstance(max_rows, int) or isinstance(max_rows, bool)
            or not 1 <= max_rows <= _MAX_ROWS
            or not isinstance(max_chars, int) or isinstance(max_chars, bool)
            or not 1 <= max_chars <= _MAX_CHARS
            or overflow not in _OVERFLOW):
        return {}
    return {
        "default_view": default_view,
        "list": {
            "mode": mode,
            "columns": normalized,
            "max_rows": max_rows,
            "max_chars": max_chars,
            "overflow": overflow,
        }
    }
