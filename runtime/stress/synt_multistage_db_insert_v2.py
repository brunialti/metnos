#!/usr/bin/env python3
"""synt_multistage_db_insert_v2.py — Phase C di ADR 0052 (28/4 sera): inserisce
8-10 system test cases derivati dal run multistage v2 (results_multistage_35_v2.jsonl).

Identico nello spirito a `synt_multistage_db_insert.py` (v1) ma:
  - sorgente RESULTS = jsonl v2 (post-fix prompt prescrittivi + max_tokens 4000).
  - selezione mira a un MIX rappresentativo:
      A. 2-3 'rejected' che ora hanno final_state=='rejected' (se ci sono);
      B. 2-3 'new_executor' synthesized (probe di happy-path full-multistage);
      C. 2-3 'new_executor' abandoned a stage 2/3 (validator catches);
      D. 1-2 'new_executor' rejected legittime fuori-vocab (resize/frames).

VINCOLO RUNNER: timeout 120s/test. I synthesized full prendono 140-200s,
quindi NON entrano nel timeout. Per quelli marchiamo `enabled=0` con
expected_brief annotato, cosi' restano on-disk per future esecuzioni
manuali, ma non rompono il runner.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DB = Path("/opt/myclaw/runtime/testing/tests.db")
RESULTS = Path("/opt/myclaw/decisions/synt_stress/results_multistage_35_v2.jsonl")
QUERIES = Path("/opt/myclaw/decisions/synt_stress/queries_50.json")

MODULE_ID_SYNT = 12

TIMEOUT_BUDGET_S = 110  # margine sul runner 120s


def make_test_code(query: str, expected_state: str, expected_name: str | None = None) -> str:
    pieces = [
        "from synt_multistage import run_full",
        "from llm_provider import LlamaCppProvider",
        "",
        "prov = LlamaCppProvider(model='gemma-4-26B-A4B-it-UD-Q4_K_M.gguf', endpoint='http://127.0.0.1:8080')",
        "",
        "def _make(think):",
        "    def _c(system, user, max_tokens=2048):",
        "        cr = prov.chat(system, user, max_tokens=max_tokens, temperature=0, think=think)",
        "        return {'text': cr.text, 'in_tokens': cr.in_tokens, 'out_tokens': cr.out_tokens, 'latency_ms': cr.latency_ms}",
        "    return _c",
        "",
        f"query = {query!r}",
        "run = run_full(query, _make(True), _make(True))",
        f"assert run.final_state == {expected_state!r}, f\"expected state {expected_state!r}, got {{run.final_state}} (reason={{run.abandon_reason}})\"",
    ]
    if expected_name and expected_state == "synthesized":
        pieces.append(
            f"assert run.name == {expected_name!r}, f\"expected name {expected_name!r}, got {{run.name}}\""
        )
    return "\n".join(pieces) + "\n"


def select_cases() -> list[dict]:
    if not RESULTS.exists():
        return []
    recs = [json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]
    queries = {q["id"]: q for q in json.loads(QUERIES.read_text())}

    cases = []
    seen_ids: set[str] = set()

    def push(qid: str, expected_state: str, brief: str, comment: str,
             enabled: int = 1, expected_name: str | None = None,
             tag: str = ""):
        if qid in seen_ids:
            return
        seen_ids.add(qid)
        q = queries[qid]
        nm = f"multistage_v2_{qid}_{tag}".strip("_")
        cases.append({
            "name": nm,
            "test_code": make_test_code(q["query"], expected_state, expected_name),
            "expected_brief": brief,
            "qid": qid,
            "category": "integration",
            "comment": comment,
            "enabled": enabled,
        })

    # A. rejected → rejected (vocab/scope) — entro timeout (stage 1 ~22s)
    for r in recs:
        if r["expected"] == "rejected" and r["state"] == "rejected" \
                and r["total_latency_ms"] < TIMEOUT_BUDGET_S * 1000:
            push(r["id"], "rejected",
                 f"rejected (stage1 vocab/scope) — {r['id']}",
                 (r.get("abandon_reason") or "")[:140],
                 enabled=1, tag="rejected")
            if sum(1 for c in cases if "rejected" in c["name"]) >= 3:
                break

    # B. new_executor → synthesized (happy path completo)
    #    Synthesized typically >120s wall: enabled=0 (kept on disk, not run by CI).
    syn = [r for r in recs if r["expected"] == "new_executor" and r["state"] == "synthesized"]
    syn_in_budget = [r for r in syn if r["total_latency_ms"] < TIMEOUT_BUDGET_S * 1000]
    syn_out_budget = [r for r in syn if r["total_latency_ms"] >= TIMEOUT_BUDGET_S * 1000]
    # Prefer in-budget; fallback take 1-2 out-of-budget marked enabled=0.
    for r in syn_in_budget[:3]:
        push(r["id"], "synthesized",
             f"synthesized full (under {TIMEOUT_BUDGET_S}s) — {r['id']} → {r.get('name')}",
             "", enabled=1, expected_name=r.get("name"), tag="synth")
    if not syn_in_budget:
        for r in syn_out_budget[:2]:
            push(r["id"], "synthesized",
                 f"synthesized full (>{TIMEOUT_BUDGET_S}s, manual run only) — {r['id']} → {r.get('name')}",
                 "kept disabled due to runner timeout 120s",
                 enabled=0, expected_name=r.get("name"), tag="synth_long")

    # C. new_executor → abandoned at validator (stage 2 or 3) - entro timeout
    abandoned = [r for r in recs
                 if r["expected"] == "new_executor"
                 and r["state"] == "abandoned"
                 and r["total_latency_ms"] < TIMEOUT_BUDGET_S * 1000]
    for r in abandoned[:2]:
        push(r["id"], "abandoned",
             f"abandoned (validator) — {r['id']} reason={(r.get('abandon_reason') or '')[:60]}",
             (r.get("abandon_reason") or "")[:140],
             enabled=1, tag="abandoned")

    # D. new_executor → rejected (vocab strict, edge case)
    edge = [r for r in recs
            if r["expected"] == "new_executor" and r["state"] == "rejected"
            and r["total_latency_ms"] < TIMEOUT_BUDGET_S * 1000]
    for r in edge[:2]:
        push(r["id"], "rejected",
             f"vocab-rejected new_executor (edge) — {r['id']}",
             (r.get("abandon_reason") or "")[:140],
             enabled=1, tag="vocab_edge")

    return cases[:10]


def insert_cases(cases: list[dict], dry_run: bool):
    if not cases:
        print("No cases selected (likely missing results jsonl).")
        return

    if dry_run:
        for c in cases:
            print(f"[DRY] {c['name']:60s} enabled={c['enabled']} ({c['expected_brief']})")
        return

    con = sqlite3.connect(DB)
    cur = con.cursor()
    inserted = 0
    skipped = 0
    for c in cases:
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                MODULE_ID_SYNT, c["name"], "system", c["category"], "python",
                "",
                c["test_code"],
                "",
                c["expected_brief"],
                int(c["enabled"]),
            ),
        )
        print(f"[INS]  {c['name']} (enabled={c['enabled']})")
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
        print(f"  {c['name']}  enabled={c['enabled']}  ({c['expected_brief']})")
    print()
    insert_cases(cases, args.dry_run)


if __name__ == "__main__":
    main()
