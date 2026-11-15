#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts added by the review of the introspective functions (15/11/2026).

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_CHANGE_ACTIVATION_NOT_CARRIED_PUSH': (
        'Il tuo «Attiva» per {name} non è arrivato in tempo a chi firma, quindi non è stato attivato. Lo strumento resta in attesa nella pagina Modifiche: puoi rispondere di nuovo da lì.',
        'Your «Activate» for {name} did not reach the signer in time, so it was not activated. The tool is still waiting on the Changes page: you can answer again there.'),
    'ERR_ENGINE_INTERNAL': (
        'Non sono riuscito a rispondere per un mio errore interno, che è stato registrato. Puoi riprovare.',
        'I could not answer because of an internal error of mine, which has been recorded. You can try again.'),
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
    print(f"Introspection review: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
