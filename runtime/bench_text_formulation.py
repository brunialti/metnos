"""bench_text_formulation.py — confronta 4 formulazioni del testo che
embeddiamo per ogni executor, sullo stesso modello (MiniLM 384d).

Domanda: per il match keyword-friendly che ci serve nel prefilter, conviene
embeddare la description completa o un sottoinsieme piu' focalizzato?

Varianti testate:
  V1 baseline   : "name: full_description [affinity_flat]"   (oggi)
  V2 first-sent : "name: first_sentence_of_description [affinity_flat]"
  V3 affinity2x : "name: first_sentence [affinity_flat affinity_flat]"
  V4 keywords   : "name: [affinity_flat]" (nessuna description)

Bench corpus: lo stesso `bench_prefilter_categorized.CORPUS` (91 query con GT).
Metriche: recall@K e per-categoria.

Run:
  /opt/suprastructure/.venv/bin/python <install_root>/runtime/bench_text_formulation.py
"""
from __future__ import annotations

import json
import re
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")
_RUNTIME = os.environ.get("METNOS_RUNTIME") or str(Path(__file__).resolve().parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
sys.path.insert(0, "/opt/suprastructure/src")

import numpy as np  # noqa: E402

from loader import load_catalog  # noqa: E402
from suprastructure.embedding.onnx_embedding import EmbeddingService  # noqa: E402
from bench_prefilter_categorized import CORPUS  # noqa: E402

MODEL_DIR_FN = lambda: str(Path(_RUNTIME).parent / "models" / "embedding")
KS = (3, 5, 8)


def _aff_flat(e) -> str:
    aff = getattr(e, "affinity", None) or []
    if isinstance(aff, list):
        return " ".join(str(x) for x in aff)
    return ""


def _first_sentence(text: str) -> str:
    """Estrae la prima frase compatta. Fallback alla prima riga; cap 250 char."""
    if not text:
        return ""
    text = text.strip()
    # Spezza alla prima sequenza terminale "." ! ?  oppure newline doppia
    m = re.search(r"^(.{20,400}?[.!?])\s", text + " ")
    if m:
        return m.group(1).strip()
    # Fallback: prima riga non vuota
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) > 20:
            return line[:250]
    return text[:250]


def text_v1_baseline(e) -> str:
    return f"{e.name}: {(e.description or '').strip()} [{_aff_flat(e)}]"


def text_v2_first_sent(e) -> str:
    fs = _first_sentence(e.description or "")
    return f"{e.name}: {fs} [{_aff_flat(e)}]"


def text_v3_affinity2x(e) -> str:
    fs = _first_sentence(e.description or "")
    aff = _aff_flat(e)
    return f"{e.name}: {fs} [{aff} {aff}]"


def text_v4_keywords(e) -> str:
    return f"{e.name}: [{_aff_flat(e)}]"


VARIANTS = [
    ("V1_baseline", text_v1_baseline),
    ("V2_first_sent", text_v2_first_sent),
    ("V3_affinity2x", text_v3_affinity2x),
    ("V4_keywords_only", text_v4_keywords),
]


def main():
    print("loading catalog...", flush=True)
    cat = load_catalog(verify=True)
    execs = list(cat.executors.values())
    print(f"  catalog: {len(execs)} executors")

    print("loading embedding...", flush=True)
    emb = EmbeddingService(model_dir=MODEL_DIR)
    _ = emb.embed_query("warmup")

    cat_names = {e.name for e in execs}
    valid_corpus = [(c, q, gt & cat_names) for c, q, gt in CORPUS]
    valid_corpus = [(c, q, gt) for c, q, gt in valid_corpus if gt]
    n_total = len(valid_corpus)
    print(f"  corpus: {n_total} query con GT")

    # Embeddo le query una volta sola (riusiamo per tutte le varianti).
    queries_text = [q for _, q, _ in valid_corpus]
    print("embedding queries (one-shot)...", flush=True)
    t0 = time.perf_counter()
    q_embs = emb.embed_texts(queries_text)
    print(f"  {len(queries_text)} queries: {(time.perf_counter()-t0)*1000:.0f} ms")

    # Per ogni variante: ri-embeddo il catalog con quel formato, computo
    # recall@K aggregato + per categoria.
    summary = {}
    for vname, vfunc in VARIANTS:
        print(f"\n>>> variante {vname}", flush=True)
        cat_texts = [vfunc(e) for e in execs]
        avg_len = sum(len(t) for t in cat_texts) / len(cat_texts)
        max_len = max(len(t) for t in cat_texts)
        t0 = time.perf_counter()
        cat_emb = emb.embed_texts(cat_texts)
        emb_ms = (time.perf_counter() - t0) * 1000
        print(f"  text: avg={avg_len:.0f} char · max={max_len} char")
        print(f"  embed catalog: {emb_ms:.0f} ms")

        rec_k = {k: 0 for k in KS}
        rec_per_cat = {}
        for i, (category, _q, gt) in enumerate(valid_corpus):
            qv = q_embs[i]
            scores = cat_emb @ qv
            order = np.argsort(-scores)[:max(KS)]
            top = [execs[j].name for j in order]
            rec_per_cat.setdefault(category, {"n": 0, "@3": 0, "@5": 0})
            rec_per_cat[category]["n"] += 1
            for k in KS:
                if gt & set(top[:k]):
                    rec_k[k] += 1
            if gt & set(top[:3]):
                rec_per_cat[category]["@3"] += 1
            if gt & set(top[:5]):
                rec_per_cat[category]["@5"] += 1

        agg = {f"@{k}": 100.0 * rec_k[k] / n_total for k in KS}
        summary[vname] = {
            "agg": agg,
            "per_cat": rec_per_cat,
            "text_avg_len": avg_len,
            "text_max_len": max_len,
        }
        print(f"  AGG @3={agg['@3']:.1f}% @5={agg['@5']:.1f}% @8={agg['@8']:.1f}%")

    # ── tabella di confronto ────────────────────────────────────────
    print()
    print("═" * 75)
    print(f"CONFRONTO VARIANTI ({n_total} query con GT)")
    print("═" * 75)
    print()
    print(f"{'variante':<22} {'avg_len':>8} {'@3':>8} {'@5':>8} {'@8':>8}")
    for vname, _ in VARIANTS:
        s = summary[vname]
        print(f"  {vname:<20} {s['text_avg_len']:>7.0f}  "
              f"{s['agg']['@3']:>7.1f}% {s['agg']['@5']:>7.1f}% {s['agg']['@8']:>7.1f}%")

    # Per categoria, mostra @3 di ogni variante
    print()
    print("Per categoria (@3):")
    cats = sorted({c for vname, _ in VARIANTS for c in summary[vname]["per_cat"]})
    head = "  " + f"{'cat':<14}" + "".join(f" {vname[:14]:>14}" for vname, _ in VARIANTS)
    print(head)
    for c in cats:
        row = f"  {c:<14}"
        for vname, _ in VARIANTS:
            pc = summary[vname]["per_cat"].get(c, {"n": 0, "@3": 0})
            if pc["n"] == 0:
                row += f" {'-':>14}"
            else:
                pct = 100.0 * pc["@3"] / pc["n"]
                row += f" {pct:>13.1f}%"
        print(row)

    out = Path(__file__).resolve().parents[1] / "runtime/bench_text_formulation.result.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n  → JSON → {out}")


if __name__ == "__main__":
    main()
