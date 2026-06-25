#!/usr/bin/env python3
"""routing_compound_bench.py — oracolo END-TO-END per compound: il dispatch routa
ai tool giusti? (NON match-esatto intent — l'intent è un hint semantico che il
pool risolve; vedi memoria intent-is-semantic-not-executor).

Due livelli (§7.4 — il proposer wise costa ~25s/query):
  POOL  : per ogni clausola, il tool-gold è NEL pool costruito da
          build_routing_pool? (deterministico, ~0.5s — usato nel LOOP).
  E2E   : la catena steps[].tool del framework proposto copre i tool-gold
          in ordine? (proposer wise, lento — validazione FINALE con --e2e).

Gold = TOOL REALI (verificati on-disk), uno o più ammessi per clausola (liste =
ambiguità legittima §routing_subset_bench). Env prod: METNOS_ENGINE=metis +
grammar+verb_filter (come routing_subset_bench).

Uso: METNOS_INTENT_BOUNDARIES=1 METNOS_INTENT_SCAFFOLD=1 METNOS_ENGINE=metis \
     python3 bench/routing_compound_bench.py [--e2e] [--runs N]
"""
from __future__ import annotations
import argparse, importlib.util, os, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "runtime"))
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")

# (query, [ tool-gold per clausola; ogni elemento str o lista di alternative ])
# I tool-gold sono REALI on-disk. Per le ops generiche su foto il tool è il
# *_files generico (compress_files, move_files), per send è send_messages, ecc.
GOLD = [
    ("leggi le mail di oggi e salvale in un file",
     ["read_messages", "write_files"]),
    ("trova i file .log in /tmp e comprimili in un archivio",
     ["find_files", "compress_files"]),
    ("scarica il json da https://httpbin.org/uuid e inviamelo via mail",
     [["read_urls", "read_urls_html", "get_urls"], "send_messages"]),
    ("trova i processi che consumano piu' memoria e scrivi un report",
     ["get_processes", "write_files"]),
    # describe_entries/classify_entries sono BUILTIN in-process (runtime/*.py,
    # LLM-augmented) iniettati dal dispatch a ESECUZIONE — MAI nel pool/catalog.
    # Il check giusto è che l'intent emetta la clausola (verb describe/classify);
    # marcati "INTENT" → verificati sull'intent, non sul pool.
    ("leggi le ultime mail e riassumile",
     ["read_messages", {"intent_verb": "describe"}]),
    ("trova le foto al mare e classificale per anno",
     ["find_images_indices", {"intent_verb": "classify"}]),
    ("trova i pdf in Documenti, comprimili e mandameli via mail",
     ["find_files", "compress_files", "send_messages"]),
    ("trova le foto del 2020, comprimile e inviamele",
     ["find_images_indices", "compress_files", "send_messages"]),
    ("elenca i file in /tmp, filtra quelli piu' grandi di 1MB e cancellali",
     [["find_files", "list_dirs"], "filter_entries", "delete_files"]),
    ("cerca le foto del compleanno, comprimile in un archivio e mandami l'album",
     ["find_images_indices", "compress_files", "send_messages"]),
]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true", help="anche la catena tool finale (lento)")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    rsb = _load("rsb", Path(__file__).parent / "routing_subset_bench.py")
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from engine.types import Intent
    from engine.routing_pool import build_routing_pool
    from intent_extractor import extract_intent
    cat = filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)
    fast, wise = rsb.build_calls()

    def _ok(gold_item, present, intent_verbs):
        # builtin in-process: il gold è {"intent_verb": "describe"} → verifica
        # che l'intent abbia quella clausola-verbo (il dispatch lo materializza).
        if isinstance(gold_item, dict):
            return gold_item.get("intent_verb") in intent_verbs
        alts = gold_item if isinstance(gold_item, list) else [gold_item]
        return any(a in present for a in alts)

    pool_miss, e2e_miss = [], []
    for q, gold in GOLD:
        ir = extract_intent(q, fast) or {}
        intent = Intent(verb=(ir.get("verb") or "").lower(),
                        object=(ir.get("object") or "").lower(),
                        keywords=list(ir.get("keywords") or []), confidence=1.0,
                        lang="it", actions=list(ir.get("actions") or []))
        pool = build_routing_pool(q, intent, cat)
        intent_verbs = {(a.get("verb") or "").lower()
                        for a in (ir.get("actions") or []) if isinstance(a, dict)}
        intent_verbs.add((ir.get("verb") or "").lower())
        # POOL recall: ogni clausola-gold ha un tool (o alt) nel pool (o, per i
        # builtin, l'intent ha la clausola-verbo)?
        missing = [g for g in gold if not _ok(g, pool, intent_verbs)]
        if missing:
            pool_miss.append((q, missing, pool[:14]))
        if args.e2e:
            import dataclasses
            from engine.proposer import get_proposer
            fw = get_proposer().propose(query=q, intent=intent, pool=pool,
                    excluded_hashes=set(), llm_call=wise, lang="it", catalog=cat)
            d = dataclasses.asdict(fw) if fw and dataclasses.is_dataclass(fw) else (fw or {})
            tools = [s.get("tool") for s in (d.get("steps") or [])]
            # ordine: ogni gold compare DOPO il precedente nella catena
            pos, ordered = -1, True
            for g in gold:
                alts = g if isinstance(g, list) else [g]
                idx = next((i for i, t in enumerate(tools) if t in alts and i > pos), None)
                if idx is None:
                    ordered = False; break
                pos = idx
            if not ordered:
                e2e_miss.append((q, gold, tools))

    n = len(GOLD)
    print(f"=== ROUTING COMPOUND (e2e={args.e2e}) ===")
    print(f"POOL recall: {n - len(pool_miss)}/{n}")
    for q, miss, pool in pool_miss:
        print(f"  MISS pool: {miss}\n     Q: {q[:60]}\n     pool: {pool}")
    if args.e2e:
        print(f"E2E chain : {n - len(e2e_miss)}/{n}")
        for q, gold, tools in e2e_miss:
            print(f"  MISS e2e: gold {gold}\n     got {tools}\n     Q: {q[:60]}")
    return 1 if (pool_miss or e2e_miss) else 0


if __name__ == "__main__":
    raise SystemExit(main())
