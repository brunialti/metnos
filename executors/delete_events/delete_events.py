#!/usr/bin/env python3
"""delete_events — dispatcher canonical (sequel di create_events, 13/5/2026).

Tool UNICO per eliminare eventi dal calendario. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
- Backend builtin in `runtime/backends/calendar/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

§2.3 reversible=false (modello delete_persons): la cancellazione di un
evento esterno non e' undoable senza ricreare la risorsa con stessi id.

Vettoriale §2.1: accetta `event_ids: list[str]`, `event_id: str`
(singolare, normalizzato a list len-1), oppure `entries: list[{id, ...}]`
(da from_step di un produttore tipo read_events).

Contratto:
    stdin: JSON {event_ids?|event_id?|entries?, calendar_id?,
                 client?: 'local' (default)}
    stdout: JSON {ok, results, n_deleted, used, partial?, failures?,
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
from backends.events import local_ics, google_workspace  # noqa: E402

_HANDLERS = {
    "local": local_ics,
    "google_workspace": google_workspace,
}


def _default_client() -> str:
    try:
        return "google_workspace" if google_workspace._has_creds() else "local"
    except Exception:
        return "local"


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    client = args.get("client") or _default_client()
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    return backend.delete(args)


def reverse(plan, results):
    """Undo §2.3 (module.reverse): ricrea gli eventi cancellati.

    LOCAL ICS: re-inserisce i blocchi VEVENT catturati dal delete (restore
    verbatim, stesso uid). GOOGLE: l'evento esterno non e' ricreabile dal solo
    payload del delete → onesti, quei record contano come non-ribaltabili
    (§2.8). Dispatch sul campo `vevent` presente nei result locali.
    """
    res = results or {}
    rows = res.get("results") or []
    vevents = [r.get("vevent") for r in rows
               if isinstance(r, dict) and r.get("ok") and r.get("vevent")]
    not_reversible = [r.get("uid") for r in rows
                      if isinstance(r, dict) and r.get("ok") and not r.get("vevent")]
    out, restored = [], 0
    if vevents:
        rr = local_ics.restore({"vevents": vevents})
        out = rr.get("results") or []
        restored = int(rr.get("n_restored") or 0)
    failed = [{"uid": u, "error": "evento esterno (google): non ricreabile da undo"}
              for u in not_reversible]
    return {"ok": len(failed) == 0, "ok_count": restored,
            "fail_count": len(failed), "results": out, "failed": failed}


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID"),
                                      "error_class": "invalid_args",
                                      "results": [], "used": 0, "n_deleted": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
