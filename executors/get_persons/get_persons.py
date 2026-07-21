#!/usr/bin/env python3
"""get_persons — lookup nel registro nominale di persone.

Pattern §2.2: verb `get` = id noti o nessun arg (lookup/snapshot).
Senza arg ritorna la lista completa; con `name` ritorna i dettagli di una
sola persona o tutti i candidati ambigui.

Determinismo §7.9: nessun LLM, solo SQLite via PersonsRegistry.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg
from executor_helpers import run_stdio  # noqa: E402
from persons_registry import PersonsRegistry


def _persons_db_path() -> Path | None:
    v = os.environ.get("METNOS_USER_DATA")
    return (Path(v) / "persons.sqlite") if v else None


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
            "error_class": "invalid_input",
            "error_code": "args_not_object",
        }
    name = args.get("name")

    # Validate before opening the registry: malformed input must not depend on
    # filesystem availability or create state as a side effect.
    if name is not None and not isinstance(name, str):
        return {
            "ok": False,
            "error": _msg("ERR_ARG_NOT_STRING", arg="name"),
            "error_class": "invalid_input",
            "error_code": "name_not_string",
        }

    try:
        reg = PersonsRegistry(db_path=_persons_db_path(), read_only=True)
    except Exception as exc:
        return {
            "ok": False,
            "error": _msg("ERR_PERSONS_REGISTRY_UNAVAILABLE"),
            "error_class": "resource_unavailable",
            "error_code": "persons_registry_unavailable",
            "detail": str(exc),
        }
    try:
        if name is None or name == "":
            entries = reg.list_all()
            if not entries:
                hint = _msg("MSG_PERSONS_LIST_EMPTY")
            else:
                head = _msg("MSG_PERSONS_LIST_HEADER", n=len(entries))
                rows = [
                    _msg(
                        "MSG_PERSONS_LIST_ITEM",
                        name=e.get("name") or e.get("slug"),
                        n_examples=int(e.get("n_examples") or 0),
                    )
                    for e in entries
                ]
                hint = head + "\n" + "\n".join(rows)
            return {
                "ok": True,
                "entries": entries,
                "n_entries": len(entries),
                "final_message_hint": hint,
            }

        slugs = reg.resolve_name(name)
        if not slugs:
            return {
                "ok": True,
                "entries": [],
                "status": "unknown_name",
                "final_message_hint": _msg(
                    "MSG_PERSONS_UNKNOWN_NAME", name=name,
                ),
            }
        if len(slugs) == 1:
            entry = reg.get(slugs[0])
            return {
                "ok": True,
                "entries": [entry] if entry is not None else [],
                "n_entries": 1 if entry is not None else 0,
            }
        # Ambigua: ritorna tutti i candidati. Il PLANNER puo' decidere se
        # mostrarli o se chiedere disambig esplicita.
        entries = [reg.get(s) for s in slugs]
        entries = [e for e in entries if e is not None]
        return {
            "ok": True,
            "entries": entries,
            "n_entries": len(entries),
            "ambiguous": True,
            "final_message_hint": _msg(
                "MSG_PERSONS_AMBIGUOUS_NAME", name=name,
            ),
        }
    finally:
        reg.close()


def main():
    run_stdio(invoke, default=str)


if __name__ == "__main__":
    main()
