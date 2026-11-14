#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the cards of tools and plans on the statistics page.

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    'UI_SCORE_TOOLS_TITLE': ('Scheda per strumento', 'Card per tool'),
    'UI_SCORE_TOOLS_HELP': (
        'Che cosa è successo davvero a ogni strumento negli ultimi {days} giorni, dal più usato. '
        'Rifiuti: lo strumento ha risposto di no (un argomento sbagliato, un servizio esterno che non '
        'risponde). Guasti: è andato in errore il suo codice.',
        'What really happened to each tool in the last {days} days, the most used first. Refusals: '
        'the tool answered no (a wrong argument, an external service that does not answer). Faults: '
        'its own code failed.'),
    'UI_SCORE_PLANS_TITLE': ('Scheda per piano', 'Card per plan'),
    'UI_SCORE_PLANS_HELP': (
        'Le sequenze di strumenti che hanno servito una richiesta negli ultimi {days} giorni, dalla '
        'più usata. «Fuori dagli strumenti» è il tempo speso a capire la richiesta, pianificare e '
        'scrivere la risposta: è ciò che un piano ricordato fa risparmiare.',
        'The sequences of tools that served a request in the last {days} days, the most used first. '
        '“Outside the tools” is the time spent understanding the request, planning and writing the '
        'answer: it is what a remembered plan saves.'),
    'UI_SCORE_WINDOW': ('Ultimi {days} giorni', 'Last {days} days'),
    'UI_SCORE_EMPTY': ('Nessuna richiesta nel periodo', 'No request in the period'),
    'UI_SCORE_GENERATED': ('costruito da Metnos', 'built by Metnos'),
    'UI_SCORE_COL_TOOL': ('Strumento', 'Tool'),
    'UI_SCORE_COL_CALLS': ('Chiamate', 'Calls'),
    'UI_SCORE_COL_OK': ('Riuscite', 'Succeeded'),
    'UI_SCORE_COL_REFUSALS': ('Rifiuti', 'Refusals'),
    'UI_SCORE_COL_FAULTS': ('Guasti', 'Faults'),
    'UI_SCORE_COL_TYPICAL': ('Tempo tipico', 'Typical time'),
    'UI_SCORE_COL_SLOW': ('9 su 10 entro', '9 in 10 within'),
    'UI_SCORE_COL_LAST': ('Ultimo uso', 'Last used'),
    'UI_SCORE_COL_PLAN': ('Sequenza', 'Sequence'),
    'UI_SCORE_COL_TURNS': ('Richieste', 'Requests'),
    'UI_SCORE_COL_ANSWERED': ('Con risposta', 'Answered'),
    'UI_SCORE_COL_FAILED_STEP': ('Con un passo fallito', 'With a failed step'),
    'UI_SCORE_COL_SERVED': ('Come è stata servita', 'How it was served'),
    'UI_SCORE_COL_SECONDS': ('Tempo tipico', 'Typical time'),
    'UI_SCORE_COL_OUTSIDE': ('Fuori dagli strumenti', 'Outside the tools'),
    'UI_SCORE_COL_MODEL': ('Chiamate al modello', 'Model calls'),
    'UI_SCORE_COL_DISTINCT': ('Frasi diverse', 'Distinct phrasings'),
    'UI_SCORE_SERVED_FASTPATH': ('piano ricordato', 'remembered plan'),
    'UI_SCORE_SERVED_AUTOPATH': ('percorso ricordato', 'remembered path'),
    'UI_SCORE_SERVED_ENGINE': ('pianificata', 'planned'),
    'UI_SCORE_SERVED_TERMINATOR': ('chiusa dal controllo finale', 'closed by the final check'),
    'UI_SCORE_SERVED_LRE': ('lavoro lungo', 'long job'),
    'UI_SCORE_SERVED_RECOVERY': ('ripresa dopo un errore', 'resumed after an error'),
    'UI_SCORE_SERVED_DIRECT': ('scorciatoia senza modello', 'shortcut without the model'),
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
    print(f"Scorecards: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
