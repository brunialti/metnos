#!/usr/bin/env python3
"""move_files — dispatcher canonical (sequel di read_messages, 13/5/2026).

Tool UNICO per spostare/rinominare entries (file). Dispatcher sottile
che instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (filesystem locale).
- Backend builtin in `runtime/backends/files/<provider>.py`.
- NIENTE registry magico (§7.2 + §7.9).

Reverse pattern: ["swap_src_dst", "delete_created_dirs"] (§2.3). Il
runtime invoca `reverse(plan, results)` qui, che delega al backend
selezionato dal forward.

Contratto:
    stdin: JSON {entries: list[dict], dst_template: str, overwrite?,
                 parents?, allow_dirs?, allow_system?,
                 client?: 'local' (default)}
    stdout: JSON {ok, ok_count, fail_count, results, dirs_created, failed}
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


def _backend_for(args):
    client = args.get("client") or "local"
    return _HANDLERS.get(client), client


def invoke(args):
    backend, client = _backend_for(args)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.move(args)


def reverse(plan, results):
    """Undo multistage del move. Delega al backend usato dal forward.

    `plan` puo' contenere `args` originali con `client`; in mancanza
    cade su 'local'.
    """
    args = (plan or {}).get("args") or {}
    backend, client = _backend_for(args)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.reverse_move(plan, results)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
