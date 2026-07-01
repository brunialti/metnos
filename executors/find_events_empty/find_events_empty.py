#!/usr/bin/env python3
"""find_events_empty — dispatcher canonical (sequel di delete_events, 13/5/2026).

Tool UNICO per trovare finestre VUOTE del calendario (ADR 0127, qualifier
`_empty`). Dispatcher sottile che instrada al backend giusto in base a
`client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
- Backend builtin in `runtime/backends/calendar/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

§2.6: ritorna `entries` (read-like, nessuna mutazione). reversible=false.

Qualifier `_empty` (ADR 0127): l'executor ritorna lo stato VUOTO/SOTTO-
SOGLIA del dominio. Per i calendar, "vuoto" significa slot disponibile
(gap fra eventi). Stesso qualifier riusabile cross-dominio
(`find_files_empty`, `find_dirs_empty`, `find_messages_empty`).

Pattern propose-and-fire (ADR 0127):
    get_now -> find_events_empty(time_windows=["next-week"], size="1hour",
              time_of_day="morning", max_results=3)
    get_inputs(kind="choice", from_step=N,
              display_template="{start} - {end}", value_field="start")
    create_events(summary=..., start={{step3.values.scelta}}, end=...)

Contratto:
    stdin: JSON {time_windows?: list[str], size?, time_of_day?,
                 max_results?, calendar_id?,
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

_HANDLERS = {
    "local": local_ics,
    "google_workspace": google_workspace,
}


from backends.events import default_event_client  # SoT (era copia locale)


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    client = args.get("client") or default_event_client()
    backend = _HANDLERS.get(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    return backend.find_events_empty(args)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args", "entries": [], "used": 0})


if __name__ == "__main__":
    main()
