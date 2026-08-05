"""tune_ranker.py — Grid search ranker weights per maximize top-2 on FROZEN.

Tunable weights (env vars):
- RANK_VERB_MATCH (current 0.5)
- RANK_OBJ_MATCH (0.5)
- RANK_VERB_PREFIX (0.4)
- RANK_TARGET_EXACT (0.7)
- RANK_TARGET_COMPAT (0.4)
- RANK_TARGET_SCHEMA (0.25/0.35)
- RANK_QUALIFIER_PENALTY (1.0)
- RANK_QUALIFIER_BONUS (0.3)
- RANK_ATOMIC_BOOST (0.7)
- RANK_MUTATING_TARGET (0.5)
"""
from __future__ import annotations

import json
import os
import subprocess
import re
import sys
from pathlib import Path
from itertools import product

SIM_DIR = Path("/opt/metnos/tests/simulator")
BENCH_OUT = Path("/tmp/iter_tune_ranker.out")


def run_bench(env: dict) -> float:
    """Run bench with env overrides, return top-2 accepted."""
    full_env = os.environ.copy()
    full_env.update(env)
    full_env["BENCH_FROZEN"] = "1"
    full_env["PYTHONUNBUFFERED"] = "1"
    with open(BENCH_OUT, "w") as f:
        subprocess.run(
            ["python3", "-u", "run_sim_dedicated.py"],
            cwd=SIM_DIR, env=full_env, stdout=f, stderr=subprocess.STDOUT,
            timeout=600,
        )
    with open(BENCH_OUT) as f:
        for ln in f:
            m = re.match(r"  top-2 accepted .+= (\d+)%", ln)
            if m: return float(m.group(1))
    return 0.0


def grid_search():
    base_env = {}
    # Get baseline
    print("Running baseline (defaults)...")
    baseline = run_bench(base_env)
    print(f"Baseline top-2: {baseline}%")
    print()
    # Try variations
    candidates = [
        {"RANK_VERB_MATCH": "0.7"},
        {"RANK_VERB_MATCH": "0.3"},
        {"RANK_OBJ_MATCH": "0.7"},
        {"RANK_VERB_PREFIX": "0.6"},
        {"RANK_VERB_PREFIX": "0.2"},
        {"RANK_TARGET_EXACT": "1.0"},
        {"RANK_TARGET_EXACT": "0.5"},
        {"RANK_ATOMIC_BOOST": "1.0"},
        {"RANK_ATOMIC_BOOST": "0.5"},
        {"RANK_QUALIFIER_PENALTY": "0.7"},
        {"RANK_QUALIFIER_PENALTY": "1.5"},
        {"RANK_MUTATING_TARGET": "0.8"},
    ]
    results = []
    for cand in candidates:
        score = run_bench(cand)
        delta = score - baseline
        results.append((score, delta, cand))
        sign = "+" if delta >= 0 else ""
        print(f"  {cand}: {score}% ({sign}{delta:.0f}%)")
    print()
    results.sort(reverse=True)
    print("Top 5:")
    for score, delta, cand in results[:5]:
        print(f"  {score}% (Δ{delta:+.0f}%): {cand}")


if __name__ == "__main__":
    grid_search()
