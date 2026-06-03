#!/usr/bin/env python3
"""write_files_doc — dispatcher canonical Google Docs append (24/5/2026).

Tool UNICO per appendere testo alla fine di un Google Doc esistente.
Append e' semantica `write` con tag implicito (§2.2: no verbo separato).
Dispatcher sottile che instrada al backend giusto in base a `client`
(default `google_workspace`).

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

§2.3 reverse_pattern non applicabile: l'append modifica il flow del doc
e ricostruire l'indice di insert pre-append richiederebbe stato. Vedi
docs_append in google_api.py per la logica end-of-body. `revertible=false`.

Contratto:
    stdin: JSON {document_id, text,
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, n_written, document_id, content_length,
                  characters_appended, results, used}
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
from backends.files import google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
}


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    client = args.get("client") or "google_workspace"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    return backend.append_doc(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False,
                                      "error": _msg("ERR_JSON_INVALID"),
                                      "error_class": "invalid_args",
                                      "results": [], "used": 0,
                                      "n_written": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
