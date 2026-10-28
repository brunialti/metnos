# SPDX-License-Identifier: MIT
"""Values of listed records copied from the page, not rewritten by a model.

A list page shows its records as repeated rows whose fields the browser lays
out on separate lines or table cells (`sites_collection_sources.list_rows`).
The code finds the columns: rows of one shape align field by field; a field
equal in every row and followed by a changing one is that column's label.
The model only indicates, once per list shape, which numbered column holds
each requested field and which one is each row's own code; the code copies
every row's literal value. The shared model is not reproducible over time
(real runs en25c, 22/10/2026: the dates of one list whole at 19:25, month only
at 20:35, same code and pages); a closed choice is short and checked, a
rewritten value is neither.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field as dataclass_field

from time_window_parser import first_observed_date, normalize_observed_date, observed_month_names

_MAX_COLUMNS = 24
_MAX_EXAMPLE_CHARS = 60
_ITEMS = frozenset({"yes", "no"})


def _date(value: str, months: dict | None) -> bool:
    # A month or a day; a bare number is a code even when it reads as a date.
    return (any(ch.isdigit() for ch in value) and not value.isdigit()
            and re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?",
                             normalize_observed_date(value, month_names=months) or "") is not None)


def _amount(value: str) -> bool:
    from record_identity import _number

    return (_number(value) is not None
            and (any(unicodedata.category(ch) == "Sc" for ch in value)
                 or re.search(r"\d[.,]\d{2}(?!\d)", value) is not None))


def kind(value: str, months: dict | None = None) -> str:
    """Closed kind of one field value, from its form only. `months`: the
    catalog's month names, read once by a caller classifying many values."""
    if not value:
        return "empty"
    if _date(value, months):
        return "date"
    if _amount(value):
        return "amount"
    if not re.search(r"\s", value) and re.search(r"\d", value) and len(value) >= 4:
        return "code"
    return "text"


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass
class _Column:
    position: int
    label: str = ""
    prefix: int = 0          # words of a label written in the value's own field
    kind: str = "text"


@dataclass
class _Template:
    shape: tuple
    labels: dict             # position -> folded label text
    columns: list            # offered columns, numbered from 1

    @property
    def signature(self) -> tuple:
        return (self.shape, tuple(sorted(self.labels.items())),
                tuple((c.position, c.prefix) for c in self.columns))


def _template(rows: list[list[str]], months: dict | None = None) -> _Template:
    """Columns of rows of one shape (at least two rows)."""
    shape = tuple(kind(value, months) for value in rows[0])
    constant = [len({_fold(row[i]) for row in rows}) == 1 for i in range(len(shape))]
    labels = {i: _fold(rows[0][i]) for i in range(len(shape) - 1)
              if constant[i] and shape[i] == "text" and not constant[i + 1]}
    columns = []
    for i in range(len(shape)):
        if i in labels:
            continue
        column = _Column(i, label=rows[0][i - 1].rstrip(" :") if i - 1 in labels else "",
                         kind=shape[i])
        if shape[i] == "text" and not constant[i]:
            # «Due 12/03/2030» in one field: the words every row starts with
            # are the label when what follows is a typed value in every row.
            words = [row[i].split() for row in rows]
            common = 0
            while (all(len(w) > common + 1 for w in words)
                   and len({_fold(w[common]) for w in words}) == 1):
                common += 1
            kinds = {kind(" ".join(w[common:]), months) for w in words} if common else set()
            if common and len(kinds) == 1 and kinds <= {"date", "amount", "code"}:
                column.prefix, column.kind = common, kinds.pop()
                column.label = " ".join(words[0][:common]).rstrip(" :")
        if column.kind != "empty":
            columns.append(column)
    return _Template(shape, labels, columns[:_MAX_COLUMNS])


def _value(row: list[str], column: _Column) -> str:
    text = row[column.position]
    return " ".join(text.split()[column.prefix:]) if column.prefix else text


def _matches(template: _Template, row: list[str], months: dict | None = None) -> bool:
    """A single row read with a template learned from other rows: same shape
    and the same labels."""
    return (tuple(kind(value, months) for value in row) == template.shape
            and all(_fold(row[i]) == label for i, label in template.labels.items())
            and all(kind(_value(row, c), months) == c.kind for c in template.columns if c.prefix))


def _choice(template: _Template, rows: list[list[str]], fields: list[str], instruction: str,
            tier: str, date_field, call_llm) -> dict | None:
    """The model's closed choice for one template, checked; None if unusable."""
    import i18n
    import prompt_loader

    columns = []
    for n, column in enumerate(template.columns, 1):
        examples = list(dict.fromkeys(_value(row, column)[:_MAX_EXAMPLE_CHARS] for row in rows
                                      if _value(row, column)))[:3]
        columns.append({"n": n, "label": column.label, "kind": column.kind, "examples": examples})
    try:
        raw, _meta = call_llm(
            json.dumps({"request": instruction, "fields": fields, "columns": columns},
                       ensure_ascii=False),
            prompt_loader.get("extract_entries_list_columns", i18n.current_lang()),
            tier=tier, max_tokens=64 + 12 * len(fields))
        found = re.search(r"\{.*\}", str(raw or ""), re.S)
        answer = json.loads(found.group()) if found else None
    except Exception:  # noqa: BLE001 -- no usable answer leaves the model path
        return None
    if not isinstance(answer, dict) or answer.get("items") not in _ITEMS:
        return None
    if answer["items"] == "no":
        return {"items": "no"}
    chosen, identifier = answer.get("fields"), answer.get("identifier")
    count = len(template.columns)
    if (not isinstance(chosen, dict) or not set(chosen) <= set(fields)
            or any(type(n) is not int or not 0 <= n <= count for n in chosen.values())
            or type(identifier) is not int or not 0 <= identifier <= count):
        return None
    used = [n for n in chosen.values() if n]
    if len(used) != len(set(used)) or len(used) < 2 or (identifier and identifier not in used):
        return None
    for name, n in chosen.items():
        if n and date_field(name) and template.columns[n - 1].kind != "date":
            return None
    if identifier and template.columns[identifier - 1].kind != "code":
        return None
    if identifier and len({_value(row, template.columns[identifier - 1]) for row in rows}) < len(rows):
        # Each row's own code differs from every other row's: a code repeated
        # across rows belongs to their container (a customer, a contract).
        return None
    return {"items": "yes", "fields": {name: n for name, n in chosen.items() if n},
            "identifier": identifier}


def _record(row: list[str], template: _Template, choice: dict, fields: list[str]) -> dict:
    record = {name: "" for name in fields}
    facts, labels = [], {}
    for name, n in choice["fields"].items():
        column = template.columns[n - 1]
        value = _value(row, column)
        record[name] = value
        if column.label:
            labels[name] = column.label
        if value:
            facts.append({"field": name, "value": value, "owner": "self",
                          "role": "identifier" if n == choice["identifier"] else "attribute",
                          "quote": row[column.position]})
    record["_identity_facts"] = facts
    record["_record_extraction"] = "copied"
    if labels:
        record["_record_field_labels"] = labels
    return record


def _has_value(text: str, months: dict | None = None) -> bool:
    """Whether text left beside the copied rows still shows a date (read by
    the shared helper of observed dates) or an amount the model should read."""
    for line in text.splitlines():
        if first_observed_date(line, month_names=months):
            return True
        words = line.split()
        if any(_amount(" ".join(words[n:n + size])) for size in (1, 2)
               for n in range(len(words) - size + 1) if any(ch.isdigit() for ch in words[n])):
            return True
    return False


@dataclass
class Listed:
    records: dict = dataclass_field(default_factory=dict)    # source index -> copied records
    residual: dict = dataclass_field(default_factory=dict)   # source index -> text for the model
    meta: dict = dataclass_field(default_factory=dict)       # closed diagnostics counters


def copy_listed_rows(sources: list, fields: list[str], instruction: str, tier: str,
                     *, call_llm, date_field=lambda _name: False) -> Listed:
    """Copy the requested fields of every bound row of the listed sources.

    Rows of one page are grouped by their shape; a shape seen in two rows or
    more gives a template, a single row is read only with a template of the
    same shape and labels. Sources whose rows are not all copied stay whole
    for the model.
    """
    listed = Listed()
    meta = listed.meta
    months = observed_month_names()

    def note(key, n=1):
        meta[key] = meta.get(key, 0) + n

    pages: dict[str, list] = {}
    for index, source in enumerate(sources):
        rows = source.get("_source_list") if isinstance(source, dict) else None
        if not (isinstance(rows, dict) and isinstance(rows.get("ref"), str)
                and isinstance(rows.get("rows"), list)):
            continue
        text = source.get("text") if isinstance(source.get("text"), str) else ""
        for row in rows["rows"]:
            segments = row.get("segments") if isinstance(row, dict) else None
            start, end = (row.get("start"), row.get("end")) if isinstance(row, dict) else (None, None)
            if (isinstance(segments, list) and segments and all(isinstance(s, str) for s in segments)
                    and type(start) is int and type(end) is int and 0 <= start < end <= len(text)):
                pages.setdefault(rows["ref"], []).append((index, start, end, segments))
    if not pages:
        return listed
    groups, singles, templates = [], [], {}
    for rows in pages.values():
        by_shape: dict[tuple, list] = {}
        for item in rows:
            by_shape.setdefault(tuple(kind(value, months) for value in item[3]), []).append(item)
        for items in by_shape.values():
            (groups if len(items) > 1 else singles).append(items)
    choices: dict[tuple, dict | None] = {}

    def choose(template, rows):
        key = template.signature
        if key not in choices:
            choices[key] = _choice(template, rows, fields, instruction, tier, date_field, call_llm)
            note("list_choice_calls")
            # Diagnostics: the kind of the column each field took, never values.
            choice = choices[key]
            meta.setdefault("list_choices", []).append(
                "unusable" if choice is None else choice["items"] if choice["items"] == "no" else " ".join(
                    f"{name}:{template.columns[n - 1].kind}{'+label' if template.columns[n - 1].label else ''}"
                    f"{'+id' if n == choice['identifier'] else ''}"
                    for name, n in sorted(choice["fields"].items())))
        return choices[key]

    copied: dict[int, list] = {}
    page_labels: dict[tuple, set] = {}
    for items in groups:
        template = _template([item[3] for item in items], months)
        templates.setdefault(template.signature, template)
        choice = choose(template, [item[3] for item in items])
        if choice is None:
            note("list_unusable_choices")
            continue
        if choice["items"] == "no":
            note("list_not_items")
            continue
        for index, start, end, segments in items:
            copied.setdefault(index, []).append((start, end, _record(segments, template, choice, fields)))
        # The same label read as two different fields on one page is declared.
        page = items[0][0]
        for name, n in choice["fields"].items():
            label = template.columns[n - 1].label
            if label:
                page_labels.setdefault((str(sources[page].get("url")), _fold(label)), set()).add(name)
    changes = sum(1 for names in page_labels.values() if len(names) > 1)
    if changes:
        meta["list_choice_changes"] = changes
    for items in singles:
        index, start, end, segments = items[0]
        template = next((t for t in templates.values() if _matches(t, segments, months)), None)
        choice = choices.get(template.signature) if template else None
        if not choice or choice.get("items") != "yes":
            continue
        copied.setdefault(index, []).append((start, end, _record(segments, template, choice, fields)))
        note("list_single_rows")
    for index, rows in copied.items():
        source = sources[index]
        declared = source["_source_list"]["rows"]
        text = source["text"]
        rows.sort(key=lambda item: item[0])
        listed.records[index] = [record for _start, _end, record in rows]
        note("list_copied_rows", len(rows))
        pieces, tail = [], 0
        for start, end, _record_ in rows:
            pieces.append(text[tail:start])
            tail = end
        pieces.append(text[tail:])
        left = "\n".join(piece.strip() for piece in pieces if piece.strip())
        listed.residual[index] = left if _has_value(left, months) else ""
        if len(rows) < len(declared):
            note("list_rows_left_to_model", len(declared) - len(rows))
    return listed
