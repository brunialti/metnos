#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Add LRE console labels to a seed without replacing its catalog."""
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
    "UI_DURABLE_DISMISS_SELECTED_FAILED": (
        "Non è stato possibile cancellare {count} log: restano selezionati.",
        "Could not delete {count} logs: they remain selected."),
}


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
        i18n._checkpoint(conn)
    finally:
        conn.close()
        i18n._conn = None
    return len(TRANSLATIONS) * 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "install/data/i18n_seed.sqlite")
    args = parser.parse_args()
    print(f"LRE: {sync(args.db)} translations added to {args.db}")
