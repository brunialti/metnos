#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggiorna i testi delle credenziali: consenso sull'origine e pagina Credenziali.

Il consenso si chiede una volta: dopo l'approvazione l'origine viene ricordata
(decisione di Roberto, 13/10/2026); la pagina Credenziali in Amministrazione
elenca i record senza valori segreti. Il replay conserva gli hash della
baseline precedente; non sostituisce il DB e non aggiorna i cataloghi utente.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    "MSG_SITES_CREDENTIAL_ORIGIN_APPROVAL_PROMPT": (
        "Approvi l'uso delle credenziali sul modulo di accesso: {bindings}? "
        "Te lo chiedo solo questa volta: se approvi, ricorderò questo "
        "indirizzo per queste credenziali.",
        "Do you approve using the credentials on this sign-in form: "
        "{bindings}? You are asked only this once: if you approve, this "
        "address is remembered for these credentials.",
    ),
    "MSG_CREDENTIALS_PAGE_TITLE": ("Credenziali", "Credentials"),
    "MSG_CREDENTIALS_PAGE_HELP": (
        "Le credenziali salvate in Metnos. Password, codici e altri valori "
        "segreti non vengono mai mostrati.",
        "The credentials stored in Metnos. Passwords, codes and other secret "
        "values are never shown."),
    "MSG_CREDENTIALS_ADD_HELP": (
        "Per aggiungere o aggiornare una credenziale scrivi in chat, per "
        "esempio: salva le credenziali di enel.",
        "To add or update a credential, write in the chat, for example: save "
        "the credentials for enel."),
    "MSG_CREDENTIALS_EMPTY": ("Nessuna credenziale salvata.",
                              "No credential is stored."),
    "MSG_CREDENTIALS_COL_NAME": ("Nome", "Name"),
    "MSG_CREDENTIALS_COL_KIND": ("Tipo", "Kind"),
    "MSG_CREDENTIALS_COL_DETAILS": ("Dati non segreti", "Non-secret data"),
    "MSG_CREDENTIALS_COL_ORIGINS": ("Indirizzi di accesso", "Sign-in addresses"),
    "MSG_CREDENTIALS_COL_APPROVED": ("Indirizzi approvati", "Approved addresses"),
    "MSG_CREDENTIALS_COL_UPDATED": ("Aggiornata", "Updated"),
    "MSG_CREDENTIALS_SAME_SITE": ("Stesso sito di {name}", "Same site as {name}"),
    "MSG_CREDENTIALS_NONE": ("nessuno", "none"),
    "MSG_CREDENTIALS_UNREADABLE": ("Non leggibile", "Unreadable"),
    "MSG_CREDENTIALS_FORGET": ("Togli", "Remove"),
    "MSG_CREDENTIALS_FORGET_HELP": (
        "Se togli un indirizzo approvato, al prossimo accesso Metnos chiede di "
        "nuovo il consenso.",
        "If you remove an approved address, Metnos asks for consent again at "
        "the next sign-in."),
    "MSG_CREDENTIALS_DELETE": ("Cancella", "Delete"),
    "MSG_CREDENTIALS_DELETE_CONFIRM": (
        "Cancellare le credenziali {name}? Non si può annullare.",
        "Delete the credentials {name}? This cannot be undone."),
    "MSG_CREDENTIALS_FLASH_FORGOTTEN": (
        "Indirizzo tolto: al prossimo accesso verrà chiesto di nuovo il consenso.",
        "Address removed: consent will be asked again at the next sign-in."),
    "MSG_CREDENTIALS_FLASH_DELETED": ("Credenziali cancellate.",
                                      "Credentials deleted."),
    "MSG_CREDENTIALS_FLASH_UNCHANGED": (
        "Nessuna modifica: la voce non esiste più.",
        "Nothing changed: the entry no longer exists."),
}


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
    i18n._checkpoint(conn)
    conn.close()
    i18n._conn = None
    return len(TRANSLATIONS) * 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    count = sync(args.db)
    print(f"Credenziali: {count} traduzioni nel seed {args.db}")


if __name__ == "__main__":
    main()
