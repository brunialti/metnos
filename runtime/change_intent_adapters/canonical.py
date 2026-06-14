"""canonical adapter — mnest.sqlite::canonical_query_log → ChangeIntent.

Single-tool fast-path L1 (ADR 0149+0150).

Mapping:
  - state='candidate' → STATE_PROPOSED
  - state='shadow'    → STATE_ACCEPTED
  - state='active'    → STATE_OBSERVED
  - state='demoted'   → STATE_ROLLED_BACK

intent_kind: sempre KIND_CACHE_PATTERN (query → tool mapping).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

import config as C
from change_intents import (
    KIND_CACHE_PATTERN,
    STATE_ACCEPTED,
    STATE_OBSERVED,
    STATE_PROPOSED,
    STATE_ROLLED_BACK,
    ChangeIntent,
)

from ._base import score_from_uses


def _state_map(legacy: str) -> str:
    return {
        "candidate": STATE_PROPOSED,
        "shadow":    STATE_ACCEPTED,
        "active":    STATE_OBSERVED,
        "demoted":   STATE_ROLLED_BACK,
    }.get(legacy, STATE_PROPOSED)


def iter_canonical() -> Iterable[ChangeIntent]:
    db = C.DB_MNESTOMA
    if not db.exists():
        return
    try:
        cn = sqlite3.connect(str(db), timeout=10.0)
        cn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return
    try:
        # Tabella puo' non esistere se mnest e' fresh
        try:
            rows = cn.execute("""
                SELECT id, canonical_query, tool_name, args_shape,
                       uses, ok_count, fail_count, ts_first, ts_last, state
                  FROM canonical_query_log
            """).fetchall()
        except sqlite3.Error:
            return
        for row in rows:
            try:
                shape = json.loads(row["args_shape"]) if row["args_shape"] else {}
            except (ValueError, TypeError):
                shape = {}
            canonical = row["canonical_query"] or ""
            tool_name = row["tool_name"] or ""
            if not canonical or not tool_name:
                continue
            uses = int(row["uses"] or 0)
            score = score_from_uses(uses, mid=15)
            body = {
                "canonical_query": canonical,
                "tool_name": tool_name,
                "args_shape": shape,
                "uses": uses,
                "ok_count": int(row["ok_count"] or 0),
                "fail_count": int(row["fail_count"] or 0),
            }
            summary = f"Mappa query «{canonical}» → {tool_name} (vista {uses} volte)"
            ci_new = ChangeIntent.new(
                origin_family="canonical",
                origin_module="L1",
                origin_source_id=str(row["id"]),
                intent_kind=KIND_CACHE_PATTERN,
                intent_target=tool_name,
                intent_summary=summary,
                intent_body=body,
                score=score,
                confidence=0.9,
                discovered_at=row["ts_first"],
            )
            ci_new.state = _state_map(row["state"] or "candidate")
            yield ci_new
    finally:
        cn.close()
