"""telos adapter — telos_proposals.jsonl → ChangeIntent (CLUSTER-HEAD).

Riscritto 2/7/2026 (mandato Roberto: «utilità, performance, semplicità»):

- Proietta i CLUSTER HEAD (`recompose_clusters`), NON le righe raw:
  ~27 intents invece di ~500, score = `cluster_score` (EA_max + bonus
  convergenza cross-lente — il segnale forte), summary dal miglior membro.
- SKIP dei name_status NON azionabili (`new_invalid`,
  `existing_redundant`, …): il rumore non arriva a /admin/changes.
  Prima il ramo default li mandava TUTTI a create_executor (35% dello
  store era redundant).
- Mapping kind:
    existing_parametric → KIND_EXTEND_EXECUTOR  (applier: edit manifest)
    new_valid*          → KIND_CREATE_EXECUTOR  (applier: synth ~150s)
    existing_pipeline   → KIND_MATERIALIZE_PIPELINE con body
                          {suggested_query, tools_sequence, telos_id,
                           lenses}: l'accept ESEGUE la pipeline UNA volta
                          come turno reale (change_applier) — se funziona,
                          L0/L1 imparano dal turno vero. Regola dei
                          livelli: le pipeline sono territorio della cache,
                          niente depositi paralleli.
  (Il vecchio body {tools_sequence} senza `path_shape_hash` faceva SEMPRE
  fallire l'accept: contratto dell'applier multi_tool, famiglia ritirata.)

Lo state iniziale e' sempre PROPOSED; le decisioni storiche vengono
applicate post-upsert dal materializer.
"""
from __future__ import annotations

from typing import Iterable

from change_intents import (
    KIND_CREATE_EXECUTOR,
    KIND_EXTEND_EXECUTOR,
    KIND_MATERIALIZE_PIPELINE,
    ChangeIntent,
)

from ._base import _iso_from_ts


def iter_telos() -> Iterable[ChangeIntent]:
    try:
        import telos_proposals_store as tps
    except ImportError:
        return
    try:
        rows = tps.load_all(enrich_rows=False, max_rows=10000)
        heads = tps.recompose_clusters(rows)
    except Exception:
        return
    for head in heads:
        if not head:
            continue
        status = head.get("name_status") or ""
        # SOLO gli azionabili (SoT: tps.ACTIONABLE_NAME_STATUS).
        if status not in tps.ACTIONABLE_NAME_STATUS:
            continue
        executor_target = (head.get("executor_target") or "").strip()
        if not executor_target:
            continue
        prop_id = head.get("prop_id", "")
        action = (head.get("proposed_action") or "").strip()
        rationale = head.get("rationale") or ""
        ts = float(head.get("ts") or 0.0)
        lens = head.get("lens") or "unknown"
        lenses = list(head.get("cluster_lenses") or [lens])
        score = float(head.get("cluster_score") or 0.0)
        summary = action.split("\n")[0][:200]

        if status == "existing_parametric":
            kind = KIND_EXTEND_EXECUTOR
            body = {
                "arg_name": (head.get("parametric_arg")
                             or _infer_arg_from_action(action)),
                "arg_value_example": head.get("parametric_value"),
                "lenses": lenses,
                "telos_id": head.get("telos_id"),
            }
        elif status == "existing_pipeline":
            kind = KIND_MATERIALIZE_PIPELINE
            body = {
                # L'accept la ESEGUE come turno: la query suggerita e' la
                # prosa dell'azione proposta (NL, il motore la pianifica).
                "suggested_query": action[:400],
                "tools_sequence": (head.get("pipeline_tools_mentioned")
                                   or [executor_target]),
                "lenses": lenses,
                "telos_id": head.get("telos_id"),
            }
        else:
            # new_valid / new_valid_unique
            kind = KIND_CREATE_EXECUTOR
            body = {
                "name": executor_target,
                "intent_text": summary,
                "lenses": lenses,
                "telos_id": head.get("telos_id"),
            }

        yield ChangeIntent.new(
            origin_family="telos",
            origin_module=lens,
            origin_source_id=prop_id,
            intent_kind=kind,
            intent_target=executor_target,
            intent_summary=summary,
            intent_rationale=rationale,
            intent_body=body,
            score=min(1.0, max(0.0, score)),
            confidence=0.8,
            discovered_at=_iso_from_ts(ts) if ts else None,
        )


def _infer_arg_from_action(action: str) -> str | None:
    """Estrae nome arg da frasi tipo 'aggiungi arg X', 'parametro Y='.
    Best-effort, fallback None."""
    import re
    m = re.search(r"(?:arg|parametro|argomento)\s+([a-z_][a-z0-9_]*)",
                  action.lower())
    if m:
        return m.group(1)
    m = re.search(r"([a-z_][a-z0-9_]*)\s*=", action.lower())
    if m:
        return m.group(1)
    return None
