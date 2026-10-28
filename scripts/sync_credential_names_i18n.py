#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggiorna i testi della sezione Credenziali del profilo e dei nomi.

Roberto, 14/10/2026: «Nomi dei siti» diventa «Credenziali» ed elenca tutte le
credenziali passate, email comprese; a siti e caselle si danno un nome e dei
sinonimi, anche dal modulo iniziale delle credenziali; le credenziali di siti e
caselle si aggiungono e aggiornano anche dal profilo. Il replay conserva gli
hash della baseline precedente e toglie le chiavi della vecchia sezione; non
sostituisce il DB e non aggiorna i cataloghi utente.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    "MSG_USER_CREDENTIALS_HELP": (
        "Tutte le credenziali passate a Metnos: siti, caselle email e servizi. "
        "A un sito o a una casella puoi dare un nome e dei sinonimi: scritti in "
        "una richiesta su quel sito o sulla posta, scelgono quale usare. "
        "Password e codici non sono mai mostrati.",
        "All the credentials given to Metnos: sites, mailboxes and services. "
        "A site or a mailbox can have a name and synonyms: written in a request "
        "about that site or about mail, they choose which one to use. "
        "Passwords and codes are never shown."),
    "MSG_USER_CREDENTIALS_MANAGE": (
        "Per cancellare una credenziale o un indirizzo approvato vai in "
        "Impostazioni → Credenziali.",
        "To delete a credential or an approved address, go to "
        "Settings → Credentials."),
    "MSG_USER_CREDENTIALS_COL_CREDENTIAL": ("Credenziale", "Credential"),
    "MSG_USER_CREDENTIALS_COL_NAMES": ("Nome e sinonimi", "Name and synonyms"),
    "MSG_USER_CREDENTIALS_NO_SECRET": (
        "nessuna credenziale salvata: solo il nome",
        "no stored credential: name only"),
    "MSG_USER_CREDENTIALS_NAME": ("Nome", "Name"),
    "MSG_USER_CREDENTIALS_IDENTIFIER": ("Identificativo", "Identifier"),
    "MSG_USER_CREDENTIALS_ADDRESS": ("Indirizzo", "Address"),
    "MSG_USER_CREDENTIALS_EMAIL": ("Email di accesso", "Login email"),
    "MSG_USER_CREDENTIALS_PROPOSED_NAME": (
        "Nome proposto dal dominio o dalla casella: puoi cambiarlo. Premi Salva per usarlo nelle richieste.",
        "Name suggested from the domain or mailbox: you can change it. Press Save to use it in requests."),
    "MSG_USER_CREDENTIALS_SYNONYMS": ("Sinonimi", "Synonyms"),
    "MSG_USER_CREDENTIALS_SYNONYMS_HINT": ("separati da virgola",
                                           "separated by commas"),
    "MSG_USER_CREDENTIALS_SAVE": ("Salva", "Save"),
    "MSG_USER_CREDENTIALS_CLEAR": ("Togli i nomi", "Remove the names"),
    "MSG_USER_CREDENTIALS_NOT_NAMEABLE": (
        "nomi non previsti per questo tipo",
        "names are not available for this kind"),
    "MSG_USER_CREDENTIALS_ADD_SITE": ("Aggiungi un sito per nome",
                                      "Add a site by name"),
    "MSG_INVOCATION_NAME_INVALID": (
        "«{name}» non è valido: usa lettere, numeri, spazi, punti, trattini e "
        "trattini bassi, al massimo 80 caratteri. Sono ammessi anche indirizzi IP.",
        "«{name}» is not valid: use letters, digits, spaces, dots, hyphens and "
        "underscores, up to 80 characters. IP addresses are also allowed."),
    "MSG_INVOCATION_NAME_TOO_SHORT": (
        "«{name}» è troppo corto: servono almeno 3 caratteri, con almeno una "
        "lettera.",
        "«{name}» is too short: use at least 3 characters, including a letter."),
    "MSG_INVOCATION_NAME_VOCABULARY": (
        "«{name}» è una parola che Metnos usa già per capire le richieste: "
        "scegli un nome diverso.",
        "«{name}» is a word Metnos already uses to understand requests: "
        "choose another name."),
    "MSG_INVOCATION_NAME_TAKEN": (
        "«{name}» è già il nome di {other}: un nome può indicare una cosa sola.",
        "«{name}» is already the name of {other}: a name can point to one "
        "thing only."),
    "MSG_INVOCATION_TARGET_INVALID": (
        "Indirizzo del sito o casella email non valido.",
        "Invalid site address or mailbox."),
    "MSG_CREDENTIAL_INVOCATION_NAMES_PROMPT": (
        "Con quali nomi lo chiamerai? Il primo è il nome, gli altri sono "
        "sinonimi, separati da virgola (facoltativo).",
        "Which names will you call it by? The first is the name, the others "
        "are synonyms, separated by commas (optional)."),
    "MSG_CREDENTIAL_INVOCATION_NAMES_SAVED": ("Nomi salvati: {names}.",
                                              "Names saved: {names}."),
    # Adding and updating credentials from the profile (Roberto, 14/10/2026).
    "MSG_USER_CREDENTIALS_UPDATE": ("Aggiorna credenziali", "Update credentials"),
    "MSG_USER_CREDENTIALS_ADD": ("Aggiungi credenziali", "Add credentials"),
    "MSG_USER_CREDENTIALS_NEW_TITLE": ("Aggiungi o aggiorna credenziali",
                                       "Add or update credentials"),
    "MSG_USER_CREDENTIALS_NEW_HELP": (
        "Si apre lo stesso modulo protetto della chat: password e codici si "
        "scrivono solo lì. Se le credenziali esistono già, il modulo le "
        "aggiorna e i campi segreti lasciati vuoti restano quelli salvati.",
        "The same protected form as the chat opens: passwords and codes are "
        "typed only there. If the credentials already exist, the form updates "
        "them and secret fields left empty keep their stored value."),
    "MSG_USER_CREDENTIALS_NEW_SITE": ("Credenziali di un sito", "Site credentials"),
    "MSG_USER_CREDENTIALS_NEW_MAIL": ("Credenziali di una casella email",
                                      "Mailbox credentials"),
    "MSG_USER_CREDENTIALS_MAILBOX_NAME": ("Nome della casella (es. ufficio)",
                                          "Mailbox name (e.g. office)"),
    "MSG_USER_CREDENTIALS_CLOSE": ("Chiudi e aggiorna l'elenco",
                                   "Close and refresh the list"),
    "MSG_CREDENTIAL_FORM_NOT_OPENED": (
        "Il modulo delle credenziali non si è aperto. {detail}",
        "The credential form did not open. {detail}"),
    "MSG_CREDENTIAL_KEEP_IF_EMPTY": (
        "(lascia vuoto per conservare il valore salvato)",
        "(leave empty to keep the stored value)"),
    "MSG_CREDENTIALS_ADD_HELP": (
        "Per aggiungere o aggiornare una credenziale usa Utenti → il tuo "
        "profilo → Credenziali, oppure scrivi in chat, per esempio: salva le "
        "credenziali di enel.",
        "To add or update a credential use Users → your profile → "
        "Credentials, or write in the chat, for example: save the credentials "
        "for enel."),
}

# Keys of the former «Nomi dei siti» section, replaced by the ones above.
RETIRED = ("MSG_SITE_INVOCATIONS_TITLE", "MSG_SITE_INVOCATIONS_HELP",
           "MSG_SITE_INVOCATIONS_INVALID", "MSG_SITE_INVOCATIONS_NAME",
           "MSG_SITE_INVOCATIONS_SAVE", "MSG_SITE_INVOCATIONS_DELETE")


def sync(path: Path) -> int:
    sys.path.insert(0, str(ROOT / "runtime"))
    import i18n

    i18n.DB_PATH = path
    i18n._SEED_DB_PATH = path
    i18n._conn = None
    conn = i18n._open()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS i18n_seed_history ("
        "key TEXT NOT NULL, lang TEXT NOT NULL, version_hash TEXT NOT NULL, "
        "PRIMARY KEY (key, lang, version_hash))"
    )
    for key, (it, en) in sorted(TRANSLATIONS.items()):
        for lang, previous in conn.execute(
            "SELECT lang, text FROM i18n WHERE key=? AND text IS NOT NULL", (key,),
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO i18n_seed_history (key,lang,version_hash) "
                "VALUES (?,?,?)", (key, lang, i18n._sha256_full(previous)),
            )
        i18n.set_catalog_translations(key, {"it": it, "en": en}, source_lang="it")
    conn.commit()
    i18n.delete_keys(RETIRED)
    i18n._checkpoint(conn)
    conn.close()
    i18n._conn = None
    return len(TRANSLATIONS) * 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    count = sync(args.db)
    print(f"Credenziali e nomi: {count} traduzioni nel seed {args.db}")


if __name__ == "__main__":
    main()
