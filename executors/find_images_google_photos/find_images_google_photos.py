#!/usr/bin/env python3
"""find_images_google_photos — dispatcher canonical Google Photos search.

Cerca fra le foto caricate da Metnos su Google Photos (Library API, scope
readonly.appcreateddata). Provider FISSO (google_photos): dispatcher sottile →
backend `runtime/backends/images/google_photos.py::find`.

LIMITE API (post 31/3/2025): si vede SOLO il contenuto creato dall'app, NON
l'intera libreria dell'utente. Per la libreria completa e TUTTI gli album:
archivio Takeout (find_images_indices / list_dirs).

Contratto:
    stdin: JSON {year?, album?, albums?, max_results?}
    stdout: JSON {ok, entries:[{id, filename, mime, created_at, album}], used}
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
from backends.images import google_photos  # noqa: E402


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    return google_photos.find(args)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args",
                                   "entries": [], "used": 0})


if __name__ == "__main__":
    main()
