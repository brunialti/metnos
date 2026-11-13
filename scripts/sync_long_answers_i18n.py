#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the answers delivered as a file because too long for a chat (12/11/2026).

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_LONG_ANSWER_RESULTS': (
        'Risultati: {total} righe. Sono tutte nel file allegato «{name}».',
        'Results: {total} rows. They are all in the attached file “{name}”.'),
    'MSG_LONG_ANSWER_RESULTS_CAPPED': (
        'Risultati: {total} righe. Il file allegato «{name}» contiene le prime {shown}.',
        'Results: {total} rows. The attached file “{name}” holds the first {shown}.'),
    'MSG_LONG_ANSWER_TEXT': (
        'La risposta è lunga: qui c’è l’inizio ({shown} caratteri di {total}). '
        'La risposta completa è nel file allegato «{name}».',
        'The answer is long: this is its beginning ({shown} characters of {total}). '
        'The complete answer is in the attached file “{name}”.'),
    'MSG_LONG_ANSWER_NO_FILE': (
        'La risposta è lunga: qui c’è l’inizio ({shown} caratteri di {total}). '
        'Non sono riuscito a salvare il resto in un file.',
        'The answer is long: this is its beginning ({shown} characters of {total}). '
        'I could not save the rest in a file.'),
    'MSG_LONG_ANSWER_FILE_STEM': ('risposta', 'answer'),
    'MSG_LONG_ANSWER_SHEET': ('Risposta', 'Answer'),
    'MSG_LONG_ANSWER_NOTES': ('Note', 'Notes'),
}

# The message no longer repeats the first rows of a table (13/11/2026).
RETIRED = ('MSG_LONG_ANSWER_TABLE',)


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
    i18n.delete_keys(RETIRED)
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
    print(f"Long answers: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
