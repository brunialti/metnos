#!/usr/bin/env python3
"""Bench A/B per lo spike CP5 grammar-on-args (ADR 0177 T2/M4).

Confronta due passate sullo STESSO corpus di query reali:
  - A: METNOS_PROPOSER_GRAMMAR_ARGS=0 (baseline, args liberi)
  - B: METNOS_PROPOSER_GRAMMAR_ARGS=1 (args vincolati allo schema)
Con METNOS_GUARD_FIRE_COUNT=1 su entrambe.

Misura (per passata):
  - guard_fire totali e per-guard (dispatch): quanti guard hanno MUTATO il piano
  - parse-rate: % turni non-error (il proposer ha prodotto un piano eseguibile)
  - arg-errors: turni con error_code ERR_ARG_* (enum/tipo invalido → grammar li
    dovrebbe azzerare)
  - latenza mediana

USO: (girare col MODELLO locale attivo, engine v3)
  METNOS_ENGINE=v3 python3 bench/grammar_args_ab.py [--n 18]

Onestà (§8.3): il corpus è mirato agli args con ENUM (sort/op/compress/mode/
via_channel) + qualche compound, perché è LÌ che la grammar-args agisce. Sui
guard che fixano STRUTTURA o testo-libero (path/pattern/count) la grammar-args
NON aiuta per costruzione — il bench lo mostrerà onestamente.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent / "runtime"
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

# Corpus mirato: query che esercitano args con ENUM (dove la grammar-args
# agisce) + compound + casi che scatenano i guard args. Tool reali on-disk.
CORPUS = [
    # enum args diretti
    "elenca la cartella /opt/metnos/internal ordinata per dimensione",
    "elenca /opt/metnos/decisions ordinata per data più recente",
    "quanti file .md ci sono in /opt/metnos/decisions",
    "trova i file .log in /tmp e comprimili in uno zip",
    "trova i file .txt in /tmp e comprimili in un tar",
    # count/cap args
    "mostrami i primi 3 file .md di /opt/metnos/internal/design",
    "elenca i primi 5 file in /opt/metnos/runtime",
    # compound con args multipli
    "leggi le mail di oggi e salvale in un file",
    "trova i processi che consumano più memoria e scrivi un report",
    "elenca i file in /tmp, filtra quelli più grandi di 1MB",
    # provider/sink args (client)
    "cerca su google drive il file KAKEBO e crea uno spreadsheet coi dati",
    # read con formato
    "leggi il contenuto di /opt/metnos/README.md",
    "conta le righe di codice in /opt/metnos/runtime",
    # write mode
    "scrivi un file /tmp/nota_ab.txt con contenuto: test",
    # ordinamento entries
    "trova i file .py in /opt/metnos/runtime ordinati per dimensione decrescente",
    # find vs list (degenere)
    "elenca i file della cartella /opt/metnos/executors",
    # sort su processi
    "che processi girano sul server, i primi 5 per cpu",
    "trova le foto del 2020 in ~/.local/share/metnos/Immagini",
]


def _run_pass(queries, grammar_args: bool) -> dict:
    os.environ["METNOS_GUARD_FIRE_COUNT"] = "1"
    os.environ["METNOS_PROPOSER_GRAMMAR_ARGS"] = "1" if grammar_args else "0"
    # import DOPO aver settato l'env (alcuni moduli leggono a import-time)
    import agent_runtime
    from engine import dispatch as D

    D.reset_guard_fire_counts()
    n_ok = 0
    n_arg_err = 0
    lats = []
    for q in queries:
        t0 = time.time()
        try:
            log = agent_runtime.run_turn(q, actor="host", channel="http")
            kind = getattr(log, "final_kind", "")
            if kind == "answer":
                n_ok += 1
            # arg-error: uno step con error_code ERR_ARG_*
            for s in getattr(log, "steps", []) or []:
                res = getattr(s, "result", None)
                if isinstance(res, dict):
                    ec = str(res.get("error_code") or "")
                    if ec.startswith("ERR_ARG"):
                        n_arg_err += 1
                        break
        except Exception as ex:
            print(f"  [err] {q[:50]}: {type(ex).__name__}", file=sys.stderr)
        lats.append(time.time() - t0)
    return {
        "grammar_args": grammar_args,
        "n": len(queries),
        "n_answer": n_ok,
        "parse_rate": round(n_ok / max(1, len(queries)), 3),
        "n_arg_err": n_arg_err,
        "guard_fire_total": sum(D.guard_fire_counts().values()),
        "guard_fire_by": dict(sorted(D.guard_fire_counts().items(),
                                     key=lambda kv: -kv[1])),
        "latency_median_s": round(statistics.median(lats), 2) if lats else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=len(CORPUS),
                    help="numero di query dal corpus")
    args = ap.parse_args()
    queries = CORPUS[:args.n]

    print(f"=== BENCH A/B grammar-on-args — {len(queries)} query, engine={os.environ.get('METNOS_ENGINE')} ===\n")
    print("PASSATA A (grammar-args OFF, baseline)...")
    a = _run_pass(queries, grammar_args=False)
    print("PASSATA B (grammar-args ON)...")
    b = _run_pass(queries, grammar_args=True)

    print("\n=== RISULTATI ===")
    for label, r in (("A OFF", a), ("B ON ", b)):
        print(f"[{label}] parse_rate={r['parse_rate']} answer={r['n_answer']}/{r['n']} "
              f"arg_err={r['n_arg_err']} guard_fire={r['guard_fire_total']} "
              f"lat_med={r['latency_median_s']}s")
    print("\nguard_fire per-guard:")
    print("  A OFF:", a["guard_fire_by"])
    print("  B ON :", b["guard_fire_by"])
    print(f"\nDELTA guard_fire: {a['guard_fire_total']} → {b['guard_fire_total']} "
          f"({b['guard_fire_total'] - a['guard_fire_total']:+d})")
    print(f"DELTA arg_err:    {a['n_arg_err']} → {b['n_arg_err']} "
          f"({b['n_arg_err'] - a['n_arg_err']:+d})")
    print(f"DELTA parse_rate: {a['parse_rate']} → {b['parse_rate']}")
    import json
    print("\nJSON:", json.dumps({"A": a, "B": b}, ensure_ascii=False))


if __name__ == "__main__":
    main()
