#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Seed RM-0005 per affinity e output naturali di codegen/store entries."""
from __future__ import annotations

import logging

import detection_lexicon as _dl
import i18n as _i18n


log = logging.getLogger("metnos.detection_lexicon.seed_codegen")
_registered = False
_outputs_registered = False


# L'ordine e' parte del comportamento: skill_codegen limita il prodotto
# cartesiano a 15 frasi. Manteniamo quindi l'ordine storico completo anche
# nelle due baseline; il catalogo deduplica le forme uguali quando le unisce.
_OBJECT_AFFINITY = {
    "events": [
        "appuntamento", "appuntamenti", "agenda", "evento", "eventi",
        "calendario", "calendar", "riunione", "riunioni", "meeting",
        "scadenza", "deadline", "promemoria", "events", "schedule",
    ],
    "messages": [
        "mail", "email", "messaggio", "messaggi", "posta", "newsletter",
        "messages", "emails", "inbox", "lettera",
    ],
    "files": [
        "file", "documento", "documenti", "documents", "files",
        "spreadsheet", "doc", "docs",
    ],
    "dirs": ["cartella", "cartelle", "directory", "folder", "folders", "dirs"],
    "contacts": [
        "contatto", "contatti", "rubrica", "contact", "contacts", "address",
        "persona",
    ],
    "credentials": [
        "credenziale", "credenziali", "chiave", "token", "credentials",
        "keys", "password",
    ],
    "approval": [
        "approvazione", "approvazioni", "consenso", "conferma", "approval",
        "approvals", "consent", "confirmation",
    ],
    "processes": [
        "processo", "processi", "process", "processes", "task", "pid",
    ],
    "urls": [
        "url", "urls", "link", "links", "sito", "siti", "homepage",
        "pagina", "pagine",
    ],
    "texts": ["testo", "testi", "text", "texts", "contenuto", "stringa"],
    "images": [
        "immagine", "immagini", "image", "images", "foto", "photo",
        "photos", "picture",
    ],
    "persons": ["persona", "persone", "person", "people"],
    "places": ["luogo", "luoghi", "place", "places", "posto", "location"],
    "numbers": ["numero", "numeri", "number", "numbers", "telefono"],
    "tasks": ["task", "tasks", "promemoria", "scheduler"],
    "skills": ["skill", "skills", "capability", "modulo"],
    "calendars": ["calendario", "calendari", "calendar", "calendars", "agenda"],
    "inputs": ["input", "inputs", "valore", "valori"],
    "proposals": ["proposta", "proposte", "proposal", "proposals"],
    "signatures": ["firma", "firme", "signature", "signatures"],
    "packages": ["pacchetto", "pacchetti", "package", "packages"],
    "entries": ["voce", "voci", "entry", "entries", "elemento", "elementi"],
    "lists": ["liste", "lists", "due liste", "two lists", "insiemi", "sets"],
    "sites": [
        "sito", "siti", "site", "sites", "website", "websites",
        "sessione web", "web session",
    ],
    "issues": [
        "issue", "issues", "segnalazione", "segnalazioni", "bug", "ticket",
        "problema", "problemi",
    ],
    "pulls": [
        "pull request", "pull requests", "pr", "merge", "richiesta di merge",
        "patch", "contributo",
    ],
    "preferences": [
        "preferenza", "preferenze", "preference", "preferences",
        "impostazione", "impostazioni", "setting", "settings",
    ],
}


_ACTION_AFFINITY = {
    "read": ["leggi", "read", "view", "open"],
    "write": ["scrivi", "write", "upload"],
    "find": ["cerca", "find", "search"],
    "list": ["elenca", "list", "enumera"],
    "get": ["ottieni", "get"],
    "set": ["imposta", "set", "update"],
    "create": ["crea", "create", "new", "nuovo"],
    "delete": ["cancella", "elimina", "delete", "remove"],
    "move": ["sposta", "move"],
    "send": ["invia", "manda", "send"],
    "share": ["condividi", "share"],
    "change": ["modifica", "update", "change"],
    "filter": ["filtra", "filter"],
    "sort": ["ordina", "sort"],
    "group": ["raggruppa", "group"],
    "classify": ["classifica", "classify"],
    "describe": ["descrivi", "describe"],
    "render": ["mostra", "render"],
    "extract": ["estrai", "extract"],
    "compress": ["comprimi", "compress"],
    "compute": ["calcola", "compute"],
    "compare": ["confronta", "compare"],
    "order": ["ordina", "order"],
    "open": ["apri", "open", "avvia sessione", "start session"],
    "login": ["accedi", "autentica", "login", "sign in", "authenticate"],
    "act": [
        "agisci", "interagisci", "esegui azione", "act", "interact",
        "perform action",
    ],
    "install": [
        "installa", "disinstalla", "metti su", "install", "uninstall",
        "set up",
    ],
    "run": [
        "avvia programma", "lancia programma", "run program", "start program",
        "launch program",
    ],
}


_STORE_AFFINITY = [
    "store", "archivio", "archivi", "raccolta", "collezione", "registro dati",
    "database interno", "collection", "datastore", "memorizza nello store",
    "salva nell'archivio",
]


def register_detection() -> None:
    """Registra soltanto le risorse di detection nel relativo store."""
    R = _dl.register
    R("codegen.affinity.object", "mapping", match_mode="substring",
      it=_OBJECT_AFFINITY, en=_OBJECT_AFFINITY)
    R("codegen.affinity.action", "mapping", match_mode="substring",
      it=_ACTION_AFFINITY, en=_ACTION_AFFINITY)
    R("codegen.store_entries_affinity", "phrases", match_mode="substring",
      it=_STORE_AFFINITY, en=_STORE_AFFINITY)


def register_all() -> None:
    """Registra baseline editoriali input/output, idempotentemente."""
    register_detection()
    _register_output_messages()


def ensure_registered() -> None:
    """Dichiara soltanto il seed detection e accoda la lingua d'istanza."""
    global _registered
    if _registered:
        return
    try:
        register_detection()
    except Exception:
        log.warning("codegen lexicon seed non disponibile", exc_info=True)
        return
    _registered = True
    try:
        _dl.enqueue_language(_dl.current_lang())
    except Exception:
        log.warning("codegen detection enqueue fallito", exc_info=True)


def ensure_output_registered() -> None:
    """Provisiona output i18n senza riaprire il lifecycle del seed detection."""
    global _outputs_registered
    if _outputs_registered:
        return
    try:
        _register_output_messages()
        _enqueue_output_language(_i18n.current_lang())
    except Exception:
        log.warning("codegen output seed non disponibile", exc_info=True)
        return
    _outputs_registered = True


def _register_output_messages() -> None:
    for key, (text_it, text_en) in _OUTPUT_MESSAGES.items():
        _i18n.register_key_if_missing(key, text_it, text_en)


def _enqueue_output_language(lang: str) -> int:
    """Accoda le nuove chiavi output non viste dal precedente add-lang."""
    target = _i18n.normalize_language(lang)
    if not target:
        return 0
    queued = 0
    for key in _OUTPUT_MESSAGES:
        if _i18n.resource_for_language(key, target, fallback=False):
            continue
        source = next((
            candidate for candidate in _i18n.language_chain(target)
            if candidate != target and _i18n.resource_for_language(
                key, candidate, fallback=False,
            )
        ), None)
        if source is None:
            continue
        _i18n.mark_for_translation(key, target, source)
        queued += 1
    return queued


_OUTPUT_MESSAGES = {
    # Descrizione executor generato.
    "MSG_CODEGEN_ACTION_READ": ("Legge", "Reads"),
    "MSG_CODEGEN_ACTION_FIND": ("Cerca", "Searches"),
    "MSG_CODEGEN_ACTION_SET": ("Crea o aggiorna", "Creates or updates"),
    "MSG_CODEGEN_ACTION_DELETE": ("Cancella", "Deletes"),
    "MSG_CODEGEN_ACTION_SEND": ("Invia", "Sends"),
    "MSG_CODEGEN_ACTION_LIST": ("Elenca", "Lists"),
    "MSG_CODEGEN_ACTION_GET": ("Ottiene", "Gets"),
    "MSG_CODEGEN_ACTION_CHANGE": ("Modifica", "Modifies"),
    "MSG_CODEGEN_ACTION_WRITE": ("Scrive", "Writes"),
    "MSG_CODEGEN_ACTION_FALLBACK": ("Esegue operazione su", "Performs operation on"),
    "MSG_CODEGEN_DESCRIPTION_PROVIDER": (
        "SCOPO: {verb} {noun} di {service} (OAuth). PATTERN: {call}. NON: "
        "usare per altri provider (iCloud, Outlook, IMAP); invocare senza "
        "credenziali. OUT: {output_field}=[...].",
        "SCOPO: {verb} {noun} of {service} (OAuth). PATTERN: {call}. NON: use "
        "for other providers (iCloud, Outlook, IMAP); invoke without "
        "credentials. OUT: {output_field}=[...].",
    ),
    "MSG_CODEGEN_DESCRIPTION_GENERIC": (
        "SCOPO: {verb} {obj} (skill {domain}). PATTERN: {call}. NON: omettere "
        "gli argomenti richiesti. OUT: {output_field}=[...].",
        "SCOPO: {verb} {obj} (skill {domain}). PATTERN: {call}. NON: omit "
        "required arguments. OUT: {output_field}=[...].",
    ),
    "MSG_CODEGEN_OAUTH_PROMPT": (
        "Per usare {skill} servono credenziali OAuth. Vuoi configurarle ora?",
        "Using {skill} requires OAuth credentials. Configure now?",
    ),
    # Frasi di dominio; le chiavi canoniche domain restano nel consumer.
    "MSG_CODEGEN_DOMAIN_CALENDAR_NOUN": (
        "appuntamenti del calendario", "calendar appointments"),
    "MSG_CODEGEN_DOMAIN_CALENDAR_SERVICE": ("Google Calendar", "Google Calendar"),
    "MSG_CODEGEN_DOMAIN_CALENDAR_EXAMPLES": (
        "appuntamenti, agenda, eventi, riunioni",
        "appointments, schedule, events, meetings"),
    "MSG_CODEGEN_DOMAIN_GMAIL_NOUN": ("messaggi email", "email messages"),
    "MSG_CODEGEN_DOMAIN_GMAIL_SERVICE": ("Gmail", "Gmail"),
    "MSG_CODEGEN_DOMAIN_GMAIL_EXAMPLES": (
        "email, mail, posta, messaggi", "email, mail, messages"),
    "MSG_CODEGEN_DOMAIN_DRIVE_NOUN": ("file su cloud", "cloud files"),
    "MSG_CODEGEN_DOMAIN_DRIVE_SERVICE": ("Google Drive", "Google Drive"),
    "MSG_CODEGEN_DOMAIN_DRIVE_EXAMPLES": (
        "documenti su Drive, cartelle, file condivisi",
        "Drive documents, folders, shared files"),
    "MSG_CODEGEN_DOMAIN_SHEETS_NOUN": ("fogli di calcolo", "spreadsheets"),
    "MSG_CODEGEN_DOMAIN_SHEETS_SERVICE": ("Google Sheets", "Google Sheets"),
    "MSG_CODEGEN_DOMAIN_SHEETS_EXAMPLES": (
        "spreadsheet, foglio elettronico, tabelle",
        "spreadsheets, sheets, tables"),
    "MSG_CODEGEN_DOMAIN_DOCS_NOUN": ("documenti di testo", "text documents"),
    "MSG_CODEGEN_DOMAIN_DOCS_SERVICE": ("Google Docs", "Google Docs"),
    "MSG_CODEGEN_DOMAIN_DOCS_EXAMPLES": (
        "documento Docs, testo formattato", "Docs document, formatted text"),
    "MSG_CODEGEN_DOMAIN_CONTACTS_NOUN": (
        "contatti rubrica", "address book contacts"),
    "MSG_CODEGEN_DOMAIN_CONTACTS_SERVICE": (
        "Google Contacts", "Google Contacts"),
    "MSG_CODEGEN_DOMAIN_CONTACTS_EXAMPLES": (
        "contatti, rubrica, email di persone",
        "contacts, address book, people emails"),
    # Sostituzioni legacy delle descrizioni argomento.
    "MSG_CODEGEN_TERM_LIST": ("Lista", "List"),
    "MSG_CODEGEN_TERM_IDENTIFIER": ("Identificatore", "Identifier"),
    "MSG_CODEGEN_TERM_START": ("Inizio", "Start"),
    "MSG_CODEGEN_TERM_END": ("Fine", "End"),
    "MSG_CODEGEN_TERM_DEFAULT": ("Default", "Default"),
    "MSG_CODEGEN_TERM_PLURAL": ("Versione plurale", "Plural form"),
    "MSG_CODEGEN_TERM_CAP": ("Cap superiore esplicito", "Explicit upper cap"),
    "MSG_CODEGEN_TERM_WINDOW": ("Finestra temporale", "Time window"),
    "MSG_CODEGEN_TERM_CALENDAR": ("del calendario", "calendar"),
    "MSG_CODEGEN_TERM_STRING": ("Stringa", "String"),
    "MSG_CODEGEN_TERM_RETURNED": ("ritornate", "returned"),
    "MSG_CODEGEN_TERM_MAX_ENTRIES": ("max entries", "max entries"),
    "MSG_CODEGEN_TERM_PURE_COMPUTE": ("Pure compute", "Pure compute"),
    "MSG_CODEGEN_TERM_SINGLE_ID": (
        "Identificatore singolo", "Single identifier"),
    # Errori e descrizioni del builtin store_entries.
    "MSG_STORE_NONE": ("(nessuno)", "(none)"),
    "ERR_STORE_NOT_REGISTERED": (
        "store «{name}» non registrato. Store disponibili: {available}.",
        "store «{name}» is not registered. Available stores: {available}."),
    "ERR_STORE_REQUIRED_FIND": (
        "manca 'store' (nome dello store da interrogare)",
        "missing 'store' (name of the store to query)"),
    "ERR_STORE_REQUIRED_WRITE": (
        "manca 'store' (nome dello store su cui scrivere)",
        "missing 'store' (name of the store to write to)"),
    "ERR_STORE_REQUIRED_DELETE": (
        "manca 'store' (nome dello store da cui eliminare)",
        "missing 'store' (name of the store to delete from)"),
    "ERR_STORE_ENTRIES_REQUIRED": (
        "manca 'entries' (lista record): passa from_step=N del producer da persistere",
        "missing 'entries' (record list): pass from_step=N of the producer to persist"),
    "ERR_STORE_WRONG_COLUMNS": (
        "{error} — colonne dello store «{name}»: {columns}",
        "{error} — columns of store «{name}»: {columns}"),
    "MSG_STORE_FIND_DESCRIPTION": (
        "SCOPO: legge record da uno STORE generico NOMINATO (archivio/raccolta "
        "dati interna, non file/mail/eventi). PATTERN: find_entries(store=\"spese\", "
        "where={\"mese\":\"06\"}, max_results=50). NON: file su disco -> "
        "find_files; mail -> read_messages; filtrare una lista GIÀ in memoria -> "
        "filter_entries. OUT: entries=[{...}].",
        "PURPOSE: reads records from a NAMED generic STORE (internal data "
        "collection, not files/mail/events). PATTERN: find_entries(store=\"expenses\", "
        "where={\"month\":\"06\"}, max_results=50). NOT: disk files -> find_files; "
        "mail -> read_messages; an in-memory list -> filter_entries. OUT: entries=[{...}]."),
    "MSG_STORE_WRITE_DESCRIPTION": (
        "SCOPO: salva/aggiorna (UPSERT, crea-se-manca) record in uno STORE "
        "generico NOMINATO; aggiorna campi coi set_fields. PATTERN: producer allo "
        "step N poi write_entries(store=\"spese\", from_step=N, key=[\"id\"], "
        "set_fields={\"status\":\"posted\"}). NON: scrivere file -> write_files; "
        "inviare -> send_messages; per rispondere 'quanti NUOVI ho inserito' NON "
        "contare n_written/results (un upsert su un record GIA' presente conta "
        "comunque) -> usa SEMPRE n_new (record assenti prima di questa call, quindi "
        "creati ora). Crea lo store e i record se mancano. OUT: "
        "results=[{written,was_new}], n_written, n_new, n_updated.",
        "PURPOSE: saves/updates records in a NAMED generic STORE (UPSERT, create "
        "when missing); updates fields with set_fields. PATTERN: producer at step N, "
        "then write_entries(store=\"expenses\", from_step=N, key=[\"id\"], "
        "set_fields={\"status\":\"posted\"}). NOT: files -> write_files; sending -> "
        "send_messages. For newly inserted records always use n_new, not n_written. "
        "OUT: results=[{written,was_new}], n_written, n_new, n_updated."),
    "MSG_STORE_DELETE_DESCRIPTION": (
        "SCOPO: elimina record da uno STORE generico NOMINATO. PATTERN: "
        "delete_entries(store=\"spese\", where={\"id\":\"x\"}). NON: file -> "
        "delete_files; mail -> move_messages(dst_folder=\"Trash\"). where "
        "assente/vuoto = svuota lo store. OUT: results, n_deleted.",
        "PURPOSE: deletes records from a NAMED generic STORE. PATTERN: "
        "delete_entries(store=\"expenses\", where={\"id\":\"x\"}). NOT: files -> "
        "delete_files; mail -> move_messages(dst_folder=\"Trash\"). Missing/empty "
        "where empties the store. OUT: results, n_deleted."),
    "MSG_STORE_ARG_NAME_FIND": (
        "Nome dello store (archivio) da interrogare, es. \"spese\".",
        "Name of the store (archive) to query, e.g. \"expenses\"."),
    "MSG_STORE_ARG_WHERE_FIND": (
        "Filtro di uguaglianza {campo: valore}; valore lista = IN. Es. "
        "{\"stato\":\"aperto\"}.",
        "Equality filter {field: value}; a list value means IN. E.g. "
        "{\"status\":\"open\"}."),
    "MSG_STORE_ARG_ORDER": (
        "Campi di ordinamento, es. [\"data\"].",
        "Ordering fields, e.g. [\"date\"]."),
    "MSG_STORE_ARG_MAX": ("Cap risultati (§2.1).", "Result cap (§2.1)."),
    "MSG_STORE_ARG_NAME_WRITE": (
        "Nome dello store su cui scrivere.", "Name of the store to write to."),
    "MSG_STORE_ARG_FROM_STEP": (
        "Step che ha prodotto i record da persistere (il runtime espande in entries).",
        "Step that produced the records to persist (runtime expands it into entries)."),
    "MSG_STORE_ARG_KEY": (
        "Campi-chiave per l'upsert (conflitto). Es. [\"id\"]. Assente -> insert puro.",
        "Upsert conflict key fields. E.g. [\"id\"]. Missing -> plain insert."),
    "MSG_STORE_ARG_SET_FIELDS": (
        "Override {campo: valore} applicato a OGNI record prima dell'upsert "
        "(aggiorna lo stato). Es. {\"status\":\"posted\"}.",
        "Override {field: value} applied to EVERY record before upsert. "
        "E.g. {\"status\":\"posted\"}."),
    "MSG_STORE_ARG_NAME_DELETE": (
        "Nome dello store da cui eliminare.", "Name of the store to delete from."),
    "MSG_STORE_ARG_WHERE_DELETE": (
        "Filtro {campo: valore} dei record da eliminare; assente = svuota.",
        "Filter {field: value} for records to delete; missing = empty the store."),
}
