#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay document acquisition translations into the candidate seed only."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = {
    "MSG_SITES_DOWNLOAD_APPROVAL": {
        "it": ("{action}. Salvataggio in {destination}: al massimo {max_files} file, "
               "{max_file_bytes} byte per file, {max_total_bytes} byte totali, "
               "{timeout_s} secondi per download."),
        "en": ("{action}. Save to {destination}: at most {max_files} files, "
               "{max_file_bytes} bytes per file, {max_total_bytes} bytes in total, "
               "{timeout_s} seconds per download."),
    },
    "MSG_SITES_DOWNLOAD_FAILED": {
        "it": ("Il documento non è stato salvato. L'azione sul sito potrebbe essere "
               "già avvenuta; non viene ripetuta automaticamente."),
        "en": ("The document was not saved. The website action may already have "
               "occurred; it is not repeated automatically."),
    },
    "MSG_SITES_DOCUMENTS_SAVED": {
        "it": "Documenti salvati: {n}.",
        "en": "Documents saved: {n}.",
    },
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
    for key, translations in sorted(TRANSLATIONS.items()):
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
    return sum(map(len, TRANSLATIONS.values()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    print(f"Download: {sync(args.db)} translations in candidate seed {args.db}")


if __name__ == "__main__":
    main()
