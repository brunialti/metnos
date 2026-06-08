"""introvertiva adapter — proposals_state.db → ChangeIntent.

Mapping:
  - kind='dedupe'     → KIND_DEDUPE_EXECUTORS (unifica due tool equivalenti)
  - kind='generalize' → KIND_EXTEND_EXECUTOR (generalizza varianti in 1 tool)
  - kind='specialize' → KIND_EXTEND_EXECUTOR (default arg ad un tool)

Sig_key shape (dal generatore introvertiva, vedi runtime/introvertiva.py):
  - dedupe:    `["dedupe", reason, tool_a, tool_b]`
  - generalize: `["generalize", [tool_list]]` (lista di tool della pipeline)
  - specialize: `["specialize", tool_name, arg_name, arg_value]`

State legacy → state canonical:
  - 'pending'   → PROPOSED
  - 'applied'   → FINALIZED (gia' attuato nel vecchio lifecycle)
  - 'dormant'   → STAGED
  - 'rejected'  → REJECTED
  - 'blocked'   → REJECTED (con reason='blocked')
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

import proposals_state as ps
from change_intents import (
    KIND_CACHE_PATTERN,
    KIND_DEDUPE_EXECUTORS,
    KIND_EXTEND_EXECUTOR,
    STATE_FINALIZED,
    STATE_PROPOSED,
    STATE_REJECTED,
    STATE_STAGED,
    ChangeIntent,
)


def _state_legacy_to_canonical(s: str) -> str:
    return {
        "pending": STATE_PROPOSED,
        "applied": STATE_FINALIZED,
        "dormant": STATE_STAGED,
        "rejected": STATE_REJECTED,
        "blocked": STATE_REJECTED,
    }.get(s, STATE_PROPOSED)


def iter_introvertiva() -> Iterable[ChangeIntent]:
    try:
        cn = ps._open()
    except Exception:
        return
    try:
        rows = cn.execute("""
            SELECT sig_key, kind, state, first_seen, last_seen,
                   last_uses, n_seen, last_action
              FROM proposals_state
        """).fetchall()
    except sqlite3.Error:
        return
    finally:
        cn.close()

    for row in rows:
        sig_key, kind, state, first_seen, last_seen, last_uses, n_seen, last_action = row
        try:
            tools = json.loads(sig_key) if sig_key else []
        except (ValueError, TypeError):
            tools = []
        if not tools:
            continue

        from ._base import score_from_n_seen
        score = score_from_n_seen(int(n_seen or 0), int(last_uses or 0))

        # sig_key[0] e' la kind label letterale ("dedupe"/"generalize"/
        # "specialize"). Lo skippiamo: i dati semantici partono da [1:].
        if not tools or str(tools[0]) != kind:
            continue
        payload = tools[1:]

        if kind == "dedupe":
            # payload = [reason, a, b] (3 elementi)
            if len(payload) < 3:
                continue
            reason, a, b = payload[0], payload[1], payload[2]
            if not isinstance(a, str) or not isinstance(b, str) or a == b:
                continue
            intent_kind = KIND_DEDUPE_EXECUTORS
            target = b
            body = {"a": a, "b": b, "reason": reason}
            summary = f"Unifica {a} con {b} (motivo: {reason}, visto {n_seen} volte)"
        elif kind == "generalize":
            # payload = [[tool_list]] — un singolo array di tool della pipeline
            tool_list: list = []
            if payload and isinstance(payload[0], list):
                tool_list = [str(t) for t in payload[0] if t]
            if len(tool_list) < 2:
                continue
            parent = tool_list[0]
            intent_kind = KIND_EXTEND_EXECUTOR
            target = parent
            body = {
                "arg_name": "_generalized",
                "variants_observed": tool_list[1:],
                "n_variants": len(tool_list) - 1,
            }
            summary = (f"Generalizza {parent} con arg che copre "
                       f"{len(tool_list)-1} varianti osservate")
        elif kind == "specialize":
            # payload = [tool_name, arg_name, arg_value]
            if len(payload) < 3:
                continue
            tool_name, arg_name, arg_value_raw = payload[0], payload[1], payload[2]
            if not isinstance(tool_name, str) or not isinstance(arg_name, str):
                continue
            intent_kind = KIND_EXTEND_EXECUTOR
            target = tool_name
            body = {
                "arg_name": arg_name,
                "arg_value_observed": arg_value_raw,
                "uses_observed": int(last_uses or 0),
            }
            summary = (f"Estendi {tool_name} con default {arg_name}="
                       f"{arg_value_raw} (visto {last_uses} volte)")
        else:
            continue

        ci_new = ChangeIntent.new(
            origin_family="introvertiva",
            origin_module=kind,
            origin_source_id=sig_key,
            intent_kind=intent_kind,
            intent_target=target,
            intent_summary=summary,
            intent_body=body,
            score=score,
            confidence=0.7,
            discovered_at=first_seen,
        )
        # Sovrascrivi stato dal legacy
        ci_new.state = _state_legacy_to_canonical(state or "pending")
        yield ci_new
