#!/usr/bin/env python3
"""vocab.py — vocabolario chiuso di Metnos (single source of truth).

Tutti i prompt, executor synt, intent extractor, prefilter consumano
da QUI. Aggiungere/togliere un verbo si fa qui, non sparso in 5 file.

Convenzione: aggiungere un verbo richiede:
1. Aggiungerlo alla tupla `ACTIONS` (in coda, mai inserire in mezzo).
2. Aggiungere la categoria in `ACTION_CATEGORIES` (es. {"check": "verifica"}).
3. Aggiungere la mappa bilingue + confine semantico in `ACTION_MAPPING`.
4. Classificarlo come PRODUCER/CONSUMER/DESTRUCTIVE in `PRODUCER_VERBS`,
   `DESTRUCTIVE_VERBS` (sotto).
5. Aggiungere mapping in `intent_extractor` se serve disambiguazione vs
   verbi simili.

NON serve duplicare la lista nei documenti prescrittivi: i conteggi e gli
elenchi esaustivi devono essere derivati o verificati contro questo modulo.

Multilingua (it+en oggi, espandibile):
- Vocabolario CANONICO in inglese (ACTIONS, OBJECTS, QUALIFIERS).
- ACTION_MAPPING ha chiavi "it" e "en" simmetriche per i sinonimi —
  aggiungere "es"/"fr"/... seguendo lo stesso pattern.
- LANGS enumera le lingue supportate. I prompt che vogliono restare
  agnostici devono iterare LANGS, non hardcodare "it"/"en".
"""
from __future__ import annotations

# ── Lingue supportate ─────────────────────────────────────────────────
# Ordine = priorita' di rendering nei prompt che mostrano alternative
# multilingue (es. stage 1 di synt mostra IT prima di EN perche' la
# maggior parte delle query utente sono in italiano).
LANGS = ("it", "en")


# ── Vocabolario chiuso (ADR 0045 + naming convention §2.2) ────────────

# Lista canonica delle azioni. Ordine = categoria semantica (vedi
# ACTION_CATEGORIES). Aggiungere SOLO in coda alla rispettiva categoria.
ACTIONS = (
    # I/O fs
    "read", "write", "move", "delete", "create",
    # discovery
    "find", "list",
    # transform su entries
    "filter", "sort", "group", "classify",
    # metadata / scalari sistema (assorbe anche `fetch` HTTP — ADR
    # informale 3/5/2026: HTTP GET = lettura di URL = `get_urls`).
    "get", "set",
    # network
    "send",
    # output formattato / sintesi
    "describe", "render",
    # decomp / pack
    "extract", "compress",
    # calcolo / confronto
    "compute", "compare",
    # trasformazione (modifica forma/parametri di un dato senza cambio di natura)
    "change",
    # ordinamento persistente (materializza un'organizzazione del corpus per
    # query future veloci: indici CLIP, perceptual hash, threading messages, ...)
    "order",
    # OUTBOUND CONSENT: grant access a entita' senza spostarla (ADR 0128, 12/5/2026).
    # Distinto da `send` (outbound copy/notification) e da `set` (upsert idempotente
    # di stato/labels/metadata). Usato per `share_files_google_workspace`
    # (Drive share), `share_events_google_workspace` (Calendar invite), share di
    # folder cifs/smb. Side-effect: ACL/permission grant remoto, reversibile via
    # `delete_<obj>_permissions_by_id` (5° reverse_pattern §2.3).
    "share",
    # Dominio `sites` (spec sites F1/F2, RATIFICATO D-A 10/7/2026): interazione
    # web SICURA con siti autenticati. `open`=apre una sessione browser
    # persistente/autenticabile; `login`=login con credenziali cifrate (il broker
    # inietta il segreto, l'agente non lo vede §3.2); `act`=azione su una sessione
    # (F2, gate §4.2). `read` (gia' canonico) legge la sessione (read_sites).
    # Mappatura forzata sui canonici RESPINTA (ambigua) -> token nuovi.
    "open", "login", "act",
)

# Oggetti ammessi (plurale).
# `files` resta oggetto generico (read/find/list/get/move/delete su qualunque
# tipo, con filtro per kind). Domini specifici (`images` oggi, `audios`/
# `videos` quando emergeranno ops dedicate) sono oggetti di prima classe SOLO
# per i verbi che operano sul loro contenuto in modo specializzato (change,
# describe-visuale, find-similar). Non duplicano `files` per le ops generiche.
OBJECTS = (
    "files", "dirs", "packages", "messages", "events",
    "contacts", "places", "processes", "urls", "numbers",
    "images",
    # Politiche di sicurezza shell (ADR 0071): signature canonicalizzate
    # del tipo `binary:subcommand:target_kind`, classificate in
    # whitelist/blacklist/graylist/forbidden. Visibili al PLANNER come
    # ordinari executor handcrafted (find_signatures_*, write_*, ecc.).
    "signatures",
    # Testi come dominio di prima classe (3/5/2026): l'oggetto su cui si
    # applica un filtro/trasformazione testuale. La grana di operazione
    # (righe, paragrafi, frasi, pagine) e' espressa come QUALIFIER, non
    # come oggetto separato — vedi `filter_texts_lines`. `lines` e
    # affini sono stati spostati in QUALIFIERS.
    "texts",
    # Proposte / candidati in attesa di review (3/5/2026):
    # - candidati introvertiva (dedupe / generalize / specialize)
    #   prodotti dal task notturno `introvertiva_propose`;
    # - in prospettiva, anche proposte synt di nuovi executor e
    #   richieste di approvazione del Vaglio (oggi modellate in altro modo).
    # Visibili al PLANNER come ordinario `get_proposals(kind=...)`.
    "proposals",
    # Persons (15/5/2026): registro nominale di persone enrolled (volti
    # arcface, slug case+accent-insensitive). Distinto da `contacts`
    # (rubrica indirizzi/email). Entita' di prima classe: ha 4 executor
    # canonici (get_persons/set_persons/find_persons_indices/delete_persons)
    # e dialog flow di disambiguazione face-picker. Sezione planner
    # `photos` (compositiva con images: «foto di Carol al mare»).
    "persons",
    # Tasks (15/5/2026): task ricorrenti / promemoria / timer schedulati
    # nel scheduler v2 Metnos (ADR 0112). Entita' di prima classe distinta
    # da `events` (calendario utente): hanno trigger grammar `daily@HH:MM`/
    # `every_Nm`/`at:<ISO>`/`cron:<5-field>`, history esecuzioni, query
    # da rilanciare al fire, grace window. Sezione planner `scheduled_tasks`
    # gated automaticamente via `_OBJECT_TO_SECTIONS['tasks']`.
    "tasks",
    # Inputs: raccolta strutturata di valori forniti dall'utente in
    # risposta a un dialogo (ADR 0090, 4-5/5/2026). Plurale invariante.
    # Astratto come `signatures`: oggetto-strumento per la raccolta
    # dichiarativa di parametri (credenziali, conferme, scelte multiple,
    # configurazioni). Il singolo executor canonico e' `get_inputs(title,
    # dialog=[{var, prompt, schema}, ...], fmt=...)`. ECCEZIONE SEMANTICA
    # controllata: l'output di `get_inputs` e' un dict `{var: value, ...}`
    # (gli input raccolti), NON una lista di entries — analogo all'eccezione
    # di `find_images_indices` che ritorna `images` (il dominio principale)
    # interrogando il mezzo di ricerca `indices` (modalita').
    "inputs",
    # approval (17/6/2026): gate di consenso umano. Oggetto-strumento di
    # `get_approval` (builtin cross-skill): chiede Approva/Disapprova e instrada
    # la scelta a un executor (on_approve/on_reject). Come `inputs` e' UI: niente
    # mutating verb proprio.
    "approval",
    # Credentials: storage cifrato di chiavi/token/segreti (Fernet+HKDF,
    # ADR 0082+0089, 10/5/2026). Plurale invariante. I VALORI cleartext
    # NON tornano mai al PLANNER: gli executor `find_credentials` /
    # `set_credentials` / `delete_credentials` espongono SOLO metadata
    # (binding, fingerprint, scopes, age, status). I valori cifrati sono
    # accessibili agli altri executor via `runtime/credentials.py::load(binding)`
    # durante invocation, fuori dalla vista LLM. Vincolo capability:
    # `metnos:credentials_metadata_only` — il vaglio rifiuta payload con
    # campi `value`/`token`/`secret`/`api_key` nel return.
    "credentials",
    # Issues (ADR 0141, 17/5/2026): entita' remota GitHub mutabile e threaded,
    # con stato (open/closed), label, assignee, reazioni. Provider qualifier
    # `_github`. 5 executor `*_issues_github` (find/read/create/set/delete).
    # Distinta da `pulls`: una issue NON ha diff ne' azione `merge`.
    "issues",
    # Pulls (ADR 0141): pull request GitHub. Distinta da `issues` per 3
    # proprieta' che romperebbero l'invariante schema §2.6 se collassate:
    # diff sempre presente, azione `merge`, review strutturate
    # (approve/request_changes/comment). 6 executor `*_pulls_github`
    # (find/read/create/set/change/delete). Le review sono una sub-forma di
    # `messages` (send_messages_github con `review_event`), non un OBJECT.
    "pulls",
    # Calendars (3/6/2026): CALENDARIO-contenitore (non l'evento). Distinto da
    # `events` (item) per §2.6: un calendario AGGREGA eventi, ha id/summary/
    # timezone propri; create/list/delete operano sul container, non sull'evento.
    # Esposto dal backend Google (calendars().insert / calendarList.list /
    # calendars().delete) — il `.ics` locale gestisce un solo calendario.
    # 3 executor `*_calendars`. §2.2: necessario (no equivalente in `events`),
    # generale (concetto cross-provider), comprensibile. Approvato Roberto 3/6.
    "calendars",
    # Entries: meta-oggetto per pipeline in-memory dello stesso turno
    # (12/5/2026, formalizzazione audit). NON una risorsa esterna: e' una
    # lista runtime prodotta da step precedenti e consumata da operatori
    # di trasformazione/aggregazione (compute_entries/sort_entries/
    # filter_entries/group_entries). Plurale invariante. Discrimine
    # rispetto agli altri OBJECTS: gli altri puntano a sorgenti di
    # verita' esterne (filesystem, IMAP, calendario, web, indici);
    # `entries` non ha sorgente, esiste solo nel ciclo di vita del
    # turno corrente. Nessun executor `find_entries`/`read_entries`/
    # `get_entries` (non si scopre/legge cio' che esiste solo a runtime).
    "entries",
    # Lists (ADR 0138): meta-oggetto strutturale per operatori fra DUE liste
    # di entries. Distinto da `entries` (una lista in-memory): oggi ammette
    # soltanto i verbi filter/compute, enforced dalla Naming Authority.
    "lists",
    # Skill installate/abilitate nel catalogo runtime. Oggetto amministrativo
    # distinto dagli executor che le skill forniscono; list/set sono i soli
    # verbi builtin correnti.
    "skills",
    # Sites (spec sites F1, RATIFICATO D-A 10/7/2026): SESSIONE web con stato
    # (cookie/credenziali), distinta da `urls` (pagina pubblica senza stato).
    # Confine manifest: «pagina senza login = read_urls_html; sites = sessione
    # con stato/credenziali». Ha executor open/login/read/close_sites (+act F2).
    # Il `session_id` e' interno (§12-bis): l'utente dice «il sito X», il planner
    # cabla il session_id via from_step. Gira SOLO server (§10.8).
    "sites",
)
# NB §2.2 (26/5/2026, ADR 0163): `users` NON è OBJECT vocab. L'account
# Metnos paired (host/guest, ADR 0083) è runtime-internal, esposto al
# PLANNER come ATTRIBUTI di `persons` via `read_persons` aggregator (legge
# users.db + persons.sqlite). Per «chi sono io»/«mio profilo» il pattern
# è `read_persons(name="${RUNTIME:actor}")`. Vedi placeholder runtime in
# `praxis_executor._resolve_runtime_placeholders`.
# NB: `indices` (ex 16° OBJECT, ADR 0086) e' stato declassato a qualifier
# di modalita' il 5/5/2026: la lettura `verbo_oggetto[_modalita']` e' piu'
# trasparente per LLM medium quando il mezzo di ricerca e' un derivato
# persistente del dominio principale (es. `find_images_indices`,
# `create_images_indices`). Vedi nota in QUALIFIERS sotto.

# Qualifier opzionali. TRE famiglie (collassate da 5 il 5/5/2026):
# 1) FORMATO/codifica file (csv, pdf, gz, ...) — restringe il tipo di sorgente.
# 2) MODALITA' — sotto-unita' o specializzazione di come l'azione opera dentro
#    all'oggetto. Include:
#      - operazione specifica entro il dominio (size, format, loc, similar);
#      - granularita' di dominio (lines, paragraphs, sentences, pages, segments);
#      - mezzo astratto persistente (indices: derivato per query veloci,
#        es. `find_images_indices`, `create_images_indices`).
# 3) SAFETY POLICY — categorie del dominio `signatures` (blacklist, whitelist,
#    graylist, forbidden, seed, diff, sanity, command, reversibility,
#    promotion, candidates).
QUALIFIERS = (
    # Famiglia 1 — Formato file
    "csv", "xlsx", "ocr", "zip", "pdf", "xml", "html", "json", "text",
    "gz", "tar", "video", "audio", "image", "hash",
    # Famiglia 1 — Formato file: applicazioni cloud strutturate (24/5/2026).
    # `spreadsheet` = formato Google Sheets / generico foglio elettronico
    # online (output: matrice di righe/celle, range A1). Distinto da `xlsx`
    # (file binario Excel locale): qui il dato sta su un servizio remoto,
    # l'identificatore e' uno `spreadsheet_id`, non un path.
    # `doc` = formato Google Docs (output: testo flow corrente, document_id).
    # Distinto da `text`/`pdf`/`html` (formati file): qui il "file" e' un
    # documento di un editor remoto. Compat: solo files (cloud-resident).
    "spreadsheet", "doc",
    # Famiglia 2 — Modalita': operazione specifica entro il dominio
    # (introdotti per change_* / compute_*_loc / order_*).
    "size", "format", "loc", "similar",
    # Famiglia 2 — Modalita': stato "vuoto/sotto-soglia" del dominio.
    # (12/5/2026, ADR 0127). Modifica l'operazione del verbo per
    # ritornare entita' con proprieta' di vuoto/disponibilita'. Args
    # canonical: `size` (str unit-aware). Per-domain:
    #  - events:   find_events_empty(size="1hour")   → slot >=1h liberi (gap)
    #  - files:    find_files_empty(size="10KB")     → file <=10KB
    #  - messages: find_messages_empty(size="100chars") → body <=100 char
    #  - dirs:     find_dirs_empty()                 → cartelle vuote
    # Generalizzabile cross-dominio (§7.3): una sola semantica, niente
    # analogie forzate fra dominii. Pattern propose-and-fire (ADR 0127)
    # idiomatico: find_<obj>_empty → get_inputs(choice) → set_<obj>.
    "empty",
    # Famiglia 2 — Modalita': granularita' di dominio (3/5/2026). Es.
    # `filter_texts_lines` filtra a livello riga; `filter_pdfs_pages`
    # a livello pagina; `filter_audios_segments` a livello segmento.
    "lines", "paragraphs", "sentences", "pages", "segments",
    # Famiglia 2 — Modalita': mezzo astratto persistente (ADR 0086,
    # rinominato 5/5/2026). Quando il verbo opera tramite un derivato
    # persistente del dominio principale (vettori CLIP, embedding ArcFace,
    # coordinate EXIF, threading messages, perceptual hash). Esempi:
    # `find_images_indices(query_text="mare")` interroga l'indice di foto;
    # `create_images_indices(idx="scene")` lo costruisce/aggiorna;
    # `find_messages_indices` (in prospettiva) per ricerca semantica mail.
    # ECCEZIONE SEMANTICA controllata: il verbo opera SUL mezzo (indice)
    # ma l'output ritorna entries del DOMINIO principale (images, messages,
    # ...). Lista chiusa: oggi solo `indices`; aggiunte future seguono lo
    # stesso pattern (es. `cache`, `histogram` se emergeranno).
    "indices",
    # Storico append-only di esecuzioni del dominio tasks.
    "history",
    # Famiglia 2 — Modalita': origine ricerca = web pubblico (24/5/2026).
    # Distingue executor che interrogano servizi web (Google Vision Web
    # Detection, web search engines, public APIs) da quelli che operano
    # su dati locali (filesystem, `_indices`). Pattern cross-domain:
    # `find_images_web` (reverse image search via Cloud Vision),
    # `find_urls` (web crawl, qualifier omesso perche' urls implica web),
    # future: `find_persons_web`, `find_news_web`. Governance §2.2:
    # necessario (nessun qualifier copre origine=web), generale
    # (applicabile a tutti gli oggetti ricercabili sul web pubblico),
    # comprensibile (termine universale).
    "web",
    # Famiglia 3 — Safety policy (ADR 0071, oggetto `signatures`):
    # find_signatures_blacklist, write_signatures_whitelist, ecc.
    "blacklist", "whitelist", "graylist", "forbidden", "seed",
    "sanity", "command", "reversibility",
    # Diff di seed verso DB (find_signatures_seed_diff).
    "diff",
    # Candidati a promozione graylist→whitelist
    # (find_signatures_promotion_candidates → qualifier "promotion").
    "promotion", "candidates",
    # Famiglia 4 — Provider (ADR 0136): backend non-default come qualifier.
    # `_metnos` (default) e' omesso; i provider espliciti sono token vocab.
    # `github` (ADR 0141): issues/pulls/messages/tasks/files/dirs su GitHub REST.
    # `google_workspace` (ADR 0123): gmail/calendar/drive. Erano gestiti solo a
    # livello skill importer → `validate_name(read_events_google_workspace)`
    # FALLIVA (bug latente scoperto 26/6: i vendorizzati firmati bypassano la
    # validazione, ma un provider SINTETIZZATO sarebbe rifiutato). Ora membri
    # espliciti della famiglia provider (vedi PROVIDER_SUFFIXES sotto).
    "github",
    "google_workspace",
    # `google_photos` (spec Google Photos): dominio `images` app-created
    # (upload/album/find/download). Provider a se', NON un backend di `files`.
    "google_photos",
)

# ── Famiglia PROVIDER (asse ortogonale §2.2) ─────────────────────────────
# Sottoinsieme dei QUALIFIERS che identificano un BACKEND non-default (stesso
# intento, provider diverso) — distinti dai qualifier-MODALITA' (ocr/csv/...)
# che cambiano l'intento. È l'asse su cui vale la regola di FOCUSING: un nome
# `verbo_oggetto_<provider>` ESTENDE (contiene) il generico `verbo_oggetto` →
# col marker provider presente, il generico è escluso (provider_gate_names).
# Un qualifier-modalita' NON ha questa relazione (read_files_ocr ≠ read_files).
#
# FONTE UNICA dell'IDENTITÀ provider: l'identità (quali provider esistono) è
# vocabolario chiuso e vive QUI. `detection_lexicon_seed` DERIVA da qui le chiavi
# di `provider.markers` e vi aggiunge solo i marker NL i18n (i valori). Zero
# duplicazione: vocab è puro (0 import, radice); il seed importa vocab (direzione
# sicura, mai il contrario). `_metnos` (default) è OMESSO dal nome → non qui.
PROVIDER_SUFFIXES = frozenset({"github", "google_workspace", "google_photos"})

# Oggetti CARRIER-di-files (§2.2, nota sopra OBJECTS): domini di contenuto
# specializzato che per le OPS GENERICHE (enumerare/spostare per path) NON
# duplicano `files` — «carica le foto di /tmp/dir» enumera FILE di una
# cartella (find_files), non interroga l'indice semantico foto. Consumata da
# `engine.dispatch._fs_equivalent` (un producer files/dirs soddisfa un intent
# images/texts: la guard align non lo riscrive — turn a4f1f12c) e concettualmente
# dal remap routability dell'intent_extractor (carrier images/texts→files).
FILE_CARRIER_OBJECTS = frozenset({"images", "texts"})

# Mappa provider → SKILL che ne custodisce credenziali/CLI (identità chiusa,
# stessa natura di PROVIDER_SUFFIXES: dato, SoT unica — guard
# `test_provider_skills_cover_suffixes`). `google_photos` usa la STESSA skill
# google-workspace (spec Photos D4: stesso client secret/token, scope aggiunti).
# Consumata dalla capability canonica `provider:access` e da
# `sandbox.invocation_skills`: lo stesso binding effettivo (1) rende
# l'invocazione non eleggibile al device e (2) concede a bwrap la home skill RW
# + rete. I segnali per nome/client restano solo nel ripiego legacy.
PROVIDER_SKILLS = {
    "github": "github",
    "google_workspace": "google-workspace",
    "google_photos": "google-workspace",
}

# ── Qualifier → Object compatibility map (Naming Authority R4) ────────
#
# Mappa quali OBJECTS ammettono ogni qualifier (vocab-validi ma
# semantica corretta). None = cross-domain (qualsiasi object OK).
# Set vuoto = qualifier riservato a una famiglia stretta.
#
# Razionale per famiglia §2.2:
# - SAFETY POLICY (blacklist/whitelist/.../candidates): solo signatures
#   (ADR 0071 — policy shell). `candidates` ammesso anche su proposals
#   per simmetria (introvertiva candidates).
# - GRANULARITA' TESTO (lines/paragraphs/sentences): texts, messages.
#   Pages: anche files (PDF). Segments: anche files (audio/video).
# - INDICES (mezzo persistente, ADR 0086+0117): images, messages, texts,
#   persons (domini con embedding/hash perceptual).
# - LOC: solo files (lines of code).
# - FORMATO/CODIFICA: files primario, messages secondario (allegati,
#   formati strutturati json/xml/html), urls per html, images/files per
#   ocr.
# - CROSS-DOMAIN (size/empty/format/similar): None = ammessi ovunque.
QUALIFIER_OBJECT_COMPAT = {
    # Safety policy — signatures (con candidates su proposals)
    "blacklist": frozenset({"signatures"}),
    "whitelist": frozenset({"signatures"}),
    "graylist": frozenset({"signatures"}),
    "forbidden": frozenset({"signatures"}),
    "seed": frozenset({"signatures"}),
    "diff": frozenset({"signatures"}),
    "sanity": frozenset({"signatures"}),
    "command": frozenset({"signatures"}),
    "reversibility": frozenset({"signatures"}),
    "promotion": frozenset({"signatures"}),
    "candidates": frozenset({"signatures", "proposals"}),
    # Provider GitHub (ADR 0141): issues/pulls + messages (commenti/review) +
    # tasks (workflow runs). +files/dirs (25/6/2026, supersede della riga
    # «NON files/dirs» di ADR 0141): un repo È file e cartelle; gli executor
    # repo-tree/contents (find_files_github=git/trees ricorsivo,
    # read_files_github=contents, list_dirs_github=contents single-dir) li
    # espongono. Coerente con l'asse provider §2.2 (backend non-default nel
    # qualifier). Origine: turn 6ec02267 «quanti file su github nel repo».
    "github": frozenset({"issues", "pulls", "messages", "tasks", "files", "dirs"}),
    # Provider Google Workspace (ADR 0123): gmail (messages), calendar (events,
    # calendars), drive (files), contacts (contacts, persons). Asse provider §2.2,
    # gemello di github. Object ammessi = i domini coperti dalla skill.
    "google_workspace": frozenset({"messages", "events", "calendars", "files",
                                   "contacts", "persons"}),
    # Provider Google Photos (spec Google Photos): SOLO l'object `images`
    # (upload/album/find/download del creato-da-app). Non tocca `files`.
    "google_photos": frozenset({"images"}),
    # Granularita' testo
    "lines": frozenset({"texts", "messages"}),
    "paragraphs": frozenset({"texts", "messages"}),
    "sentences": frozenset({"texts", "messages"}),
    "pages": frozenset({"texts", "files"}),
    "segments": frozenset({"texts", "messages", "files"}),
    # Indices (mezzo persistente)
    "indices": frozenset({"images", "messages", "texts", "persons"}),
    "history": frozenset({"tasks"}),
    # LOC
    "loc": frozenset({"files"}),
    # Formato/codifica
    "csv": frozenset({"files", "messages"}),
    "xlsx": frozenset({"files"}),
    "ocr": frozenset({"files", "images"}),
    "zip": frozenset({"files"}),
    "pdf": frozenset({"files", "messages", "urls"}),
    "xml": frozenset({"files", "messages"}),
    "html": frozenset({"files", "messages", "urls"}),
    "json": frozenset({"files", "messages"}),
    "text": frozenset({"files", "messages"}),
    "gz": frozenset({"files"}),
    "tar": frozenset({"files"}),
    "video": frozenset({"files"}),
    "audio": frozenset({"files"}),
    "image": frozenset({"files"}),
    "hash": frozenset({"files", "signatures"}),
    "spreadsheet": frozenset({"files"}),
    "doc": frozenset({"files"}),
    # Cross-domain (None: ammessi su qualsiasi object)
    "size": None,
    "empty": None,
    "format": None,
    "similar": None,
    # `web` = origine=web pubblico, dichiarata general/cross-domain in QUALIFIERS
    # (find_images_web reale, future find_persons_web/find_news_web). Senza
    # questa riga validate_name R4 RIFIUTAVA find_images_web (bug drift 20/6).
    "web": None,
}


def qualifier_compatible(qualifier: str, obj: str) -> bool:
    """True se `qualifier` e' semanticamente ammesso per `obj`.
    §7.9 deterministico. False se la coppia viola il dominio."""
    if qualifier not in QUALIFIER_OBJECT_COMPAT:
        # Qualifier fuori vocab: lascia a validate_name la rejection
        # con l'errore "qualifier not in vocab §2.2".
        return False
    compat = QUALIFIER_OBJECT_COMPAT[qualifier]
    return compat is None or obj in compat


def qualifiers_for_object(obj: str) -> list[str]:
    """Ritorna i qualifier ammessi per `obj` (per generazione GBNF
    object-specific). Include i cross-domain (None) + quelli che
    elencano `obj` nel proprio set."""
    return sorted(
        q for q, compat in QUALIFIER_OBJECT_COMPAT.items()
        if compat is None or obj in compat
    )


# Categorie semantiche (descrittive, usate dai prompt synt stage 1).
ACTION_CATEGORIES = {
    "read": "I/O fs", "write": "I/O fs", "move": "I/O fs",
    "delete": "I/O fs", "create": "I/O fs",
    "find": "discovery", "list": "discovery",
    "filter": "transform", "sort": "transform", "group": "transform",
    "classify": "transform",
    "get": "metadata", "set": "metadata",
    "send": "network",
    "describe": "stat/output", "render": "stat/output",
    "extract": "decomp/pack", "compress": "decomp/pack",
    "compute": "calcolo", "compare": "confronto",
    "change": "trasformazione",
    "order": "ordinamento-persistente",
    "share": "outbound-consent",
    "open": "web-session", "login": "web-session", "act": "web-session",
}

# ── Classificazione operativa per il runtime ──────────────────────────

# Verbi PRODUCER: producono entries da fonti esterne (FS/IMAP/web/scalari
# di sistema). Non hanno bisogno di precursor automatico nel prefilter.
# `find` sussume anche il concetto di "verifica esistenza" (lista vuota = non
# presente, lista non vuota = presente + dettaglio): `check` non e' verbo
# canonico, va mappato a `find`.
PRODUCER_VERBS = frozenset({"read", "find", "list", "get"})

# Verbi la cui ASSENZA dal framework — se RICHIESTI dalla query — segnala una
# decomposizione INCOMPLETA (guard coverage §4.3/§2.8, usato da dispatch +
# decomposer). Producer (senza i dati la pipeline è monca) + side-effecting
# espliciti (l'utente li ha chiesti: «mandami»/«crea»/«salva» → vanno portati a
# termine). ESCLUSI i soft (describe/get/classify/sort/filter): si fondono nel
# final_answer o sono trasformatori, non azioni dovute. Multilingue: i verbi
# sono CANONICI (detect_canonical_verbs_all normalizza già IT+EN).
COVERAGE_REQUIRED_VERBS = PRODUCER_VERBS | frozenset({
    "send", "create", "write", "move", "delete", "share",
    # sites F1: azioni web esplicite da portare a termine (open precursore di
    # login; login autentica). `act` (F2) si aggiunge quando l'executor esiste.
    "open", "login"})

# Verbi PROCESSOR: trasformano una lista gia' presente nello scratchpad
# (input via `from_step`), non producono dati nuovi. Conseguenze runtime:
# (a) `_collect_truncation_notices` NON emette notice user-facing per questi
#     verbi: il loro `truncated:True` e' metadata per il PLANNER (pattern
#     cap_expand §2.11), non un evento di troncamento del dato sorgente —
#     il dato sorgente e' gia' stato annunciato dal producer upstream.
# (b) prefilter precursor injection non si applica (input arriva da from_step).
PROCESSOR_VERBS = frozenset({
    "describe", "classify", "filter", "sort", "group", "compute", "compare",
})

# Verbi che lasciano residuo permanente (modifiche reali). Il vaglio
# potrebbe escludere o richiedere conferma esplicita.
DESTRUCTIVE_VERBS = frozenset({"move", "delete", "send", "write", "extract", "create", "share"})

# Verbi candidati per precursor injection (chi può "popolare entries"
# upstream di un consumer come describe/filter/move/...).
PRECURSOR_VERBS = ("read", "find", "list", "get")

# Verbo MUTATING di default per ogni OBJECT (ADR 0129, 14/5/2026):
# usato da `detect_implicit_actions` per il pattern intent-implicit.
# Es. user dice «proponi appuntamento» (sostantivo "appuntamento" -> object
# "events") senza verbo create esplicito; il default mutating per `events`
# e' `create`, quindi l'azione implicita inferita e' `create_events`.
# Lookup tabellare §7.9 — niente LLM, niente case-patch per dominio.
# Tabella chiusa allineata a OBJECTS §2.2. None = nessun mutating default
# per quel object (read-only-by-construction).
OBJECT_DEFAULT_MUTATING_VERB: dict[str, str | None] = {
    "files":      "write",
    "dirs":       "create",
    "packages":   None,        # install/upgrade non sono verbi vocab §2.2
    "messages":   "send",
    "events":     "create",
    "contacts":   "set",
    "places":     None,        # places sono entita' lookup-only
    "processes":  None,        # kill/start sono fuori vocab §2.2
    "urls":       None,        # urls sono fetched, non creati dall'utente
    "numbers":    None,        # numeri sono compute-only
    "images":     "create",
    "signatures": "set",
    "texts":      "write",
    "proposals":  "set",       # approve/reject mappa a set (state upsert)
    "persons":    "set",       # set_persons (enroll); delete_persons separato
    "tasks":      "create",    # create_tasks (scheduler v2 ricorrenti)
    "inputs":     None,        # get_inputs e' lookup interno, no mutating
    "approval":   None,        # get_approval: gate UI, nessun mutating proprio
    "credentials": "set",
    "entries":    None,        # entries sono meta-oggetto in-memory
    "lists":      None,        # coppia di liste in-memory, processor-only
    "skills":     "set",       # abilita/disabilita skill installate
    # Provider objects (github/calendar): None = mention ≠ mutazione (un "le
    # issue su github" è read/list, non un set) → niente orphan-injection
    # spuria. Completano la tabella vs OBJECTS (drift 21/6).
    "issues":     None,
    "pulls":      None,
    "calendars":  None,
    # sites: una mention nuda («il sito X») NON implica un verbo mutante (open e
    # login vanno chiesti esplicitamente) → None, niente orphan-injection.
    "sites":      None,
}


# Verbi safe-by-construction: read-only / pure-compute / output-only.
# Il vaglio puo' approvarli per costruzione senza chiamare l'LLM giudice
# (ADR 0107). Esclude tutto cio' che ha side effect (scrittura locale,
# rete uscente, exec, modifica forma persistente). I verbi destructive
# tipo write/move/delete/send/create/change/extract/render restano
# soggetti al giudice completo.
SAFE_VERBS = frozenset({
    "read", "find", "get", "list", "filter",
    "describe", "classify", "compute", "compare",
    "sort", "group",
})


# ── System verbs riservati (the design guide §2.2) ───────────────────────────
# Verbi-meta di sistema fuori dai verbi canonici. Discriminano la
# chiusura del turno (`undo`), l'esecuzione di shell privilegiata
# (`admin`), la sintesi al volo di nuovi executor (`synthesize`) o la
# delega a frontier LLM esterno (`consult`). Stage 1 NAMING NON li
# propone come azione di nuovi executor: rifiuta il name se inizia con
# uno di questi verbi. Reservato a builtin runtime e a `consult_frontier`
# (l'unico executor utente-domain che usa `consult`).
#
# - admin   -> verb-unique builtin (system/admin.py)
# - undo    -> handcrafted executor `undo_last_turn`
# - synthesize -> synth runtime (synt_multistage), nessun executor utente
# - consult -> handcrafted executor `consult_frontier` (delega a frontier
#              LLM esterni: Opus/Sonnet/GPT-5)
SYSTEM_VERBS = frozenset({"admin", "undo", "synthesize", "consult"})

# Nomi runtime esatti ammessi fuori dalla grammatica user-domain. Non sono
# token proponibili dal synt: l'eccezione e' chiusa sul nome completo.
SYSTEM_EXECUTOR_NAMES = frozenset({"undo_last_turn", "consult_frontier"})

# Operazioni intrinsecamente singolari ratificate da ADR 0002. Anche queste
# sono eccezioni chiuse sul nome completo; non aprono gli oggetti `now` o
# `location` alla generazione di nuovi executor.
SINGULAR_EXECUTOR_NAMES = frozenset({"get_now", "get_location"})

# ── MAPPING bilingue per stage 1 di synt + intent extractor ───────────
# Per ogni verbo: sinonimi IT, sinonimi EN, confine semantico (1 frase).
# Stage 1 di synt LO ESPONE INTERAMENTE nel prompt. L'intent extractor
# lo USA INTERAMENTE per la disambiguazione cross-language.
ACTION_MAPPING = {
    "read": {
        "it": ["leggi", "apri", "visualizza", "mostra-il-contenuto", "conta-occorrenze-in",
                "scarica-contenuto", "richiedi-da-url"],
        "en": ["read", "open", "view", "show-contents", "count-occurrences-in",
                "download-content", "fetch-from-url"],
        "boundary": {
            "it": "Materializza il CONTENUTO/corpo GREZZO di un oggetto identificato per id/path/URL: byte, testo, JSON, forma decodificata. Input = id/path/URL NOTO (oggetto `urls` SOLO se la query contiene un url/endpoint esplicito http(s); altrimenti l'oggetto è quello di dominio: files/messages/numbers/…). side_effect=fetch_content, illimitato, tipizzato per formato (read_*_csv/_pdf/_ocr). NON describe (riassumi/sintetizza/aggrega = describe: read consegna il grezzo, non lo riassume) · NON get (snapshot/metadata, senza corpo) · NON list (solo nomi) · NON find (non sai ancora quali).",
            "en": "Materialize the RAW CONTENT/body of an object identified by id/path/URL: bytes, text, JSON, decoded form. Input = KNOWN id/path/URL (object `urls` ONLY if the query contains an explicit http(s) url/endpoint; otherwise the object is the domain one: files/messages/numbers/…). side_effect=fetch_content, unbounded, format-typed (read_*_csv/_pdf/_ocr). NOT describe (summarize/aggregate = describe: read delivers the raw, doesn't summarize) · NOT get (snapshot/metadata, no body) · NOT list (names only) · NOT find (you don't know which yet).",
        },
    },
    "write": {
        "it": ["scrivi", "salva", "sostituisci-il-contenuto", "sovrascrivi",
                "carica", "caricare"],
        "en": ["write", "save", "replace-contents", "overwrite", "persist",
                "upload"],
        "boundary": {
            "it": "Crea o sostituisce contenuto di un file specifico; incluso l'UPLOAD di file/foto verso un servizio o provider («carica su drive/google photos»). NON send (send = destinatari o canali umani: mail, chat, notifiche).",
            "en": "Creates or replaces the content of a specific file; including the UPLOAD of files/photos to a service or provider (\"upload to drive/google photos\"). NOT send (send = human recipients or channels: mail, chat, notifications).",
        },
    },
    "create": {
        "it": ["crea-cartella", "crea-dir", "nuova-directory", "mkdir",
                "costruisci-indice", "crea-indice", "indicizza"],
        "en": ["create-folder", "create-directory", "mkdir", "make-dir",
                "build-index", "create-index", "index"],
        "boundary": "Creazione di contenitori (dir) o di derivati persistenti del dominio (indici). I file con contenuto vanno a write. Per gli indici: `create_<dom>_indices` (es. create_images_indices) costruisce o aggiorna l'indice del dominio target; il qualifier `_indices` (modalita') segnala che il mezzo di ricerca e' un derivato persistente del dominio.",
    },
    "move": {
        "it": ["sposta", "rinomina", "muovi", "cambia-estensione", "sposta-in"],
        "en": ["move", "rename", "relocate", "change-extension"],
        "boundary": "Cambia path o nome di file/dir esistente. Reversibile via swap_src_dst.",
    },
    "delete": {
        "it": ["cancella", "elimina", "rimuovi", "butta-via"],
        "en": ["delete", "remove", "erase", "drop", "discard"],
        "boundary": "Distruzione (irreversibile o reversibile con backup blob).",
    },
    "find": {
        "it": ["trova", "cerca", "cerca-per-nome", "cerca-pattern", "localizza",
                "cerca-i-file-che", "cerca-un-pacchetto", "cerca-un-luogo",
                "cerca-foto-simili", "cerca-volti", "ricerca-semantica"],
        "en": ["find", "locate", "search", "search-by-name", "search-pattern",
                "glob", "lookup-by-pattern", "semantic-search", "find-similar-photos",
                "search-faces"],
        "boundary": {
            "it": "DISCOVERY: cerca su una sorgente di verità per PATTERN/QUERY (`patterns`, `query`, `name`). Non sai in anticipo cosa esiste; lo scopri. Output = lista che matcha (vuota = assente; sussume la verifica-esistenza E il conteggio «quanti/conta X»: enumeri gli ELEMENTI per contarli → find_<dom>, oggetto = l'elemento). NON get (get prende id NOTI, non un pattern di ricerca; «quanti file in DIR» = find_files, non get) · NON filter (filter resta in memoria su una lista già in mano; find va alla sorgente). Eccezione controllata `find_<dom>_indices` (ADR 0086): `_indices` = il MEZZO di ricerca è un indice persistente, l'output resta entries del dominio (find_images_indices → foto).",
            "en": "DISCOVERY: search a source of truth by PATTERN/QUERY (`patterns`, `query`, `name`). You don't know in advance what exists; you discover it. Output = matching list (empty = absent; subsumes existence-check AND counting «how-many/count X»: you enumerate the ELEMENTS to count them → find_<dom>, object = the element). NOT get (get takes KNOWN ids, not a search pattern; «how many files in DIR» = find_files, not get) · NOT filter (filter stays in-memory on a list already held; find hits the source). Controlled exception `find_<dom>_indices` (ADR 0086): `_indices` = the search MEDIUM is a persistent index, output stays domain entries (find_images_indices → photos).",
        },
    },
    "list": {
        "it": ["elenca", "lista", "mostra-il-contenuto-di", "dammi-l'elenco-di"],
        "en": ["list", "enumerate", "show-contents-of", "ls"],
        "boundary": {
            "it": "ENUMERA i membri di un container SENZA scaricarne il contenuto (nomi file in una dir, folder IMAP). NON read (read scarica il contenuto dei membri) · NON find (find prende un pattern; list prende il container).",
            "en": "ENUMERATE the members of a container WITHOUT fetching content (file names in a dir, IMAP folders). NOT read (read pulls the members' content) · NOT find (find takes a pattern; list takes the container).",
        },
    },
    "filter": {
        "it": ["filtra", "tieni", "scarta", "escludi", "seleziona", "subset",
                "estrai-righe", "estrai-da-testo"],
        "en": ["filter", "keep", "discard", "exclude", "select", "subset",
                "extract-lines", "extract-from-text"],
        "boundary": {
            "it": "RIDUCE una lista PREESISTENTE di entries (via `from_step:N` o argomento `entries`) al sottoinsieme che soddisfa un predicato (regex, range, soglia). Pure compute, niente I/O verso sorgenti di sistema. NON get/find (quelli prendono dati NUOVI; filter ha già la lista in mano). Copre anche selezionare un sottoinsieme di righe da un testo (filter_texts_lines): è `filter`, non `extract`.",
            "en": "REDUCE a PRE-EXISTING list of entries (via `from_step:N` or the `entries` arg) to the subset matching a predicate (regex, range, threshold). Pure compute, no I/O to system sources. NOT get/find (those fetch NEW data; filter already holds the list). Also covers selecting a subset of text lines (filter_texts_lines): that is `filter`, not `extract`.",
        },
    },
    "sort": {
        "it": ["ordina", "classifica", "top", "primi", "ultimi"],
        "en": ["sort", "rank", "order", "top", "first", "last"],
        "boundary": "Riordina entries per chiave. Opzionale top-K. Pure compute.",
    },
    "group": {
        "it": ["raggruppa", "unisci", "combina", "deduplica",
               "aggrega-per", "partiziona-per"],
        "en": ["group", "merge", "combine", "deduplicate",
               "aggregate-by", "partition-by"],
        "boundary": "Combina uno o piu' flussi di entries, con deduplica opzionale, oppure raggruppa entries per valore di un campo. Pure compute.",
    },
    "classify": {
        "it": ["classifica", "categorizza", "etichetta", "assegna-categoria"],
        "en": ["classify", "categorize", "label", "assign-category"],
        "boundary": "Aggiunge un'etichetta a ogni entry secondo un criterio (LLM-augmented).",
    },
    "get": {
        "it": ["ottieni", "dimmi", "dammi", "che-ora-e", "dove-sono", "metadati-di",
                "leggi-stato", "elenca-processi", "snapshot"],
        "en": ["get", "obtain", "tell-me", "give-me", "what-time", "where-am-i",
                "metadata-of", "read-state", "list-processes", "snapshot"],
        # DISGIUNZIONE (24/6): rimossi `scarica/download/fetch/richiedi-da-url/
        # GET-http/leggi-questi-paths` → l'asse ratificato li assegna a `read`
        # (fetch del CONTENUTO/body). Restavano qui per la vecchia doctrine
        # "HTTP GET=get_urls", ora superata. Token sotto UN solo verbo (no
        # contaminazione lessicale, no priming d'ordine nei prompt).
        "boundary": {
            "it": "Ottiene uno SNAPSHOT per IDENTIFICATORI NOTI (paths, signatures, lat/lon, pid) OPPURE snapshot completo del dominio (filtri accessori `user`/`pid`/`top=N`): metadata/attributi/scalare freschi, SENZA il corpo/contenuto. side_effect=fetch_metadata|none, economico, agnostico al formato. Input = identificatori o nulla (=tutto), MAI un pattern di ricerca. NON read (read scarica il CORPO/contenuto: il JSON di una URL, il testo di un file → read) · NON find (find prende un pattern; get prende id o nulla) · NON filter (filter riduce una lista già in mano).",
            "en": "Retrieve a SNAPSHOT for KNOWN identifiers (paths, signatures, lat/lon, pid) OR a whole-domain snapshot (accessory filters `user`/`pid`/`top=N`): fresh metadata/attributes/scalar, WITHOUT the content body. side_effect=fetch_metadata|none, cheap, format-agnostic. Input = identifiers or nothing (=all), NEVER a search pattern. NOT read (read pulls the BODY/content: a URL's JSON, a file's text → read) · NOT find (find takes a pattern; get takes ids or nothing) · NOT filter (filter reduces a list already held).",
        },
    },
    "set": {
        "it": ["imposta", "configura", "set", "modifica-il-valore",
               "chiudi", "riapri", "assegna", "etichetta", "cambia-stato"],
        "en": ["set", "configure", "update-value",
               "close", "reopen", "assign", "label", "change-state"],
        "boundary": {
            "it": "Upsert idempotente di STATO/labels/assignee/metadata di un'entità ESISTENTE (config locale O entità remota), per IDENTIFICATORE noto: il record resta, cambia un suo campo di stato. Es. «chiudi/riapri la issue» (state=closed/open), «assegna», «etichetta», impostare una config. side_effect=state_update, reversibile via diff/stato-precedente. NON delete (delete DISTRUGGE il record; set lo lascia vivo e modificabile — «chiudi»≠«cancella») · NON change (change trasforma FORMA/contenuto: resize/convert/merge; set tocca STATO/labels, non i pixel/il corpo) · NON create (create fa un'entità NUOVA; set modifica una esistente).",
            "en": "Idempotent upsert of STATE/labels/assignee/metadata of an EXISTING entity (local config OR remote entity), by KNOWN identifier: the record stays, a status field changes. E.g. «close/reopen the issue» (state=closed/open), «assign», «label», set a config. side_effect=state_update, reversible via diff/prior-state. NOT delete (delete DESTROYS the record; set keeps it alive and editable — «close»≠«delete») · NOT change (change transforms FORM/content: resize/convert/merge; set touches STATE/labels, not pixels/body) · NOT create (create makes a NEW entity; set modifies an existing one).",
        },
    },
    "send": {
        "it": ["invia", "manda", "mandami", "spedisci", "inoltra", "publica",
               "notifica", "notificami", "avvisami", "scrivimi"],
        "en": ["send", "deliver", "forward", "publish",
               "email", "notify", "message", "post", "tell"],
        "boundary": {
            "it": "Side-effect remoto verso DESTINATARI o canali (mail SMTP, push, webhook, messaggio). NON l'upload di file/foto a un servizio di storage («carica su drive/photos» → write). Irreversibile.",
            "en": "Remote side-effect towards RECIPIENTS or channels (SMTP mail, push, webhook, message). NOT uploading files/photos to a storage service (\"upload to drive/photos\" → write). Irreversible.",
        },
    },
    "describe": {
        "it": ["riassumi", "sintetizza", "descrivi", "punti-importanti", "panoramica"],
        "en": ["describe", "summarize", "synthesize", "highlights", "overview"],
        "boundary": "Insight aggregato/condensato di una lista (LLM-augmented). Opposto di get.",
    },
    "render": {
        "it": ["mostra", "fammi-vedere", "visualizza", "format-come"],
        "en": ["render", "show", "display", "format-as"],
        "boundary": "Format di dati gia' disponibili (markdown/html/json). Non prende dati nuovi.",
    },
    "extract": {
        "it": ["scompatta", "decomprimi", "estrai-da-archivio", "unzip", "untar",
               "estrai-record", "estrai-strutturati", "ricava-dati", "parsa"],
        "en": ["extract", "unpack", "decompress", "unzip", "untar",
               "extract-records", "structured-extract", "parse-out"],
        "boundary": "Tira fuori STRUTTURA incapsulata in un contenitore. Due usi (§2.2, allargato 3/6): (1) decompressione archivi zip/tar/gz → `extract_files`; (2) RECORD STRUTTURATI da testo NON strutturato (web, mail, pdf) → `extract_entries` (es. eventi {summary,start,end}, voci di spesa). NON: «estrai righe da un testo» = `filter` (filter_texts_lines); «estrai campi da entries GIÀ strutturate» = `get`; «estrai testo GREZZO da PDF/HTML» = `read` (read_files_pdf/html). Differenza con (2): qui il testo è libero e produci record TIPIZZATI nuovi, non selezioni/leggi.",
    },
    "compress": {
        "it": ["comprimi", "archivia", "zippa", "gzippa", "crea-archivio"],
        "en": ["compress", "archive", "zip", "gzip", "pack", "bundle"],
        "boundary": "Crea archivio compresso da file/dir.",
    },
    "compute": {
        "it": ["calcola", "valuta", "risolvi", "fai-il-conto", "somma", "calcola-l'hash"],
        "en": ["compute", "evaluate", "calculate", "eval-expression", "hash", "checksum"],
        "boundary": "Calcolo deterministico puro (math, eval, unit convert, hashing). Nessun side-effect.",
    },
    "compare": {
        "it": ["confronta", "fai-diff", "differenza", "uguale?", "matcha?"],
        "en": ["compare", "diff", "difference", "equals", "match", "identical"],
        "boundary": "Confronto fra due (o piu') entita' → relazione/diff/booleano.",
    },
    "change": {
        "it": ["cambia", "modifica", "ridimensiona", "ridimensionare", "trasforma",
                "converti", "ruota", "ritaglia", "normalizza", "rinomina-formato"],
        "en": ["change", "modify", "resize", "transform", "convert", "rotate",
                "crop", "normalize", "reformat"],
        "boundary": {
            "it": "Modifica FORMA/parametri/contenuto di un dato senza cambio di natura (resize, convert format, rotate, crop, merge di una PR). L'oggetto resta dello stesso tipo (un'immagine resta immagine: cambiano dimensione/pixel). NON set (set tocca STATO/labels/metadata, non la forma: «chiudi la issue»=set, non change) · NON compress (compress archivia in container) · NON render (render = format di OUTPUT).",
            "en": "Modify the FORM/parameters/content of a datum without changing its nature (resize, convert format, rotate, crop, merge a PR). The object stays the same type (an image stays an image: size/pixels change). NOT set (set touches STATE/labels/metadata, not form: «close the issue»=set, not change) · NOT compress (compress archives into a container) · NOT render (render = OUTPUT format).",
        },
    },
    "order": {
        "it": ["indicizza", "costruisci-indice", "rebuilda-indice", "materializza-ordinamento",
                "prepara-ricerca", "aggiorna-indice"],
        "en": ["order", "index", "build-index", "materialize-order", "prepare-search",
                "refresh-index"],
        "boundary": "Materializza un ordinamento PERSISTENTE del corpus (indice CLIP, perceptual hash, threading messages, ...) per rendere veloci query future. Distinto da sort: sort ordina una lista IN MEMORIA del turno corrente; order produce un derivato durevole su disco. Composizione naturale: order_X_y costruisce/refresha l'indice, find_X_y lo interroga. Refresh tipicamente lazy (al primo find_X_y che lo richiede) o esplicito (utente: 'ricostruisci indice').",
    },
    "share": {
        "it": ["condividi", "concedi-accesso", "invita", "dai-permesso",
                "rendi-pubblico", "rendi-accessibile"],
        "en": ["share", "grant-access", "give-permission", "invite-to",
                "make-public", "make-accessible"],
        "boundary": "OUTBOUND CONSENT (ADR 0128): grant access a una risorsa senza spostarla o duplicarla. Crea un permission/ACL grant remoto sull'entita' identificata da `id`/`ids`. Distinto da `send` (outbound copy o notifica: il destinatario riceve un OGGETTO, es. una mail) e da `set` (upsert idempotente di valori/labels/metadata interni al record). Esempio: condividere un Drive file con un utente = share_files (l'entita' resta nel proprio drive, il destinatario riceve solo un permesso di lettura/scrittura). Reversibile via revoke (`delete_<obj>_permissions_by_id` 5° reverse_pattern §2.3).",
    },
    "open": {
        "it": ["apri-il-sito", "apri-la-sessione", "vai-sul-sito", "apri-il-portale"],
        "en": ["open-site", "open-session", "go-to-site", "open-portal"],
        "boundary": {
            "it": "Apre una SESSIONE browser persistente e autenticabile su un sito (dominio `sites`, spec F1): un contesto con cookie/stato pronto per login e lettura. NON read (read_urls_html legge una pagina PUBBLICA senza stato) · NON get (get_urls = fetch HTTP singolo). Il session_id e' interno, cablato via from_step.",
            "en": "Opens a persistent, authenticatable browser SESSION on a website (`sites` domain, F1): a stateful/cookie context ready for login and reading. NOT read (read_urls_html reads a PUBLIC stateless page) · NOT get (get_urls = single HTTP fetch). The session_id is internal, wired via from_step.",
        },
    },
    "login": {
        "it": ["accedi", "fai-il-login", "autenticati", "entra-nel-sito", "logga"],
        "en": ["login", "log-in", "sign-in", "authenticate"],
        "boundary": {
            "it": "Autentica una SESSIONE `sites` con le credenziali cifrate GIA' salvate nel vault: il BROKER inietta il segreto (l'agente/planner non lo vede mai, §3.2). NON get_inputs (raccolta interattiva di valori dall'utente) · NON set_credentials (salvataggio metadata della credenziale). Usa credenziali gia' presenti; se assenti, fallisce onestamente.",
            "en": "Authenticates a `sites` SESSION with the encrypted credentials ALREADY stored in the vault: the BROKER injects the secret (the agent/planner never sees it, §3.2). NOT get_inputs (interactive value collection from the user) · NOT set_credentials (storing credential metadata). Uses already-stored credentials; if absent, fails honestly.",
        },
    },
    "act": {
        "it": ["compi-azione", "agisci-sul-sito", "clicca", "compila-e-invia"],
        "en": ["act", "do-action", "click", "fill-and-submit"],
        "boundary": {
            "it": "Compie un'AZIONE su una sessione `sites` autenticata (click/compila/invia — F2). Le azioni sensibili (submit/POST/navigazione) passano da un gate di approvazione umana BATCH (§4.2). NON change (trasforma la FORMA di un dato) · NON send (destinatari umani). Solo F2.",
            "en": "Performs an ACTION on an authenticated `sites` session (click/fill/submit — F2). Sensitive actions (submit/POST/navigation) go through a BATCH human-approval gate (§4.2). NOT change (transforms a datum's FORM) · NOT send (human recipients). F2 only.",
        },
    },
}

# NB: `check`/`verifica` NON e' verbo canonico — sussunto da `find`. Una
# query come "controlla se ffmpeg e' installato" si mappa a
# `find_packages(name='ffmpeg')`: lista vuota = non installato, lista non
# vuota = installato + dettaglio (path).


# ── Mapping OBJECT → sezioni planner (Fase C2, 11/5/2026) ─────────────
# Tabella deterministica (the design guide §7.9): dato l'`object` estratto dall'intent
# extractor, ritorna l'elenco di sezioni del planner da iniettare nel prompt
# composto. Caller (`prompt_loader.compose`) usa il selettore via
# `sections_for_object(obj)`; lista vuota = nessun mapping (caller decide
# fallback: includere TUTTE le sezioni).
#
# Convenzione: chiavi = membri di OBJECTS; valori = nomi base (no `.j2`) di
# file in `runtime/prompts/<lang>/planner/sections/`.
#
# Razionale di assegnazione:
# - `messages` → mail: IMAP + Google Workspace mail vivono insieme.
# - `events` → calendar: calendario events (Google Calendar). Workspace
#   non-calendar (drive/sheets/docs/contacts) vivono in sezioni dedicate.
# - `contacts` → workspace/contacts + mail + calendar: rubrica e' usata sia
#   per `to_user` (mail) sia per partecipanti agli eventi (calendar).
# - `urls` → web/search + web/crawl + web/content: tutto il dominio crawler
#   splittato in 3 sub-topic (12/5/2026, asse A refactor).
# - `images`/`signatures` → photos: foto + face index + EXIF/GPS unified.
#   `signatures` ospita anche safety policy (mount/admin) ma quel routing
#   avviene via admin_shell quando l'intent e' shell-imperative; per le
#   query relative ai criteri di firma (find_signatures_*) il routing
#   admin_shell e' piu' pertinente di photos — escolgliamo admin_shell.
# - `processes` → system: top-K processi + health block.
# - `credentials` → admin_shell: gestione token + mount + sudo dipendono
#   dallo store cifrato.
# - `files`/`dirs`/`packages`/`places`/`numbers`/`texts`/`proposals`/`inputs`
#   → [] (no sezione dedicata): coperti dal core (filesystem generico,
#   compute, find_places, get_inputs UI). Il composer fallback aggiunge tutte
#   le sezioni se la lista e' vuota (degrade graceful). Per `files`: non
#   triggeriamo automaticamente workspace/drive|docs sull'object da solo —
#   preferiamo MISS che over-load. La detection workspace passa per
#   l'intent extractor o synonym piu' specifico (skill_vocab_map).
_OBJECT_TO_SECTIONS: dict[str, tuple[str, ...]] = {
    "files": (),                  # generico FS, coperto dal core
    "dirs": (),                   # generico FS, coperto dal core
    "packages": (),               # find_packages: query verbale-deterministica
    "messages": ("mail",),
    "events": ("calendar",),
    "contacts": ("workspace/contacts", "mail", "calendar"),
    "places": (),                 # find_places: globale (con/senza get_location)
    "processes": ("system",),
    "urls": ("web/search", "web/crawl", "web/content"),
    "numbers": (),                # compute scalare, coperto dal core
    "images": ("photos",),
    "signatures": ("admin_shell",),  # safety policy shell + mount
    "texts": (),                  # filter/read text generico, coperto dal core
    "proposals": (),              # admin proposals_cli, no PLANNER routing
    "tasks": ("scheduled_tasks",),  # scheduler v2 ricorrenti + one-shot
    "persons": ("photos",),       # registro nominale, compositive con images
    "inputs": (),                 # dialog UI, gestito dal runtime, no sezione
    "approval": (),               # gate UI (get_approval), no sezione planner
    "credentials": ("admin_shell",),
    "entries": (),                # meta-oggetto runtime, no sezione dedicata
    "lists": (),                  # meta-oggetto bi-lista, no sezione dedicata
    "skills": (),                 # amministrazione catalogo skill
    "issues": (),                 # provider github, no sezione planner dedicata
    "pulls": (),                  # provider github, no sezione planner dedicata
    "calendars": (),              # provider google_workspace, core-only
    # sites (F1): core-only per ora. Gli executor open/login/read/close_sites
    # sono offerti via affinity nel prefilter; una sezione planner dedicata
    # (.j2, dominio Fable) e' un follow-up di qualita' del routing, non un
    # requisito di disponibilita' dei tool.
    "sites": (),
}


def sections_for_object(obj: str | None) -> tuple[str, ...]:
    """Ritorna le sezioni planner attive per un OBJECT.

    `()` (vuota) = nessun mapping noto: il caller (`prompt_loader.compose`)
    decide il fallback (tipicamente: includere TUTTE le sezioni per degrade
    graceful in caso di intent.confidence bassa o object unknown).

    Esempi:
        >>> sections_for_object("messages")
        ('mail',)
        >>> sections_for_object("contacts")
        ('workspace/contacts', 'mail', 'calendar')
        >>> sections_for_object("urls")
        ('web/search', 'web/crawl', 'web/content')
        >>> sections_for_object("files")
        ()
        >>> sections_for_object(None)
        ()
        >>> sections_for_object("unknown_obj")
        ()
    """
    if not obj:
        return ()
    return _OBJECT_TO_SECTIONS.get(obj, ())


def object_is_core_only(obj: str | None) -> bool:
    """True se l'OBJECT e' mappato esplicitamente a NESSUNA sezione, cioe'
    e' interamente coperto dal `_core.j2` (no mail/calendar/photos/web/...).

    Usato da `agent_runtime` per distinguere:
      - object known-core (in mapping con `()`)  → sections=() = core-only
      - object unknown (NOT in mapping)          → sections=None = all
      - object con sezioni esplicite             → sections=[...] mirate

    #H0 19/5/2026 sera: con la distinzione None/(), risparmiamo ~14k tok / step
    sui turn di dominio core (files, dirs, numbers, texts, ...).
    """
    if not obj:
        return False
    return obj in _OBJECT_TO_SECTIONS and _OBJECT_TO_SECTIONS[obj] == ()


# ── L7 admission: imported skill bindings registry (ADR 0125, 12/5/2026) ──
# Ogni skill importato via `metnos-skills import` (ADR 0123) installa N
# executor in `~/.local/share/metnos/executors/_imports/<skill>/<name>/`.
# Quando il PLANNER chiede un synth con intent (verb, object) gia' coperto
# da un imported, e' un FALSE NEED: il bug live 11/5 (`read_appointments`
# proposto invece di chiamare `read_events`) ha bruciato 119s di synt cascade.
#
# L7 = lookup tabellare deterministico (§7.9). Scan al boot di `_imports/`,
# parse `name = <verb>_<object>[_qualifier]`, popola la tabella
# `(verb, object) → [imported_names]`. Auto-discovery non statica perche'
# i bindings cambiano on-the-fly via `metnos-skills import`.
#
# Vedi `imported_bindings_index()` sotto. La tabella e' cached per il
# processo + path-mtime invalidation.


_IMPORTED_BINDINGS_CACHE: dict[str, object] = {
    # cache_key: (path_mtime_signature, dict)
}


def _imports_root() -> "Path":
    """Path della root canonica degli imported skills (Path lazy).

    ADR 0160: new name `skills/`. Back-compat reader: `_imports_roots()`
    ritorna entrambi i path per scan. Manteniamo `_imports_root()` per
    callsite legacy (es. test); il primo path esistente vince.
    """
    import config as _C  # §7.11
    if _C.PATH_SKILLS_USER.exists():
        return _C.PATH_SKILLS_USER
    return _C.PATH_SKILLS_USER_LEGACY


def _imports_roots() -> list:
    """Lista dei root attivi (skills/ + legacy _imports/). ADR 0160."""
    from skills_paths import skill_roots as _sr
    return _sr(include_builtin=False)


def _imports_signature(root_or_roots) -> tuple:
    """Signature delle dir imports per invalidazione cache.

    Accetta sia un singolo `Path` (back-compat) sia una `list[Path]`
    (multi-root post ADR 0160). Max mtime delle subdir + count totale.
    """
    if isinstance(root_or_roots, list):
        roots = root_or_roots
    else:
        roots = [root_or_roots]
    max_mt = 0.0
    n = 0
    for root in roots:
        if not root or not root.exists():
            continue
        try:
            for skill in root.iterdir():
                if not skill.is_dir():
                    continue
                for ex in skill.iterdir():
                    if not ex.is_dir() or not (ex / "manifest.toml").is_file():
                        continue
                    n += 1
                    try:
                        mt = ex.stat().st_mtime
                        if mt > max_mt:
                            max_mt = mt
                    except OSError:
                        pass
        except OSError:
            pass
    return (max_mt, n)


def imported_bindings_index() -> dict[tuple[str, str], list[str]]:
    """Ritorna la mappa `(verb, object) -> [imported_executor_names]`.

    Auto-discovery dei manifest in `~/.local/share/metnos/executors/_imports/`:
    parsa il `name` di ogni manifest TOML, estrae verb+object da name.split('_').
    Risultato cached con invalidazione su mtime (cheap O(N) directory scan).

    L'object qualificato (es. `set_events`, `read_messages_google_workspace`)
    viene mappato per `(verb, object)` ignorando i qualifier oltre il primo
    token-object. Cosi' una richiesta synt per `read_appointments` (verb=read,
    object=appointments) non matcha; ma una richiesta per `read_events`
    (synth_request -> expected_name=read_events, intent: "leggi calendario")
    matcha contro l'imported `read_events`. L'evaluator chiama questo via
    `lookup_imported_for_intent(verb, object_synonyms)` con sinonimi
    (appointments/events) per chiudere il loop.

    Determinismo (§7.9): zero LLM, zero network, una sola scansione fs +
    parse manifest.toml read-only (legge solo il campo `name`).

    Returns:
        dict: chiave tuple (verb, object) lower-case → lista nomi imported.
              Vuoto se `_imports/` non esiste o nessun manifest valido.
    """
    import tomllib
    roots = _imports_roots()
    sig = _imports_signature(roots)
    cached = _IMPORTED_BINDINGS_CACHE.get("index")
    if cached is not None and cached[0] == sig:
        return cached[1]

    index: dict[tuple[str, str], list[str]] = {}
    if not roots:
        _IMPORTED_BINDINGS_CACHE["index"] = (sig, index)
        return index

    skill_dirs = []
    for r in roots:
        if not r.exists():
            continue
        for sd in sorted(r.iterdir()):
            if sd.is_dir():
                skill_dirs.append(sd)
    for skill_dir in skill_dirs:
        for ex_dir in sorted(skill_dir.iterdir()):
            if not ex_dir.is_dir():
                continue
            mf = ex_dir / "manifest.toml"
            if not mf.is_file():
                continue
            try:
                doc = tomllib.loads(mf.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            name = doc.get("name") or ex_dir.name
            parts = name.split("_", 2)
            if len(parts) < 2:
                continue
            verb = parts[0].lower()
            obj = parts[1].lower()
            key = (verb, obj)
            index.setdefault(key, []).append(name)

    _IMPORTED_BINDINGS_CACHE["index"] = (sig, index)
    return index


# Sinonimi cross-language verb -> object atomico, deterministico (no LLM).
# Espanso solo quando un termine nel synth `expected_name` o `intent` deve
# essere ricondotto a un OBJECT canonico ufficiale per il match con
# imported_bindings_index. Lista CHIUSA, esoticismi escalation a Roberto.
_OBJECT_SYNONYMS_IT: dict[str, str] = {
    # IT singolare/plurale → OBJECT canonico
    "appuntamento": "events", "appuntamenti": "events",
    "agenda": "events", "calendario": "events",
    "evento": "events", "eventi": "events",
    "riunione": "events", "riunioni": "events",
    "incontro": "events", "incontri": "events",
    "scadenza": "events", "scadenze": "events",
    "messaggio": "messages", "messaggi": "messages",
    "mail": "messages", "email": "messages", "posta": "messages",
    "contatto": "contacts", "contatti": "contacts",
    "rubrica": "contacts",
    "file": "files", "documento": "files", "documenti": "files",
    "cartella": "dirs", "cartelle": "dirs", "directory": "dirs",
    "pacchetto": "packages", "pacchetti": "packages",
    "processo": "processes", "processi": "processes",
    "luogo": "places", "luoghi": "places", "posto": "places",
    "task": "tasks", "promemoria": "tasks", "timer": "tasks",
    "ricorrente": "tasks", "ricorrenti": "tasks",
    "schedulato": "tasks", "schedulati": "tasks",
    "persona": "persons", "persone": "persons",
    "enrollato": "persons", "enrollati": "persons",
    "enrollata": "persons", "enrollate": "persons",
    "enrolled": "persons", "registrata": "persons", "registrate": "persons",
    "registrato": "persons", "registrati": "persons",
    "volto": "persons", "volti": "persons",
    "viso": "persons", "visi": "persons",
    # GitHub (ADR 0141). NB: "issue"/"pr" sono anche nei marker provider
    # `_github` di tool_grammar; qui mappano l'OBJECT canonico.
    "issue": "issues", "issues": "issues",
    "segnalazione": "issues", "segnalazioni": "issues", "ticket": "issues",
    "pr": "pulls", "pull request": "pulls", "pull": "pulls",
    "merge request": "pulls",
    # approval (gate consenso): solo termini DISTINTIVI; "conferma" resta fuori
    # (troppo generico) — l'intent LLM lo gestisce dal few-shot.
    "approvazione": "approval", "approva": "approval", "approvare": "approval",
    "consenso": "approval", "autorizzazione": "approval", "autorizza": "approval",
}
_OBJECT_SYNONYMS_EN: dict[str, str] = {
    "appointment": "events", "appointments": "events",
    "calendar": "events", "schedule": "events",
    "event": "events", "events": "events",
    "meeting": "events", "meetings": "events",
    "deadline": "events", "deadlines": "events",
    "message": "messages", "messages": "messages",
    "mail": "messages", "email": "messages",
    "contact": "contacts", "contacts": "contacts",
    "file": "files", "document": "files", "documents": "files",
    "folder": "dirs", "directory": "dirs",
    "package": "packages", "packages": "packages",
    "process": "processes", "processes": "processes",
    "place": "places", "places": "places",
    "task": "tasks", "tasks": "tasks", "reminder": "tasks", "timer": "tasks",
    "scheduled": "tasks", "recurring": "tasks",
    "person": "persons", "persons": "persons", "people": "persons",
    "enrolled": "persons", "registered": "persons",
    "face": "persons", "faces": "persons",
    # GitHub (ADR 0141)
    "issue": "issues", "issues": "issues", "ticket": "issues",
    "pull": "pulls", "pulls": "pulls", "pull request": "pulls",
    "pr": "pulls", "merge request": "pulls",
    # approval (consent gate): distinctive terms only; "confirm" stays out
    # (too generic) — the intent LLM handles it from the few-shot.
    "approval": "approval", "approve": "approval", "consent": "approval",
    "authorization": "approval", "authorize": "approval",
}


def canonical_object(token: str | None) -> str | None:
    """Risolve un token (singolare/plurale IT/EN o OBJECT diretto) all'OBJECT
    canonico §2.2. Ritorna None se non riconosciuto.

    Determinismo (§7.9): lookup tabellare puro.

    Esempi:
        >>> canonical_object("appointments")
        'events'
        >>> canonical_object("appuntamenti")
        'events'
        >>> canonical_object("events")
        'events'
        >>> canonical_object("xyz")  # None
    """
    if not token:
        return None
    t = str(token).lower().strip()
    if t in OBJECTS:
        return t
    if t in _OBJECT_SYNONYMS_IT:
        return _OBJECT_SYNONYMS_IT[t]
    if t in _OBJECT_SYNONYMS_EN:
        return _OBJECT_SYNONYMS_EN[t]
    return None


def detect_implicit_actions(query: str,
                             explicit_verbs: list[str] | None = None,
                             ) -> list[dict]:
    """Pattern intent-implicit cross-domain (ADR 0129, 14/5/2026).

    Dato `query` testuale, identifica i sostantivi che realizzano un OBJECT
    §2.2 (via `canonical_object`). Per ognuno controlla se la query contiene
    un verbo MUTATING esplicito per quell'OBJECT. Se NO, emette una entry
    `implicit_action` con strategia di risoluzione (auto/ask/skip) basata
    su confidence deterministica.

    `explicit_verbs` (opzionale): lista dei verbi canonici gia' rilevati
    dalla query (es. da `prefilter.detect_canonical_verbs_all`). Se omesso,
    chiama il detector internamente.

    Ritorna lista (vuota se niente di implicito o conf<0.6). Ogni entry:
        {
            "verb": "<verb>_<object>",      # tool name canonico, es. "create_events"
            "object": "events",
            "noun_token": "appuntamento",   # sostantivo trovato in query
            "verb_canonical": "create",     # azione §2.2
            "confidence": 0.85,
            "strategy": "auto" | "ask" | "skip",
            "rationale": "noun '<token>' -> events, mutating verb 'create' missing",
        }

    Determinismo §7.9: lookup tabellare + token scan, niente LLM.

    Heuristica confidence:
      - base 0.7 per single noun→object match
      - +0.10 se verbo principale e' producer (find/get/list/read) → chiaro
        che l'oggetto e' destinatario di un'azione mutating successiva
      - +0.05 se default mutating verb del object e' reversible (create/set
        coperti da reverse_pattern §2.3)
      - -0.20 se OBJECT.default_mutating e' None (nessun default canonico)
    Strategy:
      - auto:  confidence >= 0.85
      - ask:   0.60 <= confidence < 0.85
      - skip:  confidence < 0.60 (entry non emessa)

    NB: §7.3 niente case-patch per dominio. Tutti gli OBJECTS §2.2 passano
    dallo stesso lookup. Threshold/peso e' parametrico, non hardcoded.
    """
    if not query or not isinstance(query, str):
        return []
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
    except ImportError:
        return []

    tokens = tokenize(query)
    detected_verbs = (explicit_verbs
                      if explicit_verbs is not None
                      else detect_canonical_verbs_all(tokens))
    detected_verbs_set = set(detected_verbs)

    # Bigram-aware verb detection per parole verbo-noun-omonime
    # (commento prefilter.py riga 81-83): «email», «mail», «message»,
    # «text» sono esclusi dai single-token verb lookup perche' usati
    # piu' spesso come nomi. Qui rilevamo i bigrammi tipici dove la
    # parola SI riferisce a un'azione (verbo + pronome 1a persona o
    # complemento esplicito). §7.3 generale: lookup tabellare bilingue.
    _BIGRAM_VERB_HINTS = {
        "send": (
            # EN: verbo+pronome 1a pers
            "email me", "mail me", "message me", "text me", "tell me",
            "let me know", "ping me", "shoot me",
            # IT: forme idiomatiche di notifica
            "mandami una email", "mandami una mail", "mandami un messaggio",
            "mandami un'email", "mandami un'e-mail",
            "fammi sapere", "tienimi al corrente", "tienimi informato",
        ),
    }
    q_low = query.lower()
    for verb_canon, patterns in _BIGRAM_VERB_HINTS.items():
        if any(p in q_low for p in patterns):
            detected_verbs_set.add(verb_canon)

    # Mappa OBJECT → verbi mutating canonici gia' presenti nella query
    # (per fare il check "covered" per ogni object trovato).
    mutating_in_query = {v for v in detected_verbs_set if v in DESTRUCTIVE_VERBS}
    # Boost se TUTTI i verbi della query sono read-only (SAFE_VERBS):
    # significa che la query e' «cerca/proponi/leggi X + manda email» tipica,
    # dove l'azione mutating per X e' implicita e quasi sempre intesa. Se la
    # query gia' contiene un verbo mutating per QUALSIASI altro object (es.
    # «manda email...»), e' marker di pipeline multi-azione, e per l'object
    # rimasto «orfano» il default mutating e' altamente probabile.
    is_producer_principal = (
        all(v in SAFE_VERBS or v in DESTRUCTIVE_VERBS for v in detected_verbs_set)
        and any(v in SAFE_VERBS for v in detected_verbs_set)
    )

    # Condizione necessaria: la query DEVE avere almeno un verbo mutating
    # esplicito per qualcosa (=> e' una pipeline multi-azione). Altrimenti la
    # query e' read-only single-purpose («cerca file pdf») e nessuna azione
    # implicita ha senso — sarebbe falso positivo. Pattern intent-implicit
    # vale solo per «pipeline incompleta», non per «niente da fare».
    if not mutating_in_query:
        return []

    # Reversible defaults — riferiti a reverse_patterns.py §2.3:
    # create/set hanno reverse `delete_<object>_by_id`. send/write/move/share
    # variano caso per caso (move ha swap_src_dst, send non e' reversibile).
    _REVERSIBLE_DEFAULTS = {"create", "set", "move"}

    # Raccolgo gli object detected via canonical_object (preservando l'ordine).
    seen_objects: set[str] = set()
    detected_objects: list[tuple[str, str]] = []  # (noun_token, obj_canon)
    for tok in tokens:
        t = tok.lower().strip()
        obj_canon = canonical_object(t)
        if obj_canon is None or obj_canon in seen_objects:
            continue
        seen_objects.add(obj_canon)
        detected_objects.append((t, obj_canon))

    # Pattern intent-implicit: «pipeline incompleta» = N object distinti > M mutating
    # verbs distinti in query. Cap orfani = max(0, N - M).
    # Candidato orfano = object il cui default_verb NON e' in mutating_in_query
    # (cioe' il LLM non ha gia' fornito un'azione mutating canonica per esso).
    n_objects = len(detected_objects)
    n_mut = len(mutating_in_query)
    orphan_cap = max(0, n_objects - n_mut)
    if orphan_cap == 0:
        return []
    orphan_candidates = [
        (t, obj_canon) for (t, obj_canon) in detected_objects
        if (OBJECT_DEFAULT_MUTATING_VERB.get(obj_canon) or "") not in mutating_in_query
    ]
    orphan_objects = orphan_candidates[:orphan_cap]

    out: list[dict] = []
    for t, obj_canon in orphan_objects:
        default_verb = OBJECT_DEFAULT_MUTATING_VERB.get(obj_canon)
        if not default_verb:
            continue  # object senza mutating canonico (places/urls/...)

        # Calcolo confidence
        conf = 0.70
        if is_producer_principal:
            conf += 0.10
        if default_verb in _REVERSIBLE_DEFAULTS:
            conf += 0.05

        # Strategy
        if conf >= 0.85:
            strategy = "auto"
        elif conf >= 0.60:
            strategy = "ask"
        else:
            continue  # skip

        out.append({
            "verb": f"{default_verb}_{obj_canon}",
            "object": obj_canon,
            "noun_token": t,
            "verb_canonical": default_verb,
            "confidence": round(conf, 2),
            "strategy": strategy,
            "rationale": (
                f"noun '{t}' -> {obj_canon}, mutating verb "
                f"'{default_verb}' missing in query"
            ),
        })

    return out


def lookup_imported_for_intent(verb: str, object_token: str) -> list[str]:
    """Cerca imported executor che coprono l'intent (verb + object).

    Risolve `object_token` via `canonical_object()` per accettare sinonimi
    (es. "appointments" → "events"). Cerca poi nella tabella
    `imported_bindings_index()` per (verb, canonical_object). Ritorna lista
    di executor name (ordinata) — vuota se nessun match.

    L7 admission gate: se ritorna una lista non vuota, il caller
    (synth_request) deve rifiutare il synth con error class
    `duplicates_imported_skill_<name>`.

    Determinismo (§7.9): lookup tabellare puro. Nessun LLM, nessun network.
    """
    if not verb:
        return []
    v = verb.lower().strip()
    obj_canon = canonical_object(object_token)
    if not obj_canon:
        return []
    index = imported_bindings_index()
    hits = index.get((v, obj_canon), [])
    return sorted(hits)


def invalidate_imported_bindings_cache() -> None:
    """Forza ricostruzione dell'indice al prossimo `imported_bindings_index()`.
    Utile nei test per scenari multipli e dopo `metnos-skills import`."""
    _IMPORTED_BINDINGS_CACHE.clear()


# ── Helper di rendering per i prompt ──────────────────────────────────

def render_actions_inline() -> str:
    """Lista verbi separati da virgola: 'read, write, move, ..., check.'"""
    return ", ".join(ACTIONS) + "."


def render_actions_pipe() -> str:
    """Lista verbi separati da pipe: 'read|write|...|check'."""
    return "|".join(ACTIONS)


def render_objects_inline() -> str:
    return ", ".join(OBJECTS) + "."


def render_objects_pipe() -> str:
    return "|".join(OBJECTS)


def render_qualifiers_inline() -> str:
    return ", ".join("_" + q for q in QUALIFIERS) + "."


def render_qualifiers_pipe() -> str:
    return "|".join("_" + q for q in QUALIFIERS)


def render_action_categories_block() -> str:
    """Blocco multilinea delle azioni raggruppate per categoria.
    Usato nel prompt stage 1 di synt."""
    by_cat: dict[str, list[str]] = {}
    for a in ACTIONS:
        cat = ACTION_CATEGORIES.get(a, "altro")
        by_cat.setdefault(cat, []).append(a)
    lines = []
    for cat, verbs in by_cat.items():
        lines.append(f"  Categoria {cat:14s}: {', '.join(verbs)}")
    return "\n".join(lines)


def _boundary_text(m: dict, lang: str = "it") -> str:
    """Normalizza il campo `boundary` (str legacy o {it,en}) → stringa lang.

    Schema transitorio (pilota 5 verbi-produttori ratificato 24/6): i verbi
    confondibili (read/get/find/list/filter) hanno boundary `{it,en}`
    contrastiva (canonico EN); gli altri 18 restano `str` IT finché il pilota
    non è validato dal simulatore (poi conversione + drop di questo ramo str).
    Un SOLO punto gestisce le due forme: niente shape-handling sparso.
    """
    b = m.get("boundary", "")
    if isinstance(b, dict):
        return b.get(lang) or b.get("en") or b.get("it") or ""
    return b


def render_action_mapping_block() -> str:
    """Blocco multilinea con il MAPPING bilingue completo per stage 1."""
    lines = []
    for verb in ACTIONS:
        m = ACTION_MAPPING.get(verb)
        if not m:
            continue
        lines.append(f"  {verb:10s} IT: {', '.join(m['it'])}")
        lines.append(f"             EN: {', '.join(m['en'])}")
        lines.append(f"             {_boundary_text(m, 'it')}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_boundaries(lang: str = "it", verbs=None) -> str:
    """Confini-verbo per il prompt di filtraggio (intent extractor v4).

    SoT-driven: il prompt È la descrizione dei vocaboli (no parafrasi, no
    drift). Default = TUTTI i verbi (lo spazio completo, come richiesto:
    «il filtraggio dovrebbe avere come prompt tutta la descrizione dei
    vocaboli»). I 5 produttori portano la boundary `{it,en}` contrastiva;
    gli altri la loro definizione esistente via `_boundary_text`.
    """
    sel = verbs if verbs is not None else ACTIONS
    lines = []
    for v in sel:
        m = ACTION_MAPPING.get(v)
        if not m:
            continue
        txt = _boundary_text(m, lang)
        if txt:
            lines.append(f"- {v}: {txt}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"ACTIONS ({len(ACTIONS)}): {render_actions_inline()}")
    print(f"OBJECTS ({len(OBJECTS)}): {render_objects_inline()}")
    print(f"QUALIFIERS ({len(QUALIFIERS)}): {render_qualifiers_inline()}")
    print(f"PRODUCER_VERBS: {sorted(PRODUCER_VERBS)}")
    print(f"DESTRUCTIVE_VERBS: {sorted(DESTRUCTIVE_VERBS)}")
    print()
    print("=== ACTION CATEGORIES ===")
    print(render_action_categories_block())
