#!/usr/bin/env python3
"""create_files_doc — dispatcher canonical Google Docs create (24/5/2026).

Tool UNICO per creare un nuovo Google Doc. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `google_workspace`).

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

§2.3 reverse_pattern='delete_files_by_id': il backend ritorna `_undo`
embed con `ids=[document_id]` + scope `client=google_workspace`;
il runtime instrada via `reverse_patterns_patch::delete_files_by_id`.

Contratto:
    stdin: JSON {title, body?,
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, n_created, document_id, web_view_url, title,
                  results, used, _undo}
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
                "results": [], "used": 0, "n_created": 0}
    client = args.get("client") or "google_workspace"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    return backend.create_doc(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False,
                                      "error": _msg("ERR_JSON_INVALID"),
                                      "error_class": "invalid_args",
                                      "results": [], "used": 0,
                                      "n_created": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
