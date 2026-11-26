#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the closed operations that create folders and move files on the server, and of a filesystem host older than the service.

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_FS_FOLDERS_UNNAMED': (
        'cartelle del percorso richiesto il cui nome non è visibile',
        'folders in the requested path whose names are not visible'),
    'MSG_FS_FOLDERS_LEFT': (
        'Sono rimaste le cartelle create: {folders}. Non ho potuto toglierle: {reason}',
        'The created folders remain: {folders}. I could not remove them: {reason}'),
    'MSG_FS_FOLDERS_LEFT_NO_UNDO': (
        'Sono rimaste le cartelle create: {folders}. Non ho potuto toglierle: {reason} '
        'Non è disponibile l’annullamento: puoi toglierle tu o chiederlo a Metnos.',
        'The created folders remain: {folders}. I could not remove them: {reason} '
        'Undo is unavailable: you can remove them yourself or ask Metnos to do it.'),
    # A folder to create whose path meets a file, not a folder.
    'ERR_FS_NOT_A_FOLDER': (
        'Non ho creato la cartella: nel percorso c\'è un file con lo stesso nome di una delle cartelle.',
        'I did not create the folder: the path has a file with the same name as one of its folders.'),
    # A folder to create whose parent is missing, when missing parents must not be made.
    'ERR_FS_PARENT_MISSING': (
        'Non ho creato la cartella: quella che deve contenerla non esiste, e mi è stato chiesto di non '
        'creare le cartelle mancanti.',
        'I did not create the folder: the folder that should contain it does not exist, and I was asked '
        'not to create missing folders.'),
    # The undo of a creation meets a folder that is no longer empty.
    'ERR_FS_FOLDER_NOT_EMPTY': (
        'Non ho tolto la cartella: nel frattempo ci è stato messo dentro qualcosa, che resta dov\'è.',
        'I did not remove the folder: something was put in it in the meantime, and it stays where it is.'),
    # A file to move that is no longer where the row says.
    'ERR_FS_SOURCE_MISSING': (
        'Il file di partenza non c\'è più.',
        'The source file is no longer there.'),
    # More items than one call of a closed operation carries: nothing was done.
    'ERR_FS_TOO_MANY': (
        'Sono {count} elementi: in una richiesta ne tratto al massimo {cap}, e non ho fatto nulla. '
        'Dividi la richiesta in parti più piccole.',
        'There are {count} items: one request handles at most {cap}, and nothing was done. '
        'Split the request into smaller parts.'),
    # Why a row of a move is refused before anything is done.
    'MSG_MOVE_CAUSE_FOLDER': (
        'è una cartella, e sposto solo file',
        'it is a folder, and I move files only'),
    'MSG_MOVE_CAUSE_SYSTEM_FILE': (
        'è un file di sistema',
        'it is a system file'),
    # A call served in groups ran out of its time: what was not reached.
    'MSG_FS_CAUSE_TIME': (
        'il tempo della richiesta è finito prima di arrivarci',
        'the request ran out of time before reaching it'),
    'MSG_FS_CAUSE_TIME_UNSURE': (
        'il tempo della richiesta è finito mentre ci stavo lavorando: controlla com\'è rimasto',
        'the request ran out of time while it was being handled: check how it was left'),
    'MSG_FS_TIME_PARTIAL': (
        'Fatti {done} su {total}: il tempo della richiesta è finito, e {left} non li ho raggiunti; sono '
        'rimasti come erano. Chiedi di nuovo per continuare.',
        'Done {done} of {total}: the request ran out of time, and {left} were not reached; they were left '
        'as they were. Ask again to go on.'),
    # The component that works on the server's files is older than the service.
    'ERR_FS_HOST_OUTDATED': (
        'Il componente che lavora sui file del server è di una versione precedente e non sa ancora fare '
        'questa operazione: non ho fatto nulla. Si aggiorna con il rilascio di Metnos.',
        'The component that works on the server\'s files is an older version and cannot do this '
        'operation yet: nothing was done. It is updated with the Metnos release.'),
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
    print(f"Server folders: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
