#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggiorna cause e limiti di raccolta nel seed i18n candidato.

Il replay conserva gli hash della baseline precedente; non sostituisce il DB
e non aggiorna i cataloghi degli utenti. Il runtime li integra al rilascio.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    "ACTION_OUTCOME_UNKNOWN": ("Esito dell’ultima azione non verificato.",
                               "The outcome of the last action has not been verified."),
    "TARGET_CHANGED": ("Il controllo della pagina è cambiato o non è più disponibile.",
                       "The page control changed or is no longer available."),
    "PAGE_CHANGED": ("La pagina è cambiata durante la navigazione.",
                     "The page changed during navigation."),
    "SELECTOR_HIDDEN": ("Il controllo richiesto non è visibile nella pagina.",
                        "The requested control is not visible on the page."),
    "LOGIN_INTERVAL": ("Accesso rimandato: l'ultimo tentativo ha chiesto una verifica anti-robot "
                       "e ripeterlo subito la renderebbe più severa. Riprova fra un'ora.",
                       "Login postponed: the last attempt asked for an anti-robot check and "
                       "repeating it now would make it stricter. Try again in an hour."),
}

COLLECTION_TRANSLATIONS = {
    "MSG_SITES_COLLECTION_PARTIAL": (
        "Completezza della raccolta non verificata. {reason}",
        "Collection completeness has not been verified. {reason}"),
    "MSG_COLLECTION_LIMIT_SECONDS": (
        "Raggiunto il limite di {cap_value} secondi per la raccolta sul sito.",
        "The limit of {cap_value} seconds for collection on the site was reached."),
    "MSG_COLLECTION_LIMIT_ACTIONS": (
        "Raggiunto il limite di {cap_value} azioni.",
        "The limit of {cap_value} actions was reached."),
    "MSG_COLLECTION_LIMIT_PAGES": (
        "Raggiunto il limite di {cap_value} pagine.",
        "The limit of {cap_value} pages was reached."),
    "MSG_COLLECTION_LIMIT_DEPTH": (
        "Raggiunto il limite di profondità di {cap_value} livelli.",
        "The depth limit of {cap_value} levels was reached."),
    "MSG_COLLECTION_COMPLETENESS_UNVERIFIED": (
        "Completezza della raccolta non verificata. {detail}",
        "Collection completeness has not been verified. {detail}"),
    "WARN_TABLE_PARTIAL_SOURCE": (
        "Scheda {sheet}: completezza della raccolta non verificata. {detail}",
        "Sheet {sheet}: collection completeness has not been verified. {detail}"),
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
    catalog = {f"MSG_SITES_RC_{suffix}": {"it": it, "en": en}
               for suffix, (it, en) in TRANSLATIONS.items()}
    catalog.update({key: {"it": it, "en": en}
                    for key, (it, en) in COLLECTION_TRANSLATIONS.items()})
    for key, translations in sorted(catalog.items()):
        for lang, previous in conn.execute(
            "SELECT lang, text FROM i18n WHERE key=? AND text IS NOT NULL", (key,),
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO i18n_seed_history (key,lang,version_hash) "
                "VALUES (?,?,?)", (key, lang, i18n._sha256_full(previous)),
            )
        i18n.set_catalog_translations(key, translations, source_lang="it")
    conn.commit()
    i18n._checkpoint(conn)
    conn.close()
    i18n._conn = None
    return len(catalog) * 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    count = sync(args.db)
    print(f"Navigazione: {count} traduzioni nel seed {args.db}")


if __name__ == "__main__":
    main()
