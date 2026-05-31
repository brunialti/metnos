"""praxis_cluster_merge.py — daily batch consolidation cluster piccoli.

ADR 0162 §"Open questions" — risolve frammentazione cluster nei primi
giorni di vita Praxis. Scheduler v2 daily@03:30.

Pipeline:
  1. SELECT cluster_id GROUP BY HAVING COUNT(*) ≤ MIN_SIZE → cluster piccoli
  2. Per ciascun cluster piccolo, find_neighbors del centroide embedding
  3. Se trova cluster vicino (cosine 0.75-0.90), LLM judge merge
  4. Se SAME → UPDATE obs SET cluster_id = larger_cluster + UPDATE skills

Cap CLUSTER_MERGE_MAX_PAIRS per fire (default 20). Idempotente.
"""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger(__name__)


def run(*, store=None, **kwargs) -> dict:
    """Entry point scheduler v2."""
    t0 = time.monotonic()
    try:
        from praxis_constants import (
            CLUSTER_MERGE_ENABLED, CLUSTER_MERGE_MIN_SIZE,
            CLUSTER_MERGE_MAX_PAIRS, COSINE_HIGH, COSINE_LOW,
        )
        if not CLUSTER_MERGE_ENABLED:
            return {"skipped": True, "reason": "disabled"}
        if store is None:
            from praxis import get_store
            store = get_store()
        import praxis_cluster as pc
        from praxis import _get_middle_llm_call
        llm = _get_middle_llm_call()
    except Exception as ex:
        log.warning("praxis_cluster_merge: import: %r", ex)
        return {"error": str(ex), "merged": 0}

    conn = store.conn
    # 1. Cluster piccoli candidati (size ≤ MIN_SIZE).
    cur = conn.execute(
        "SELECT cluster_id, COUNT(*) as sz FROM observations "
        "WHERE cluster_id IS NOT NULL "
        "GROUP BY cluster_id HAVING sz <= ?", (CLUSTER_MERGE_MIN_SIZE,))
    small_clusters = [r[0] for r in cur.fetchall()]
    if not small_clusters:
        return {"checked": 0, "merged": 0, "elapsed_ms": 0}

    merged = 0
    checked = 0
    for small_cid in small_clusters[:CLUSTER_MERGE_MAX_PAIRS]:
        checked += 1
        # Centroid embedding del cluster piccolo.
        cur = conn.execute(
            "SELECT embedding, intent_sig FROM observations "
            "WHERE cluster_id = ? AND embedding IS NOT NULL LIMIT 1",
            (small_cid,))
        row = cur.fetchone()
        if not row or not row[0]:
            continue
        eb, sig = row[0], row[1]
        # Top-K vicini esclusi same-cluster.
        neighbors = pc.find_neighbors(conn, eb, k=5)
        candidate = None
        for sim, _oid, _osig, ncid in neighbors:
            if ncid == small_cid:
                continue
            if COSINE_LOW <= sim < COSINE_HIGH:
                candidate = (sim, ncid)
                break
        if not candidate:
            continue
        sim, target_cid = candidate
        # LLM judge: same intent?
        # Recupera 3 query proxy del target cluster.
        cur = conn.execute(
            "SELECT intent_sig FROM observations "
            "WHERE cluster_id = ? LIMIT 3", (target_cid,))
        peers = [r[0].replace("|", " ").replace("_", " ").strip()
                 for r in cur.fetchall()]
        query_proxy = sig.replace("|", " ").replace("_", " ").strip()
        same = pc.judge_same_intent(query_proxy, peers, llm)
        if not same:
            continue
        # MERGE: re-assign obs + skills al cluster grande.
        try:
            conn.execute(
                "UPDATE observations SET cluster_id = ? WHERE cluster_id = ?",
                (target_cid, small_cid))
            conn.execute(
                "UPDATE skills SET cluster_id = ? WHERE cluster_id = ?",
                (target_cid, small_cid))
            conn.commit()
            log.info("praxis_cluster_merge: %s → %s (sim=%.2f)",
                      small_cid, target_cid, sim)
            merged += 1
        except Exception as ex:
            log.warning("merge fail %s→%s: %r", small_cid, target_cid, ex)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {"checked": checked, "merged": merged, "elapsed_ms": elapsed_ms}
