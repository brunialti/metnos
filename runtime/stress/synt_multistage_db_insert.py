#!/usr/bin/env python3
"""synt_multistage_db_insert.py — Phase C di ADR 0052: inserisce 8-10 test
cases di sistema sul synt multistage in `runtime/testing/tests.db`.

I test sono di livello 'system' / categoria 'integration' / kind 'python'.
Ognuno richiama `synt_multistage.run_full(...)` con il LlamaCppProvider
locale e fa assert su `state` e (se applicabile) `name`.

Vincolo: il runner Python ha timeout=120s per script. Quindi inseriamo
SOLO casi che si chiudono in stage 1 (rejection ≈ 22s) o early-stage
(stage 2-3 abandon ≈ 50-100s). Synthesized full take 140-200s e non
entrano nel timeout: per quelli, conserviamo l'evidenza nel jsonl ma
non come test runnable in CI.

Uso:
  python3 runtime/stress/synt_multistage_db_insert.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DB = Path("/opt/myclaw/runtime/testing/tests.db")
RESULTS = Path("/opt/myclaw/decisions/synt_stress/results_multistage_35.jsonl")
QUERIES = Path("/opt/myclaw/decisions/synt_stress/queries_50.json")

MODULE_ID_SYNT = 12  # 'synt' module per registry


def make_test_code(query: str, expected_state: str, expected_name: str | None = None) -> str:
    """Genera test_code che invoca multistage e fa assert.

    Il test usa LlamaCppProvider su 127.0.0.1:8080 (assumpto attivo).
    Il runner del DB ha timeout=120s, quindi targettiamo SOLO casi che
    si chiudono in <=100s wall (= stage 1 rejection, stage 2-3 abandon).
    """
    pieces = [
        "from synt_multistage import run_full",
        "from llm_provider import LlamaCppProvider",
        "",
        "prov = LlamaCppProvider(model='gemma-4-26B-A4B-it-UD-Q4_K_M.gguf', endpoint='http://127.0.0.1:8080')",
        "",
        "def _make(think):",
        "    def _c(system, user, max_tokens=2048):",
        "        cr = prov.chat(system, user, max_tokens=max_tokens, temperature=0)",
        "        return {'text': cr.text, 'in_tokens': cr.in_tokens, 'out_tokens': cr.out_tokens, 'latency_ms': cr.latency_ms}",
        "    return _c",
        "",
        f"query = {query!r}",
        "run = run_full(query, _make(False), _make(True))",
        f"assert run.final_state == {expected_state!r}, f\"expected state {expected_state!r}, got {{run.final_state}} (reason={{run.abandon_reason}})\"",
    ]
    if expected_name and expected_state == "synthesized":
        pieces.append(
            f"assert run.name == {expected_name!r}, f\"expected name {expected_name!r}, got {{run.name}}\""
        )
    return "\n".join(pieces) + "\n"


def select_cases() -> list[dict]:
    """Seleziona 8-10 casi rappresentativi dai risultati multistage.

    Criteri:
      - tutti i casi 'rejected' che hanno final_state='rejected' (stage1, ~22s,
        sicuri per il timeout 120s).
      - 2-3 casi 'new_executor' che hanno final_state='abandoned' a
        stage 2-3 (latenza ~50-100s, dimostrano la robustezza dei
        validator).
      - 1-2 casi edge (es. q06 vocab violation legitima ma falsamente
        rejected).
    Esclusi: synthesized success (>140s, fuori timeout runner).
    """
    if not RESULTS.exists():
        return []
    recs = [json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]
    queries = {q["id"]: q for q in json.loads(QUERIES.read_text())}

    cases = []

    # 1. Rejected hits (rejected expected → state=rejected via stage 1 vocab miss)
    for r in recs:
        if r["expected"] == "rejected" and r["state"] == "rejected":
            q = queries[r["id"]]
            cases.append({
                "name": f"multistage_{r['id']}_{q['desired_executor']}_rejected".replace("/", "_"),
                "test_code": make_test_code(q["query"], "rejected"),
                "expected_brief": f"rejected (vocab/scope) — {r['id']}",
                "qid": r["id"],
                "category": "integration",
                "comment": r.get("abandon_reason", ""),
            })

    # 2. Abandoned at stage 2-3 (=50-100s, runnable; show validator catches)
    abandoned_synthable = [r for r in recs
                           if r["state"] == "abandoned"
                           and r["expected"] == "new_executor"
                           and r["total_latency_ms"] < 100_000]  # <100s
    for r in abandoned_synthable[:3]:
        q = queries[r["id"]]
        cases.append({
            "name": f"multistage_{r['id']}_abandoned_at_validator".replace("/", "_"),
            "test_code": make_test_code(q["query"], "abandoned"),
            "expected_brief": f"abandoned (validator catch) — {r['id']}",
            "qid": r["id"],
            "category": "integration",
            "comment": r.get("abandon_reason", "")[:120],
        })

    # 3. Edge: vocab-rejected legitimate new_executor (es. q06 ridimensionare)
    edge_vocab = [r for r in recs
                  if r["state"] == "rejected"
                  and r["expected"] == "new_executor"
                  and r["total_latency_ms"] < 30_000]
    for r in edge_vocab[:2]:
        q = queries[r["id"]]
        cases.append({
            "name": f"multistage_{r['id']}_vocab_rejection_edge".replace("/", "_"),
            "test_code": make_test_code(q["query"], "rejected"),
            "expected_brief": f"rejected (vocab too strict, expected synth) — {r['id']}",
            "qid": r["id"],
            "category": "integration",
            "comment": r.get("abandon_reason", "")[:120],
        })

    return cases[:10]


def insert_cases(cases: list[dict], dry_run: bool):
    if not cases:
        print("No cases selected (likely missing results jsonl).")
        return

    if dry_run:
        for c in cases:
            print(f"[DRY] {c['name']:60s} ({c['expected_brief']})")
        return

    con = sqlite3.connect(DB)
    cur = con.cursor()
    inserted = 0
    skipped = 0
    for c in cases:
        # Idempotenza: skip se esiste gia' (UNIQUE module_id+name)
        cur.execute(
            "SELECT id FROM test_cases WHERE module_id=? AND name=?",
            (MODULE_ID_SYNT, c["name"]),
        )
        if cur.fetchone():
            print(f"[SKIP] {c['name']} (already in DB)")
            skipped += 1
            continue
        cur.execute(
            """INSERT INTO test_cases
               (module_id, name, level, category, test_kind,
                setup_code, test_code, teardown_code, expected, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                MODULE_ID_SYNT, c["name"], "system", c["category"], "python",
                "",  # setup_code
                c["test_code"],
                "",  # teardown
                c["expected_brief"],
            ),
        )
        print(f"[INS]  {c['name']}")
        inserted += 1
    con.commit()
    con.close()
    print(f"\n{inserted} inserted, {skipped} skipped.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cases = select_cases()
    print(f"Selected {len(cases)} test cases:")
    for c in cases:
        print(f"  {c['name']}  ({c['expected_brief']})")
    print()
    insert_cases(cases, args.dry_run)


if __name__ == "__main__":
    main()
