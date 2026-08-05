"""run_simulation.py — esegue simulazione graph search su set query test.

Usage:
  python3 run_simulation.py
  python3 run_simulation.py --query "quanti file in /tmp"
  python3 run_simulation.py --corpus 50

Report:
  - per ogni query: candidates count + top-3 paths + selettività
  - aggregato: avg candidates, hit rate (se expected annotato)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from graph_search import (
    load_typing, search, QueryInput, MAX_DEPTH,
)

CORPUS = Path("/opt/metnos/tests/e2e/corpus/corpus.sqlite")

# Test queries con expected path annotato (manuale, 20 baseline)
TEST_QUERIES_ANNOTATED = [
    {"query": "che ora e", "intent_verb": "get", "intent_object": "numbers",
     "inputs": [], "target_type": "scalar_metric",
     "expected_path": ["get_now"]},
    {"query": "quanti file in /tmp", "intent_verb": "compute", "intent_object": "files",
     "inputs": [{"name": "base_path", "semantic_type": "dir_path", "value": "/tmp"}],
     "target_type": "scalar_metric",
     "expected_path": ["find_files", "compute_entries"]},
    {"query": "appuntamenti settimana prossima", "intent_verb": "read", "intent_object": "events",
     "inputs": [{"name": "time_window", "semantic_type": "time_window", "value": "next-week"}],
     "target_type": "event_entry[]",
     "expected_path": ["read_events", "describe_entries"]},
    {"query": "leggi mail oggi", "intent_verb": "read", "intent_object": "messages",
     "inputs": [{"name": "time_window", "semantic_type": "time_window", "value": "today"}],
     "target_type": "message_entry[]",
     "expected_path": ["read_messages", "describe_entries"]},
    {"query": "chi sono io", "intent_verb": "read", "intent_object": "persons",
     "inputs": [{"name": "name", "semantic_type": "person_name", "value": "${RUNTIME:actor}"}],
     "target_type": "person_entry[]",
     "expected_path": ["read_persons"]},
    {"query": "dimmi tutto su Lucia", "intent_verb": "read", "intent_object": "persons",
     "inputs": [{"name": "name", "semantic_type": "person_name", "value": "Lucia"}],
     "target_type": "person_entry[]",
     "expected_path": ["read_persons"]},
    {"query": "trova foto di Matteo", "intent_verb": "find", "intent_object": "images",
     "inputs": [{"name": "name", "semantic_type": "person_name", "value": "Matteo"}],
     "target_type": "image_entry[]",
     "expected_path": ["find_persons_indices"]},
    {"query": "elenca file in /tmp con .py", "intent_verb": "find", "intent_object": "files",
     "inputs": [{"name": "base_path", "semantic_type": "dir_path", "value": "/tmp"},
                 {"name": "pattern", "semantic_type": "glob_pattern", "value": "*.py"}],
     "target_type": "file_entry[]",
     "expected_path": ["find_files", "describe_entries"]},
    {"query": "manda telegram lista mail oggi", "intent_verb": "send", "intent_object": "messages",
     "inputs": [{"name": "time_window", "semantic_type": "time_window", "value": "today"}],
     "target_type": "scalar_metric",
     "expected_path": ["read_messages", "send_messages"]},
    {"query": "scarica https://example.com/file.pdf", "intent_verb": "get", "intent_object": "urls",
     "inputs": [{"name": "urls", "semantic_type": "url", "value": "https://example.com/file.pdf"}],
     "target_type": "free_text",
     "expected_path": ["get_urls"]},
]


def report_query(query_in: QueryInput, candidates: list, registry: dict,
                  expected: list = None, verbose: bool = False) -> dict:
    """Print + return metrics per singola query."""
    n_total_executors = len(registry)
    n_candidates = len(candidates)
    selectivity = n_candidates / n_total_executors if n_total_executors else 0
    top3_paths = [
        " → ".join(s["tool"] for s in c.steps) for c in candidates[:3]
    ]
    metrics = {
        "n_candidates": n_candidates,
        "selectivity_pct": round(selectivity * 100, 2),
        "top3_paths": top3_paths,
    }
    if expected:
        top1_path = [s["tool"] for s in candidates[0].steps] if candidates else []
        metrics["expected"] = expected
        metrics["top1_match"] = top1_path == expected
        metrics["top3_match"] = any(
            [s["tool"] for s in c.steps] == expected
            for c in candidates[:3]
        )
    if verbose:
        print(f"  candidates: {n_candidates} ({metrics['selectivity_pct']}% pool)")
        for i, p in enumerate(top3_paths, 1):
            print(f"    {i}. {p}")
        if expected:
            mark = "✓" if metrics["top1_match"] else "✗"
            print(f"  expected: {' → '.join(expected)} {mark}")
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", help="Una sola query test ad-hoc")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    registry = load_typing()
    print(f"Loaded {len(registry)} executor typings")
    if len(registry) == 0:
        print("ERROR: no typing. Run extract_typing.py first.")
        sys.exit(1)

    if args.query:
        # Single ad-hoc query, no expected
        q = QueryInput(intent_verb="", intent_object="", inputs=[], target_type="")
        # Heuristic intent guess (semplice, just for demo)
        candidates = search(q, registry)
        print(f"\nQuery: {args.query}")
        report_query(q, candidates, registry, verbose=True)
        return

    # Run annotated test queries
    print(f"\nRunning {len(TEST_QUERIES_ANNOTATED)} annotated test queries...\n")
    all_metrics = []
    for tq in TEST_QUERIES_ANNOTATED:
        q = QueryInput(
            intent_verb=tq["intent_verb"],
            intent_object=tq["intent_object"],
            inputs=tq["inputs"],
            target_type=tq["target_type"],
        )
        candidates = search(q, registry)
        print(f"Q: {tq['query']}")
        m = report_query(q, candidates, registry, expected=tq.get("expected_path"),
                          verbose=True)
        all_metrics.append(m)
        print()

    # Aggregate
    n = len(all_metrics)
    avg_cand = sum(m["n_candidates"] for m in all_metrics) / n if n else 0
    avg_sel = sum(m["selectivity_pct"] for m in all_metrics) / n if n else 0
    top1_hits = sum(1 for m in all_metrics if m.get("top1_match"))
    top3_hits = sum(1 for m in all_metrics if m.get("top3_match"))
    print("=" * 70)
    print(f"AGGREGATED ({n} queries)")
    print(f"  avg candidates per query: {avg_cand:.1f}")
    print(f"  avg selectivity: {avg_sel:.1f}% of pool")
    print(f"  top-1 hit (expected == top): {top1_hits}/{n} = {top1_hits/n*100:.0f}%")
    print(f"  top-3 hit (expected in top 3): {top3_hits}/{n} = {top3_hits/n*100:.0f}%")


if __name__ == "__main__":
    main()
