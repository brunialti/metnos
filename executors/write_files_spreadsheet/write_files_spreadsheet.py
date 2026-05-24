#!/usr/bin/env python3
"""write_files_spreadsheet — dispatcher canonical Google Sheets write (24/5/2026).

Tool UNICO per scrivere celle in un range di Google Sheets. Dispatcher
sottile che instrada al backend giusto in base a `client` (default
`google_workspace`).

`mode='overwrite'` sovrascrive il range (sheets.values.update).
`mode='append'` aggiunge in coda (sheets.values.append). Append = write
con flag; non c'e' verbo separato §2.2.

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

§2.3 reverse_pattern non applicabile (snapshot blob delle celle previe
sarebbe oneroso e cross-sheet inaffidabile). `revertible=false` esplicito.

Contratto:
    stdin: JSON {spreadsheet_id, range, values: [[...]], mode?: 'overwrite'|'append',
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, n_written, updated_cells, updated_rows, range,
                  spreadsheet_id, mode, results, used}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from backends.files import google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
}


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    client = args.get("client") or "google_workspace"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": f"unsupported spreadsheet client '{client}'. "
                         f"Available: {avail}",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    return backend.write_spreadsheet(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False,
                                      "error": f"invalid input json: {e}",
                                      "error_class": "invalid_args",
                                      "results": [], "used": 0,
                                      "n_written": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
