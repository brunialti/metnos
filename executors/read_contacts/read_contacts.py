#!/usr/bin/env python3
"""read_contacts — lettura puntuale di un contatto rubrica per id/alias.

Tool per fetch di UN contatto specifico per `contact_id` (slug name) o
alias `email` / `phone` esatto. Distinto da `find_contacts` che ritorna
N risultati per query substring.

Contratto:
    stdin: JSON {contact_id? | email? | phone?, client?}
    stdout: JSON {ok, entries: [{name, emails, phones, id}], used,
                  messaging_source}
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
    return backend.read(args)


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
