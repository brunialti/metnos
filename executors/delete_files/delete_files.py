#!/usr/bin/env python3
"""delete_files — dispatcher canonical (19/5/2026 v4).

Tool UNICO per rimuovere uno o piu' file (NON directory: usa
`delete_dirs`). Dispatcher sottile che instrada al backend (default
`local`).

Reversibile §2.3: ogni file rimosso ha backup blob in
`<HISTORY>/<turn>/blob/<sha256>.bin`. Reverse pattern `restore_blob_backup`.

Contratto:
    stdin: JSON {paths: list[str], client?: 'local' (default)}
    stdout: JSON {ok, ok_count, fail_count,
                  results: [{path, removed, blob_path, blob_sha256}],
                  failed}
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
from backends.files import local  # noqa: E402

_HANDLERS = {
    "local": local,
}


def invoke(args):
    client = args.get("client") or "local"
    backend = _HANDLERS.get(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.delete_files(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
