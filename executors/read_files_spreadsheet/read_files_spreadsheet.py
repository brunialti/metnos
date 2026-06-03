#!/usr/bin/env python3
"""read_files_spreadsheet — dispatcher canonical Google Sheets read (24/5/2026).

Tool UNICO per leggere un range di celle da Google Sheets. Dispatcher
sottile che instrada al backend giusto in base a `client` (default
`google_workspace`, unico backend disponibile per ora).

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.
NIENTE registry magico, dispatch table esplicito (§7.2 + §7.9).

Contratto:
    stdin: JSON {spreadsheet_id, range?,
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, values: [[...]], range, spreadsheet_id,
                  entries, used, available_total}
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
from backends.files import google_workspace, local  # noqa: E402

# §10.3 self-hosted default: `local` (.xlsx/.csv su path). `google_workspace`
# (Google Sheets) e' opt-in.
_HANDLERS = {
    "local": local,
    "google_workspace": google_workspace,
}


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    client = args.get("client") or "local"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    return backend.read_spreadsheet(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False,
                                      "error": _msg("ERR_JSON_INVALID"),
                                      "error_class": "invalid_args",
                                      "entries": [], "used": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
