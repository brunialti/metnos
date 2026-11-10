#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the private-root guard and of the safety-signature tools when their store cannot be reached (10/11/2026).

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'ERR_VAGLIO_PRIVATE_ROOT': (
        'il percorso indicato è nelle cartelle private di Metnos (configurazione o stato), '
        'che gli strumenti non possono aprire',
        'the path is inside Metnos\u2019s private folders (configuration or state), '
        'which tools cannot open'),
    'ERR_SAFETY_STORE_UNAVAILABLE': (
        'Il registro delle firme di sicurezza non è raggiungibile: non ho letto né cambiato niente.',
        'The safety-signature register cannot be reached: nothing was read or changed.'),
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
    print(f"Safety store: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
