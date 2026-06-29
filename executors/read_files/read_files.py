#!/usr/bin/env python3
"""read_files — dispatcher canonical (sequel di read_messages, 13/5/2026).

Tool UNICO per leggere il contenuto di UN file. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (filesystem locale).
- Backend builtin in `runtime/backends/files/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

Predisposizione plugin esterni:
- Quando arrivera' l'ADR plugin esterni, `_HANDLERS` sara' arricchito
  da loader scan di `~/.local/share/metnos/plugins/files-*/backends/`.

Contratto:
    stdin: JSON {path, encoding?, max_bytes?, tail_bytes?, offset?,
                 client?: 'local' (default)}
    stdout: JSON {ok, content, metadata, truncated?, used?,
                  available_total?, cap_field?, cap_value?}
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
from backends.files import local, google_workspace  # noqa: E402

# Dispatch table read-side (predisposta a plugin esterni).
# Valori = modulo: attribute lookup `module.read` a call-time per testabilita'.
_HANDLERS = {
    "google_workspace": google_workspace,
    "local": local,
}


def invoke(args):
    client = args.get("client") or "local"
    backend = _HANDLERS.get(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    # Per google_workspace: se richiesto `dst_path`/`dst_dir` → download
    # vero (bytes su disco). Altrimenti `read` = metadata Drive.
    if client == "google_workspace" and (args.get("dst_path") or args.get("dst_dir")):
        return backend.download(args)
    # Attribute lookup a call-time: i test possono patchare `backend.read`.
    return backend.read(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
