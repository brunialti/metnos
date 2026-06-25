#!/usr/bin/env python3
"""intent_loop_driver.py — misura COMPLETA dello stato intent (hybrid scaffold).

Gira TUTTI i set su un flag-set dato e scrive un report JSON + tabella. Usato dal
loop iterate-test-fix (Roberto 24/6: «cicla fino a errore=0, fix sistemico»).

Set: CORE+EDGE (mono, 85), COMPOUND (24), COMPOUND_XL (6). K-stabilità sui compound.
Output: per ogni set accuracy + lista miss (per il fix mirato). Exit 0 se 0 miss.

Uso: METNOS_INTENT_BOUNDARIES=1 [METNOS_INTENT_SCAFFOLD=1] python3 bench/intent_loop_driver.py [K]
"""
from __future__ import annotations
import importlib.util, json, os, sys, collections
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "runtime"))


def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, str(path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def main():
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    here = Path(__file__).resolve().parent
    rsb = _load("rsb", here / "routing_subset_bench.py")
    fast, _ = rsb.build_calls()
    from intent_extractor import extract_intent

    iab = _load("iab", here / "intent_accuracy_bench.py")
    icb = _load("icb", here / "intent_compound_bench.py")

    flag_b = os.getenv("METNOS_INTENT_BOUNDARIES", "0")
    flag_s = os.getenv("METNOS_INTENT_SCAFFOLD", "0")
    report = {"boundaries": flag_b, "scaffold": flag_s, "sets": {}}

    # --- MONO: CORE+EDGE (1 run, deterministico abbastanza) ---
    mono_miss = []
    for label, cases in (("CORE", iab.GOLD), ("EDGE", iab.EDGE_GOLD)):
        for q, exp in cases:
            ir = extract_intent(q, fast) or {}
            got = f"{ir.get('verb')}/{ir.get('object')}"
            if got != exp:
                mono_miss.append({"set": label, "q": q, "exp": exp, "got": got})
    nmono = len(iab.GOLD) + len(iab.EDGE_GOLD)
    report["sets"]["mono"] = {"total": nmono, "miss": mono_miss}

    # --- COMPOUND + XL (K run, modale) ---
    def _astr(intent):
        if not intent: return "None"
        acts = intent.get("actions") or [{"verb": intent.get("verb"), "object": intent.get("object")}]
        return ",".join(f"{a.get('verb')}/{a.get('object')}" for a in acts)

    for label, cases in (("compound", icb.COMPOUND_GOLD), ("xl", icb.COMPOUND_XL_GOLD)):
        miss = []; flaky = 0; cl_ok = cl_tot = 0
        for q, exp in cases:
            expstr = ",".join(exp)
            runs = [_astr(extract_intent(q, fast)) for _ in range(K)]
            modal, cnt = collections.Counter(runs).most_common(1)[0]
            if cnt < K: flaky += 1
            got_cl = modal.split(",")
            for i, e in enumerate(exp):
                cl_tot += 1
                if i < len(got_cl) and got_cl[i] == e: cl_ok += 1
            if modal != expstr:
                miss.append({"q": q, "exp": expstr, "got": modal, "flaky": cnt < K})
        report["sets"][label] = {"total": len(cases), "miss": miss,
                                  "flaky": flaky, "clause_ok": cl_ok, "clause_tot": cl_tot}

    # --- riepilogo ---
    tot_miss = (len(mono_miss) + len(report["sets"]["compound"]["miss"])
                + len(report["sets"]["xl"]["miss"]))
    report["total_miss"] = tot_miss
    out = Path(os.getenv("INTENT_REPORT_OUT",
               "/tmp/claude-1000/-opt-metnos/aa51dedd-f07f-4c7d-aaee-130822475826/scratchpad/intent_report.json"))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"=== INTENT STATE (B={flag_b} S={flag_s}, K={K}) ===")
    print(f"MONO     : {nmono - len(mono_miss)}/{nmono} miss={len(mono_miss)}")
    c = report["sets"]["compound"]
    print(f"COMPOUND : {c['total']-len(c['miss'])}/{c['total']} exact, clausole {c['clause_ok']}/{c['clause_tot']}, flaky {c['flaky']}, miss={len(c['miss'])}")
    x = report["sets"]["xl"]
    print(f"XL       : {x['total']-len(x['miss'])}/{x['total']} exact, clausole {x['clause_ok']}/{x['clause_tot']}, flaky {x['flaky']}, miss={len(x['miss'])}")
    print(f"TOTAL MISS: {tot_miss}")
    if tot_miss:
        print("\n-- MISS DETTAGLIO --")
        for m in mono_miss:
            print(f"  [mono/{m['set']}] {m['got']:18} (exp {m['exp']:18}) | {m['q'][:55]}")
        for lab in ("compound", "xl"):
            for m in report["sets"][lab]["miss"]:
                fl = " ~flaky" if m.get("flaky") else ""
                print(f"  [{lab}]{fl}\n     exp {m['exp']}\n     got {m['got']}\n     Q: {m['q'][:70]}")
    return 1 if tot_miss else 0


if __name__ == "__main__":
    raise SystemExit(main())
