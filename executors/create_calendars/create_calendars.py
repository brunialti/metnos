#!/usr/bin/env python3
"""create_calendars — crea un CALENDARIO-contenitore Google (non un evento).

Dispatcher §7.2. Backend FORZATO `google_workspace` (i calendari-contenitore
sono un concetto Google; il `.ics` locale gestisce un solo calendario) → NON
scelta dell'LLM (§7.9). Distinto da `create_events` (evento DENTRO un calendario).

SEMPRE CON CONFERMA (richiesta utente 3/6): la prima invocazione ritorna
`needs_inputs` con un campo NOME pre-compilato e MODIFICABILE; alla conferma il
runtime ri-invoca con `_confirmed=true` (resume_executor_with_values) e crea.
Accetta "crea calendario" (default nome) e 'crea calendario "test pippo"'
(nome estratto in `summary`).

§2.3 reverse_pattern: `delete_calendars_by_id`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from backends.events import google_workspace  # noqa: E402

_DEFAULT_NAME = "Metnos"


def _find_owned_calendar_id(name):
    """ID del calendario OWNED con `name` (case-insensitive), o None. Solo di
    proprietà: un nome che collide con un calendario condiviso/iscritto NON
    blocca la creazione (non è un doppione tuo)."""
    try:
        lst = google_workspace.list_calendars({})
    except Exception:
        return None
    if not (isinstance(lst, dict) and lst.get("ok")):
        return None
    tgt = (name or "").strip().lower()
    for e in (lst.get("entries") or []):
        if ((e.get("summary") or "").strip().lower() == tgt
                and (e.get("access_role") or "").lower() == "owner"
                and e.get("id")):
            return e["id"]
    return None


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args", "results": [], "n_created": 0}
    a = dict(args)
    client = a.get("client")
    if client and client != "google_workspace":
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE",
                              what=f"client '{client}' (i calendari sono solo Google)"),
                "error_class": "invalid_args", "results": [], "n_created": 0}
    proposed = (a.get("summary") or a.get("name") or a.get("title")
                or _DEFAULT_NAME)
    # SEMPRE conferma: prima invocazione → dialog con nome modificabile.
    if not a.get("_confirmed"):
        return {
            "ok": True,
            "decision": "needs_inputs",
            "needs_inputs": {
                "title": "Nuovo calendario",
                "description": ("Crea un calendario Google. Modifica il nome se "
                                "vuoi, poi Invia per confermare o Annulla."),
                "dialog": [{
                    "var": "summary",
                    "prompt": "Nome del calendario",
                    "schema": {"kind": "text", "placeholder": proposed},
                    "default": proposed,
                    # vuoto → ricade sul default (executor §68), no errore form.
                    "optional": True,
                }],
                # form: campo NOME editabile pre-compilato + pulsanti
                # Invia/Annulla (HTTP). Su canali non-HTTP degrada a dialogue
                # lato runtime. Evita che un «si» di conferma venga preso come
                # nome del calendario (bug live 3/6).
                "fmt": "form",
                "on_complete": {
                    "type": "resume_executor_with_values",
                    "executor": "create_calendars",
                    # values del dialog (summary) override; se vuoto resta proposed.
                    "args_base": {**a, "_confirmed": True, "summary": proposed},
                },
            },
        }
    # Confermato: il nome eventualmente vuoto ricade sul default.
    if not (isinstance(a.get("summary"), str) and a["summary"].strip()):
        a["summary"] = proposed
    name = a["summary"]

    # UPSERT / dedup-on-create (§2.9-spirito): se esiste GIÀ un calendario
    # OWNED con lo stesso nome, non creare un doppione silenzioso → conferma.
    # `dup_confirm` assente = primo passaggio (controlla); presente = scelta.
    dup_confirm = a.get("dup_confirm")
    if dup_confirm is None:
        existing_id = _find_owned_calendar_id(name)
        if existing_id:
            return {
                "ok": True,
                "decision": "needs_inputs",
                "needs_inputs": {
                    "title": "Calendario già esistente",
                    "dialog": [{
                        "var": "dup_confirm",
                        "prompt": _msg("MSG_CALENDAR_EXISTS_CONFIRM", name=name),
                        "schema": {"kind": "yes_no"},
                    }],
                    "fmt": "form",
                    "on_complete": {
                        "type": "resume_executor_with_values",
                        "executor": "create_calendars",
                        "args_base": {**a, "_existing_id": existing_id},
                    },
                },
            }
    else:
        yes = dup_confirm
        if isinstance(yes, str):
            yes = yes.strip().lower() in ("si", "sì", "yes", "y", "ok", "true", "1")
        if not yes:
            # No duplicato: tieni l'esistente (onestà §2.8).
            return {"ok": True, "used": 0, "ok_count": 0,
                    "summary": _msg("MSG_CALENDAR_NOT_DUPLICATED", name=name),
                    "results": [{"ok": True, "calendar_id": a.get("_existing_id"),
                                 "summary": name, "reused": True, "kind": "calendar"}]}
        # Sì: procede a creare il secondo omonimo (fallthrough).

    res = google_workspace.create_calendar(a)
    # summary user-facing (i18n) → la chat mostra un messaggio pulito, non il
    # JSON grezzo del result (resume_executor_with_values fallback orchestration).
    if isinstance(res, dict) and res.get("ok") and "summary" not in res:
        res["summary"] = _msg("MSG_CALENDAR_CREATED", name=a["summary"])
    return res


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args", "results": [], "n_created": 0})


if __name__ == "__main__":
    main()
