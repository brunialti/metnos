#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Texts of the consent setup form: what is asked, and the return address (14/11/2026).

The replay keeps the hashes of the previous baseline; it does not replace the DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRANSLATIONS = {
    # No count of the fields: the form shows them, and a number drifts.
    'MSG_OAUTH_SETUP_NEEDED': (
        'Per usare Google Workspace serve una configurazione iniziale, da fare una volta sola. '
        'Compila i campi qui sotto.',
        'Using Google Workspace needs a setup, done once. Fill in the fields below.'),
    # Every permission the widest choice asks for is named: the provider's page lists them all.
    'MSG_OAUTH_PROMPT_SERVICES': (
        'Quali servizi vuoi autorizzare? «calendar»: solo il calendario. '
        '«calendar,drive,sheets,docs»: calendario, Drive, fogli e documenti. '
        '«all»: tutti, cioè anche posta (leggere, spedire, modificare), contatti, foto '
        'e riconoscimento delle immagini.',
        'Which services do you want to authorize? “calendar”: the calendar only. '
        '“calendar,drive,sheets,docs”: calendar, Drive, sheets and documents. '
        '“all”: every one, which adds mail (read, send, modify), contacts, photos '
        'and image recognition.'),
    'MSG_OAUTH_RETURN_ADDRESS': (
        'Dopo il consenso il fornitore ti riporta a questo indirizzo:\n{redirect}\n'
        'Se il client che crei è di tipo «applicazione web», registralo nella console del fornitore '
        'fra gli indirizzi di ritorno autorizzati.',
        'After the consent the provider brings you back to this address:\n{redirect}\n'
        'If the client you create is a “web application”, register it in the provider\'s console '
        'among the authorized return addresses.'),
    'MSG_OAUTH_RETURN_UNREGISTERED': (
        'Attenzione: il file del client indicato qui sotto è di tipo «applicazione web» e non elenca '
        'l\'indirizzo a cui il fornitore deve riportarti dopo il consenso:\n{redirect}\n'
        'Se non l\'hai già fatto, aggiungilo nella console del fornitore fra gli indirizzi di ritorno '
        'autorizzati del client (in Google Cloud: API e servizi → Credenziali → il client → '
        '«URI di reindirizzamento autorizzati»), salva e aspetta qualche minuto. '
        'Senza, il consenso finisce con un errore del fornitore («redirect_uri_mismatch»).',
        'Warning: the client file given below is a “web application” and does not list the address '
        'the provider must bring you back to after the consent:\n{redirect}\n'
        'If you have not done it yet, add it in the provider\'s console among the authorized return '
        'addresses of the client (in Google Cloud: APIs & Services → Credentials → the client → '
        '“Authorized redirect URIs”), save and wait a few minutes. '
        'Without it the consent ends with an error of the provider (“redirect_uri_mismatch”).'),
    'MSG_OAUTH_RETURN_INSECURE': (
        'Attenzione: questa pagina è aperta da un indirizzo che un fornitore non accetta per '
        'riportarti qui dopo il consenso:\n{redirect}\n'
        'Serve un indirizzo https oppure localhost. Apri la chat da uno di questi e ripeti la richiesta.',
        'Warning: this page is opened from an address a provider does not accept to bring you back '
        'here after the consent:\n{redirect}\n'
        'It takes an https address or localhost. Open the chat from one of these and repeat the request.'),
    # Pages outside the chat: the return from the provider, a pairing link opened twice.
    'MSG_OAUTH_PAGE_REFUSED_TITLE': ('Autorizzazione non concessa', 'Authorization not granted'),
    'MSG_OAUTH_PAGE_REFUSED_BODY': (
        'Il fornitore ha risposto: {error}. Per riprovare, torna a Metnos e ripeti la richiesta.',
        'The provider answered: {error}. To try again, go back to Metnos and repeat the request.'),
    'MSG_OAUTH_PAGE_INCOMPLETE_TITLE': ('Ritorno incompleto', 'Incomplete return'),
    'MSG_OAUTH_PAGE_REPEAT': (
        'Al ritorno dal fornitore mancano dei dati. Torna a Metnos e ripeti la richiesta.',
        'Some data is missing in the return from the provider. Go back to Metnos and repeat the request.'),
    'MSG_OAUTH_PAGE_EXPIRED_TITLE': ('Richiesta di consenso scaduta', 'Consent request expired'),
    'MSG_OAUTH_PAGE_EXPIRED_BODY': (
        'Il consenso va dato entro {minutes} minuti dalla richiesta, e una volta sola: questa era più '
        'vecchia, o già usata. Nulla è stato salvato. Torna a Metnos e ripeti la richiesta: '
        'riparte da capo.',
        'The consent must be given within {minutes} minutes of the request, and once only: this one '
        'was older, or already used. Nothing was saved. Go back to Metnos and repeat the request: '
        'it starts again.'),
    'MSG_OAUTH_PAGE_NO_OWNER_BODY': (
        'Questa richiesta non appartiene più a una persona attiva. Torna a Metnos e ripeti la richiesta.',
        'This request no longer belongs to an active person. Go back to Metnos and repeat the request.'),
    'MSG_PAIR_LINK_SPENT_TITLE': ('Collegamento non più valido', 'Link no longer valid'),
    'MSG_PAIR_LINK_SPENT_BODY': (
        'Questo collegamento funziona una volta sola: è già stato usato, oppure è scaduto. '
        'Chiedine uno nuovo a chi amministra Metnos.',
        'This link works once only: it was already used, or it expired. '
        'Ask who administers Metnos for a new one.'),
    # The chat opened again after the browser was sent to the provider.
    'MSG_CHAT_AUTH_PENDING': ('Il consenso è ancora da dare.', 'The consent is still to be given.'),
    'MSG_CHAT_AUTH_OPEN': ('Apri la pagina di autorizzazione', 'Open the authorization page'),
    'MSG_CHAT_DIALOG_REDIRECT_OVER': (
        'Questa richiesta di consenso è conclusa o scaduta. Se serve ancora, ripeti la richiesta.',
        'This consent request is over or expired. If it is still needed, repeat the request.'),
    'MSG_OAUTH_PAGE_PARTIAL': (
        'Il consenso è stato salvato, ma non hai concesso tutti i permessi richiesti. Mancano: {missing}. '
        'Gli strumenti che ne hanno bisogno non funzioneranno; per concederli ripeti la richiesta e '
        'spunta tutte le caselle.',
        'The consent was saved, but you did not grant every permission asked. Missing: {missing}. '
        'The tools that need them will not work; to grant them repeat the request and tick every box.'),
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
    print(f"Consent setup: {count} translations in seed {args.db}")


if __name__ == "__main__":
    main()
