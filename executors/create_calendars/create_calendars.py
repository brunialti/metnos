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
from backends.events import google_workspace  # noqa: E402

_DEFAULT_NAME = "Metnos"


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
                "dialog": [{
                    "var": "summary",
                    "prompt": (f"Creo un nuovo calendario Google. Nome "
                               f"(modificabile, default «{proposed}»):"),
                    "schema": {"kind": "text"},
                }],
                "fmt": "dialogue",
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
    return google_workspace.create_calendar(a)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps(
            {"ok": False, "error": _msg("ERR_JSON_INVALID"),
             "error_class": "invalid_args", "results": [], "n_created": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
