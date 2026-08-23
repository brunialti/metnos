#!/usr/bin/env python3
"""Sincronizza i confini action nel seed i18n distribuito.

Il sorgente editoriale resta ``vocab.ACTION_MAPPING``; il runtime legge solo
chiavi versionate dal catalogo. Lo storico degli hash permette agli upgrade di
aggiornare una vecchia baseline senza sovrascrivere revisioni locali.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

import i18n  # noqa: E402
from vocab import (  # noqa: E402
    ACTIONS,
    ACTION_BOUNDARY_KEY_PREFIX,
    ACTION_MAPPING,
    action_boundary_key,
    action_seed_languages,
)


def sync(path: Path) -> tuple[int, int]:
    i18n.DB_PATH = path
    i18n._SEED_DB_PATH = path
    i18n._conn = None
    conn = i18n._open()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS i18n_seed_history ("
        "key TEXT NOT NULL, lang TEXT NOT NULL, version_hash TEXT NOT NULL, "
        "PRIMARY KEY (key, lang, version_hash))"
    )
    expected = {action_boundary_key(action) for action in ACTIONS}
    existing = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT key FROM i18n WHERE key LIKE ?",
            (f"{ACTION_BOUNDARY_KEY_PREFIX}%_BOUNDARY",),
        )
    }
    stale = sorted(existing - expected)
    if stale:
        placeholders = ",".join("?" for _ in stale)
        conn.execute(f"DELETE FROM i18n WHERE key IN ({placeholders})", stale)
        conn.execute(
            f"DELETE FROM i18n_seed_history WHERE key IN ({placeholders})",
            stale,
        )

    languages = action_seed_languages()
    if not languages:
        raise RuntimeError("action vocabulary has no complete seed language")
    written = 0
    for action in ACTIONS:
        key = action_boundary_key(action)
        for lang, old_text in conn.execute(
            "SELECT lang, text FROM i18n WHERE key=? AND text IS NOT NULL",
            (key,),
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO i18n_seed_history"
                "(key,lang,version_hash) VALUES (?,?,?)",
                (key, lang, i18n._sha256_full(old_text)),
            )
        translations = {
            lang: ACTION_MAPPING[action]["boundary"][lang]
            for lang in languages
        }
        i18n.set_catalog_translations(
            key, translations, source_lang=languages[0],
        )
        written += len(translations)
    conn.commit()
    i18n._checkpoint(conn)
    return written, len(stale)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", type=Path,
        default=ROOT / "install" / "data" / "i18n_seed.sqlite",
    )
    args = parser.parse_args()
    written, removed = sync(args.db.resolve())
    print(f"action boundary rows synchronized={written}; stale removed={removed}")


if __name__ == "__main__":
    main()
