#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the growth loop on the changes page (RM-0009 and RM-0010 F3, 9/11/2026).

The changes page shows them in the instance language. The replay keeps the
hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'UI_CHANGE_VOCAB_PRESSURE': (
        'Parole degli strumenti, ultimi {days} giorni: {refused} strumenti rifiutati '
        'per parole in comune, {requests} proposte chiedono una parola nuova del vocabolario.',
        'Tool words, last {days} days: {refused} tools refused for shared words, '
        '{requests} proposals ask for a new vocabulary word.'),
    'MSG_LEARNING_GAP_SUMMARY': (
        'Funzione mancante chiesta {n} volte, per esempio: «{query}».',
        'Missing capability requested {n} times, for example: “{query}”.'),
    'MSG_LEARNING_GAP_RATIONALE': (
        'Nessuno strumento esegue {target}. Se approvi, Synt prepara un candidato '
        'che entra in uso solo dopo i controlli di Birth, la revisione e il tuo consenso.',
        'No tool performs {target}. If you approve, Synt prepares a candidate that '
        'is used only after the Birth checks, review and your consent.'),
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
    print(f"Learning gap: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
