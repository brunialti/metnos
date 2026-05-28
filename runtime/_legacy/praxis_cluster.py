"""praxis_cluster.py — semantic cache & champion/challenger per Praxis (ADR 0161 ext, 26/5/2026).

Sostituisce bucket discreto `(verb, object[, keywords])` con cluster emergente
via embedding multilingua + cosine + LLM judge zona grigia (pattern ClusterLLM,
Zhang 2023; GPTCache-style retrieval, Bang 2023).

Pipeline assign_cluster (deterministic-first, §7.9):
  1. cosine top-1 ≥ HIGH → riusa cluster_id     (no LLM)
  2. cosine top-1 < LOW  → nuovo cluster         (no LLM)
  3. zona grigia LOW..HIGH → LLM judge (Gemma 26B middle) su top-3 peers

Selezione darwiniana (champion vs challenger):
  swap_score = 2·(challenger.succ - champion.succ)
              + (champion.lat - challenger.lat) / champion.lat
  swap se score > 0.15  AND  challenger.uses ≥ 1.
  reversibile: 2 ✗ consecutive del nuovo champion → swap-back.

Tunabile via env:
  METNOS_CLUSTER_COSINE_HIGH (default 0.90)
  METNOS_CLUSTER_COSINE_LOW  (default 0.75)
  METNOS_CLUSTER_K           (default 10)
  METNOS_CLUSTER_SWAP_THR    (default 0.15)
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from typing import Optional, Callable

import numpy as np

log = logging.getLogger(__name__)

from praxis_constants import (
    COSINE_HIGH, COSINE_LOW, K_NEIGHBORS, SWAP_THR,
    W_SUCCESS, W_SPEED, W_ALIGNMENT, MAX_LATENCY_MS, EMA_ALPHA,
)

_EMBEDDER = None  # lazy: BGEEmbeddingService | False (unavailable) | None (uninit)


# ── Embedding ─────────────────────────────────────────────────────────────

def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is False:
        return None
    if _EMBEDDER is None:
        try:
            from bge_embedding import BGEEmbeddingService
            _EMBEDDER = BGEEmbeddingService()
        except Exception as ex:
            log.info("praxis_cluster: BGE non disponibile: %r", ex)
            _EMBEDDER = False
            return None
    return _EMBEDDER


def embed(query: str) -> Optional[bytes]:
    """BGE-M3 query → float32 1024d → bytes (4096B). None se BGE non disponibile."""
    if not query or not query.strip():
        return None
    emb = _get_embedder()
    if emb is None:
        return None
    try:
        vec = emb.embed_query(query)
        return vec.astype(np.float32, copy=False).tobytes()
    except Exception as ex:
        log.warning("praxis_cluster.embed failed: %r", ex)
        return None


def cosine(a: bytes, b: bytes) -> float:
    if not a or not b:
        return 0.0
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    n = float(np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9)
    return float(np.dot(va, vb) / n)


# ── Cluster assignment ───────────────────────────────────────────────────

def _new_cluster_id() -> str:
    return f"cl_{uuid.uuid4().hex[:12]}"


def find_neighbors(conn: sqlite3.Connection, eb: bytes,
                    k: int = K_NEIGHBORS,
                    exclude_turn_id: str = ""
                    ) -> list[tuple[float, int, str, str]]:
    """Top-K neighbors per cosine.
    Returns [(sim, obs_id, intent_sig, cluster_id), ...] sorted DESC."""
    if eb is None:
        return []
    cur = conn.execute(
        "SELECT id, turn_id, intent_sig, cluster_id, embedding "
        "FROM observations "
        "WHERE embedding IS NOT NULL AND cluster_id IS NOT NULL"
    )
    cands: list[tuple[float, int, str, str]] = []
    for row_id, tid, sig, cid, emb_b in cur:
        if tid == exclude_turn_id:
            continue
        cands.append((cosine(eb, emb_b), row_id, sig, cid))
    cands.sort(reverse=True, key=lambda t: t[0])
    return cands[:k]


def judge_same_intent(query: str, peers: list[str],
                       llm_call: Optional[Callable]) -> bool:
    """LLM judge zona grigia. True se stesso intent dei peers."""
    if not peers or llm_call is None:
        return False
    peers_txt = "\n".join(f"- {p}" for p in peers[:5])
    system = (
        "Sei un classificatore di intent.\n"
        "Determini se una QUERY ha lo stesso intent semantico (stessa "
        "azione, stesso tipo di oggetto) dei NEIGHBORS, indipendentemente "
        "da lingua o parole specifiche.\n"
        "Rispondi SOLO con SAME o DIFFERENT."
    )
    user = f"NEIGHBORS:\n{peers_txt}\n\nQUERY: {query}"
    try:
        out = llm_call(system, user, max_tokens=10, think=False)
        return "SAME" in (out or "").upper()
    except Exception as ex:
        log.warning("praxis_cluster.judge failed: %r", ex)
        return False


def assign_cluster(conn: sqlite3.Connection, query: str, eb: bytes,
                    llm_call: Optional[Callable] = None,
                    exclude_turn_id: str = "") -> str:
    """Assegna cluster_id. Deterministic-first, LLM solo in zona grigia."""
    if eb is None:
        return _new_cluster_id()
    neighbors = find_neighbors(conn, eb, k=K_NEIGHBORS,
                                exclude_turn_id=exclude_turn_id)
    if not neighbors:
        return _new_cluster_id()
    top_sim, _, _, top_cid = neighbors[0]
    if top_sim >= COSINE_HIGH:
        return top_cid
    if top_sim < COSINE_LOW:
        return _new_cluster_id()
    peers = [n[2] for n in neighbors if n[3] == top_cid][:3]
    if judge_same_intent(query, peers, llm_call):
        return top_cid
    return _new_cluster_id()


# ── Champion / challenger selection ──────────────────────────────────────

def composite_score(success_rate: float, latency_p50_ms: int,
                     alignment: float,
                     max_latency_ms: int = MAX_LATENCY_MS) -> float:
    """Composite 0..1 con pesi calibrati (praxis_constants).
    W_SUCCESS·success_rate + W_SPEED·speed + W_ALIGNMENT·alignment
    speed = max(0, 1 - latency_p50 / MAX_LATENCY_MS)."""
    if max_latency_ms <= 0:
        speed = 0.0
    else:
        speed = max(0.0, 1.0 - latency_p50_ms / max_latency_ms)
    return W_SUCCESS * success_rate + W_SPEED * speed + W_ALIGNMENT * alignment


def update_skill_metrics(conn: sqlite3.Connection, skill_id: str,
                          success: bool, latency_ms: int) -> None:
    """EMA update post-esecuzione (α=0.2). Contatori separati per semantica:
      uses       = esecuzioni reali (success + fail)
      ok_count   = solo feedback ✓
      fail_count = solo feedback ✗
    Non confonde lookup-hit (selezione) con execute (outcome reale).
    """
    cur = conn.execute(
        "SELECT uses, ok_count, fail_count, latency_p50_ms, alignment_score "
        "FROM skills WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        return
    uses, ok_count, fail_count, p50, align = row
    p50 = p50 or 0
    align = align or 0.0
    new_uses = (uses or 0) + 1
    new_ok = (ok_count or 0) + (1 if success else 0)
    new_fail = (fail_count or 0) + (0 if success else 1)
    new_p50 = (int((1 - EMA_ALPHA) * p50 + EMA_ALPHA * latency_ms)
                if p50 else int(latency_ms))
    succ_rate = new_ok / max(1, new_uses)
    score = composite_score(succ_rate, new_p50, align)
    conn.execute(
        "UPDATE skills SET uses=?, ok_count=?, fail_count=?, "
        "latency_p50_ms=?, composite_score=?, ts_last_used=? WHERE id=?",
        (new_uses, new_ok, new_fail, new_p50, score,
         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         skill_id))
    conn.commit()


def log_skill_version(conn: sqlite3.Connection, skill_id: str,
                       event: str, *, old_fw_hash: Optional[str] = None,
                       new_fw_hash: Optional[str] = None,
                       reason: str = "") -> None:
    """Audit trail skill change. Event in {created, refresh_template,
    retry_repeat, champion_swap}."""
    try:
        conn.execute(
            "INSERT INTO skill_versions(skill_id, ts, event, old_fw_hash, "
            "new_fw_hash, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (skill_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             event, old_fw_hash, new_fw_hash, reason))
        conn.commit()
    except Exception as ex:
        log.warning("log_skill_version: %r", ex)


def maybe_swap_champion(conn: sqlite3.Connection,
                         cluster_id: str) -> Optional[str]:
    """Selezione darwiniana rapida. Ritorna nuovo champion skill_id se swap.

    Soglia: swap_score = 2·Δsuccess + Δlat_normalized
    Swap immediato se score > SWAP_THR (default 0.15) AND challenger.uses ≥ 1.
    """
    cur = conn.execute(
        "SELECT id, champion, uses, ok_count, fail_count, latency_p50_ms, "
        "composite_score FROM skills "
        "WHERE cluster_id = ? AND status = 'active' "
        "ORDER BY composite_score DESC", (cluster_id,))
    rows = cur.fetchall()
    if len(rows) < 2:
        return None
    champ = next((r for r in rows if r[1] == 1), None)
    if not champ:
        conn.execute("UPDATE skills SET champion=1 WHERE id=?", (rows[0][0],))
        conn.commit()
        return rows[0][0]
    c_id, _, c_uses, c_ok, c_fail, c_lat, _ = champ
    c_succ = (c_ok or 0) / max(1, (c_uses or 1))
    c_lat_safe = max(1, c_lat or 1)
    for r in rows:
        if r[0] == c_id:
            continue
        h_id, _, h_uses, h_ok, h_fail, h_lat, _ = r
        if (h_uses or 0) < 1:
            continue
        h_succ = (h_ok or 0) / max(1, (h_uses or 1))
        h_lat_safe = max(1, h_lat or 1)
        swap_score = (2.0 * (h_succ - c_succ)
                       + (c_lat_safe - h_lat_safe) / c_lat_safe)
        if swap_score > SWAP_THR:
            conn.execute("UPDATE skills SET champion=0 WHERE id=?", (c_id,))
            conn.execute("UPDATE skills SET champion=1 WHERE id=?", (h_id,))
            conn.commit()
            log_skill_version(
                conn, h_id, "champion_swap",
                reason=f"swap_score={swap_score:.2f} "
                        f"lat={c_lat_safe}->{h_lat_safe} "
                        f"succ={c_succ:.2f}->{h_succ:.2f}")
            log.info("praxis_cluster: SWAP cluster=%s %s->%s (score=%.2f, "
                      "lat %d->%d, succ %.2f->%.2f)",
                      cluster_id, c_id, h_id, swap_score,
                      c_lat_safe, h_lat_safe, c_succ, h_succ)
            return h_id
    return None


def select_skill_for_cluster(conn: sqlite3.Connection,
                              cluster_id: str) -> Optional[dict]:
    """Ritorna champion skill o challenger random (exploration ε-greedy).

    ε=0 (default) → pure-exploit, champion sempre.
    ε>0           → con probabilita' ε ritorna challenger random invece
                    di champion. Anti lock-in subottimale.
    Skippa skill con template_issue=1 (refresh notturno pending).
    """
    cur = conn.execute(
        "SELECT id, framework_json, uses, ok_count, fail_count, "
        "composite_score, alignment_score, champion FROM skills "
        "WHERE cluster_id = ? AND status IN ('active', 'shadow') "
        "AND COALESCE(template_issue, 0) = 0 "
        "ORDER BY champion DESC, composite_score DESC",
        (cluster_id,))
    rows = cur.fetchall()
    if not rows:
        return None
    # ε-greedy: con prob ε scegli challenger random (rows[1:]).
    try:
        from praxis_constants import EXPLORATION_EPSILON
        if (EXPLORATION_EPSILON > 0
                and len(rows) >= 2):
            import random as _r
            if _r.random() < EXPLORATION_EPSILON:
                pick = _r.choice(rows[1:])
                row = pick
                explore = True
            else:
                row = rows[0]
                explore = False
        else:
            row = rows[0]
            explore = False
    except Exception:
        row = rows[0]
        explore = False
    sid, fw_json, uses, oks, fails, score, align, _champ = row
    import json as _json
    return {
        "skill_id": sid,
        "framework": _json.loads(fw_json),
        "stats": {"uses": uses, "ok": oks, "fail": fails,
                   "composite_score": score, "alignment": align,
                   "explored": explore},
    }


# ── Schema migration ─────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    """ALTER TABLE idempotenti. Chiamare al boot di PraxisStore."""
    def _has_col(table: str, col: str) -> bool:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return any(r[1] == col for r in cur.fetchall())

    # observations: + embedding, cluster_id
    if not _has_col("observations", "embedding"):
        conn.execute("ALTER TABLE observations ADD COLUMN embedding BLOB")
    if not _has_col("observations", "cluster_id"):
        conn.execute("ALTER TABLE observations ADD COLUMN cluster_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_cluster "
                  "ON observations(cluster_id)")

    # skills: + cluster_id, framework_hash, latency_p50_ms,
    # composite_score, champion
    if not _has_col("skills", "cluster_id"):
        conn.execute("ALTER TABLE skills ADD COLUMN cluster_id TEXT")
    if not _has_col("skills", "framework_hash"):
        conn.execute("ALTER TABLE skills ADD COLUMN framework_hash TEXT")
    if not _has_col("skills", "latency_p50_ms"):
        conn.execute("ALTER TABLE skills ADD COLUMN latency_p50_ms INTEGER "
                      "DEFAULT 0")
    if not _has_col("skills", "composite_score"):
        conn.execute("ALTER TABLE skills ADD COLUMN composite_score REAL "
                      "DEFAULT 0.5")
    if not _has_col("skills", "champion"):
        conn.execute("ALTER TABLE skills ADD COLUMN champion INTEGER "
                      "DEFAULT 1")
    if not _has_col("skills", "template_issue"):
        # Pronoia format_fail marker: skill ha framework giusto ma template
        # final_message buggy. Batch notturno rigenera (ADR 0161 ext).
        conn.execute("ALTER TABLE skills ADD COLUMN template_issue INTEGER "
                      "DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_cluster "
                  "ON skills(cluster_id, champion DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_cluster_fw "
                  "ON skills(cluster_id, framework_hash)")
    # Audit trail skill version history (ADR 0161 ext, 26/5/2026).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_versions (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          skill_id        TEXT NOT NULL,
          ts              TEXT NOT NULL,
          event           TEXT NOT NULL,   -- 'created'|'refresh_template'|'retry_repeat'|'champion_swap'
          old_fw_hash     TEXT,
          new_fw_hash     TEXT,
          reason          TEXT,
          FOREIGN KEY (skill_id) REFERENCES skills(id)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_versions_skill "
                  "ON skill_versions(skill_id, ts DESC)")
    conn.commit()


# ── Backfill ──────────────────────────────────────────────────────────────

def backfill(conn: sqlite3.Connection,
              llm_call: Optional[Callable] = None) -> int:
    """Embed + assign cluster per obs senza embedding/cluster_id.
    Ritorna numero processate."""
    cur = conn.execute(
        "SELECT id, turn_id, intent_sig FROM observations "
        "WHERE embedding IS NULL OR cluster_id IS NULL"
    )
    rows = cur.fetchall()
    n = 0
    for row_id, turn_id, sig in rows:
        # intent_sig = "verb|object|kw1_kw2" → testo embeddable
        query_proxy = sig.replace("|", " ").replace("_", " ").strip()
        eb = embed(query_proxy)
        if eb is None:
            continue
        cid = assign_cluster(conn, query_proxy, eb,
                              llm_call=llm_call,
                              exclude_turn_id=turn_id)
        conn.execute(
            "UPDATE observations SET embedding=?, cluster_id=? WHERE id=?",
            (eb, cid, row_id))
        n += 1
    conn.commit()
    return n
