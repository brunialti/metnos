#!/usr/bin/env python3
"""Regenerate the approved-mutation and partial-list texts on the current seed."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import sync_undo_request_i18n as seed

TRANSLATIONS = {
    "MSG_ROWS_NOT_PREPARED": ("Trovati {count} elementi. Non è stato possibile preparare l’elenco per questa operazione: nessuna conferma richiesta e nessuna modifica eseguita.", "Found {count} items. The list could not be prepared for this operation: no confirmation was requested and no changes were made."),
    "ERR_ROWS_NOT_ADMISSIBLE": ("Non è stato possibile registrare questa operazione sull’elenco. Nessuna modifica eseguita.", "This operation on the list could not be registered. No changes were made."),
    "MSG_ORCH_CONTINUATION_FAILED": ("Non è stato possibile proseguire la richiesta.", "The request could not be continued."),
    "ERR_UNDO_ROWS_DRAIN": ("Lavoro in arresto; annullamento non ancora eseguito. Ripeti «annulla».", "Job stopping; undo has not run yet. Request undo again."),
    "ERR_UNDO_ROWS_UNCERTAIN": ("Nel blocco interrotto: {done} effetti riconosciuti, {uncertain} da verificare. Annullamento sospeso; nessun effetto incerto è stato annullato.", "In the interrupted block: {done} effects recognized, {uncertain} need verification. Undo suspended; no uncertain effect was reversed."),
    "MSG_LIST_PARTIAL": ("Fatti {done} su {total}.", "Done {done} of {total}."),
    "MSG_LIST_STOPS_PLAN": ("I passi successivi non sono stati eseguiti.", "The following steps were not executed."),
    "MSG_LIST_CAUSE_TIME": ("{count} non raggiunti entro il tempo disponibile", "{count} not reached within the available time"),
    "MSG_LIST_CAUSE_PERMISSION": ("{count} senza il permesso necessario", "{count} without the required permission"),
    "MSG_LIST_CAUSE_CHANGED": ("{count} cambiati o da verificare dopo un’interruzione", "{count} changed or requiring verification after an interruption"),
    "MSG_LIST_CAUSE_FIXED": ("{count} esclusi dalle regole dello strumento", "{count} excluded by the tool’s rules"),
    "MSG_LIST_CAUSE_OTHER": ("{count} rifiutati per altre cause, indicate nei risultati", "{count} refused for other reasons listed in the results"),
    "MSG_LIST_UNREPORTED": ("{count} altri elementi non completati.", "{count} other items not completed."),
    "MSG_APPROVED_MUTATION_RESULT": ("Fatti {done} su {total}. Restano {left} elementi non raggiunti.", "Done {done} of {total}. {left} items remain unreached."),
    "MSG_APPROVED_MUTATION_REFUSED": ("{count} elementi non fatti: {reasons}.", "{count} items not done: {reasons}."),
    "MSG_APPROVED_MUTATION_NOT_SUBMITTED": ("Il resto non è stato affidato al servizio dei lavori lunghi. Gli effetti già eseguiti si possono annullare.", "The remainder was not submitted to the long-job service. Effects already performed can be undone."),
    "MSG_APPROVED_MUTATION_STOPPED": ("Lavoro fermato da «annulla». Prima dell’arresto: fatti {done} su {total}; {left} non raggiunti. Gli effetti registrati entrano nell’annullamento della richiesta.", "Job stopped by ‘undo’. Before stopping: {done} of {total} done; {left} not reached. Recorded effects are included in undoing the request."),
    "MSG_MUTATION_QUESTION_FIXED": ("{total} elementi in tutto; non spostabili: {reasons}. I numeri per cartella contano tutti gli elementi.", "{total} items in total; items that cannot be moved: {reasons}. Folder counts include all items."),
    "MSG_MUTATION_QUESTION_CAUSE": ("{count}: {cause}", "{count}: {cause}"),
    "MSG_MUTATION_QUESTION_TOO_LONG": ("La domanda non entra nei 600 caratteri senza perdere informazioni necessarie. Nessuna conferma richiesta: abbrevia i nomi o dividi la richiesta.", "The question cannot fit within 600 characters without losing required facts. No confirmation was requested: shorten the names or split the request."),
}

def sync(path):
    previous = seed.TRANSLATIONS
    try:
        seed.TRANSLATIONS = TRANSLATIONS
        return seed.sync(path)
    finally:
        seed.TRANSLATIONS = previous

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=seed.ROOT / "install/data/i18n_seed.sqlite")
    print(sync(parser.parse_args().db))
