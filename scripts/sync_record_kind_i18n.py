#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Testi del modulo che chiede il tipo di elementi da raccogliere da un sito.

Roberto 21/10/2026: «la query dell'utente deve esprimere il tipo di oggetto;
se non lo fa, Metnos chiede una precisazione con un modulo, con esempi come
"fatture" o "bollette"». La risposta si aggiunge alla richiesta, che viene
pianificata di nuovo. Il replay conserva gli hash della baseline precedente;
non sostituisce il DB e non aggiorna i cataloghi utente.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_RECORD_KIND_TITLE': (
        'Quali elementi?',
        'Which items?'),
    'MSG_RECORD_KIND_PROMPT': (
        'Quali elementi devo raccogliere dal sito? Per esempio: fatture, bollette.',
        'Which items should I collect from the site? For example: invoices, bills.'),
    'MSG_RECORD_KIND_REQUEST': (
        '{query}. Gli elementi da raccogliere sono: {kind}.',
        '{query}. The items to collect are: {kind}.'),
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
    print(f"Tipo di elementi: {count} traduzioni nel seed {args.db}")


if __name__ == "__main__":
    main()
