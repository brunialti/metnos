#!/usr/bin/env python3
"""Fifth arm: does incisiveness pay, at equal content and equal length?

  C  contract + TIE_BREAK removed                  -- best measured state
  E  C + ONTOLOGY rewritten incisively             -- same 33 facts, sharper

Both arms run back to back on the SAME 120 held-out queries, because machine
state drifts: the same configuration scored 40, 38 and 39 on three runs while
another job shared the GPU. A stored result from an earlier run is not a control.

The two arms differ in one block and in nothing else, so a difference is
attributable. Read-only on production.
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

import ontologia_incisiva as OI                               # noqa: E402
import prova_cieca as P                                       # noqa: E402


def arm_e(tag: str):
    module = P.arm(tag, contract=True, drop_tie_break=True)
    OI.install(module)
    return module


def main() -> int:
    queries, groups = P.sample()
    originale = P.load(P.LAB / "unified_query_bench_v23_checkpoint.py", "misura")
    print(f"incisivita' | {len(queries)} query mai usate | budget {P.BUDGET}")
    print(f"  ONTOLOGY: {len(originale.ONTOLOGY_REFINEMENTS)} char -> "
          f"{len(OI.ONTOLOGIA_INCISIVA)} char "
          f"({len(OI.ONTOLOGIA_INCISIVA) - len(originale.ONTOLOGY_REFINEMENTS):+d})")
    print(f"  punti ciechi: "
          + ", ".join(f"{k} {len(v)}" for k, v in groups.items()) + "\n", flush=True)

    arms = [("C corrente", lambda: P.arm("c2", contract=True, drop_tie_break=True)),
            ("E incisiva", lambda: arm_e("e"))]
    results = {}
    for tag, build in arms:
        started = time.perf_counter()
        rows = P.run(build(), queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        print(f"{tag:12s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"p50 {lat[len(lat) // 2]:.0f} ms  ({time.perf_counter() - started:.0f} s)",
              flush=True)

    c, e = results["C corrente"], results["E incisiva"]
    broke = [i for i in range(len(queries)) if c[i]["valid"] and not e[i]["valid"]]
    healed = [i for i in range(len(queries)) if not c[i]["valid"] and e[i]["valid"]]
    print(f"\nE rispetto a C: rotte {len(broke)}  risanate {len(healed)}  "
          f"netto {len(healed) - len(broke):+d}")
    for i in broke:
        print(f"  - {e[i]['reason'][:32]:32s} {queries[i][:54]!r}")
    for i in healed:
        print(f"  + {c[i]['reason'][:32]:32s} {queries[i][:54]!r}")

    print("\n=== punti ciechi (il campione di messa a punto ne aveva zero)")
    for name, indexes in groups.items():
        if not indexes:
            continue
        print(f"  {name:9s} ({len(indexes):2d}): " + "  ".join(
            f"{tag.split()[0]} {sum(1 for i in indexes if results[tag][i]['valid']):2d}"
            for tag, _ in arms))

    print("\n=== dove cambia il route, a parita' di validita'")
    diversi = [i for i in range(len(queries))
               if c[i]["valid"] and e[i]["valid"] and c[i]["routes"] != e[i]["routes"]]
    print(f"  {len(diversi)} query instradate diversamente")
    for i in diversi[:12]:
        print(f"    {queries[i][:44]!r}\n       C {c[i]['routes']}\n       E {e[i]['routes']}")

    (SCRATCH / "prova_incisiva.json").write_text(json.dumps({
        "queries": queries, "results": results, "testo_E": OI.ONTOLOGIA_INCISIVA,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\nscritto prova_incisiva.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
