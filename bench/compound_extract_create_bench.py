#!/usr/bin/env python3
"""compound_extract_create_bench — banco RIPRODUCIBILE per la flakiness compound
sulla forma «PRODUTTORE → extract_entries → create_files_<formato>».

Bug live 21/6 (turni 0b9c2773/e6e4af72): «cerca le mail con le fatture, estrai
data e importo, crea un foglio» a volte pianifica il piano corretto
[read_messages → extract_entries → create_files_spreadsheet], a volte droppa il
create o lo trasforma in un read_messages spurio. NON un bug puntuale: instabilita'
del proposer sul compound a 3 clausole (territorio engine v3).

Questo banco PLANIFICA A SECCO (compound_dryrun.plan_only, zero side-effect) ogni
query N volte e misura:
  - se il piano contiene la sottosequenza ATTESA in ordine (read_<obj> →
    extract_entries → create_files_<fmt>);
  - STABILITA' fra i run (quante volte su N esce il piano corretto);
  - i FAILURE MODE reali (i piani sbagliati, per capire dove iterare).

Run:  METNOS_ENGINE=v3 python3 bench/compound_extract_create_bench.py [--runs N]
      [--save out.json]
La prossima sessione dedicata itera proposer/enforce finche' stabilita' = 100%.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "runtime"))
sys.path.insert(0, str(_ROOT / "bench"))

os.environ.setdefault("METNOS_ENGINE", "metis")
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_PREFILTER_RULES", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")
os.environ.setdefault("METNOS_LLM_SEED", "42")

# (query, [tool attesi in ORDINE: sottosequenza che il piano DEVE contenere]).
# Il produttore puo' essere read_<obj>/find_<obj>; tolleriamo entrambi via il
# set `_PRODUCER_OK`. extract_entries + create_files_<fmt> sono OBBLIGATORI.
_PRODUCER_OK = {"read_messages", "find_messages", "read_files", "find_files",
                "read_files_pdf", "read_events", "find_issues_github"}

CASES = [
    ("Cerca le email da Anthropic degli ultimi 12 mesi, estrai data e importo, e crea un foglio di calcolo.",
     ["<prod_messages>", "extract_entries", "create_files_spreadsheet"]),
    ("Leggi le mail con le fatture ricevute, estrai importo e data, e mettile in un foglio.",
     ["<prod_messages>", "extract_entries", "create_files_spreadsheet"]),
    ("Trova nelle mie email gli ordini, estrai numero e totale, e salvali in un csv.",
     ["<prod_messages>", "extract_entries", "create_files_spreadsheet"]),
    ("Cerca le email di conferma, estrai mittente e data, e crea un documento.",
     ["<prod_messages>", "extract_entries", "create_files_doc"]),
    ("Leggi i pdf nella cartella Documenti, estrai i totali, e crea un foglio di calcolo.",
     ["<prod_files>", "extract_entries", "create_files_spreadsheet"]),
    ("Trova i file di spesa, estrai importi e categorie, e mettili in uno spreadsheet.",
     ["<prod_files>", "extract_entries", "create_files_spreadsheet"]),
    ("Leggi gli eventi della settimana, estrai titolo e orario, e crea un foglio.",
     ["<prod_events>", "extract_entries", "create_files_spreadsheet"]),
    ("Cerca le issue aperte su github, estrai titolo e autore, e crea un documento.",
     ["<prod_issues>", "extract_entries", "create_files_doc"]),
]

_PROD_BY_OBJ = {
    "<prod_messages>": {"read_messages", "find_messages"},
    "<prod_files>": {"read_files", "find_files", "read_files_pdf"},
    "<prod_events>": {"read_events", "find_events_empty"},
    "<prod_issues>": {"find_issues_github", "read_issues"},
}


def _exec_tools(fw) -> list[str]:
    steps = getattr(fw, "steps", None) or []
    return [getattr(s, "tool", None) for s in steps
            if getattr(s, "tool", None) and getattr(s, "tool", "") != "final_answer"]


def _matches(expected: list[str], tools: list[str]) -> bool:
    """expected come sottosequenza ORDINATA di tools (produttore via set)."""
    i = 0
    for tok in tools:
        exp = expected[i]
        ok = (tok in _PROD_BY_OBJ[exp]) if exp.startswith("<prod_") else (tok == exp)
        if ok:
            i += 1
            if i == len(expected):
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--save", default="")
    args = ap.parse_args()

    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from routing_subset_bench import build_calls
    from compound_dryrun import plan_only
    cat = filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)
    fast, wise = build_calls()

    results = []
    n_stable = 0
    for q, expected in CASES:
        oks = 0
        plans: Counter = Counter()
        for _r in range(args.runs):
            try:
                fw, _intent, _actions, _pool = plan_only(q, cat, fast, wise)
                tools = _exec_tools(fw) if fw else []
            except Exception as e:  # noqa: BLE001
                tools = [f"<ERR:{type(e).__name__}>"]
            plans[" → ".join(tools)] += 1
            if _matches(expected, tools):
                oks += 1
        stable = oks == args.runs
        n_stable += int(stable)
        mark = "●" if stable else ("◑" if oks else "✗")
        print(f"{mark} {oks}/{args.runs}  {q[:58]}")
        print(f"      atteso: {' → '.join(expected)}")
        for plan, cnt in plans.most_common():
            print(f"      [{cnt}x] {plan or '(vuoto)'}")
        results.append({"query": q, "expected": expected, "ok": oks,
                        "runs": args.runs, "stable": stable,
                        "plans": dict(plans)})

    print(f"\nSTABILITA': {n_stable}/{len(CASES)} query stabili "
          f"(piano corretto in TUTTI i {args.runs} run)")
    if args.save:
        Path(args.save).write_text(json.dumps(
            {"runs": args.runs, "n_stable": n_stable, "total": len(CASES),
             "cases": results}, ensure_ascii=False, indent=1))
        print(f"salvato → {args.save}")


if __name__ == "__main__":
    main()
