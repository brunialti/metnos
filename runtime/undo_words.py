# SPDX-License-Identifier: MIT
"""What an «undo» did, said in the person's words (§2.8).

The trusted broker (`undo_last_turn`) reverses every operation of the
request, most recent first, and reports each one in ``details``: the stages
of the closed reverse patterns with the rows each one put back or could not,
or only the counts of a tool's own ``reverse``. The answer used to be
«Undone. Items restored: N», N being the rows of the last operation alone,
and a partial undo was told only by its first failure (17/11/2026: two files
back, two folders left, «Items restored: 2»).

This module reads those details and says what came back (files back where
they were, folders the request had created removed, created files removed,
files restored, other items) and what stayed as it is, with why. It knows
the shapes the closed reverse patterns declare, never a tool by name; what
it cannot classify is counted as items, never left out.
"""
from __future__ import annotations

import re

_FILES_BACK = "MSG_UNDO_PART_FILES_BACK"
_FOLDERS_REMOVED = "MSG_UNDO_PART_FOLDERS_REMOVED"
_FILES_REMOVED = "MSG_UNDO_PART_FILES_REMOVED"
_FILES_RESTORED = "MSG_UNDO_PART_FILES_RESTORED"
_ITEMS = "MSG_UNDO_PART_ITEMS"
_PARTS = (_FILES_BACK, _FOLDERS_REMOVED, _FILES_REMOVED, _FILES_RESTORED, _ITEMS)

# Why a row stayed as it was, by the code its reverse declared.
_CAUSES = {
    "ERR_FS_FOLDER_NOT_EMPTY": "MSG_UNDO_CAUSE_NOT_EMPTY",
    "ERR_FS_SOURCE_CHANGED": "MSG_UNDO_CAUSE_CHANGED",
    "ERR_FS_SOURCE_MISSING": "MSG_UNDO_CAUSE_GONE",
    "ERR_FS_DEST_EXISTS": "MSG_UNDO_CAUSE_OCCUPIED",
    "ERR_FS_PERMISSION_REQUIRED": "MSG_UNDO_CAUSE_PERMISSION",
}
# A detail in one of these states did not put everything back.
_NOT_ALL_BACK = frozenset({"partial", "failed", "skipped", "error"})
_CODE = re.compile(r"ERR_[A-Z0-9_]+")
# Rows named one by one; the others are counted.
_LEFT_SHOWN = 5


def _say(key: str, lang: str | None, **values) -> str:
    if lang:
        import i18n

        return i18n.get_for_language(key, lang, **values)
    from messages import get

    return get(key, **values)


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _came_back(pattern: str, row: dict) -> str:
    """What one row put back is, from the pattern that put it back."""
    if pattern == "swap_src_dst":
        if row.get("removed") or row.get("deleted"):
            return _FILES_REMOVED          # the copy a copy-move had made
        if row.get("src") and row.get("dst") and not ({"account", "uid"} & row.keys()):
            return _FILES_BACK
        return _ITEMS
    if pattern == "delete_created_dirs":
        return _FOLDERS_REMOVED
    if pattern == "delete_created_paths":
        kind = row.get("kind")
        # The closed operation removes a folder ("removed") or a file
        # ("deleted"); the native pattern says which in ``kind``.
        if kind == "dir" or (kind is None and row.get("removed") and not row.get("deleted")):
            return _FOLDERS_REMOVED
        return _FILES_REMOVED
    if pattern == "restore_blob_backup" and row.get("path") and "account" not in row:
        return _FILES_RESTORED
    return _ITEMS


def _cause(row: dict, lang: str | None) -> str:
    text = str(row.get("error") or "")
    code = str(row.get("error_code") or (text if _CODE.fullmatch(text) else ""))
    if code in _CAUSES:
        return _say(_CAUSES[code], lang)
    if _CODE.fullmatch(text):
        return _say(text, lang)
    return text or (_say(code, lang) if code else _say("MSG_UNDO_CAUSE_UNKNOWN", lang))


def _left_row(row: dict, lang: str | None) -> str:
    path = row.get("path") or row.get("dst") or row.get("src")
    cause = _cause(row, lang)
    if isinstance(path, str) and path:
        return _say("MSG_UNDO_LEFT_ITEM", lang, path=re.split(r"[\\/]", path.rstrip("/\\"))[-1], cause=cause)
    return _say("MSG_UNDO_LEFT_ACTION", lang, cause=cause)


def _reason_of(detail: dict, stages: list[dict]) -> str:
    """The first words a detail gives for not having put everything back."""
    for text in (detail.get("reverse_error"), detail.get("reason"),
                 *((stage.get("result") or {}).get("error") for stage in stages
                   if isinstance(stage.get("result"), dict))):
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def tally(observation: dict, lang: str | None = None) -> tuple[dict, list[str]]:
    """``({part key: count}, [what stayed, in words])`` of one undo."""
    counts = dict.fromkeys(_PARTS, 0)
    left: list[str] = []
    for detail in _rows(observation.get("details")):
        stages = _rows(detail.get("stages"))
        said = False
        for stage in stages:
            result = stage.get("result") if isinstance(stage.get("result"), dict) else {}
            pattern = str(stage.get("pattern") or "")
            done = _rows(result.get("results"))
            for row in done:
                counts[_came_back(pattern, row)] += 1
            if not done and isinstance(result.get("ok_count"), int) and result["ok_count"] > 0:
                counts[_ITEMS] += result["ok_count"]   # a reverse that only counts
            remaining = result.get("_left_counts") or {}
            if remaining.get("time") or remaining.get("uncertain") or sum(remaining.values()) > _LEFT_SHOWN:
                for cause, count in remaining.items():
                    if count:
                        left.append(_say("MSG_UNDO_LEFT_" + cause.upper(), lang, count=count))
                        said = True
            else:
                for row in _rows(result.get("failed")):
                    left.append(_left_row(row, lang))
                    said = True
        if not stages:
            # A tool's own reverse reports counts only.
            counts[_ITEMS] += int(detail.get("ok_count") or 0)
        if detail.get("status") in _NOT_ALL_BACK and not said:
            reason = _reason_of(detail, stages)
            left.append(_left_row({"error": reason}, lang))
    return counts, left


def describe(observation: dict, lang: str | None = None) -> str:
    """The answer to «annulla»: what came back, then what stayed and why."""
    counts, left = tally(observation, lang)
    done = "; ".join(
        _say(key + "_ONE", lang) if counts[key] == 1 else _say(key, lang, count=counts[key])
        for key in _PARTS if counts[key])
    stayed = "; ".join(left[:_LEFT_SHOWN])
    if len(left) > _LEFT_SHOWN:
        stayed += " " + _say("MSG_UNDO_LEFT_MORE", lang, count=len(left) - _LEFT_SHOWN)
    resumable = any(any((stage.get("result") or {}).get("_left_counts", {}).get(key) for key in ("time", "uncertain"))
                    for detail in _rows(observation.get("details"))
                    for stage in _rows(detail.get("stages")))
    again = " " + _say("MSG_UNDO_CONTINUE", lang) if resumable else ""
    if done and stayed:
        return _say("MSG_UNDO_REQUEST_PARTIAL", lang, done=done, left=stayed) + again
    if done:
        return _say("MSG_UNDO_REQUEST_DONE", lang, done=done)
    if stayed:
        return _say("MSG_UNDO_REQUEST_NONE", lang, left=stayed) + again
    return _say("MSG_UNDO_ALREADY", lang)
