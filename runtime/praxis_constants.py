"""praxis_constants.py — costanti calibrate per Praxis + ClusterLLM.

Sezione UNICA per soglie/pesi. Modifiche richiedono bench di re-calibration
(vedi `runtime/bench_praxis_cluster.py`). Override via env per esperimenti
runtime (no redeploy).

Pattern §7.3: tunables centralizzati, no constants sparsi nei moduli.
Sources/research: ClusterLLM (Zhang 2023), GPTCache (Bang 2023),
Reflexion (Shinn 2023), bandit pure-exploit (no epsilon-greedy).
"""
from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    """Env override float con default."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ── ClusterLLM (assign_cluster) ─────────────────────────────────────────
# Soglie cosine BGE-M3 (1024d).
#   ≥ HIGH  → match deterministic (riusa cluster existing)
#   < LOW   → no match deterministic (genera cluster nuovo)
#   in mezzo (zona grigia) → LLM judge (~2-3s Gemma 26B)
# Empirical: 0.90 / 0.75 separa intent semantici equivalenti vs distinti
# nei test 26/5/2026 (multilingua IT+EN, count/list/move/delete).
COSINE_HIGH = _f("METNOS_CLUSTER_COSINE_HIGH", 0.90)
COSINE_LOW = _f("METNOS_CLUSTER_COSINE_LOW", 0.75)

# K-nearest scansionati per cluster lookup. Trade-off precision vs cost
# linear (oggi). Con FAISS futuro: K=50 raccomandato.
K_NEIGHBORS = _i("METNOS_CLUSTER_K", 10)


# ── Champion / Challenger swap (selezione darwiniana) ──────────────────
# swap_score = 2·Δsuccess + Δlat_normalized
# swap se score > SWAP_THR (default 0.15).
# Aggressive: 1 sample challenger con lat 0.8× champion vince.
# Anti-flapping: nessuno (oggi). Pianificato hysteresis margin in v2.
SWAP_THR = _f("METNOS_CLUSTER_SWAP_THR", 0.15)


# ── Composite score (ranking skill) ────────────────────────────────────
# composite = W_SUCCESS·success + W_SPEED·speed + W_ALIGNMENT·alignment
# Sum=1.0 enforced (no normalize, calibra a mano).
# Defaults: success domina (50%), speed importante (30%), alignment
# secondario (20%) — alignment_score spesso 0.0 oggi, peso effettivo
# minore. Bump quando alignment backfill (ADR 0157) consolidato.
W_SUCCESS = _f("METNOS_PRAXIS_W_SUCCESS", 0.5)
W_SPEED = _f("METNOS_PRAXIS_W_SPEED", 0.3)
W_ALIGNMENT = _f("METNOS_PRAXIS_W_ALIGNMENT", 0.2)

# Max latency normalization (ms). Query oltre = speed=0.
# 60s = budget tipico turno full-Mētis. Calibrato osservando p95 reali.
MAX_LATENCY_MS = _i("METNOS_PRAXIS_MAX_LATENCY_MS", 60000)

# EMA smoothing factor per latency_p50_ms (α). 0.2 = smoothing su ~5 turni.
EMA_ALPHA = _f("METNOS_PRAXIS_EMA_ALPHA", 0.2)


# ── Promote thresholds ─────────────────────────────────────────────────
# Min observations per promote a skill ACTIVE:
#   mutating verb (move/delete/send): 1 obs (azione esplicita, verificabile)
#   producer verb (find/read/list/get): 2 obs (statistica prudente)
#   cluster vuoto (no champion): override a 1 (primo successo = champion)
MIN_OBS_PRODUCER = _i("METNOS_PRAXIS_MIN_OBS_PRODUCER", 2)
MIN_OBS_MUTATING = _i("METNOS_PRAXIS_MIN_OBS_MUTATING", 1)
MIN_SUCCESS = _f("METNOS_PRAXIS_MIN_SUCCESS", 1.0)  # 100% ok floor


# ── Anti-skill TTL ─────────────────────────────────────────────────────
# Tipo                  TTL          Reason
# repeat (soft)         1 ora        soft anti, scade rapidamente
# pipeline_fail (hard)  30 giorni    blocca framework, scade lento
TTL_REPEAT_SOFT_SECS = _i("METNOS_TTL_REPEAT_SOFT_SECS", 3600)
TTL_PIPELINE_FAIL_SECS = _i("METNOS_TTL_PIPELINE_FAIL_SECS", 30 * 86400)


# ── Pronoia classify_fail ──────────────────────────────────────────────
# Default class su LLM error/ambiguous: pipeline_fail (conservative,
# penalizza skill — safer del false negative).
PRONOIA_DEFAULT_CLASS = "pipeline_fail"
PRONOIA_CLASSIFY_ENABLED = os.environ.get(
    "METNOS_PRONOIA_CLASSIFY_FAIL", "1") == "1"


# ── Re-execute on ↻ (opt-in) ───────────────────────────────────────────
# Quando ↻ utente: solo re-propose framework (default) o anche execute
# nuovo framework e mostrare nuovo result inline?
# Vincolo safety: execute solo se framework idempotent (no mutating verb).
REEXECUTE_ON_REPEAT = os.environ.get(
    "METNOS_REEXECUTE_ON_REPEAT", "0") == "1"


# ── Exploration ε-greedy in select_skill_for_cluster ───────────────────
# 0.0 = pure-exploit (default, deterministic).
# 0.05-0.10 = exploration leggera (5-10% delle query usa challenger
# random invece di champion). Anti lock-in.
EXPLORATION_EPSILON = _f("METNOS_EXPLORATION_EPSILON", 0.0)


# ── Cluster batch maintenance (daily jobs) ─────────────────────────────
# Cluster merge: rivisita cluster singleton/piccoli, LLM judge merge.
CLUSTER_MERGE_ENABLED = os.environ.get(
    "METNOS_CLUSTER_MERGE_ENABLED", "1") == "1"
CLUSTER_MERGE_MIN_SIZE = _i("METNOS_CLUSTER_MERGE_MIN_SIZE", 1)  # rivisita
CLUSTER_MERGE_MAX_PAIRS = _i("METNOS_CLUSTER_MERGE_MAX_PAIRS", 20)


__all__ = [
    "COSINE_HIGH", "COSINE_LOW", "K_NEIGHBORS",
    "SWAP_THR",
    "W_SUCCESS", "W_SPEED", "W_ALIGNMENT", "MAX_LATENCY_MS", "EMA_ALPHA",
    "MIN_OBS_PRODUCER", "MIN_OBS_MUTATING", "MIN_SUCCESS",
    "TTL_REPEAT_SOFT_SECS", "TTL_PIPELINE_FAIL_SECS",
    "PRONOIA_DEFAULT_CLASS", "PRONOIA_CLASSIFY_ENABLED",
    "REEXECUTE_ON_REPEAT", "EXPLORATION_EPSILON",
    "CLUSTER_MERGE_ENABLED", "CLUSTER_MERGE_MIN_SIZE", "CLUSTER_MERGE_MAX_PAIRS",
]
