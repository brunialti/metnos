#!/usr/bin/env python3
"""bench_planner_split — #H0c.2 bench ampio PLANNER split forced-on (19/5/2026).

Wrapper minimale su bench_planner_slim che:
  - Forza METNOS_PLANNER_SPLIT=1 in env.
  - Limita CONFIGS al solo SLIM+SMART (la config production-bound).
  - Esegue BENCH_CORPUS esteso (25q post-19/5 extension).
  - Default runs=3.

Output: solito JSONL in ~/.local/share/metnos/bench_planner_split_<ts>.jsonl
Stdout: summary delta vs split=OFF (richiede 2 run: 1 con split=1, 1 senza).

Uso:
    python3 runtime/bench_planner_split.py --runs 3 --output bench_split.json
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import json
import statistics
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or str(Path(__file__).resolve().parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def main():
    ap = argparse.ArgumentParser(description="Bench PLANNER split forced-on")
    ap.add_argument("--runs", type=int, default=3, help="Run per query (default 3)")
    ap.add_argument("--split", choices=["on", "off", "both"], default="both",
                     help="on=METNOS_PLANNER_SPLIT=1, off=baseline, both=run both serially")
    ap.add_argument("--output-dir", default=str(Path.home() / ".local" / "share" / "metnos"))
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"bench_planner_split_{ts}.jsonl"

    # Limito CONFIGS a SLIM+SMART (production-bound) per ridurre wall-time.
    # Da 25q × 4 cfg × 3 run = 300 call a 25q × 1 cfg × 3 run = 75 call.
    import bench_planner_slim as bench
    bench.CONFIGS = [("SLIM+SMART", True, "smart")]

    results_by_mode = {}
    modes = ["off", "on"] if args.split == "both" else [args.split]

    for mode in modes:
        os.environ["METNOS_PLANNER_SPLIT"] = "1" if mode == "on" else "0"
        print(f"\n=== mode=split-{mode} (METNOS_PLANNER_SPLIT={os.environ['METNOS_PLANNER_SPLIT']}) ===")
        print(f"=== corpus={len(bench.BENCH_CORPUS)} q × {args.runs} run = "
              f"{len(bench.BENCH_CORPUS) * args.runs} LLM calls ===")
        t0 = time.time()
        from loader import load_catalog
        catalog = load_catalog()
        from llm_provider import LlamaCppProvider
        provider = LlamaCppProvider()
        # Riusa _run_live di bench_planner_slim (non esiste come pubblico,
        # ricostruisco logica minimale)
        per_q_runs = []
        for entry in bench.BENCH_CORPUS:
            for run_idx in range(args.runs):
                t_q = time.time()
                try:
                    from agent_runtime import run_turn
                    log_ = run_turn(entry.q, mode="local", verbose=False)
                    elapsed = time.time() - t_q
                    n_steps = len(log_.steps) if hasattr(log_, "steps") else 0
                    first_tool = (log_.steps[0].chosen_tool if n_steps else "") or ""
                    ok = (getattr(log_, "final_kind", "") == "answer")
                    per_q_runs.append({
                        "q": entry.q, "expected": entry.expected,
                        "first_tool": first_tool, "ok": ok,
                        "elapsed_s": round(elapsed, 2), "n_steps": n_steps,
                        "mode": mode, "run": run_idx,
                    })
                    print(f"  [{mode} {run_idx+1}/{args.runs}] {entry.q[:50]:50s} "
                          f"→ {first_tool[:25]:25s} ok={ok} {elapsed:.1f}s")
                except Exception as ex:
                    per_q_runs.append({
                        "q": entry.q, "expected": entry.expected,
                        "error": str(ex), "mode": mode, "run": run_idx,
                    })
                    print(f"  [{mode} {run_idx+1}/{args.runs}] ERROR: {ex}")
        elapsed_total = time.time() - t0
        with out_file.open("a") as f:
            for r in per_q_runs:
                f.write(json.dumps(r) + "\n")
        results_by_mode[mode] = per_q_runs
        elapsed_list = [r["elapsed_s"] for r in per_q_runs if "elapsed_s" in r]
        ok_count = sum(1 for r in per_q_runs if r.get("ok"))
        print(f"  → mode={mode}: ok={ok_count}/{len(per_q_runs)}, "
              f"median={statistics.median(elapsed_list):.1f}s, "
              f"total wall={elapsed_total:.0f}s")

    # Delta summary
    if len(results_by_mode) == 2:
        off = results_by_mode["off"]; on = results_by_mode["on"]
        off_t = statistics.median([r["elapsed_s"] for r in off if "elapsed_s" in r])
        on_t = statistics.median([r["elapsed_s"] for r in on if "elapsed_s" in r])
        off_ok = sum(1 for r in off if r.get("ok"))
        on_ok = sum(1 for r in on if r.get("ok"))
        speedup = off_t / on_t if on_t > 0 else 0
        print(f"\n=== DELTA split=on vs off ===")
        print(f"  median elapsed off={off_t:.1f}s, on={on_t:.1f}s, speedup={speedup:.2f}x")
        print(f"  accuracy off={off_ok}/{len(off)} ({off_ok*100//max(1,len(off))}%), "
              f"on={on_ok}/{len(on)} ({on_ok*100//max(1,len(on))}%)")
    print(f"\n[bench_planner_split] output: {out_file}")


if __name__ == "__main__":
    main()
