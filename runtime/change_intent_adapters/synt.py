"""synt adapter — ~/.local/share/metnos/synt_proposals/*.json → ChangeIntent.

I synt_proposals storici sono "executor request" gia' processati dalla
pipeline 5 stage. Importiamo in stato FINALIZED (installed) o
ROLLED_BACK (rejected/abandoned) per preservare l'audit, NON per
ri-proporli.

Mapping:
  - final_state='installed'       → STATE_FINALIZED  (executor attivo)
  - final_state='rejected*'       → STATE_ROLLED_BACK
  - final_state='abandoned*'      → STATE_ROLLED_BACK
  - final_state='in_progress'     → STATE_ACCEPTED (synt in corso)
"""
from __future__ import annotations

import json
from typing import Iterable

import config as C
from change_intents import (
    KIND_CREATE_EXECUTOR,
    STATE_ACCEPTED,
    STATE_FINALIZED,
    STATE_ROLLED_BACK,
    ChangeIntent,
)

from ._base import _iso_from_ts, score_from_synt_state


def iter_synt() -> Iterable[ChangeIntent]:
    proposals_dir = C.PATH_USER_DATA / "synt_proposals"
    if not proposals_dir.is_dir():
        return
    for jf in sorted(proposals_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        prop_id = data.get("id") or jf.stem
        expected_name = data.get("expected_name") or ""
        intent_text = data.get("intent") or ""
        user_query = data.get("user_query") or ""
        ts_start = float(data.get("ts_start") or 0.0)
        final_state = (data.get("final_state") or "").strip()
        name = data.get("name") or expected_name
        abandon_reason = data.get("abandon_reason") or ""

        if not name:
            continue

        score = score_from_synt_state(final_state)

        body = {
            "name": name,
            "expected_name": expected_name,
            "intent_text": intent_text,
            "user_query": user_query,
            "final_state": final_state,
            "abandon_reason": abandon_reason,
            "stages": data.get("stages") or [],
        }

        # Action/object/qualifier dal naming (best-effort)
        parts = name.split("_")
        if len(parts) >= 2:
            body["action"] = parts[0]
            body["object"] = parts[1]
            if len(parts) >= 3:
                body["qualifier"] = parts[2]

        summary = (intent_text or user_query or name).strip().split("\n")[0][:200]

        ci_new = ChangeIntent.new(
            origin_family="synt",
            origin_module="request_new_executor",
            origin_source_id=prop_id,
            intent_kind=KIND_CREATE_EXECUTOR,
            intent_target=name,
            intent_summary=summary,
            intent_rationale=user_query,
            intent_body=body,
            score=score,
            confidence=0.6,
            discovered_at=_iso_from_ts(ts_start) if ts_start else None,
        )
        # Stato dal final_state
        if final_state == "installed":
            ci_new.state = STATE_FINALIZED
        elif final_state.startswith("rejected") or final_state.startswith("abandoned"):
            ci_new.state = STATE_ROLLED_BACK
            ci_new.rolled_back_reason = abandon_reason or final_state
        elif final_state == "in_progress" or not final_state:
            ci_new.state = STATE_ACCEPTED
        yield ci_new
