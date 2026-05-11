"""bench_latency_breakdown.py — analisi della latenza turn-by-turn.

Estrae ogni turno da `~/.local/share/metnos/turns/*.jsonl` e computa:

  - total_ms      = (ts_end - ts_start) * 1000
  - planner_llm_ms = sum(step.llm_latency_ms)        # solo PLANNER call
  - intent_ms     = sum(step.intent_ms or 0)         # ADR 0080 (4/5/2026)
  - vaglio_ms    = sum(step.vaglio_ms or 0)
  - exec_ms      = sum(step.exec_ms or 0)
  - rerank_ms    = sum(step.rerank_ms or 0)
  - prefilter_ms = sum(step.prefilter_ms or 0)
  - else_ms       = total_ms - planner_llm_ms - intent - vaglio - exec - rerank - prefilter
                   # quel che resta: catalog load, audit, scratchpad, IO, channel round-trip.

Compatibilita' (ADR 0080): i 5 campi fini sono nuovi sui StepLog. I turni
storici li hanno None: il bench somma 0 e ricade sul `else_ms` legacy.
"""
from __future__ import annotations

import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

TURNS = sorted(glob.glob(str(Path.home() / ".local/share/metnos/turns/*.jsonl")))


def collect_turns():
    out = []
    for fn in TURNS:
        try:
            with open(fn) as f:
                for line in f:
                    try:
                        t = json.loads(line)
                    except Exception:
                        continue
                    if not t.get("ts_start") or not t.get("ts_end"):
                        continue
                    if not (t.get("user_query") or "").strip():
                        continue
                    out.append(t)
        except Exception:
            continue
    return out


def stat(arr, label):
    if not arr:
        return f"{label:<24} (n=0)"
    a = sorted(arr)
    n = len(a)
    return (
        f"{label:<24} n={n:>5}  "
        f"mean={statistics.mean(a):>7.0f} ms  "
        f"p50={a[n//2]:>7.0f}  "
        f"p95={a[min(n-1, int(0.95*n))]:>7.0f}  "
        f"max={a[-1]:>7.0f}  "
        f"sum={sum(a)/1000:>7.1f} s"
    )


def _safe_int(v):
    """Ritorna int(v) per int/float, 0 per None/altro. ADR 0080: i campi
    intent_ms/vaglio_ms/exec_ms/rerank_ms/prefilter_ms sono None sui turni
    storici e vanno trattati come 0 nelle somme."""
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def main():
    turns = collect_turns()
    print(f"Loaded {len(turns)} turns from {len(TURNS)} files")
    print()

    total_ms = []
    planner_llm_ms = []
    else_ms = []
    n_steps_dist = []
    # Telemetria fine (ADR 0080)
    intent_ms_total = []
    vaglio_ms_total = []
    exec_ms_total = []
    rerank_ms_total = []
    prefilter_ms_total = []
    fine_grained_ms = []  # somma dei 5 nuovi campi
    n_with_fine_grained = 0

    # Per executor: latency stimata = tempo speso quando quello e' l'ultimo
    # step ok (cattura la varianza per categoria — IMAP vs FS vs web).
    by_exec_else_ms = defaultdict(list)
    by_exec_planner_ms = defaultdict(list)

    # Distribuzione PLANNER per N step
    planner_per_step_ms = []

    # Casi degeneri: vaglio_approved=False (rejection cost), error in result, ...
    n_vaglio_rejected = 0
    n_error = 0
    n_synth_request = 0
    synth_request_total_ms = []

    for t in turns:
        tot = (t["ts_end"] - t["ts_start"]) * 1000
        steps = t.get("steps") or []
        planner = sum(int(s.get("llm_latency_ms") or 0) for s in steps)
        else_t = tot - planner
        total_ms.append(tot)
        planner_llm_ms.append(planner)
        else_ms.append(else_t)
        n_steps_dist.append(len(steps))
        # Aggregati sui nuovi campi: 0 se mancano (turni vecchi).
        _intent = sum(_safe_int(s.get("intent_ms")) for s in steps)
        _vaglio = sum(_safe_int(s.get("vaglio_ms")) for s in steps)
        _exec = sum(_safe_int(s.get("exec_ms")) for s in steps)
        _rerank = sum(_safe_int(s.get("rerank_ms")) for s in steps)
        _pref = sum(_safe_int(s.get("prefilter_ms")) for s in steps)
        intent_ms_total.append(_intent)
        vaglio_ms_total.append(_vaglio)
        exec_ms_total.append(_exec)
        rerank_ms_total.append(_rerank)
        prefilter_ms_total.append(_pref)
        _fine = _intent + _vaglio + _exec + _rerank + _pref
        fine_grained_ms.append(_fine)
        if any(s.get("intent_ms") is not None or s.get("vaglio_ms") is not None
               or s.get("exec_ms") is not None for s in steps):
            n_with_fine_grained += 1
        for s in steps:
            ll = int(s.get("llm_latency_ms") or 0)
            planner_per_step_ms.append(ll)
            ct = s.get("chosen_tool") or ""
            if ct:
                by_exec_planner_ms[ct].append(ll)
            if s.get("vaglio_approved") is False:
                n_vaglio_rejected += 1
            if s.get("error"):
                n_error += 1
            if ct == "request_new_executor":
                n_synth_request += 1
                synth_request_total_ms.append(tot)
        # Attribuisci `else_t` all'ultimo executor non-final-answer (proxy
        # per la quota di exec dominante)
        last_exec = None
        for s in reversed(steps):
            ct = s.get("chosen_tool")
            if ct and ct != "final_answer":
                last_exec = ct
                break
        if last_exec:
            by_exec_else_ms[last_exec].append(else_t)

    print("═" * 80)
    print("AGGREGATI PER TURNO")
    print("═" * 80)
    print(stat(total_ms, "total_ms (turn)"))
    print(stat(planner_llm_ms, "  planner_llm_ms"))
    print(stat(else_ms, "  else_ms (intent+vaglio+exec+IO)"))
    print()
    if total_ms:
        ratio_planner = sum(planner_llm_ms) / sum(total_ms) * 100
        ratio_else = sum(else_ms) / sum(total_ms) * 100
        print(f"  → PLANNER LLM = {ratio_planner:5.1f}% del tempo totale")
        print(f"  → ALTRO       = {ratio_else:5.1f}% del tempo totale")
    print()

    # Telemetria fine (ADR 0080): mostra solo quando ci sono turni con i
    # nuovi campi popolati. Sui turni storici i 5 campi sono None → 0.
    if n_with_fine_grained:
        print("═" * 80)
        print(f"BREAKDOWN FINE (ADR 0080) — turni con telemetria: {n_with_fine_grained}/{len(turns)}")
        print("═" * 80)
        print(stat(prefilter_ms_total, "  prefilter_ms"))
        print(stat(intent_ms_total,    "  intent_ms"))
        print(stat(vaglio_ms_total,    "  vaglio_ms"))
        print(stat(exec_ms_total,      "  exec_ms"))
        print(stat(rerank_ms_total,    "  rerank_ms"))
        print(stat(fine_grained_ms,    "  somma_fine"))
        # Residuo: else_ms - somma_fine = catalog load + scratchpad + audit + channel.
        residual = [e - f for e, f in zip(else_ms, fine_grained_ms)]
        print(stat(residual, "  residual_ms"))
        print()
    print(stat(planner_per_step_ms, "planner_llm_ms (per step)"))
    print()
    print(stat(n_steps_dist, "n_steps per turn"))
    print()
    print(f"  vaglio_rejected: {n_vaglio_rejected} step")
    print(f"  step con error:  {n_error}")
    print(f"  request_new_executor calls: {n_synth_request}")
    if synth_request_total_ms:
        print(f"  total_ms quando synt parte: {stat(synth_request_total_ms, '  synt-turn')}")
    print()

    print("═" * 80)
    print("TOP-15 EXECUTORS PIU' COSTOSI (else_ms quando ultimo step)")
    print("═" * 80)
    print(f"  {'executor':<28}  {'n':>5}  {'mean_ms':>8}  {'p50':>7}  {'p95':>7}  {'sum_s':>7}")
    rows = []
    for ex, arr in by_exec_else_ms.items():
        rows.append((ex, arr))
    rows.sort(key=lambda kv: -sum(kv[1]))
    for ex, arr in rows[:15]:
        a = sorted(arr)
        n = len(a)
        rows_str = (
            f"  {ex:<28}  {n:>5}  "
            f"{statistics.mean(a):>8.0f}  "
            f"{a[n//2]:>7.0f}  "
            f"{a[min(n-1, int(0.95*n))]:>7.0f}  "
            f"{sum(a)/1000:>7.1f}"
        )
        print(rows_str)
    print()

    print("═" * 80)
    print("TOP-10 TURNI PIU' LENTI")
    print("═" * 80)
    rows2 = sorted(turns, key=lambda t: -(t["ts_end"] - t["ts_start"]))[:10]
    for t in rows2:
        tot = (t["ts_end"] - t["ts_start"]) * 1000
        steps = t.get("steps") or []
        planner = sum(int(s.get("llm_latency_ms") or 0) for s in steps)
        chain = " → ".join(
            (s.get("chosen_tool") or "?")[:18] for s in steps if s.get("chosen_tool")
        )
        print(f"  {tot/1000:6.1f} s  (planner_llm={planner/1000:.1f}s, else={(tot-planner)/1000:.1f}s)  steps={len(steps)}")
        print(f"    Q: {(t.get('user_query') or '')[:78]}")
        print(f"    chain: {chain}")
        print()


if __name__ == "__main__":
    main()
