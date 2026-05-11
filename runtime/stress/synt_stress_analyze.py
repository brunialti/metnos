#!/usr/bin/env python3
"""synt_stress_analyze.py — analizza i risultati JSONL di synt_stress_50.

Legge il JSONL prodotto e:
- aggrega outcome per categoria attesa (acc, mismatch);
- estrae i nomi degli executor proposti dal synt e verifica la conformita'
  rispetto alla naming convention ADR 0045 (azione + oggetto plurale);
- elenca i fallimenti tecnici raggruppati per causa;
- estrae i pattern di proto-mnest emersi (frequenza dei tool nei chains);
- riporta latenze medie e token totali.

Output:
- summary su stdout
- analisi.json con i numeri grezzi
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ALLOWED_ACTIONS = frozenset({
    "read", "write", "move", "delete",
    "find", "list",
    "filter", "sort", "group",
    "get", "set",
    "send",
    "describe",
    "render",
    "extract",
})
ALLOWED_OBJECTS = frozenset({
    "files", "dirs", "packages", "messages", "events", "contacts",
    "places", "processes", "urls", "numbers", "texts",
})
SINGULAR_EXCEPTIONS = frozenset({"get_now"})


def is_naming_compliant(name: str) -> tuple[bool, str]:
    if name in SINGULAR_EXCEPTIONS:
        return True, "ok (exception)"
    parts = name.split("_")
    if len(parts) < 2:
        return False, f"need verb_noun, got '{name}'"
    action = parts[0]
    if action not in ALLOWED_ACTIONS:
        return False, f"action '{action}' not in vocabulary"
    obj = parts[1]
    if obj not in ALLOWED_OBJECTS:
        return False, f"object '{obj}' not in vocabulary"
    return True, "ok"


def fail_bucket(rationale: str) -> str:
    r = rationale.lower()
    if "birth tests failed" in r:
        return "birth_tests_failed"
    if "convention" in r:
        return "convention_failed"
    if "sandbox dangerous" in r:
        return "sandbox_dangerous"
    if "non-stdlib" in r:
        return "non_stdlib_imports"
    if "ast parse failed" in r:
        return "ast_parse_failed"
    if "no chain found" in r:
        return "compose_no_chain"
    if "loop_break" in r:
        return "planner_loop_break"
    if "no proto_mnest" in r:
        return "no_proto_mnest"
    if "policy" in r or "scope" in r:
        return "policy_scope"
    return "other"


def analyze(records: list[dict]) -> dict:
    n = len(records)
    by_expected = Counter()
    by_outcome = Counter()
    correct = Counter()
    mismatches = []
    failures_by_bucket = Counter()
    naming_check = {"compliant": 0, "non_compliant": 0,
                     "non_compliant_examples": []}
    proposed_names = []
    chain_tools = Counter()
    for r in records:
        e, o = r.get("expected"), r.get("outcome")
        by_expected[e] += 1
        by_outcome[o] += 1
        if e == o:
            correct[e] += 1
        else:
            mismatches.append({
                "id": r.get("id"), "expected": e, "outcome": o,
                "rationale": r.get("synt_rationale", "")[:160],
            })
        # bucket dei fallimenti
        if r.get("state") == "abandoned":
            failures_by_bucket[fail_bucket(r.get("synt_rationale", ""))] += 1
        # naming convention
        name = r.get("proposal_name")
        if name:
            proposed_names.append(name)
            ok, reason = is_naming_compliant(name)
            if ok:
                naming_check["compliant"] += 1
            else:
                naming_check["non_compliant"] += 1
                naming_check["non_compliant_examples"].append({
                    "name": name, "reason": reason, "id": r.get("id"),
                })
        # chain tools (proto_mnest paths)
        for tool in r.get("executors_used") or []:
            chain_tools[tool] += 1
    accuracy_pct = {
        cat: (correct[cat] / by_expected[cat] * 100.0 if by_expected[cat] else 0.0)
        for cat in ("new_executor", "proto_mnest", "rejected")
    }
    avg_latency = sum(r.get("latency_ms", 0) for r in records) / max(1, n)
    return {
        "n": n,
        "by_expected": dict(by_expected),
        "by_outcome": dict(by_outcome),
        "correct": dict(correct),
        "accuracy_pct": accuracy_pct,
        "overall_pct": (
            sum(correct[c] for c in correct) / max(1, n) * 100.0
        ),
        "mismatches": mismatches,
        "failures_by_bucket": dict(failures_by_bucket),
        "naming_check": naming_check,
        "proposed_names": proposed_names,
        "chain_tools_top": chain_tools.most_common(20),
        "avg_latency_ms": int(avg_latency),
    }


def print_report(s: dict) -> None:
    print(f"n={s['n']}  overall={s['overall_pct']:.1f}%  avg_latency={s['avg_latency_ms']}ms")
    print()
    print("By expected (n=expected, c=correct):")
    for cat in ("new_executor", "proto_mnest", "rejected"):
        n_e = s["by_expected"].get(cat, 0)
        c = s["correct"].get(cat, 0)
        acc = s["accuracy_pct"].get(cat, 0)
        print(f"  {cat:14s}  n={n_e:3d}  c={c:3d}  acc={acc:5.1f}%")
    print()
    print("By outcome:")
    for k, v in sorted(s["by_outcome"].items(), key=lambda x: -x[1]):
        print(f"  {k:14s}  {v:3d}")
    print()
    print("Failures by bucket (state=abandoned):")
    for k, v in sorted(s["failures_by_bucket"].items(), key=lambda x: -x[1]):
        print(f"  {k:24s}  {v:3d}")
    print()
    nc = s["naming_check"]
    print(f"Naming: compliant={nc['compliant']} non_compliant={nc['non_compliant']}")
    for ex in nc["non_compliant_examples"][:8]:
        print(f"  {ex['id']:6s}  '{ex['name']}'  -> {ex['reason']}")
    print()
    print("Top tools used in successful chains:")
    for t, n in s["chain_tools_top"][:10]:
        print(f"  {t:24s}  {n:3d}")
    print()
    print("Mismatches (expected vs outcome):")
    for m in s["mismatches"][:30]:
        print(f"  {m['id']:6s}  exp={m['expected']:14s} got={m['outcome']:14s}  {m['rationale'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args()
    records = []
    for line in args.results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    s = analyze(records)
    if args.out_json:
        args.out_json.write_text(json.dumps(s, indent=2, ensure_ascii=False))
    print_report(s)


if __name__ == "__main__":
    main()
