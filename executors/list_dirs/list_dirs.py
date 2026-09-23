#!/usr/bin/env python3
"""list_dirs — dispatcher canonico: elenca il contenuto di un contenitore.

Un livello per volta (§2.2: `list` enumera un contenitore, senza contenuto).
Instrada al backend giusto in base a `client`, default `local`.

Principio (feedback_robust_executors): list_dirs e' "uso generale" e NON
filtra. Restituisce TUTTO il contenuto, arricchito di metadata utili. Il
filtraggio (per kind, regex, size, ...) e' di `filter_entries`, componibile
via data piping (`from_step=N`).

Era l'ultimo verbo su cartelle con l'implementazione dentro l'executor:
find/create/delete_dirs stavano gia' nei backend. Finche' restava cosi', non
poteva avere un provider senza diventare un'eccezione (RM-0011 F0).

Contratto:
    stdin:  JSON {path, recursive?, sort?, max_results?, max_depth?,
                  client?: 'local' (default)}
    stdout: JSON {ok, entries, metadata} oppure {ok=false, error}
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
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
        }
    client = args.get("client") or "local"
    backend = _backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.list_dirs(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
