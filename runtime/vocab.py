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

NON serve toccare CLAUDE.md §2.2 ad ogni cambio: la doc dichiara la
convenzione, non la lista. Ma se la lista cambia, aggiornare il numero
totale ("17 azioni" oggi).

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
)
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
    # Famiglia 2 — Modalita': operazione specifica entro il dominio
    # (introdotti per change_* / compute_*_loc / order_*).
    "size", "format", "loc", "similar",
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
    # Famiglia 3 — Safety policy (ADR 0071, oggetto `signatures`):
    # find_signatures_blacklist, write_signatures_whitelist, ecc.
    "blacklist", "whitelist", "graylist", "forbidden", "seed",
    "sanity", "command", "reversibility",
    # Diff di seed verso DB (find_signatures_seed_diff).
    "diff",
    # Candidati a promozione graylist→whitelist
    # (find_signatures_promotion_candidates → qualifier "promotion").
    "promotion", "candidates",
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
}

# ── Classificazione operativa per il runtime ──────────────────────────

# Verbi PRODUCER: producono entries da fonti esterne (FS/IMAP/web/scalari
# di sistema). Non hanno bisogno di precursor automatico nel prefilter.
# `find` sussume anche il concetto di "verifica esistenza" (lista vuota = non
# presente, lista non vuota = presente + dettaglio): `check` non e' verbo
# canonico, va mappato a `find`.
PRODUCER_VERBS = frozenset({"read", "find", "list", "get"})

# Verbi che lasciano residuo permanente (modifiche reali). Il vaglio
# potrebbe escludere o richiedere conferma esplicita.
DESTRUCTIVE_VERBS = frozenset({"move", "delete", "send", "write", "extract", "create"})

# Verbi candidati per precursor injection (chi può "popolare entries"
# upstream di un consumer come describe/filter/move/...).
PRECURSOR_VERBS = ("read", "find", "list", "get")

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

# ── MAPPING bilingue per stage 1 di synt + intent extractor ───────────
# Per ogni verbo: sinonimi IT, sinonimi EN, confine semantico (1 frase).
# Stage 1 di synt LO ESPONE INTERAMENTE nel prompt. L'intent extractor
# lo USA INTERAMENTE per la disambiguazione cross-language.
ACTION_MAPPING = {
    "read": {
        "it": ["leggi", "apri", "visualizza", "mostra-il-contenuto", "conta-occorrenze-in"],
        "en": ["read", "open", "view", "show-contents", "count-occurrences-in"],
        "boundary": "Sola lettura: ritorna contenuto/dati. Nessun side-effect.",
    },
    "write": {
        "it": ["scrivi", "salva", "sostituisci-il-contenuto", "sovrascrivi"],
        "en": ["write", "save", "replace-contents", "overwrite", "persist"],
        "boundary": "Crea o sostituisce contenuto di un file specifico.",
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
        "boundary": "DISCOVERY: cerca su una sorgente di verita' per PATTERN o QUERY testuale. Input primario = pattern/criterio (`patterns`, `query`, `name`). L'utente NON sa in anticipo cosa trovera' e lo scopre. Output: lista degli elementi che matchano (possibilmente vuota → assenza). NON usare `find` quando l'utente passa identificatori specifici (paths, urls): in quel caso `get`. Sussume anche «verifica esistenza» (find lista vuota = non presente). ECCEZIONE SEMANTICA controllata (ADR 0086, rinominato 5/5/2026) per il pattern `find_<dom>_indices`: il modificatore di modalita' `indices` segnala che il mezzo di ricerca e' un indice persistente, ma l'output ritorna entries del dominio principale (es. `find_images_indices` ritorna foto). Pattern accettato perche' `indices` e' qualifier di modalita' (mezzo), il dominio principale resta l'oggetto.",
    },
    "list": {
        "it": ["elenca", "lista", "mostra-il-contenuto-di", "dammi-l'elenco-di"],
        "en": ["list", "enumerate", "show-contents-of", "ls"],
        "boundary": "Enumera elementi di un container senza fetch del contenuto (es. nomi file in dir, folder IMAP).",
    },
    "filter": {
        "it": ["filtra", "tieni", "scarta", "seleziona", "subset",
                "estrai-righe", "estrai-da-testo"],
        "en": ["filter", "keep", "discard", "select", "subset",
                "extract-lines", "extract-from-text"],
        "boundary": "RIDUCE una lista PREESISTENTE di entries (ricevuta via `from_step:N` o argomento `entries`) a un sottoinsieme che soddisfa un predicato (regex, range, soglia). Pure compute, niente I/O verso sorgenti di sistema. NON va a prendere dati nuovi: se non hai gia' la lista, usa prima `get` o `find` per produrla. Si usa anche per «estrarre» righe da un testo (filter_texts_lines): la selezione di un sottoinsieme di righe e' `filter`, non `extract` (extract resta riservato a decompressione archivi).",
    },
    "sort": {
        "it": ["ordina", "classifica", "top", "primi", "ultimi"],
        "en": ["sort", "rank", "order", "top", "first", "last"],
        "boundary": "Riordina entries per chiave. Opzionale top-K. Pure compute.",
    },
    "group": {
        "it": ["raggruppa", "aggrega-per", "partiziona-per"],
        "en": ["group", "aggregate-by", "partition-by"],
        "boundary": "Raggruppa entries per valore di un campo. Pure compute.",
    },
    "classify": {
        "it": ["classifica", "categorizza", "etichetta", "assegna-categoria"],
        "en": ["classify", "categorize", "label", "assign-category"],
        "boundary": "Aggiunge un'etichetta a ogni entry secondo un criterio (LLM-augmented).",
    },
    "get": {
        "it": ["ottieni", "dimmi", "dammi", "che-ora-e", "dove-sono", "metadati-di",
                "scarica", "richiedi-da-url", "GET-http", "leggi-stato", "elenca-processi",
                "snapshot", "leggi-questi-paths"],
        "en": ["get", "obtain", "tell-me", "give-me", "what-time", "where-am-i",
                "metadata-of", "fetch", "download", "request-from-url", "http-get",
                "read-state", "list-processes", "snapshot", "read-these-paths"],
        "boundary": "LOOKUP / SNAPSHOT: ottiene dati FRESCHI da una sorgente di verita' per IDENTIFICATORI gia' noti (paths, urls, signatures, lat/lon) OPPURE per snapshot completo del dominio (con filtri opzionali di restringimento accessori, es. `user`, `pid`, `top=N`). Input primario = identificatori o assenza di argomenti (= tutto); NON pattern/query testuale di ricerca (quello e' `find`). NON riceve `entries` da step precedente con criterio di filtro: se hai gia' una lista in mano e vuoi ridurla per predicato, usa `filter`. Discrimine pratico vs `find`: «pattern/query come input primario» → find; «id noti o snapshot» → get.",
    },
    "set": {
        "it": ["imposta", "configura", "set", "modifica-il-valore"],
        "en": ["set", "configure", "update-value"],
        "boundary": "Modifica un valore di configurazione locale. Reversibile via diff.",
    },
    "send": {
        "it": ["invia", "manda", "spedisci", "inoltra", "publica"],
        "en": ["send", "deliver", "forward", "publish"],
        "boundary": "Side-effect remoto (mail SMTP, push, webhook). Irreversibile.",
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
        "it": ["scompatta", "decomprimi", "estrai-da-archivio", "unzip", "untar"],
        "en": ["extract", "unpack", "decompress", "unzip", "untar"],
        "boundary": "RISTRETTO: solo decompressione di archivi (zip / tar / gz). NIENT'ALTRO: «estrai righe da un testo» = `filter`; «estrai campi da entries» = `get`; «estrai testo da PDF/HTML» = `read` (read_files_pdf, read_files_html). Sinonimi italiani come «estrai» vanno disambiguati al contesto dall'intent extractor.",
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
        "boundary": "Modifica forma/parametri di un dato senza cambio di natura (resize, convert format, rotate, crop). Distinta da compress (archivia in container) e da render (format di OUTPUT). L'oggetto resta dello stesso tipo (un'immagine resta un'immagine, cambiano dimensione/formato pixel).",
    },
    "order": {
        "it": ["indicizza", "costruisci-indice", "rebuilda-indice", "materializza-ordinamento",
                "prepara-ricerca", "aggiorna-indice"],
        "en": ["order", "index", "build-index", "materialize-order", "prepare-search",
                "refresh-index"],
        "boundary": "Materializza un ordinamento PERSISTENTE del corpus (indice CLIP, perceptual hash, threading messages, ...) per rendere veloci query future. Distinto da sort: sort ordina una lista IN MEMORIA del turno corrente; order produce un derivato durevole su disco. Composizione naturale: order_X_y costruisce/refresha l'indice, find_X_y lo interroga. Refresh tipicamente lazy (al primo find_X_y che lo richiede) o esplicito (utente: 'ricostruisci indice').",
    },
}

# NB: `check`/`verifica` NON e' verbo canonico — sussunto da `find`. Una
# query come "controlla se ffmpeg e' installato" si mappa a
# `find_packages(name='ffmpeg')`: lista vuota = non installato, lista non
# vuota = installato + dettaglio (path).


# ── Mapping OBJECT → sezioni planner (Fase C2, 11/5/2026) ─────────────
# Tabella deterministica (CLAUDE.md §7.9): dato l'`object` estratto dall'intent
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
# - `events` → calendar: calendario + Google Workspace (drive/sheets/docs/contacts).
# - `contacts` → mail + calendar: rubrica e' usata sia per `to_user` (mail)
#   sia per partecipanti agli eventi (calendar).
# - `urls` → web: tutto il dominio crawler.
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
#   le sezioni se la lista e' vuota (degrade graceful).
_OBJECT_TO_SECTIONS: dict[str, tuple[str, ...]] = {
    "files": (),                  # generico FS, coperto dal core
    "dirs": (),                   # generico FS, coperto dal core
    "packages": (),               # find_packages: query verbale-deterministica
    "messages": ("mail",),
    "events": ("calendar",),
    "contacts": ("mail", "calendar"),
    "places": (),                 # find_places: globale (con/senza get_location)
    "processes": ("system",),
    "urls": ("web",),
    "numbers": (),                # compute scalare, coperto dal core
    "images": ("photos",),
    "signatures": ("admin_shell",),  # safety policy shell + mount
    "texts": (),                  # filter/read text generico, coperto dal core
    "proposals": (),              # admin proposals_cli, no PLANNER routing
    "inputs": (),                 # dialog UI, gestito dal runtime, no sezione
    "credentials": ("admin_shell",),
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
        ('mail', 'calendar')
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


def render_action_mapping_block() -> str:
    """Blocco multilinea con il MAPPING bilingue completo per stage 1."""
    lines = []
    for verb in ACTIONS:
        m = ACTION_MAPPING.get(verb)
        if not m:
            continue
        lines.append(f"  {verb:10s} IT: {', '.join(m['it'])}")
        lines.append(f"             EN: {', '.join(m['en'])}")
        lines.append(f"             {m['boundary']}")
        lines.append("")
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    print(f"ACTIONS ({len(ACTIONS)}): {render_actions_inline()}")
    print(f"OBJECTS ({len(OBJECTS)}): {render_objects_inline()}")
    print(f"QUALIFIERS ({len(QUALIFIERS)}): {render_qualifiers_inline()}")
    print(f"PRODUCER_VERBS: {sorted(PRODUCER_VERBS)}")
    print(f"DESTRUCTIVE_VERBS: {sorted(DESTRUCTIVE_VERBS)}")
    print()
    print("=== ACTION CATEGORIES ===")
    print(render_action_categories_block())
