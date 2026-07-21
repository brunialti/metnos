#!/usr/bin/env python3
"""write_files_doc — dispatcher canonical Google Docs append (24/5/2026).

Tool UNICO per appendere testo alla fine di un Google Doc esistente.
Append e' semantica `write` con tag implicito (§2.2: no verbo separato).
Dispatcher sottile che instrada al backend giusto in base a `client`
(default `google_workspace`).

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

§2.3 reversibile (module.reverse): l'append registra `inserted_at` (indice
di insert) + `characters_appended`; l'undo elimina il range
[inserted_at, inserted_at+chars) via deleteContentRange
(backend.delete_doc_range → `docs delete-range` in google_api.py).

Contratto:
    stdin: JSON {document_id, text,
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, n_written, document_id, content_length,
                  characters_appended, results, used}
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
from backends.files import google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
}


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    client = args.get("client") or "google_workspace"
    backend = _HANDLERS.get(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    return backend.append_doc(args)


def reverse(plan, results):
    """Undo §2.3 (module.reverse): rimuove dal Doc il testo appeso.

    L'append registra `inserted_at` (indice 1-based) + `characters_appended`;
    il range [inserted_at, inserted_at+chars) viene eliminato via
    deleteContentRange (backend.delete_doc_range). Reversibile solo per i
    result con `inserted_at` (append riuscito col nuovo script). Senza
    inserted_at → non ribaltabile onesto (§2.8).
    """
    res = results or {}
    rows = res.get("results") or []
    did = res.get("document_id")
    out, failed = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        doc = r.get("document_id") or did
        start = r.get("inserted_at")
        chars = r.get("characters_appended")
        if not doc or not isinstance(start, int) or not isinstance(chars, int):
            failed.append({"document_id": doc,
                           "error": "inserted_at/characters mancanti: non ribaltabile"})
            continue
        dr = google_workspace.delete_doc_range(
            {"document_id": doc, "start": start, "end": start + chars})
        if dr.get("ok"):
            out.append({"document_id": doc, "removed_range": [start, start + chars]})
        else:
            failed.append({"document_id": doc, "error": dr.get("error", "delete_range failed")})
    return {"ok": len(failed) == 0, "ok_count": len(out),
            "fail_count": len(failed), "results": out, "failed": failed}


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args", "results": [], "used": 0, "n_written": 0})


if __name__ == "__main__":
    main()
