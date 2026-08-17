#!/usr/bin/env python3
"""Eighth arm: does the Metnos §6 prescriptive form pay on this block?

  E  incisive prose rewrite   -- 2392 char, domain heads, 15 explicit pairs
  H  §6 form                  -- 5389 char, 17 YOU MUST / YOU MUST NOT / OK / ERROR

Same content in both, verified adversarially. The house standard has never been
measured against an alternative on a real block; this turns an opinion into a
number. Back to back on the same 120 held-out queries. Read-only on production.
"""
from __future__ import annotations
import json, pathlib, sys, time
SCRATCH = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRATCH)); sys.path.insert(0, "/opt/metnos")
sys.path.insert(0, "/opt/metnos/runtime")
import ontologia_incisiva as OI, ontologia_sei as OS, prova_cieca as P


def costruisci(tag, modulo_testo):
    module = P.arm(tag, contract=True, drop_tie_break=True)
    modulo_testo.install(module)
    return module


def main() -> int:
    queries, groups = P.sample()
    print(f"forma §6 | {len(queries)} query mai usate | budget {P.BUDGET}")
    print(f"  E {len(OI.ONTOLOGIA_INCISIVA)} char | H {len(OS.ONTOLOGIA_SEI)} char\n", flush=True)
    arms = [("E incisiva", lambda: costruisci("e2", OI)),
            ("H forma §6", lambda: costruisci("h", OS))]
    results = {}
    for tag, build in arms:
        started = time.perf_counter()
        rows = P.run(build(), queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        print(f"{tag:12s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"p50 {lat[len(lat)//2]:.0f} ms  ({time.perf_counter()-started:.0f} s)", flush=True)
    e, h = results["E incisiva"], results["H forma §6"]
    broke = [i for i in range(len(queries)) if e[i]["valid"] and not h[i]["valid"]]
    healed = [i for i in range(len(queries)) if not e[i]["valid"] and h[i]["valid"]]
    print(f"\nH rispetto a E: rotte {len(broke)}  risanate {len(healed)}  netto {len(healed)-len(broke):+d}")
    for i in broke:
        print(f"  - {h[i]['reason'][:32]:32s} {queries[i][:54]!r}")
    for i in healed:
        print(f"  + era {e[i]['reason'][:28]:28s} {queries[i][:54]!r}")
    print("\n=== punti ciechi")
    for name, idx in groups.items():
        if idx:
            print(f"  {name:9s} ({len(idx):2d}): " + "  ".join(
                f"{t.split()[0]} {sum(1 for i in idx if results[t][i]['valid']):2d}" for t, _ in arms))
    (SCRATCH / "prova_sei.json").write_text(json.dumps(
        {"queries": queries, "results": results}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\nscritto prova_sei.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
