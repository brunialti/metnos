"""user_feedback adapter — turn_feedback.jsonl → ChangeIntent.

Aggregates negative feedback (action='error') per request and tool sequence
and proposes a reject_pattern. Only with at least two refusals still standing
(a later ✓ on the same sequence cancels the earlier ✗, as the reader does).

The request is keyed in the form the reader compares
(`turn_feedback.normalized_request`), and the tool sequence is the one the
feedback record itself carries: a proposal without a sequence would have
nothing to avoid, so it is not produced.

Mapping:
  - intent_kind: KIND_REJECT_PATTERN
  - state: STATE_PROPOSED (a formal ban needs a human decision)
"""
from __future__ import annotations

import json
from typing import Iterable

import config as C
from change_intents import (
    KIND_REJECT_PATTERN,
    ChangeIntent,
)
from turn_feedback import normalized_request

from ._base import _iso_from_ts, score_for_reject_pattern


def iter_user_feedback() -> Iterable[ChangeIntent]:
    fpath = C.PATH_USER_DATA / "turn_feedback.jsonl"
    if not fpath.exists():
        return

    # (request, tools) -> refusals still standing, in file (chronological) order
    aggregated: dict[tuple[str, tuple[str, ...]], dict] = {}
    try:
        with fpath.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                action = rec.get("action")
                request = normalized_request(rec.get("user_query") or rec.get("canonical"))
                tools = tuple(str(tool) for tool in (
                    rec.get("rejected_pipeline") or rec.get("approved_pipeline") or ()) if tool)
                if not request or not tools or action not in ("error", "ok"):
                    continue
                key = (request, tools)
                if action == "ok":
                    aggregated.pop(key, None)
                    continue
                ts = float(rec.get("ts") or 0.0)
                agg = aggregated.setdefault(
                    key, {"count": 0, "first_ts": ts, "turn_ids": []})
                agg["count"] += 1
                agg["first_ts"] = min(agg["first_ts"], ts)
                agg["turn_ids"].append(rec.get("turn_id", ""))
    except OSError:
        return

    for (request, tools), agg in aggregated.items():
        if agg["count"] < 2:
            continue  # 1-shot noise
        tools_sig = "→".join(tools)
        body = {
            "canonical_query": request,
            "tools_sequence": list(tools),
            "n_rejections": agg["count"],
            "turn_ids_sample": agg["turn_ids"][:5],
        }
        summary = (
            f"Bandisci pattern «{request}» → {tools_sig} "
            f"(rifiutato {agg['count']} volte)"
        )
        yield ChangeIntent.new(
            origin_family="user",
            origin_module="feedback",
            origin_source_id=f"{request}::{tools_sig}",
            intent_kind=KIND_REJECT_PATTERN,
            intent_target=request,
            intent_summary=summary,
            intent_body=body,
            score=score_for_reject_pattern(agg["count"]),
            confidence=1.0,  # direct user feedback
            discovered_at=_iso_from_ts(agg["first_ts"]) if agg["first_ts"] else None,
        )
