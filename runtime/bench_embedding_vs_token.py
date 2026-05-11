"""bench_embedding_vs_token.py — benchmark prefilter token-based vs embedding.

Esegue 374 query reali estratte dai turn log (~/.local/share/metnos/turns/*.jsonl)
contro il catalog corrente. Per ogni query:
  - token_rank   = prefilter.rank_adaptive(query, catalog)
  - embed_rank   = top-K cosine fra query embed e catalog embeds (MiniLM 384d)

Ground truth = set di `chosen_tool` osservati nel turno (esclusa final_answer).
Metriche: Recall@K (intersezione non vuota), Recall_full@K (GT ⊆ topK),
latenza per query, overlap fra le due classifiche.

Run:
  /opt/suprastructure/.venv/bin/python /opt/myclaw/runtime/bench_embedding_vs_token.py
"""
from __future__ import annotations

import glob
import json
import statistics
import sys
import time
from pathlib import Path

# bridge sys.path: cryptography from system, runtime + suprastructure src
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, "/opt/myclaw/runtime")
sys.path.insert(0, "/opt/suprastructure/src")

import numpy as np  # noqa: E402

from loader import load_catalog  # noqa: E402
from prefilter import rank_adaptive  # noqa: E402
from suprastructure.embedding.onnx_embedding import EmbeddingService  # noqa: E402

MODEL_DIR = "/opt/giorgio2/models/onnx"
TURNS_GLOB = "/home/roberto/.local/share/metnos/turns/*.jsonl"
KS = (5, 8, 10)


def load_real_queries() -> list[tuple[str, set[str]]]:
    """Returns [(user_query, ground_truth_tools), ...] deduplicated by query."""
    by_query: dict[str, set[str]] = {}
    for fn in sorted(glob.glob(TURNS_GLOB)):
        try:
            with open(fn) as f:
                for line in f:
                    try:
                        t = json.loads(line)
                    except Exception:
                        continue
                    q = (t.get("user_query") or "").strip()
                    if not q or len(q) < 3:
                        continue
                    tools = {
                        s.get("chosen_tool")
                        for s in t.get("steps", [])
                        if s.get("chosen_tool") and s["chosen_tool"] != "final_answer"
                    }
                    by_query.setdefault(q, set()).update(tools)
        except Exception:
            continue
    return [(q, tools) for q, tools in by_query.items() if tools]


def build_executor_text(e) -> str:
    desc = (e.description or "").strip()
    kws = []
    aff = getattr(e, "affinity", None) or {}
    if isinstance(aff, dict):
        for v in aff.values():
            if isinstance(v, list):
                kws.extend(str(x) for x in v)
            elif isinstance(v, str):
                kws.append(v)
    kws_s = " ".join(kws)
    return f"{e.name}: {desc} [{kws_s}]"


def main():
    print("loading catalog...", flush=True)
    cat = load_catalog(verify=True)
    execs = list(cat.executors.values())
    name_to_idx = {e.name: i for i, e in enumerate(execs)}
    print(f"  catalog: {len(execs)} executors")

    print("loading embedding model (ONNX MiniLM 384d, paraphrase-multilingual-MiniLM-L12-v2)...", flush=True)
    t0 = time.perf_counter()
    emb = EmbeddingService(model_dir=MODEL_DIR)
    # warmup load
    _ = emb.embed_query("warmup")
    t1 = time.perf_counter()
    print(f"  load+warmup: {(t1-t0)*1000:.0f} ms")

    print("embedding catalog...", flush=True)
    t0 = time.perf_counter()
    cat_texts = [build_executor_text(e) for e in execs]
    cat_emb = emb.embed_texts(cat_texts)  # (N, 384) L2-normalized
    t1 = time.perf_counter()
    print(f"  embed {len(execs)} executors: {(t1-t0)*1000:.0f} ms")

    print("loading real queries...", flush=True)
    queries = load_real_queries()
    # query con ground truth (tool ∈ catalog corrente)
    cat_names = set(name_to_idx)
    queries = [(q, gt & cat_names) for q, gt in queries]
    queries = [(q, gt) for q, gt in queries if gt]
    print(f"  {len(queries)} unique queries with at least 1 in-catalog tool as GT")

    # ── benchmark ─────────────────────────────────────────────────
    rec_token = {k: 0 for k in KS}
    rec_emb = {k: 0 for k in KS}
    recfull_token = {k: 0 for k in KS}
    recfull_emb = {k: 0 for k in KS}
    overlap5 = []
    lat_token_ms = []
    lat_emb_ms = []
    divergences = []  # esempi di disaccordo

    for i, (q, gt) in enumerate(queries):
        # token side
        t0 = time.perf_counter()
        out_token, _ = rank_adaptive(q, cat, k_min=5, k_max=10)
        t1 = time.perf_counter()
        lat_token_ms.append((t1 - t0) * 1000)
        token_top = [e.name for e in out_token]

        # embedding side
        t0 = time.perf_counter()
        qv = emb.embed_query(q)  # (384,)
        scores = cat_emb @ qv     # (N,)
        order = np.argsort(-scores)[:max(KS)]
        emb_top = [execs[idx].name for idx in order]
        t1 = time.perf_counter()
        lat_emb_ms.append((t1 - t0) * 1000)

        for k in KS:
            t_topk = set(token_top[:k])
            e_topk = set(emb_top[:k])
            if gt & t_topk:
                rec_token[k] += 1
            if gt & e_topk:
                rec_emb[k] += 1
            if gt.issubset(t_topk):
                recfull_token[k] += 1
            if gt.issubset(e_topk):
                recfull_emb[k] += 1
            if k == 5:
                overlap5.append(len(t_topk & e_topk))

        # divergenze interessanti: emb hit GT, token miss (a K=5)
        t5 = set(token_top[:5])
        e5 = set(emb_top[:5])
        if (gt & e5) and not (gt & t5) and len(divergences) < 10:
            divergences.append({
                "kind": "embed_wins",
                "query": q,
                "gt": sorted(gt),
                "token5": token_top[:5],
                "emb5": emb_top[:5],
            })
        elif (gt & t5) and not (gt & e5) and len([d for d in divergences if d["kind"] == "token_wins"]) < 10:
            divergences.append({
                "kind": "token_wins",
                "query": q,
                "gt": sorted(gt),
                "token5": token_top[:5],
                "emb5": emb_top[:5],
            })

        if (i + 1) % 50 == 0:
            print(f"  ... {i+1}/{len(queries)}", flush=True)

    N = len(queries)

    def pct(c): return 100 * c / N if N else 0.0

    def stat(arr):
        return f"mean={statistics.mean(arr):.2f} ms · p50={statistics.median(arr):.2f} · p95={sorted(arr)[int(0.95*len(arr))]:.2f}"

    print()
    print("═" * 70)
    print(f"BENCHMARK — {N} query reali · catalog {len(execs)} executor")
    print("═" * 70)
    print()
    print("Recall@K (∃ GT ∈ topK)")
    print(f"  {'K':<4} {'token':>10} {'embedding':>12}")
    for k in KS:
        print(f"  {k:<4} {pct(rec_token[k]):>9.1f}%  {pct(rec_emb[k]):>11.1f}%")
    print()
    print("Recall_full@K (GT ⊆ topK)")
    print(f"  {'K':<4} {'token':>10} {'embedding':>12}")
    for k in KS:
        print(f"  {k:<4} {pct(recfull_token[k]):>9.1f}%  {pct(recfull_emb[k]):>11.1f}%")
    print()
    print("Latenza per query")
    print(f"  token      {stat(lat_token_ms)}")
    print(f"  embedding  {stat(lat_emb_ms)}")
    print()
    print(f"Overlap medio top-5 (su {len(overlap5)} query): {statistics.mean(overlap5):.2f} / 5")
    print()
    print("─── divergenze (esempi) ───")
    for d in divergences[:8]:
        marker = "✓EMB" if d["kind"] == "embed_wins" else "✓TOK"
        print(f"\n  [{marker}] q: {d['query'][:80]}")
        print(f"        GT: {d['gt']}")
        print(f"     token5: {d['token5']}")
        print(f"       emb5: {d['emb5']}")

    out = {
        "n_queries": N,
        "n_executors": len(execs),
        "recall_at_k": {str(k): {"token": rec_token[k] / N, "embedding": rec_emb[k] / N} for k in KS},
        "recall_full_at_k": {str(k): {"token": recfull_token[k] / N, "embedding": recfull_emb[k] / N} for k in KS},
        "latency_ms": {
            "token": {"mean": statistics.mean(lat_token_ms), "p95": sorted(lat_token_ms)[int(0.95*len(lat_token_ms))]},
            "embedding": {"mean": statistics.mean(lat_emb_ms), "p95": sorted(lat_emb_ms)[int(0.95*len(lat_emb_ms))]},
        },
        "overlap_top5_mean": statistics.mean(overlap5),
        "divergences_sample": divergences,
    }
    out_path = Path("/opt/myclaw/runtime/bench_embedding_vs_token.result.json")
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n  → JSON → {out_path}")


if __name__ == "__main__":
    main()
