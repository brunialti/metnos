"""telos adapter — telos_proposals.jsonl → ChangeIntent.

Mapping:
  - name_status='new_valid_unique'     → KIND_CREATE_EXECUTOR
  - name_status='existing_parametric'  → KIND_EXTEND_EXECUTOR
  - name_status='existing_pipeline'    → KIND_MATERIALIZE_PIPELINE
  - default (no status)                → KIND_CREATE_EXECUTOR (fallback)

Lo state iniziale e' sempre PROPOSED. Le decisioni utente storiche
gia' presenti in telos_decisions.jsonl vengono applicate post-upsert
dal materializer (via lookup separato), per mantenere l'adapter puro.
"""
from __future__ import annotations

from typing import Iterable

from change_intents import (
    KIND_CREATE_EXECUTOR,
    KIND_EXTEND_EXECUTOR,
    KIND_MATERIALIZE_PIPELINE,
    ChangeIntent,
)

from ._base import _iso_from_ts, score_from_ea


def iter_telos() -> Iterable[ChangeIntent]:
    try:
        import telos_proposals_store as tps
    except ImportError:
        return
    try:
        rows = tps.load_all(enrich_rows=False, max_rows=10000)
    except Exception:
        return
    for payload in rows:
        # load_all ritorna dict (record JSONL + decision)
        if not payload:
            continue
        prop_id = payload.get("prop_id", "")
        executor_target = (payload.get("executor_target") or "").strip()
        if not executor_target:
            continue
        action = payload.get("proposed_action") or ""
        rationale = payload.get("rationale") or ""
        ts = float(payload.get("ts") or 0.0)
        lens = payload.get("lens") or "unknown"
        ea = score_from_ea(payload.get("expected_alignment"))

        name_status = payload.get("name_status") or "new_valid_unique"

        if name_status == "existing_parametric":
            kind = KIND_EXTEND_EXECUTOR
            arg_name = payload.get("parametric_arg") or _infer_arg_from_action(action)
            body = {
                "arg_name": arg_name,
                "arg_value_example": payload.get("parametric_value"),
                "lens": lens,
                "telos_id": payload.get("telos_id"),
                "operator": payload.get("operator"),
            }
            summary = action.strip().split("\n")[0][:200]
        elif name_status == "existing_pipeline":
            kind = KIND_MATERIALIZE_PIPELINE
            body = {
                "tools_sequence": payload.get("pipeline_tools") or [executor_target],
                "lens": lens,
                "telos_id": payload.get("telos_id"),
            }
            summary = action.strip().split("\n")[0][:200]
        else:
            # new_valid_unique (default)
            kind = KIND_CREATE_EXECUTOR
            body = {
                "name": executor_target,
                "action": _verb_from_name(executor_target),
                "object": _object_from_name(executor_target),
                "qualifier": _qualifier_from_name(executor_target),
                "lens": lens,
                "telos_id": payload.get("telos_id"),
                "operator": payload.get("operator"),
            }
            summary = action.strip().split("\n")[0][:200]

        yield ChangeIntent.new(
            origin_family="telos",
            origin_module=lens,
            origin_source_id=prop_id,
            intent_kind=kind,
            intent_target=executor_target,
            intent_summary=summary,
            intent_rationale=rationale,
            intent_body=body,
            score=ea,
            confidence=0.8,
            discovered_at=_iso_from_ts(ts) if ts else None,
        )


def _verb_from_name(name: str) -> str | None:
    parts = name.split("_")
    return parts[0] if parts else None


def _object_from_name(name: str) -> str | None:
    parts = name.split("_")
    return parts[1] if len(parts) >= 2 else None


def _qualifier_from_name(name: str) -> str | None:
    parts = name.split("_")
    return parts[2] if len(parts) >= 3 else None


def _infer_arg_from_action(action: str) -> str | None:
    """Estrae nome arg da frasi tipo 'aggiungi arg X', 'parametro Y='.
    Best-effort, fallback None."""
    import re
    m = re.search(r"(?:arg|parametro|argomento)\s+([a-z_][a-z0-9_]*)", action.lower())
    if m:
        return m.group(1)
    m = re.search(r"([a-z_][a-z0-9_]*)\s*=", action.lower())
    if m:
        return m.group(1)
    return None
