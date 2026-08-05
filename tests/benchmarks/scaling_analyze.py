#!/usr/bin/env python3
"""Analisi post-hoc dello scaling bench — isola l'effetto di #azioni e #domini.

Legge i JSON salvati da compound_scaling_bench (--save) e produce:
  - heatmap accuracy (azioni × domini);
  - effetto MARGINALE di n_actions (media sui domini) e n_domains (media sulle azioni);
  - breakdown dei FALLIMENTI per modo (dropped clause / reorder / flaky);
  - frontiera del LIMITE (prima cella sotto soglia).

Run: python3 tests/benchmarks/scaling_analyze.py /tmp/scaling_v3.json [/tmp/scaling_metis.json]
"""
from __future__ import annotations
import json, sys
from collections import defaultdict


def load(path):
    return json.loads(open(path).read())


def fail_mode(r):
    if r["flaky"]:
        return "flaky"
    if r["anyo"] < r["gold_hard"]:
        return "dropped"
    if not r["in_order"]:
        return "reorder"
    return "ok"


def report(data, label):
    rows = data["rows"]
    print(f"\n########## {label}  (engine={data.get('engine')}) ##########")
    tot_ok, tot = data["total"]
    print(f"TOTALE: {tot_ok}/{tot} = {100*tot_ok/tot:.1f}%")

    # heatmap
    cells = defaultdict(lambda: [0, 0])
    for r in rows:
        cells[(r["a"], r["d"])][1] += 1
        cells[(r["a"], r["d"])][0] += int(r["ok"])
    amax = max(r["a"] for r in rows)
    dmax = max(r["d"] for r in rows)
    print("\nHEATMAP accuracy% (righe=azioni, colonne=domini):")
    print("a\\d " + "".join(f"{d:>6}" for d in range(1, dmax + 1)))
    for a in range(2, amax + 1):
        line = f"{a:>3} "
        for d in range(1, dmax + 1):
            if (a, d) in cells:
                c, t = cells[(a, d)]
                line += f"{int(100*c/t):>5}%"
            else:
                line += "     ·"
        print(line)

    # marginale per n_actions
    by_a = defaultdict(lambda: [0, 0])
    by_d = defaultdict(lambda: [0, 0])
    for r in rows:
        by_a[r["a"]][1] += 1; by_a[r["a"]][0] += int(r["ok"])
        by_d[r["d"]][1] += 1; by_d[r["d"]][0] += int(r["ok"])
    print("\nEFFETTO MARGINALE #AZIONI (media sui domini):")
    for a in sorted(by_a):
        c, t = by_a[a]; print(f"  a={a}: {100*c/t:5.0f}%  ({c}/{t})")
    print("EFFETTO MARGINALE #DOMINI (media sulle azioni):")
    for d in sorted(by_d):
        c, t = by_d[d]; print(f"  d={d}: {100*c/t:5.0f}%  ({c}/{t})")

    # breakdown fallimenti
    modes = defaultdict(int)
    for r in rows:
        modes[fail_mode(r)] += 1
    print("\nBREAKDOWN esiti:", dict(modes))
    fails = [r for r in rows if not r["ok"]]
    if fails:
        print(f"Primi 10 fallimenti:")
        for r in fails[:10]:
            print(f"  a={r['a']} d={r['d']} [{fail_mode(r)}] anyo={r['anyo']}/{r['gold_hard']} "
                  f"fwd={r['fwd']} plan={r['n_plan']}  {r['q'][:50]}")
            print(f"        tools: {r.get('tools','')[:96]}")

    # frontiera limite (prima a/d con accuracy <75%)
    print("\nFRONTIERA LIMITE (cella <75%):")
    for a in range(2, amax + 1):
        for d in range(1, dmax + 1):
            if (a, d) in cells:
                c, t = cells[(a, d)]
                if 100*c/t < 75:
                    print(f"  a={a} d={d}: {100*c/t:.0f}%")


def main():
    for path in sys.argv[1:]:
        report(load(path), path.split("/")[-1])


if __name__ == "__main__":
    main()
