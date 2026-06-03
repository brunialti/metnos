"""queries_real_500.py — Realistic 500 test set.

Composition:
- ~388 real Metnos production queries (from turn logs, excluding always-failing)
- ~112 synthetic complex multi-domain queries (from queries_500_builder)

Used when BENCH_REAL=1 env var set.
"""
from __future__ import annotations

import json
from pathlib import Path

TEST_QUERIES_REAL: list[dict] = []


def _load_real():
    fn = Path(__file__).parent / "real_queries.json"
    if not fn.exists():
        return []
    raw = json.loads(fn.read_text())
    out = []
    for r in raw:
        if "request_new_executor" in r.get("expected_path", [None])[0] or "":
            continue
        if "@uploaded" in r.get("expected_path", [None])[0] or "":
            continue
        out.append({
            "query": r["query"],
            "expected_path": r["expected_path"],
            "accepted_first": set(r["accepted_first"]),
        })
    return out


TEST_QUERIES_REAL = _load_real()

# Pad with synthetic queries to reach 500.
# Strategy: prioritize GitHub queries + multi-step + complex (more challenging)
if len(TEST_QUERIES_REAL) < 500:
    try:
        from queries_500_builder import TEST_QUERIES_500
        seen = {t["query"] for t in TEST_QUERIES_REAL}
        # Phase 1: GitHub queries (test web scraping path)
        github_added = 0
        for t in TEST_QUERIES_500:
            if t["query"] in seen: continue
            if "github" in t["query"].lower() and github_added < 20:
                TEST_QUERIES_REAL.append(t)
                seen.add(t["query"])
                github_added += 1
                if len(TEST_QUERIES_REAL) >= 500: break
        # Phase 2: multi-step (3+ steps)
        for t in TEST_QUERIES_500:
            if t["query"] in seen: continue
            n_steps = len(t.get("expected_path", []))
            if n_steps >= 3:
                TEST_QUERIES_REAL.append(t)
                seen.add(t["query"])
                if len(TEST_QUERIES_REAL) >= 500: break
        # Phase 3: any remaining
        for t in TEST_QUERIES_500:
            if t["query"] in seen: continue
            TEST_QUERIES_REAL.append(t)
            seen.add(t["query"])
            if len(TEST_QUERIES_REAL) >= 500: break
    except Exception:
        pass

if __name__ == "__main__":
    print(f"Total: {len(TEST_QUERIES_REAL)}")
    from collections import Counter
    verbs = Counter()
    for t in TEST_QUERIES_REAL:
        if t.get("expected_path"):
            v = t["expected_path"][0].split("_")[0]
            verbs[v] += 1
    print(f"Verb distribution: {dict(verbs.most_common(20))}")
    multi_step = sum(1 for t in TEST_QUERIES_REAL if len(t.get("expected_path", [])) >= 2)
    print(f"Multi-step queries: {multi_step}")
