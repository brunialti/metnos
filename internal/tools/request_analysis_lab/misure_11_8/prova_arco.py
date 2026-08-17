#!/usr/bin/env python3
"""Sixth arm: does naming the edge field that actually exists fix the edges?

  C  contract + TIE_BREAK removed                  -- best measured state
  F  C + the edge instruction redirected onto `input_from_predicate_id`

Prediction to falsify: the gain, if any, concentrates on failures whose reason
is `predicate_N_source_edge`. If the count moves but the reasons do not, the
explanation is wrong even when the number improves.

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

import campo_arco as CA                                       # noqa: E402
import prova_cieca as P                                       # noqa: E402


def main() -> int:
    queries, _ = P.sample()
    print(f"campo arco | {len(queries)} query mai usate | budget {P.BUDGET}\n",
          flush=True)

    def costruisci_f():
        module = P.arm("f", contract=True, drop_tie_break=True)
        CA.install(module)
        return module

    arms = [("C corrente", lambda: P.arm("c3", contract=True, drop_tie_break=True)),
            ("F campo reale", costruisci_f)]
    results = {}
    for tag, build in arms:
        started = time.perf_counter()
        rows = P.run(build(), queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        archi = sum(1 for r in rows if "source_edge" in r["reason"])
        print(f"{tag:14s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"cadute su arco {archi:2d}  p50 {lat[len(lat) // 2]:.0f} ms  "
              f"({time.perf_counter() - started:.0f} s)", flush=True)

    c, f = results["C corrente"], results["F campo reale"]
    broke = [i for i in range(len(queries)) if c[i]["valid"] and not f[i]["valid"]]
    healed = [i for i in range(len(queries)) if not c[i]["valid"] and f[i]["valid"]]
    print(f"\nF rispetto a C: rotte {len(broke)}  risanate {len(healed)}  "
          f"netto {len(healed) - len(broke):+d}")
    for i in broke:
        print(f"  - {f[i]['reason'][:32]:32s} {queries[i][:54]!r}")
    for i in healed:
        print(f"  + era {c[i]['reason'][:28]:28s} {queries[i][:54]!r}")

    print("\n=== motivi di caduta, prima e dopo")
    for tag in ("C corrente", "F campo reale"):
        conteggio: dict[str, int] = {}
        for row in results[tag]:
            if not row["valid"]:
                conteggio[row["reason"][:28]] = conteggio.get(row["reason"][:28], 0) + 1
        print(f"  {tag:14s} " + "  ".join(
            f"{k}={v}" for k, v in sorted(conteggio.items(), key=lambda kv: -kv[1])))

    (SCRATCH / "prova_arco.json").write_text(json.dumps({
        "queries": queries, "results": results,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\nscritto prova_arco.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
