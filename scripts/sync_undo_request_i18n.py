#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the answer to «annulla»: what came back and what stayed, and why.

One «annulla» undoes the whole request (17/11/2026): the answer says the files
put back, the folders the request had created that were removed, and what
could not be undone with its reason.

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    # The whole sentence: everything came back.
    'MSG_UNDO_REQUEST_DONE': (
        'Annullato: {done}.',
        'Undone: {done}.'),
    # Part came back, part stayed as it is.
    'MSG_UNDO_REQUEST_PARTIAL': (
        'Annullato in parte: {done}. Non annullato: {left}.',
        'Partly undone: {done}. Not undone: {left}.'),
    # Nothing came back.
    'MSG_UNDO_REQUEST_NONE': (
        'Non annullato: {left}.',
        'Not undone: {left}.'),
    # The operations were there, but everything was already as before.
    'MSG_UNDO_ALREADY': (
        'Niente da annullare: era già tutto com\'era prima della richiesta.',
        'Nothing to undo: everything was already as it was before the request.'),
    # What came back, one part each.
    'MSG_UNDO_PART_FILES_BACK_ONE': (
        '1 file è tornato dov\'era',
        '1 file is back where it was'),
    'MSG_UNDO_PART_FILES_BACK': (
        '{count} file sono tornati dov\'erano',
        '{count} files are back where they were'),
    'MSG_UNDO_PART_FOLDERS_REMOVED_ONE': (
        '1 cartella creata è stata tolta',
        '1 created folder was removed'),
    'MSG_UNDO_PART_FOLDERS_REMOVED': (
        '{count} cartelle create sono state tolte',
        '{count} created folders were removed'),
    'MSG_UNDO_PART_FILES_REMOVED_ONE': (
        '1 file creato è stato tolto',
        '1 created file was removed'),
    'MSG_UNDO_PART_FILES_REMOVED': (
        '{count} file creati sono stati tolti',
        '{count} created files were removed'),
    'MSG_UNDO_PART_FILES_RESTORED_ONE': (
        '1 file è stato ripristinato',
        '1 file was restored'),
    'MSG_UNDO_PART_FILES_RESTORED': (
        '{count} file sono stati ripristinati',
        '{count} files were restored'),
    'MSG_UNDO_PART_ITEMS_ONE': (
        '1 elemento è stato ripristinato',
        '1 item was restored'),
    'MSG_UNDO_PART_ITEMS': (
        '{count} elementi sono stati ripristinati',
        '{count} items were restored'),
    # What stayed: a named place, or an action with no place.
    'MSG_UNDO_LEFT_ITEM': (
        '«{path}» ({cause})',
        '“{path}” ({cause})'),
    'MSG_UNDO_LEFT_ACTION': (
        'un\'azione ({cause})',
        'an action ({cause})'),
    'MSG_UNDO_LEFT_MORE': (
        'e altri {count}',
        'and {count} more'),
    # Why it stayed.
    'MSG_UNDO_CAUSE_NOT_EMPTY': (
        'la cartella non è più vuota: dentro c\'è qualcosa che la richiesta non ci aveva messo',
        'the folder is no longer empty: it holds something the request did not put there'),
    'MSG_UNDO_CAUSE_CHANGED': (
        'non è più quello che la richiesta aveva lasciato',
        'it is no longer what the request left there'),
    'MSG_UNDO_CAUSE_GONE': (
        'non è più dove la richiesta l\'aveva messo',
        'it is no longer where the request put it'),
    'MSG_UNDO_CAUSE_OCCUPIED': (
        'dove stava prima ora c\'è un altro file',
        'another file now stands where it was'),
    'MSG_UNDO_CAUSE_PERMISSION': (
        'manca il permesso di scrivere lì',
        'there is no permission to write there'),
    'MSG_UNDO_CAUSE_UNKNOWN': (
        'non è stato possibile rimetterlo com\'era',
        'it could not be put back as it was'),
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
    print(f"Undo of a request: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
