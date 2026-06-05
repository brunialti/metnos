#!/usr/bin/env python3
"""write_files_spreadsheet — dispatcher canonical Google Sheets write (24/5/2026).

Tool UNICO per scrivere celle in un range di Google Sheets. Dispatcher
sottile che instrada al backend giusto in base a `client` (default
`google_workspace`).

`mode='overwrite'` sovrascrive il range (sheets.values.update).
`mode='append'` aggiunge in coda (sheets.values.append). Append = write
con flag; non c'e' verbo separato §2.2.

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

§2.3 reversible: il backend LOCALE salva un blob dei bytes previ prima di
modificare → undo via `restore_blob_backup` (file preesistente) o
`delete_created_paths` (file nuovo). Backend Google: nessun blob locale,
l'undo riporta onestamente nulla-ribaltato (§2.8).

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
from messages import get as _msg  # noqa: E402
from backends.files import google_workspace, local  # noqa: E402

# §10.3 self-hosted default: `local` (.xlsx/.csv su path). `google_workspace`
# (Google Sheets, richiede spreadsheet_id Drive) e' opt-in.
_HANDLERS = {
    "local": local,
    "google_workspace": google_workspace,
}


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    client = args.get("client") or "local"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    return backend.write_spreadsheet(args)


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
