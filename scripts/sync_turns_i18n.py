#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggiorna soltanto le chiavi Turni del seed i18n candidato.

Il replay conserva gli hash della baseline precedente; non sostituisce il DB
e non aggiorna i cataloghi degli utenti. Il runtime li integra al rilascio.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {'TITLE': ('Turni', 'Turns'),
 'DESCRIPTION': ('Ultimi turni conclusi, dal più recente. Aggiornamento automatico ogni 15 '
                 'secondi.',
                 'Latest finished turns, newest first. Refreshes automatically every 15 seconds.'),
 'START': ('Inizio', 'Started'),
 'CHANNEL': ('Canale', 'Channel'),
 'ACTOR': ('Attore', 'Actor'),
 'STEPS': ('Passi', 'Steps'),
 'OUTCOME': ('Esito', 'Outcome'),
 'DURATION': ('Durata', 'Duration'),
 'QUERY': ('Richiesta', 'Request'),
 'ID': ('Turno', 'Turn'),
 'EMPTY': ('Nessun turno', 'No turns'),
 'REFRESH': ('Aggiorna', 'Refresh'),
 'LIMIT': ('Turni da mostrare', 'Turns to show'),
 'COMPLETED': ('Completato', 'Completed'),
 'FAILED': ('Fallito', 'Failed'),
 'PARTIAL': ('Parziale', 'Partial'),
 'AWAITING_INPUT': ('In attesa', 'Awaiting input')}


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
    catalog = {f"UI_TURNS_{suffix}": {"it": it, "en": en}
               for suffix, (it, en) in TRANSLATIONS.items()}
    for key, translations in sorted(catalog.items()):
        for lang, previous in conn.execute(
            "SELECT lang, text FROM i18n WHERE key=? AND text IS NOT NULL", (key,),
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO i18n_seed_history (key,lang,version_hash) "
                "VALUES (?,?,?)", (key, lang, i18n._sha256_full(previous)),
            )
        i18n.set_catalog_translations(key, translations, source_lang="it")
    conn.commit()
    i18n._checkpoint(conn)
    conn.close()
    i18n._conn = None
    return len(catalog) * 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    count = sync(args.db)
    print(f"Turni: {count} traduzioni nel seed {args.db}")


if __name__ == "__main__":
    main()
