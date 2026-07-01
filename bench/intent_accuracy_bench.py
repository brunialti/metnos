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
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "runtime"))

# GOLD (query → "verb/object" atteso). Verificato-corretto al 13/6/2026.
GOLD = [
    # --- enrollment / persons (residuo storico: object misclass mascherato) ---
    ("cancella l'enrollment di ospite alfa", "delete/persons"),
    ("cancella la registrazione biometrica di ospite beta", "delete/persons"),
    ("elimina l'iscrizione di ospite gamma", "delete/persons"),
    ("rimuovi ospite gamma dal registro volti", "delete/persons"),
    ("aggiungi ospite alfa al registro volti", "set/persons"),
    ("chi e' enrollato", "get/persons"),
    ("delete ospite alfa's enrollment", "delete/persons"),
    ("who is enrolled", "get/persons"),
    # --- persons (read/get) ---
    ("dimmi tutto su ospite alfa", "read/persons"),
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
    # ASSE RATIFICATO 24/6 (boundary read/get): fetch del CORPO/contenuto di
    # una URL (json, html, testo) = `read` (read_urls). `get` resta lo SNAPSHOT
    # di metadata/scalari, MAI il body. Il vecchio label "scarica→get/urls" era
    # sbagliato (§8.2: il test era errato, non il codice) — "scarica il json"
    # è fetch_content → read. Coerente col tool reale dominante (read_urls_html).
    ("controlla l'url https://x.com", "read/urls"),
    ("scarica il json da example.com", "read/urls"),
    # --- events / images / numbers / places / packages ---
    ("crea un evento domani alle 9", "create/events"),
    ("che impegni ho domani", "read/events"),
    ("cerca foto al mare", "find/images"),
    ("che ora e'", "get/numbers"),
    ("dove mi trovo", "get/places"),
    ("controlla se ffmpeg e' installato", "find/packages"),
]

# EDGE GOLD (24/6) — stress robustezza della boundary read/get/find/list/filter.
# Ogni label è verificato-corretto per l'ASSE RATIFICATO (read=CONTENUTO/body;
# get=SNAPSHOT/metadata; find=pattern/discovery; list=enum container; filter=
# riduci lista preesistente). NO gaming (§8.5): casi dove la doctrina è netta.
# Codifica anche le regressioni pescate dal replay (conta-file, riassumi→describe,
# url-content→read) così restano sorvegliate.
EDGE_GOLD = [
    # --- READ (contenuto) vs GET (metadata/snapshot): l'asse centrale ---
    ("leggi /etc/hosts", "read/files"),
    ("mostrami il contenuto di config.py", "read/files"),
    ("apri il file note.txt", "read/files"),
    ("dammi il contenuto di /var/log/syslog", "read/files"),
    ("voglio vedere cosa c'e' in log.txt", "read/files"),
    ("open the file readme.md", "read/files"),
    ("show me the contents of app.log", "read/files"),
    ("che dimensione ha config.py", "get/files"),
    ("quanto pesa il file backup.iso", "get/files"),
    ("quando e' stato modificato report.pdf", "get/files"),
    ("che permessi ha /etc/passwd", "get/files"),
    ("dammi le informazioni sul file fattura.pdf", "get/files"),
    ("how big is video.mp4", "get/files"),
    ("when was photo.jpg last modified", "get/files"),
    ("i metadati EXIF della foto IMG_001.jpg", "get/files"),
    # --- READ images = OCR testo grezzo (read, NON extract) ---
    ("leggi il testo dalla foto scan.png", "read/images"),
    ("read the text in the screenshot.png", "read/images"),
    # --- READ testo grezzo da pdf/html (read, NON extract) ---
    ("leggi il testo grezzo del pdf manuale.pdf", "read/files"),
    ("dammi il testo della pagina salvata index.html", "read/files"),
    # --- READ (grezzo) vs DESCRIBE (riassunto/aggregato) ---
    ("leggi le mail di oggi", "read/messages"),
    ("riassumi le mail di oggi", "describe/messages"),
    ("summarize my inbox", "describe/messages"),
    ("fammi un riassunto delle ultime email", "describe/messages"),
    ("sintetizza le mail della settimana", "describe/messages"),
    ("dammi il contenuto delle ultime mail", "read/messages"),
    ("riassumi le foto per anno e luogo", "describe/images"),
    # --- READ urls SOLO con url esplicito ---
    ("scarica il json da https://api.example.com/data", "read/urls"),
    ("leggi la pagina https://example.com", "read/urls"),
    ("dammi il contenuto di http://x.com/info.json", "read/urls"),
    ("fetch the json from https://httpbin.org/uuid", "read/urls"),
    ("scarica il report.pdf in /tmp/reports", "read/files"),
    ("leggi le note dal file appunti.txt", "read/files"),
    # --- FIND (pattern) vs GET (id noti) vs READ (id->contenuto) ---
    ("trova i file .py in /opt", "find/files"),
    ("cerca le foto al mare", "find/images"),
    ("find pdfs modified today", "find/files"),
    ("cerca i documenti che contengono fattura", "find/files"),
    ("leggi /opt/metnos/README.md", "read/files"),
    ("che ora e'", "get/numbers"),
    ("what time is it", "get/numbers"),
    # --- FIND (criterio) vs LIST (enumera container) ---
    ("elenca i file in /tmp", "list/files"),
    ("trova i .py in /tmp", "find/files"),
    ("elenca le sottocartelle di /opt", "list/dirs"),
    ("cosa c'e' nella cartella Downloads", "list/dirs"),
    ("list the IMAP folders", "list/messages"),
    ("trova le directory piu' grandi", "find/dirs"),
    ("quanti file ci sono in /opt", "find/files"),
    ("conta i file in /opt", "find/files"),
    ("how many files are in /opt", "find/files"),
    # --- LIST (nomi) vs READ (contenuto) ---
    ("elenca le folder IMAP", "list/messages"),
    ("leggi le mail nell'inbox", "read/messages"),
    # --- FILTER (riduce lista preesistente) ---
    ("scarta i file piu' piccoli di 1KB", "filter/files"),
    ("tieni solo le mail non lette", "filter/messages"),
    ("escludi i file temporanei", "filter/files"),
    ("keep only photos from 2024", "filter/images"),
    # --- GET snapshot/scalari ---
    # NB: get_dirs NON esiste come executor (lacuna catalogo); la dimensione di una
    # dir si ottiene via find_dirs (modo size). Gold = combo ROUTABLE on-disk §8.2.
    ("quanto e' grande la cartella Downloads", "find/dirs"),
    ("dimmi la mia posizione", "get/places"),
    ("arricchisci le foto con i dati EXIF", "get/files"),
    # --- FIND existence ---
    ("verifica che python3 sia presente", "find/packages"),
    ("is nginx running", "find/processes"),
    ("esiste il file /tmp/lock", "find/files"),
]


def _run_set(name, cases, extract_intent, fast, verbose=True):
    miss = []
    for q, exp in cases:
        ir = extract_intent(q, fast) or {}
        got = f"{ir.get('verb')}/{ir.get('object')}"
        if got != exp:
            miss.append((q, exp, got))
            if verbose:
                print(f"  XX {q[:50]:50} {got:18} (exp {exp})")
    n = len(cases)
    acc = n - len(miss)
    print(f"[{name}] ACCURACY: {acc}/{n} = {100*acc/n:.1f}%  (miss {len(miss)})")
    return miss


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "rsb", str(Path(__file__).resolve().parent / "routing_subset_bench.py"))
    rsb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rsb)
    fast, _ = rsb.build_calls()
    from intent_extractor import extract_intent

    flag = os.getenv("METNOS_INTENT_BOUNDARIES", "0")
    print(f"=== intent accuracy (METNOS_INTENT_BOUNDARIES={flag}) ===")
    miss_core = _run_set("CORE", GOLD, extract_intent, fast)
    print()
    miss_edge = _run_set("EDGE", EDGE_GOLD, extract_intent, fast)
    total = len(GOLD) + len(EDGE_GOLD)
    nmiss = len(miss_core) + len(miss_edge)
    print(f"\nTOTALE: {total - nmiss}/{total} = {100*(total-nmiss)/total:.1f}%  (miss {nmiss})")
    return 1 if nmiss else 0


if __name__ == "__main__":
    raise SystemExit(main())
