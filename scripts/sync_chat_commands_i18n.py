#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the chat page's own commands (`/clear`, `/clearbuf`) and of a page request that fails, 12/11/2026.

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_CHAT_CLEARED': (
        '✓ Chat pulita su questo dispositivo. I comandi già scritti restano richiamabili '
        'con ↑ e ↓: /clearbuf li cancella.',
        '✓ Chat cleared on this device. The commands you typed can still be recalled '
        'with ↑ and ↓: /clearbuf removes them.'),
    'MSG_CHAT_BUFFER_CLEARED': (
        '✓ Elenco dei comandi già scritti cancellato.',
        '✓ List of typed commands removed.'),
    # A page button whose request fails says so (every administration page).
    'MSG_UI_REQUEST_FAILED': (
        'La richiesta non è riuscita (errore {status}): la pagina non è cambiata. '
        'Riprova; se succede ancora, segnalalo.',
        'The request did not succeed (error {status}): the page did not change. '
        'Try again; if it happens again, report it.'),
    'MSG_UI_REQUEST_NO_ANSWER': (
        'Il server non ha risposto: la pagina non è cambiata. Controlla la connessione e riprova.',
        'The server did not answer: the page did not change. Check the connection and try again.'),
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
    print(f"Chat commands: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
