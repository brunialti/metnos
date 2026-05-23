#!/usr/bin/env python3
"""synt_multistage_compare_v1v2.py — Phase B di ADR 0052: confronto 1:1 fra
multistage v1 (results_multistage_35.jsonl) e multistage v2
(results_multistage_35_v2.jsonl) sulle stesse 35 query non-proto-mnest.

Mapping di "correct" identico per entrambe le versioni:
  expected==new_executor && state==synthesized → corretto
  expected==rejected     && state==rejected    → corretto

Output: tabella delta + dettagli per categoria (recovered/regressed/
same fail/same pass), JSON salvato in
`results_multistage_35_v2.compare.json` per riuso da ADR.
"""
from __future__ import annotations
import json
from pathlib import Path

V1_PATH = Path(__file__).resolve().parents[2] / "decisions/synt_stress/results_multistage_35.jsonl"
V2_PATH = Path(__file__).resolve().parents[2] / "decisions/synt_stress/results_multistage_35_v2.jsonl"
OUT_PATH = Path(__file__).resolve().parents[2] / "decisions/synt_stress/results_multistage_35_v2.compare.json"


def correct(rec):
    e, s = rec.get("expected"), rec.get("state")
    return (e == "new_executor" and s == "synthesized") or \
           (e == "rejected" and s == "rejected")


def load_jsonl(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main():
    v1 = load_jsonl(V1_PATH)
    v2 = load_jsonl(V2_PATH)
    v1_ix = {r["id"]: r for r in v1}
    v2_ix = {r["id"]: r for r in v2}

    common = sorted(v1_ix.keys() & v2_ix.keys())
    print(f"common queries: {len(common)} (v1={len(v1)}, v2={len(v2)})")

    recovered = []
    regressed = []
    same_pass = []
    same_fail = []

    for qid in common:
        a = v1_ix[qid]
        b = v2_ix[qid]
        ca = correct(a)
        cb = correct(b)
        row = {
            "id": qid,
            "expected": a.get("expected"),
            "query": (a.get("query") or "")[:80],
            "v1_state": a.get("state"),
            "v1_name": a.get("name"),
            "v1_reason": (a.get("abandon_reason") or "")[:120],
            "v2_state": b.get("state"),
            "v2_name": b.get("name"),
            "v2_reason": (b.get("abandon_reason") or "")[:120],
            "v1_correct": ca,
            "v2_correct": cb,
        }
        if ca and cb:
            same_pass.append(row)
        elif (not ca) and (not cb):
            same_fail.append(row)
        elif (not ca) and cb:
            recovered.append(row)
        else:  # ca and not cb
            regressed.append(row)

    # categories accuracy
    cats = {"new_executor": [0, 0, 0], "rejected": [0, 0, 0]}  # n, v1ok, v2ok
    for qid in common:
        a = v1_ix[qid]
        b = v2_ix[qid]
        cat = a["expected"]
        cats[cat][0] += 1
        if correct(a):
            cats[cat][1] += 1
        if correct(b):
            cats[cat][2] += 1

    overall_n = sum(c[0] for c in cats.values())
    overall_v1 = sum(c[1] for c in cats.values())
    overall_v2 = sum(c[2] for c in cats.values())

    print()
    print(f"| Categoria | v1 corretti | v2 corretti | Delta |")
    print(f"| --- | --- | --- | --- |")
    for cat, (n, ok1, ok2) in cats.items():
        print(f"| {cat} ({n}) | {ok1}/{n} | {ok2}/{n} | {ok2-ok1:+d} |")
    print(f"| **Total** | **{overall_v1}/{overall_n}** | **{overall_v2}/{overall_n}** | **{overall_v2-overall_v1:+d}** |")

    print()
    print(f"v1 accuracy: {100.0*overall_v1/overall_n:.1f}%")
    print(f"v2 accuracy: {100.0*overall_v2/overall_n:.1f}%")
    print(f"Improvement: {100.0*(overall_v2-overall_v1)/overall_n:+.1f} pp")

    print()
    print(f"Recovered  ({len(recovered)}): query che falliva in v1 e ora passa in v2")
    for r in recovered:
        print(f"  {r['id']:5s} {r['expected']:12s} q={r['query']!r}")
        print(f"        v1: {r['v1_state']}/{r['v1_name']!s} reason={r['v1_reason']}")
        print(f"        v2: {r['v2_state']}/{r['v2_name']!s}")

    print()
    print(f"Regressed  ({len(regressed)}): query che passava in v1 e ora fallisce in v2 (anomalo)")
    for r in regressed:
        print(f"  {r['id']:5s} {r['expected']:12s} q={r['query']!r}")
        print(f"        v1: {r['v1_state']}/{r['v1_name']!s}")
        print(f"        v2: {r['v2_state']}/{r['v2_name']!s} reason={r['v2_reason']}")

    print()
    print(f"Same pass  ({len(same_pass)}): invariate, OK in entrambe")
    print(f"Same fail  ({len(same_fail)}): invariate, FAIL in entrambe")
    for r in same_fail:
        print(f"  {r['id']:5s} {r['expected']:12s}")
        print(f"        v1: {r['v1_state']}/{r['v1_name']!s} reason={r['v1_reason']}")
        print(f"        v2: {r['v2_state']}/{r['v2_name']!s} reason={r['v2_reason']}")

    delta = {
        "common": len(common),
        "categories": {
            cat: {
                "n": cats[cat][0],
                "v1_correct": cats[cat][1],
                "v2_correct": cats[cat][2],
            } for cat in cats
        },
        "overall": {
            "n": overall_n,
            "v1": overall_v1, "v2": overall_v2,
            "v1_pct": round(100.0*overall_v1/overall_n, 1),
            "v2_pct": round(100.0*overall_v2/overall_n, 1),
            "delta_pp": round(100.0*(overall_v2-overall_v1)/overall_n, 1),
        },
        "recovered": recovered,
        "regressed": regressed,
        "same_pass_ids": [r["id"] for r in same_pass],
        "same_fail": same_fail,
    }
    OUT_PATH.write_text(json.dumps(delta, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
