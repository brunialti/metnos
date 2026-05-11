#!/usr/bin/env python3
"""bench_intent_vaglio_tier — empirical benchmark fast vs middle (ADR 0106).

Misura precision dell'intent_extractor e concordanza del giudice LLM su
corpus storico (turn logs) con tier='fast' (qwen3:8b) vs tier='middle'
(gemma 4 26B). Promote a 'fast' SOLO se le soglie sono superate:

    - intent_extractor: >= 90% del baseline middle (verb+object match)
    - vaglio LLM judge: >= 95% concordanza approve/deny

Uso:
    /opt/suprastructure/.venv/bin/python -m bench_intent_vaglio_tier \
        --kind=intent --n=50          # benchmark intent_extractor
    /opt/suprastructure/.venv/bin/python -m bench_intent_vaglio_tier \
        --kind=vaglio --n=50          # benchmark vaglio LLM judge
    /opt/suprastructure/.venv/bin/python -m bench_intent_vaglio_tier \
        --kind=both --n=50            # entrambi

Output JSON in stdout + summary "PROMOTE_OK" / "PROMOTE_FAIL".

Nota onesta (§2.8 no silent failure): se il LLM e' down o il corpus e'
insufficiente (<20 query), il bench RIPORTA fallimento esplicito e il
risultato e' "INCONCLUSIVE" — NON va interpretato come "promote ok".
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/myclaw/runtime")


def _sample_queries(n: int) -> list[str]:
    """Estrae fino a `n` query distinte dai turn log piu' recenti."""
    out: list[str] = []
    seen: set[str] = set()
    fps = sorted(Path.home().glob(".local/share/metnos/turns/*.jsonl"))[-7:]
    for fp in reversed(fps):
        try:
            with open(fp) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    q = rec.get("user_query") or rec.get("query") or rec.get("text")
                    if not q or not isinstance(q, str):
                        continue
                    q = q.strip()
                    if not (5 < len(q) < 250):
                        continue
                    if q in seen:
                        continue
                    seen.add(q)
                    out.append(q)
                    if len(out) >= n:
                        return out
        except Exception:
            continue
    return out


def _bench_intent(queries: list[str]) -> dict:
    """Ogni query → estrai con tier=middle (baseline) e tier=fast (test).
    Concordanza = stesso (verb, object)."""
    from llm_router import LLMRouter
    from intent_extractor import extract_intent

    router = LLMRouter()
    middle = router.provider("middle")
    fast = router.provider("fast")

    def _call(prov):
        def _f(system, user, max_tokens=80, think=False):
            t0 = time.perf_counter()
            r = prov.chat(system, user, max_tokens=max_tokens,
                          temperature=0, think=think)
            dt = (time.perf_counter() - t0) * 1000
            return {"text": r.text or "", "in_tokens": r.in_tokens,
                    "out_tokens": r.out_tokens, "_ms": dt}
        return _f

    middle_call = _call(middle)
    fast_call = _call(fast)

    results = []
    middle_total_ms = 0.0
    fast_total_ms = 0.0
    for q in queries:
        t0 = time.perf_counter()
        m = extract_intent(q, middle_call)
        middle_ms = (time.perf_counter() - t0) * 1000
        middle_total_ms += middle_ms
        t0 = time.perf_counter()
        f = extract_intent(q, fast_call)
        fast_ms = (time.perf_counter() - t0) * 1000
        fast_total_ms += fast_ms
        agree = (
            m is not None and f is not None
            and m.get("verb") == f.get("verb")
            and m.get("object") == f.get("object")
        )
        results.append({
            "query": q[:80],
            "middle": m,
            "fast": f,
            "agree": agree,
            "middle_ms": round(middle_ms, 1),
            "fast_ms": round(fast_ms, 1),
        })

    n = len(results)
    agreed = sum(1 for r in results if r["agree"])
    pct = (agreed / n * 100) if n else 0
    threshold_pct = 90.0
    return {
        "kind": "intent",
        "n": n,
        "agreed": agreed,
        "concordance_pct": round(pct, 1),
        "threshold_pct": threshold_pct,
        "promote_ok": pct >= threshold_pct,
        "middle_avg_ms": round(middle_total_ms / max(n, 1), 1),
        "fast_avg_ms": round(fast_total_ms / max(n, 1), 1),
        "speedup_x": round(middle_total_ms / max(fast_total_ms, 1), 2),
        "samples": results[:5],
    }


def _bench_vaglio(queries: list[str]) -> dict:
    """Per ogni query simula un step (executor=read_files, args minimi) e
    confronta giudizio fast vs middle."""
    from vaglio import _judge_score_llm
    import vaglio as v

    # Salva e flippa via env
    import os
    saved_kind = os.environ.get("METNOS_JUDGE_KIND", "")
    os.environ["METNOS_JUDGE_KIND"] = "llm-v1"
    try:
        results = []
        middle_total_ms = 0.0
        fast_total_ms = 0.0
        # Patch tier interno: il _judge_score_llm hard-coda tier='middle';
        # per il bench invochiamo direttamente router con due tier diversi.
        from llm_router import LLMRouter
        import prompt_loader
        from config import DEFAULT_LANG
        router = LLMRouter()
        sys_prompt = prompt_loader.get("vaglio", DEFAULT_LANG)

        def _judge(tier_name, q):
            user = (
                f"Intent dell'utente: {q}\n"
                f"Executor proposto: read_files\n"
                f"Chiavi argomenti: ['paths']\n"
                f"Contesto: critical=False, capability=fs:read, step=1\n\n"
                f"Restituisci il punteggio di allineamento."
            )
            t0 = time.perf_counter()
            res = router.chat(sys_prompt, user, tier=tier_name,
                              max_tokens=120, for_code=False, think=False)
            dt = (time.perf_counter() - t0) * 1000
            text = (res.text or "").strip()
            import re
            m = re.search(
                r'\{[^{}]*"score"\s*:\s*([0-9.]+)[^{}]*\}', text)
            score = float(m.group(1)) if m else 0.5
            return score, dt

        for q in queries:
            ms, mdt = _judge("middle", q)
            middle_total_ms += mdt
            fs, fdt = _judge("fast", q)
            fast_total_ms += fdt
            # Concordanza: stesso lato della soglia (>=0.5 vs <0.5).
            m_approve = ms >= 0.5
            f_approve = fs >= 0.5
            agree = m_approve == f_approve
            results.append({
                "query": q[:80],
                "middle_score": round(ms, 2),
                "fast_score": round(fs, 2),
                "agree": agree,
                "middle_ms": round(mdt, 1),
                "fast_ms": round(fdt, 1),
            })

        n = len(results)
        agreed = sum(1 for r in results if r["agree"])
        pct = (agreed / n * 100) if n else 0
        threshold_pct = 95.0
        return {
            "kind": "vaglio",
            "n": n,
            "agreed": agreed,
            "concordance_pct": round(pct, 1),
            "threshold_pct": threshold_pct,
            "promote_ok": pct >= threshold_pct,
            "middle_avg_ms": round(middle_total_ms / max(n, 1), 1),
            "fast_avg_ms": round(fast_total_ms / max(n, 1), 1),
            "speedup_x": round(middle_total_ms / max(fast_total_ms, 1), 2),
            "samples": results[:5],
        }
    finally:
        if saved_kind:
            os.environ["METNOS_JUDGE_KIND"] = saved_kind
        else:
            os.environ.pop("METNOS_JUDGE_KIND", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["intent", "vaglio", "both"],
                    default="both")
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()

    queries = _sample_queries(args.n)
    if len(queries) < 20:
        print(json.dumps({
            "result": "INCONCLUSIVE",
            "reason": f"corpus insufficient ({len(queries)} query, need >=20)",
        }, indent=2))
        return 2

    out = {}
    overall_ok = True
    if args.kind in ("intent", "both"):
        try:
            r = _bench_intent(queries)
            out["intent"] = r
            overall_ok = overall_ok and r["promote_ok"]
        except Exception as e:
            out["intent"] = {"error": f"{type(e).__name__}: {e}"}
            overall_ok = False
    if args.kind in ("vaglio", "both"):
        try:
            r = _bench_vaglio(queries)
            out["vaglio"] = r
            overall_ok = overall_ok and r["promote_ok"]
        except Exception as e:
            out["vaglio"] = {"error": f"{type(e).__name__}: {e}"}
            overall_ok = False

    out["overall"] = "PROMOTE_OK" if overall_ok else "PROMOTE_FAIL"
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
