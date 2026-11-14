#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of a generated tool withdrawn and written again (ADR 0239, rule 4).

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'MSG_GENERATED_WITHDRAWN_REWRITING': (
        'Lo strumento «{name}», costruito da Metnos, ha dato un errore interno. L\'ho ritirato e '
        'lo riscrivo: quando la nuova versione è pronta ti chiedo di attivarla.',
        'The tool «{name}», built by Metnos, failed with an internal error. I withdrew it and I am '
        'writing it again: when the new version is ready I will ask you to activate it.'),
    'MSG_GENERATED_WITHDRAWN': (
        'Lo strumento «{name}», costruito da Metnos, ha dato un errore interno: l\'ho ritirato.',
        'The tool «{name}», built by Metnos, failed with an internal error: I withdrew it.'),
    'MSG_CHANGE_REWRITE_NOT_DONE': (
        'Non sono riuscito a riscrivere «{name}»: resta in uso la versione di prima.',
        'I could not write «{name}» again: the previous version stays in use.'),
    'UI_CHANGE_READY_REPLACES_ASKED': (
        'Nuova versione: sostituisce quella in uso, come hai chiesto («{words}»).',
        'New version: it replaces the one in use, as you asked («{words}»).'),
    'UI_CHANGE_READY_REPLACES_FAULT': (
        'Nuova versione: sostituisce quella ritirata per un errore interno.',
        'New version: it replaces the one withdrawn after an internal error.'),
    'UI_CHANGE_BTN_REWRITE': ('Riscrivi', 'Rewrite'),
    'UI_CHANGE_REWRITE_WORDS': (
        'Fa la cosa sbagliata? Scrivi che cosa non va',
        'Does it do the wrong thing? Say what is wrong'),
    'UI_CHANGE_REWRITE_EXAMPLE': (
        'Esempio: mostra i nomi, non i codici',
        'Example: show the names, not the codes'),
    'UI_CHANGE_REWRITE_HINT': (
        'Metnos lo riscrive e ti chiede di attivare la versione nuova. Intanto questo resta in uso.',
        'Metnos writes it again and asks you to activate the new version. Meanwhile this one stays in use.'),
    # The same action has two names: it restores what a change replaced, or
    # it takes away a tool Metnos built.
    'UI_CHANGE_BTN_ROLLBACK': ('Ripristina', 'Roll back'),
    'UI_CHANGE_BTN_WITHDRAW': ('Ritira', 'Withdraw'),
    'UI_CHANGE_TAB_ROLLED_BACK': ('Ritirate', 'Rolled back'),
    'UI_CHANGE_BADGE_ROLLED_BACK': ('ritirata', 'rolled back'),
    'UI_CHANGE_CONFIRM_ROLLBACK': (
        'Sicuro? Verrà ripristinato lo stato precedente.',
        'Sure? The previous state will be restored.'),
    'UI_CHANGE_CONFIRM_WITHDRAW': (
        'Sicuro? Lo strumento «{name}» verrà tolto per sempre: non si potrà riattivare né '
        'ricostruire. Se fa la cosa sbagliata, usa Riscrivi.',
        'Sure? The tool «{name}» will be removed for good: it cannot be reactivated or built '
        'again. If it does the wrong thing, use Rewrite.'),
    'UI_CHANGE_REWRITING': (
        'In riscrittura: la nuova versione arriva appena è pronta',
        'Being written again: the new version comes as soon as it is ready'),
    'ERR_CHANGE_REWRITE_NOT_IN_USE': (
        'Si può riscrivere solo uno strumento costruito da Metnos e in uso (stato attuale: {state})',
        'Only a tool built by Metnos and in use can be written again (current state: {state})'),
    'ERR_CHANGE_REWRITE_WORDS_REQUIRED': (
        'Scrivi che cosa non va: serve a chi lo riscrive',
        'Say what is wrong: whoever writes it again needs it'),
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
    print(f"Generated rewrite: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
