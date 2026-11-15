#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of a selection on a piece of data the results do not carry.

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'ERR_UNKNOWN_FIELDS': (
        'Nei risultati non c\'è il dato «{unknown}». Ci sono: {supported}.',
        'The results carry no «{unknown}». They carry: {supported}.'),
    'ERR_FILTER_WHERE_TEST': (
        'La selezione nomina il dato «{field}» ma non dice che cosa deve valere: manca il criterio.',
        'The selection names «{field}» but does not say what it must hold: the test is missing.'),
    'MSG_TERM_UNKNOWN_FIELD_ACTION': (
        'Posso scegliere solo in base ai dati elencati: riformula la richiesta con uno di quelli',
        'I can select only by the data listed: rephrase the request with one of those'),
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
    print(f"Missing data: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
