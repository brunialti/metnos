# SPDX-License-Identifier: MIT
"""One card for every tool and for every plan, from what really happened.

The turn log already holds, for each step, which tool ran, how it ended and
how long it took, and for each turn how it was served and what the person
said about it.  Nothing read those numbers: a tool was known only by its call
count and by the outcome of its last call.  A card sums them over a window of
days, so that choosing, withdrawing and improving can rest on measures.

Nothing new is collected and nothing is stored: a card is computed from the
log, one day at a time, and a day that did not change is not read again.  A
card holds names and counts only, never what a request or an answer said.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import Counter
from pathlib import Path

import config as C
from logging_setup import get_logger

log = get_logger(__name__)

WINDOW_DAYS = 30
WINDOWS = (7, 30, 90)
_ANSWER = "final_answer"
_THUMBS_UP, _THUMBS_DOWN = "ok", "error"
_TOP_CLASSES = 3
# A tool that raised, or a built-in that returned no proper answer: a fault
# of the code too, like the classes that withdraw a generated tool
# (15/11/2026: they were counted as refusals the tool declared).
_CRASH_CLASSES = frozenset({"exception", "internal_error"})

_lock = threading.Lock()
_days: dict[str, tuple[int, int, list]] = {}


def _digest(text: object) -> str:
    return hashlib.sha256(" ".join(str(text or "").lower().split()).encode("utf-8")).hexdigest()[:16]


def _summary(row: dict) -> dict | None:
    """What a card needs of one turn: names, outcomes and times."""
    if not isinstance(row, dict) or not row.get("turn_id"):
        return None
    steps = []
    for step in row.get("steps") or []:
        if not isinstance(step, dict):
            continue
        name = step.get("chosen_tool")
        result = step.get("result")
        if not name or name == _ANSWER or not isinstance(result, dict) or not result:
            continue
        took = step.get("exec_ms")
        steps.append((str(name), bool(result.get("ok")), str(result.get("error_class") or ""),
                      took if isinstance(took, int) and not isinstance(took, bool) else None))
    started, ended = row.get("ts_start"), row.get("ts_end")
    seconds = None
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)) and ended >= started:
        seconds = float(ended) - float(started)
    calls = row.get("model_calls")
    return {
        "turn": str(row["turn_id"]), "at": float(started) if isinstance(started, (int, float)) else 0.0,
        "steps": steps, "kind": str(row.get("final_kind") or ""),
        "served": str(row.get("match_source") or ""), "seconds": seconds,
        "model_calls": calls if isinstance(calls, int) and not isinstance(calls, bool) else None,
        "false_success": bool(row.get("false_success_detected")),
        "request": _digest(row.get("user_query")),
    }


def _read_day(path: Path) -> list:
    """The turns of one file. A turn written more than once (its record is
    completed as it goes) counts once, as its last line says."""
    summaries: dict[str, dict] = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                summary = _summary(json.loads(line))
            except ValueError:
                continue
            if summary is not None:
                summaries.pop(summary["turn"], None)
                summaries[summary["turn"]] = summary
    return list(summaries.values())


def _day(path: Path) -> list:
    """The turns of one day; read again only when the file changed."""
    try:
        info = path.stat()
    except OSError:
        return []
    key = str(path)
    with _lock:
        known = _days.get(key)
        if known is not None and known[:2] == (info.st_size, info.st_mtime_ns):
            return known[2]
    try:
        summaries = _read_day(path)
    except OSError as exc:
        log.warning("scorecards: %s not read: %r", path.name, exc)
        return []
    with _lock:
        _days[key] = (info.st_size, info.st_mtime_ns, summaries)
    return summaries


def turns(days: int = WINDOW_DAYS, *, now: float | None = None) -> list:
    """The turns of the last ``days`` days, oldest first."""
    folder = Path(C.PATH_TURNS)
    if not folder.is_dir():
        return []
    since = (time.time() if now is None else now) - int(days) * 86400
    first = time.strftime("%Y-%m-%d", time.gmtime(since - 86400))
    found = []
    for path in sorted(folder.glob("*.jsonl")):
        if path.stem >= first:
            found.extend(turn for turn in _day(path) if turn["at"] >= since)
    return found


def _thumbs() -> dict[str, str]:
    """The last thing the person said about each turn."""
    said: dict[str, str] = {}
    try:
        from turn_feedback import FEEDBACK_PATH

        with Path(FEEDBACK_PATH).open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("turn_id"):
                    said[str(record["turn_id"])] = str(record.get("action") or "")
    except OSError:
        pass
    return said


def _middle(values, share: float = 0.5):
    """The value below which ``share`` of the measures fall (nearest rank)."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    return ordered[min(len(ordered), max(1, math.ceil(share * len(ordered)))) - 1]


def _day_of(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(at)) if at else ""


def tool_cards(days: int = WINDOW_DAYS, *, now: float | None = None) -> list[dict]:
    """One card per tool that ran in the window, the most used first.

    ``faults`` are failures of the tool's own code (the classes that withdraw
    a generated tool, and a crash); ``refusals`` are the failures it declared
    itself.
    """
    from generated_rewrite import FAULT_CLASSES

    said = _thumbs()
    cards: dict[str, dict] = {}
    for turn in turns(days, now=now):
        seen = set()
        for name, ok, error_class, took in turn["steps"]:
            card = cards.setdefault(name, {
                "name": name, "calls": 0, "ok": 0, "refusals": 0, "faults": 0, "classes": Counter(),
                "times": [], "turns": 0, "thumbs_up": 0, "thumbs_down": 0, "false_successes": 0,
                "last": 0.0})
            card["calls"] += 1
            if ok:
                card["ok"] += 1
            elif error_class in FAULT_CLASSES or error_class in _CRASH_CLASSES:
                card["faults"] += 1
            else:
                card["refusals"] += 1
            if not ok and error_class:
                card["classes"][error_class] += 1
            if took is not None:
                card["times"].append(took)
            card["last"] = max(card["last"], turn["at"])
            seen.add(name)
        for name in seen:
            card = cards[name]
            card["turns"] += 1
            card["thumbs_up"] += said.get(turn["turn"]) == _THUMBS_UP
            card["thumbs_down"] += said.get(turn["turn"]) == _THUMBS_DOWN
            card["false_successes"] += turn["false_success"]
    out = []
    for card in cards.values():
        times = card.pop("times")
        classes = card.pop("classes")
        card.update(ms_typical=_middle(times), ms_slow=_middle(times, 0.9),
                    failure_classes=classes.most_common(_TOP_CLASSES),
                    last_used=_day_of(card.pop("last")))
        out.append(card)
    return sorted(out, key=lambda card: (-card["calls"], card["name"]))


def plan_cards(days: int = WINDOW_DAYS, *, now: float | None = None) -> list[dict]:
    """One card per sequence of tools that served a turn, the most used first.

    ``outside_s`` is the time of a turn spent outside its tools: understanding
    the request, planning, writing the answer.  It is what a remembered plan
    saves, and what no tool can make shorter.
    """
    said = _thumbs()
    cards: dict[tuple, dict] = {}
    for turn in turns(days, now=now):
        tools = tuple(name for name, _ok, _class, _took in turn["steps"])
        if not tools:
            continue
        card = cards.setdefault(tools, {
            "tools": list(tools), "turns": 0, "answered": 0, "failed_steps": 0, "served": Counter(),
            "seconds": [], "inside": [], "model_calls": [], "requests": set(),
            "thumbs_up": 0, "thumbs_down": 0, "last": 0.0})
        card["turns"] += 1
        card["answered"] += turn["kind"] == "answer"
        card["failed_steps"] += any(not ok for _name, ok, _class, _took in turn["steps"])
        card["served"][turn["served"] or "-"] += 1
        inside = sum(took for _name, _ok, _class, took in turn["steps"] if took is not None) / 1000
        if turn["seconds"] is not None:
            card["seconds"].append(turn["seconds"])
            card["inside"].append(min(inside, turn["seconds"]))
        card["model_calls"].append(turn["model_calls"])
        card["requests"].add(turn["request"])
        card["thumbs_up"] += said.get(turn["turn"]) == _THUMBS_UP
        card["thumbs_down"] += said.get(turn["turn"]) == _THUMBS_DOWN
        card["last"] = max(card["last"], turn["at"])
    out = []
    for card in cards.values():
        seconds, inside = card.pop("seconds"), card.pop("inside")
        typical = _middle(seconds)
        within = _middle(inside)
        card.update(
            seconds_typical=None if typical is None else round(typical, 1),
            outside_s=None if typical is None or within is None else round(max(0.0, typical - within), 1),
            model_calls=_middle(card.pop("model_calls")),
            distinct_requests=len(card.pop("requests")),
            served=dict(card.pop("served").most_common()),
            last_used=_day_of(card.pop("last")))
        out.append(card)
    return sorted(out, key=lambda card: (-card["turns"], card["tools"]))
