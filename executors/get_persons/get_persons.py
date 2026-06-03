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
from persons_registry import PersonsRegistry


def _persons_db_path() -> Path | None:
    v = os.environ.get("METNOS_USER_DATA")
    return (Path(v) / "persons.sqlite") if v else None


def invoke(args):
    name = args.get("name")

    reg = PersonsRegistry(db_path=_persons_db_path())
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

        if not isinstance(name, str):
            return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="name")}

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
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
