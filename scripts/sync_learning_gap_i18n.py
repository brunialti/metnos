#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the growth loop on the changes page (RM-0009, RM-0010 F3 and F5, 9-10/11/2026).

The changes page shows them in the instance language. The replay keeps the
hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'UI_VOCAB_DOSSIER': (
        'Scheda della parola nuova', 'New word case file'),
    'UI_VOCAB_WORD_EXISTS': (
        '«{word}» c’è già nel vocabolario ({kind}): la parola nuova non serve.',
        '“{word}” is already in the vocabulary ({kind}): no new word is needed.'),
    'UI_VOCAB_WORD_SYNONYM': (
        '«{word}» è un sinonimo del verbo «{term}», che c’è già: basta quello.',
        '“{word}” is a synonym of the existing verb “{term}”: that one is enough.'),
    'UI_VOCAB_WORD_OBJECT': (
        '«{word}» nomina l’oggetto «{term}», che c’è già.',
        '“{word}” names the existing object “{term}”.'),
    'UI_VOCAB_WORD_FORBIDDEN': (
        '«{word}» è esclusa dalle regole dei nomi degli strumenti.',
        '“{word}” is excluded by the tool naming rules.'),
    'UI_VOCAB_WORD_NEW': (
        '«{word}» è nuova: nessun termine o sinonimo la copre. La chiedono {requests} '
        'proposte; le descrizioni di {tools} strumenti su {objects} oggetti la usano già. '
        'Che sia comprensibile lo dichiara il proponente.',
        '“{word}” is new: no term or synonym covers it. {requests} proposals ask for it; '
        'the descriptions of {tools} tools over {objects} objects already use it. '
        'Its clarity is stated by the proposer.'),
    'UI_VOCAB_WORD_NONE': (
        'La proposta non indica quale parola serve: la richiesta non si può valutare.',
        'The proposal does not say which word it needs: the request cannot be assessed.'),
    'UI_VOCAB_PROPOSER': (
        'Testo del proponente: {text}', 'Proposer’s text: {text}'),
    'UI_VOCAB_KIND_ACTION': ('verbo', 'verb'),
    'UI_VOCAB_KIND_OBJECT': ('oggetto', 'object'),
    'UI_VOCAB_KIND_QUALIFIER': ('qualificatore', 'qualifier'),
    'UI_VOCAB_KIND_SYSTEM': ('verbo di sistema', 'system verb'),
    'UI_CHANGE_DECISION_REASON': (
        'Motivo della decisione', 'Reason for the decision'),
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
