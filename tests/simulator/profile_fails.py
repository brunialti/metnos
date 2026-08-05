"""profile_fails.py — categorize each fail to drive targeted fixes.

Categories:
  A. LLM parse error (wrong intent_verb/object/target)
  B. Path NOT in candidates (LLM gives correct intent but ranker excludes)
  C. Ranker bias (correct path in candidates but not top-2)
  D. Catalog gap (no executor can answer)
  E. Annotation issue (accepted_first too narrow)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Default to BENCH_TRIM if test_set_trimmed.json exists
if not os.environ.get("BENCH_TRIM") and not os.environ.get("BENCH_REAL") and not os.environ.get("BENCH_500"):
    if (Path(__file__).parent / "test_set_trimmed.json").exists():
        os.environ["BENCH_TRIM"] = "1"
    else:
        os.environ["BENCH_REAL"] = "1"
from run_simulation_v2 import TEST_QUERIES


def parse_bench(fn: str) -> dict:
    """Parse bench output → {query: {tops:[], intent_v, intent_o, target_t, n_candidates}}"""
    data = {}
    cur = None
    with open(fn) as f:
        for ln in f:
            ln = ln.rstrip()
            m = re.match(r"^Q: (.+)$", ln)
            if m:
                if cur:
                    data[cur["q"]] = cur
                cur = {"q": m.group(1).strip(), "tops": [], "tops_text": []}
                continue
            if not cur:
                continue
            m = re.match(r"\[timing\] search: \S+ candidates=(\d+)", ln)
            if m:
                cur["n_cands"] = int(m.group(1))
                continue
            m = re.match(r"  intent: (\S+) (\S+)", ln)
            if m:
                cur["intent_v"] = m.group(1)
                cur["intent_o"] = m.group(2)
                continue
            m = re.match(r"  target: (\S+) \((\S+)\)", ln)
            if m:
                cur["target_t"] = m.group(1)
                continue
            m = re.match(r"    \d+\. \[-?[\d\.]+\] (.+?)$", ln)
            if m:
                cur["tops_text"].append(m.group(1))
                first_tool = m.group(1).split(" → ")[0]
                cur["tops"].append(first_tool)
    if cur:
        data[cur["q"]] = cur
    return data


def categorize_fail(q: str, expected_path: list, accepted: set, data: dict) -> str:
    if q not in data:
        return "E_missing"
    d = data[q]
    tops = d.get("tops", [])
    n_cands = d.get("n_cands", 0)
    top1 = tops[0] if tops else "?"
    top2 = tops[1] if len(tops) > 1 else "?"

    # OK case
    if top1 in accepted or top2 in accepted:
        return "OK"

    # Check if correct executor IS in candidates (broader)
    expected_first = expected_path[0] if expected_path else None
    intent_v = d.get("intent_v", "")
    intent_o = d.get("intent_o", "")
    target_t = d.get("target_t", "")

    # A. LLM wrong intent
    if expected_first and "_" in expected_first:
        exp_v, exp_o = expected_first.split("_", 1)
        exp_o_base = exp_o.split("_", 1)[0]
        if intent_v != exp_v and intent_o != exp_o_base:
            return "A_llm_wrong_both"
        elif intent_v != exp_v:
            return "A_llm_wrong_verb"
        elif intent_o != exp_o_base:
            return "A_llm_wrong_object"

    # B. Path not in any of top candidates
    if any(t in accepted for t in tops):
        return "C_ranker"  # path in top-10 but not top-2
    if n_cands == 0:
        return "D_no_candidates"
    return "B_path_excluded"


def main(bench_out: str):
    data = parse_bench(bench_out)
    cats = Counter()
    examples = defaultdict(list)
    for t in TEST_QUERIES:
        q = t["query"]
        cat = categorize_fail(q, t["expected_path"], set(t["accepted_first"]), data)
        cats[cat] += 1
        if cat != "OK":
            top1 = data.get(q, {}).get("tops", ["?"])[0] if q in data else "?"
            examples[cat].append((q, top1, t["expected_path"][:2]))
    total = sum(cats.values())
    print(f"=== Profile {bench_out} ({total} queries) ===")
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        pct = 100 * n / total
        print(f"  {cat:24s}: {n:4d} ({pct:5.1f}%)")
    print()
    for cat, items in examples.items():
        if cat == "OK": continue
        print(f"--- {cat} samples ---")
        for q, t1, exp in items[:6]:
            print(f"  {q[:60]:<61} top={t1:<20} exp={exp[0] if exp else '?'}")
        print()


if __name__ == "__main__":
    bench_out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/iter_real_qwen35.out"
    main(bench_out)
