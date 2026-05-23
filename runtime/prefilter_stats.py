"""CLI aggregator del telemetry JSONL del prefilter modulare.

Uso:
  python3 -m prefilter_stats                  # riepilogo per strategy
  python3 -m prefilter_stats --since 1d       # filtro temporale
  python3 -m prefilter_stats --query-hash X   # dettagli per query
  python3 -m prefilter_stats --compare a,b    # A/B fra due strategy

Lavora SOLO su `~/.local/share/metnos/prefilter_telemetry.jsonl`. Niente
overhead runtime. Determinismo §7.9.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path


import config as _C  # §7.11
TELEMETRY_PATH = _C.PATH_USER_DATA / "prefilter_telemetry.jsonl"


def _parse_since(s: str | None) -> float:
    if not s:
        return 0.0
    s = s.strip().lower()
    now = time.time()
    if s.endswith("d"):
        return now - int(s[:-1]) * 86400
    if s.endswith("h"):
        return now - int(s[:-1]) * 3600
    if s.endswith("m"):
        return now - int(s[:-1]) * 60
    raise ValueError(f"--since: usa 1d/24h/30m, ricevuto {s!r}")


def _load(since_ts: float = 0.0) -> list[dict]:
    if not TELEMETRY_PATH.exists():
        return []
    out = []
    for line in TELEMETRY_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("ts", 0) < since_ts:
            continue
        out.append(rec)
    return out


def summary(records: list[dict]) -> None:
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_strategy[r.get("strategy", "?")].append(r)
    print(f"Total records: {len(records)}")
    print(f"Strategies: {len(by_strategy)}\n")
    print(f"{'strategy':<24} {'n':>6} {'avg_ms':>8} {'p95_ms':>8} {'avg_cand':>9} {'avg_conf':>9}")
    print("-" * 70)
    for name in sorted(by_strategy.keys()):
        rs = by_strategy[name]
        elapsed = [r.get("elapsed_ms", 0) for r in rs if r.get("elapsed_ms") is not None]
        n_cand = [r.get("n_candidates", 0) for r in rs]
        conf = [r.get("confidence") or 0 for r in rs if r.get("confidence") is not None]
        avg_ms = sum(elapsed) / len(elapsed) if elapsed else 0
        p95_ms = sorted(elapsed)[int(len(elapsed) * 0.95)] if elapsed else 0
        avg_cand = sum(n_cand) / len(n_cand) if n_cand else 0
        avg_conf = sum(conf) / len(conf) if conf else 0
        print(f"{name:<24} {len(rs):>6} {avg_ms:>8.0f} {p95_ms:>8.0f} "
              f"{avg_cand:>9.1f} {avg_conf:>9.3f}")


def compare(records: list[dict], a: str, b: str) -> None:
    """A/B fra due strategy: per ogni query_hash presente in entrambi,
    confronta top3 (overlap), latency, candidati."""
    by_query_a: dict[str, dict] = {}
    by_query_b: dict[str, dict] = {}
    for r in records:
        h = r.get("query_hash")
        if not h:
            continue
        if r.get("strategy") == a:
            by_query_a[h] = r
        elif r.get("strategy") == b:
            by_query_b[h] = r
    common = set(by_query_a) & set(by_query_b)
    print(f"Compare {a!r} vs {b!r}: {len(common)} query in entrambi\n")
    if not common:
        return
    overlap_top1 = 0
    overlap_top3 = 0
    sum_diff_ms = 0
    for h in common:
        ra, rb = by_query_a[h], by_query_b[h]
        ta, tb = ra.get("top3") or [], rb.get("top3") or []
        if ta and tb and ta[0] == tb[0]:
            overlap_top1 += 1
        if ta and tb:
            overlap_top3 += len(set(ta) & set(tb))
        ma = ra.get("elapsed_ms") or 0
        mb = rb.get("elapsed_ms") or 0
        sum_diff_ms += (ma - mb)
    print(f"  top1 match: {overlap_top1}/{len(common)} ({100*overlap_top1/len(common):.1f}%)")
    avg_top3 = overlap_top3 / (len(common) * 3)
    print(f"  top3 overlap medio: {avg_top3*100:.1f}%")
    print(f"  delta latency medio (a-b): {sum_diff_ms/len(common):.1f} ms")


def main():
    p = argparse.ArgumentParser(description="Prefilter telemetry stats")
    p.add_argument("--since", help="filtro temporale: 1d, 24h, 30m")
    p.add_argument("--query-hash", help="dettagli per una query")
    p.add_argument("--compare", help="A/B fra due strategy: a,b")
    args = p.parse_args()
    since_ts = _parse_since(args.since) if args.since else 0.0
    records = _load(since_ts)
    if not records:
        print(f"Nessun record in {TELEMETRY_PATH}")
        sys.exit(0)
    if args.compare:
        parts = args.compare.split(",")
        if len(parts) != 2:
            print("--compare richiede formato: A,B", file=sys.stderr)
            sys.exit(2)
        compare(records, parts[0].strip(), parts[1].strip())
        return
    if args.query_hash:
        for r in records:
            if (r.get("query_hash") or "").startswith(args.query_hash):
                print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    summary(records)


if __name__ == "__main__":
    main()
