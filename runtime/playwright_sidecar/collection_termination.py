"""When a collection may stop: the requested year is covered by the page's
own record rows (performance review 21/10/2026: the search went on through
other menus after every invoice had been found; real run am7, ENI 23/10:
34 of 64 observations after each contract's list was complete).

Pure functions. Dates are read with the shared helper of observed dates
(`time_window_parser`), never with patterns of their own or names of a site
or a language. The order of the rows is not required: lists of ENI, Enel and
Fastweb are not read newest first. The caller requires the model to be done
with the list and no next page left.
"""
from __future__ import annotations

from time_window_parser import first_observed_date, observed_month_names


def row_date(text: str, months: dict[str, int] | None = None) -> tuple[int, int] | None:
    """(year, month) of the first date written in a row, or None."""
    value = first_observed_date(text, month_names=observed_month_names() if months is None else months)
    return (int(value[:4]), int(value[5:7])) if value else None


def row_year(text: str, months: dict[str, int] | None = None) -> int | None:
    """The year of the first date written in a row, or None."""
    date = row_date(text, months)
    return date[0] if date else None


def _dated(rows: list[str], months: dict[str, int] | None) -> list[tuple[int, int]]:
    months = observed_month_names() if months is None else months
    return [d for d in (row_date(text, months) for text in rows) if d is not None]


def stream_covers_year(year: int | None, rows: list[str], months: dict[str, int] | None = None) -> bool:
    """Rows of the requested year and an older one: the list reaches past the
    start of the year (rule a)."""
    dated = _dated(rows, months) if year is not None else []
    years = {y for y, _ in dated}
    return len(dated) >= 2 and year in years and min(years) < year


def stream_reaches_year_start(year: int | None, rows: list[str],
                              months: dict[str, int] | None = None) -> bool:
    """Rows of the requested year down to its January, without an older row
    (rule c; Fastweb 21/10: a list of the current year only, January to
    October, with no end marker)."""
    dated = _dated(rows, months) if year is not None else []
    return len(dated) >= 2 and (year, 1) in dated


def _period_context(year: int | None, context) -> bool:
    return (year is not None and isinstance(context, dict) and context.get("current_matches") is True
            and isinstance(context.get("requested"), dict)
            and context["requested"].get("role") == "temporal"
            and str(year) in str(context["requested"].get("text") or ""))


def period_filter_ids(year: int | None, contexts: list[dict]) -> set:
    """The controls of the site's own filter already limiting the list to the
    requested year: temporal contexts of the request, matching now, whose
    requested words name that year (rule b)."""
    return {c.get("id") for c in contexts or () if _period_context(year, c)}


def only_year(year: int | None, rows: list[str], months: dict[str, int] | None = None) -> bool:
    """Every dated row of the list is of the requested year: what a list
    limited to that year shows (a filter reset to another period shows
    other years)."""
    dated = _dated(rows, months) if year is not None else []
    return bool(dated) and all(y == year for y, _ in dated)


def page_rows(records: dict, text: str, months: dict[str, int] | None = None) -> list[str]:
    """The rows to read the stream from: the page's record-bound rows when
    two or more carry a date, otherwise the lines of its text."""
    months = observed_month_names() if months is None else months
    bound = [v.get("text") if isinstance(v, dict) else v for v in (records or {}).values()]
    if sum(row_year(row, months) is not None for row in bound) >= 2:
        return bound
    return str(text or "").splitlines()
