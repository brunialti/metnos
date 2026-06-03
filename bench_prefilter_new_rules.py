#!/usr/bin/env python3
"""Bench prefilter porting rules tipate (input_coverage + schema_field).

Confronta top-1/3/5 accuracy con METNOS_PREFILTER_RULES=0 vs =1 su FROZEN-446.

§7.3: nessun hardcoded executor/query — usa solo file FROZEN + catalog runtime.
§7.9: deterministico — niente LLM call (intent extractor disattivato per
isolare l'effetto del prefilter ranking puro).

Eseguito standalone: python3 /opt/metnos/bench_prefilter_new_rules.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path("/opt/metnos")
sys.path.insert(0, str(ROOT / "runtime"))


def load_test_set() -> list[dict]:
    for cand in ("test_set_FROZEN_v2.json", "test_set_FROZEN.json"):
        p = ROOT / "e2e" / "simulator" / cand
        if p.exists():
            print(f"[bench] loaded {p.name}: ", end="")
            data = json.loads(p.read_text())
            print(f"{len(data)} queries")
            return data
    raise SystemExit("no test set found")


def load_catalog():
    """Carica catalog runtime read-only."""
    from loader import load_catalog as _lc
    cat = _lc()
    return cat


def acc_at_k(query: str, expected: list[str], catalog, *, k: int) -> tuple[bool, list[str]]:
    """Ranking puro bag-of-words + rules (NO intent extractor LLM).

    Match: hit se almeno UN expected (o accepted_first) compare nei top-k.
    """
    from prefilter import _rank_adaptive_legacy
    picked, _info = _rank_adaptive_legacy(
        query, catalog, k_min=k, k_max=k, llm_call=None, prefer_intent=False,
    )
    names = [e.name for e in picked]
    return any(n in names for n in expected), names


def run(test_set, catalog, *, rules_enabled: bool) -> dict:
    os.environ["METNOS_PREFILTER_RULES"] = "1" if rules_enabled else "0"
    try:
        import prefilter_rules
        prefilter_rules._RARE_TOKENS_CACHE = None
    except Exception:
        pass
    top1 = top3 = top5 = total = 0
    for q in test_set:
        query = q.get("query", "")
        if not query:
            continue
        expected = list(q.get("expected_path") or [])
        expected += list(q.get("accepted_first") or [])
        if not expected:
            continue
        total += 1
        _hit5, names = acc_at_k(query, expected, catalog, k=5)
        top5_names = names[:5]
        top3_names = names[:3]
        if top5_names and any(n in expected for n in top5_names):
            top5 += 1
        if top3_names and any(n in expected for n in top3_names):
            top3 += 1
        if names and names[0] in expected:
            top1 += 1
    return {"total": total, "top1": top1, "top3": top3, "top5": top5,
            "p1": top1 / total if total else 0,
            "p3": top3 / total if total else 0,
            "p5": top5 / total if total else 0}


def main():
    test_set = load_test_set()
    print("[bench] loading catalog...")
    os.environ.setdefault("METNOS_LOADER_VERIFY", "0")
    catalog = load_catalog()
    print(f"[bench] catalog: {len(catalog)} executors")
    print()
    print("[bench] BASELINE (METNOS_PREFILTER_RULES=0):")
    base = run(test_set, catalog, rules_enabled=False)
    print(f"  top1={base['top1']}/{base['total']} ({base['p1']:.1%})  "
          f"top3={base['top3']} ({base['p3']:.1%})  "
          f"top5={base['top5']} ({base['p5']:.1%})")
    print()
    print("[bench] NEW (METNOS_PREFILTER_RULES=1):")
    new = run(test_set, catalog, rules_enabled=True)
    print(f"  top1={new['top1']}/{new['total']} ({new['p1']:.1%})  "
          f"top3={new['top3']} ({new['p3']:.1%})  "
          f"top5={new['top5']} ({new['p5']:.1%})")
    print()
    d1 = (new['p1'] - base['p1']) * 100
    d3 = (new['p3'] - base['p3']) * 100
    d5 = (new['p5'] - base['p5']) * 100
    print(f"[bench] DELTA: top1={d1:+.1f}pp  top3={d3:+.1f}pp  top5={d5:+.1f}pp")


if __name__ == "__main__":
    main()
