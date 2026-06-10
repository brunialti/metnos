#!/usr/bin/env python3
"""Routing regression bench — SOTTOINSIEME executor confondibili (anti-regressione di massa).

A differenza di repro_prefilter_bench.py (che misura solo il RECALL del prefilter,
llm_call=None), questo bench esegue la DECISIONE COMPLETA:
    query -> intent_extract (fast) -> prefilter pool -> proposer (wise) -> first_tool
e confronta il first_tool con un gold CURATO e verificato-corretto.

Scopo: guardrail veloce da rieseguire PRIMA/DOPO ogni modifica a manifest, render
o thinking, per beccare i misroute (es. read_urls html->pdf del 7/6) senza dover
testare tutti i 96 executor.

Run:  python3 bench/routing_subset_bench.py [--runs N] [--baseline FILE] [--save FILE]
Exit code 1 se accuracy < baseline (regressione).
"""
from __future__ import annotations
import argparse, json, sys, os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "runtime"))

# Env di PRODUZIONE (drop-in proposer-hardening.conf, CLAUDE.md §11): il guard
# DEVE riflettere prod per costruzione, non per invocazione fortunata.
# setdefault: un env esplicito dell'utente (es. A/B di un flag) vince.
os.environ.setdefault("METNOS_ENGINE", "metis")
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_PREFILTER_RULES", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")

# Gold CURATO: famiglie confondibili dove nascono i misroute. Label = first_tool
# CORRETTO (verificato a mano), non "scelta storica di produzione".
GOLD = [
    # --- web: leggere URL HTML noto vs PDF vs ricerca vs GET grezzo ---
    {"q": "Controlla se è uscita la versione 8 di AMD ROCm su https://rocm.docs.amd.com/en/latest/release/versions.html", "tool": "read_urls_html"},
    {"q": "leggi questa pagina https://example.com/blog/post.html e riassumila", "tool": "read_urls_html"},
    {"q": "estrai il testo dal documento https://site.org/report-2026.pdf", "tool": "read_urls_pdf"},
    {"q": "scarica e leggi il PDF https://www.comune.it/circolare.pdf", "tool": "read_urls_pdf"},
    {"q": "cerca sul web le ultime notizie su AMD ROCm", "tool": "find_urls"},
    {"q": "cerca online cos'è AMD ROCm e dammi 3 fonti", "tool": "find_urls"},
    # Info-seeking generico SENZA marker web espliciti (misroute live 9/6/2026:
    # il pareggio find_* tagliava find_urls dal pool → vincevano find_issues/
    # find_places/find_files). "informazioni/novità su <tema>" senza repo/path
    # = ricerca WEB; il tema volutamente nome-proprio-like (l'intent può
    # classificare object=persons: il pool deve reggere comunque).
    {"q": "cerca informazioni su claude fable", "tool": "find_urls"},
    {"q": "cerca novità su claude fable e riassumile", "tool": "find_urls"},
    # --- locale vs web: pacchetto installato ---
    {"q": "è installato ffmpeg sul sistema?", "tool": "find_packages"},
    {"q": "controlla se il comando git è presente", "tool": "find_packages"},
    # --- file vs dir ---
    {"q": "cerca i file .pdf nella cartella Documenti", "tool": "find_files"},
    # AMBIGUO genuino (8/6/2026, decisione Roberto): "elenca le sottocartelle"
    # mappa ENTRAMBI — list_dirs (elenca il contenuto-dir di un livello) e
    # find_dirs (trova le subdir). L'intent-extractor classifica "elenca"=list,
    # il prefilter top-1=list_dirs: scelta corretta. Accettiamo i due tool reali
    # (NON è masking: sono entrambi giusti, non c'è un answer unico).
    {"q": "elenca le sottocartelle di /home/roberto", "tool": ["find_dirs", "list_dirs"]},
    {"q": "mostra tutto il contenuto della cartella Downloads", "tool": "list_dirs"},
    # --- file vs messaggi (move/delete) ---
    {"q": "sposta vecchio.txt nella cartella archivio", "tool": "move_files"},
    # mail-move è multi-step (§4.3: find/read spam -> move): accetta qualunque
    # tool del DOMINIO messaggi; il misroute che conta è move_files (dominio file).
    {"q": "sposta in Posta indesiderata le mail di spam", "tool": ["move_messages", "find_messages", "read_messages"]},
    {"q": "cancella il file /tmp/scratch.log", "tool": "delete_files"},
    {"q": "rimuovi la cartella /tmp/buildcache", "tool": "delete_dirs"},
    # --- mail ---
    {"q": "leggi le mail non lette di oggi", "tool": "read_messages"},
    {"q": "invia una mail a Mario con oggetto Promemoria", "tool": "send_messages"},
    # --- tempo / processi / posizione ---
    {"q": "che ore sono adesso?", "tool": "get_now"},
    {"q": "quali processi stanno consumando più CPU?", "tool": "get_processes"},
    # --- calendario ---
    {"q": "che impegni ho domani in calendario?", "tool": "read_events"},
    {"q": "trova le fasce libere nel mio calendario questa settimana", "tool": "find_events_empty"},
    # --- immagini ---
    {"q": "cerca foto di una persona col viso in primo piano", "tool": "find_images_indices"},
    # --- account/credenziali vs mail (misroute live 10/6/2026): "account mail"
    # → intent object=messages ("mail" domina su "account") → pool gated per
    # object escludeva sia find_credentials sia read_persons = RECALL miss →
    # read_messages leggeva 426 email. Fix: affinity_phrase_recall (tag
    # "quali account", curato in entrambi i manifest). DUE tool corretti
    # by-design (non masking): find_credentials elenca account/servizi con
    # credenziali (ADR 0123); read_persons(name=actor) è il bersaglio della
    # sez. I del prompt proposer shippato («quali account mail hai» →
    # profilo con mail_accounts). Il misroute che conta è read_messages.
    {"q": "quali account mail hai?", "tool": ["find_credentials", "read_persons"]},
    {"q": "quali account email ho configurato?", "tool": ["find_credentials", "read_persons"]},
]


def build_calls():
    from llm_router import LLMRouter
    r = LLMRouter()
    def fast(system, user, max_tokens=80, think=False):
        return getattr(r.provider("fast").chat(system, user, max_tokens=max_tokens, temperature=0, think=think), "text", "")
    def wise(system, user, *, max_tokens=2048, think=True, **kw):
        ck = {"max_tokens": max_tokens, "think": think}
        if kw.get("grammar") is not None: ck["grammar"] = kw["grammar"]
        if kw.get("reasoning_budget") is not None: ck["reasoning_budget"] = kw["reasoning_budget"]
        return (getattr(r.provider("wise").chat(system, user, **ck), "text", "") or "").strip()
    return fast, wise


def route(query, cat, fast, wise):
    """Pipeline completa → first_tool (o None).

    Fix B3 (9/6/2026): la costruzione-pool NON e' piu' re-implementata qui
    (vecchia copia: k=10 fisso, niente actions/compound, niente
    universal-helpers, niente companions) — si chiama
    `engine.routing_pool.build_routing_pool`, la STESSA funzione di
    produzione usata da `engine/dispatch.py::run_turn`. Cosi' il bench
    esercita il pool reale e becca le regressioni su quei layer.
    """
    import dataclasses
    from engine.types import Intent
    from engine.proposer import get_proposer
    from engine.routing_pool import build_routing_pool
    from intent_extractor import extract_intent
    ir = extract_intent(query, fast) or {}
    # Intent costruito COME in produzione (agent_runtime._try_engine_v2):
    # lowercase + keywords + actions (la decomposizione compound pilota
    # l'unione pool per-clausola dentro build_routing_pool).
    intent = Intent(
        verb=(ir.get("verb") or "").lower(),
        object=(ir.get("object") or "").lower(),
        keywords=list(ir.get("keywords") or []),
        confidence=float(ir.get("confidence") or 1.0),
        lang="it",
        actions=list(ir.get("actions") or []),
    )
    pool = build_routing_pool(query, intent, cat)
    fw = get_proposer().propose(query=query, intent=intent, pool=pool, excluded_hashes=set(),
                                llm_call=wise, lang="it", catalog=cat)
    d = dataclasses.asdict(fw) if fw and dataclasses.is_dataclass(fw) else (fw or {})
    steps = d.get("steps") or []
    return (steps[0].get("tool") if steps else None), {"verb": intent.verb, "object": intent.object}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="ripetizioni per query (flakiness)")
    ap.add_argument("--baseline", help="JSON baseline da confrontare")
    ap.add_argument("--save", help="salva i risultati come baseline JSON")
    args = ap.parse_args()

    from loader import load_catalog
    cat = load_catalog(verify=True)
    fast, wise = build_calls()

    rows, ok = [], 0
    for item in GOLD:
        picks = []
        for _ in range(args.runs):
            t, intent = route(item["q"], cat, fast, wise)
            picks.append(t)
        # PASS se TUTTI i run sono corretti (stringente, becca i flaky).
        # item["tool"] può essere str o lista di tool accettabili (multi-step).
        accept = item["tool"] if isinstance(item["tool"], list) else [item["tool"]]
        passed = all(p in accept for p in picks)
        ok += int(passed)
        flaky = len(set(picks)) > 1
        rows.append({"q": item["q"][:60], "exp": item["tool"], "got": picks,
                     "pass": passed, "flaky": flaky, "intent": intent})
        mark = "OK " if passed else "XX "
        fl = " (FLAKY)" if flaky else ""
        exp = item["tool"] if isinstance(item["tool"], str) else "|".join(item["tool"])
        print(f"{mark}{exp:22} <- {picks}{fl}  | {item['q'][:55]}")

    acc = ok / len(GOLD)
    print(f"\nACCURACY: {ok}/{len(GOLD)} = {acc:.1%}  (runs={args.runs})")

    if args.save:
        Path(args.save).write_text(json.dumps({"acc": acc, "ok": ok, "n": len(GOLD), "rows": rows}, ensure_ascii=False, indent=1))
        print(f"baseline salvata: {args.save}")

    if args.baseline and Path(args.baseline).exists():
        base = json.loads(Path(args.baseline).read_text())
        print(f"BASELINE: {base['ok']}/{base['n']} = {base['acc']:.1%}")
        if acc < base["acc"]:
            print(f"!!! REGRESSIONE: {acc:.1%} < {base['acc']:.1%}")
            sys.exit(1)
        print("no regression.")


if __name__ == "__main__":
    main()
