#!/usr/bin/env python3
"""compare_bench.py — confronto due run di bench_praxis_coverage.

Usage:
  python3 compare_bench.py <simple.json> <metis.json>

Output: tabella confronto coverage, latency, distribuzione handler,
breakdown per domain, regression detection per categoria.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, Counter
from pathlib import Path


def load(p: str) -> dict:
    return json.loads(Path(p).read_text())


def fmt_pct(n: int, d: int) -> str:
    return f"{n/d*100:.1f}%" if d else "—"


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: compare_bench.py <simple.json> <metis.json>")
    s = load(sys.argv[1])
    m = load(sys.argv[2])
    ss = s["summary"]; ms = m["summary"]

    print("=" * 80)
    print("BENCH COMPARISON: SIMPLE vs METIS")
    print("=" * 80)
    print(f"  Simple: N={ss['total']} wall={ss['elapsed_total_s']:.0f}s")
    print(f"  Metis:  N={ms['total']} wall={ms['elapsed_total_s']:.0f}s")

    print("\n=== COVERAGE ===")
    print(f"  {'metric':30} {'simple':>12} {'metis':>12} {'delta':>10}")
    print(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*10}")
    metrics = [
        ("Total coverage", ss["coverage_total"], ms["coverage_total"], "↑"),
        ("Praxis/engine coverage", ss.get("praxis_coverage", 0),
         ms.get("praxis_coverage", 0), "↑"),
        ("Planner fallback", ss.get("planner_coverage", 0),
         ms.get("planner_coverage", 0), "↓"),
    ]
    for name, sv, mv, want in metrics:
        delta = mv - sv
        sign = "+" if delta >= 0 else ""
        flag = "✓" if (want == "↑" and delta > 0) or (want == "↓" and delta < 0) else " "
        print(f"  {name:30} {sv*100:11.1f}% {mv*100:11.1f}% {sign}{delta*100:7.1f}% {flag}")

    print("\n=== LATENCY ===")
    print(f"  {'metric':30} {'simple':>12} {'metis':>12} {'delta':>10}")
    print(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*10}")
    for label, key in (("Mean (s)", "mean_latency_s"), ("p50 (s)", "p50_latency_s")):
        sv = ss.get(key, 0); mv = ms.get(key, 0)
        delta = mv - sv
        sign = "+" if delta >= 0 else ""
        print(f"  {label:30} {sv:11.2f}s {mv:11.2f}s {sign}{delta:7.2f}s")

    print("\n=== HANDLER DISTRIBUTION (success) ===")
    sh = ss.get("by_handler_ok", {}); mh = ms.get("by_handler_ok", {})
    handlers = sorted(set(sh.keys()) | set(mh.keys()))
    print(f"  {'handler':20} {'simple':>10} {'metis':>10}")
    for h in handlers:
        print(f"  {h:20} {sh.get(h,0):10} {mh.get(h,0):10}")

    print("\n=== BY DOMAIN ===")
    sd = ss.get("by_domain", {}); md = ms.get("by_domain", {})
    print(f"  {'domain':20} {'simple':>12} {'metis':>12}")
    for d in sorted(set(sd.keys()) | set(md.keys())):
        print(f"  {d:20} {sd.get(d, '—'):>12} {md.get(d, '—'):>12}")

    # Per-query regression detection
    print("\n=== REGRESSIONS (simple OK → metis FAIL) ===")
    s_by_q = {r["query"]: r for r in s["results"]}
    m_by_q = {r["query"]: r for r in m["results"]}
    common = set(s_by_q.keys()) & set(m_by_q.keys())
    regressions = []
    improvements = []
    for q in common:
        sr = s_by_q[q]; mr = m_by_q[q]
        if sr["success"] and not mr["success"]:
            regressions.append(q)
        elif not sr["success"] and mr["success"]:
            improvements.append(q)
    print(f"  Regressions: {len(regressions)}")
    for q in regressions[:10]:
        print(f"    - {q[:70]}")
    print(f"  Improvements: {len(improvements)}")
    for q in improvements[:10]:
        print(f"    + {q[:70]}")

    # Verdict
    print("\n" + "=" * 80)
    cov_delta = ms["coverage_total"] - ss["coverage_total"]
    lat_delta = ms["mean_latency_s"] - ss["mean_latency_s"]
    if cov_delta > 0.02 and lat_delta < ss["mean_latency_s"] * 0.5:
        print("VERDICT: METIS WIN (coverage +%.1fpt, latency manageable)" % (cov_delta*100))
    elif cov_delta > 0:
        print("VERDICT: METIS marginal (coverage +%.1fpt, latency +%.1fs)" %
              (cov_delta*100, lat_delta))
    elif cov_delta < -0.02:
        print("VERDICT: METIS REGRESSION (coverage %.1fpt)" % (cov_delta*100))
    else:
        print("VERDICT: METIS no significant advantage (coverage %.1fpt)" % (cov_delta*100))


if __name__ == "__main__":
    main()
