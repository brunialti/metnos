#!/usr/bin/env python3
"""Seventh arm: the missing cell of the 2x2.

The held-out run measured three of the four combinations:

                       TIE_BREAK presente   TIE_BREAK tolto
  senza contratto      A = 112              G = ?
  con contratto        B = 110              C = 112

Removing TIE_BREAK is worth +2 given the contract (B -> C). The contract itself
costs -2 (A -> B). Nobody has measured the cell where TIE_BREAK goes away and
the contract never arrives, which is one edit from the reference prompt and
could be the best state on unseen queries.

If G lands near 114 the two effects are additive and independent. If it lands at
112 the removal only repairs damage the contract caused, and neither belongs in
the prompt. Both answers are worth the run. Read-only on production.
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

import prova_cieca as P                                       # noqa: E402


def main() -> int:
    queries, groups = P.sample()
    print(f"quadro 2x2 | {len(queries)} query mai usate | budget {P.BUDGET}\n",
          flush=True)

    arms = [("A riferimento", lambda: P.arm("a2", contract=False, drop_tie_break=False)),
            ("G senza tie_break", lambda: P.arm("g", contract=False, drop_tie_break=True))]
    results = {}
    for tag, build in arms:
        started = time.perf_counter()
        rows = P.run(build(), queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        print(f"{tag:18s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"p50 {lat[len(lat) // 2]:.0f} ms  ({time.perf_counter() - started:.0f} s)",
              flush=True)

    a, g = results["A riferimento"], results["G senza tie_break"]
    broke = [i for i in range(len(queries)) if a[i]["valid"] and not g[i]["valid"]]
    healed = [i for i in range(len(queries)) if not a[i]["valid"] and g[i]["valid"]]
    print(f"\nG rispetto ad A: rotte {len(broke)}  risanate {len(healed)}  "
          f"netto {len(healed) - len(broke):+d}")
    for i in broke:
        print(f"  - {g[i]['reason'][:32]:32s} {queries[i][:54]!r}")
    for i in healed:
        print(f"  + era {a[i]['reason'][:28]:28s} {queries[i][:54]!r}")

    print("\n=== punti ciechi, dove TIE_BREAK teneva le sue regole piu' lunghe")
    for name, indexes in groups.items():
        if not indexes:
            continue
        print(f"  {name:9s} ({len(indexes):2d}): " + "  ".join(
            f"{tag.split()[0]} {sum(1 for i in indexes if results[tag][i]['valid']):2d}"
            for tag, _ in arms))

    (SCRATCH / "prova_quadro.json").write_text(json.dumps({
        "queries": queries, "results": results,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\nscritto prova_quadro.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
