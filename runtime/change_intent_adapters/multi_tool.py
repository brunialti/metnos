"""multi_tool adapter — multi_tool_paths.sqlite → ChangeIntent.

Pipeline ricorrenti L2 catturate dal fast-path memoization (ADR 0150).

Mapping:
  - state='candidate' → STATE_PROPOSED (osservata, da promuovere?)
  - state='shadow'    → STATE_ACCEPTED (promotion approvata, in valutazione)
  - state='active'    → STATE_OBSERVED (in uso reale, monitoring)
  - state='demoted'   → STATE_ROLLED_BACK

intent_kind: sempre KIND_MATERIALIZE_PIPELINE (cache di una catena).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

import config as C
from change_intents import (
    KIND_MATERIALIZE_PIPELINE,
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


def iter_multi_tool() -> Iterable[ChangeIntent]:
    db = C.DB_MULTI_TOOL_PATHS
    if not db.exists():
        return
    try:
        cn = sqlite3.connect(str(db), timeout=10.0)
        cn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return
    try:
        try:
            rows = cn.execute("""
                SELECT id, canonical_query, tools_sequence, args_shape,
                       path_shape_hash, uses, ok_count, fail_count,
                       ts_first, ts_last, state
                  FROM multi_tool_paths
            """).fetchall()
        except sqlite3.Error:
            return
        for row in rows:
            try:
                tools = json.loads(row["tools_sequence"]) if row["tools_sequence"] else []
                shape = json.loads(row["args_shape"]) if row["args_shape"] else []
            except (ValueError, TypeError):
                continue
            if not tools or len(tools) < 2:
                continue
            uses = int(row["uses"] or 0)
            ok_count = int(row["ok_count"] or 0)
            fail_count = int(row["fail_count"] or 0)
            score = score_from_uses(uses, mid=20)
            target = "→".join(tools)
            body = {
                "tools_sequence": tools,
                "args_shape": shape,
                "path_shape_hash": row["path_shape_hash"],
                "canonical_query": row["canonical_query"],
                "uses": uses,
                "ok_count": ok_count,
                "fail_count": fail_count,
            }
            summary = f"Materializza pipeline {target} (vista {uses} volte)"
            ci_new = ChangeIntent.new(
                origin_family="multi_tool",
                origin_module="L2",
                origin_source_id=row["path_shape_hash"],
                intent_kind=KIND_MATERIALIZE_PIPELINE,
                intent_target=target,
                intent_summary=summary,
                intent_body=body,
                score=score,
                confidence=0.85,
                discovered_at=row["ts_first"],
            )
            ci_new.state = _state_map(row["state"] or "candidate")
            yield ci_new
    finally:
        cn.close()
