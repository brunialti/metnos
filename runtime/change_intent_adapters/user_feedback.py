"""user_feedback adapter — turn_feedback.jsonl → ChangeIntent.

Aggrega i feedback negativi (action='error') per (canonical_query,
pipeline_tools) e propone reject_pattern. Solo se n_rejections >= 2
(evita 1-shot noise).

Mapping:
  - intent_kind: KIND_REJECT_PATTERN
  - state: STATE_PROPOSED (servono approvazione umana per ban formale)

I feedback OK non generano change_intent (sono solo conferme di pattern
gia' esistenti — sarebbe rumore puro).
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

import config as C
from change_intents import (
    KIND_REJECT_PATTERN,
    ChangeIntent,
)

from ._base import _iso_from_ts, score_for_reject_pattern


def iter_user_feedback() -> Iterable[ChangeIntent]:
    fpath = C.PATH_USER_DATA / "turn_feedback.jsonl"
    if not fpath.exists():
        return

    # (canonical, tools_sig) → {count, first_ts, last_ts, turn_ids}
    aggregated: dict[tuple, dict] = defaultdict(
        lambda: {"count": 0, "first_ts": None, "last_ts": None, "turn_ids": []}
    )

    try:
        with fpath.open("r") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("action") != "error":
                    continue
                canonical = rec.get("canonical") or ""
                if not canonical:
                    continue
                # tools_sig: stringa "t1→t2→..." (None se non disponibile)
                effects = rec.get("effects") or []
                tools_sig = None
                for ef in effects:
                    if ef.get("type") == "demote_pipeline":
                        tools = ef.get("tools") or []
                        if tools:
                            tools_sig = "→".join(tools)
                            break
                key = (canonical, tools_sig or "")
                ts = float(rec.get("ts") or 0.0)
                agg = aggregated[key]
                agg["count"] += 1
                agg["last_ts"] = ts if agg["last_ts"] is None else max(agg["last_ts"], ts)
                agg["first_ts"] = ts if agg["first_ts"] is None else min(agg["first_ts"], ts)
                agg["turn_ids"].append(rec.get("turn_id", ""))
    except OSError:
        return

    for (canonical, tools_sig), agg in aggregated.items():
        if agg["count"] < 2:
            continue  # 1-shot noise
        score = score_for_reject_pattern(agg["count"])
        tools = tools_sig.split("→") if tools_sig else []
        body = {
            "canonical_query": canonical,
            "tools_sequence": tools,
            "n_rejections": agg["count"],
            "turn_ids_sample": agg["turn_ids"][:5],
        }
        summary = (
            f"Bandisci pattern «{canonical}» → {tools_sig or '(no pipeline)'} "
            f"(rifiutato {agg['count']} volte)"
        )
        first_ts = agg["first_ts"] or 0.0
        yield ChangeIntent.new(
            origin_family="user",
            origin_module="feedback",
            origin_source_id=f"{canonical}::{tools_sig}",
            intent_kind=KIND_REJECT_PATTERN,
            intent_target=canonical,
            intent_summary=summary,
            intent_body=body,
            score=score,
            confidence=1.0,  # feedback diretto utente
            discovered_at=_iso_from_ts(first_ts) if first_ts else None,
        )
