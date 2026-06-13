#!/usr/bin/env python3
"""intent_accuracy_bench.py — accuratezza dell'intent-classifier (verb+object).

Complementare a `routing_subset_bench.py` (che misura il first_tool dopo
prefilter+proposer): qui si misura l'intent PURO (`extract_intent`), per
catturare le misclassificazioni a monte che il proposer oggi MASCHERA
(es. «cancella l'enrollment di X» → object=entries invece di persons).

Gold CURATO e verificato-corretto (§8.5, no gaming): famiglie/domini dove
nascono i misroute. Label = "verb/object" canonico §2.2.

Uso:
    python3 bench/intent_accuracy_bench.py          # report + exit!=0 su miss
Riusa `build_calls()` di routing_subset_bench per il provider fast (tier
dell'intent extractor).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "runtime"))

# GOLD (query → "verb/object" atteso). Verificato-corretto al 13/6/2026.
GOLD = [
    # --- enrollment / persons (residuo storico: object misclass mascherato) ---
    ("cancella l'enrollment di silvia", "delete/persons"),
    ("cancella la registrazione biometrica di marco", "delete/persons"),
    ("elimina l'iscrizione di lucia", "delete/persons"),
    ("rimuovi lucia dal registro volti", "delete/persons"),
    ("aggiungi silvia al registro volti", "set/persons"),
    ("chi e' enrollato", "get/persons"),
    ("delete silvia's enrollment", "delete/persons"),
    ("who is enrolled", "get/persons"),
    # --- persons (read/get) ---
    ("dimmi tutto su silvia", "read/persons"),
    ("chi sono io", "read/persons"),
    ("elenca le persone registrate", "get/persons"),
    # --- messages ---
    ("leggi le mail di oggi", "read/messages"),
    ("sposta in spam le mail di pubblicita", "move/messages"),
    ("trova le mail con oggetto fattura", "find/messages"),
    # --- files / dirs (confine list vs find, §2.2) ---
    ("elenca i file in /tmp", "list/files"),
    ("elenca le sottocartelle di /tmp", "list/dirs"),
    ("cancella la cartella build_old", "delete/dirs"),
    # --- urls (read vs get vs find) ---
    ("controlla l'url https://x.com", "read/urls"),
    ("scarica il json da example.com", "get/urls"),
    # --- events / images / numbers / places / packages ---
    ("crea un evento domani alle 9", "create/events"),
    ("che impegni ho domani", "read/events"),
    ("cerca foto al mare", "find/images"),
    ("che ora e'", "get/numbers"),
    ("dove mi trovo", "get/places"),
    ("controlla se ffmpeg e' installato", "find/packages"),
]


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "rsb", str(Path(__file__).resolve().parent / "routing_subset_bench.py"))
    rsb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rsb)
    fast, _ = rsb.build_calls()
    from intent_extractor import extract_intent

    miss = []
    for q, exp in GOLD:
        ir = extract_intent(q, fast) or {}
        got = f"{ir.get('verb')}/{ir.get('object')}"
        ok = got == exp
        if not ok:
            miss.append((q, exp, got))
        print(f"  {'OK ' if ok else 'XX '}{q[:46]:46} {got:18} (exp {exp})")
    n = len(GOLD)
    acc = n - len(miss)
    print(f"\nACCURACY: {acc}/{n} = {100*acc/n:.1f}%")
    if miss:
        print("MISS:")
        for q, e, g in miss:
            print(f"  - {q!r}: exp {e} got {g}")
        return 1
    print("no regression.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
