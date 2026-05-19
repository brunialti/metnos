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

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from backends.files import local, google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
    "local": local,
}


def invoke(args):
    client = args.get("client") or "local"
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": f"unsupported files client '{client}'. "
                         f"Available: {avail}"}
    return backend.write(args)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        result = {"ok": False, "error": "empty input"}
    else:
        try:
            args = json.loads(raw)
            result = invoke(args)
        except json.JSONDecodeError as e:
            result = {"ok": False, "error": f"invalid input json: {e}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
