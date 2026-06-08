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
    # --- locale vs web: pacchetto installato ---
    {"q": "è installato ffmpeg sul sistema?", "tool": "find_packages"},
    {"q": "controlla se il comando git è presente", "tool": "find_packages"},
    # --- file vs dir ---
    {"q": "cerca i file .pdf nella cartella Documenti", "tool": "find_files"},
    {"q": "elenca le sottocartelle di /home/user", "tool": "find_dirs"},
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
    """Pipeline completa → first_tool (o None)."""
    import prefilter, dataclasses
    from engine.types import Intent
    from engine.proposer import get_proposer
    from intent_extractor import extract_intent
    ir = extract_intent(query, fast) or {}
    verb, obj = (ir.get("verb") or ""), (ir.get("object") or "")
    intent = Intent(verb=verb, object=obj, lang="it")
    pool = None
    if verb or obj:
        pool = prefilter.rank_with_intent(query, cat, {"verb": verb, "object": obj}, k=10)
    if not pool:  # fallback come produzione: intent assente/vuoto
        pool = prefilter.rank(query, cat, k=10)
    pool = [p if isinstance(p, str) else getattr(p, "name", str(p)) for p in (pool or [])]
    fw = get_proposer().propose(query=query, intent=intent, pool=pool, excluded_hashes=set(),
                                llm_call=wise, lang="it", catalog=cat)
    d = dataclasses.asdict(fw) if fw and dataclasses.is_dataclass(fw) else (fw or {})
    steps = d.get("steps") or []
    return (steps[0].get("tool") if steps else None), {"verb": verb, "object": obj}


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
