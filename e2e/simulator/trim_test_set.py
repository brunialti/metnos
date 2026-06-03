"""trim_test_set.py — Reduce test set to 500 keeping CHALLENGING queries.

Strategy:
1. Keep all FAILED queries from latest bench (high-value, where simulator struggles)
2. Keep all multi-step (≥2 steps) queries (force pipeline reasoning)
3. Keep github + catalog-gap queries (test request_new_executor)
4. From easy passes: dedup near-duplicates, sample 20%
5. Padding from synth (github + multi-stage) if under 500
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
os.environ["BENCH_REAL"] = "1"
from queries_real_500 import TEST_QUERIES_REAL


def parse_passed_failed(bench_out: str) -> tuple[set, set]:
    tops = {}; tops2 = {}
    with open(bench_out) as f:
        cur_q = None; done = {1: False, 2: False}
        for ln in f:
            ln = ln.rstrip()
            m = re.match(r"^Q: (.+)$", ln)
            if m: cur_q = m.group(1).strip(); done = {1: False, 2: False}; continue
            if cur_q:
                for n, dst in [(1, tops), (2, tops2)]:
                    if done[n]: continue
                    m = re.match(rf"    {n}\. \[-?[\d\.]+\] (\S+)", ln)
                    if m: dst[cur_q] = m.group(1); done[n] = True; break
    passed = set()
    failed = set()
    for t in TEST_QUERIES_REAL:
        q = t["query"]
        ok = tops.get(q, "?") in t["accepted_first"] or tops2.get(q, "?") in t["accepted_first"]
        if ok: passed.add(q)
        else: failed.add(q)
    return passed, failed


def normalize_for_dedup(q: str) -> str:
    """Strip case + punctuation + path specifics → group near-duplicates."""
    s = q.lower().strip()
    # Replace specific paths/IDs with generic
    s = re.sub(r"/tmp/\S+", "/tmp/X", s)
    s = re.sub(r"https?://\S+", "https://X", s)
    s = re.sub(r"\d+", "N", s)
    s = re.sub(r"[?!.,;:🕐]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def trim_to_500(bench_out: str) -> list[dict]:
    passed, failed = parse_passed_failed(bench_out)
    print(f"Input: {len(TEST_QUERIES_REAL)} queries", file=sys.stderr)
    print(f"  passed: {len(passed)}", file=sys.stderr)
    print(f"  failed: {len(failed)}", file=sys.stderr)

    kept = []
    seen_normalized = set()

    # 1. All FAILED (high-value)
    for t in TEST_QUERIES_REAL:
        if t["query"] in failed:
            norm = normalize_for_dedup(t["query"])
            if norm in seen_normalized:
                continue
            kept.append(t)
            seen_normalized.add(norm)
    print(f"After fails (deduped): {len(kept)}", file=sys.stderr)

    # 2. All multi-step (≥2 steps)
    for t in TEST_QUERIES_REAL:
        if t["query"] in passed:
            n_steps = len(t.get("expected_path", []))
            if n_steps >= 2:
                norm = normalize_for_dedup(t["query"])
                if norm in seen_normalized: continue
                kept.append(t); seen_normalized.add(norm)
    print(f"After multi-step passes: {len(kept)}", file=sys.stderr)

    # 3. github + complex (anything with github/admin/special features)
    KEYWORDS = ("github", "comprimi", "estrai", "indicizza", "manda",
                 "scrivi", "report", "monta", "crea task", "rispondi")
    kept_set = {x["query"] for x in kept}
    for t in TEST_QUERIES_REAL:
        if t["query"] in kept_set: continue
        if any(kw in t["query"].lower() for kw in KEYWORDS):
            norm = normalize_for_dedup(t["query"])
            if norm in seen_normalized: continue
            kept.append(t); seen_normalized.add(norm)
            kept_set.add(t["query"])
    print(f"After keywords: {len(kept)}", file=sys.stderr)

    # 4. Sample of easy passes (20% of remaining)
    easy = [t for t in TEST_QUERIES_REAL
             if t["query"] in passed
             and t["query"] not in {x["query"] for x in kept}
             and normalize_for_dedup(t["query"]) not in seen_normalized]
    sample = easy[::max(1, len(easy) // 100)][:100]  # ~100 spread sample
    for t in sample:
        norm = normalize_for_dedup(t["query"])
        if norm in seen_normalized: continue
        kept.append(t); seen_normalized.add(norm)
    print(f"After easy sample: {len(kept)}", file=sys.stderr)

    # 5. Pad with synth github + multi-stage if under 500
    try:
        from queries_500_builder import TEST_QUERIES_500
        synth_priority = [t for t in TEST_QUERIES_500
                            if "github" in t["query"].lower()
                            or len(t.get("expected_path", [])) >= 3]
        for t in synth_priority:
            if len(kept) >= 500: break
            norm = normalize_for_dedup(t["query"])
            if norm in seen_normalized: continue
            kept.append({k: (set(v) if k == "accepted_first" and isinstance(v, (list, set)) else v)
                          for k, v in t.items()})
            seen_normalized.add(norm)
        print(f"After synth padding: {len(kept)}", file=sys.stderr)
    except Exception as e:
        print(f"synth padding err: {e}", file=sys.stderr)

    # Trim to exactly 500
    if len(kept) > 500:
        kept = kept[:500]
    return kept


if __name__ == "__main__":
    bench_out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/iter_real_v2.out"
    trimmed = trim_to_500(bench_out)
    # Convert sets to lists for json
    for t in trimmed:
        if isinstance(t.get("accepted_first"), set):
            t["accepted_first"] = sorted(t["accepted_first"])
    out = Path("/opt/metnos/e2e/simulator/test_set_trimmed.json")
    out.write_text(json.dumps(trimmed, indent=2, ensure_ascii=False))
    print(f"Written {len(trimmed)} queries to {out}")
