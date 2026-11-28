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
    'MSG_CHANGE_OBSERVER_NO_CALLS': (
        'Il periodo di osservazione è trascorso senza chiamate.',
        'The observation period has elapsed without any calls.'),
    'MSG_CHANGE_OBSERVER_DEPRECATED': (
        'Il sistema ha messo da parte lo strumento il {date}.',
        'The system set the tool aside on {date}.'),
    'MSG_CHANGE_OBSERVER_LAST_CALL_FAILED': (
        'L’ultima chiamata è fallita, dopo {calls} chiamate complessive.',
        'The last call failed, after {calls} calls in all.'),
    'MSG_CHANGE_OBSERVER_ALIASES_MISSING': (
        'L’archivio dei collegamenti fra strumenti non è più presente.',
        'The store of links between tools is no longer present.'),
    'MSG_CHANGE_OBSERVER_ALIASES_UNREADABLE': (
        'L’archivio dei collegamenti fra strumenti non è leggibile.',
        'The store of links between tools cannot be read.'),
    'MSG_CHANGE_OBSERVER_ALIAS_CHANGED': (
        'Il collegamento fra i due strumenti è stato rimosso o modificato.',
        'The link between the two tools was removed or changed.'),
    'MSG_CHANGE_OBSERVER_FALSE_SUCCESS': (
        'La sequenza ha dichiarato un successo che non trova riscontro nei risultati.',
        'The sequence reported a success that its results do not support.'),
    'MSG_CHANGE_OBSERVER_TURN_ERROR': (
        'La sequenza si è conclusa con un errore.',
        'The sequence ended with an error.'),
    'MSG_CHANGE_OBSERVER_TURN_LOOP': (
        'La sequenza è stata interrotta perché ripeteva gli stessi passi.',
        'The sequence was stopped because it kept repeating the same steps.'),
    'MSG_CHANGE_OBSERVER_NO_EFFECTS': (
        'La sequenza è fallita senza produrre effetti osservabili.',
        'The sequence failed without producing observable effects.'),
    'MSG_CHANGE_OBSERVER_NEW_REJECTIONS': (
        'Dopo l’accettazione, la richiesta ha ricevuto {count} nuovi giudizi negativi: la regola non è efficace.',
        'After acceptance, the request received {count} new negative ratings: the rule is ineffective.'),
    'MSG_CHANGE_OBSERVER_TRIGGER': (
        'La modifica è stata ritirata in seguito alla verifica dei suoi risultati.',
        'The change was taken back following a check of its results.'),
    'LOG_CHANGE_OBSERVER_TRANSITION_ERROR': (
        'Errore nella transizione della proposta: {detail}',
        'Proposal transition error: {detail}'),
    'LOG_CHANGE_OBSERVER_ROLLBACK_UNAVAILABLE': (
        'change_rollback non importabile per la proposta {id}: {detail}',
        'change_rollback cannot be imported for proposal {id}: {detail}'),
    'LOG_CHANGE_OBSERVER_ROLLBACK_FAILED': (
        'Ripristino fisico fallito per la proposta {id}: {detail}',
        'Physical rollback failed for proposal {id}: {detail}'),
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
