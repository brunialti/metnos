#!/usr/bin/env python3
"""routing_gate — gate di ROUTING per il livetest, separato dall'ESECUZIONE.

Il livetest (livetest.py) misura E2E: `step ok=False` fallisce anche quando il
routing e' CORRETTO ma un servizio esterno (github/mail) o una risorsa (indice
immagini) non e' disponibile nell'ambiente. Quel numero (esecuzione) NON e' un
gate valido per cambi di MANIFEST/proposer: quelli toccano solo la SCELTA dei
tool, non se github/mail funzionano.

Questo analizzatore riclassifica i `runs` di un round per ROUTING-correttezza:
il tool atteso (`expect_re`) e' fra i `tools` scelti? Confronta col baseline e
fallisce (exit 1) solo se il ROUTING regredisce. Nessun re-run: legge il DB.

Uso:  python3 tests/e2e/livetest/routing_gate.py --round <R> [--baseline <R0>]
"""
from __future__ import annotations
import argparse, re, sqlite3, sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "livetest.sqlite"


def routing_score(round_: str) -> tuple[int, int, list]:
    c = sqlite3.connect(str(DB))
    q = {r[0]: (r[1], r[2], r[3]) for r in
         c.execute("SELECT id,ord,text,expect_re FROM queries")}
    last = {}
    for qid, ok, tools in c.execute(
            "SELECT query_id,ok,tools FROM runs WHERE round=? ORDER BY id", (round_,)):
        last[qid] = (ok, tools or "")
    ok_r = tot = 0
    misroutes = []
    for qid, (ok, tools) in last.items():
        ordn, txt, expect = q.get(qid, (0, "", None))
        tot += 1
        tl = [t for t in tools.split(",") if t]
        r_ok = (not expect) or any(re.search(expect, t) for t in tl)
        ok_r += 1 if r_ok else 0
        if not r_ok:
            misroutes.append((ordn, expect, tools, (txt or "")[:45]))
    return ok_r, tot, sorted(misroutes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--baseline")
    a = ap.parse_args()
    ok_r, tot, mis = routing_score(a.round)
    pct = ok_r / tot if tot else 0
    print(f"ROUTING {a.round}: {ok_r}/{tot} = {pct:.0%}")
    for o, e, t, x in mis:
        print(f"  miss ord{o}: atteso /{e}/ usati=[{t}] | {x}")
    if a.baseline:
        b_ok, b_tot, _ = routing_score(a.baseline)
        b_pct = b_ok / b_tot if b_tot else 0
        print(f"BASELINE {a.baseline}: {b_ok}/{b_tot} = {b_pct:.0%}")
        if pct < b_pct - 1e-9:
            print(f"!!! REGRESSIONE ROUTING: {pct:.0%} < {b_pct:.0%}")
            sys.exit(1)
        print("routing: no regression.")


if __name__ == "__main__":
    main()
