#!/usr/bin/env python3
"""intent_compound_bench.py — intent su query COMPLESSE: multidominio, multi-azione.

Complementare a intent_accuracy_bench (mono-azione). Qui la query ha >=2 clausole
in sequenza → l'intent deve ritornare `actions=[{verb,object}, ...]` ORDINATO, con
l'OGGETTO REALE di OGNI clausola (no contaminazione fra clausole §11).

Roberto: «su query lunghe degrada». Output lungo (array JSON multi-clausola) =
zona flaky nota → ogni caso girato K volte, riportata accuratezza STABILE
(modale) + il tasso di flakiness. Confronto old (v3) vs new (v4 boundary).

Label = lista ordinata "verb/object". Asse ratificato (read=contenuto, get=
snapshot, find=discovery, ...). NO gaming §8.5.

Uso:  METNOS_INTENT_BOUNDARIES={0|1} python3 bench/intent_compound_bench.py [K]
"""
from __future__ import annotations

import collections
import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "runtime"))

# (query, [ "verb/object" ordinati per clausola ])
COMPOUND_GOLD = [
    ("leggi le mail di oggi e salvale in un file",
     ["read/messages", "write/files"]),
    ("trova i file .log in /tmp e comprimili in un archivio",
     ["find/files", "compress/files"]),
    ("scarica il json da https://httpbin.org/uuid e inviamelo via mail",
     ["read/urls", "send/messages"]),
    # get/processes: find_processes NON esiste (snapshot top=N = get_processes §2.2).
    ("trova i processi che consumano piu' memoria e scrivi un report",
     ["get/processes", "write/files"]),
    ("leggi le ultime mail e riassumile",
     ["read/messages", "describe/messages"]),
    ("cerca le fatture nelle mail e salvale nello store",
     ["find/messages", "write/entries"]),
    ("trova le foto al mare e classificale per anno",
     ["find/images", "classify/images"]),
    ("elenca i file in /tmp, filtra quelli piu' grandi di 1MB e cancellali",
     ["list/files", "filter/files", "delete/files"]),
    ("trova i pdf in Documenti, comprimili e mandameli via mail",
     ["find/files", "compress/files", "send/messages"]),
    ("leggi le mail di oggi e crea gli eventi corrispondenti in calendario",
     ["read/messages", "create/events"]),
    ("prendi i .txt di /tmp/test, filtra quelli vuoti, comprimi i restanti e cancella gli originali",
     ["find/files", "filter/files", "compress/files", "delete/files"]),
    ("leggi le mail di Anthropic, estrai gli importi e crea un foglio di calcolo",
     ["read/messages", "extract/entries", "create/files"]),
    ("find the photos from 2020, compress them and send them to me",
     ["find/images", "compress/files", "send/messages"]),
    ("read today's emails and save them to a file",
     ["read/messages", "write/files"]),
    ("download the report from https://x.com/r.pdf and email it",
     ["read/urls", "send/messages"]),
    ("cerca online cos'e' il QUIC e salva il riassunto in un file",
     ["find/urls", "write/files"]),
    # move/files: move_images NON esiste (spostamento = op generica §2.2 → files).
    ("trova le foto sfocate, spostale in /tmp/blur e mandami l'elenco",
     ["find/images", "move/files", "send/messages"]),
    ("leggi i task attivi, filtra quelli scaduti e cancellali",
     ["read/tasks", "filter/tasks", "delete/tasks"]),
]

# STRESS ESTREMO (Roberto 24/6): >=4 azioni E >=5 DOMINI interlacciati.
# GOLD CORRETTO 24/6 (audit logico+linguistico, verificato ON-DISK vs catalogo
# executor, §8.2 — NON spostato verso l'output modello): 11/27 clausole avevano
# combo verbo_oggetto INESISTENTI (write/images, get/images, find/images-literal,
# get/numbers, find/processes, create/files) → corrette a §2.2/§5 + catalogo:
#  - images su verbi GENERICI (write/get/compress/share) = `files` (§2.2: images
#    first-class solo per find-similar/read-OCR/describe/change/classify visuale).
#  - get/numbers NON esiste (numbers=compute-only) → «quanta RAM» folds in get_processes.health.
#  - find/processes NON esiste → get/processes (snapshot top=N).
#  - create/files NON esiste → create/files_spreadsheet («foglio»).
#  - persistenza «estrai…nello store» = SEMPRE +write/entries (uniforme caso 5; store_sink).
#  - allegato literal «allega logo.png» folds in send_messages(attachments=) → non azione propria.
COMPOUND_XL_GOLD = [
    # 5 segmenti → 5 clausole a livello INTENT (il fold dell'allegato in
    # send_messages(attachments=) è ESECUZIONE downstream, non intent). «allega
    # la foto logo.png» = get/files (path noto, snapshot per allegare).
    ("scarica il pdf da https://x.com/a.pdf, salvalo in /tmp, mandalo via mail a roberto, crea un evento promemoria domani e allega la foto logo.png",
     ["read/urls", "write/files", "send/messages", "create/events", "get/files"]),
    # 6 azioni: messages, entries(+persist), files_spreadsheet, contacts, events
    ("leggi le mail di fatturazione, estrai gli importi nello store, crea un foglio riepilogo, trova il contatto del fornitore e fissa un promemoria di pagamento",
     ["read/messages", "extract/entries", "write/entries", "create/files_spreadsheet", "find/contacts", "create/events"]),
    # 6 segmenti → 6 clausole a livello INTENT («quanta RAM resta» = get/processes,
    # lo stesso snapshot health; il fold con la clausola 1 è esecuzione downstream).
    ("trova i processi che usano piu' CPU, scrivi un report, mandamelo via mail, cerca online come ridurli, fissa un controllo fra un'ora e dimmi quanta RAM resta",
     ["get/processes", "write/files", "send/messages", "find/urls", "create/events", "get/processes"]),
    # 4 azioni, 5 domini: images(search), files, persons(biometric), messages
    ("cerca le foto del compleanno, comprimile in un archivio, identifica le persone nelle foto e mandami l'album per mail",
     ["find/images", "compress/files", "get/persons", "send/messages"]),
    # 5 azioni, 5 domini: urls, entries(+persist), messages, tasks — RIFERIMENTO CORRETTO
    ("cerca online le conferenze AI 2026, estrai nome e data, salvale nello store, mandami l'elenco e programma un task settimanale di aggiornamento",
     ["find/urls", "extract/entries", "write/entries", "send/messages", "create/tasks"]),
    # 4 azioni: messages, files(write-disk), files(EXIF-geo), files(share) — anafora pesante
    ("leggi le mail con allegati foto, salva le immagini in /tmp/foto, trova dove sono state scattate e condividile col contatto Lucia",
     ["read/messages", "write/files", "get/files", "share/files"]),
]


def _actions_str(intent) -> str:
    if not intent:
        return "None"
    acts = intent.get("actions")
    if not acts:
        acts = [{"verb": intent.get("verb"), "object": intent.get("object")}]
    return ",".join(f"{a.get('verb')}/{a.get('object')}" for a in acts)


def main() -> int:
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    flag = os.getenv("METNOS_INTENT_BOUNDARIES", "0")
    spec = importlib.util.spec_from_file_location(
        "rsb", str(Path(__file__).resolve().parent / "routing_subset_bench.py"))
    rsb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rsb)
    fast, _ = rsb.build_calls()
    from intent_extractor import extract_intent

    only_xl = "--xl" in sys.argv
    cases = COMPOUND_XL_GOLD if only_xl else (COMPOUND_GOLD + COMPOUND_XL_GOLD)
    print(f"=== intent COMPOUND (flag={flag}, K={K} run/caso, "
          f"{'XL-only' if only_xl else 'all'} = {len(cases)} casi) ===")
    exact = 0
    flaky = 0
    clause_ok = clause_tot = 0
    for q, exp in cases:
        expstr = ",".join(exp)
        runs = [_actions_str(extract_intent(q, fast)) for _ in range(K)]
        modal, cnt = collections.Counter(runs).most_common(1)[0]
        is_flaky = cnt < K
        if is_flaky:
            flaky += 1
        ok = modal == expstr
        if ok:
            exact += 1
        # accuratezza per-clausola sul run modale (degradazione parziale)
        got_cl = modal.split(",")
        for i, e in enumerate(exp):
            clause_tot += 1
            if i < len(got_cl) and got_cl[i] == e:
                clause_ok += 1
        mark = "OK " if ok else "XX "
        fl = " ~flaky" if is_flaky else ""
        print(f"  {mark}{q[:52]:52}")
        if not ok:
            print(f"      exp {expstr}")
            print(f"      got {modal}{fl}")
    n = len(cases)
    print(f"\nEXACT actions-list: {exact}/{n} = {100*exact/n:.1f}%")
    print(f"per-clausola:       {clause_ok}/{clause_tot} = {100*clause_ok/clause_tot:.1f}%")
    print(f"flaky (run discordi): {flaky}/{n}")
    return 1 if exact < n else 0


if __name__ == "__main__":
    raise SystemExit(main())
