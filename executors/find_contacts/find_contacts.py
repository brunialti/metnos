#!/usr/bin/env python3
"""find_contacts — dispatcher canonical contacts (rubrica esterna).

Tool UNICO per cercare contatti per nome/email/telefono. Dispatcher
sottile che instrada al backend giusto in base a `client`.

Distinzione vs `persons` (ADR 0113 + 0137):
- `persons` = utenti del SISTEMA Metnos (chi parla a Metnos via
  chat/pairing): storage `~/.local/share/metnos/persons.sqlite`.
- `contacts` = rubrica indirizzi di TERZI (di chi Metnos parla:
  numeri telefono, email di amici, indirizzi business): storage
  esterno (Google People API, vCard locale futura).

Default `client="google_workspace"` (no rubrica locale builtin per
ora). Quando arrivera' un backend `local` (vCard import), il default
diventera' `local` per coerenza con altri verbi find_*.

Contratto:
    stdin: JSON {query?, max_results?, client?: 'google_workspace'}
    stdout: JSON {ok, entries: [{name, emails, phones, id}], used,
                  available_total, truncated?, messaging_source}
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
from backends.contacts import google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
}


def invoke(args):
    client = args.get("client") or "google_workspace"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.find(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False,
                                      "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
