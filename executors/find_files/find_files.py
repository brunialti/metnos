#!/usr/bin/env python3
"""find_files — dispatcher canonical (sequel di read_messages, 13/5/2026).

Tool UNICO per cercare file per pattern. Dispatcher sottile che instrada
al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (filesystem locale).
- Backend builtin in `runtime/backends/files/<provider>.py`.
- NIENTE registry magico (§7.2 + §7.9).

Contratto:
    stdin: JSON {base_path, pattern? | patterns?, recursive?, max_results?,
                 max_depth?, include_dirs?, case_sensitive?,
                 client?: 'local' (default)}
    stdout: JSON {ok, entries, matches, metadata, truncated?, ...}
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/myclaw/runtime")
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
    return backend.find(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
