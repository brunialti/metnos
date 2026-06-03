#!/usr/bin/env python3
"""Bench coverage Praxis vs PLANNER vs fast_path su sample corpus.

Misura empirica per decisione architetturale ADR 0163 ext:
  - Quanti turn risolve Praxis (cache O(1) hit / Mētis propose)
  - Quanti cadono al PLANNER fallback monolitico (step loop)
  - Quanti finiscono in Aporia (vicolo cieco onesto)
  - Quanti fast_path L0 (deterministic, pre-Praxis)
  - Success rate per categoria (final_kind=answer no error)

Sample stratificato da corpus.sqlite per category/domain. Default N=100
(stratified) → ~15-25 min wall time live server.

Output: bench_praxis_coverage_<ts>.json + console summary.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

CORPUS = Path("/opt/metnos/e2e/corpus/corpus.sqlite")
ADMIN_KEY = (Path.home() / ".config/metnos/admin.key").read_text().strip()
SERVER = "http://localhost:8770"
SAMPLE_SIZE = int(os.environ.get("SAMPLE_SIZE", "100"))
CATEGORY = os.environ.get("CATEGORY", "planner")  # planner principalmente
TIMEOUT = int(os.environ.get("TIMEOUT", "180"))


def stratified_sample(n: int, category: str) -> list[dict]:
    conn = sqlite3.connect(str(CORPUS))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT query, query_canonical, lang, domain, tools_used, "
        "n_steps, mutating, success FROM queries "
        "WHERE category=? AND lang='it' AND dedup_master_id IS NULL "
        "ORDER BY n_seen DESC LIMIT 500",
        (category,),
    ).fetchall()
    conn.close()
    # Stratifica per domain: prendi proporzionale
    by_domain: dict[str, list] = defaultdict(list)
    for r in rows:
        by_domain[r["domain"] or "unknown"].append(dict(r))
    # Calcola quota per domain
    n_doms = len(by_domain)
    per_dom = max(1, n // n_doms) if n_doms else n
    sample = []
    for dom, qs in by_domain.items():
        sample.extend(qs[:per_dom])
        if len(sample) >= n:
            break
    return sample[:n]


def turn(query: str, lang: str = "it") -> dict:
    body = json.dumps({"query": query, "lang": lang}).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVER}/agent/turn", data=body,
        headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {ADMIN_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": str(e), "final_kind": "timeout"}
    except Exception as e:
        return {"error": str(e), "final_kind": "error"}


def cancel_pending_dialogs():
    """Cancella tutti i dialog pending lato server (cap-expand / strato3)."""
    dialog_dir = Path.home() / ".local/share/metnos/get_inputs/http_host"
    if dialog_dir.exists():
        for f in dialog_dir.glob("*.json"):
            try:
                f.unlink()
            except Exception:
                pass


def classify_handler(turn_id: str) -> str:
    """Identifica chi ha gestito il turn: praxis | planner | fast_path | error.

    Order of detection:
      1. Praxis: observation in praxis.sqlite (priorità — Praxis registra sempre)
      2. Turn log: mode/candidates hint per fast_path / planner
    """
    # Praxis: observation in praxis.sqlite con quel turn_id
    pdb = Path.home() / ".local/share/metnos/praxis.sqlite"
    if pdb.exists():
        conn = sqlite3.connect(str(pdb))
        row = conn.execute(
            "SELECT verdict FROM observations WHERE turn_id=?",
            (turn_id,)).fetchone()
        conn.close()
        if row:
            return "praxis"
    # Turn log: cerca indicator fast_path / planner
    tdir = Path.home() / ".local/share/metnos/turns"
    if tdir.exists():
        for f in sorted(tdir.glob("*.jsonl"), reverse=True)[:2]:
            try:
                lines = f.read_text().split("\n")
            except Exception:
                continue
            for line in lines:
                if not line.strip():
                    continue
                if turn_id not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("turn_id") != turn_id:
                    continue
                mode = (o.get("mode") or "").lower()
                steps = o.get("steps") or []
                # Detect fast_path: mode contiene "fast" o n_step == 0 con final answer
                if "fast" in mode:
                    return "fast_path"
                # Detect dialog/cap-expand
                if o.get("final_kind") == "ask":
                    return "dialog"
                if not steps:
                    # Final answer senza step → fast_path L0 (es. get_now)
                    return "fast_path"
                return "planner"
    return "unknown"


def main():
    print(f"=== Bench coverage Praxis ===")
    print(f"sample size: {SAMPLE_SIZE}, category: {CATEGORY}")
    samples = stratified_sample(SAMPLE_SIZE, CATEGORY)
    print(f"loaded {len(samples)} stratified queries")

    # Clear cache per fresh-state measurement
    # IMPORTANTE: DELETE FROM preserva schema. Mai rm file (server in-memory
    # init flag staleness, ADR engine v2 _conn DDL idempotent gestisce comunque).
    for dbf, tables in [
        (Path.home() / ".local/share/metnos/autopath.sqlite",
         ["skills", "anti_skills", "observations"]),
        (Path.home() / ".local/share/metnos/fastpaths.sqlite", ["fastpaths"]),
    ]:
        if not dbf.exists():
            continue
        try:
            conn = sqlite3.connect(str(dbf))
            for t in tables:
                try:
                    conn.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass  # tabella non esiste, ignora
            conn.commit()
            conn.close()
            print(f"cleared {dbf.name}: {tables}")
        except Exception as ex:
            print(f"warn clear {dbf.name}: {ex}")

    results = []
    t0 = time.time()
    cancel_pending_dialogs()  # start clean state
    for i, q in enumerate(samples, 1):
        query = q["query"]
        print(f"[{i}/{len(samples)}] {query[:60]}", flush=True)
        t_start = time.time()
        r = turn(query, lang=q.get("lang") or "it")
        elapsed = time.time() - t_start
        turn_id = r.get("turn_id", "")
        # Detect cap-expand stuck state: turn_id literal "cap-expand"
        if turn_id == "cap-expand":
            cancel_pending_dialogs()
            r["error"] = "cap-expand_stuck"
            r["final_kind"] = "cap-expand"
            turn_id = ""
        # Cancel any dialog this query left pending
        cancel_pending_dialogs()
        handler = classify_handler(turn_id) if turn_id else "error"
        final_kind = r.get("final_kind", "")
        success = (final_kind == "answer")
        results.append({
            "query": query,
            "domain": q.get("domain"),
            "turn_id": r.get("turn_id"),
            "handler": handler,
            "final_kind": final_kind,
            "success": success,
            "latency_s": round(elapsed, 2),
            "error": r.get("error"),
        })
        if i % 10 == 0:
            elapsed_total = time.time() - t0
            eta = elapsed_total / i * (len(samples) - i)
            print(f"  progress {i}/{len(samples)} elapsed={elapsed_total:.0f}s ETA={eta:.0f}s", flush=True)

    # Aggregate
    total = len(results)
    by_handler = defaultdict(int)
    by_handler_ok = defaultdict(int)
    by_kind = defaultdict(int)
    by_domain_ok = defaultdict(lambda: [0, 0])  # [ok, total]
    latencies = []
    for r in results:
        by_handler[r["handler"]] += 1
        if r["success"]:
            by_handler_ok[r["handler"]] += 1
        by_kind[r["final_kind"]] += 1
        d = r.get("domain") or "unknown"
        by_domain_ok[d][1] += 1
        if r["success"]:
            by_domain_ok[d][0] += 1
        latencies.append(r["latency_s"])

    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total,
        "by_handler": dict(by_handler),
        "by_handler_ok": dict(by_handler_ok),
        "by_kind": dict(by_kind),
        "by_domain": {d: f"{ok}/{tot}" for d, (ok, tot) in by_domain_ok.items()},
        "coverage_total": sum(by_handler_ok.values()) / total if total else 0,
        "praxis_coverage": by_handler_ok.get("praxis", 0) / total if total else 0,
        "planner_coverage": by_handler_ok.get("planner", 0) / total if total else 0,
        "fast_path_coverage": by_handler_ok.get("fast_path", 0) / total if total else 0,
        "mean_latency_s": sum(latencies) / len(latencies) if latencies else 0,
        "p50_latency_s": sorted(latencies)[len(latencies)//2] if latencies else 0,
        "elapsed_total_s": round(time.time() - t0, 1),
    }

    print()
    print("=== SUMMARY ===")
    print(f"Total queries: {total}")
    print(f"Coverage TOTAL: {summary['coverage_total']*100:.1f}%")
    print(f"  Praxis:    {summary['praxis_coverage']*100:.1f}% ({by_handler_ok['praxis']}/{total})")
    print(f"  PLANNER:   {summary['planner_coverage']*100:.1f}% ({by_handler_ok['planner']}/{total})")
    print(f"  fast_path: {summary['fast_path_coverage']*100:.1f}% ({by_handler_ok['fast_path']}/{total})")
    print(f"By handler (all):     {dict(by_handler)}")
    print(f"By handler (success): {dict(by_handler_ok)}")
    print(f"By kind: {dict(by_kind)}")
    print(f"Latency mean: {summary['mean_latency_s']:.2f}s, p50: {summary['p50_latency_s']:.2f}s")
    print(f"Wall time: {summary['elapsed_total_s']}s")

    out = Path("/opt/metnos/runtime") / f"bench_praxis_coverage_{int(time.time())}.json"
    out.write_text(json.dumps({"summary": summary, "results": results},
                              indent=2, ensure_ascii=False))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
