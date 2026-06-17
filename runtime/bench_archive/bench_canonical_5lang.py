#!/usr/bin/env python3
"""bench_canonical_5lang — #14 bench canonical_query 100q × 5 lingue.

Misura la signal-quality del match BGE-M3 cosine query↔canonical su un
corpus multi-lingua di 100 query con varianti morfologiche e clitiche.
(NB 11/6/2026: il matcher L1 `canonical_matcher` che usava questo segnale
e' stato ritirato; il bench resta valido come misura della qualita'
dell'embedder sul corpus canonical.)

Schema corpus (jsonl): `{lang, query, expected_canonical, expected_executor}`.

Per ogni entry:
  emb_q = BGE.embed(query)
  emb_c = BGE.embed(expected_canonical)
  cosine = emb_q · emb_c  (BGE returns L2-normalized → dot product)

Gate test:
  pass = cosine >= THRESHOLD (default 0.7 == DEFAULT_THRESHOLD canonical_matcher).

Output:
  - stdout: tabella per-lingua (precision, median cosine, min, max).
  - JSONL: ~/.local/share/metnos/bench_canonical_<ts>.jsonl con tutte le entries.

Uso:
    python3 runtime/bench_canonical_5lang.py [--threshold 0.7] [--corpus PATH]

Determinismo §7.9: niente LLM, solo encoder ONNX int8 BGE-M3.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or str(Path(__file__).resolve().parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def main():
    ap = argparse.ArgumentParser(description="Bench canonical_query 5 lingue")
    ap.add_argument("--corpus", default=str(Path(__file__).resolve().parent /
                                              "bench_corpora" /
                                              "canonical_5lang_100q.jsonl"))
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--output-dir", default=str(Path.home() / ".local" / "share" / "metnos"))
    args = ap.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.is_file():
        print(f"corpus non trovato: {corpus_path}", file=sys.stderr)
        sys.exit(2)

    entries = []
    with corpus_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    print(f"[bench_canonical] corpus={len(entries)} queries, threshold={args.threshold}")

    try:
        from bge_embedding import BGEEmbeddingService
        emb = BGEEmbeddingService()
    except Exception as ex:
        print(f"BGE-M3 init failed: {ex}", file=sys.stderr)
        sys.exit(3)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"bench_canonical_{ts}.jsonl"

    by_lang: dict[str, list[dict]] = {}
    t0 = time.time()
    for i, entry in enumerate(entries):
        lang = entry.get("lang", "??")
        q = entry["query"]
        c = entry["expected_canonical"]
        try:
            qv = emb.embed_query(q)
            cv = emb.embed_query(c)
            import numpy as _np
            qv = _np.asarray(qv, dtype=_np.float32)
            cv = _np.asarray(cv, dtype=_np.float32)
            # BGE retorna L2-normalized; dot product = cosine.
            cosine = float(qv @ cv)
        except Exception as ex:
            cosine = -1.0
            print(f"  [{i+1}/{len(entries)}] {lang} ERROR: {ex}")
            continue
        passed = cosine >= args.threshold
        rec = {
            "lang": lang, "query": q, "canonical": c,
            "expected_executor": entry.get("expected_executor"),
            "cosine": round(cosine, 4), "passed": passed,
        }
        by_lang.setdefault(lang, []).append(rec)
        if (i + 1) % 20 == 0:
            print(f"  ... {i+1}/{len(entries)} done")

    elapsed = time.time() - t0
    with out_file.open("a") as f:
        for lang, lst in by_lang.items():
            for r in lst:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=== RESULTS (elapsed {elapsed:.1f}s, threshold={args.threshold}) ===")
    print(f"{'lang':<6} {'n':>4} {'pass':>5} {'%':>5} {'median':>8} {'min':>6} {'max':>6}")
    print("-" * 50)
    for lang in sorted(by_lang):
        lst = by_lang[lang]
        cs = [r["cosine"] for r in lst if r["cosine"] >= 0]
        if not cs:
            continue
        n_pass = sum(1 for r in lst if r["passed"])
        pct = 100 * n_pass // len(lst)
        med = statistics.median(cs)
        print(f"{lang:<6} {len(lst):>4} {n_pass:>5} {pct:>4}% "
              f"{med:>8.3f} {min(cs):>6.3f} {max(cs):>6.3f}")
    # Aggregate
    all_recs = [r for lst in by_lang.values() for r in lst]
    all_pass = sum(1 for r in all_recs if r["passed"])
    if all_recs:
        all_pct = 100 * all_pass // len(all_recs)
        all_med = statistics.median([r["cosine"] for r in all_recs if r["cosine"] >= 0])
        print(f"{'TOT':<6} {len(all_recs):>4} {all_pass:>5} {all_pct:>4}% "
              f"{all_med:>8.3f}")
    print(f"\n[bench_canonical] output: {out_file}")
    # Exit code: 0 se >=90% global precision, altrimenti 1
    if all_recs and (all_pass / len(all_recs)) < 0.90:
        sys.exit(1)


if __name__ == "__main__":
    main()
