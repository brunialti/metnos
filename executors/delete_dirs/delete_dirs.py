#!/usr/bin/env python3
"""delete_dirs — dispatcher canonical (sequel di read_messages, 13/5/2026).

Tool UNICO per rimuovere una o piu' directory. Dispatcher sottile che
instrada al backend (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (filesystem locale).
- Backend builtin in `runtime/backends/files/<provider>.py`.
- NIENTE registry magico (§7.2 + §7.9).

Contratto:
    stdin: JSON {paths: list[str], if_empty_only?, force?,
                 client?: 'local' (default)}
    stdout: JSON {ok, ok_count, fail_count, results, failed}
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


def _failure(error_code, error, *, error_class="invalid_input"):
    return {
        "ok": False,
        "ok_count": 0,
        "fail_count": 0,
        "results": [],
        "failed": [],
        "error_class": error_class,
        "error_code": error_code,
        "error": error,
    }


def invoke(args):
    if not isinstance(args, dict):
        return _failure("args_not_object", _msg("ERR_ARGS_NOT_OBJECT"))
    client_arg = args.get("client")
    if client_arg is not None and not isinstance(client_arg, str):
        return _failure(
            "client_not_string", _msg("ERR_ARG_NOT_STRING", arg="client"))
    client = args.get("client") or "local"
    backend = _backend(client)
    if backend is None:
        return _failure(
            "client_not_supported",
            _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
            error_class="not_applicable",
        )
    return backend.delete_dirs(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
