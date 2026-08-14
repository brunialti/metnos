#!/usr/bin/env python3
"""get_images_google_photos — dispatcher canonical Google Photos download.

Scarica foto app-created da Google Photos per `media_item_id` nel workspace
foto. Provider FISSO (google_photos): dispatcher sottile → backend
`runtime/backends/images/google_photos.py::download`.

Il download locale e' reversibile: reverse_pattern='delete_created_paths'
(il backend ritorna `_undo` con i path scaricati).

Contratto:
    stdin: JSON {ids|entries (from_step), dst_dir?}
    stdout: JSON {ok, ok_count, fail_count,
                  results:[{id, local_path, filename, bytes}], used, _undo}
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
    # Picker (P3, D8): l'utente seleziona nella UI Google (anche dentro album
    # NON creati da Metnos) e Metnos scarica i selezionati. Il resume del
    # dialog arriva con `picker_session_id`.
    if args.get("picker") or args.get("picker_session_id"):
        return google_photos.picker(args)
    return google_photos.download(args)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args",
                                   "results": [], "used": 0, "ok_count": 0})


if __name__ == "__main__":
    main()
