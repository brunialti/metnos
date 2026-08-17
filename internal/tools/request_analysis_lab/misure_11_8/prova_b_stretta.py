#!/usr/bin/env python3
"""Fourth arm: is the contract sentence written as well as it can be?

Arm B states the rule in our own jargon ("executable intents"), argues against a
temptation ("however clear ... may be") and puts the non-request branch first.
Arm D states the same contract as a rule about the two fields the validator
actually checks, in the validator's own order, with nothing to argue against.

Today's lesson is that a rewrite is measured, never judged by reading it: two
rewrites of the anchor rule, both reasonable on the page, cost -2 and -3. So B
and D run on the SAME held-out queries, back to back.

Read-only on production.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

SCRATCH = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRATCH))
sys.path.insert(0, "/opt/metnos")
sys.path.insert(0, "/opt/metnos/runtime")

import prova_cieca as P                                     # noqa: E402
import patch_corrente as PC                                 # noqa: E402

CONTRACT_STRETTO = (
    "A request record carries one canonical action and one canonical object, "
    "both\ndifferent from `none`. Every other role carries `none` as its action "
    "and `none`\nas its object.")


def arm_d(tag: str):
    module = P.load(P.LAB / "unified_query_bench_v23_checkpoint.py", f"arm_{tag}")
    if PC.LICENCE not in module.BASE_INSTRUCTION:
        raise AssertionError("licence text not found")
    module.BASE_INSTRUCTION = module.BASE_INSTRUCTION.replace(
        PC.LICENCE, CONTRACT_STRETTO)
    real = __import__("urllib.request", fromlist=["request"]).Request

    class _B(real):
        def __init__(self, url, data=None, headers=None, **kw):
            if data:
                body = json.loads(data)
                if "max_tokens" in body:
                    body["max_tokens"] = P.BUDGET
                    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            super().__init__(url, data=data, headers=headers or {}, **kw)

    module.urllib.request.Request = _B
    return module


def main() -> int:
    queries, groups = P.sample()
    print(f"B contro D | {len(queries)} query mai usate | budget {P.BUDGET}\n", flush=True)
    arms = [("B corrente", P.arm("b2", contract=True, drop_tie_break=False)),
            ("D stretta", arm_d("d"))]
    results = {}
    for tag, module in arms:
        started = time.perf_counter()
        rows = P.run(module, queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        print(f"{tag:12s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"p50 {lat[len(lat) // 2]:.0f} ms  ({time.perf_counter() - started:.0f} s)",
              flush=True)

    b, d = results["B corrente"], results["D stretta"]
    broke = [i for i in range(len(queries)) if b[i]["valid"] and not d[i]["valid"]]
    healed = [i for i in range(len(queries)) if not b[i]["valid"] and d[i]["valid"]]
    print(f"\nD rispetto a B: rotte {len(broke)}  risanate {len(healed)}  "
          f"netto {len(healed) - len(broke):+d}")
    for i in broke:
        print(f"  - {d[i]['reason'][:32]:32s} {queries[i][:54]!r}")
    for i in healed:
        print(f"  + {b[i]['reason'][:32]:32s} {queries[i][:54]!r}")

    (SCRATCH / "prova_b_stretta.json").write_text(json.dumps({
        "queries": queries, "results": results, "testo_D": CONTRACT_STRETTO,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\nscritto prova_b_stretta.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
