#!/usr/bin/env python3
"""Analizza bench coverage result, identifica gap di pattern Mētis.

Input: bench_praxis_coverage_*.json (output di bench_praxis_coverage.py).
Output: report categorizzato di failure + suggerimenti pattern nuovi
        per praxis_propose.j2.

Categorizzazione failure:
  - dialog: utente bloccato in cap-expand/strato3 (probabile gap pattern)
  - timeout: Mētis non risolve in TIMEOUT s (probabile pattern troppo
    complesso o tool mancante)
  - error: provider error / parse error / executor crash (bug)
  - planner_fallback: query NON gestita da Praxis (Mētis non propose) →
    finita al PLANNER step loop legacy
  - praxis_fail: Praxis propose ma execute fallisce (anti_skill candidate)

Per ogni categoria stampa:
  - top domain (events/files/persons/mail/...) per identificare scope
  - sample query (per copy-paste in praxis_propose.j2 esempi)
  - tool sequence attempted (per capire dove cade)
  - suggerimento pattern nuovo (template)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def latest_bench_result() -> Path:
    runtime_dir = Path("/opt/metnos/runtime")
    files = sorted(runtime_dir.glob("bench_praxis_coverage_*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.exit("No bench result JSON found in /opt/metnos/runtime/")
    return files[0]


def load_turn_steps(turn_id: str) -> list[dict]:
    """Recupera la pipeline reale di un turno dal turn log."""
    tdir = Path.home() / ".local/share/metnos/turns"
    if not tdir.exists():
        return []
    for f in sorted(tdir.glob("*.jsonl"), reverse=True)[:3]:
        try:
            for line in f.read_text().split("\n"):
                if not line.strip() or turn_id not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("turn_id") == turn_id:
                    return o.get("steps") or []
        except Exception:
            continue
    return []


def categorize_failure(r: dict) -> str:
    kind = r.get("final_kind", "")
    handler = r.get("handler", "")
    if kind == "answer":
        return "ok"
    if kind == "error" or r.get("error") == "cap-expand_stuck":
        return "error"
    if kind == "timeout":
        return "timeout"
    if kind == "ask" or kind == "cap-expand":
        return "dialog"
    if handler == "planner":
        return "planner_fallback"
    if handler == "praxis":
        return "praxis_fail"
    return "unknown"


def suggest_pattern(category: str, queries: list[dict]) -> str:
    """Genera suggerimento pattern Mētis basato su sample query failed."""
    if not queries:
        return ""
    # Top domain
    by_dom = defaultdict(int)
    for q in queries:
        by_dom[q.get("domain") or "unknown"] += 1
    top_dom = max(by_dom.items(), key=lambda kv: kv[1])

    # Sample queries (3 max)
    samples = [q["query"] for q in queries[:3]]

    # Get attempted pipelines
    pipelines = []
    for q in queries[:5]:
        tid = q.get("turn_id")
        if tid:
            steps = load_turn_steps(tid)
            tools = [s.get("chosen_tool") for s in steps if s.get("chosen_tool")]
            if tools:
                pipelines.append(tools)

    pat = f"""
─────────────────────────────────────────
SUGGERIMENTO PATTERN per categoria '{category}'
  Domain dominante: {top_dom[0]} ({top_dom[1]} hit)
  N. failure: {len(queries)}

  Sample queries:"""
    for s in samples:
        pat += f"\n    • {s[:80]}"

    if pipelines:
        pat += f"\n  Pipeline tentate (vuoto = Mētis non propone):"
        for p in pipelines:
            pat += f"\n    {' → '.join(p) if p else '(empty)'}"
    pat += f"""

  TEMPLATE prompt patch in praxis_propose.j2:
  ─────────
  {top_dom[0].upper()}_CATEGORY

  MATCH
  - per intento, non per token
  - l'utente vuole [DESCRIVI INTENTO sulla base delle sample queries]
  - dominio: {top_dom[0]}

  NO MATCH
  - [casi simili che NON devono entrare in questo pattern]

  PIPELINE
  - step1: [producer]
  - step2: [finalizer | final_answer]

  OK: {{"steps":[...], "final_message":"..."}}
  ERRORE: [pipeline rotta osservata sopra]
  ─────────
"""
    return pat


def main():
    bench_file = latest_bench_result() if len(sys.argv) < 2 else Path(sys.argv[1])
    print(f"Analyzing: {bench_file}")
    data = json.loads(bench_file.read_text())
    results = data["results"]
    summary = data["summary"]

    by_cat = defaultdict(list)
    for r in results:
        cat = categorize_failure(r)
        by_cat[cat].append(r)

    print(f"\n=== COVERAGE SUMMARY ===")
    print(f"Total: {summary['total']}")
    print(f"  ok (final_kind=answer): {len(by_cat['ok'])} ({len(by_cat['ok'])/summary['total']*100:.1f}%)")
    print(f"  dialog (ask/cap-expand): {len(by_cat['dialog'])}")
    print(f"  timeout: {len(by_cat['timeout'])}")
    print(f"  error: {len(by_cat['error'])}")
    print(f"  planner_fallback: {len(by_cat['planner_fallback'])}")
    print(f"  praxis_fail: {len(by_cat['praxis_fail'])}")
    print(f"  unknown: {len(by_cat['unknown'])}")

    print(f"\n=== BY HANDLER (success rate) ===")
    for h, n in summary.get("by_handler", {}).items():
        ok = summary.get("by_handler_ok", {}).get(h, 0)
        if n > 0:
            print(f"  {h:15} ok={ok}/{n} = {ok/n*100:.1f}%")

    print(f"\n=== FAILURE CATEGORIES — PATTERN GAPS ===")
    failure_cats = ["dialog", "timeout", "error", "planner_fallback", "praxis_fail", "unknown"]
    for cat in failure_cats:
        qs = by_cat.get(cat, [])
        if not qs:
            continue
        print(suggest_pattern(cat, qs))

    # ─── Praxis engine improvements analysis ─────────────────────────
    # Cerca pattern strutturali che indicano bug/miglioramento engine
    # (non solo prompt). Rispetta §7.1/§7.2/§7.3/§7.9 — preferire
    # determinismo + generalità + semplicità, non hardcoded.
    print(f"\n=== PRAXIS ENGINE IMPROVEMENT SIGNALS ===")
    engine_signals = []

    # Signal 1: high dialog rate → escalation troppo aggressiva o
    # cap-expand mal calibrato
    n_dialog = len(by_cat.get("dialog", []))
    if n_dialog > summary["total"] * 0.15:
        engine_signals.append((
            "ESCALATION_OVERTRIGGER",
            f"{n_dialog}/{summary['total']} ({n_dialog/summary['total']*100:.0f}%) finiscono in dialog. "
            "Strato 3 o cap-expand troppo aggressivi.",
            "Tune `count_consecutive_errors_for_query` threshold (§3) o "
            "find_*/read_* default `max_results` (§2.11 cap)."
        ))

    # Signal 2: praxis_fail con sample bassa N stesso intent_hash =
    # cluster_id condivide skill cross-query → arricchire scope marker
    praxis_fails = by_cat.get("praxis_fail", [])
    if praxis_fails:
        # Check turn logs for shared cluster pattern
        shared_clusters = defaultdict(list)
        pdb = Path.home() / ".local/share/metnos/praxis.sqlite"
        if pdb.exists():
            conn = sqlite3.connect(str(pdb))
            for r in praxis_fails:
                tid = r.get("turn_id")
                if not tid:
                    continue
                row = conn.execute(
                    "SELECT cluster_id, intent_sig FROM observations WHERE turn_id=?",
                    (tid,)).fetchone()
                if row and row[0]:
                    shared_clusters[row[0]].append(r["query"])
            conn.close()
            multi_cluster = {c: qs for c, qs in shared_clusters.items() if len(qs) > 1}
            if multi_cluster:
                engine_signals.append((
                    "CLUSTER_BLEED",
                    f"{len(multi_cluster)} cluster_id condivisi da query semanticamente diverse → "
                    "skill cached riusata cross-query.",
                    "Arricchire compute_intent_sig con scope marker piu' granulare "
                    "(domain-specific tokens) o abbassare COSINE_HIGH."
                ))

    # Signal 3: timeout > 0 → Mētis non converge → prompt sovraccarico
    n_timeout = len(by_cat.get("timeout", []))
    if n_timeout > 0:
        engine_signals.append((
            "METIS_TIMEOUT",
            f"{n_timeout} query timeout (Mētis wise non risponde in TIMEOUT s).",
            "Considera: (a) ridurre praxis_propose.j2 (oggi 250+ righe IT) "
            "verso Mētis minimal + frontier auto-escalate opt (a); "
            "(b) parse error → fallback strict GBNF."
        ))

    # Signal 4: planner_fallback > 5% → Praxis miss Mētis propose
    n_planner = len(by_cat.get("planner_fallback", []))
    if n_planner > summary["total"] * 0.05:
        engine_signals.append((
            "PRAXIS_PROPOSE_MISS",
            f"{n_planner}/{summary['total']} ({n_planner/summary['total']*100:.0f}%) "
            "non handled by Praxis → PLANNER step loop fallback.",
            "Mētis non sa proporre framework per quel intent. Pattern mancante "
            "o intent_extractor non riconosce il verb. Aggiungere few-shot in "
            "intent_extractor.j2 + pattern in praxis_propose.j2."
        ))

    # Signal 5: errors > 1% → executor bug o args validation
    n_error = len(by_cat.get("error", []))
    if n_error > summary["total"] * 0.01:
        engine_signals.append((
            "EXECUTOR_ERRORS",
            f"{n_error} hard errors (provider/parse/executor crash).",
            "Investigare turn log per error_class. Possibili: GBNF parse fail, "
            "executor exception, validate_args mismatch. §7.9 deterministic fix "
            "preferito a prompt tuning."
        ))

    if not engine_signals:
        print("  Nessun signal critico. Engine sano sui parametri analizzati.")
    else:
        for label, observation, suggestion in engine_signals:
            print(f"\n  [{label}]")
            print(f"  OSSERVAZIONE: {observation}")
            print(f"  SUGGERIMENTO (§7.2/§7.3/§7.9): {suggestion}")

    # Domain coverage breakdown
    print(f"\n=== COVERAGE BY DOMAIN ===")
    dom_stats = defaultdict(lambda: {"ok": 0, "total": 0, "fail_modes": defaultdict(int)})
    for r in results:
        d = r.get("domain") or "unknown"
        dom_stats[d]["total"] += 1
        cat = categorize_failure(r)
        if cat == "ok":
            dom_stats[d]["ok"] += 1
        else:
            dom_stats[d]["fail_modes"][cat] += 1
    for d in sorted(dom_stats.keys(), key=lambda x: -dom_stats[x]["total"]):
        s = dom_stats[d]
        rate = s["ok"] / s["total"] * 100 if s["total"] else 0
        fail_str = ", ".join(f"{k}={v}" for k, v in s["fail_modes"].items())
        print(f"  {d:20} {s['ok']:3}/{s['total']:3} = {rate:5.1f}%  [{fail_str}]")


if __name__ == "__main__":
    main()
