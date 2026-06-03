"""component_profile.py — Profila per componente i mismatch su bench output.

Componenti:
  P1 LLM intent_verb wrong
  P2 LLM intent_object wrong
  P3 LLM target_type wrong
  P4 LLM constraints wrong/missing
  P5 LLM inputs malformed
  P6 Entry filter — path corretto fuori da candidates
  P7 Ranker — path corretto in cand ma fuori top-2
  P8 Annotation — top sembra semanticamente OK ma non in accepted
  P9 Catalog gap — no exec realisticamente esistente
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
if not os.environ.get("BENCH_FROZEN") and not os.environ.get("BENCH_TRIM") and not os.environ.get("BENCH_500") and not os.environ.get("BENCH_REAL"):
    os.environ["BENCH_FROZEN"] = "1"
from run_simulation_v2 import TEST_QUERIES
from registry import ExecutorRegistry


def parse_bench(fn: str) -> dict:
    """{query: {intent_v, intent_o, target_t, n_cands, tops:[]}}"""
    data = {}
    cur = None
    with open(fn) as f:
        for ln in f:
            ln = ln.rstrip()
            m = re.match(r"^Q: (.+)$", ln)
            if m:
                if cur:
                    data[cur["q"]] = cur
                cur = {"q": m.group(1).strip(), "tops": [], "intent_v": "?",
                        "intent_o": "?", "target_t": "?"}
                continue
            if not cur:
                continue
            m = re.match(r"\[timing\] search: \S+ candidates=(\d+)", ln)
            if m: cur["n_cands"] = int(m.group(1)); continue
            m = re.match(r"  intent: (\S+) (\S+)", ln)
            if m: cur["intent_v"] = m.group(1); cur["intent_o"] = m.group(2); continue
            m = re.match(r"  target: (\S+) \(", ln)
            if m: cur["target_t"] = m.group(1); continue
            m = re.match(r"    \d+\. \[-?[\d\.]+\] (.+?)$", ln)
            if m:
                first = m.group(1).split(" → ")[0]
                cur["tops"].append((first, m.group(1)))
    if cur: data[cur["q"]] = cur
    return data


def classify_mismatch(t: dict, d: dict, registry_names: set,
                        catalog_gap_names: set) -> str:
    """Classify a single fail into component bucket."""
    tops_first = [x[0] for x in d.get("tops", [])]
    accepted = set(t["accepted_first"])
    expected_path = t.get("expected_path", [])
    exp_first = expected_path[0] if expected_path else "?"

    # OK?
    if (tops_first[0] in accepted if tops_first else False) or \
       (len(tops_first) > 1 and tops_first[1] in accepted):
        return "OK"

    # P9: catalog gap — expected NOT in registry
    if exp_first not in registry_names and exp_first != "request_new_executor":
        return "P9_catalog_gap"

    # P7: path in candidates but not top-2 (Ranker)
    if any(t in accepted for t in tops_first[:10]):
        return "P7_ranker"

    iv = d.get("intent_v", "?")
    io = d.get("intent_o", "?")
    tt = d.get("target_t", "?")
    n_cands = d.get("n_cands", 0)

    # Decompose expected first into verb_object
    if "_" in exp_first:
        ev, eo = exp_first.split("_", 1)
        eo_base = eo.split("_", 1)[0]
    else:
        ev, eo_base = exp_first, "?"

    # P1/P2/P3: LLM extraction error
    if iv != ev and io != eo_base:
        return "P1+P2_llm_both"
    if iv != ev:
        return "P1_llm_verb"
    if io != eo_base:
        return "P2_llm_object"

    # LLM intent OK, but path not in cands → P6 entry filter
    if n_cands == 0:
        return "P6_no_candidates"
    return "P6_entry_filter"


def profile(bench_out: str):
    reg = ExecutorRegistry(json_dir=Path(__file__).parent / "typing_cache")
    registry_names = set(reg.all_names())

    # Identify catalog-gap expected exec
    catalog_gap = set()
    for t in TEST_QUERIES:
        for tool in t.get("expected_path", []):
            if tool and tool not in registry_names and tool != "request_new_executor":
                catalog_gap.add(tool)

    data = parse_bench(bench_out)
    buckets = Counter()
    examples = defaultdict(list)
    for t in TEST_QUERIES:
        q = t["query"]
        d = data.get(q, {})
        cat = classify_mismatch(t, d, registry_names, catalog_gap)
        buckets[cat] += 1
        if cat != "OK":
            tops = [x[0] for x in d.get("tops", [])][:2]
            examples[cat].append((q, tops, t["expected_path"][:2]))

    total = sum(buckets.values())
    print(f"=== COMPONENT PROFILE ({total} queries) ===")
    for cat, n in sorted(buckets.items(), key=lambda x: -x[1]):
        pct = 100 * n / total
        bar = "█" * int(pct / 2)
        print(f"  {cat:25s}: {n:4d} ({pct:5.1f}%) {bar}")
    print()
    print("=== EXAMPLES per component ===")
    for cat, items in examples.items():
        if cat == "OK": continue
        print(f"\n--- {cat} ({len(items)} fails) ---")
        for q, tops, exp in items[:4]:
            print(f"  Q: {q[:65]}")
            print(f"     tops: {tops}  expected: {exp}")


if __name__ == "__main__":
    bench_out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/iter_frozen_baseline.out"
    profile(bench_out)
