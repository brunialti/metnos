#!/usr/bin/env python3
"""Bench A/B per lo spike CP5 grammar-on-args (ADR 0177 T2/M4). v2 6/7/2026.

Misura ISOLATA: chiama il PROPOSER DIRETTAMENTE (no run_turn → no esecuzione,
no describe/classify fan-out, no cache) su ogni query, in due modi:
  - A: METNOS_PROPOSER_GRAMMAR_ARGS=0 (args liberi)
  - B: METNOS_PROPOSER_GRAMMAR_ARGS=1 (args vincolati allo schema)
Poi applica i guard deterministici al framework GREZZO e conta i fire +
verifica la validità degli enum. Così isola l'effetto della grammar sugli
args generati dal proposer, senza i confondenti (cache/esecuzione/describe)
della v1.

Per ogni (query, mode): 1 call intent-extract (condivisa, fatta una volta) +
1 call proposer wise. ~15 query × 2 mode ≈ 30 proposer-call ≈ 3-5 min.

USO: METNOS_ENGINE=v3 python3 tests/benchmarks/grammar_args_ab.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

_RT = Path(__file__).resolve().parents[2] / "runtime"
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

# Corpus mirato agli args con ENUM + count + provider (dove la grammar-args
# agisce). NIENTE query che scatenano describe/classify su molte entries.
CORPUS = [
    "elenca la cartella /opt/metnos/internal ordinata per dimensione",
    "elenca /opt/metnos/decisions ordinata per data più recente",
    "trova i file .log in /tmp e comprimili in uno zip",
    "trova i file .txt in /tmp e comprimili in un tar",
    "mostrami i primi 3 file .md di /opt/metnos/internal/design",
    "elenca i primi 5 file in /opt/metnos/runtime",
    "leggi le mail di oggi e salvale in un file",
    "elenca i file in /tmp e filtra quelli più grandi di 1MB",
    "cerca su google drive il file KAKEBO e crea uno spreadsheet coi dati",
    "scrivi un file /tmp/nota_ab.txt con contenuto test",
    "trova i file .py in /opt/metnos/runtime ordinati per dimensione decrescente",
    "elenca i file della cartella /opt/metnos/executors",
    "leggi le ultime 10 mail",
    "comprimi la cartella /tmp/x in gz",
    "sposta i file .log da /tmp a /tmp/logs",
]


def _wise_call():
    from llm_router import LLMRouter

    def _call(sys_msg, user_msg, *, max_tokens=2048, think=True, **kw):
        # ``think`` remains in the benchmark callback signature for proposer
        # compatibility; generation policy belongs to the selected tier.
        grammar = kw.get("grammar")
        tier = kw.get("tier_override") or ("precise" if grammar else "wise")
        ck = {"max_tokens": max_tokens}
        if grammar:
            ck["grammar"] = grammar
        r = LLMRouter().provider(tier).chat(sys_msg, user_msg, **ck)
        return r.text if hasattr(r, "text") else str(r)
    return _call


def _run(queries, catalog, intents, grammar_args: bool) -> dict:
    os.environ["METNOS_GUARD_FIRE_COUNT"] = "1"
    os.environ["METNOS_PROPOSER_GRAMMAR"] = "1"
    os.environ["METNOS_PROPOSER_GRAMMAR_ARGS"] = "1" if grammar_args else "0"
    from engine.proposer import get_proposer
    from engine.routing_pool import build_routing_pool
    from engine import dispatch as D
    import arg_provenance as AP

    wise = _wise_call()
    prop = get_proposer()
    D.reset_guard_fire_counts()
    n_plans = 0
    n_enum_invalid = 0
    lats = []
    for q, intent in zip(queries, intents):
        if intent is None:
            continue
        pool = build_routing_pool(q, intent, catalog)
        t0 = time.time()
        try:
            fw = prop.propose(query=q, intent=intent, pool=pool,
                              excluded_hashes=set(), llm_call=wise,
                              lang="it", catalog=catalog)
        except Exception as ex:
            print(f"  [err propose] {q[:40]}: {type(ex).__name__}", file=sys.stderr)
            fw = None
        lats.append(time.time() - t0)
        if fw is None:
            continue
        n_plans += 1
        # verifica enum-validità sul framework GREZZO (pre-guard)
        for st in getattr(fw, "steps", []) or []:
            tool = getattr(st, "tool", "")
            ex = next((e for e in catalog if getattr(e, "name", "") == tool), None)
            if not ex:
                continue
            sch = getattr(ex, "args_schema", None) or {}
            props = sch.get("properties", {})
            for aname, aval in (getattr(st, "args", {}) or {}).items():
                decl = props.get(aname) or {}
                enum = decl.get("enum")
                if enum and aval is not None and aval not in enum:
                    n_enum_invalid += 1
        # applica i guard e conta i fire
        try:
            D._apply_deterministic_structure_guards(fw, intent, q, catalog)
        except Exception as ex:
            print(f"  [err guard] {q[:40]}: {type(ex).__name__}", file=sys.stderr)
    return {
        "grammar_args": grammar_args,
        "n": len(queries),
        "n_plans": n_plans,
        "n_enum_invalid": n_enum_invalid,
        "guard_fire_total": sum(D.guard_fire_counts().values()),
        "guard_fire_by": dict(sorted(D.guard_fire_counts().items(),
                                     key=lambda kv: -kv[1])),
        "latency_median_s": round(statistics.median(lats), 2) if lats else 0,
    }


def main():
    os.environ.setdefault("METNOS_ENGINE", "v3")
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from intent_extractor import extract_intent
    from engine.types import Intent

    catalog = filter_for_visibility(load_catalog(verify=True),
                                    VISIBILITY_COMPOSER)
    wise = _wise_call()

    print(f"=== BENCH A/B grammar-on-args v2 — {len(CORPUS)} query "
          f"(proposer diretto, no cache/exec) ===\n")
    print("Estrazione intent (una volta, condivisa)...")
    intents = []
    for q in CORPUS:
        try:
            d = extract_intent(q, wise)
            intents.append(Intent.from_extractor(d) if d and hasattr(Intent, "from_extractor")
                           else (_intent_from_dict(d) if d else None))
        except Exception as ex:
            print(f"  [err intent] {q[:40]}: {type(ex).__name__}", file=sys.stderr)
            intents.append(None)

    print("PASSATA A (grammar-args OFF)...")
    a = _run(CORPUS, catalog, intents, grammar_args=False)
    print("PASSATA B (grammar-args ON)...")
    b = _run(CORPUS, catalog, intents, grammar_args=True)

    print("\n=== RISULTATI ===")
    for label, r in (("A OFF", a), ("B ON ", b)):
        print(f"[{label}] plans={r['n_plans']}/{r['n']} "
              f"enum_invalid={r['n_enum_invalid']} "
              f"guard_fire={r['guard_fire_total']} lat_med={r['latency_median_s']}s")
    print("\nguard_fire per-guard:")
    print("  A OFF:", a["guard_fire_by"])
    print("  B ON :", b["guard_fire_by"])
    print(f"\nDELTA enum_invalid: {a['n_enum_invalid']} → {b['n_enum_invalid']} "
          f"({b['n_enum_invalid'] - a['n_enum_invalid']:+d})")
    print(f"DELTA guard_fire:   {a['guard_fire_total']} → {b['guard_fire_total']} "
          f"({b['guard_fire_total'] - a['guard_fire_total']:+d})")
    print("\nJSON:", json.dumps({"A": a, "B": b}, ensure_ascii=False))


def _intent_from_dict(d):
    from engine.types import Intent
    return Intent(verb=d.get("verb", ""), object=d.get("object", ""),
                  keywords=d.get("keywords", []) or [],
                  actions=d.get("actions", []) or [])


if __name__ == "__main__":
    main()
