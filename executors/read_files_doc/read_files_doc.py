#!/usr/bin/env python3
"""read_files_doc — dispatcher canonical Google Docs read (24/5/2026).

Tool UNICO per leggere il contenuto testuale di un Google Doc. Dispatcher
sottile che instrada al backend giusto in base a `client` (default
`google_workspace`, unico backend disponibile per ora).

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

Contratto:
    stdin: JSON {document_id,
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, body_text, title, document_id,
                  entries: [{document_id, title, body_text, content_length}],
                  used, available_total}
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
from backends.files import google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
}


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    client = args.get("client") or "google_workspace"
    backend = _HANDLERS.get(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    return backend.read_doc(args)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args", "entries": [], "used": 0})


if __name__ == "__main__":
    main()
