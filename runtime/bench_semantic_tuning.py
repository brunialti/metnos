"""bench_semantic_tuning.py — grid search soglia/alpha BGE-M3 fallback.

Corpus: query reali dai turn JSONL (15/5/2026), ground truth = chosen_tool
del primo step di turni riusciti.

Grid: threshold ∈ {0, 2, 4, 6, 8, 10} × alpha ∈ {2, 3, 4, 5, 6}.

Metriche:
  - recall@1 / @3 / @5 (l'executor atteso in top-N posizioni del ranking)
  - latency media (separata fra fallback attivo/inattivo)
  - activation_rate (% query in cui BGE entra)

Bypassa intent extractor (`prefer_intent=False`): testa SOLO il path BoW
+ semantic fallback. L'intent extractor LLM e' ortogonale e in produzione
ha priorita': il fallback BoW e' il path edge (LLM offline/timeout).

Determinismo §7.9: niente LLM, sola inferenza ONNX.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or str(Path(__file__).resolve().parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
sys.path.insert(0, "/opt/suprastructure/src")

import affinity_semantic as asm  # noqa: E402
from loader import invalidate_catalog_cache, load_catalog  # noqa: E402
from prefilter import rank_adaptive  # noqa: E402


CORPUS_PATH = Path("/tmp/bench_corpus.json")
RESULT_PATH = Path(__file__).resolve().parents[1] / "runtime/bench_semantic_tuning.result.json"

# Filter out non-executor tools (uploads, deprecated names)
SKIP_TOOLS = {"@uploaded", "fetch_urls", "scratchpad_read", "final_answer"}


def load_corpus():
    data = json.loads(CORPUS_PATH.read_text())
    out = []
    for x in data:
        if x["expected"] in SKIP_TOOLS:
            continue
        out.append((x["query"], x["expected"]))
    return out


def evaluate(catalog_list, corpus, threshold, alpha):
    """Run rank_adaptive on corpus with given threshold/alpha. Returns metrics."""
    os.environ["METNOS_SEMANTIC_THRESHOLD"] = str(threshold)
    os.environ["METNOS_SEMANTIC_ALPHA"] = str(alpha)

    n = len(corpus)
    top_k_correct = defaultdict(int)  # k -> count
    fallback_activated = 0
    lat_with_fb = []
    lat_without_fb = []
    missing_expected = 0

    for query, expected in corpus:
        t0 = time.perf_counter()
        sel, info = rank_adaptive(
            query, catalog_list, k_min=5, k_max=10,
            llm_call=None, prefer_intent=False,
        )
        el = (time.perf_counter() - t0) * 1000
        is_fb = info.get("reason") == "semantic_fallback"
        if is_fb:
            fallback_activated += 1
            lat_with_fb.append(el)
        else:
            lat_without_fb.append(el)

        names = [e.name for e in sel]
        if expected not in {e.name for e in catalog_list}:
            missing_expected += 1
            continue
        for k in (1, 3, 5, 10):
            if expected in names[:k]:
                top_k_correct[k] += 1

    def pct(n_correct):
        return 100.0 * n_correct / n if n > 0 else 0.0

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0.0

    return {
        "threshold": threshold,
        "alpha": alpha,
        "n": n,
        "missing_expected": missing_expected,
        "top1_pct": pct(top_k_correct[1]),
        "top3_pct": pct(top_k_correct[3]),
        "top5_pct": pct(top_k_correct[5]),
        "top10_pct": pct(top_k_correct[10]),
        "fallback_activated": fallback_activated,
        "fallback_rate_pct": pct(fallback_activated),
        "avg_lat_with_fb_ms": round(avg(lat_with_fb), 2),
        "avg_lat_without_fb_ms": round(avg(lat_without_fb), 2),
        "avg_lat_total_ms": round(
            (sum(lat_with_fb) + sum(lat_without_fb)) / n if n > 0 else 0, 2
        ),
    }


def main():
    print("Loading catalog…")
    invalidate_catalog_cache()
    cat = load_catalog(verify=True, include_synth=False)
    catalog_list = list(cat.executors.values())
    print(f"  {len(catalog_list)} executors loaded")

    print("Loading corpus…")
    corpus = load_corpus()
    print(f"  {len(corpus)} query/expected pairs")

    print("Warming BGE-M3 + building affinity cache…")
    t0 = time.perf_counter()
    cache = asm.build_or_load_cache(catalog_list)
    print(f"  cache shape={cache['matrix'].shape if cache else None} "
          f"in {(time.perf_counter()-t0)*1000:.0f}ms")

    # Baseline: no semantic fallback (threshold=0 → never activated;
    # plus opt-out env var to be safe).
    print("\n=== BASELINE (semantic OFF) ===")
    os.environ["METNOS_SEMANTIC_MATCH"] = "0"
    baseline = evaluate(catalog_list, corpus, threshold=0, alpha=4.0)
    baseline["label"] = "baseline (semantic OFF)"
    print(f"  top1={baseline['top1_pct']:.1f}%  "
          f"top3={baseline['top3_pct']:.1f}%  "
          f"top5={baseline['top5_pct']:.1f}%  "
          f"avg_lat={baseline['avg_lat_total_ms']:.2f}ms")

    os.environ["METNOS_SEMANTIC_MATCH"] = "1"

    # Grid
    print("\n=== GRID (threshold × alpha) ===")
    print(f"{'thr':>4} {'alpha':>6} | {'top1':>6} {'top3':>6} {'top5':>6} "
          f"| {'fb%':>5} {'lat_fb':>7} {'lat_nofb':>9} {'lat_avg':>8}")
    print("-" * 80)
    results = [baseline]
    for thr in (0, 2, 4, 6, 8, 10):
        for alpha in (2.0, 3.0, 4.0, 5.0, 6.0):
            r = evaluate(catalog_list, corpus, threshold=thr, alpha=alpha)
            results.append(r)
            print(f"{thr:>4} {alpha:>6.1f} | "
                  f"{r['top1_pct']:>5.1f}% {r['top3_pct']:>5.1f}% {r['top5_pct']:>5.1f}% "
                  f"| {r['fallback_rate_pct']:>4.1f}% {r['avg_lat_with_fb_ms']:>6.2f}ms "
                  f"{r['avg_lat_without_fb_ms']:>8.2f}ms {r['avg_lat_total_ms']:>7.2f}ms")

    # Pick Pareto-best by top1 then top3 (tie-break) with reasonable activation
    candidates = [r for r in results
                  if r.get("threshold") is not None
                  and r["fallback_rate_pct"] <= 50.0]  # ragionevole
    candidates.sort(
        key=lambda r: (-r["top1_pct"], -r["top3_pct"], r["avg_lat_total_ms"])
    )
    best = candidates[0]
    print()
    print("=== BEST by (top1, top3, latency) with fb_rate <= 50% ===")
    print(f"  threshold={best['threshold']} alpha={best['alpha']}")
    print(f"  top1={best['top1_pct']:.1f}% (+{best['top1_pct']-baseline['top1_pct']:+.1f} vs baseline)")
    print(f"  top3={best['top3_pct']:.1f}% (+{best['top3_pct']-baseline['top3_pct']:+.1f} vs baseline)")
    print(f"  fb_rate={best['fallback_rate_pct']:.1f}%  avg_lat={best['avg_lat_total_ms']:.2f}ms")

    # Save
    payload = {
        "corpus_n": len(corpus),
        "catalog_n": len(catalog_list),
        "baseline": baseline,
        "grid": [r for r in results if r is not baseline],
        "best": best,
    }
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nSaved: {RESULT_PATH}")


if __name__ == "__main__":
    main()
