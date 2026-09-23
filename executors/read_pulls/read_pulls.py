#!/usr/bin/env python3
"""read_pulls — dispatcher canonico per l'oggetto `pulls`.

Il fornitore e' un ARGOMENTO (`client`), non un suffisso nel nome: e' la
regola unica decisa in RM-0011. Il runtime possiede il valore
(`backend_resolver`), l'executor non lo sceglie e non lo nomina. Aggiungere
un secondo sistema di versionamento e' un modulo sotto `backends/pulls/`,
non un altro executor.

Contratto:
    stdin:  JSON {repo, number, client?}
    stdout: JSON {ok, entries, used, available_total} oppure {ok=false, error}
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

_AREA = "pulls"
_DEFAULT_CLIENT = "github"
_HANDLERS: dict = {}


def _backend(client: str):
    """Modulo del provider richiesto, o None se qui non e' disponibile."""
    backend = _HANDLERS.get(client)
    if backend is None:
        backend = _load_backend(_AREA, client)
        if backend is not None:
            _HANDLERS[client] = backend
    return backend


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "entries": [], "used": 0}
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "entries": [], "used": 0}
    return backend.read_pulls(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
