"""bench_minilm_vs_bge.py — confronto diretto MiniLM 384d vs bge-m3 int8 1024d.

Stesso corpus categorizzato + stesse 4 formulazioni del testo. Per ogni
combinazione (modello × variante × @K) calcola precision e tempo.

Run:
  /opt/suprastructure/.venv/bin/python /opt/myclaw/runtime/bench_minilm_vs_bge.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, "/opt/myclaw/runtime")
sys.path.insert(0, "/opt/suprastructure/src")

import numpy as np  # noqa: E402

from loader import load_catalog  # noqa: E402
from prefilter import rank_adaptive  # noqa: E402
from suprastructure.embedding.onnx_embedding import EmbeddingService  # noqa: E402
from bge_embedding import BGEEmbeddingService  # noqa: E402
from bench_prefilter_categorized import CORPUS  # noqa: E402
from bench_text_formulation import (  # noqa: E402
    text_v1_baseline, text_v2_first_sent, text_v3_affinity2x, text_v4_keywords,
)

KS = (3, 5, 8)


def main():
    print("loading catalog...", flush=True)
    cat = load_catalog(verify=True)
    execs = list(cat.executors.values())
    print(f"  {len(execs)} executors")

    print("loading models...", flush=True)
    t0 = time.perf_counter()
    minilm = EmbeddingService(model_dir="/opt/myclaw/models/embedding")
    minilm.embed_query("warmup")
    print(f"  MiniLM 384d ready in {(time.perf_counter()-t0)*1000:.0f} ms")
    t0 = time.perf_counter()
    bge = BGEEmbeddingService(model_dir="/opt/myclaw/models/embedding-bge")
    bge.embed_query("warmup")
    print(f"  bge-m3 int8 1024d ready in {(time.perf_counter()-t0)*1000:.0f} ms")

    cat_names = {e.name for e in execs}
    valid = [(c, q, gt & cat_names) for c, q, gt in CORPUS if (gt & cat_names)]
    n = len(valid)
    print(f"  corpus: {n} query con GT")

    queries = [q for _, q, _ in valid]
    gts = [gt for _, _, gt in valid]

    # Embeddo le query con entrambi i modelli (una volta sola).
    print("embedding queries...", flush=True)
    t0 = time.perf_counter()
    q_minilm = minilm.embed_texts(queries)
    print(f"  MiniLM: {(time.perf_counter()-t0)*1000:.0f} ms")
    t0 = time.perf_counter()
    q_bge = bge.embed_texts(queries)
    print(f"  bge-m3: {(time.perf_counter()-t0)*1000:.0f} ms")

    VARIANTS = [
        ("V1", text_v1_baseline),
        ("V2", text_v2_first_sent),
        ("V3", text_v3_affinity2x),
        ("V4", text_v4_keywords),
    ]

    print()
    print("═" * 80)
    print(f"BENCH MiniLM vs bge-m3 ({n} query, {len(execs)} executors)")
    print("═" * 80)
    print()

    # Anche token-only baseline
    print(">>> TOKEN baseline (rank_adaptive)")
    t0 = time.perf_counter()
    rec_token = {k: 0 for k in KS}
    for i, (cat_name, q, gt) in enumerate(valid):
        out, _ = rank_adaptive(q, cat, k_min=5, k_max=10)
        names = [e.name for e in out]
        for k in KS:
            if gt & set(names[:k]):
                rec_token[k] += 1
    tok_time = (time.perf_counter()-t0)*1000
    print(f"  @3={100*rec_token[3]/n:.1f}% @5={100*rec_token[5]/n:.1f}% @8={100*rec_token[8]/n:.1f}%")
    print(f"  tot {tok_time:.0f} ms ({tok_time/n:.2f} ms/query)")

    results = {}
    for vname, vfunc in VARIANTS:
        cat_texts = [vfunc(e) for e in execs]
        avg_len = sum(len(t) for t in cat_texts) / len(cat_texts)
        for mlabel, model, query_embs in [("MiniLM", minilm, q_minilm),
                                              ("bge-m3", bge, q_bge)]:
            t0 = time.perf_counter()
            cat_e = model.embed_texts(cat_texts)
            cat_embed_ms = (time.perf_counter()-t0)*1000
            rec = {k: 0 for k in KS}
            for i, gt in enumerate(gts):
                qv = query_embs[i]
                scores = cat_e @ qv
                order = np.argsort(-scores)[:max(KS)]
                top = [execs[j].name for j in order]
                for k in KS:
                    if gt & set(top[:k]):
                        rec[k] += 1
            results[(vname, mlabel)] = {
                "rec": {k: rec[k] for k in KS},
                "cat_embed_ms": cat_embed_ms,
                "avg_len": avg_len,
            }

    # Tabella riassuntiva
    print()
    print(f"{'variante':<8} {'avg_len':>8} {'modello':<10} "
          f"{'@3':>8} {'@5':>8} {'@8':>8} {'cat_emb_ms':>11}")
    for vname, _ in VARIANTS:
        for mlabel in ("MiniLM", "bge-m3"):
            r = results[(vname, mlabel)]
            print(f"  {vname:<6} {r['avg_len']:>7.0f} {mlabel:<10} "
                  f"{100*r['rec'][3]/n:>7.1f}% {100*r['rec'][5]/n:>7.1f}% "
                  f"{100*r['rec'][8]/n:>7.1f}% {r['cat_embed_ms']:>10.0f}")

    # Hybrid (token + bge-m3 V3) vs hybrid (token + MiniLM V3)
    print()
    print(">>> HYBRID = 0.4*token + 0.6*embed (V3 first_sent + affinity*2)")
    cat_text_v3 = [text_v3_affinity2x(e) for e in execs]
    hybrid_results = {}
    for mlabel, model, q_embs in [("MiniLM", minilm, q_minilm), ("bge-m3", bge, q_bge)]:
        cat_e = model.embed_texts(cat_text_v3)
        rec = {k: 0 for k in KS}
        for i, (cat_name, q, gt) in enumerate(valid):
            # token side
            out, _ = rank_adaptive(q, cat, k_min=5, k_max=10)
            tok_names = [e.name for e in out]
            tok_score = {n: 1.0 - (i_/len(tok_names)) for i_, n in enumerate(tok_names)}
            # embed side
            qv = q_embs[i]
            scores = cat_e @ qv
            hybrid_pairs = []
            for j, e in enumerate(execs):
                t = tok_score.get(e.name, 0.0)
                em = float(scores[j])
                hybrid_pairs.append((0.4*t + 0.6*em, e.name))
            hybrid_top = [n for _, n in sorted(hybrid_pairs, reverse=True)[:max(KS)]]
            for k in KS:
                if gt & set(hybrid_top[:k]):
                    rec[k] += 1
        hybrid_results[mlabel] = rec
        print(f"  {mlabel}: @3={100*rec[3]/n:.1f}% @5={100*rec[5]/n:.1f}% @8={100*rec[8]/n:.1f}%")

    # Salva JSON
    out = {
        "n_queries": n,
        "n_executors": len(execs),
        "token_only": {f"@{k}": rec_token[k] for k in KS},
        "embed_only": {f"{v}_{m}": {f"@{k}": results[(v,m)]["rec"][k] for k in KS}
                        for v,_ in VARIANTS for m in ("MiniLM","bge-m3")},
        "hybrid_v3": {m: {f"@{k}": hybrid_results[m][k] for k in KS}
                       for m in ("MiniLM","bge-m3")},
    }
    Path("/opt/myclaw/runtime/bench_minilm_vs_bge.result.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )
    print(f"\n  → JSON → /opt/myclaw/runtime/bench_minilm_vs_bge.result.json")


if __name__ == "__main__":
    main()
