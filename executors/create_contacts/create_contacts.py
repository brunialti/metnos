#!/usr/bin/env python3
"""create_contacts — canonical dispatcher for the `contacts` object.

The provider is an ARGUMENT (`client`), never a name suffix (RM-0011). The
runtime owns its value (`backend_resolver`); a second address book is one more
module under `backends/contacts/`, not another executor.

Contract:
    stdin:  JSON {name?, emails?, phones?, entries?, client?}
    stdout: JSON {ok, results, ok_count, fail_count, failed, contact_ids, _undo} or {ok=false, error}
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

_AREA = "contacts"
_DEFAULT_CLIENT = "google_workspace"


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "results": [], "used": 0}
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _load_backend(_AREA, client)
    if backend is None:
        return {"ok": False, "error_code": "ERR_NOT_APPLICABLE",
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "results": [], "used": 0}
    return backend.create_contacts(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
