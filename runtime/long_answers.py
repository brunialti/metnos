# SPDX-License-Identifier: MIT
"""A very long answer is delivered as a file, not written whole in the chat.

Roberto, 12/11/2026: a job answered with a table of 500 rows, 147,000
characters, shown whole in the conversation. An answer longer than a person
reads in a chat goes into a file to download, chosen by its nature: a sheet
when it is a table, a text otherwise. The chat keeps the beginning and says
where the rest is (§2.7: what is cut is declared, with shown and total).

One rule for every turn and channel: it is applied where a turn is closed
(``TurnLog.write``), on the message as the person would have read it. The
file rides the turn's ordinary attachments, so the web chat shows a link to
download and Telegram receives a document.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import config as C
from logging_setup import get_logger

log = get_logger(__name__)

# Longer than this, an answer is a document. About a hundred lines.
MAX_CHAT_ANSWER_CHARS = 6000
# Rows a results file may hold. In the chat an answer is cut to what a person
# reads; a file can carry them all (Roberto, 13/11/2026: «128k o 256k»).
FILE_ROWS = 256_000
# What stays in the chat fits one pushed message on every channel.
SHORT_ANSWER_CHARS = 3200
_SHOWN_ROWS = 20
# A file nobody can reach any more is not kept: the links of a turn resolve
# only while the turn is recent.
KEEP_DAYS = 30
# Marks the engine puts around a table cell that must not wrap (private use).
_NOWRAP = ("", "")
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def answers_dir() -> Path:
    return C.PATH_USER_DATA / "answers"


# ---- reading the message ------------------------------------------------------

def _is_row(line: str) -> bool:
    text = line.strip()
    return len(text) >= 2 and text.startswith("|") and text.endswith("|")


def _cells(line: str) -> list[str]:
    """The cells of one table line; ``\\|`` is a bar inside a cell."""
    body = line.strip()[1:-1]
    cells, current, index = [], [], 0
    while index < len(body):
        char = body[index]
        if char == "\\" and body[index + 1:index + 2] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current))
    return [cell.replace(_NOWRAP[0], "").replace(_NOWRAP[1], "").strip() for cell in cells]


def _is_separator(line: str) -> bool:
    if not _is_row(line):
        return False
    cells = _cells(line)
    return bool(cells) and all(
        len(cell) >= 3 and set(cell) <= set("-:") and "-" in cell for cell in cells)


def largest_table(text: str) -> tuple[int, int] | None:
    """Line span ``(first, end)`` of the table with most rows, header and
    separator included, or None when the message holds no table."""
    lines = text.split("\n")
    best, index = None, 0
    while index < len(lines) - 1:
        if _is_row(lines[index]) and _is_separator(lines[index + 1]):
            end = index + 2
            while end < len(lines) and _is_row(lines[end]):
                end += 1
            if best is None or end - index > best[1] - best[0]:
                best = (index, end)
            index = end
        else:
            index += 1
    return best


def _bounded(text: str, limit: int) -> str:
    """The beginning of a prose block, cut at a line or a word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("\n"), head.rfind(" "))
    return (head[:cut] if cut > limit // 2 else head).rstrip() + " …"


# ---- writing the file ---------------------------------------------------------

def _native(value: str):
    """A plain whole number as a number, so the sheet sorts and sums it;
    every other cell stays the text it is."""
    digits = value[1:] if value.startswith("-") else value
    if digits.isascii() and digits.isdigit() and len(digits) <= 15 and (
            digits == "0" or not digits.startswith("0")):
        return int(value)
    return value


def _write_sheet(path: Path, header: list[str], rows: list[list[str]],
                 notes: list[str], *, sheet: str, notes_sheet: str) -> None:
    import openpyxl
    from openpyxl.cell import Cell

    book = openpyxl.Workbook()
    table = book.active
    table.title = sheet[:31] or "Sheet"

    def append(worksheet, values) -> None:
        cells = []
        for value in values:
            cell = Cell(worksheet, value=_native(value))
            # Text is written as text: a cell that begins with «=» is shown,
            # never computed (the same rule as the shared sheet writer).
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cells.append(cell)
        worksheet.append(cells)

    append(table, header)
    for row in rows:
        append(table, row)
    table.freeze_panes = "A2"
    if notes:
        other = book.create_sheet(notes_sheet[:31] or "Notes")
        for line in notes:
            append(other, [line])
    book.save(path)


def _store(name: str, write) -> Path:
    """Write through a temporary file; the final name appears complete."""
    folder = answers_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / name
    staged = folder / f".{name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        write(staged)
        os.chmod(staged, 0o600)
        os.replace(staged, target)
    finally:
        try:
            staged.unlink()
        except OSError:
            pass
    return target


def prune(now: float | None = None) -> int:
    """Remove answer files older than ``KEEP_DAYS``; returns how many."""
    limit = (now if now is not None else time.time()) - KEEP_DAYS * 86400
    removed = 0
    try:
        candidates = list(answers_dir().iterdir())
    except OSError:
        return 0
    for item in candidates:
        try:
            if item.is_file() and item.stat().st_mtime < limit:
                item.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# ---- every result, not only the rows a chat shows -------------------------------

def _records(turn):
    """The records of the last step that produced a list, with the tool that
    produced them, or None. They are what the answer's table was drawn from:
    the chat shows a bounded number of rows, the tool returned them all."""
    for step in reversed(getattr(turn, "steps", None) or []):
        result = getattr(step, "result", None)
        entries = result.get("entries") if isinstance(result, dict) else None
        if (isinstance(entries, list) and entries
                and all(isinstance(entry, dict) for entry in entries)):
            return entries, str(getattr(step, "chosen_tool", "") or "")
    return None


def _all_rows(entries: list, tool: str, header: list[str]) -> list[list[str]] | None:
    """Every record as the row the chat would have shown, same columns; None
    when the records are not what the message's table was drawn from."""
    from engine import executor as engine

    presentation = None
    try:
        from loader import load_catalog
        known = load_catalog().executors.get(tool)
        presentation = getattr(known, "presentation", None)
    except Exception as exc:  # noqa: BLE001 - without a catalog the plain table is used
        log.debug("long_answers: presentation of %s unavailable: %r", tool, exc)
    if isinstance(presentation, dict) and isinstance(presentation.get("list"), dict):
        presentation = {**presentation, "list": {**presentation["list"],
                                                 "max_rows": FILE_ROWS, "max_chars": 10 ** 9}}
    paged = engine._paged_tables.set(False)
    try:
        table = engine._entries_table(entries[:FILE_ROWS], max_rows=FILE_ROWS,
                                      max_chars=10 ** 9, presentation=presentation)
    finally:
        engine._paged_tables.reset(paged)
    lines = table.split("\n")
    if len(lines) < 3 or not _is_row(lines[0]) or not _is_separator(lines[1]) \
            or _cells(lines[0]) != header:
        return None
    return [_cells(line) for line in lines[2:] if _is_row(line)]


# ---- the rule -----------------------------------------------------------------

def _texts() -> dict:
    from messages import get as message

    return {key: message(code) for key, code in (
        ("stem", "MSG_LONG_ANSWER_FILE_STEM"), ("sheet", "MSG_LONG_ANSWER_SHEET"),
        ("notes", "MSG_LONG_ANSWER_NOTES"))}


def _as_sheet(text: str, span: tuple[int, int], token: str, texts: dict, turn=None):
    from messages import get as message

    lines = text.split("\n")
    first, end = span
    header, rows = _cells(lines[first]), [_cells(line) for line in lines[first + 2:end]]
    # The file holds every record the tool returned, not only the rows the
    # message had room for.
    found = _records(turn) if turn is not None else None
    total = len(rows)
    if found is not None and len(found[0]) > len(rows):
        try:
            complete = _all_rows(found[0], found[1], header)
        except Exception as exc:  # noqa: BLE001 - the rows of the message are still there
            log.warning("long_answers: full results not rendered (%r), message rows kept", exc)
            complete = None
        if complete is not None and len(complete) > len(rows):
            rows, total = complete, len(found[0])
    before, after = "\n".join(lines[:first]).strip(), "\n".join(lines[end:]).strip()
    notes = [line for block in (before, after) for line in block.split("\n") if line.strip()]
    name = f"{texts['stem']}-{token}.xlsx"
    path = _store(name, lambda staged: _write_sheet(
        staged, header, rows, notes, sheet=texts["sheet"], notes_sheet=texts["notes"]))
    # In the chat: the outcome in short, not the first rows (Roberto,
    # 13/11/2026). What the tool itself said before and after the table stays.
    notice = (message("MSG_LONG_ANSWER_RESULTS", total=total, name=name) if len(rows) >= total
              else message("MSG_LONG_ANSWER_RESULTS_CAPPED", shown=len(rows), total=total, name=name))
    prose = max(200, (SHORT_ANSWER_CHARS - len(notice)) // 2 - 100)
    short = "\n\n".join(part for part in (
        _bounded(before, prose), notice, _bounded(after, prose)) if part)
    return path, _XLSX_MIME, short


def _as_text(text: str, token: str, texts: dict):
    from messages import get as message

    name = f"{texts['stem']}-{token}.txt"
    path = _store(name, lambda staged: staged.write_text(text, encoding="utf-8"))
    head = text[:SHORT_ANSWER_CHARS]
    head = (head[:head.rfind("\n")] if "\n" in head[SHORT_ANSWER_CHARS // 2:] else head).rstrip()
    notice = message("MSG_LONG_ANSWER_TEXT", shown=len(head), total=len(text), name=name)
    return path, "text/plain", "\n\n".join((head, notice))


def deliver_as_file(turn) -> bool:
    """Move a too-long answer of ``turn`` into a file among its attachments
    and leave its beginning as the message; True when it was done.

    Never raises: a file that cannot be written leaves the beginning with the
    cut declared, which is still better than a wall of text.
    """
    text = str(getattr(turn, "final_message", "") or "")
    if getattr(turn, "final_kind", "") != "answer" or len(text) <= MAX_CHAT_ANSWER_CHARS:
        return False
    from messages import get as message

    token = (str(getattr(turn, "turn_id", "") or "") or uuid.uuid4().hex)[:8]
    try:
        texts = _texts()
        span = largest_table(text)
        made = None
        if span is not None and span[1] - span[0] - 2 > _SHOWN_ROWS:
            try:
                made = _as_sheet(text, span, token, texts, turn)
            except Exception as exc:  # noqa: BLE001 - the text file still holds it all
                log.warning("long_answers: sheet not written (%r), text instead", exc)
        path, mime, short = made or _as_text(text, token, texts)
    except Exception as exc:  # noqa: BLE001 - never the reason a turn is lost
        log.warning("long_answers: answer file not written: %r", exc)
        head = _bounded(text, SHORT_ANSWER_CHARS)
        turn.final_message = "\n\n".join((head, message(
            "MSG_LONG_ANSWER_NO_FILE", shown=len(head), total=len(text))))
        return False
    turn.final_message = short
    turn.attachments = list(getattr(turn, "attachments", None) or []) + [{
        "kind": "file", "path": str(path), "basename": path.name, "mime": mime}]
    prune()
    return True
