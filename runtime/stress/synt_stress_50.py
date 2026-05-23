#!/usr/bin/env python3
"""synt_stress_50.py — stress test del sintetizzatore Metnos su 50+ richieste.

Scopo (vedi ADR 0049, 0050):
- Misurare il tasso di esiti corretti (new_executor / proto_mnest / rejected)
  contro la distribuzione attesa 60/30/10.
- Identificare il modello LLM minimo per la convergenza (>=80%).
- Estrarre i gap di synt che impediscono la convergenza.

Architettura del test
---------------------
Il sintetizzatore Metnos lavora dentro la cascata reattiva: compose -> generate.
Per esercitare la fase generate (che e' il vero scopo del test), il banco di
prova *prepara* il mnestoma con un proto-mnest sintetico per ogni query, poi
chiama Synt.react() con quel proto-mnest e il target_intent estratto dalla
query. La compose-only (senza catena nota) fallisce e si scende in _generate
che chiama il tier=wise via LLMRouter.

Outcomes possibili:
- new_executor   : SynthProposal.state == 'generating' o 'born'
- rejected       : 'abandoned' con reason di policy/scope/sandbox
- proto_mnest    : 'composed' (e' stato risolto componendo)
- fail_other     : 'abandoned' generico (LLM error, missing fields, ecc.)

Output:
- <install_root>/decisions/synt_stress/results_iter_<N>.jsonl  (un record/query)
- summary su stdout

Il banco e' isolato: usa SYNT_AUDIT_DIR / SYNT_LOCK_PATH / SYNT_PROPOSALS_DIR
in /tmp/synt_stress_run/<runid>/ per non interferire con lo stato persistente.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR.parent))


def _ensure_isolated_synt_env(run_id: str) -> Path:
    base = Path(tempfile.gettempdir()) / "synt_stress_run" / run_id
    base.mkdir(parents=True, exist_ok=True)
    os.environ["SYNT_AUDIT_DIR"] = str(base / "audit")
    os.environ["SYNT_LOCK_PATH"] = str(base / "locks.json")
    os.environ["SYNT_PROPOSALS_DIR"] = str(base / "proposals")
    os.environ["SYNT_REJECTED_DIR"] = str(base / "rejected")
    os.environ["MNESTOMA_DB_PATH"] = str(base / "mnestoma.sqlite")
    return base


def _classify_outcome(prop, query_record: dict) -> str:
    """Mappa SynthProposal -> outcome semantico.

    Categorie:
        new_executor : proposal generato e birth-tests passati (state=generating/born).
        proto_mnest  : risolto via compose, no nuovo executor (state=composed).
        rejected     : abbandonato per ragioni di sicurezza/policy/scope/sandbox.
        synt_gap     : synt ha prodotto codice ma birth-test o convention falliti
                       (significa che il SYNT ha provato, ma i test/codice non
                       reggono al primo colpo; vedi ADR 0049 D6 — niente refinement loop).
        fail_other   : abbandono generico (LLM error, missing fields, infra).
    """
    state = prop.state
    if state in ("generating", "born"):
        return "new_executor"
    if state == "composed":
        return "proto_mnest"
    if state in ("rejected",):
        return "rejected"
    if state == "abandoned":
        rationale = (prop.rationale or "").lower()
        # Rejection per policy/safety/scope/sandbox
        if any(k in rationale for k in (
            "sandbox dangerous", "dangerous call",
            "policy", "scope violation", "non-stdlib",
        )):
            return "rejected"
        # Synt ha tentato ma e' caduto su qualita' di codice/test
        if any(k in rationale for k in (
            "birth tests failed", "convention",
            "ast parse failed",
        )):
            return "synt_gap"
        return "fail_other"
    return "fail_other"


def _executors_used_from_chain(prop) -> list[str]:
    if not prop.artefact:
        return []
    chain = prop.artefact.get("chain")
    if isinstance(chain, list):
        return [str(x) for x in chain if x]
    name = prop.artefact.get("name")
    if name:
        return [str(name)]
    return []


def _build_router(tier_override: str | None = None):
    """Costruisce LLMRouter; tier_override applica un default planner fast.

    Per il bench della fase E (modello LLM minimo), bypassiamo la quality-floor
    monkey-patching `_wise_passes_quality_floor` a `True`. Bypass scoped al
    test: NON modifica la configurazione production.
    """
    import llm_router
    from llm_router import LLMRouter
    if tier_override in {"qwen3:8b", "qwen3:8b-think", "qwen2.5:7b-instruct"}:
        # Bypass quality floor per il test
        llm_router._wise_passes_quality_floor = lambda spec: True
        if tier_override == "qwen3:8b-think":
            wise_spec = {"provider": "ollama", "model": "qwen3:8b",
                          "endpoint": "http://localhost:11434", "think": True}
        elif tier_override == "qwen2.5:7b-instruct":
            wise_spec = {"provider": "ollama", "model": "qwen2.5:7b-instruct",
                          "endpoint": "http://localhost:11434", "think": False}
        else:
            wise_spec = {"provider": "ollama", "model": "qwen3:8b",
                          "endpoint": "http://localhost:11434", "think": False}
        tiers = {
            "fast": {"provider": "ollama", "model": "qwen3:8b",
                     "endpoint": "http://localhost:11434", "think": False},
            "wise": wise_spec,
        }
        return LLMRouter(tiers_override=tiers)
    if tier_override == "claude":
        # Fallback online (Claude). Richiede ANTHROPIC_API_KEY in env.
        tiers = {
            "fast": {"provider": "ollama", "model": "qwen3:8b",
                     "endpoint": "http://localhost:11434", "think": False},
            "wise": {"provider": "anthropic",
                     "model": "claude-sonnet-4-5-20250929"},
        }
        return LLMRouter(tiers_override=tiers)
    # Default: configurazione di Roberto (Gemma 4 26B su llamacpp)
    return LLMRouter()


def _quality_floor_bypass_check(_router):
    """qwen3:8b non passa il quality floor; il bypass avviene via override."""
    return True


def run_query_synt_direct(query_record: dict, *, router=None) -> dict:
    """Esegue una query forzando la cascade reactive verso _generate.

    Costruisce un proto-mnest sintetico (src fittizio, dst = desired_executor):
    compose non puo' trovare catene perche' src non esiste nel grafo, quindi
    il synt scende a _generate via tier=wise. Questo testa direttamente la
    capacita' del SYNT di produrre, validare e rifiutare un nuovo executor.

    Adatto a:
        - new_executor : misura se synt produce executor con birth-test passing
        - rejected     : misura se synt ferma la sintesi su safety/sandbox
    """
    from mnestoma import Mnestoma, build_desired_signature
    from synt import Synt, make_request

    qid = query_record["id"]
    q = query_record["query"]
    record = _new_record(query_record, mode="synt_direct")
    t0 = time.perf_counter()
    try:
        m = Mnestoma()
        desired = (
            query_record.get("desired_executor")
            or "do_" + qid.replace("-", "_")
        )
        sig = build_desired_signature(desired, {}, q)
        src = "stress_src_" + qid
        proto_id = m.record_passing(
            src, "1.0.0", desired,
            dst_exists=False,
            desired_signature=sig,
            tags=["stress"],
        )

        if router is None:
            router = _build_router()

        synt = Synt(mnestoma=m, router=router)
        req = make_request(
            target_intent=q,
            proto_mnest=proto_id,
            capability_hint=query_record.get("capability_hint")
                            or [desired],
            budget_cents=200,
        )
        prop = synt.react(req)

        record["outcome"] = _classify_outcome(prop, query_record)
        record["state"] = prop.state
        record["strategy"] = prop.strategy
        record["synt_rationale"] = prop.rationale
        record["executors_used"] = _executors_used_from_chain(prop)
        if prop.artefact:
            record["proposal_id"] = prop.artefact.get("proposal_id")
            record["proposal_name"] = prop.artefact.get("name")
        # Cerca l'ultima audit entry per raccogliere LLM provider/tokens
        audit_dir = Path(os.environ.get("SYNT_AUDIT_DIR", ""))
        if audit_dir.exists():
            files = sorted(audit_dir.glob("*.jsonl"))
            if files:
                last = files[-1]
                lines = last.read_text().splitlines()
                for ln in reversed(lines):
                    try:
                        e = json.loads(ln)
                    except Exception:
                        continue
                    if e.get("request_id") != req.request_id:
                        continue
                    record["wise_provider"] = e.get("llm_provider", "?")
                    record["wise_model"]    = e.get("llm_model", "?")
                    record["llm_in_tokens"]  = e.get("llm_in_tokens", 0)
                    record["llm_out_tokens"] = e.get("llm_out_tokens", 0)
                    bt = e.get("birth_test_results") or {}
                    record["birth_test_summary"] = bt.get("summary", "")
                    break
        m.close()
    except Exception as e:
        record["errors"].append(repr(e))
        record["outcome"] = "fail_other"
    finally:
        record["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return record


def run_query_via_planner(query_record: dict) -> dict:
    """Esegue una query attraverso il planner ReAct (run_turn), non chiamando
    direttamente il synt. Adatto al caso proto_mnest dove ci si aspetta che il
    planner componga gli executor esistenti senza chiedere capacita' nuove.

    Outcome:
        - proto_mnest : il planner ha terminato con final_kind='answer' usando
                        almeno un executor del pool, ed e' visto come compose.
        - fail_other  : loop_break / cap / errore.
    """
    record = _new_record(query_record, mode="planner")
    t0 = time.perf_counter()
    try:
        from agent_runtime import run_turn
        log = run_turn(query_record["query"], verbose=False)
        kind = log.final_kind
        steps_used = [
            s.chosen_tool for s in log.steps
            if getattr(s, "chosen_tool", None)
        ]
        record["state"] = kind
        record["strategy"] = "planner"
        record["synt_rationale"] = log.final_message[:200]
        record["executors_used"] = steps_used
        record["llm_in_tokens"]  = sum(s.llm_in_tokens or 0 for s in log.steps)
        record["llm_out_tokens"] = sum(s.llm_out_tokens or 0 for s in log.steps)
        record["wise_provider"] = "planner"
        record["wise_model"] = "qwen3:8b/gemma-4-26B"  # default planner stack
        if kind == "answer" and steps_used:
            record["outcome"] = "proto_mnest"
        elif kind == "answer" and not steps_used:
            # Risposta diretta senza tool: il planner ha rifiutato/risposto
            record["outcome"] = "rejected"
        elif kind in ("loop_break", "cap_steps", "cap_same_executor", "error"):
            record["outcome"] = "fail_other"
        else:
            record["outcome"] = "fail_other"
    except Exception as e:
        record["errors"].append(repr(e))
        record["outcome"] = "fail_other"
    finally:
        record["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return record


def _new_record(query_record: dict, *, mode: str) -> dict:
    return {
        "id": query_record["id"],
        "query": query_record["query"],
        "expected": query_record.get("expected", "?"),
        "rationale": query_record.get("rationale", ""),
        "mode": mode,
        "outcome": "fail_other",
        "latency_ms": 0,
        "executors_used": [],
        "errors": [],
        "state": "?",
        "strategy": "?",
        "synt_rationale": "",
        "proposal_id": None,
        "proposal_name": None,
        "llm_in_tokens": 0,
        "llm_out_tokens": 0,
        "wise_provider": "?",
        "wise_model": "?",
        "birth_test_summary": "",
    }


def run_query(query_record: dict, *, run_id: str, router=None) -> dict:
    """Dispatcher: proto_mnest -> planner; altrimenti -> synt direct."""
    expected = query_record.get("expected", "")
    if expected == "proto_mnest":
        return run_query_via_planner(query_record)
    return run_query_synt_direct(query_record, router=router)


def run_dataset(queries: list[dict], *, run_id: str,
                tier_override: str | None = None,
                max_queries: int | None = None,
                output_path: Path | None = None,
                progress: bool = True) -> list[dict]:
    """Esegue tutte le query e ritorna i record. Output JSONL streaming."""
    if max_queries is not None:
        queries = queries[:max_queries]
    router = _build_router(tier_override)
    out: list[dict] = []
    out_fp = None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out_fp = open(output_path, "w", encoding="utf-8")
    for i, qr in enumerate(queries, 1):
        if progress:
            print(f"[{i:3d}/{len(queries)}] {qr['id']:12s} expected={qr.get('expected','?'):14s} ", flush=True)
        rec = run_query(qr, run_id=run_id, router=router)
        rec["index"] = i
        out.append(rec)
        if out_fp is not None:
            out_fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            out_fp.flush()
        if progress:
            ok = "OK" if rec["outcome"] == rec["expected"] else "MISS"
            print(
                f"           outcome={rec['outcome']:14s} state={rec['state']:14s} "
                f"latency={rec['latency_ms']:5d}ms wise={rec['wise_provider']}:"
                f"{rec['wise_model'][:30]:30s} [{ok}]"
            )
    if out_fp is not None:
        out_fp.close()
    return out


def summarize(results: list[dict]) -> dict:
    """Calcola distribuzione effettiva vs attesa + accuracy per categoria."""
    n = len(results)
    by_expected = {"new_executor": 0, "proto_mnest": 0, "rejected": 0}
    by_outcome  = {"new_executor": 0, "proto_mnest": 0, "rejected": 0,
                   "synt_gap": 0, "fail_other": 0}
    correct = {"new_executor": 0, "proto_mnest": 0, "rejected": 0}
    for r in results:
        e = r["expected"]
        o = r["outcome"]
        if e in by_expected:
            by_expected[e] += 1
        if o in by_outcome:
            by_outcome[o] += 1
        if e in correct and o == e:
            correct[e] += 1
    accuracy_pct = {
        cat: (correct[cat] / by_expected[cat] * 100.0 if by_expected[cat] else 0.0)
        for cat in correct
    }
    overall_correct = sum(correct.values())
    avg_latency = (
        sum(r["latency_ms"] for r in results) / max(1, n)
    )
    total_in_tokens  = sum(r.get("llm_in_tokens", 0) or 0 for r in results)
    total_out_tokens = sum(r.get("llm_out_tokens", 0) or 0 for r in results)
    return {
        "n": n,
        "by_expected": by_expected,
        "by_outcome": by_outcome,
        "correct": correct,
        "accuracy_pct": accuracy_pct,
        "overall_correct": overall_correct,
        "overall_pct": overall_correct / max(1, n) * 100.0,
        "avg_latency_ms": int(avg_latency),
        "total_in_tokens": total_in_tokens,
        "total_out_tokens": total_out_tokens,
    }


def print_summary(s: dict) -> None:
    print()
    print("=" * 72)
    print(f"Total queries:    {s['n']}")
    print(f"Overall correct:  {s['overall_correct']}/{s['n']}  ({s['overall_pct']:.1f}%)")
    print(f"Avg latency:      {s['avg_latency_ms']} ms")
    print(f"Tokens in/out:    {s['total_in_tokens']} / {s['total_out_tokens']}")
    print()
    print("By expected:")
    for k, v in s["by_expected"].items():
        print(f"  {k:14s} {v:3d} (correct: {s['correct'].get(k,0)}, "
              f"acc: {s['accuracy_pct'].get(k,0):.1f}%)")
    print("By outcome:")
    for k, v in s["by_outcome"].items():
        print(f"  {k:14s} {v:3d}")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True, type=Path,
                    help="path al JSON con la lista di query")
    ap.add_argument("--out", required=True, type=Path,
                    help="path al JSONL di output")
    ap.add_argument("--max", type=int, default=None,
                    help="limita numero di query")
    ap.add_argument("--tier", default=None,
                    choices=[None, "qwen3:8b", "qwen3:8b-think",
                              "qwen2.5:7b-instruct", "claude"],
                    help="override del wise tier per test comparativi")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    run_id = args.run_id or uuid.uuid4().hex[:8]
    base = _ensure_isolated_synt_env(run_id)
    print(f"[stress] run_id={run_id} env={base} tier={args.tier or 'default'}")
    queries = json.loads(args.queries.read_text(encoding="utf-8"))
    if not isinstance(queries, list):
        print("error: --queries deve essere una lista JSON", file=sys.stderr)
        sys.exit(2)
    results = run_dataset(
        queries, run_id=run_id, tier_override=args.tier,
        max_queries=args.max, output_path=args.out,
    )
    s = summarize(results)
    print_summary(s)
    summary_path = args.out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(s, indent=2, ensure_ascii=False))
    print(f"[stress] summary -> {summary_path}")


if __name__ == "__main__":
    main()
