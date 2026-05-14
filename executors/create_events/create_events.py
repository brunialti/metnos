#!/usr/bin/env python3
"""create_events — dispatcher canonical (sequel di read_events, 13/5/2026).

Tool UNICO per creare un evento sul calendario. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (calendar locale via ICS file — stub NON IMPLEMENTATO).
- Backend builtin in `runtime/backends/calendar/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

§2.3 reverse_pattern: `delete_events_by_id` (catalogo deterministico).

Contratto:
    stdin: JSON {summary, start, end, location?, attendees?, calendar_id?,
                 client?: 'local' (default)}
    stdout: JSON {ok, results, n_created, used, error?, error_class?,
                  _undo?: {pattern, ids}}
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/myclaw/runtime")
from backends.events import local_ics, google_workspace  # noqa: E402

_HANDLERS = {
    "local": local_ics,
    "google_workspace": google_workspace,
}


def _default_client() -> str:
    """Auto-default: google_workspace se OAuth token presente, altrimenti
    local_ics. Detect deterministico §7.9 (filesystem check)."""
    try:
        return "google_workspace" if google_workspace._has_creds() else "local"
    except Exception:
        return "local"


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    client = args.get("client") or _default_client()
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": f"unsupported calendar client '{client}'. "
                         f"Available: {avail}",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    return backend.create(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}",
                                      "error_class": "invalid_args",
                                      "results": [], "used": 0, "n_created": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
