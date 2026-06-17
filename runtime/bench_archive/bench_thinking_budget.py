#!/usr/bin/env python3
"""bench_thinking_budget.py — bench corpus deterministico thinking budget LLM.

Validazione dell'euristica thinking budget definita in
[[feedback_thinking_budget_heuristic]] (19/5/2026, il modello locale).

Due livelli:
  - MICRO: per-step LLM call diretto, no agent_runtime overhead. Misura
    latenza + correctness su prompt fissi varianti tipo (intent, classify,
    tool-call pool small/medium/large, synthesis).
  - MACRO: end-to-end via run_turn per query rappresentative. Misura
    wall-time + success rate + planner correctness.

Uso:
    python3 runtime/bench_thinking_budget.py micro
    python3 runtime/bench_thinking_budget.py macro
    python3 runtime/bench_thinking_budget.py all

Output: JSONL su stdout + summary table per console + JSON aggregati su
disco (~/.local/share/metnos/bench_thinking_<ts>.json).

Convergence loop policy: bench deterministico → bench risultato decide
quale formula default-on (vs legacy dyn) in agent_runtime. NON modificare
agent_runtime senza prima bench corpus completo.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or str(Path(__file__).resolve().parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from llm_provider import LlamaCppProvider


# --- MICRO BENCH (per-step LLM call) --------------------------------------

# Prompt fissi per tipologia di decisione. Ognuno ha (system, user) + ground
# truth per misurare correctness.

MICRO_PROMPTS = [
    # 1. Intent extractor — tipico planner Stage 0 (extract user intent).
    {
        "kind": "intent",
        "system": "Estrai l'intent dall'input utente. Rispondi solo con il "
                  "nome del verbo canonico in italiano.",
        "user": "che ora è a Tokyo?",
        "expected_substring": "get_now",  # o similar
        "min_correctness_substring": ("ora", "time", "now"),
    },
    # 2. Tool-call pool small (≤5): 3 tool candidati, scelta ovvia.
    {
        "kind": "tool_small",
        "system": (
            "Hai 3 tool disponibili:\n"
            "- get_now(timezone): ritorna l'ora corrente.\n"
            "- read_files(paths): legge file.\n"
            "- write_files(paths, content): scrive file.\n"
            "Rispondi con il nome del tool da chiamare per la query utente."
        ),
        "user": "che ora è?",
        "expected_substring": "get_now",
        "min_correctness_substring": ("get_now",),
    },
    # 3. Tool-call pool medium (6-15): scelta meno ovvia.
    {
        "kind": "tool_medium",
        "system": (
            "Hai 10 tool: get_now, read_files, write_files, find_files, "
            "list_dirs, get_urls, read_urls_html, find_urls, send_messages, "
            "read_messages. Rispondi solo con il nome del tool."
        ),
        "user": "scarica https://httpbin.org/get",
        "expected_substring": "get_urls",
        "min_correctness_substring": ("get_urls",),
    },
    # 4. Tool-call pool large (>15): pool ampio, richiede discriminazione.
    {
        "kind": "tool_large",
        "system": (
            "Hai 20 tool: get_now, read_files, write_files, find_files, "
            "list_dirs, get_urls, read_urls_html, read_urls_pdf, find_urls, "
            "send_messages, read_messages, move_files, delete_files, "
            "create_dirs, delete_dirs, get_processes, get_location, "
            "find_places, get_places, get_files. "
            "Rispondi solo con il nome del tool."
        ),
        "user": "trova i file *.py in /tmp",
        "expected_substring": "find_files",
        "min_correctness_substring": ("find_files",),
    },
    # 5. Multi-step planning (la query "scarica e salva", composta).
    {
        "kind": "multi_step",
        "system": (
            "Hai 5 tool: get_urls, write_files, read_files, find_files, "
            "get_now. Per la query utente, descrivi la cascata di tool da "
            "chiamare (in ordine, separati da '->'). Niente prosa."
        ),
        "user": "scarica https://httpbin.org/get e salva in /tmp/x.txt",
        "expected_substring": "get_urls -> write_files",
        "min_correctness_substring": ("get_urls", "write_files"),
    },
    # 6. Binary contestuale (vaglio-like).
    {
        "kind": "binary",
        "system": (
            "Decidi se il path utente e' dentro lo scope consentito.\n"
            "Scope: /tmp/**, /home/user/Documents/**\n"
            "Rispondi solo 'allowed' o 'denied'."
        ),
        "user": "/etc/passwd",
        "expected_substring": "denied",
        "min_correctness_substring": ("denied",),
    },
    # 7. Synthesis stage 5 mini (code generation bounded).
    {
        "kind": "code_gen",
        "system": (
            "Genera SOLO il body di una funzione Python `invoke(args)` che "
            "ritorna {'ok': True, 'now': <ora ISO UTC>}. Niente import, "
            "niente prosa. Risposta tra ```python e ```."
        ),
        "user": "Genera invoke().",
        "expected_substring": "datetime",
        "min_correctness_substring": ("def invoke", "now"),
    },
]


BUDGETS_TO_TEST = [
    ("think_off", False, 0),
    ("budget_128", True, 128),
    ("budget_256", True, 256),
    ("budget_512", True, 512),
    ("budget_768", True, 768),
    ("budget_1024", True, 1024),
]

N_RUNS_PER_VARIANT = 3  # mediana di 3 per ridurre stocasticita'
N_RUNS_PER_VARIANT_MACRO = 2  # macro e' lento (~30-90s/run), 2 run = trade-off


def micro_bench(provider: LlamaCppProvider) -> list[dict]:
    """Esegue micro bench su MICRO_PROMPTS × BUDGETS_TO_TEST."""
    results = []
    for prompt in MICRO_PROMPTS:
        for label, think, budget in BUDGETS_TO_TEST:
            print(f"[micro] {prompt['kind']:12s} {label:14s} ", end="", flush=True)
            latencies = []
            corrects = []
            outputs = []
            tokens_out = []
            for run in range(N_RUNS_PER_VARIANT):
                t0 = time.time()
                kwargs = {"max_tokens": 300, "temperature": 0, "think": think}
                if think:
                    kwargs["reasoning_budget"] = budget
                try:
                    r = provider.chat(prompt["system"], prompt["user"], **kwargs)
                    dt = time.time() - t0
                    latencies.append(dt)
                    text_lower = (r.text or "").lower()
                    correct = all(
                        s.lower() in text_lower
                        for s in prompt["min_correctness_substring"]
                    )
                    corrects.append(correct)
                    outputs.append((r.text or "")[:120])
                    tokens_out.append(r.out_tokens)
                except Exception as e:
                    latencies.append(-1)
                    corrects.append(False)
                    outputs.append(f"ERR: {e}"[:120])
                    tokens_out.append(0)
            valid_lat = [x for x in latencies if x > 0]
            med_lat = statistics.median(valid_lat) if valid_lat else -1
            acc = sum(corrects) / len(corrects)
            med_tok = statistics.median(tokens_out) if tokens_out else 0
            print(f"med={med_lat:6.2f}s acc={acc*100:3.0f}% tok={med_tok:4.0f}")
            results.append({
                "kind": prompt["kind"],
                "variant": label,
                "think": think,
                "budget": budget,
                "latencies": latencies,
                "median_latency_s": med_lat,
                "accuracy": acc,
                "median_tokens_out": med_tok,
                "sample_output": outputs[0],
            })
    return results


# --- MACRO BENCH (end-to-end run_turn) ------------------------------------

MACRO_QUERIES = [
    # (label, query, expected_substring_in_final_message_lower, setup_shell, teardown_shell)
    # Bench ridotto (19/5/2026): 3 query rappresentative per matrix shorter:
    # triviale (1 step), tool-call mutating (1-2 step), multi-step composto.
    ("triviale_che_ora", "che ora è?", ":", None, None),
    ("file_write", "scrivi 'hello bench' nel file /tmp/bench_thinking_write.txt",
     "completato", None,
     "rm -f /tmp/bench_thinking_write.txt"),
    ("multi_step_download", "scarica https://httpbin.org/get e salva in /tmp/bench_thinking_dl.txt",
     "completato", None,
     "rm -f /tmp/bench_thinking_dl.txt"),
]


MACRO_BUDGETS = [
    ("dyn_legacy", "dyn"),
    ("ctx_v1", "ctx"),
    ("flat_256", "256"),
    ("flat_512", "512"),
]


def _run_setup(cmd: str | None) -> None:
    if cmd:
        os.system(cmd)


def macro_bench() -> list[dict]:
    """Esegue macro bench end-to-end. Per ogni query × variant, 3 run."""
    import importlib
    import agent_runtime
    results = []
    for label, query, expect_sub, setup, teardown in MACRO_QUERIES:
        for var_label, rb_env in MACRO_BUDGETS:
            print(f"[macro] {label:24s} {var_label:14s} ", end="", flush=True)
            latencies = []
            corrects = []
            n_steps = []
            for run in range(N_RUNS_PER_VARIANT_MACRO):
                _run_setup(setup)
                os.environ["METNOS_REASONING_BUDGET"] = rb_env
                # Re-import per pickup env (cache_key includes it).
                importlib.reload(agent_runtime)
                t0 = time.time()
                try:
                    log = agent_runtime.run_turn(query)
                    dt = time.time() - t0
                    latencies.append(dt)
                    correct = (log.final_kind == "answer") and (
                        expect_sub.lower() in (log.final_message or "").lower()
                    )
                    corrects.append(correct)
                    n_steps.append(len(log.steps))
                except Exception as e:
                    latencies.append(-1)
                    corrects.append(False)
                    n_steps.append(-1)
                    print(f" ERR: {e}", end="")
                _run_setup(teardown)
            valid_lat = [x for x in latencies if x > 0]
            med_lat = statistics.median(valid_lat) if valid_lat else -1
            acc = sum(corrects) / len(corrects)
            avg_steps = statistics.mean([n for n in n_steps if n > 0]) if any(n>0 for n in n_steps) else -1
            print(f"med={med_lat:6.1f}s acc={acc*100:3.0f}% steps={avg_steps:.1f}")
            results.append({
                "query_label": label,
                "variant": var_label,
                "budget_env": rb_env,
                "latencies": latencies,
                "median_latency_s": med_lat,
                "accuracy": acc,
                "avg_steps": avg_steps,
            })
    # Cleanup env
    os.environ.pop("METNOS_REASONING_BUDGET", None)
    return results


# --- AGGREGATE + REPORT ---------------------------------------------------

def aggregate_micro(results: list[dict]) -> dict:
    """Tabella aggregata: per ogni kind, miglior variant per latency e per accuracy."""
    by_kind: dict[str, list[dict]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(r)
    summary = {}
    for kind, rows in by_kind.items():
        # Best latency (min) tra le varianti con accuracy >= 0.66 (2/3 run).
        accurate = [r for r in rows if r["accuracy"] >= 0.66]
        if accurate:
            best_lat = min(accurate, key=lambda r: r["median_latency_s"])
            summary[kind] = {
                "best_variant": best_lat["variant"],
                "best_latency_s": best_lat["median_latency_s"],
                "best_accuracy": best_lat["accuracy"],
                "best_budget": best_lat["budget"],
            }
        else:
            summary[kind] = {"best_variant": "NONE_ACCURATE", "note": "all variants fail accuracy>=66%"}
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["micro", "macro", "all"])
    args = ap.parse_args()

    import config as _C  # §7.11
    out_path = _C.PATH_USER_DATA / f"bench_thinking_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    all_results = {}

    provider = LlamaCppProvider(model="local", endpoint="http://127.0.0.1:8080")

    if args.mode in ("micro", "all"):
        print(f"\n=== MICRO BENCH ({len(MICRO_PROMPTS)} prompts × {len(BUDGETS_TO_TEST)} variants × {N_RUNS_PER_VARIANT} runs) ===\n")
        all_results["micro"] = micro_bench(provider)
        all_results["micro_summary"] = aggregate_micro(all_results["micro"])
        print("\n=== MICRO SUMMARY (best variant per kind, accuracy ≥66%) ===")
        for kind, s in all_results["micro_summary"].items():
            print(f"  {kind:14s} → {s}")

    if args.mode in ("macro", "all"):
        print(f"\n=== MACRO BENCH ({len(MACRO_QUERIES)} queries × {len(MACRO_BUDGETS)} variants × {N_RUNS_PER_VARIANT} runs) ===\n")
        all_results["macro"] = macro_bench()

    with out_path.open("w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    main()
