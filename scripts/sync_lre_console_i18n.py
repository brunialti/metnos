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
