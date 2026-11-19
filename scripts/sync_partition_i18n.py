#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of a step served partition by partition (for files: folder by folder), and of a move that could not be done for some files.

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_NOTHING_IT = "Non ho avviato nulla: "
_NOTHING_EN = "Nothing was started: "

TRANSLATIONS = {
    # Why a folder has no result, when the reason is of the folder itself.
    'MSG_PARTITION_CAUSE_WRONG_TYPE': ('Non è una cartella', 'Not a folder'),
    'MSG_PARTITION_CAUSE_UNANSWERED': (
        'Il lavoro non ha dato un risultato per questa cartella',
        'The job gave no result for this folder'),
    'MSG_PARTITION_CAUSE_TOO_LARGE': (
        'Risultato troppo grande per riunirlo agli altri',
        'Result too large to join with the others'),
    # A path built from the rows of a step.
    'ERR_ROW_PATH_FIELD': (
        'Alla riga {row} manca «{fields}», che serve per costruire il percorso: non ho creato nulla.',
        'Row {row} has no «{fields}», needed to build the path: nothing was created.'),
    'ERR_ROW_PATH_TEMPLATE': (
        'Il modello del percorso «{template}» non è scritto in un modo che so usare: non ho creato nulla.',
        'The path template «{template}» is not written in a way I can use: nothing was created.'),
    # Said in the question before a removal: which record of each group stays
    # (chosen by age), and a list that a ceiling cut.
    'MSG_KEPT_BY_AGE_OLDEST': (
        'Di ogni gruppo resta il più vecchio.',
        'In each group the oldest one stays.'),
    'MSG_KEPT_BY_AGE_NEWEST': (
        'Di ogni gruppo resta il più recente.',
        'In each group the most recent one stays.'),
    'MSG_CONSENT_GATE_CUT': (
        'L\'elenco è stato tagliato dal tetto dei risultati: ce ne sono altri {n}, su cui non agisco.',
        'The list was cut by the result limit: there are {n} more, which I will not touch.'),
    'MSG_CONSENT_GATE_CUT_ANY': (
        'L\'elenco è stato tagliato dal tetto dei risultati: ce ne sono altri, su cui non agisco.',
        'The list was cut by the result limit: there are more, which I will not touch.'),
    'MSG_CONSENT_GATE_CUT_FOLDERS': (
        'Cartelle tagliate: {labels}.',
        'Folders cut: {labels}.'),
    # A move that could not be done for some files: how many and why, without
    # paths and without naming an argument (the rows carry the paths).
    'MSG_MOVE_NOT_MOVED': (
        'Non spostati: {n} su {total}. Motivo: {reasons}.',
        'Not moved: {n} of {total}. Reason: {reasons}.'),
    'MSG_MOVE_NOT_COPIED': (
        'Non copiati: {n} su {total}. Motivo: {reasons}.',
        'Not copied: {n} of {total}. Reason: {reasons}.'),
    'MSG_MOVE_CAUSE_NAME_TAKEN': (
        'nella cartella di destinazione c\'è già un file con lo stesso nome',
        'the destination folder already has a file with the same name'),
    'MSG_MOVE_CAUSE_GONE': (
        'il file di partenza non c\'è più',
        'the source file is no longer there'),
    'MSG_MOVE_CAUSE_SAME_PLACE': (
        'il file è già in quel posto',
        'the file is already in that place'),
    'MSG_MOVE_CAUSE_PERMISSION': (
        'permesso negato',
        'permission denied'),
    'MSG_MOVE_CAUSE_OTHERS': (
        'altri motivi',
        'other reasons'),
    # A list of folders that cannot be served as written: nothing starts.
    'ERR_PARTITION_VALUES': (
        _NOTHING_IT + 'l\'elenco delle cartelle su cui lavorare non è leggibile. Indica le cartelle una per una.',
        _NOTHING_EN + 'the list of folders to work on cannot be read. Name the folders one by one.'),
    'ERR_PARTITION_PATTERN': (
        _NOTHING_IT + '«{value}» non indica delle cartelle in un modo che so usare. Posso lavorare su cartelle '
        'nominate una per una, oppure su ogni sottocartella di una cartella.',
        _NOTHING_EN + '«{value}» does not name folders in a way I can use. I can work on folders named one '
        'by one, or on every subfolder of a folder.'),
    'ERR_PARTITION_UNRESOLVED': (
        _NOTHING_IT + 'non ho potuto elencare le sottocartelle indicate con «{value}».',
        _NOTHING_EN + 'I could not list the subfolders meant by «{value}».'),
    'ERR_PARTITION_EMPTY': (
        _NOTHING_IT + 'la cartella «{parent}» non ha sottocartelle.',
        _NOTHING_EN + 'the folder «{parent}» has no subfolders.'),
    'ERR_PARTITION_OVERLAP': (
        _NOTHING_IT + 'la cartella «{inner}» sta dentro «{outer}» e verrebbe lavorata due volte. Indica '
        'cartelle che non stanno una dentro l\'altra.',
        _NOTHING_EN + 'the folder «{inner}» is inside «{outer}» and would be worked on twice. Name folders '
        'that are not one inside another.'),
    'ERR_PARTITION_TOO_MANY': (
        _NOTHING_IT + 'le cartelle sono {count}, e in una richiesta ne lavoro al massimo {cap}. Restringi la '
        'richiesta a una parte di esse.',
        _NOTHING_EN + 'there are {count} folders, and one request works on {cap} at most. Narrow the request '
        'to some of them.'),
    'ERR_PARTITION_LIST': (
        _NOTHING_IT + 'non ho potuto elencare le sottocartelle di «{parent}». Motivo: {reason}',
        _NOTHING_EN + 'I could not list the subfolders of «{parent}». Reason: {reason}'),
    'ERR_PARTITION_LIST_FAILED': (
        _NOTHING_IT + 'non ho potuto elencare le sottocartelle di «{parent}».',
        _NOTHING_EN + 'I could not list the subfolders of «{parent}».'),
    'ERR_PARTITION_LIST_INCOMPLETE': (
        _NOTHING_IT + 'non ho potuto leggere tutte le sottocartelle di «{parent}», e lavorerei solo su una parte.',
        _NOTHING_EN + 'I could not read every subfolder of «{parent}», and I would work on a part only.'),
    'ERR_PARTITION_LIST_CUT': (
        _NOTHING_IT + 'le sottocartelle di «{parent}» sono troppe per elencarle tutte.',
        _NOTHING_EN + 'the subfolders of «{parent}» are too many to list them all.'),
    # A folder of a finished job that has no result to show.
    'ERR_PARTITION_UNANSWERED': (
        'Il lavoro non ha dato un risultato per questa cartella.',
        'The job gave no result for this folder.'),
    'ERR_PARTITION_RESULT_TOO_LARGE': (
        'I risultati di questa cartella non sono stati letti: tutti insieme superano la dimensione massima '
        'di una risposta.',
        'The results of this folder were not read: all together they exceed the largest size of an answer.'),
    # Beside the answer: what was done, and one line for each folder.
    'MSG_PARTITION_SEPARATE': (
        'Ricerca in {total} cartelle, ciascuna per conto suo: fra cartelle diverse non ho cercato.',
        'Search in {total} folders, each on its own: I did not look across different folders.'),
    'MSG_PARTITION_EXACT': (
        'Risultati per {total} cartelle.',
        'Results for {total} folders.'),
    'MSG_PARTITION_TOTALS_MINIMUM': (
        'I totali sono un minimo: non tutte le cartelle sono complete.',
        'The totals are a minimum: not every folder is complete.'),
    'MSG_PARTITION_COL_NAME': ('Cartella', 'Folder'),
    'MSG_PARTITION_COL_COUNT': ('Trovati', 'Found'),
    'MSG_PARTITION_COL_OUTCOME': ('Esito ricerca', 'Search outcome'),
    'MSG_PARTITION_COL_MOVED': ('Spostati', 'Moved'),
    'MSG_PARTITION_COL_NOT_MOVED': ('Non spostati', 'Not moved'),
    'MSG_PARTITION_TOTAL': ('Totale', 'Total'),
    'MSG_PARTITION_PARENT_ROW': ('| {name} (madre) | 0 | {n} file fuori |', '| {name} (parent) | 0 | {n} files excluded |'),
    'MSG_PARTITION_DONE': ('ricerca completata', 'search completed'),
    'MSG_PARTITION_DONE_LOST': (
        'ricerca completata, {n} elementi non riusciti', 'search completed, {n} items not done'),
    'MSG_PARTITION_CUT': ('primi {used} di {total}', 'first {used} of {total}'),
    'MSG_PARTITION_CUT_UNKNOWN': (
        'primi {used}, ce ne sono altri', 'first {used}, there are more'),
    'MSG_PARTITION_FAILED': ('non riuscita: {reason}', 'not done: {reason}'),
    # Why a result is partial, for the steps that read it (a sheet's notes).
    'MSG_PARTITION_PARTIAL_FOLDERS': (
        'Cartelle non riuscite: {failed} su {total}: {names}.',
        'Folders not done: {failed} of {total}: {names}.'),
    'MSG_PARTITION_PARTIAL_ITEMS': (
        'Elementi non riusciti dentro le cartelle: {n}.',
        'Items not done inside the folders: {n}.'),
    # In a question before moving or deleting: where the items are.
    'MSG_PARTITION_COUNTS': ('| Cartella | Trovati | Fuori |\n|---|---|---|\n{items}\n| Totale | {total} | {excluded} |',
                             '| Folder | Found | Excluded |\n|---|---|---|\n{items}\n| Total | {total} | {excluded} |'),
    'MSG_PARTITION_COUNTS_MORE': (
        'Per cartella: {items} e altre {more} cartelle.',
        'By folder: {items} and {more} more folders.'),
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
    print(f"Partitions: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
