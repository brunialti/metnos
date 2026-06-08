#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""compress_files — comprime una lista di file in UN archivio (zip/tar/gz).

Vettoriale §2.1: input lista di path (anche 1, anche 0 → no-op onesto), output
un singolo archivio. Reversible §2.3: l'archivio creato e' rimovibile via
`delete_created_paths` (results=[{path, created:true}]).

Formato: inferito dall'estensione di `dest` (.zip/.tar/.tar.gz|.tgz/.gz) o
forzato via `format`. `gz` comprime UN solo file (stream singolo).

Contratto:
    stdin: JSON {paths|entries: list, dest: str, format?: zip|tar|gztar|gz}
    stdout: JSON {ok, ok_count, fail_count,
                  results:[{path:dest, created:true, file_count, archive_bytes, format}],
                  added:[...], failed:[{path,error}]}
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402

_FMTS = ("zip", "tar", "gztar", "gz")


def _infer_format(dest: str) -> str:
    d = dest.lower()
    if d.endswith((".tar.gz", ".tgz")):
        return "gztar"
    if d.endswith(".tar"):
        return "tar"
    if d.endswith(".gz"):
        return "gz"
    return "zip"


def _collect_paths(args: dict):
    """Accetta `paths` (lista di str) O `entries` (lista di dict con
    path/src/file) — robusto al from_step di find_files (§2.10)."""
    paths = args.get("paths")
    if paths is None:
        ents = args.get("entries")
        if isinstance(ents, list):
            paths = []
            for e in ents:
                if isinstance(e, str):
                    paths.append(e)
                elif isinstance(e, dict):
                    v = e.get("path") or e.get("src") or e.get("file")
                    if isinstance(v, str):
                        paths.append(v)
    if isinstance(paths, str):
        paths = [paths]
    return paths


def invoke(args: dict) -> dict:
    args = args or {}
    paths = _collect_paths(args)
    if not isinstance(paths, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="paths", of="strings"),
                "error_class": "invalid_args", "results": []}
    dest = args.get("dest")
    if not (isinstance(dest, str) and dest.strip()):
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="dest"),
                "error_class": "invalid_args", "results": []}
    fmt = (args.get("format") or "").strip().lower() or _infer_format(dest)
    if fmt not in _FMTS:
        fmt = _infer_format(dest)
    dest_p = Path(os.path.expanduser(dest))

    valid, failed = [], []
    for p in paths:
        if not isinstance(p, str) or not p:
            failed.append({"path": str(p), "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="path")})
            continue
        fp = Path(os.path.expanduser(p))
        if not fp.exists():
            failed.append({"path": p, "error": _msg("ERR_PATH_NOT_FOUND", path=p)})
            continue
        if fp.is_dir():
            failed.append({"path": p, "error": _msg("ERR_OP_FAILED", reason=f"{p}: directory")})
            continue
        valid.append(fp)

    if not valid:
        # §2.1 degenere: nessun file valido → ok solo se non c'erano fallimenti.
        return {"ok": len(failed) == 0, "ok_count": 0, "fail_count": len(failed),
                "results": [], "failed": failed}
    if fmt == "gz" and len(valid) > 1:
        return {"ok": False,
                "error": _msg("ERR_OP_FAILED", reason="gz comprime un solo file; usa zip o tar per piu' file"),
                "error_class": "invalid_args", "results": []}
    try:
        if dest_p.parent and not dest_p.parent.exists():
            dest_p.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "zip":
            with zipfile.ZipFile(dest_p, "w", zipfile.ZIP_DEFLATED) as z:
                for fp in valid:
                    z.write(fp, arcname=fp.name)
        elif fmt in ("tar", "gztar"):
            with tarfile.open(dest_p, "w:gz" if fmt == "gztar" else "w") as t:
                for fp in valid:
                    t.add(fp, arcname=fp.name)
        else:  # gz
            with open(valid[0], "rb") as fin, gzip.open(dest_p, "wb") as fout:
                shutil.copyfileobj(fin, fout)
    except Exception as e:
        return {"ok": False, "error": _msg("ERR_OP_FAILED", reason=str(e)),
                "error_class": "unknown", "results": []}

    size = dest_p.stat().st_size if dest_p.exists() else 0
    return {
        "ok": True,
        "ok_count": len(valid),
        "fail_count": len(failed),
        "results": [{"path": str(dest_p), "created": True,
                     "file_count": len(valid), "archive_bytes": size, "format": fmt}],
        "added": [str(f) for f in valid],
        "failed": failed,
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
