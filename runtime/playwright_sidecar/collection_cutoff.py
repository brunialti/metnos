"""Conservative, source-witnessed invoice year boundary; never archive completeness."""
from __future__ import annotations

import datetime as dt
import re
import json
import detection_lexicon as _dl

# Read-only: typed invoice dates supply a logical or physical stream witness.
# IDs must be unique; unrelated navigation is never assigned to this stream.
OBSERVE_JS = r"""() => {
  const invoiceDate = new RegExp(__INVOICE_DATE_PATTERN__, "i");
  const result = [];
  const visible = el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return !el.hidden && s.display !== 'none' && s.visibility === 'visible'
      && Number.parseFloat(s.opacity || '1') >= .05 && r.width > 1 && r.height > 1;
  };
  for (const table of document.querySelectorAll('table[id]')) {
    if (!visible(table) || document.querySelectorAll('[id="'+CSS.escape(table.id)+'"]').length !== 1) continue;
    const headers = Array.from(table.querySelectorAll('thead th'));
    const dates = headers.filter(h => invoiceDate.test((h.innerText || '').trim()));
    if (dates.length !== 1) continue;
    const declared = dates[0].getAttribute('aria-sort');
    if (declared && declared !== 'descending') continue;
    const header = dates[0], column = headers.indexOf(header);
    if (headers.some(h => h.colSpan !== 1 || h.rowSpan !== 1)) continue;
    const rows = [];
    let invalid = false;
    for (const row of table.querySelectorAll('tbody > tr')) {
      if (!visible(row)) continue;
      const cells = Array.from(row.children);
      if (cells.some(c => c.colSpan !== 1 || c.rowSpan !== 1) || !cells[column] || !visible(cells[column])) { invalid = true; break; }
      const ids = Array.from(row.querySelectorAll('[data-metnos-action-id]')).map(el => el.getAttribute('data-metnos-action-id'));
      const text = (cells[column].innerText || '').trim();
      const identity = (row.innerText || '').trim();
      rows.push({date: text, identity, actions: ids});
    }
    const continuations = Array.from(document.querySelectorAll('[aria-controls]'))
      .filter(el => visible(el) && (el.getAttribute('aria-controls') || '').split(/\s+/).includes(table.id))
      .map(el => el.getAttribute('data-metnos-action-id')).filter(Boolean);
    if (!invalid && rows.length) result.push({id: table.id, schema: 'table_invoice_date', order: declared === 'descending' ? 'invoice_date_descending' : 'physical_sequence', rows, continuations});
  }
  for (const list of document.querySelectorAll('ul[id],ol[id],[role=list][id]')) {
    if (!visible(list) || document.querySelectorAll('[id="'+CSS.escape(list.id)+'"]').length !== 1) continue;
    const rows = []; let invalid = false;
    for (const row of Array.from(list.children)) {
      if (!visible(row)) continue;
      const fields = Array.from(row.querySelectorAll('dt')).filter(el => invoiceDate.test((el.innerText || '').trim()));
      if (fields.length !== 1 || !fields[0].nextElementSibling || fields[0].nextElementSibling.tagName !== 'DD') {invalid = true; break;}
      const field = fields[0].nextElementSibling;
      if (!visible(field)) {invalid = true; break;}
      rows.push({date: (field.innerText || '').trim(), identity: (row.innerText || '').trim(),
        actions: Array.from(row.querySelectorAll('[data-metnos-action-id]')).map(el => el.getAttribute('data-metnos-action-id'))});
    }
    const continuations = Array.from(document.querySelectorAll('[aria-controls]'))
      .filter(el => visible(el) && (el.getAttribute('aria-controls') || '').split(/\s+/).includes(list.id))
      .map(el => el.getAttribute('data-metnos-action-id')).filter(Boolean);
    if (!invalid && rows.length) result.push({id: list.id, schema: 'list_invoice_date', order: 'physical_sequence', rows, continuations});
  }
  return result;
}"""


def observation_script() -> str:
    """Use the same translatable recognition registry as other site detectors."""
    patterns = _dl.forms("sites.invoice_date_field")
    # Missing translations must not broaden a typed date into a generic date.
    pattern = "|".join("(?:" + value + ")" for value in patterns) or "(?!)"
    return OBSERVE_JS.replace("__INVOICE_DATE_PATTERN__", json.dumps(pattern))


def target_year(target: str) -> int | None:
    # A mentioned year may be an excluded year or an interval boundary.
    # Ambiguous natural-language ranges retain the ordinary exhaustive search.
    if _dl.match("sites.invoice_year_range", target):
        return None
    years = set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", target))
    if len(years) != 1 or not _dl.match("sites.invoice_object", target):
        return None
    return int(next(iter(years)))


def invoice_date(value: object) -> dt.date | None:
    """The day a cell writes, read by the shared helper of observed dates
    (any written form of a whole day); a month, a year or a bare number is
    no day."""
    from time_window_parser import normalize_observed_date

    if not isinstance(value, str) or not value.strip() or value.strip().isdigit():
        return None
    day = normalize_observed_date(value.strip())
    try:
        return dt.date.fromisoformat(day) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") else None
    except ValueError:
        return None



def _valid_rows(rows: object) -> bool:
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        return False
    dates = [invoice_date(row.get("date")) for row in rows]
    identities = [row.get("identity") for row in rows]
    return (all(date is not None for date in dates)
            and all(isinstance(identity, str) and identity for identity in identities)
            and len(set(identities)) == len(identities)
            and all(a >= b for a, b in zip(dates, dates[1:])))

def boundary(target: str, streams: object) -> dict[str, str]:
    """Return action ID -> bounded stream, after validating every observed row.

    Callers retain all observed source text. Removing older detail actions and
    an explicitly associated continuation ends only this ordered stream.
    """
    year = target_year(target)
    if year is None or not isinstance(streams, list):
        return {}
    excluded = {}
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("order") not in {"invoice_date_descending", "physical_sequence"}:
            continue
        rows = stream.get("rows")
        if not stream.get("id") or not isinstance(rows, list) or not rows:
            continue
        if any(not isinstance(row, dict) for row in rows):
            continue
        dates = [invoice_date(row.get("date")) for row in rows]
        identities = [row.get("identity") for row in rows]
        # A partially readable, duplicate or non-monotone page cannot prove a boundary.
        if (any(date is None for date in dates) or any(not isinstance(i, str) or not i for i in identities)
                or len(set(identities)) != len(identities)
                or any(a < b for a, b in zip(dates, dates[1:]))):
            continue
        target_seen = False
        cutoff = None
        for index, date in enumerate(dates):
            if date.year == year:
                target_seen = True
            elif date.year < year and target_seen:
                cutoff = index
                break
        if cutoff is None:
            continue
        ids = [action for row in rows[cutoff:] for action in row.get("actions", [])]
        ids.extend(stream.get("continuations", []))
        for action in ids:
            if isinstance(action, str) and action:
                excluded[action] = stream["id"]
    return excluded


def carried_boundary(target: str, streams: list, previous: list) -> dict[str, str]:
    """Join only caller-verified continuations; emitted IDs belong to current page."""
    result = boundary(target, streams)
    invalid_streams = set()
    for current in streams:
        # Validate BEFORE overlap removal: dedup must not hide an inversion,
        # duplicated row or unreadable date in the full current observation.
        if not isinstance(current, dict) or not _valid_rows(current.get("rows")):
            continue
        for old in previous:
            if not isinstance(old, dict) or old.get("id") != current.get("id"):
                continue
            if (not _valid_rows(old.get("rows")) or old.get("order") != current.get("order")
                    or old.get("schema") != current.get("schema")):
                invalid_streams.add(current["id"])
                continue
            rows = list(old.get("rows", []))
            known = {row.get("identity"): row.get("date") for row in rows}
            valid = True
            for row in current.get("rows", []):
                identity = row.get("identity")
                if identity in known:
                    if known[identity] != row.get("date"):
                        valid = False; break
                    continue
                rows.append(row)
            if not valid or not _valid_rows(rows):
                invalid_streams.add(current["id"])
                continue
            joined = {**current, "rows": rows}
            allowed = {a for row in current.get("rows", []) for a in row.get("actions", [])}
            allowed.update(current.get("continuations", []))
            result.update({a: sid for a, sid in boundary(target, [joined]).items() if a in allowed})
    return {action: stream for action, stream in result.items() if stream not in invalid_streams}
