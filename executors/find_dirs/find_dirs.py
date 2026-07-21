#!/usr/bin/env python3
"""find_dirs — dispatcher canonical (sequel di read_messages, 13/5/2026).

Tool UNICO per walk dell'albero di directory con metadata aggregati.
Dispatcher sottile che instrada al backend (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (filesystem locale).
- Backend builtin in `runtime/backends/files/<provider>.py`.
- NIENTE registry magico (§7.2 + §7.9).

Contratto:
    stdin: JSON {base_path, recursive?, max_depth?, max_results?,
                 include_hidden?, client?: 'local' (default)}
    stdout: JSON {ok, entries, matches, metadata, truncated?, ...}
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

# `google_workspace` è import LAZY (C7 Area-2 CP4, come find/read/write_files):
# a module-load trascina moduli SERVER-only → sul DEVICE farebbe
# ModuleNotFoundError per ogni invocazione, anche client=local.

_HANDLERS = {
    "local": local,
}


def _backend(client: str):
    b = _HANDLERS.get(client)
    if b is None and client == "google_workspace":
        try:
            from backends.files import google_workspace as _gw  # lazy, server-only
        except ImportError:
            return None  # device: gw assente → errore strutturato a valle (§2.8)
        _HANDLERS[client] = _gw
        b = _gw
    return b


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
        }
    client = args.get("client") or "local"
    backend = _backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.find_dirs(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
