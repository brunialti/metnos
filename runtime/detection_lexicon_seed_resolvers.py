#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Seed RM-0005 dei lessici posseduti dai resolver deterministici.

Le chiavi dei mapping sono identita' tecniche stabili.  Soltanto i valori
sono superfici traducibili; struttura delle query, enum, nomi configurati e
policy restano nei rispettivi consumer.
"""
from __future__ import annotations

import logging

import detection_lexicon as _dl


_registered = False
_outputs_registered = False
log = logging.getLogger("metnos.detection_lexicon.seed_resolvers")


def register_detection() -> None:
    """Registra soltanto i lessici di input nel detection store."""
    R = _dl.register

    R("fast_path.intent_exact", "mapping", match_mode="substring", it={
        "configured_timezone": [
            "che ora e nel fuso configurato",
            "che ore sono nel fuso configurato",
        ],
        "time": [
            "che ora e", "che ore sono", "che ora", "che ore",
            "dimmi l'ora", "dimmi che ore sono", "ora attuale",
        ],
        "date": [
            "che giorno e oggi", "che data e oggi", "che data e",
            "che data", "che giorno", "data odierna",
            "oggi che giorno e",
        ],
        "undo": [
            "annulla", "annulla ultima azione", "annulla l'ultima azione",
            "annullare", "annullo", "annulla turn",
            "annulla l'ultimo turno", "annulla ultimo evento",
            "annulla ultimo messaggio", "ripristina",
            "ripristina turno precedente",
        ],
        "location": [
            "dove sono", "dove mi trovo", "posizione attuale",
            "mia posizione", "qual'e' la mia posizione",
            "qual e la mia posizione",
        ],
        "identity": [
            "chi sei", "chi sei tu", "tu chi sei", "ma chi sei",
            "e tu chi sei", "chi sei esattamente", "cosa sei",
            "che cosa sei", "sei un assistente", "sei un ai",
            "sei un'ai", "sei metnos", "presentati", "chi e metnos",
        ],
    }, en={
        "configured_timezone": [
            "what time is it in the configured time zone",
            "what time is it in the configured timezone",
        ],
        "time": [
            "what time is it", "what's the time", "whats the time",
            "what time", "current time", "tell me the time",
        ],
        "date": [
            "what date is it", "what's the date", "what date",
            "today's date", "current date", "what day is it",
        ],
        "undo": [
            "undo", "undo last", "undo last action", "undo last turn",
            "revert", "revert last", "rollback", "rollback last",
        ],
        "location": [
            "where am i", "current location", "my location",
            "my current location", "what is my location",
        ],
        "identity": [
            "who are you", "who are you?", "what are you",
            "what are you?", "are you an assistant", "are you an ai",
            "are you metnos", "introduce yourself",
            "tell me who you are",
        ],
    }, review_policy="manual")
    R("fast_path.undo_prefix", "phrases", match_mode="word",
      it=["annulla", "annullare", "annullo", "ripristina"],
      en=["undo", "rollback", "revert"], review_policy="manual")
    R("fast_path.identity_suffix", "phrases", match_mode="substring",
      it=["chi sei", "tu chi sei"], en=["who are you"],
      review_policy="manual")

    R("resolver.backend_provider", "mapping", match_mode="substring", it={
        "events.local": [
            "calendario locale", "calendar locale", "in locale",
            "sul locale", "localmente",
        ],
        "events.google_workspace": [
            "google calendar", "calendario google", "google primary",
            "su google", "gmail calendar",
        ],
        "files.google_workspace": [
            "google drive", "gdrive", "su drive", "in drive",
            "drive google", "google docs", "google doc", "google sheet",
            "google sheets", "google fogli", "google workspace",
        ],
        "dirs.google_workspace": [
            "google drive", "gdrive", "su drive", "in drive",
            "drive google", "google workspace",
        ],
    }, en={
        "events.local": ["local calendar", "in local"],
        "events.google_workspace": ["on google"],
        "files.google_workspace": ["google drive"],
        "dirs.google_workspace": ["google drive"],
    })

    R("resolver.calendar", "mapping", match_mode="word", it={
        "primary_alias": ["predefinito", "me", "self", "utente", "roberto"],
        "all_alias": ["tutti"],
        "calendar_word": ["calendario", "calendari"],
        "all_request": ["tutti i calendari", "ogni calendario"],
    }, en={
        "primary_alias": ["default", "me", "self", "user"],
        "all_alias": ["all"],
        "calendar_word": ["calendar", "calendars"],
        "all_request": ["all calendars", "every calendar"],
    })

    R("resolver.mail_bulk", "mapping", match_mode="word", it={
        "universal": ["tutta", "tutte", "tutti"],
        "plural_possessive": ["mie", "miei"],
        "mail_noun": [
            "email", "e-mail", "mail", "posta", "casella", "caselle",
            "account", "accounts", "messaggio", "messaggi",
        ],
    }, en={
        "universal": ["all"],
        "plural_possessive": ["my"],
        "mail_noun": [
            "email", "emails", "e-mail", "e-mails", "mail", "mails",
            "inbox", "inboxes", "account", "accounts", "mailbox",
            "mailboxes", "message", "messages",
        ],
    })
    R("resolver.email_channel_request", "phrases", match_mode="word",
      it=["email", "e-mail", "mail", "posta elettronica"],
      en=["email", "e-mail", "mail"])

    R("resolver.from_contains", "mapping", match_mode="word", it={
        "vendor_root": [
            "fattur", "pagament", "ordin", "ricevut", "bollett",
            "abbonament", "addebit",
        ],
        "direct_preposition": [
            "da", "dal", "dalla", "dall'", "dai", "dagli",
        ],
        "vendor_preposition": [
            "da", "di", "dell'", "della", "delle",
        ],
        "stopword": [
            "lunedi", "lunedì", "martedi", "martedì", "mercoledi",
            "mercoledì", "giovedi", "giovedì", "venerdi", "venerdì",
            "sabato", "domenica", "gennaio", "febbraio", "marzo",
            "aprile", "maggio", "giugno", "luglio", "agosto",
            "settembre", "ottobre", "novembre", "dicembre", "email",
            "e-mail", "mail", "posta", "casella", "caselle", "messaggi",
            "messaggio", "oggi", "ieri", "domani", "settimana", "mese",
            "anno", "telegram", "imap", "gmail", "il", "lo", "la", "le",
            "gli", "una", "uno", "questa", "questo", "queste", "questi",
            "tutte", "tutti", "tutta", "mie", "miei", "mia", "mio",
            "fare", "leggere", "inviare", "spostare", "cancellare",
            "scaricare",
        ],
    }, en={
        "vendor_root": [
            "invoice", "receipt", "payment", "order", "bill",
            "subscription", "statement", "charge",
        ],
        "direct_preposition": ["from"],
        "vendor_preposition": ["from", "of"],
        "stopword": [
            "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "january", "february", "march", "april",
            "may", "june", "july", "august", "september", "october",
            "november", "december", "email", "e-mail", "mail", "inbox",
            "outbox", "today", "yesterday", "tomorrow", "week", "month",
            "year", "telegram", "imap", "gmail", "the", "all", "my",
            "this", "these", "some", "any",
        ],
    })

    R("resolver.read_document_format", "phrases", match_mode="word", it=[
        "pdf", "doc", "docx", "documento", "documenti", "xls", "xlsx",
        "excel", "csv", "foglio di calcolo", "fogli di calcolo",
    ], en=[
        "pdf", "doc", "docx", "document", "documents", "xls", "xlsx",
        "excel", "csv", "spreadsheet", "spreadsheets",
    ])
    R("resolver.read_deduplicate", "phrases", match_mode="word", it=[
        "duplicato", "duplicati", "doppione", "doppioni", "deduplica",
        "deduplicare", "deduplicato", "deduplicati",
    ], en=[
        "duplicate", "duplicates", "duplicated", "deduplicate",
        "deduplicated",
    ])

    # Placement cambia autorita' di esecuzione: una traduzione e' ammessa solo
    # quando il mapping nativo e' completo e sottoposto a review manuale.
    R("resolver.target_device", "mapping", match_mode="word", it={
        "locative_anchor": [
            "su", "sul", "sullo", "sulla", "sui", "sugli", "sulle",
            "nel", "su questo",
        ],
        "nominal_anchor": [
            "il", "lo", "la", "l'", "del", "dello", "della", "dell'",
            "dei", "degli", "delle", "di",
        ],
        "local_marker": [
            "su questo pc", "su questo computer", "su questa macchina",
            "sul mio pc", "sul mio computer", "sul mio portatile",
            "sul mio fisso", "localmente", "in locale", "qui sul pc",
            "sul pc locale",
        ],
        "server_adjunct": [
            "sul server", "qui sul server", "sul .33", "sul metnos",
            "lato server",
        ],
        "server_nominal": [
            "del server", "dello .33", "questo server", "il server",
            "metnos server", "server metnos", "questo metnos",
        ],
    }, en={
        "locative_anchor": ["on", "onto"],
        "nominal_anchor": ["the", "of", "from"],
        "local_marker": [
            "on this pc", "on this computer", "on this machine", "on my pc",
            "on my computer", "on my laptop", "on my machine", "locally",
        ],
        "server_adjunct": ["on the server", "server side"],
        "server_nominal": ["of the server", "this server", "the server"],
    }, review_policy="manual")



def register_all() -> None:
    """Registra, in modo idempotente, lessici input e output dei resolver."""
    register_detection()
    _register_output_messages()


def ensure_registered() -> None:
    """Registra soltanto input detection; gli output hanno lifecycle proprio."""
    global _registered
    if _registered:
        return
    try:
        register_detection()
    except Exception:  # read-only sandbox / store temporaneamente occupato
        log.warning("resolver lexicon seed non disponibile", exc_info=True)
        return
    _registered = True
    # Il seed modulare viene dichiarato dopo lo startup del catalogo core:
    # accoda qui le nuove risorse per la lingua d'istanza. Su it/en e' un
    # no-op, per una lingua terza evita un gap invisibile al primo boot.
    try:
        _dl.enqueue_language(_dl.current_lang())
    except Exception:
        log.warning("resolver lexicon translation enqueue fallito", exc_info=True)


def ensure_output_registered() -> None:
    """Provisiona separatamente gli output; non e' un prerequisito detection."""
    global _outputs_registered
    if _outputs_registered:
        return
    try:
        _register_output_messages()
    except Exception:
        log.warning("resolver output seed non disponibile", exc_info=True)
        return
    _outputs_registered = True


def _register_output_messages() -> None:
    """Seed i18n degli output deterministici senza introdurre branch locali."""
    import i18n

    messages = {
        "MSG_FAST_TIME": ("Sono le {hhmm}.", "It's {hhmm}."),
        "MSG_FAST_TIME_TZ": (
            "Sono le {hhmm} nel fuso {tz}.",
            "It's {hhmm} in the {tz} time zone.",
        ),
        "MSG_FAST_DATE": (
            "Oggi e' {weekday} {day} {month} {year}.",
            "Today is {weekday}, {month} {day}, {year}.",
        ),
        "MSG_FAST_IDENTITY": (
            "Sono Metnos, un assistente personale self-hosted che gira sulla "
            "tua macchina (via Telegram e interfaccia web). Ti aiuto con file, "
            "posta, foto, calendario, web e altro — solo le funzioni che attivi "
            "tu. Come posso aiutarti?",
            "I am Metnos, a self-hosted personal assistant running on your own "
            "machine (via Telegram and a web UI). I help with files, mail, "
            "photos, calendar, the web and more — only the capabilities you "
            "switch on. How can I help?",
        ),
        "MSG_FAST_NOTHING_UNDO": (
            "Niente da annullare: nessuna azione reversibile nel turno precedente.",
            "Nothing to undo: no reversible action in the previous turn.",
        ),
        "MSG_FAST_UNDO": (
            "Annullato: {executor} ({count} elementi).",
            "Undone: {executor} ({count} items).",
        ),
        "MSG_FAST_LOCATION_RECENT_MISSING": (
            "Posizione non disponibile (nessuna condivisione recente).",
            "Location not available (no recent share).",
        ),
        "MSG_FAST_LOCATION_MISSING": (
            "Posizione non disponibile.", "Location not available.",
        ),
        "MSG_FAST_LOCATION": (
            "Posizione: {lat:.4f}, {lon:.4f}{age}.",
            "Location: {lat:.4f}, {lon:.4f}{age}.",
        ),
        "MSG_FAST_AGE_SECONDS": (
            " (aggiornata {value}s fa)", " (updated {value}s ago)",
        ),
        "MSG_FAST_AGE_MINUTES": (
            " (aggiornata {value}min fa)", " (updated {value}min ago)",
        ),
        "MSG_FAST_AGE_HOURS": (
            " (aggiornata {value}h fa)", " (updated {value}h ago)",
        ),
        "ERR_FAST_EXECUTOR": (
            "Errore in {executor}: {error}", "Error in {executor}: {error}",
        ),
    }
    weekdays_it = [
        "lunedi'", "martedi'", "mercoledi'", "giovedi'", "venerdi'",
        "sabato", "domenica",
    ]
    weekdays_en = [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday",
    ]
    months_it = [
        "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
    ]
    months_en = [
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
    ]
    for index, (it, en) in enumerate(zip(weekdays_it, weekdays_en)):
        messages[f"MSG_FAST_WEEKDAY_{index}"] = (it, en)
    for index, (it, en) in enumerate(zip(months_it, months_en), start=1):
        messages[f"MSG_FAST_MONTH_{index}"] = (it, en)
    for key, (text_it, text_en) in messages.items():
        i18n.register_key_if_missing(key, text_it, text_en)
