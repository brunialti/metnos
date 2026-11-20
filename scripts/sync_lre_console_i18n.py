#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bring the LRE texts of a seed up to date without replacing its catalog."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = {
    "ERR_DURABLE_MODEL_RESOURCE_UNAVAILABLE": (
        "La risorsa del modello è momentaneamente non disponibile. Riprova a far ripartire il job appena sarà nuovamente disponibile.",
        "The model resource is temporarily unavailable. Try restarting the job when it becomes available again."),
    "UI_DURABLE_ERROR_MODEL_RESOURCE_UNAVAILABLE": (
        "Risorsa {resource} momentaneamente non disponibile. Riprova a far ripartire il job appena sarà nuovamente disponibile.",
        "Resource {resource} is temporarily unavailable. Try restarting the job when it becomes available again."),
    "UI_DURABLE_MODEL_RESOURCE": ("modello LLM", "LLM model"),
    "UI_DURABLE_SELECT_ALL": (
        "Seleziona tutti i log mostrati", "Select all shown logs"),
    "UI_DURABLE_SELECT_LOG": ("Seleziona log: {name}", "Select log: {name}"),
    "UI_DURABLE_SELECTION_COUNT": ("Selezionati: {count}", "Selected: {count}"),
    "UI_DURABLE_SELECTION_HELP": (
        "Puoi cancellare solo log di lavori conclusi con i temporanei ripuliti.",
        "You can only delete logs of finished jobs whose temporary files have been cleaned up."),
    "UI_DURABLE_DISMISS_SELECTED": ("Cancella selezionati", "Delete selected"),
    "UI_DURABLE_DISMISS_SELECTED_PENDING": (
        "Cancellazione in corso…", "Deleting…"),
    "UI_DURABLE_DISMISS_SELECTED_CONFIRM": (
        "Cancellare dall'elenco i {count} log selezionati?",
        "Delete the {count} selected logs from the list?"),
    "UI_DURABLE_DISMISS_SELECTED_DONE": (
        "Log cancellati: {count}.", "Logs deleted: {count}."),
    # What a tool names as the cause of a stop, or of one item it lost.
    "UI_DURABLE_CAUSE_NOT_FOUND": (
        "Percorso o elemento non trovato", "Path or item not found"),
    "UI_DURABLE_CAUSE_PERMISSION_DENIED": ("Permesso negato", "Permission denied"),
    "UI_DURABLE_CAUSE_IO_ERROR": (
        "Lettura o scrittura non riuscita", "Read or write failed"),
    "UI_DURABLE_CAUSE_INVALID_ARGS": ("Argomenti non validi", "Invalid arguments"),
    "UI_DURABLE_CAUSE_INVALID_INPUT": (
        "Dati in ingresso non validi", "Invalid input"),
    "UI_DURABLE_CAUSE_RESOURCE_UNAVAILABLE": (
        "Risorsa non disponibile", "Resource unavailable"),
    "UI_DURABLE_CAUSE_DEPENDENCY_UNAVAILABLE": (
        "Un componente necessario non è disponibile",
        "A required component is unavailable"),
    "UI_DURABLE_CAUSE_UNSUPPORTED": (
        "Operazione non supportata", "Unsupported operation"),
    "UI_DURABLE_CAUSE_TIMEOUT": ("Tempo scaduto", "Timed out"),
    "UI_DURABLE_CAUSE_REFUSED_FOLDER": ("È una cartella", "It is a folder"),
    "UI_DURABLE_CAUSE_REFUSED_SYSTEM_FILE": ("È un file di sistema", "It is a system file"),
    "MSG_LRE_REMOVAL_NOT_DONE": (
        "Non ho eseguito la cancellazione: manca la tua conferma.",
        "I did not delete anything: your confirmation is needed."),
    "UI_DURABLE_CAUSE_ITEM_FAILED": (
        "Elemento non riuscito, causa non indicata",
        "Item failed, cause not given"),
    # The pushed notice of a job: which one, how it ended.
    "MSG_DURABLE_WHAT_REQUEST": ("«{query}»", "“{query}”"),
    "MSG_DURABLE_WHAT_PHOTO_INDEX": (
        "Aggiornamento dell'indice delle foto", "Photo index update"),
    "MSG_DURABLE_WHAT_TOOL": ("Lavoro {tool}", "Job {tool}"),
    "MSG_DURABLE_WHAT_JOB": ("Un lavoro lungo", "A long job"),
    "MSG_DURABLE_NOTICE_COMPLETED": ("{what}: completato.", "{what}: completed."),
    "MSG_DURABLE_LOST_ITEMS": (
        "Elementi non riusciti: {count}.", "Items that failed: {count}."),
    "MSG_DURABLE_NOTICE_NEEDS_ATTENTION": (
        "{what}: si è fermato e serve il tuo intervento, nella pagina dei lavori lunghi.",
        "{what}: it stopped and needs you, in the long jobs page."),
    "MSG_DURABLE_NOTICE_FAILED": ("{what}: non completato.", "{what}: not completed."),
    "MSG_DURABLE_NOTICE_CAUSE": ("Motivo: {cause}.", "Reason: {cause}."),
    "MSG_LRE_ANSWER_CUT": (
        "La risposta è lunga: qui c'è l'inizio ({shown} caratteri su {total}).",
        "The answer is long: this is its beginning ({shown} characters of {total})."),
    "MSG_LRE_ANSWER_WILL_FOLLOW": (
        "Ti scrivo qui la risposta quando finisce.",
        "I will write the answer here when it ends."),
    "UI_DURABLE_DISMISS_SELECTED_FAILED": (
        "Non è stato possibile cancellare {count} log: restano selezionati.",
        "Could not delete {count} logs: they remain selected."),
    # A request that asks for more than its long step: what is promised when
    # the job starts, and what is said when the rest cannot follow.
    "MSG_LRE_REST_WILL_FOLLOW": (
        "Quando finisce, completo il resto della richiesta e ti scrivo qui la risposta.",
        "When it ends, I will do the rest of the request and write the answer here."),
    "MSG_LRE_REST_NOT_KEPT": (
        "Non sono riuscito a tenere da parte il resto della richiesta: quando il lavoro finisce, chiedimi il resto.",
        "I could not keep the rest of the request: when the job ends, ask me for the rest."),
    "MSG_LRE_REST_NOT_DONE": (
        "Il resto della richiesta non è stato eseguito.",
        "The rest of the request was not carried out."),
    "MSG_LRE_REST_INTERRUPTED": (
        "Il lavoro lungo è finito, ma il resto della richiesta si è interrotto prima della fine. Non lo ripeto da solo, per non fare due volte la stessa cosa: controlla che cosa è stato fatto e chiedimi di nuovo ciò che manca.",
        "The long job ended, but the rest of the request was interrupted before its end. I do not repeat it by myself, so that nothing is done twice: check what was done and ask me again for what is missing."),
    "MSG_LRE_REST_FAILED": (
        "Questo è ciò che ha trovato il lavoro lungo. Il resto della richiesta non è andato a buon fine:",
        "This is what the long job found. The rest of the request did not succeed:"),
    "MSG_LRE_REST_NEEDS_YOU": (
        "Un passo successivo ha bisogno di una tua risposta che dopo un lavoro lungo non so ancora chiederti. Mi sono fermato lì: chiedimi di nuovo la parte che manca.",
        "A later step needs an answer from you that I cannot yet ask for after a long job. I stopped there: ask me again for the part that is missing."),
    "MSG_LRE_CONSENT_IN_PAGE": (
        "Per rispondere apri questa conversazione nella pagina di Metnos: lì trovi i pulsanti.",
        "To answer, open this conversation in the Metnos page: the buttons are there."),
    "MSG_CONSENT_GATE_AFTER_LONG_JOB": (
        "⚠ Il lavoro lungo è finito. Sto per {action} {n} elementi: confermi? (reversibile con «annulla»)",
        "⚠ The long job has ended. I am about to {action} {n} items: confirm? (reversible with «undo»)"),
    "MSG_CONSENT_GATE_AFTER_LONG_JOB_ANY": (
        "⚠ Il lavoro lungo è finito. Sto per {action} degli elementi: confermi? (reversibile con «annulla»)",
        "⚠ The long job has ended. I am about to {action} some items: confirm? (reversible with «undo»)"),
    # The long step could not be prepared as it was asked: said in plain words,
    # with what the person can write differently.
    "ERR_LRE_EXECUTOR_CONTRACT_UNSUPPORTED": (
        "Non sono riuscito a preparare questo lavoro lungo ({tool}): non ho avviato nulla. Riprova scrivendo per esteso su che cosa deve lavorare, per esempio il percorso della cartella, e su quale dispositivo se non è il server.",
        "I could not prepare this long job ({tool}): nothing was started. Try again writing out what it must work on, for example the folder path, and on which device if it is not the server."),
    "ERR_LRE_PLAN_NOT_ADMISSIBLE": (
        "Questa richiesta unisce un lavoro lungo ad altri passi in un modo che non so ancora portare a termine da solo. Non ho avviato nulla. Chiedi prima il lavoro lungo da solo; quando arriva la risposta, chiedi il resto.",
        "This request joins a long job with other steps in a way I cannot yet carry through by myself. Nothing was started. Ask for the long job alone first; when its answer arrives, ask for the rest."),
    "ERR_LRE_PLAN_SEVERAL_LONG": (
        "Questa richiesta contiene più lavori lunghi ({tools}) e so portarne avanti uno per richiesta. Non ho avviato nulla: chiedili uno alla volta.",
        "This request contains several long jobs ({tools}) and I can carry one per request. Nothing was started: ask for them one at a time."),
    "ERR_LRE_PLAN_LONG_NEEDS_STEP": (
        "In questa richiesta il lavoro lungo ({tool}) ha bisogno del risultato di un altro passo, e non so ancora tenerli insieme. Non ho avviato nulla. Chiedi prima quel passo; poi chiedi il lavoro lungo, scrivendo per esteso su che cosa deve lavorare.",
        "In this request the long job ({tool}) needs the result of another step, and I cannot yet keep them together. Nothing was started. Ask for that step first; then ask for the long job, writing out what it must work on."),
    "ERR_LRE_PLAN_LONG_NEEDS_VALUE": (
        "Il lavoro lungo ({tool}) ha bisogno di un valore che conosco solo durante la conversazione, per esempio «questa cartella». Non ho avviato nulla: ripeti la richiesta scrivendo per esteso su che cosa deve lavorare, per esempio il nome della cartella.",
        "The long job ({tool}) needs a value I know only during the conversation, such as «this folder». Nothing was started: repeat the request writing out what it must work on, for example the name of the folder."),
    "ERR_LRE_PLAN_STEPS_BEFORE_LONG": (
        "In questa richiesta ci sono passi prima del lavoro lungo ({tool}), e so tenerli in ordine solo quando tutti si limitano a leggere. Non ho avviato nulla. Chiedi prima quella parte; quando è fatta, chiedi il resto.",
        "In this request some steps come before the long job ({tool}), and I can keep them in order only when all of them just read. Nothing was started. Ask for that part first; when it is done, ask for the rest."),
}

# Texts no code uses any longer: taken out of a seed that still has them.
RETIRED = (
    # It pointed to an answer kept elsewhere; the answer now reaches the person.
    "MSG_DURABLE_NOTICE_ANSWER_IN_CHAT",
)


def sync(path: Path) -> int:
    sys.path.insert(0, str(ROOT / "runtime"))
    import i18n

    i18n.DB_PATH = path
    i18n._SEED_DB_PATH = path
    i18n._conn = None
    conn = i18n._open()
    try:
        for key, (it, en) in sorted(TRANSLATIONS.items()):
            current = dict(conn.execute(
                "SELECT lang, text FROM i18n WHERE key=? AND needs_translation=0",
                (key,),
            ))
            if current.get("it") == it and current.get("en") == en:
                continue
            i18n.set_catalog_translations(key, {"it": it, "en": en}, source_lang="it")
        i18n.delete_keys(RETIRED)
        i18n._checkpoint(conn)
    finally:
        conn.close()
        i18n._conn = None
    return len(TRANSLATIONS) * 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    print(f"LRE: {sync(args.db)} translations current in {args.db}")
