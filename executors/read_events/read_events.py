#!/usr/bin/env python3
"""read_events — dispatcher canonical (sequel di read_files/read_messages, 13/5/2026).

Tool UNICO per leggere gli eventi del calendario. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (calendar locale via ICS file — stub NON IMPLEMENTATO).
- Backend builtin in `runtime/backends/calendar/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

Predisposizione plugin esterni:
- Quando arrivera' l'ADR plugin esterni, `_HANDLERS` sara' arricchito
  da loader scan di `~/.local/share/metnos/plugins/calendar-*/backends/`.
  Es. `cloud-calendar-gcal` (Google), `caldav-radicale`, `outlook-graph`.

Contratto:
    stdin: JSON {time_window?|start+end?, top_k?, calendar_id?,
                 client?: 'local' (default)}
    stdout: JSON {ok, entries, used, available_total?,
                  truncated?, truncated_what?, cap_field?, cap_value?,
                  error?, error_class?}
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
from backends.events import local_ics, google_workspace  # noqa: E402

# Dispatch table read-side (predisposta a plugin esterni).
# Valori = modulo: attribute lookup `module.read` a call-time per testabilita'.
_HANDLERS = {
    "local": local_ics,
    "google_workspace": google_workspace,
}


from backends.events import default_event_client  # SoT (era copia locale)


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    client = args.get("client") or default_event_client()
    backend = _HANDLERS.get(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    # Attribute lookup a call-time: i test possono patchare `backend.read`.
    return backend.read(args)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args", "entries": [], "used": 0})


if __name__ == "__main__":
    main()
