#!/usr/bin/env python3
"""synt_multistage_35_v2.py — re-run di synt_multistage_35.py dopo i fix
strutturali del 28/4 (prompt prescrittivi stage 1-4, max_tokens 4000 stage
2/3, vocab esteso 17→20 con compute/compare/compress).

Differenze rispetto a v1:
  - Output va su results_multistage_35_v2.jsonl/.summary.json (file NUOVI).
  - think=True su TUTTI gli stage 1-5 (esplicito), per tenere il
    chain-of-thought di Gemma sui task di scelta nel vocab chiuso.
  - Codice di run_full immutato (i fix sono in `runtime/synt_multistage.py`).

LLM: SOLO Gemma 4 26B locale via LlamaCppProvider su 127.0.0.1:8080.

Idempotenza: re-run riprende da dove si era fermato (skip query gia' nel jsonl).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Path setup
RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
sys.path.insert(0, str(RUNTIME))

from synt_multistage import run_full  # type: ignore  # noqa: E402
from llm_provider import LlamaCppProvider  # type: ignore  # noqa: E402

# Tolleranza al SIGPIPE/BrokenPipe.
import builtins as _bi  # noqa: E402

_orig_print = _bi.print


def _safe_print(*args, **kwargs):
    try:
        _orig_print(*args, **kwargs)
    except BrokenPipeError:
        try:
            sys.stdout = open("/dev/null", "w")  # noqa: SIM115
        except Exception:
            pass


_bi.print = _safe_print


QUERIES_PATH = Path(__file__).resolve().parents[2] / "decisions/synt_stress/queries_50.json"
RESULTS_JSONL = Path(__file__).resolve().parents[2] / "decisions/synt_stress/results_multistage_35_v2.jsonl"
SUMMARY_JSON = Path(__file__).resolve().parents[2] / "decisions/synt_stress/results_multistage_35_v2.summary.json"

LLAMA_ENDPOINT = "http://127.0.0.1:8080"
LLAMA_MODEL = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"


def make_llm_call(provider, *, think: bool):
    """Adatta `LlamaCppProvider.chat(...)` alla signature di synt_multistage,
    passando esplicitamente il flag `think` al provider (semantics in
    `llm_provider.LlamaCppProvider.chat`).
    """
    def _call(system: str, user: str, max_tokens: int = 2048):
        cr = provider.chat(system, user, max_tokens=max_tokens,
                           temperature=0, think=think)
        return {
            "text": cr.text,
            "in_tokens": cr.in_tokens,
            "out_tokens": cr.out_tokens,
            "latency_ms": cr.latency_ms,
            "_think_intent": think,
        }
    return _call


def classify_correct(expected: str, state: str) -> bool:
    if expected == "new_executor" and state == "synthesized":
        return True
    if expected == "rejected" and state == "rejected":
        return True
    return False


def load_queries():
    data = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    return [q for q in data if q.get("expected") in ("new_executor", "rejected")]


def already_done() -> set[str]:
    if not RESULTS_JSONL.exists():
        return set()
    done = set()
    for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            if r.get("id"):
                done.add(r["id"])
        except json.JSONDecodeError:
            continue
    return done


def append_jsonl(rec: dict):
    RESULTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def stage_record(stages):
    out = []
    for s in stages:
        out.append({
            "stage": s.stage,
            "success": s.success,
            "error": s.error,
            "in_tokens": s.in_tokens,
            "out_tokens": s.out_tokens,
            "latency_ms": s.latency_ms,
        })
    return out


def run_one(query_obj, llm_middle_call, llm_wise_call):
    qid = query_obj["id"]
    query = query_obj["query"]
    expected = query_obj["expected"]
    desired = query_obj.get("desired_executor", "")

    t0 = time.time()
    try:
        run = run_full(query, llm_middle_call, llm_wise_call)
    except Exception as e:  # noqa: BLE001
        return {
            "id": qid,
            "query": query,
            "expected": expected,
            "desired_executor": desired,
            "state": "error",
            "name": None,
            "abandon_reason": f"exception: {type(e).__name__}: {e}",
            "correct": False,
            "total_latency_ms": int((time.time() - t0) * 1000),
            "in_tokens": 0,
            "out_tokens": 0,
            "code_chars": 0,
            "stages": [],
            "runtime_error": True,
        }

    in_t, out_t = run.total_tokens()
    code_chars = len(run.code_text or "")
    state = run.final_state
    correct = classify_correct(expected, state)

    return {
        "id": qid,
        "query": query,
        "expected": expected,
        "desired_executor": desired,
        "state": state,
        "name": run.name,
        "abandon_reason": run.abandon_reason,
        "correct": correct,
        "total_latency_ms": run.total_latency_ms(),
        "in_tokens": in_t,
        "out_tokens": out_t,
        "code_chars": code_chars,
        "stages": stage_record(run.stages),
        "runtime_error": False,
    }


def write_summary(results: list[dict]):
    by_cat = {"new_executor": [], "rejected": []}
    for r in results:
        by_cat.setdefault(r["expected"], []).append(r)

    def cat_acc(rows):
        if not rows:
            return None
        ok = sum(1 for x in rows if x["correct"])
        return {"correct": ok, "total": len(rows), "accuracy_pct": round(100.0 * ok / len(rows), 1)}

    total_lat = sum(r["total_latency_ms"] for r in results)
    n = len(results)
    avg_lat_s = round((total_lat / 1000.0) / n, 2) if n else 0
    in_t = sum(r["in_tokens"] for r in results)
    out_t = sum(r["out_tokens"] for r in results)

    by_stage = {1: {}, 2: {}, 3: {}, 4: {}, 5: {}, "rejected": 0, "synthesized": 0, "error": 0}
    for r in results:
        s = r["state"]
        if s == "synthesized":
            by_stage["synthesized"] += 1
        elif s == "rejected":
            by_stage["rejected"] += 1
        elif s == "error":
            by_stage["error"] += 1
        elif s == "abandoned":
            ar = r.get("abandon_reason") or ""
            stage_num = None
            for k in (1, 2, 3, 4, 5):
                if ar.startswith(f"stage{k}:"):
                    stage_num = k
                    break
            if stage_num is None:
                by_stage[1].setdefault("unknown", 0)
                by_stage[1]["unknown"] += 1
            else:
                tag = ar.split(":", 1)[1].strip()[:80]
                by_stage[stage_num].setdefault(tag, 0)
                by_stage[stage_num][tag] += 1

    overall = cat_acc(results)
    summary = {
        "n_queries": n,
        "model": LLAMA_MODEL,
        "endpoint": LLAMA_ENDPOINT,
        "by_category": {
            "new_executor": cat_acc(by_cat.get("new_executor", [])),
            "rejected": cat_acc(by_cat.get("rejected", [])),
        },
        "overall": overall,
        "avg_latency_s": avg_lat_s,
        "total_latency_s": round(total_lat / 1000.0, 1),
        "total_in_tokens": in_t,
        "total_out_tokens": out_t,
        "abandon_distribution_by_stage": by_stage,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main():
    queries = load_queries()
    done = already_done()
    print(f"[init v2] {len(queries)} non-proto-mnest queries; "
          f"{len(done)} already done", flush=True)

    provider = LlamaCppProvider(model=LLAMA_MODEL, endpoint=LLAMA_ENDPOINT)
    # think=True su TUTTI gli stage (1-4 middle e 5 wise), come da specifica
    # del task: "non degradare la scelta di verbo nel vocab chiuso".
    llm_middle = make_llm_call(provider, think=True)
    llm_wise = make_llm_call(provider, think=True)

    existing = []
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    t_start = time.time()
    for i, q in enumerate(queries, start=1):
        qid = q["id"]
        if qid in done:
            print(f"[{i}/{len(queries)}] {qid}: skip (already done)", flush=True)
            continue

        q_short = q["query"][:60].replace("\n", " ")
        print(f"[{i}/{len(queries)}] {qid}: {q_short!r} ...", flush=True)

        rec = run_one(q, llm_middle, llm_wise)
        append_jsonl(rec)
        existing.append(rec)

        elapsed_s = (time.time() - t_start)
        lat_s = rec["total_latency_ms"] / 1000.0
        print(
            f"[{i}/{len(queries)}] {qid}: state={rec['state']} "
            f"name={rec['name']!s} "
            f"correct={rec['correct']} in {lat_s:.1f}s "
            f"(total elapsed {elapsed_s/60:.1f}min)",
            flush=True,
        )

    summ = write_summary(existing)
    print("\n=== SUMMARY (v2) ===")
    print(json.dumps(summ, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
