#!/usr/bin/env python3
"""write_images_google_photos — dispatcher canonical Google Photos upload.

Carica foto/video locali su Google Photos (Library API, scope appendonly).
Provider FISSO (google_photos): dispatcher sottile → backend
`runtime/backends/images/google_photos.py::upload`.

L'upload NON e' reversibile: l'API Google non permette di eliminare mediaItems
(§2.8 + spec §0/§3.4). `revertible=false`, nessun `_undo`.

Contratto:
    stdin: JSON {paths|entries (from_step), album?, max_total?}
    stdout: JSON {ok, ok_count, fail_count,
                  results:[{path, media_item_id, filename, album}], used}
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
                "error_class": "invalid_args",
                "results": [], "used": 0, "ok_count": 0}
    return google_photos.upload(args)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args",
                                   "results": [], "used": 0, "ok_count": 0})


if __name__ == "__main__":
    main()
