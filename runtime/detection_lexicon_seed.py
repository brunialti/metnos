#!/usr/bin/env python3
"""detection_lexicon_seed — fonte canonica IT+EN dei lessici di detection.

Unico punto in cui i lessici NL vivono nel codice (come il seed delle chiavi
i18n). Ogni `register(...)` e' idempotente: scrive solo le righe (concept,
lang) mancanti, quindi e' sicuro richiamarlo ad ogni boot.

REGOLA DI MIGRAZIONE (a regressione zero): per ogni concept, l'unione delle
forme it+en deve essere ESATTAMENTE l'insieme del costrutto hardcoded che
sostituisce. Il matcher unisce sempre {lingua_corrente} ∪ {it,en}, quindi su
istanze it/en il comportamento e' identico. Le righe per lingue nuove le
popola il daemon di traduzione (`jobs/detection_translate_pending.py`).

Per i regex morfologici "intrecciati" (unita' temporali it+en nello stesso
pattern) il pattern hand-tuned e' archiviato verbatim sotto it ED en (il
merge deduplica → compilato una volta) cosi' la copertura it/en e' corretta;
la sintesi per lingue nuove la fa il daemon dal word-list tradotto.
"""
from __future__ import annotations

import detection_lexicon as _dl
from vocab import PROVIDER_SUFFIXES  # identità provider = vocabolario (SoT unica)


# Marker NL (linguistici, i18n) per ogni provider — SOLO i VALORI. Le CHIAVI
# (l'identità dei provider) NON si ripetono qui: derivano da
# `vocab.PROVIDER_SUFFIXES` (SoT unica). Un nuovo provider si aggiunge in vocab
# + qui i suoi marker; le due liste devono coprire lo stesso set (guard:
# `test_provider_markers_cover_suffixes`). Le altre lingue le sintetizza il
# daemon dal word-list tradotto (it={} → fallback en).
#
# DEBITO i18n (NON sanare finché lo scope è IT+EN, §1 — YAGNI): questo `{it,en}`
# è un dict a 2 LOCALI FISSI, non i18n vero. Conseguenza: «aggiungi una lingua =
# lascia cadere un file» NON funziona — il 3° locale richiede PRIMA il refactor
# verso il DB i18n locale-driven (chiavi semantiche + fallback xx→en, come
# `messages.get`/METNOS_LANG §11) + `validate_invariant` su set-locali APERTO
# (oggi esige parità IT/EN incondizionata). Vedi memoria project-i18n-lexicon-debt.
_PROVIDER_MARKERS_EN = {
    "github": ["github", "pr", "issue", "issues", "repo", "repository",
               "commit", "branch", "workflow", "gist", "fork", "merge"],
    "google_workspace": ["google", "drive", "gmail", "gdrive", "workspace",
                         "calendar google", "g suite"],
    # SOLO brand: MAI "photos"/"foto" nudo — parola comune che dirotterebbe le
    # query foto LOCALI (§7.3). Ogni marker contiene "google" o "gphotos".
    "google_photos": ["google photos", "google foto", "google photo",
                      "foto google", "gphotos"],
}


def register_all() -> None:
    R = _dl.register

    # ── UNDO ───────────────────────────────────────────────────────────
    # tool_grammar._UNDO_MARKERS (word-boundary, query_has_undo_marker)
    R("undo.grammar_marker", "phrases", match_mode="word",
      it=["annulla", "annullare", "annullo", "annullala", "ripristina",
          "ripristino", "ripristinare", "torna indietro", "torna su",
          "disfa", "disfare", "annulla l'ultimo"],
      en=["undo", "rollback"])
    # intent_extractor._UNDO_PATTERNS (substring, bypass deterministico)
    R("undo.intent_bypass", "phrases", match_mode="substring",
      it=["annulla", "annullare", "annullo", "ripristina", "torna indietro",
          "indietreggia", "anull"],
      en=["undo", "revert", "rollback"])

    # ── MAIL: posta indesiderata ───────────────────────────────────────
    # junk_mail_resolver: «sposta/cancella/filtra le email di spam / la posta
    # indesiderata» → filtro su category_hints (bulk/newsletter), NON
    # classify(junk) (= spam evidente, ~0; controprova 23/6, opzione b Roberto).
    R("mail.junk_terms", "phrases", match_mode="substring",
      it=["spam", "posta indesiderata", "indesiderata", "spazzatura",
          "posta spazzatura"],
      en=["spam", "junk", "junk mail"])

    # ── TASKS / SCHEDULING ─────────────────────────────────────────────
    # tool_grammar._TASKS_MARKERS (word-boundary)
    R("tasks.marker", "phrases", match_mode="word",
      it=["schedula", "schedulare", "ricorrente", "ricorrenti", "promemoria",
          "ricordami", "ricordati", "ricorda", "storico", "esecuzione",
          "esecuzioni", "cancella task", "elenca task", "lista task"],
      en=["task", "tasks", "schedule", "scheduled", "reminder", "remind",
          "timer", "daily", "weekly", "hourly", "history"])
    # tool_grammar._RECURRENCE_WORDS (word-boundary)
    R("tasks.recurrence_word", "phrases", match_mode="word",
      it=[], en=["daily", "weekly", "hourly"])
    # tool_grammar._RE_SCHEDULE_PHRASE (regex intrecciato → verbatim it+en)
    _SCHEDULE = (
        r"\b(?:ogni|every)\s+(?:\d+\s*)?"
        r"(?:second|minut|min\b|or[ae]\b|giorn|d[ìi]\b|settiman|mes[ei]\b|ann|"
        r"day|hour|week|month|year)"
        r"|\b(?:fra|tra)\s+(?:\d+|un[ao']?|mezz)"
    )
    R("tasks.schedule_phrase", "regex", it=[_SCHEDULE], en=[_SCHEDULE])
    # tool_grammar._RE_RECURRENCE_PHRASE (sottoinsieme stretto)
    _RECUR = (
        r"\b(?:ogni|every)\s+(?:\d+\s*)?"
        r"(?:second|minut|min\b|or[ae]\b|giorn|d[ìi]\b|settiman|mes[ei]\b|ann|"
        r"day|hour|week|month|year)"
    )
    R("tasks.recurrence_phrase", "regex", it=[_RECUR], en=[_RECUR])

    # ── SKILLS ─────────────────────────────────────────────────────────
    # tool_grammar._SKILLS_MARKERS (word-boundary)
    R("skills.marker", "phrases", match_mode="word",
      it=["capacità", "capacita", "modulo", "moduli"],
      en=["skill", "skills", "capability", "capabilities", "module",
          "modules"])

    # ── NOTIFY ─────────────────────────────────────────────────────────
    # orchestration._NOTIFY_HINTS (substring)
    R("notify.request", "phrases", match_mode="substring",
      it=["mandami", "manda", "inviami", "invia", "notificami", "scrivimi",
          "avvisami", "informami", "rispondimi"],
      en=["send me", "email me", "notify me", "let me know"])
    # orchestration._CHANNEL_HINTS (mapping canale -> forme)
    R("notify.channel", "mapping",
      it={"email": ["posta"],
          "telegram": ["telegrami", "messaggio telegram"]},
      en={"email": ["email", "e-mail", "mail"],
          "telegram": ["telegram", "chat"]})
    # photo_fields_resolver: sinonimi NL -> valore-enum canonico di get_files.
    # Le CHIAVI sono i valori enum TECNICI (identici fra le lingue, non
    # tradotti); le FORME sono le superfici NL che il planner puo' emettere,
    # per lingua. La chiave speciale "all" espande a tutti i campi. Ogni
    # canonico include se stesso fra le forme (self-map: se il planner emette
    # gia' il valore corretto, resta invariato).
    R("photo.metadata_fields", "mapping",
      it={"dates.semantic": ["dates.semantic", "data", "date", "quando", "datazione"],
          "dates.created": ["dates.created", "creazione", "creato", "data di creazione", "scatto"],
          "dates.modified": ["dates.modified", "modifica", "modificato", "data di modifica"],
          "gps": ["gps", "coordinate", "coordinata", "geolocalizzazione", "posizione gps"],
          "place": ["place", "luogo", "localita", "posizione", "dove"],
          "device": ["device", "dispositivo", "fotocamera", "macchina fotografica", "modello", "marca"],
          "image_dimensions": ["image_dimensions", "dimensioni", "risoluzione", "larghezza", "altezza"],
          "size": ["size", "dimensione", "peso", "grandezza", "byte"],
          "all": ["all", "tutto", "tutti", "metadata", "metadati", "exif", "tutti i metadati"]},
      en={"dates.semantic": ["dates.semantic", "date", "dates", "when", "datetime"],
          "dates.created": ["dates.created", "created", "creation", "taken", "capture date"],
          "dates.modified": ["dates.modified", "modified", "modification"],
          "gps": ["gps", "coordinates", "geo", "geolocation", "latitude", "longitude"],
          "place": ["place", "location", "where", "city"],
          "device": ["device", "camera", "make", "model"],
          "image_dimensions": ["image_dimensions", "dimensions", "resolution", "width", "height"],
          "size": ["size", "filesize", "bytes", "weight"],
          "all": ["all", "everything", "metadata", "exif"]})

    # ── OUTPUT INTENT ──────────────────────────────────────────────────
    # output_policy._COUNT_MARKERS (regex, split pulito it/en)
    R("output.count_request", "regex",
      it=[r"\b(quant[io]|quante|numero di|conta)\b"],
      en=[r"\b(count|how many|how much)\b"])
    # output_policy._VISUALIZE_MARKERS (regex, split pulito it/en)
    R("output.visualize_request", "regex",
      it=[r"\b(mostra|mostrami|fammi vedere|vedi|visualizz\w*|guarda)\b"],
      en=[r"\b(show|show me|display|view|let me see)\b"])

    # ── SYSTEM STATUS (intent_extractor bypass → get_processes+health) ──
    # «stato del server / come sta il server / server status» = l'INSIEME dei
    # dati di stato del sistema (Roberto 9/7) = get_processes(include_health).
    # Query ellittiche al confine semantico: l'LLM fast in prod (call
    # concorrenti) estraeva object instabile (approval/numbers, turn live 9/7)
    # → bypass DETERMINISTICO (§7.9, come undo.intent_bypass). Phrases → il
    # daemon i18n_translator le traduce per-lingua.
    R("system.status_query", "phrases", match_mode="substring",
      it=["stato del server", "stato server", "come sta il server",
          "come va il server", "salute del server", "salute del sistema",
          "stato del sistema", "stato della macchina", "come sta la macchina",
          "descrivi metnos server", "descrivi il server"],
      en=["server status", "system status", "server health", "system health",
          "how is the server", "how's the server", "describe the server",
          "machine status"])

    # ── MACHINE REFERENCE (intent bypass hardware, con health.section_focus) ──
    # «…del server / della macchina / questo pc»: la domanda riguarda LA
    # macchina (server o device) → col focus-hardware instrada a get_processes.
    R("machine.reference", "phrases", match_mode="substring",
      it=["server", ".33", "questo pc", "mio pc", "macchina", "computer",
          "sistema"],
      en=["server", "this pc", "my pc", "machine", "computer", "system"])

    # ── HEALTH SECTION FOCUS (agent_runtime._prepend_health_block_if_any) ──
    # Domanda SPECIFICA su una sezione health («qual è l'ip», «che gpu ha») →
    # il blocco-status mostra SOLO quella sezione, dettagliata. Chiavi = nomi
    # tecnici delle sezioni health (stabili cross-lingua); forme NL per lingua.
    R("health.section_focus", "mapping",
      it={"network": ["ip", "indirizzo ip", "indirizzi ip", "mac",
                      "indirizzo mac", "interfacce", "interfaccia di rete"],
          "gpu": ["gpu", "scheda video", "scheda grafica", "vram"],
          "cpu": ["cpu", "processore", "core", "frequenza"],
          "system": ["sistema operativo", "hostname", "kernel", "distro",
                     "che os", "quale os", "architettura"],
          "memory": ["ram", "memoria"],
          "disk": ["disco", "dischi", "spazio su disco", "filesystem"],
          "thermal": ["temperatura", "temperature", "gradi"],
          "power": ["consumo", "watt", "energia", "potenza"],
          "peripherals": ["usb", "periferiche", "periferica", "nvme", "ssd"],
          "services": ["servizi", "servizio", "systemd", "demoni"],
          "load": ["carico", "load", "uptime"]},
      en={"network": ["ip", "ip address", "ip addresses", "mac",
                      "mac address", "interfaces", "network interface"],
          "gpu": ["gpu", "video card", "graphics card", "vram"],
          "cpu": ["cpu", "processor", "cores", "frequency"],
          "system": ["operating system", "hostname", "kernel", "distro",
                     "which os", "architecture"],
          "memory": ["ram", "memory"],
          "disk": ["disk", "disks", "disk space", "filesystem"],
          "thermal": ["temperature", "temperatures", "degrees"],
          "power": ["power draw", "watts", "power consumption"],
          "peripherals": ["usb", "peripherals", "peripheral", "nvme", "ssd"],
          "services": ["services", "service", "systemd", "daemons"],
          "load": ["load", "uptime"]})

    # ── FILESYSTEM SIZE QUERY (dispatch._route_folder_size) ────────────
    # «quanto è grande la cartella X / how big is folder X» = intento
    # DIMENSIONE-cartella. Guarda: il peso di una cartella = somma dei file
    # RICORSIVI (find_files+compute), NON il conteggio delle sottodir (find_dirs).
    # Il guard combina QUESTO match con un produttore-contenitore nel piano →
    # falsi positivi bassi (una query-foto «dimensione» produce get_files, non
    # find_dirs). Regex morfologica, split pulito it/en.
    R("fs.size_query", "phrases", match_mode="substring",
      it=["quanto è grande", "quanto e grande", "quanto occupa", "quanto pesa",
          "quanto spazio", "dimensione della", "dimensione di", "dimensione del",
          "dimensione totale", "dimensione complessiva", "dimensione in",
          "quanti byte", "grandezza della", "grandezza di", "peso della",
          "peso di", "peso dei", "peso totale", "spazio occupato",
          "spazio su disco"],
      en=["how big", "how large", "how much space", "how much room",
          "folder size", "directory size", "disk usage", "size of the folder",
          "size of folder", "size of the directory", "space used"])

    # ── MOVE «i file DA/IN una cartella» (dispatch._enrich_move_source_dir) ──
    # «sposta i file da X a Y»: X è un CONTENITORE, i file vanno enumerati
    # (find_files→move), non passati come singola dir-entry (che il safety-net
    # rifiuta). Il segnale è «i file + da/in/della cartella»; «sposta la cartella
    # X» (senza «file») NON matcha → si sposta X stessa. Regex, split it/en.
    R("fs.files_in_folder", "phrases", match_mode="substring",
      it=["i file da", "i file in", "i file nella", "i file della",
          "i file dalla", "i file dentro", "i file presenti in",
          "i file contenuti in", "tutti i file da", "tutti i file in",
          "gli allegati da", "i documenti da", "i documenti in"],
      en=["the files in", "the files from", "the files inside", "files in the",
          "files from the", "all files in", "all the files in"])

    # ── WEB SCRAPING ───────────────────────────────────────────────────
    # output_format._COOKIE_BANNER_MARKERS (substring)
    R("web.cookie_banner", "phrases", match_mode="substring",
      it=["questo sito utilizza cookie", "uso dei cookie", "accetta i cookie",
          "cookie tecnici", "proseguendo nella navigazione",
          "informativa sulla privacy"],
      en=["this site uses cookies", "we use cookies", "accept cookies",
          "cookie policy", "by continuing to browse", "privacy policy"])

    # ── CONFERME DIALOGO (channels/daemon._YES_PATTERN/_NO_PATTERN) ─────
    R("confirm.yes", "regex",
      it=[r"\b(s[iì]|alza|aumenta|rilancia|più)\b"],
      en=[r"\b(yes|y|ok|okay)\b"])
    R("confirm.no", "regex",
      it=[r"\b(no|annulla|lascia|niente)\b"], en=[r"\b(n|stop)\b"])

    # ── OBJECT CLASSIFICATION (store sink) ─────────────────────────────
    # dispatch._normalize_store_clauses (D2-c, 18/6): riferimento a uno
    # STORE/ARCHIVIO/RACCOLTA interno (object=entries). Forme con
    # preposizione: lo store-sink, non il sostantivo nudo (evita "vai allo
    # store"). Sicuro a falsi-positivi: il flip a entries scatta SOLO su una
    # clausola NON-routabile (tool-existence guard), mai su tool reali.
    R("object.store_sink", "phrases", match_mode="substring",
      it=["nello store", "nel store", "dallo store", "dal store", "allo store",
          "nell'archivio", "nell archivio", "dall'archivio", "nella raccolta",
          "dalla raccolta", "store interno", "archivio interno",
          "raccolta dati", "database interno", "datastore"],
      en=["in the store", "to the store", "into the store", "from the store",
          "internal store", "data store", "datastore", "internal archive",
          "internal collection", "data collection"])

    # ── CONNETTORI MULTI-STEP ──────────────────────────────────────────
    # compound_decomposer._CONNECTOR_PATTERN: solo le PAROLE (i simboli ,;&&
    # sono lingua-invarianti, restano nel builder del pattern di split).
    R("compound.connector_word", "phrases", match_mode="word",
      it=["e", "poi", "infine"], en=["and", "then", "after", "finally"])

    # ── CREDENTIALS: etichette dei campi nella chat ───────────────────
    # I valori vengono estratti PRIMA del planner; solo queste forme
    # traducibili identificano i due campi, senza imporre un comando CLI.
    R("credentials.field_label", "mapping", match_mode="word",
      it={"username": ["utente", "nome utente", "user", "username", "usr",
                       "email", "e-mail", "login"],
          "password": ["password", "passwd", "passphrase", "pwd", "psw",
                       "pass"]},
      en={"username": ["user", "username", "user id", "userid", "usr",
                       "email", "e-mail", "login"],
          "password": ["password", "passwd", "passphrase", "pwd", "pass"]})
    R("credentials.pair_connector", "phrases", match_mode="word",
      it=["e", "con"], en=["and", "with"])

    # ── SITES: azioni browser in linguaggio naturale ──────────────────
    # Chiavi tecniche chiuse; le superfici sono traducibili e non vivono nel
    # broker. "apri/open" e' click senza URL, navigazione con URL esplicito.
    R("sites.action_verb", "mapping", match_mode="word",
      it={"goto": ["vai", "naviga", "visita", "raggiungi"],
          "click": ["clicca", "premi", "seleziona", "scegli", "tocca", "apri"],
          "fill": ["compila", "scrivi", "inserisci", "digita"],
          "submit": ["invia", "conferma", "salva", "pubblica"],
          "wait": ["attendi", "aspetta", "pausa"]},
      en={"goto": ["go", "navigate", "visit", "reach"],
          "click": ["click", "press", "select", "choose", "tap", "open"],
          "fill": ["fill", "write", "enter", "type"],
          "submit": ["submit", "confirm", "save", "publish"],
          "wait": ["wait", "pause"]})
    # Target che porta dalla landing page al form di autenticazione. Serve al
    # guard sites per distinguere un click PRE-login dalle azioni richieste
    # dopo l'accesso, senza imporre una sintassi alla frase dell'utente.
    R("sites.login_entry_target", "phrases", match_mode="word",
      it=["accedi", "accesso", "entra", "login", "log in", "sign in",
          "area riservata", "area clienti", "area personale", "account"],
      en=["sign in", "log in", "login", "access", "enter", "account",
          "customer area", "member area", "personal area"])
    # Sottoinsieme che identifica un controllo di autenticazione DIRETTO. I
    # reveal generici (account/area personale) restano nel concetto precedente
    # come fallback, ma non devono vincere su un vero link di accesso.
    R("sites.login_direct_target", "phrases", match_mode="word",
      it=["accedi", "accesso", "entra", "login", "log in", "sign in"],
      en=["sign in", "log in", "log on", "login", "access", "enter"])
    # Intento forte di autenticazione espresso sull'intero comando. E' distinto
    # dal nome di un controllo: impedisce che un semplice testo "Accedi" nella
    # pagina recluti login_sites, ma copre le formulazioni naturali con cui
    # l'utente chiede di autenticarsi senza dover nominare l'executor.
    R("sites.login_intent", "phrases", match_mode="word",
      it=["accedi al sito", "accedi sul sito", "accedi a", "fai login",
          "effettua il login", "effettua login", "effettua l'accesso",
          "effettua accesso", "autenticati", "entra nel portale",
          "entra nell'area riservata", "login sul sito"],
      en=["sign in to", "log in to", "login to", "authenticate to",
          "authenticate on", "enter the customer area",
          "enter the member area"])
    # Forma naturale di ingresso al sito usata come mandato di sessione. E' un
    # concetto distinto perche' il seed preserva i payload gia' tradotti delle
    # chiavi esistenti: una nuova chiave si propaga senza sovrascriverli.
    R("sites.session_entry_intent", "phrases", match_mode="word",
      it=["entra nel sito"], en=["enter the site"])
    R("sites.no_credentials", "phrases", match_mode="word",
      it=["senza credenziali", "senza usare le credenziali",
          "non usare le credenziali", "senza login", "non fare login",
          "non accedere", "solo pagina pubblica"],
      en=["without credentials", "do not use credentials",
          "don't use credentials", "without login", "do not log in",
          "do not sign in", "public page only"])
    # Procedure interne traducibili. Le chiavi canoniche restano nel codice;
    # tutte le forme linguistiche con cui utente e pagina le esprimono vivono
    # qui, cosi' l'interazione non diventa un mini-linguaggio della CLI.
    R("sites.search_action_verb", "phrases", match_mode="word",
      it=["cerca", "trova", "ricerca"],
      en=["search", "find", "look for"])
    # Richiesta di un insieme di record dalla pagina, distinta da una lettura
    # scalare o da una semplice navigazione. Il guard sites usa solo questo
    # concetto semantico; le forme linguistiche restano qui, traducibili.
    R("sites.structured_record_request", "phrases", match_mode="word",
      it=["dimmi quali", "quali sono", "dimmi i", "dimmi gli", "dimmi le",
          "elenca", "fammi l'elenco", "dammi l'elenco", "dammi la lista",
          "mostrami i", "mostrami gli", "mostrami le", "estrai i",
          "estrai gli", "estrai le"],
      en=["tell me which", "which are", "what are", "list", "give me a list",
          "show me all", "extract the"])
    # Ricerca espressa con articolo plurale: e' una collezione anche senza un
    # quantificatore esplicito ("trova le mie ..."). Regex linguistiche nel DB,
    # non nel router; il contesto sites resta una precondizione in dispatch.
    R("sites.collection_search_request", "regex",
      it=[r"\b(?:cerca|trova|ricerca)\s+(?:(?:tutt[ei]|tutti)\s+)?(?:i|gli|le)\b"],
      en=[r"\b(?:find|search(?:\s+for)?|list)\s+(?:all\s+)?(?:my\s+|the\s+)?(?:[\w'-]+\s+){0,3}[\w'-]+s\b"])
    R("sites.search_entry_target", "phrases", match_mode="word",
      it=["cerca", "ricerca", "apri ricerca", "mostra ricerca"],
      en=["search", "find", "open search", "show search"])
    R("sites.personal_goal_marker", "phrases", match_mode="word",
      it=["mio", "mia", "miei", "mie", "personale"],
      en=["my", "mine", "personal"])
    R("sites.account_reveal_control", "phrases", match_mode="word",
      it=["account", "menu account", "profilo", "menu profilo"],
      en=["account", "account menu", "profile", "profile menu"])
    R("sites.goal_term_alias", "mapping", match_mode="word",
      it={"booking": ["prenotazione", "prenotazioni", "viaggio", "viaggi",
                       "booking", "bookings", "trip", "trips"]},
      en={"booking": ["booking", "bookings", "trip", "trips",
                       "prenotazione", "prenotazioni", "viaggio", "viaggi"]})
    # Stati/facet trasversali alle UI. Il canonicale inglese rende confrontabili
    # query e controlli anche quando genere, numero o lingua differiscono
    # (es. «prenotazioni passate» -> tab «Passati»). Concetto nuovo per
    # propagarsi anche nei DB persistenti senza sovrascrivere goal_term_alias.
    R("sites.goal_state_alias", "mapping", match_mode="word",
      it={
          "past": ["passato", "passata", "passati", "passate",
                   "precedente", "precedenti"],
          "future": ["futuro", "futura", "futuri", "future",
                     "prossimo", "prossima", "prossimi", "prossime",
                     "in programma"],
          "cancelled": ["cancellato", "cancellata", "cancellati",
                        "cancellate", "annullato", "annullata", "annullati",
                        "annullate"],
          "archived": ["archiviato", "archiviata", "archiviati",
                       "archiviate"],
      },
      en={
          "past": ["past", "previous", "prior"],
          "future": ["future", "upcoming", "scheduled"],
          "cancelled": ["cancelled", "canceled"],
          "archived": ["archived"],
      })
    R("sites.continuation_target", "phrases", match_mode="word",
      it=["mostra altro", "mostra altri", "mostra altre", "carica altro",
          "carica altri", "carica altre", "vedi altro", "vedi altri",
          "vedi altre", "altri risultati", "altre fatture",
          "pagina successiva", "prossima pagina", "successivo", "avanti"],
      en=["show more", "load more", "view more", "more results",
          "more invoices", "next page", "next", "continue"])
    # Stato transitorio della superficie, usato soltanto insieme a segnali DOM
    # di caricamento o come testo breve visibile. Non prova mai da solo che il
    # goal sia stato raggiunto e non contiene label specifiche di un sito.
    R("sites.loading_marker", "phrases", match_mode="substring",
      it=["caricamento", "sto caricando", "attendi"],
      en=["loading", "please wait", "fetching"])
    R("sites.goal_noise", "phrases", match_mode="word",
      it=["a", "al", "alla", "alle", "con", "da", "dal", "dalla", "de",
          "dei", "del", "della", "di", "e", "gli", "i", "il", "in",
          "la", "le", "lo", "mio", "mia", "miei", "mie", "nel", "nella",
          "per", "su", "un", "una"],
      en=["a", "an", "and", "at", "for", "from", "in", "of", "on",
          "the", "to", "with", "my"])
    # Concetto additivo per installazioni che hanno gia' il payload immutabile
    # di goal_noise. Completa le preposizioni articolate italiane: sono
    # grammatica della richiesta, mai termini di contenuto da cercare nel DOM.
    R("sites.goal_noise_articulated_preposition", "phrases", match_mode="word",
      it=["al", "allo", "alla", "ai", "agli", "alle",
          "dal", "dallo", "dalla", "dai", "dagli", "dalle",
          "del", "dello", "della", "dei", "degli", "delle",
          "nel", "nello", "nella", "nei", "negli", "nelle",
          "sul", "sullo", "sulla", "sui", "sugli", "sulle"],
      en=[])
    R("sites.goal_scope_quantifier", "phrases", match_mode="word",
      it=["tutto", "tutta", "tutti", "tutte", "ogni", "intero", "intera",
          "interi", "intere"],
      en=["all", "every", "entire"])
    R("sites.external_search_scope", "phrases", match_mode="word",
      it=["sul web", "su internet", "nel web", "in internet"],
      en=["on the web", "on internet", "web search", "internet search"])
    # Modalita' dell'executor immagini web. Il router combina questi segnali
    # linguistici con object=images: il lessico non decide mai da solo il tool.
    R("images.web_search_scope", "phrases", match_mode="word",
      it=["sul web", "su internet", "nel web", "in internet", "online"],
      en=["on the web", "on internet", "web search", "internet search",
          "online"])
    R("images.reverse_search_intent", "phrases", match_mode="word",
      it=["ricerca inversa", "immagini simili", "immagine simile",
          "foto simili", "foto simile", "origine dell'immagine",
          "origine della foto", "da dove viene questa immagine",
          "da dove viene questa foto", "questa immagine", "questa foto",
          "immagine allegata", "foto allegata"],
      en=["reverse image search", "reverse search", "similar images",
          "similar image", "similar photos", "similar photo",
          "image origin", "photo origin", "where this image comes from",
          "where this photo comes from", "this image", "this photo",
          "attached image", "attached photo"])
    R("sites.privacy_reject_target", "phrases", match_mode="word",
      it=["rifiuta", "rifiuta tutti", "rifiuta tutto", "solo necessari",
          "continua senza accettare"],
      en=["reject", "reject all", "decline", "decline all",
          "necessary only", "continue without accepting"])
    # Concetto additivo: `register` non sovrascrive payload seed gia' installati.
    # La forma nominale e' comune nelle UI italiane e resta consumata soltanto
    # dal locator privacy strutturale, mai come click testuale libero.
    R("sites.privacy_reject_noun_target", "phrases", match_mode="word",
      it=["rifiuto"], en=[])
    # Marker del CONTENITORE, distinti dalle azioni. Il broker li combina con
    # struttura modale/fixed e un target di rifiuto esatto: una parola isolata
    # nel corpo pagina non e' mai sufficiente per produrre un click.
    R("sites.privacy_overlay_marker", "phrases", match_mode="substring",
      it=["cookie", "scelte pubblicitarie", "consenso", "privacy"],
      en=["cookie", "advertising choices", "consent", "privacy"])
    R("sites.login_continue_target", "phrases", match_mode="word",
      it=["continua", "avanti", "prosegui", "successivo"],
      en=["continue", "next", "proceed"])
    R("sites.overlay_dismiss_target", "phrases", match_mode="word",
      it=["chiudi", "chiudi dialogo", "chiudi finestra", "ignora",
          "non ora", "non adesso", "forse dopo", "piu tardi", "ho capito",
          "capito", "va bene", "annulla"],
      en=["close", "close dialog", "close modal", "dismiss",
          "dismiss dialog", "not now", "maybe later", "later", "got it",
          "understood", "okay", "cancel"])
    # `OK` e' un riconoscimento internazionale, non un'etichetta di sito. Un
    # concept nuovo propaga anche sui DB persistenti dove il seed precedente e'
    # intenzionalmente immutabile.
    R("sites.overlay_acknowledge_target", "phrases", match_mode="word",
      it=["ok"], en=["ok"])
    R("sites.two_factor_push_marker", "phrases", match_mode="substring",
      it=["approva la richiesta", "conferma sul dispositivo",
          "notifica sul telefono", "controlla il telefono"],
      en=["approve the request", "confirm on your device",
          "notification on your phone", "check your phone"])
    # Parole funzionali e nomi del tipo di controllo non fanno parte del nome
    # accessibile cercato. Esempio naturale: "clicca sul pulsante Accedi" ->
    # target "accedi". Lessico traducibile, non sintassi obbligatoria.
    R("sites.action_target_noise", "phrases", match_mode="word",
      it=["il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
          "sul", "sullo", "sulla", "sui", "sugli", "sulle",
          "pulsante", "bottone", "link", "collegamento", "voce"],
      en=["the", "a", "an", "on", "button", "link", "item", "control"])
    # Accessible-name di controlli che espandono la navigazione. Si usa solo
    # quando il target e' gia' presente nel testo pagina ma non interagibile;
    # il broker richiede unicita' + gate e poi risolve di nuovo il target.
    R("sites.reveal_control", "phrases", match_mode="word",
      it=["apri menu", "apri il menu", "mostra menu", "menu di navigazione"],
      en=["open menu", "show menu", "open navigation menu",
          "show navigation menu"])
    # agent_runtime._MULTISTEP_CONJUNCTIONS_RE (regex)
    R("query.multistep", "regex",
      it=[r"\b(e\s+poi|e\s+dopo|e\s+inoltre|e\s+anche|inoltre|poi|"
          r"dopodiche'|dopodiche)\b"],
      en=[r"\b(and\s+then|and\s+also|and\s+after|moreover|then|afterwards|"
          r"additionally)\b"])

    # ── PROVIDER (provider.markers → tool_grammar.provider_gate_names) ──
    # Brand/nomi propri en-canonici; le altre lingue aggiungono nomi comuni
    # localizzati via daemon. mapping suffix -> markers (match word).
    # CHIAVI derivate da vocab.PROVIDER_SUFFIXES (SoT unica dell'identità
    # provider, zero duplicazione); VALORI = i marker NL i18n qui sopra.
    R("provider.markers", "mapping", match_mode="word",
      it={},
      en={f"_{p}": _PROVIDER_MARKERS_EN[p] for p in PROVIDER_SUFFIXES})

    # ── PLANNER MARKERS (agent_runtime) ────────────────────────────────
    # _LLM_REFUSAL_MARKERS (substring) — testo di rifiuto LLM in un arg
    R("llm.refusal_marker", "phrases", match_mode="substring",
      it=["come modello linguistico", "come un modello linguistico",
          "in qualità di assistente", "in qualita di assistente",
          "in qualità di modello", "in qualita di modello",
          "non ho accesso diretto", "non ho accesso ai tuoi",
          "non posso eseguire questa", "non posso completare questa",
          "non sono in grado di", "mi dispiace, non posso",
          "mi dispiace ma non posso", "non posso fornire",
          "non posso accedere"],
      en=["as a language model", "as an ai", "as an a.i",
          "as an artificial intelligence", "i do not have access",
          "i don't have access", "i'm unable to", "i am unable to",
          "i cannot fulfill", "i cannot assist", "i can't assist",
          "i'm sorry, i can"])
    # _HEALTH_IMPERATIVE_KEYWORDS (substring; "stop " con spazio finale)
    R("health.imperative", "phrases", match_mode="substring",
      it=["uccidi", "ferma", "termina", "spegni", "manda", "invia", "scrivi",
          "esegui", "lancia", "riavvia"],
      en=["kill", "stop ", "restart"])
    # _COUNT_QUANTIFIER_MARKERS (substring; spazi significativi preservati)
    R("count.quantifier", "phrases", match_mode="substring",
      it=["quanti ", "quante ", " conta ", "numero di "],
      en=["how many ", " count "])
    # args_extractor._extract_count (E.2, 2/7/2026): CAP/conteggio ESPLICITO.
    # Capture group = il numero. Due forme: (a) prefisso di cap + numero
    # («prime 5», «top 3», «al massimo 10»); (b) numero + sostantivo
    # CONTABILE adiacente («100 foto», «10 mail»). Il sostantivo adiacente
    # disambigua conteggio-vs-altro: «foto del 2020» (anno) e «da 50 euro»
    # (prezzo) NON matchano — mai iniettare il primo intero qualsiasi.
    R("count.cap_pattern", "regex",
      it=[r"\b(?:prim[ie]|top|al massimo|massimo|non pi[uù]' ?di|fino a)"
          r"\s+(\d{1,4})\b",
          r"\b(\d{1,4})\s+(?:foto|immagini|file|documenti|allegati|mail|"
          r"email|messaggi|conversazioni|eventi|appuntamenti|contatti|"
          r"persone|cartelle|directory|task|attivit[aà]|issue|pull|url|"
          r"link|pagine|righe|voci|elementi|risultati|video|processi|"
          r"pacchetti|luoghi|posti|calendari|firme|proposte|credenziali|"
          r"numeri|testi|canzoni|brani)\b"],
      en=[r"\b(?:first|top|at most|max|maximum|no more than|up to)"
          r"\s+(\d{1,4})\b",
          r"\b(\d{1,4})\s+(?:photos?|pictures?|images?|files?|documents?|"
          r"attachments?|mails?|emails?|messages?|conversations?|events?|"
          r"appointments?|contacts?|persons?|people|folders?|directories|"
          r"dirs?|tasks?|issues?|pulls?|urls?|links?|pages?|lines?|entries|"
          r"items?|results?|videos?|processes|packages?|places?|calendars?|"
          r"signatures?|proposals?|credentials?|numbers?|texts?|songs?|"
          r"tracks?)\b"])
    # _RESUME_AFTER_DIALOG_HINTS_IT/EN (substring; gia' separati per lingua)
    R("dialog.resume_hint", "phrases", match_mode="substring",
      it=["mandami", "manda", "inviami", "invia", "notificami", "scrivimi",
          "avvisami", "informami",
          "e poi crea", "e poi prenota", "e poi fissa", "e poi sposta",
          "e poi cancella", "e poi invia", "e poi manda",
          "e crea", "e prenota", "e fissa", "e sposta", "e cancella",
          "e invia", "e manda"],
      en=["send me", "notify me", "tell me", "email me", "let me know",
          "and create", "and book", "and schedule", "and move",
          "and delete", "and send", "and notify",
          "then create", "then book", "then schedule", "then send"])
