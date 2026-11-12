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
    'ERR_CHANGE_INVALID_BODY': (
        'Il contenuto della richiesta non è leggibile: la decisione non è stata registrata.',
        'The request body cannot be read: the decision was not recorded.'),
    'UI_CHANGE_REVIEW_PENDING': (
        'Strumento pronto e provato ({proposal_id}): aspetta la tua risposta, in cima alla pagina.',
        'Tool ready and tested ({proposal_id}): it waits for your answer, at the top of the page.'),
    'MSG_PLAN_REJECTED_BY_DECISION': (
        'Non ho eseguito il piano: per questa richiesta quel percorso è stato rifiutato dalla '
        'pagina Modifiche e non ne ho trovato un altro. Non ho cambiato niente. '
        'Puoi riformulare la richiesta o togliere il rifiuto dalla pagina Modifiche.',
        'I did not run the plan: for this request that path was rejected on the Changes page '
        'and I found no other one. Nothing was changed. '
        'You can rephrase the request or remove the rejection on the Changes page.'),
    'MSG_CHANGE_REJECT_EMPTY': (
        'La proposta non indica quali strumenti evitare: non c’è niente da registrare.',
        'The proposal does not say which tools to avoid: there is nothing to record.'),
    'ERR_CHANGE_REASON_REQUIRED': (
        'Questa proposta chiede una parola nuova del vocabolario: per accettarla scrivi il motivo.',
        'This proposal asks for a new vocabulary word: write the reason to accept it.'),
    'ERR_CHANGE_RETRY_NOT_FAILED': (
        'Si può riprovare solo una modifica fallita; questa è nello stato «{state}».',
        'Only a failed change can be retried; this one is in state “{state}”.'),
    'ERR_CHANGE_REOPEN_NOT_REJECTED': (
        'Si riapre solo una proposta scartata; questa è nello stato «{state}».',
        'Only a rejected proposal can be reopened; this one is in state “{state}”.'),
    'UI_CHANGE_BTN_REOPEN': ('Riapri', 'Reopen'),
    'UI_CHANGE_CONFIRM_CLOSE': (
        'Chiudo questa proposta? Resta chiusa per trenta giorni. '
        'Se cambi idea, si riapre dalla scheda «Scartate».',
        'Close this proposal? It stays closed for thirty days. '
        'If you change your mind, reopen it from the “Rejected” tab.'),
    'UI_CHANGE_CONFIRM_DECLINE': (
        'Dico no a questo strumento? Il candidato viene tolto e la proposta resta chiusa '
        'per trenta giorni. Se cambi idea, si riapre dalla scheda «Scartate» e lo strumento '
        'viene ricostruito.',
        'Say no to this tool? The candidate is removed and the proposal stays closed for '
        'thirty days. If you change your mind, reopen it from the “Rejected” tab and the '
        'tool is built again.'),
    'ERR_CHANGE_ROLLBACK_FAILED': (
        'Il ritiro non è riuscito: la modifica resta com’era. Riprova più tardi o guarda il registro.',
        'The rollback did not succeed: the change stays as it was. Try again later or check the log.'),
    'MSG_CHANGE_REDIRECTED': (
        'Synt non ha creato {name}: esiste già lo strumento {existing}, che fa lo stesso lavoro.',
        'Synt did not create {name}: the tool {existing} already does the same job.'),
    'MSG_CHANGE_NOT_ACTIVE': (
        'Synt ha concluso, ma {name} non è fra gli strumenti attivi: nessuna funzione nuova è in uso.',
        'Synt finished, but {name} is not among the active tools: no new capability is in use.'),
    'UI_CHANGE_EXTERNAL_QUESTION': (
        'Tre tentativi non sono bastati. Posso usare il modello esterno per finire questo '
        'strumento? Gli mando la richiesta d’esempio, la descrizione, le prove e il codice generato.',
        'Three attempts were not enough. May I use the external model to finish this tool? '
        'I will send it the sample request, the description, the test cases and the generated code.'),
    'UI_CHANGE_WAITING_TITLE': ('Aspettano una tua risposta', 'Waiting for your answer'),
    'UI_CHANGE_WAITING_ABOVE': (
        'La domanda è in cima alla pagina.', 'The question is at the top of the page.'),
    'UI_CHANGE_READY_TITLE': (
        'Strumento pronto e provato: lo attivo?', 'Tool ready and tested: shall I activate it?'),
    'UI_CHANGE_READY_DOES': ('Che cosa fa', 'What it does'),
    'UI_CHANGE_READY_PERMISSIONS': ('Permessi che chiede', 'Permissions it asks for'),
    'UI_CHANGE_READY_NO_PERMISSIONS': ('nessuno', 'none'),
    'UI_CHANGE_READY_UNDO_YES': (
        'Quello che fa si può annullare.', 'What it does can be undone.'),
    'UI_CHANGE_READY_UNDO_NO': (
        'Quello che fa non si può annullare.', 'What it does cannot be undone.'),
    'UI_CHANGE_READY_CASES': (
        'Prove su cui è stato costruito: {n}', 'Cases it was built on: {n}'),
    'UI_CHANGE_BTN_ACTIVATE': ('Attiva', 'Activate'),
    'UI_CHANGE_BTN_DECLINE': ('No', 'No'),
    'UI_CHANGE_ACTIVATING': (
        'Attivazione in corso: controlli finali e firma. Può richiedere qualche minuto.',
        'Activation in progress: final checks and signature. It can take a few minutes.'),
    'UI_CHANGE_ACTIVATED': (
        '{name} è attivo: Metnos può usarlo da ora.',
        '{name} is active: Metnos can use it from now on.'),
    'UI_CHANGE_ACTIVATION_REFUSED': (
        '{name} non è stato attivato: i controlli finali lo hanno rifiutato.',
        '{name} was not activated: the final checks refused it.'),
    'UI_CHANGE_SIGNER_MISSING': (
        'Su questo server manca il programma che firma le attivazioni: '
        'lo installa una volta l’amministratore del server.',
        'This server lacks the program that signs activations: '
        'the server administrator installs it once.'),
    'UI_CHANGE_SIGNER_SILENT': (
        'Il programma di firma non ha risposto: nulla è stato attivato, puoi riprovare.',
        'The signing program did not answer: nothing was activated, you can try again.'),
    'ERR_CHANGE_NO_CANDIDATE': (
        'Per questa proposta non c’è uno strumento pronto in attesa.',
        'No ready tool is waiting for this proposal.'),
    'ERR_CHANGE_ACTIVATION_RUNNING': (
        'L’attivazione è già in corso.', 'The activation is already in progress.'),
    'UI_CHANGE_EXTERNAL_PREF_LABEL': (
        'Modello esterno per finire uno strumento dopo tre tentativi:',
        'External model to finish a tool after three attempts:'),
    'UI_CHANGE_EXTERNAL_PREF_ASK': ('chiedimelo ogni volta', 'ask me each time'),
    'UI_CHANGE_EXTERNAL_PREF_NEVER': ('mai', 'never'),
    'UI_CHANGE_EXTERNAL_PREF_ALWAYS': ('sempre, senza chiedere', 'always, without asking'),
    'MSG_CHANGE_EXTERNAL_PUSH': (
        'Ho provato tre volte a costruire lo strumento «{name}» e non ci sono riuscito. '
        'Posso usare il modello esterno per finirlo? Gli manderei la richiesta d’esempio, '
        'la descrizione, le prove e il codice generato.',
        'I tried three times to build the tool “{name}” and did not succeed. '
        'May I use the external model to finish it? I would send it the sample request, '
        'the description, the test cases and the generated code.'),
    'MSG_CHANGE_READY_PUSH': (
        'Lo strumento «{name}» è pronto e provato. Lo attivo?\n'
        'Che cosa fa: {does}\n'
        'Permessi che chiede: {permissions}\n'
        '{undo}\n'
        'Prove su cui è stato costruito: {cases}\n'
        'Puoi rispondere qui sotto oppure nella pagina Modifiche, dove si leggono anche le prove.',
        'The tool “{name}” is ready and tested. Shall I activate it?\n'
        'What it does: {does}\n'
        'Permissions it asks for: {permissions}\n'
        '{undo}\n'
        'Cases it was built on: {cases}\n'
        'You can answer below or on the Changes page, where the cases can be read too.'),
    'MSG_CHANGE_ACTIVATION_REQUESTED': (
        'Va bene: attivo «{name}». Partono i controlli finali e la firma: può volerci qualche '
        'minuto. Ti scrivo qui com’è andata.',
        'All right: I am activating “{name}”. The final checks and the signature start now: it '
        'can take a few minutes. I will write here how it went.'),
    'MSG_CHANGE_DECLINED_BY_PERSON': (
        'Va bene: «{name}» non viene attivato e il candidato è stato tolto. Non lo ripropongo '
        'per trenta giorni. Se è stato un errore, si riapre dalla pagina Modifiche, scheda «Scartate».',
        'All right: “{name}” is not activated and the candidate was removed. I will not propose '
        'it again for thirty days. If it was a mistake, reopen it from the Changes page, '
        '“Rejected” tab.'),
    'MSG_CHANGE_ACTIVATION_REFUSED_PUSH': (
        '«{name}» NON è stato attivato: {reason}. Lo strumento non è entrato in uso e il '
        'candidato è stato tolto.',
        '“{name}” was NOT activated: {reason}. The tool did not enter service and the candidate '
        'was removed.'),
    'MSG_CHANGE_ACTIVATION_NOT_DONE': (
        'Non ho potuto completare l’attivazione di «{name}»: il programma di firma non ha '
        'risposto. Lo strumento resta in attesa: puoi riprovare.',
        'I could not complete the activation of “{name}”: the signing program did not answer. '
        'The tool is still waiting: you can try again.'),
    'MSG_ACTIVATION_REFUSED_UNCERTAIN': (
        'il revisore finale non è sicuro che lo strumento faccia davvero ciò che la sua '
        'descrizione promette',
        'the final reviewer is not sure the tool really does what its description promises'),
    'MSG_ACTIVATION_REFUSED_MISALIGNED': (
        'il revisore finale ha trovato che lo strumento non fa ciò che la sua descrizione promette',
        'the final reviewer found that the tool does not do what its description promises'),
    'MSG_ACTIVATION_REFUSED_TESTS': (
        'nella revisione finale non ha superato le sue prove ({cases})',
        'in the final review it did not pass its cases ({cases})'),
    'MSG_ACTIVATION_REFUSED_OTHER': (
        'un controllo finale non è passato ({code})',
        'a final check did not pass ({code})'),
    'MSG_CHANGE_EXTERNAL_ALLOWED': (
        'Va bene: finisco «{name}» con il modello esterno. Ti avviso quando è pronto.',
        'All right: I will finish “{name}” with the external model. I will tell you when it is ready.'),
    'MSG_CHANGE_CLOSED_BY_PERSON': (
        'Proposta «{name}» chiusa: non la ripropongo per trenta giorni. '
        'Se è stato un errore, si riapre dalla pagina Modifiche, scheda «Scartate».',
        'Proposal “{name}” closed: I will not propose it again for thirty days. '
        'If it was a mistake, reopen it from the Changes page, “Rejected” tab.'),
    'MSG_CHANGE_ALREADY_ANSWERED': (
        'Questa domanda ha già avuto risposta: non ho cambiato niente.',
        'This question has already been answered: I changed nothing.'),
    'MSG_CHANGE_ADMIN_ONLY': (
        'A questa domanda può rispondere solo l’amministratore.',
        'Only the administrator can answer this question.'),
    'UI_CHANGE_BTN_EXTERNAL': ('Usa il modello esterno', 'Use the external model'),
    'UI_CHANGE_BTN_CLOSE': ('Chiudi la proposta', 'Close the proposal'),
    'ERR_CHANGE_EXTERNAL_NOT_OFFERED': (
        'Per questa proposta non c’è una richiesta di modello esterno in attesa.',
        'No external-model request is pending for this proposal.'),
    'MSG_LEARNING_GAP_SUMMARY': (
        'Funzione mancante chiesta {n} volte, per esempio: «{query}».',
        'Missing capability requested {n} times, for example: “{query}”.'),
    'MSG_LEARNING_GAP_RATIONALE': (
        'Nessuno strumento esegue {target}. Metnos prepara da solo un candidato e lo prova; '
        'entra in uso solo dopo i controlli finali e il tuo sì, chiesto una volta in questa pagina.',
        'No tool performs {target}. Metnos prepares a candidate by itself and tests it; '
        'it is used only after the final checks and your yes, asked once on this page.'),
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
