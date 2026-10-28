#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggiorna i testi della risposta quando si crea un foglio e del risultato parziale.

Richiesta di Roberto del 13/10/2026: frasi semplici, senza «Sheet1» quando c'è
una sola scheda. Cause limite e completezza restano nel registro comune
di sync_collection_i18n.py. Il replay
conserva gli hash della baseline precedente; non sostituisce il DB e non
aggiorna i cataloghi utente.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_TABLE_VERIFIED_ROWS_SINGLE': (
        'Nel foglio sono state scritte {rows} righe di dati.',
        '{rows} data rows were written in the sheet.'),
    'MSG_TABLE_VERIFIED_ROWS': (
        'Nel foglio «{title}» sono state scritte {rows} righe di dati.',
        'In the sheet «{title}», {rows} data rows were written.'),
    'MSG_TABLE_CREATED': (
        'Ho creato il foglio «{title}» con {rows} righe.',
        'I created the sheet «{title}» with {rows} rows.'),
    'MSG_TABLE_WRITTEN': (
        'Ho aggiornato il foglio «{title}»: {rows} righe.',
        'I updated the sheet «{title}»: {rows} rows.'),
    'MSG_TABLE_NOTES_COUNT': (
        'Nella scheda Note trovi {notices} avvisi o riferimenti.',
        'The Notes sheet holds {notices} warnings or references.'),
    'WARN_TABLE_MERGED_ROWS': (
        'Scheda {sheet}: sono state unite {count} osservazioni collegate allo stesso elemento. I dati più dettagliati completano la riga.',
        'Sheet {sheet}: {count} observations linked to the same item were merged. More detailed data enrich the row.'),
    'WARN_TABLE_MERGED_ROWS_ONE_SHEET': (
        'Sono state unite {count} osservazioni collegate allo stesso elemento. I dati più dettagliati completano la riga.',
        '{count} observations linked to the same item were merged. More detailed data enrich the row.'),
    'WARN_TABLE_CONFLICTING_ROWS': (
        'Scheda {sheet}: {count} righe contengono dati diversi tra le fonti. Nelle Note trovi i valori osservati e i riferimenti.',
        'Sheet {sheet}: {count} rows contain values that differ between sources. Notes preserve the observed values and references.'),
    'WARN_TABLE_CONFLICTING_ROWS_ONE_SHEET': (
        '{count} righe contengono dati diversi tra le fonti. Nelle Note trovi i valori osservati e i riferimenti.',
        '{count} rows contain values that differ between sources. Notes preserve the observed values and references.'),
    'MSG_TABLE_CONFLICT_DETAIL': (
        'Valori osservati: {detail}',
        'Observed values: {detail}'),
    'WARN_TABLE_SUSPECTED_DUPLICATES': (
        "Scheda {sheet}: {count} righe potrebbero ripetere un elemento già presente, letto in un'altra pagina o fonte: controllale.",
        'Sheet {sheet}: {count} rows may repeat an item already present, read on another page or source: check them.'),
    'WARN_TABLE_SUSPECTED_DUPLICATES_ONE_SHEET': (
        "{count} righe potrebbero ripetere un elemento già presente, letto in un'altra pagina o fonte: controllale.",
        '{count} rows may repeat an item already present, read on another page or source: check them.'),
    'WARN_TABLE_UNIDENTIFIED_ABSORBED': (
        'Scheda {sheet}: righe senza identificativo tolte perché copie di un record che lo ha: {count}.',
        'Sheet {sheet}: rows without an identifier removed as copies of a record that has one: {count}.'),
    'WARN_TABLE_UNIDENTIFIED_ABSORBED_ONE_SHEET': (
        'Righe senza identificativo tolte perché copie di un record che lo ha: {count}.',
        'Rows without an identifier removed as copies of a record that has one: {count}.'),
    'WARN_TABLE_UNIDENTIFIED_REJECTED': (
        'Scheda {sheet}: righe scartate perché senza identificativo, mentre gli altri record lo hanno: {count}.',
        'Sheet {sheet}: rows rejected for lacking an identifier while the other records have one: {count}.'),
    'WARN_TABLE_UNIDENTIFIED_REJECTED_ONE_SHEET': (
        'Righe scartate perché senza identificativo, mentre gli altri record lo hanno: {count}.',
        'Rows rejected for lacking an identifier while the other records have one: {count}.'),
    'WARN_TABLE_FOREIGN_SECTIONS': (
        'Scheda {sheet}: pagine non lette perché elencano un altro tipo di documento rispetto a quello richiesto: {count}.',
        'Sheet {sheet}: pages not read because they list another kind of document than the one requested: {count}.'),
    'WARN_TABLE_FOREIGN_SECTIONS_ONE_SHEET': (
        'Pagine non lette perché elencano un altro tipo di documento rispetto a quello richiesto: {count}.',
        'Pages not read because they list another kind of document than the one requested: {count}.'),
    'MSG_TABLE_ROW_MERGED': (
        '{detail} → unita nella riga di dati {row}',
        '{detail} → merged into data row {row}'),
    'MSG_TABLE_ROW_REJECTED': (
        '{detail} → scartata: senza identificativo',
        '{detail} → rejected: no identifier'),
    'MSG_TABLE_CONTEXT_COLUMN': (
        'Contesto',
        'Context'),
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
    print(f"Risposta del foglio: {count} traduzioni nel seed {args.db}")


if __name__ == "__main__":
    main()
