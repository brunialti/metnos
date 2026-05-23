#!/usr/bin/env python3
"""Aggrega i risultati delle run di tier diversi nel JSON di confronto."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "decisions/synt_stress"

# Costi (USD per Mtok) — Claude Sonnet 4.5: 3/15. Locali: 0/0.
COST = {
    "anthropic": (3.0, 15.0),
    "ollama":    (0.0, 0.0),
    "llamacpp":  (0.0, 0.0),
}


def cost_estimate(records, default_provider):
    in_t = sum((r.get("llm_in_tokens", 0) or 0) for r in records)
    out_t = sum((r.get("llm_out_tokens", 0) or 0) for r in records)
    rate = COST.get(default_provider, (0.0, 0.0))
    return {
        "in_tokens": in_t,
        "out_tokens": out_t,
        "usd": round(in_t/1e6 * rate[0] + out_t/1e6 * rate[1], 4),
    }


def load(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def summarize(records, label, provider):
    if not records:
        return None
    n = len(records)
    correct = sum(1 for r in records if r["expected"] == r["outcome"])
    by_outcome = {}
    for r in records:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    by_expected = {}
    for r in records:
        by_expected[r["expected"]] = by_expected.get(r["expected"], 0) + 1
    cat_correct = {"new_executor": 0, "proto_mnest": 0, "rejected": 0}
    for r in records:
        if r["expected"] in cat_correct and r["expected"] == r["outcome"]:
            cat_correct[r["expected"]] += 1
    cat_acc = {
        cat: (cat_correct[cat] / by_expected.get(cat, 0) * 100.0
              if by_expected.get(cat, 0) else 0.0)
        for cat in cat_correct
    }
    cost = cost_estimate(records, provider)
    return {
        "label": label,
        "provider": provider,
        "n": n,
        "overall_correct": correct,
        "overall_pct": round(correct / max(1, n) * 100.0, 1),
        "by_expected": by_expected,
        "by_outcome": by_outcome,
        "cat_correct": cat_correct,
        "cat_accuracy_pct": {k: round(v, 1) for k, v in cat_acc.items()},
        "avg_latency_ms": int(sum(r["latency_ms"] for r in records) / max(1, n)),
        **cost,
    }


def main():
    out = {
        "subset": "10 representative queries (6 new_executor, 2 proto_mnest, 2 rejected)",
        "tiers": [],
    }
    for label, fname, provider in [
        ("gemma-4-26B", "results_tier_gemma.jsonl", "llamacpp"),
        ("qwen3:8b", "results_tier_qwen3_8b.jsonl", "ollama"),
        ("qwen2.5:7b-instruct", "results_tier_qwen25_7b.jsonl", "ollama"),
        ("claude-sonnet-4-5", "results_tier_claude.jsonl", "anthropic"),
    ]:
        recs = load(ROOT / fname)
        s = summarize(recs, label, provider)
        if s:
            out["tiers"].append(s)
    out_path = ROOT / "llm_tier_comparison.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
