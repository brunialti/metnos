#!/usr/bin/env python3
"""synt_multistage_compare.py — Phase B di ADR 0052: confronto 1:1 fra
single-prompt baseline (results_iter_1.jsonl) e multistage (results_multistage_35.jsonl)
sulle stesse 35 query non-proto-mnest.

Mapping di "correct":
  - multistage:    expected×state (ADR 0052: synthesized==new_executor, rejected==rejected)
  - single-prompt: expected×outcome (ADR 0050 published mapping)

Output: stampa la tabella delta sintetica + dettagli per query divergenti.
Salva inoltre `results_multistage_35.compare.json` per riuso da ADR.
"""
from __future__ import annotations
import json
from pathlib import Path

BASE_PATH = Path("/opt/myclaw/decisions/synt_stress/results_iter_1.jsonl")
MS_PATH = Path("/opt/myclaw/decisions/synt_stress/results_multistage_35.jsonl")
OUT_PATH = Path("/opt/myclaw/decisions/synt_stress/results_multistage_35.compare.json")


def baseline_correct(rec):
    return rec.get("expected") == rec.get("outcome")


def multistage_correct(rec):
    e, s = rec.get("expected"), rec.get("state")
    return (e == "new_executor" and s == "synthesized") or \
           (e == "rejected" and s == "rejected")


def main():
    base = [json.loads(l) for l in BASE_PATH.read_text().splitlines() if l.strip()]
    base = [r for r in base if r.get("expected") in ("new_executor", "rejected")]
    base_ix = {r["id"]: r for r in base}

    ms = [json.loads(l) for l in MS_PATH.read_text().splitlines() if l.strip()]
    ms_ix = {r["id"]: r for r in ms}

    common = sorted(base_ix.keys() & ms_ix.keys())
    print(f"common queries: {len(common)} (base={len(base)}, ms={len(ms)})")

    cats = {"new_executor": [], "rejected": []}
    for qid in common:
        b = base_ix[qid]
        m = ms_ix[qid]
        cats[b["expected"]].append((qid, b, m))

    rows = []
    rows.append(("Categoria", "Single-prompt corretti", "Multistage corretti", "Delta"))
    overall_b = 0
    overall_m = 0
    overall_n = 0
    for cat in ("new_executor", "rejected"):
        items = cats[cat]
        b_ok = sum(1 for _, b, _ in items if baseline_correct(b))
        m_ok = sum(1 for _, _, m in items if multistage_correct(m))
        n = len(items)
        rows.append((f"{cat} ({n})", f"{b_ok}/{n}", f"{m_ok}/{n}", f"{m_ok - b_ok:+d}"))
        overall_b += b_ok
        overall_m += m_ok
        overall_n += n
    rows.append(("**Total**", f"**{overall_b}/{overall_n}**", f"**{overall_m}/{overall_n}**", f"**{overall_m - overall_b:+d}**"))

    print()
    print("| " + " | ".join(rows[0]) + " |")
    print("| " + " | ".join("---" for _ in rows[0]) + " |")
    for r in rows[1:]:
        print("| " + " | ".join(r) + " |")

    print()
    print(f"Single-prompt accuracy: {100.0 * overall_b / overall_n:.1f}%")
    print(f"Multistage accuracy:    {100.0 * overall_m / overall_n:.1f}%")
    print(f"Improvement:            {100.0 * (overall_m - overall_b) / overall_n:+.1f} pp")

    # Errori multistage residui (per analisi)
    print()
    print("=== Multistage failures ===")
    fails_by_stage = {}
    for qid in common:
        m = ms_ix[qid]
        b = base_ix[qid]
        if not multistage_correct(m):
            ar = m.get("abandon_reason") or m.get("state")
            stage = "?"
            if ar and ar.startswith("stage"):
                stage = ar.split(":", 1)[0]
            fails_by_stage.setdefault(stage, []).append({
                "id": qid,
                "expected": b["expected"],
                "desired_executor": b.get("desired_executor"),
                "query": b["query"][:80],
                "ms_state": m["state"],
                "ms_name": m.get("name"),
                "abandon_reason": (m.get("abandon_reason") or "")[:150],
            })

    for stage in sorted(fails_by_stage.keys()):
        print(f"\n--- {stage} ({len(fails_by_stage[stage])} failures) ---")
        for f in fails_by_stage[stage]:
            print(f"  {f['id']:5s} exp={f['expected']:12s} desired={f['desired_executor']!s} "
                  f"got={f['ms_state']}/{f['ms_name']!s}")
            print(f"        query: {f['query']}")
            print(f"        reason: {f['abandon_reason']}")

    # Dump JSON per ADR
    delta = {
        "common": len(common),
        "categories": {
            cat: {
                "n": len(cats[cat]),
                "single_prompt_correct": sum(1 for _, b, _ in cats[cat] if baseline_correct(b)),
                "multistage_correct": sum(1 for _, _, m in cats[cat] if multistage_correct(m)),
            } for cat in ("new_executor", "rejected")
        },
        "overall": {"n": overall_n, "single_prompt": overall_b, "multistage": overall_m,
                    "single_prompt_pct": round(100.0 * overall_b / overall_n, 1),
                    "multistage_pct": round(100.0 * overall_m / overall_n, 1)},
        "multistage_failures_by_stage": {k: v for k, v in fails_by_stage.items()},
    }
    OUT_PATH.write_text(json.dumps(delta, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
