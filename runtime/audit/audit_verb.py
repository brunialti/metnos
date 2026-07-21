#!/usr/bin/env python3
"""
audit_verb.py — esegue 10 query e2e su un singolo verbo, scrive results JSON.

Uso:
    python3 audit_verb.py <verb>            # gira tutte le 10 query
    python3 audit_verb.py <verb> 3          # gira solo la query #3

Output:
    /tmp/audit/results/<verb>.json          # full structured trace
    stdout: una riga riassuntiva per query.

Filosofia (Roberto 30/4): convergence loop. Le query NON si modificano per
adattarle al codice. Se una query fallisce, si fixa il prompt verbo /
l'executor / il runtime, e si riesegue.
"""
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
for _p in (_RUNTIME, str(Path(_RUNTIME) / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_runtime import run_turn  # noqa: E402
from queries import VERBS, setup_fixtures  # noqa: E402

OUT = Path("/tmp/audit/results")
OUT.mkdir(parents=True, exist_ok=True)


def run_one(verb: str, idx: int, spec: dict) -> dict:
    q = spec["q"]
    expect_re = spec.get("expect_executor_re")
    expect_kind = spec.get("expect_kind", "answer")
    t0 = time.time()
    record = {
        "verb": verb,
        "idx": idx,
        "query": q,
        "expect_executor_re": expect_re,
        "expect_kind": expect_kind,
    }
    try:
        log = run_turn(q, verbose=False, cap_steps=12)
    except Exception as e:
        record.update(
            ok=False, exception=str(e),
            traceback=traceback.format_exc()[-2000:],
            duration_s=round(time.time() - t0, 1),
        )
        return record

    steps = []
    for s in log.steps:
        res = s.result if isinstance(s.result, dict) else {}
        steps.append({
            "n": s.step_num,
            "tool": s.chosen_tool,
            "ok": res.get("ok"),
            "ok_count": res.get("ok_count"),
            "error": s.error or res.get("error"),
            "truncated": res.get("truncated"),
            "result_keys": sorted(list(res.keys()))[:10] if isinstance(res, dict) else None,
        })

    tools = [s["tool"] for s in steps if s["tool"]]
    executor_match = (not expect_re) or any(re.fullmatch(expect_re, t) for t in tools)
    kind_match = log.final_kind == expect_kind
    any_step_failed = any(s["ok"] is False for s in steps)

    record.update(
        duration_s=round(time.time() - t0, 1),
        final_kind=log.final_kind,
        final_message=(log.final_message or "")[:500],
        tools=tools,
        steps=steps,
        executor_match=executor_match,
        kind_match=kind_match,
        any_step_failed=any_step_failed,
        pass_=executor_match and kind_match and not any_step_failed,
    )
    return record


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in VERBS:
        print(f"Uso: audit_verb.py <verb>  (verbi: {', '.join(VERBS.keys())})")
        sys.exit(2)
    verb = sys.argv[1]
    only_idx = int(sys.argv[2]) if len(sys.argv) > 2 else None

    setup_fixtures(verb)

    queries = VERBS[verb]["queries"]
    if only_idx is not None:
        queries = [queries[only_idx - 1]]

    results = []
    print(f"=== AUDIT VERB '{verb}' — {len(queries)} query ===", flush=True)
    for i, spec in enumerate(queries, 1):
        actual_idx = only_idx if only_idx else i
        print(f"[{verb} #{actual_idx}] {spec['q'][:80]}", flush=True)
        rec = run_one(verb, actual_idx, spec)
        results.append(rec)
        flag = "PASS" if rec.get("pass_") else "FAIL"
        tools_str = ",".join(rec.get("tools", []) or [])[:80]
        msg = (rec.get("final_message") or rec.get("exception") or "")[:120]
        print(f"  → {flag}  kind={rec.get('final_kind','?')}  {rec.get('duration_s','?')}s  tools=[{tools_str}]  msg={msg!r}",
              flush=True)

    out_path = OUT / f"{verb}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    n_pass = sum(1 for r in results if r.get("pass_"))
    print(f"\n=== {verb}: {n_pass}/{len(results)} pass — written to {out_path}", flush=True)
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    main()
