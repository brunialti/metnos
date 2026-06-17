"""bench_grammar.py — benchmark constrained tool_call (ADR 0133).

Esegue 10 query rappresentative su 2 modalita' (`grammar=off` baseline,
`grammar=on`). Per ogni run misura: latency, tool-correctness, args-
correctness, step count, convergenza, JSON validity.

Output JSON in `/tmp/bench_grammar_<ts>.json` + report stdout.

Uso:
    METNOS_GRAMMAR=0 python3 bench_grammar.py --mode baseline
    METNOS_GRAMMAR=1 python3 bench_grammar.py --mode grammar
    python3 bench_grammar.py --compare /tmp/bench_grammar_<a>.json /tmp/bench_grammar_<b>.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

QUERIES: list[dict] = [
    # Simple (atomic, single step)
    {"id": "simple_now",
     "query": "che ora e'?",
     "expected_first_tool": "get_now",
     "complexity": "simple"},
    {"id": "simple_files",
     "query": "trova i file pdf in /tmp",
     "expected_first_tool": "find_files",
     "complexity": "simple"},
    {"id": "simple_events",
     "query": "che appuntamenti ho domani",
     "expected_first_tool": "read_events",
     "complexity": "simple"},
    {"id": "simple_url",
     "query": "leggi https://example.com",
     "expected_first_tool": "read_urls_html",
     "complexity": "simple"},
    {"id": "simple_messages",
     "query": "leggi le ultime mail",
     "expected_first_tool": "read_messages",
     "complexity": "simple"},
    # Medium (2-step pipeline)
    {"id": "medium_create_dir",
     "query": "crea la cartella /tmp/metnos_bench_test",
     "expected_first_tool": "create_dirs",
     "complexity": "medium"},
    {"id": "medium_search_image",
     "query": "trovami foto al mare",
     "expected_first_tool": "find_images_indices",
     "complexity": "medium"},
    # Complex (3+ step pipeline, get_inputs/dialog)
    {"id": "complex_propose_morning",
     "query": "proponi 3 orari per un appuntamento di una ora la mattina settimana prossima",
     "expected_first_tool": "find_events_empty",
     "complexity": "complex"},
    {"id": "complex_propose_bob",
     "query": "proponi 3 orari per un appuntamento con Bob di una ora la mattina settimana prossima, dopo la scelta mandami una email con la scelta",
     "expected_first_tool": "find_events_empty",
     "complexity": "complex"},
    {"id": "ambiguous_general",
     "query": "fai qualcosa di interessante",
     "expected_first_tool": None,  # ambiguo, qualsiasi
     "complexity": "ambiguous"},
]


def _post_turn(query: str, admin_key: str, *,
               timeout_s: int = 240,
               base_url: str = "http://127.0.0.1:8770") -> dict:
    """POST /agent/turn, ritorna dict response."""
    t0 = time.time()
    payload = {"query": query, "conversation_id": f"bench_{int(t0)}"}
    req = urllib.request.Request(
        f"{base_url}/agent/turn",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Admin-Key": admin_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "elapsed_s": time.time() - t0, "data": data}
    except urllib.error.HTTPError as e:
        return {"ok": False, "elapsed_s": time.time() - t0,
                "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"ok": False, "elapsed_s": time.time() - t0,
                "error": f"{type(e).__name__}: {e}"}


def _analyze(result: dict, expected_first_tool: str | None) -> dict:
    """Estrai metriche da una response /agent/turn."""
    metrics = {
        "ok": result.get("ok", False),
        "elapsed_s": round(result.get("elapsed_s", 0), 2),
    }
    if not result.get("ok"):
        metrics["error"] = result.get("error", "unknown")
        return metrics
    d = result["data"]
    metrics["turn_id"] = (d.get("turn_id") or "")[:12]
    metrics["final_kind"] = d.get("final_kind", "")
    metrics["total_ms"] = d.get("total_ms", 0)
    steps = d.get("steps_summary") or []
    metrics["n_steps"] = len(steps)
    metrics["steps"] = [(s.get("tool"), s.get("ok")) for s in steps]
    first_tool = steps[0].get("tool") if steps else ""
    metrics["first_tool"] = first_tool
    metrics["first_tool_match"] = (
        first_tool == expected_first_tool
        if expected_first_tool else None
    )
    # Convergenza: chiuso "answer" con almeno 1 step OK
    metrics["converged"] = (
        d.get("final_kind") == "answer"
        and any(s.get("ok") for s in steps if s.get("tool"))
    )
    # First step OK (proxy per JSON validity in grammar mode)
    metrics["first_step_ok"] = bool(steps[0].get("ok") if steps else False)
    metrics["caps"] = len(d.get("expandable_caps") or [])
    return metrics


def run_bench(mode_label: str, admin_key: str) -> dict:
    print(f"\n=== Running bench '{mode_label}' on {len(QUERIES)} queries ===\n",
          flush=True)
    results = []
    for q in QUERIES:
        print(f"  [{q['id']:40s}] ...", end="", flush=True)
        res = _post_turn(q["query"], admin_key)
        m = _analyze(res, q.get("expected_first_tool"))
        m.update({"id": q["id"], "query": q["query"],
                  "expected_first_tool": q.get("expected_first_tool"),
                  "complexity": q.get("complexity")})
        results.append(m)
        ok_mark = "✓" if m.get("converged") else "✗"
        first = m.get("first_tool", "?")
        print(f" {ok_mark} {m.get('elapsed_s',0):.1f}s "
              f"first={first} steps={m.get('n_steps',0)}",
              flush=True)
    return {
        "mode": mode_label,
        "ts": time.time(),
        "results": results,
    }


def _agg(rs: list[dict], group_by_complexity: bool = False) -> dict:
    """Aggrega metriche."""
    if group_by_complexity:
        out = {}
        for r in rs:
            c = r.get("complexity", "unknown")
            out.setdefault(c, []).append(r)
        return {k: _aggregate_simple(v) for k, v in out.items()}
    return _aggregate_simple(rs)


def _aggregate_simple(rs: list[dict]) -> dict:
    if not rs:
        return {}
    elapsed = [r.get("elapsed_s", 0) for r in rs]
    converged = sum(1 for r in rs if r.get("converged"))
    first_tool_matches = sum(1 for r in rs
                              if r.get("first_tool_match") is True)
    first_tool_evaluable = sum(1 for r in rs
                                if r.get("expected_first_tool") is not None)
    first_step_ok = sum(1 for r in rs if r.get("first_step_ok"))
    return {
        "n": len(rs),
        "elapsed_s_mean": round(statistics.mean(elapsed), 2),
        "elapsed_s_median": round(statistics.median(elapsed), 2),
        "elapsed_s_p95": round(sorted(elapsed)[int(len(elapsed)*0.95)-1] if len(elapsed) > 1 else elapsed[0], 2),
        "converged_pct": round(100 * converged / len(rs), 1),
        "first_tool_match_pct": (
            round(100 * first_tool_matches / first_tool_evaluable, 1)
            if first_tool_evaluable else None),
        "first_step_ok_pct": round(100 * first_step_ok / len(rs), 1),
    }


def report(d: dict) -> None:
    print(f"\n{'='*70}\nBenchmark '{d['mode']}' — {len(d['results'])} queries"
          f"\n{'='*70}\n")
    print("Aggregate:", json.dumps(_agg(d["results"]), indent=2))
    print("\nBy complexity:",
          json.dumps(_agg(d["results"], group_by_complexity=True), indent=2))
    print("\nDetail:")
    for r in d["results"]:
        complexity = r.get("complexity", "?")
        first = r.get("first_tool", "?")
        exp = r.get("expected_first_tool")
        match = "" if exp is None else ("✓" if first == exp else f"✗ exp={exp}")
        ok_mark = "✓" if r.get("converged") else "✗"
        print(f"  {ok_mark} [{complexity:9s}] {r['id']:40s} "
              f"{r.get('elapsed_s',0):>5.1f}s "
              f"steps={r.get('n_steps',0)} "
              f"first={first} {match}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="run",
                   choices=["run", "compare"],
                   help="run: esegue bench. compare: confronta 2 file.")
    p.add_argument("--label", default="bench",
                   help="Label modalita' (es. baseline / grammar)")
    p.add_argument("--compare", nargs=2, metavar="FILE",
                   help="Confronta 2 file JSON.")
    args = p.parse_args()

    admin_key = Path.home().joinpath(".config/metnos/admin.key").read_text().strip()

    if args.compare:
        a = json.load(open(args.compare[0]))
        b = json.load(open(args.compare[1]))
        print(f"\n=== Compare: {a['mode']} vs {b['mode']} ===\n")
        ag_a = _agg(a["results"])
        ag_b = _agg(b["results"])
        print(f"{'Metric':35s} {'baseline':>15s} {'grammar':>15s} {'Δ':>15s}")
        for k in ag_a:
            va = ag_a[k]; vb = ag_b[k]
            if va is None or vb is None:
                print(f"{k:35s} {str(va):>15s} {str(vb):>15s}")
                continue
            try:
                delta = vb - va
                sign = "+" if delta > 0 else ""
                print(f"{k:35s} {va:>15} {vb:>15} {sign}{delta}")
            except (TypeError, ValueError):
                print(f"{k:35s} {va:>15} {vb:>15}")
        # Per-complexity confronto
        ag_a_c = _agg(a["results"], group_by_complexity=True)
        ag_b_c = _agg(b["results"], group_by_complexity=True)
        for c in sorted(set(ag_a_c.keys()) | set(ag_b_c.keys())):
            print(f"\n--- Complexity={c} ---")
            for k in ("converged_pct", "elapsed_s_mean", "first_tool_match_pct"):
                va = ag_a_c.get(c, {}).get(k); vb = ag_b_c.get(c, {}).get(k)
                print(f"  {k:30s} {str(va):>10s} → {str(vb):>10s}")
        return

    # Run mode
    d = run_bench(args.label, admin_key)
    out_path = f"/tmp/bench_grammar_{args.label}_{int(time.time())}.json"
    Path(out_path).write_text(json.dumps(d, indent=2, ensure_ascii=False))
    report(d)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
