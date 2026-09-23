#!/usr/bin/env python3
"""write_files — dispatcher canonical (sequel di read_messages, 13/5/2026).

Tool UNICO per scrivere il contenuto di UN file. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (filesystem locale).
- Backend builtin in `runtime/backends/files/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

Contratto:
    stdin: JSON {path, content, encoding?, mode?,
                 client?: 'local' (default)}
    stdout: JSON {ok, ok_count, fail_count, results, dirs_created}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from backends import load as _load_backend  # noqa: E402
from backends.files import local  # noqa: E402


_HANDLERS = {
    "local": local,
}


def _backend(client: str):
    """Modulo del provider richiesto, o None se qui non e' disponibile.

    Il valore di `client` lo possiede il runtime (`backend_resolver`), non
    l'LLM e non l'executor. I provider non predefiniti sono caricati per
    nome: aggiungerne uno e' un modulo sotto `backends/files/`, mai un ramo
    qui. None risale come ERR_NOT_APPLICABLE (§2.8).
    """
    backend = _HANDLERS.get(client)
    if backend is None:
        backend = _load_backend("files", client)
        if backend is not None:
            _HANDLERS[client] = backend
    return backend


def invoke(args):
    client = args.get("client") or "local"
    backend = _backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.write(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
