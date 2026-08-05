"""build_training_v2.py — Expand training dataset to 1000+ balanced samples.

Source: /opt/metnos/tests/simulator/test_set_FROZEN_v2.json + real_queries_v2.json
        + real_queries.json (FROZEN read-only).
Output: /tmp/training_dataset_v2.json + /tmp/dataset_v2_balance_report.md

Strategy (§7.3 deterministic):
  1. Extract existing pairs (read-only).
  2. For each tool in typing_cache (universe=82), ensure >=3 samples by
     adding deterministic templates IT+EN grounded in manifest description
     + affinity keywords.
  3. Augment all pairs with rule-based variations:
     - IT<->EN synonym swap (e.g. trova<->find, sposta<->move)
     - filler words (per favore / please)
     - cap/lowercase variation
  4. Output JSON with provenance flag.
"""
from __future__ import annotations
import json
import random
import sys
from pathlib import Path
from collections import defaultdict

SIM_DIR = Path("/opt/metnos/tests/simulator")
TYPING_DIR = SIM_DIR / "typing_cache"
EXECUTORS_DIR = Path("/opt/metnos/executors")
OUT_DATASET = Path("/tmp/training_dataset_v2.json")
OUT_REPORT = Path("/tmp/dataset_v2_balance_report.md")

# Sources (READ-ONLY)
DATA_SOURCES = ["test_set_FROZEN_v2.json", "real_queries_v2.json", "real_queries.json"]
_SKIP = {"request_new_executor"}

random.seed(42)


# ---------- Step 1: load existing ----------

def load_existing() -> list[dict]:
    """Extract dedup pairs from sources. Last wins (FROZEN_v2)."""
    acc: dict[str, str] = {}
    src_of: dict[str, str] = {}
    for src in reversed(DATA_SOURCES):
        p = SIM_DIR / src
        if not p.exists():
            continue
        tag = "frozen" if "FROZEN" in src else "real"
        for r in json.loads(p.read_text()):
            q = (r.get("query") or "").strip()
            ep = r.get("expected_path") or []
            if not q or not ep or not ep[0] or ep[0] in _SKIP:
                continue
            acc[q] = ep[0].lstrip("_")
            src_of[q] = tag
    return [{"query": q, "first_tool": t, "source": src_of[q]} for q, t in acc.items()]


# ---------- Step 2: typing universe ----------

def load_universe() -> list[str]:
    return sorted({p.stem.lstrip("_") for p in TYPING_DIR.glob("*.json")})


# ---------- Step 3: templated queries per tool ----------
# Each template is (it_query, en_query). Designed from manifest desc + affinity.
# §7.5: no proper names except "Roberto", "guest", "ospite".
# §2.2: queries respect canonical verb+object semantics.

TEMPLATES: dict[str, list[tuple[str, str]]] = {
    # ---- credentials ----
    "set_credentials": [
        ("configura le credenziali per gmail", "configure credentials for gmail"),
        ("salva il mio token github", "save my github token"),
        ("registra l'account google workspace", "register google workspace account"),
        ("imposta password per dominio example.com", "set password for domain example.com"),
        ("memorizza la chiave api openai", "store the openai api key"),
        ("connetti il mio account google", "connect my google account"),
        ("aggiungi credenziali smtp", "add smtp credentials"),
        ("salva token per migadu", "save token for migadu"),
    ],
    "delete_credentials": [
        ("dimentica le credenziali gmail", "forget gmail credentials"),
        ("rimuovi il token github", "remove github token"),
        ("revoca l'account google", "revoke google account"),
        ("cancella la chiave api openai", "delete openai api key"),
        ("elimina tutte le credenziali salvate", "delete all saved credentials"),
        ("disconnetti account google workspace", "disconnect google workspace account"),
        ("purge credenziali", "purge credentials"),
        ("rimuovi token per metnos.com", "remove token for metnos.com"),
    ],
    "find_credentials": [
        ("che credenziali ho configurate", "what credentials do i have configured"),
        ("mostrami i token salvati", "show me saved tokens"),
        ("quali account sono connessi", "which accounts are connected"),
        ("elenca le credenziali", "list credentials"),
        ("lista degli account configurati", "list configured accounts"),
        ("registro credenziali", "credentials registry"),
        ("mostra chiavi salvate", "show saved keys"),
    ],

    # ---- messages ----
    "set_messages": [
        ("metti l'etichetta Importante su queste mail", "add Important label to these emails"),
        ("aggiungi tag work alle mail di oggi", "add work tag to today's mails"),
        ("rimuovi l'etichetta spam da questo messaggio", "remove spam label from this message"),
        ("segna come letto questi messaggi", "mark these messages as read"),
        ("applica label progetto X alle mail", "apply label project X to mails"),
        ("etichetta come urgente", "label as urgent"),
        ("togli tag draft", "remove draft tag"),
    ],
    "move_messages": [
        ("cestina queste mail", "trash these emails"),
        ("sposta in archivio le mail di marzo", "move march mails to archive"),
        ("sposta in junk le newsletter", "move newsletters to junk"),
        ("metti in spam questo messaggio", "put this message in spam"),
        ("sposta in cartella lavoro le mail di knowcastle", "move knowcastle mails to work folder"),
        ("muovi le mail di bookings in archive", "move bookings mails to archive"),
        ("cestina la newsletter", "trash the newsletter"),
    ],
    "send_messages": [
        ("rispondi all'ultima mail di bookings", "reply to the last bookings email"),
        ("rispondi in thread a questo messaggio", "reply in thread to this message"),
        ("scrivi una risposta alla mail di ieri", "write a reply to yesterday's email"),
        ("rispondi con 'sono d'accordo'", "reply with 'i agree'"),
        ("componi una risposta automatica", "compose an automatic reply"),
        ("rispondi a tutti", "reply to all"),
    ],

    # ---- files: doc / spreadsheet / docs / ocr ----
    "create_files_doc": [
        ("crea un nuovo google doc", "create a new google doc"),
        ("crea documento online intitolato Verbale", "create online document titled Minutes"),
        ("nuovo google doc per il progetto", "new google doc for the project"),
        ("apri un doc vuoto in drive", "open a blank doc in drive"),
        ("crea documento condiviso", "create shared document"),
    ],
    "create_files_spreadsheet": [
        ("crea un foglio di calcolo con google", "create a spreadsheet with google"),
        ("nuovo google sheets per i conti", "new google sheets for accounts"),
        ("crea foglio elettronico vuoto", "create empty spreadsheet"),
        ("apri un nuovo google sheet", "open a new google sheet"),
        ("crea spreadsheet intitolato Budget", "create spreadsheet titled Budget"),
    ],
    "write_files_doc": [
        ("appendi questo testo al doc", "append this text to the doc"),
        ("aggiungi al google doc la frase Conclusioni", "add the phrase Conclusions to the google doc"),
        ("scrivi nel doc il riassunto", "write the summary into the doc"),
        ("appendi un paragrafo al documento", "append a paragraph to the document"),
        ("aggiorna il google doc con queste note", "update the google doc with these notes"),
    ],
    "write_files_spreadsheet": [
        ("scrivi celle nel google sheet", "write cells in the google sheet"),
        ("aggiorna il range A1:C10 nel foglio", "update range A1:C10 in the sheet"),
        ("appendi una riga al spreadsheet", "append a row to the spreadsheet"),
        ("inserisci i dati nel google sheets", "insert data into google sheets"),
        ("scrivi nel foglio elettronico", "write to the spreadsheet"),
    ],
    "read_files_doc": [
        ("leggi il google doc", "read the google doc"),
        ("apri e mostra il documento online", "open and show the online document"),
        ("leggi il contenuto del doc", "read the doc content"),
        ("estrai testo dal google doc", "extract text from google doc"),
        ("mostrami il documento condiviso", "show me the shared document"),
    ],
    "read_files_spreadsheet": [
        ("leggi le celle del google sheet", "read cells from the google sheet"),
        ("apri il foglio e mostra il range A1:C10", "open the sheet and show range A1:C10"),
        ("leggi il foglio elettronico", "read the spreadsheet"),
        ("mostra i dati del google sheets", "show data from google sheets"),
        ("estrai righe dal foglio", "extract rows from the sheet"),
    ],
    "read_files_xlsx": [
        ("leggi il file excel /tmp/data.xlsx", "read the excel file /tmp/data.xlsx"),
        ("apri il file xlsx e mostra le righe", "open the xlsx and show the rows"),
        ("leggi tabella excel /home/user/budget.xlsx", "read excel table /home/user/budget.xlsx"),
        ("estrai dati da spreadsheet xlsx", "extract data from xlsx spreadsheet"),
        ("mostra contenuto file excel", "show content of excel file"),
    ],
    "read_files_ocr": [
        ("estrai testo via ocr da /tmp/scan.pdf", "extract text via ocr from /tmp/scan.pdf"),
        ("ocr sull'immagine /tmp/foto.jpg", "ocr on the image /tmp/foto.jpg"),
        ("leggi testo dalla scansione", "read text from the scan"),
        ("trascrivi testo da pdf scansionato", "transcribe text from scanned pdf"),
        ("riconosci testo nell'immagine", "recognize text in the image"),
    ],
    "delete_files": [
        ("elimina il file /tmp/old.log", "delete file /tmp/old.log"),
        ("cancella /tmp/test.txt", "delete /tmp/test.txt"),
        ("rimuovi i file /tmp/a.txt e /tmp/b.txt", "remove files /tmp/a.txt and /tmp/b.txt"),
        ("delete /home/user/draft.md", "delete /home/user/draft.md"),
        ("cancella i file backup vecchi", "delete old backup files"),
    ],

    # ---- dirs ----
    "delete_dirs": [
        ("elimina cartella /tmp/oldproject", "delete folder /tmp/oldproject"),
        ("rimuovi la directory /tmp/cache", "remove directory /tmp/cache"),
        ("cancella la cartella /tmp/build", "delete folder /tmp/build"),
        ("rmdir /tmp/empty_dir", "rmdir /tmp/empty_dir"),
        ("rimuovi ricorsivamente /tmp/junk", "recursively remove /tmp/junk"),
    ],

    # ---- file dates / metadata ----
    "get_file_dates": [
        ("data exif di /home/user/foto.jpg", "exif date of /home/user/foto.jpg"),
        ("arricchisci con data di scatto le foto", "enrich photos with shot date"),
        ("data sensata dei file in /tmp/img", "semantic date of files in /tmp/img"),
        ("estrai exif date dalle immagini", "extract exif date from images"),
        ("metadati data creazione foto", "creation date metadata of photos"),
    ],

    # ---- packages ----
    "find_packages": [
        ("verifica se ffmpeg e installato", "check if ffmpeg is installed"),
        ("e installato curl", "is curl installed"),
        ("trova il pacchetto python3", "find the python3 package"),
        ("dove sta il comando git", "where is the git command"),
        ("cerca eseguibile node", "search for node executable"),
        ("which python", "which python"),
    ],

    # ---- places ----
    "find_places": [
        ("trova 5 ristoranti vicino a me", "find 5 restaurants near me"),
        ("cerca farmacia aperta ora", "search pharmacy open now"),
        ("dove c'e' un parcheggio", "where is a parking lot"),
        ("trova bar in centro", "find a bar downtown"),
        ("cerca supermercato vicino", "search nearby supermarket"),
        ("trovami una stazione di servizio", "find me a gas station"),
    ],
    "get_places": [
        ("che citta e' alle coordinate 41.9 12.5", "what city is at coordinates 41.9 12.5"),
        ("reverse geocoding di 45.46 9.19", "reverse geocoding of 45.46 9.19"),
        ("nome del luogo alle coordinate", "place name at the coordinates"),
        ("indirizzo per latitudine 40.7 longitudine -74.0", "address for latitude 40.7 longitude -74.0"),
        ("che posto e' a queste coordinate gps", "what place is at these gps coordinates"),
    ],

    # ---- contacts ----
    "find_contacts": [
        ("trova il contatto di Roberto", "find Roberto's contact"),
        ("cerca numero di telefono di guest", "search guest's phone number"),
        ("cerca email del contatto ospite", "search email of guest contact"),
        ("contatti con cognome simile a brun", "contacts with surname similar to brun"),
        ("trova in rubrica un guest", "find a guest in the address book"),
    ],
    "read_contacts": [
        ("leggi i contatti", "read contacts"),
        ("mostrami la lista dei contatti", "show me the contacts list"),
        ("scarica tutti i contatti", "download all contacts"),
        ("apri rubrica completa", "open full address book"),
        ("estrai contatti da google", "extract contacts from google"),
    ],

    # ---- persons ----
    "set_persons": [
        ("questa e' Silvia", "this is Silvia"),
        ("enrolla Roberto", "enroll Roberto"),
        ("registra la guest come ospite frequente", "register the guest as frequent visitor"),
        ("ricorda questa persona come Matteo", "remember this person as Matteo"),
        ("aggiungi persona al registro", "add person to the registry"),
        ("memorizza il volto come ospite", "store this face as guest"),
    ],
    "delete_persons": [
        ("dimentica Silvia", "forget Silvia"),
        ("rimuovi Matteo dal registro", "remove Matteo from registry"),
        ("cancella tutte le persone", "delete all persons"),
        ("elimina persona ospite", "delete guest person"),
        ("unenroll Roberto", "unenroll Roberto"),
        ("purge persone", "purge persons"),
    ],

    # ---- signatures (safety) ----
    "set_signatures": [
        ("autorizza il comando ffmpeg", "authorize ffmpeg command"),
        ("vieta il comando rm -rf", "forbid rm -rf command"),
        ("aggiungi alla blacklist questo hash", "add this hash to blacklist"),
        ("permetti il comando wget", "allow wget command"),
        ("blocca il binario sospetto", "block suspicious binary"),
        ("rimuovi dalla whitelist", "remove from whitelist"),
    ],
    "get_signatures": [
        ("verifica permessi del comando curl", "check permissions for curl command"),
        ("elenco comandi autorizzati", "list of authorized commands"),
        ("elenco comandi vietati", "list of forbidden commands"),
        ("lista signature whitelist", "whitelist signatures list"),
        ("mostra blacklist sicurezza", "show security blacklist"),
        ("review pending signatures", "review pending signatures"),
    ],

    # ---- compute ----
    "compute_signatures": [
        ("calcola sha256 di /tmp/test_sha.txt", "compute sha256 of /tmp/test_sha.txt"),
        ("checksum sha256 di /tmp/a.iso", "sha256 checksum of /tmp/a.iso"),
        ("verifica firma di /tmp/release.tar.gz", "verify signature of /tmp/release.tar.gz"),
        ("canonicalizza il comando ffmpeg", "canonicalize the ffmpeg command"),
        ("hash sha del file /tmp/x.bin", "sha hash of file /tmp/x.bin"),
    ],
    "compute_files_loc": [
        ("quante righe di codice in /opt/metnos/runtime", "how many lines of code in /opt/metnos/runtime"),
        ("conta linee di /tmp/data.py", "count lines of /tmp/data.py"),
        ("loc del progetto in /opt/metnos", "loc of project in /opt/metnos"),
        ("linee di codice python in src", "python lines of code in src"),
        ("quanti loc nel repository", "how many loc in the repository"),
    ],
    "compute_entries": [
        ("media dei file size in entries", "average of file sizes in entries"),
        ("somma dei byte dei file trovati", "sum of bytes of found files"),
        ("massimo della colonna age", "max of age column"),
        ("count delle entries trovate", "count of found entries"),
        ("calcola media della key size", "compute average of size key"),
    ],
    "describe_numbers": [
        ("media tra 10 20 30 40 e 50", "average of 10 20 30 40 and 50"),
        ("media e mediana di 12 18 25 30 7 14", "mean and median of 12 18 25 30 7 14"),
        ("somma 5 7 11", "sum 5 7 11"),
        ("max di 100 200 300", "max of 100 200 300"),
        ("statistiche dei numeri 3 5 8 13", "stats of numbers 3 5 8 13"),
    ],

    # ---- conversion ----
    "change_files_format": [
        ("converti /tmp/test_input.jpg in formato png", "convert /tmp/test_input.jpg to png format"),
        ("converti /home/x.png in jpg", "convert /home/x.png to jpg"),
        ("converti video.mp4 in webm", "convert video.mp4 to webm"),
        ("estrai audio mp3 dal video", "extract mp3 audio from the video"),
        ("transcodifica video in formato webm", "transcode video to webm format"),
    ],

    # ---- create dirs ----
    "create_dirs": [
        ("crea la directory /tmp/uc_test_dir", "create directory /tmp/uc_test_dir"),
        ("crea la cartella /tmp/metnos_bench_test", "create folder /tmp/metnos_bench_test"),
        ("mkdir /tmp/new_folder", "mkdir /tmp/new_folder"),
        ("crea cartella backup", "create backup folder"),
        ("nuova directory in /home/roberto", "new directory in /home/roberto"),
    ],

    # ---- tasks ----
    "set_tasks": [
        ("modifica il task riassunto_mail quotidiano alle 9", "edit task daily mail summary to 9 am"),
        ("aggiorna l'orario del task", "update task time"),
        ("disabilita il task ricorrente", "disable the recurring task"),
        ("rinomina il task in priorita_alta", "rename the task to high_priority"),
        ("cambia frequenza del task scheduler", "change frequency of scheduler task"),
    ],
    "delete_tasks": [
        ("cancella il task ricorrente test_regressione", "delete recurring task test_regressione"),
        ("rimuovi promemoria daily_summary", "remove daily_summary reminder"),
        ("elimina il task schedulato", "delete the scheduled task"),
        ("cancella tutti i task scaduti", "delete all expired tasks"),
        ("rimuovi task ricorrente", "remove recurring task"),
    ],
    "read_tasks": [
        ("mostra dettagli del task riassunto_mail", "show details of task riassunto_mail"),
        ("apri il task riassunto", "open the summary task"),
        ("leggi configurazione del task", "read task configuration"),
        ("dettagli task ricorrente", "recurring task details"),
        ("config del task daily", "config of daily task"),
    ],

    # ---- events ----
    "delete_events": [
        ("cancella l'appuntamento di domani", "cancel tomorrow's appointment"),
        ("elimina evento riunione settimanale", "delete weekly meeting event"),
        ("rimuovi il meeting di lunedi", "remove monday's meeting"),
        ("cancella tutti gli eventi della settimana", "cancel all events this week"),
        ("delete event id ev123", "delete event id ev123"),
    ],

    # ---- file sharing ----
    "share_files": [
        ("condividi /home/user/x.pdf con guest@example.com", "share /home/user/x.pdf with guest@example.com"),
        ("dai accesso in lettura al file", "grant read access to the file"),
        ("condividi il documento con il guest", "share the document with the guest"),
        ("permission grant sul file", "permission grant on the file"),
        ("invia link condiviso del pdf", "send shared link of the pdf"),
    ],

    # ---- web / urls ----
    "read_urls_pdf": [
        ("scarica e leggi questo pdf https://example.com/doc.pdf", "download and read this pdf https://example.com/doc.pdf"),
        ("estrai testo da pdf online", "extract text from online pdf"),
        ("leggi la circolare pdf", "read the pdf circular"),
        ("scarica e parsa il report pdf", "download and parse the pdf report"),
        ("leggi pdf via url", "read pdf via url"),
    ],
    "login_urls": [
        ("fai il login al sito example.com", "log in to site example.com"),
        ("autenticati al portale", "authenticate to the portal"),
        ("inizia sessione web sul dominio", "start web session on the domain"),
        ("login con credenziali salvate", "login with saved credentials"),
        ("apri sessione autenticata", "open authenticated session"),
    ],

    # ---- images ----
    "create_images_indices": [
        ("indicizza la cartella foto", "index the photos folder"),
        ("crea indice immagini in /home/user/Immagini", "build images index in /home/user/Immagini"),
        ("rebuild indice scene", "rebuild scene index"),
        ("indicizza le foto del corpus", "index the corpus photos"),
        ("ricostruisci indice persone", "rebuild persons index"),
    ],
    "delete_images_indices": [
        ("cancella indice immagini in /home/user/Immagini", "delete images index in /home/user/Immagini"),
        ("rimuovi indice scene", "remove scene index"),
        ("elimina indice persons", "delete persons index"),
        ("free storage indice gps", "free storage gps index"),
        ("rebuild from scratch indice immagini", "rebuild from scratch images index"),
    ],
    "get_images_indices": [
        ("status indice immagini", "image index status"),
        ("info sull'indice scene", "info on scene index"),
        ("quante foto indicizzate", "how many indexed photos"),
        ("snapshot indici immagini", "snapshot of image indices"),
        ("stato indice persons in Immagini", "persons index status in Immagini"),
    ],

    # ---- helpers / builtins ----
    "classify_entries": [
        ("classifica queste entries per categoria", "classify these entries by category"),
        ("etichetta semanticamente i file trovati", "semantically label found files"),
        ("classifica i messaggi per topic", "classify messages by topic"),
        ("raggruppa per categoria via llm", "group by category via llm"),
        ("classify entries per importanza", "classify entries by importance"),
    ],
    "filter_entries": [
        ("filtra solo quelle con size > 1MB", "filter only those with size > 1MB"),
        ("filtra entries con extension pdf", "filter entries with extension pdf"),
        ("tieni solo i file modificati oggi", "keep only files modified today"),
        ("subset where name like report", "subset where name like report"),
        ("filter entries per anno 2024", "filter entries by year 2024"),
    ],
    "filter_lists": [
        ("intersezione tra le due liste", "intersection of the two lists"),
        ("differenza fra step1 e step2", "difference between step1 and step2"),
        ("union delle liste", "union of the lists"),
        ("trova overlap fra entries", "find overlap between entries"),
        ("symdiff fra liste di file", "symdiff between file lists"),
    ],
    "filter_texts_lines": [
        ("estrai righe che contengono ERROR", "extract lines containing ERROR"),
        ("filtra le righe con regex", "filter lines matching regex"),
        ("grep delle righe con WARN", "grep lines with WARN"),
        ("solo righe che iniziano con 2026", "only lines starting with 2026"),
        ("filtra le linee del log per errore", "filter log lines for error"),
    ],
    "group_entries": [
        ("unisci queste liste in una", "merge these lists into one"),
        ("raggruppa entries di step1 e step2", "group entries from step1 and step2"),
        ("dedup delle entries", "dedup of entries"),
        ("merge dei risultati", "merge results"),
        ("concatena le liste deduplicate", "concatenate the lists deduplicated"),
    ],
    "sort_entries": [
        ("ordina per size desc", "sort by size desc"),
        ("ordina i file per data di modifica", "sort files by modified date"),
        ("classifica per name asc", "rank by name asc"),
        ("top 5 per byte_size", "top 5 by byte_size"),
        ("ordina per priority crescente", "sort by priority ascending"),
    ],
    "describe_entries": [
        ("riassumi i risultati trovati", "summarize the found results"),
        ("descrivi questa lista di mail", "describe this list of emails"),
        ("riassumi le ultime novita di anthropic", "summarize the latest anthropic news"),
        ("descrivi i file trovati", "describe the found files"),
        ("dimmi cosa contengono queste entries", "tell me what these entries contain"),
    ],
    "final_answer": [
        ("rispondi in linguaggio naturale", "answer in natural language"),
        ("dimmi ciao", "say hi"),
        ("conferma operazione completata", "confirm operation completed"),
        ("ok grazie", "ok thanks"),
        ("hai capito", "got it"),
    ],
    "undo_last_turn": [
        ("annulla l'ultima operazione", "undo last operation"),
        ("rollback dell'ultimo turno", "rollback last turn"),
        ("ripristina lo stato precedente", "restore previous state"),
        ("undo last", "undo last"),
        ("torna indietro", "go back"),
    ],
    "consult_frontier": [
        ("chiedi al frontier opus questo problema", "ask frontier opus this problem"),
        ("consulta sonnet per il review", "consult sonnet for the review"),
        ("delega a opus questo ragionamento complesso", "delegate this complex reasoning to opus"),
        ("ask the frontier llm", "ask the frontier llm"),
        ("usa l'oracolo frontier per analizzare", "use the frontier oracle to analyze"),
    ],
}


# ---------- Step 4: augmentation patterns ----------

IT_VERB_SYN = {
    "trova": ["cerca", "cercami", "trovami"],
    "cerca": ["trova", "cercami"],
    "elimina": ["cancella", "rimuovi"],
    "cancella": ["elimina", "rimuovi"],
    "rimuovi": ["elimina", "cancella"],
    "crea": ["genera", "fai"],
    "leggi": ["mostra", "apri"],
    "mostra": ["mostrami", "fammi vedere"],
    "scrivi": ["registra", "salva"],
    "sposta": ["muovi", "trasferisci"],
    "configura": ["imposta", "setta"],
}

EN_VERB_SYN = {
    "find": ["search", "look for"],
    "search": ["find", "look for"],
    "delete": ["remove", "drop"],
    "remove": ["delete", "drop"],
    "create": ["make", "new"],
    "read": ["show", "open"],
    "show": ["display", "list"],
    "write": ["save", "store"],
    "move": ["transfer"],
    "configure": ["setup", "set"],
}

IT_FILLERS = ["per favore", "ti prego", "puoi", "potresti"]
EN_FILLERS = ["please", "could you", "can you"]


def augment_query(q: str, lang: str) -> list[str]:
    """Generate 1-3 deterministic variations of q.

    Rules (deterministic):
    - lowercase variant if q has capital
    - filler prefix if no filler present
    - one synonym swap on first verb if applicable
    """
    out: list[str] = []
    q = q.strip()
    if not q:
        return out

    words = q.split()
    if not words:
        return out

    # 1. Lowercase variant
    if q[0].isupper():
        out.append(q[0].lower() + q[1:])

    # 2. Synonym swap on first word
    first = words[0].lower()
    syn_map = IT_VERB_SYN if lang == "it" else EN_VERB_SYN
    if first in syn_map:
        for repl in syn_map[first][:1]:
            new_words = [repl] + words[1:]
            out.append(" ".join(new_words))

    # 3. Filler prefix (only if not already a filler-led query)
    fillers = IT_FILLERS if lang == "it" else EN_FILLERS
    if not any(q.lower().startswith(f) for f in fillers):
        out.append(f"{fillers[0]} {q}")

    return out


def detect_lang(q: str) -> str:
    """Heuristic IT vs EN detection."""
    it_markers = [" e' ", " un ", " del ", " della ", " sui ", " gli ",
                  "quanti", "quante", "che ", "domani", "ieri", "mi ", "ti ",
                  "trova", "cerca", "elimina", "cancella", "leggi", "mostra",
                  "scrivi", "sposta", "crea", "configura", "rimuovi",
                  " la ", " il ", " lo ", "delle"]
    en_markers = [" the ", " is ", " a ", " of ", " in ", " on ",
                  "find", "search", "delete", "remove", "read", "show",
                  "write", "move", "create", "configure", "today", "tomorrow"]
    ql = " " + q.lower() + " "
    it_score = sum(1 for m in it_markers if m in ql)
    en_score = sum(1 for m in en_markers if m in ql)
    return "it" if it_score >= en_score else "en"


# ---------- Step 5: assemble ----------

MIN_SAMPLES_PER_TOOL = 3


def main():
    print("[1/5] Loading existing dataset ...")
    existing = load_existing()
    print(f"  Existing pairs: {len(existing)}")

    print("[2/5] Loading typing universe ...")
    universe = load_universe()
    print(f"  Universe: {len(universe)} tools")

    # Step 3: add templates for uncovered + low-coverage tools
    print("[3/5] Adding templated queries ...")
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in existing:
        by_tool[r["first_tool"]].append(r)

    templated_added = 0
    for tool, pairs in TEMPLATES.items():
        if tool not in universe:
            print(f"  WARN: template tool '{tool}' not in universe (skip)")
            continue
        for (it_q, en_q) in pairs:
            for q in (it_q, en_q):
                rec = {"query": q, "first_tool": tool, "source": "template"}
                by_tool[tool].append(rec)
                templated_added += 1
    print(f"  Templates added: {templated_added}")

    # Step 4: augmentation pass
    print("[4/5] Augmenting all queries ...")
    augmented_added = 0
    for tool in list(by_tool.keys()):
        originals = list(by_tool[tool])
        for rec in originals:
            q = rec["query"]
            lang = detect_lang(q)
            for new_q in augment_query(q, lang):
                rec2 = {"query": new_q, "first_tool": tool, "source": "augmented"}
                by_tool[tool].append(rec2)
                augmented_added += 1
    print(f"  Augmented added: {augmented_added}")

    # Dedup globally (priority: frozen > real > template > augmented)
    seen_q: dict[str, dict] = {}
    for tool, recs in by_tool.items():
        for rec in recs:
            q = rec["query"]
            if q in seen_q:
                prio = {"frozen": 4, "real": 3, "template": 2, "augmented": 1}
                if prio.get(rec["source"], 0) > prio.get(seen_q[q]["source"], 0):
                    seen_q[q] = rec
            else:
                seen_q[q] = rec

    dataset = list(seen_q.values())
    random.shuffle(dataset)
    print(f"  Total dedup samples: {len(dataset)}")

    # Step 5: balance report
    print("[5/5] Computing balance ...")
    counts: dict[str, int] = defaultdict(int)
    src_counts: dict[str, int] = defaultdict(int)
    for r in dataset:
        counts[r["first_tool"]] += 1
        src_counts[r["source"]] += 1

    covered = set(counts.keys())
    missing = sorted(set(universe) - covered)
    n_covered = len(covered & set(universe))

    sample_sizes = sorted(counts.values())
    min_s = sample_sizes[0] if sample_sizes else 0
    max_s = sample_sizes[-1] if sample_sizes else 0
    med_s = sample_sizes[len(sample_sizes) // 2] if sample_sizes else 0

    under = sorted([t for t in counts if counts[t] < MIN_SAMPLES_PER_TOOL])

    # Write dataset
    OUT_DATASET.write_text(json.dumps(dataset, indent=2, ensure_ascii=False))
    print(f"  Wrote {OUT_DATASET}")

    # Build report
    report = []
    report.append("# Dataset v2 Balance Report")
    report.append("")
    report.append(f"- Total samples: **{len(dataset)}**")
    report.append(f"- Tools covered: **{n_covered}/{len(universe)}** typing")
    report.append(f"- Min samples/tool: **{min_s}**")
    report.append(f"- Median samples/tool: **{med_s}**")
    report.append(f"- Max samples/tool: **{max_s}**")
    report.append("")
    report.append("## Source breakdown")
    for s, c in sorted(src_counts.items(), key=lambda x: -x[1]):
        report.append(f"- {s}: {c}")
    report.append("")
    report.append("## Coverage delta vs v1")
    v1_pairs = load_existing()
    v1_labels = {p["first_tool"] for p in v1_pairs}
    new_labels = sorted(covered - v1_labels)
    report.append(f"- v1 labels: {len(v1_labels)}")
    report.append(f"- v2 labels: {len(covered)}")
    report.append(f"- New labels added: {len(new_labels)}")
    if new_labels:
        report.append("  - " + ", ".join(new_labels))
    report.append("")
    report.append("## Samples per tool (sorted by count desc)")
    report.append("| tool | n_samples |")
    report.append("|------|-----------|")
    for t, c in sorted(counts.items(), key=lambda x: -x[1]):
        report.append(f"| {t} | {c} |")
    report.append("")

    if missing:
        report.append(f"## Tools STILL without coverage ({len(missing)})")
        for t in missing:
            report.append(f"- {t}")
        report.append("")
    else:
        report.append("## All typing universe covered.")
        report.append("")

    if under:
        report.append(f"## Under-covered (<{MIN_SAMPLES_PER_TOOL} samples) — {len(under)}")
        for t in under:
            report.append(f"- {t}: {counts[t]}")
        report.append("")

    OUT_REPORT.write_text("\n".join(report))
    print(f"  Wrote {OUT_REPORT}")
    print()
    print("=" * 60)
    print(f"SUMMARY: {len(dataset)} samples, {n_covered}/{len(universe)} tools covered")
    print(f"  min/median/max per tool: {min_s}/{med_s}/{max_s}")
    if missing:
        print(f"  STILL MISSING ({len(missing)}): {missing}")
    if under:
        print(f"  UNDER {MIN_SAMPLES_PER_TOOL} ({len(under)}): {under}")
    print("=" * 60)


if __name__ == "__main__":
    main()
