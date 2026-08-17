#!/usr/bin/env python3
"""Which of the 120 held-out queries does this component never see?

The Tutor is an escape BEFORE the engine (`http_routes_agent.py:1250`, "pure-help
escape before pending consumers"). A query it answers never reaches request
analysis, so scoring the analyzer on it measures nothing.

The classification is not a judgement of mine and not a list of phrases: every
turn record carries what production actually did with that exact query --
`tutor_esito`, `tutor_detection`, `mode`, `final_kind`. Read only.

Both numbers are always printed. Dropping a query silently to raise a score is
gaming the bench; separating a perimeter and saying so is not.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

SCRATCH = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRATCH))
sys.path.insert(0, "/opt/metnos")
sys.path.insert(0, "/opt/metnos/runtime")

import prova_cieca as P                                       # noqa: E402

TURNS = pathlib.Path.home() / ".local/share/metnos/turns"


def esiti_per_query() -> dict[str, list[dict]]:
    """Every recorded outcome, keyed by the exact query text."""
    per_query: dict[str, list[dict]] = collections.defaultdict(list)
    for f in sorted(TURNS.glob("*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            query = (record.get("user_query") or "").strip()
            if not query:
                continue
            per_query[query].append({
                "tutor_esito": record.get("tutor_esito"),
                "tutor_detection": record.get("tutor_detection"),
                "mode": record.get("mode"),
                "final_kind": record.get("final_kind"),
                "steps": len(record.get("steps") or []),
            })
    return per_query


def preso_dal_tutor(esiti: list[dict]) -> bool:
    """Production answered it with the Tutor, so the engine never routed it."""
    return any(e["tutor_esito"] in ("answered", "composed", "ok")
               or e["mode"] in ("EXPLAIN", "OBSERVE")
               for e in esiti)


def main() -> int:
    queries, _ = P.sample()
    per_query = esiti_per_query()

    print(f"perimetro | {len(queries)} query mai usate\n")
    tutor, motore, ignoto = [], [], []
    for i, query in enumerate(queries):
        esiti = per_query.get(query, [])
        if not esiti:
            ignoto.append(i)
        elif preso_dal_tutor(esiti):
            tutor.append(i)
        else:
            motore.append(i)
    print(f"  prese dal Tutor in produzione : {len(tutor):3d}")
    print(f"  arrivate al motore            : {len(motore):3d}")
    print(f"  senza esito registrato        : {len(ignoto):3d}")

    valori = collections.Counter(
        (e["tutor_esito"], e["mode"]) for esiti in per_query.values() for e in esiti)
    print("\n=== valori osservati (esito tutor, mode), tutto il corpus")
    for (esito, mode), n in valori.most_common(10):
        print(f"  {str(esito):18s} {str(mode):10s} {n:5d}")

    percorso = SCRATCH / "prova_cieca.json"
    if percorso.exists():
        dati = json.loads(percorso.read_text())
        print("\n=== le cadute della prova cieca, per perimetro")
        for tag, rows in dati["results"].items():
            caduti = [i for i, r in enumerate(rows) if not r["valid"]]
            dentro = [i for i in caduti if i in set(motore)]
            fuori = [i for i in caduti if i in set(tutor)]
            senza = [i for i in caduti if i in set(ignoto)]
            print(f"  {tag:22s} cadute {len(caduti):2d} = motore {len(dentro):2d} "
                  f"+ tutor {len(fuori):2d} + senza esito {len(senza):2d}")
        print("\n  dettaglio delle cadute fuori perimetro o senza esito:")
        rows = dati["results"]["A riferimento"]
        for i, r in enumerate(rows):
            if not r["valid"] and i not in set(motore):
                dove = "TUTOR" if i in set(tutor) else "senza esito"
                print(f"    {dove:11s} {r['reason'][:26]:26s} {queries[i][:52]!r}")

    print("\n=== query del campione prese dal Tutor")
    for i in tutor:
        esito = per_query[queries[i]][0]
        print(f"  [{i:3d}] esito={str(esito['tutor_esito']):10s} "
              f"mode={str(esito['mode']):8s} {queries[i][:58]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
