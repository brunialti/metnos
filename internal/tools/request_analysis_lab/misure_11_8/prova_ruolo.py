#!/usr/bin/env python3
"""Ninth arm: the role contract moved from the prompt into code.

  A  reference prompt, only the output cap raised        -- best measured, 112
  I  A + deterministic normalization of non-request records

Three prompt wordings of this rule scored 112, 110 and 108 on the same held-out
sample. The validator's rule is total and mechanical, so §7.9 applies. The repair
sets verb and object to `none` on every record whose role is not `request`, and
does nothing else: it never invents a route, so an `incomplete_request` stays a
failure.

This is not a weakened validator. The validator is untouched; the repair is a
normalization of the kind production would run, exactly like the anchor repair.
Back to back on the same 120 held-out queries. Read-only on production.
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

import riparo_ruolo as RR                                     # noqa: E402
import prova_cieca as P                                       # noqa: E402


def costruisci_i():
    module = P.arm("i", contract=False, drop_tie_break=False)
    RR.install(module)
    return module


def main() -> int:
    queries, _ = P.sample()
    print(f"riparo di ruolo | {len(queries)} query mai usate | budget {P.BUDGET}\n",
          flush=True)
    arms = [("A riferimento", lambda: P.arm("a3", contract=False, drop_tie_break=False)),
            ("I riparo codice", costruisci_i)]
    results = {}
    for tag, build in arms:
        started = time.perf_counter()
        rows = P.run(build(), queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        nre = sum(1 for r in rows if "nonrequest_exe" in r["reason"])
        inc = sum(1 for r in rows if "incomplete_request" in r["reason"])
        print(f"{tag:16s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"nonrequest_exe {nre}  incomplete {inc}  "
              f"p50 {lat[len(lat) // 2]:.0f} ms  "
              f"({time.perf_counter() - started:.0f} s)", flush=True)

    a, i = results["A riferimento"], results["I riparo codice"]
    broke = [k for k in range(len(queries)) if a[k]["valid"] and not i[k]["valid"]]
    healed = [k for k in range(len(queries)) if not a[k]["valid"] and i[k]["valid"]]
    print(f"\nI rispetto ad A: rotte {len(broke)}  risanate {len(healed)}  "
          f"netto {len(healed) - len(broke):+d}")
    for k in broke:
        print(f"  - {i[k]['reason'][:32]:32s} {queries[k][:54]!r}")
    for k in healed:
        print(f"  + era {a[k]['reason'][:28]:28s} {queries[k][:54]!r}")

    (SCRATCH / "prova_ruolo.json").write_text(json.dumps(
        {"queries": queries, "results": results}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print("\nscritto prova_ruolo.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
