#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""S-017 B5: approved organizing errors, including the D18 corrections.

Replay retains previous baseline hashes and does not replace the database.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'ERR_ORGANIZE_POLICY': (
        'La richiesta di riordino non è valida: controlla le cartelle e le regole indicate.',
        'The organizing request is not valid: check the folders and rules given.'),
    'ERR_ORGANIZE_PREFLIGHT': (
        'Un controllo di sicurezza sul riordino non è passato, quindi mi sono fermato.',
        'A safety check on the organizing did not pass, so I stopped.'),
    'ERR_ORGANIZE_STALE_PLAN': (
        'Qualcosa è cambiato nelle cartelle da quando ho preparato il piano, quindi mi sono fermato. Chiedi di nuovo il riordino.',
        'Something changed in the folders since I prepared the plan, so I stopped. Ask for the organizing again.'),
    'ERR_ORGANIZE_PLAN_NOT_FOUND': (
        'Non riesco a leggere i dati salvati del riordino, quindi mi sono fermato.',
        'I cannot read the saved data of the organizing, so I stopped.'),
    'ERR_ORGANIZE_PLAN_EXPIRED': (
        'Il piano di riordino è scaduto. Chiedi di nuovo il riordino.',
        'The organizing plan has expired. Ask for the organizing again.'),
    'ERR_ORGANIZE_PLAN_BLOCKED': (
        'Il piano di riordino contiene elementi che non posso spostare in sicurezza, quindi non lo applico.',
        'The organizing plan contains items I cannot move safely, so I will not apply it.'),
    'ERR_ORGANIZE_SCAN_INCOMPLETE': (
        'Non sono riuscito a leggere tutte le cartelle: il piano di riordino è incompleto e non lo applico.',
        'I could not read every folder: the organizing plan is incomplete and I will not apply it.'),
    'ERR_ORGANIZE_CONFIRMATION_REQUIRED': (
        'Per applicare il riordino serve la tua conferma sul piano che ti ho mostrato.',
        'Applying the organizing needs your confirmation of the plan I showed you.'),
    'ERR_ORGANIZE_RECEIPT_INVALID': (
        'Il piano o la ricevuta del riordino non sono leggibili o non corrispondono, quindi non posso continuare.',
        'The organizing plan or receipt is unreadable or does not match, so I cannot go on.'),
    'ERR_ORGANIZE_LIMIT': (
        'Il riordino supera il limite consentito ({limit}). Restringi la richiesta.',
        'The organizing exceeds the allowed limit ({limit}). Narrow the request.'),
    'ERR_ORGANIZE_APPLY': (
        'Il riordino non è riuscito e mi sono fermato.',
        'The organizing failed and I stopped.'),
    'ERR_ORGANIZE_ROLLBACK_PARTIAL': (
        "Il riordino non è riuscito e non ho potuto riportare tutto com'era: controlla le cartelle coinvolte.",
        'The organizing failed and I could not restore everything as it was: check the folders involved.'),
    'ERR_ORGANIZE_UNDO': (
        'Non sono riuscito ad annullare del tutto il riordino.',
        'I could not fully undo the organizing.'),
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
    print(f"Organize files: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
