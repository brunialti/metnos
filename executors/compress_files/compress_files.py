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
import errno
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

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


def _collect_paths(args: dict) -> tuple[object, list[dict]]:
    """Project runtime ``entries[*].path`` without echoing record contents."""
    paths = args.get("paths")
    entries = args.get("entries")
    if paths is not None and entries is not None:
        return None, [{
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="paths/entries",
                          reason="use exactly one input form"),
        }]
    if paths is not None:
        return paths, []
    if not isinstance(entries, list):
        return None, []
    projected = []
    failed = []
    for index, entry in enumerate(entries):
        value = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(value, str) and value:
            projected.append(value)
            continue
        failed.append({
            "index": index,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg=f"entries[{index}].path",
                          reason="must be a non-empty string"),
        })
    return projected, failed


def _fail(error_code: str, error: str, *, failed=None) -> dict:
    failed = failed if isinstance(failed, list) else []
    return {
        "ok": False,
        "ok_count": 0,
        "fail_count": len(failed),
        "results": [],
        "failed": failed,
        "error_class": "invalid_args" if error_code.startswith("ERR_ARG_")
                       or error_code == "ERR_DST_EXISTS" else "unknown",
        "error_code": error_code,
        "error": error,
    }


def _missing_parent_dirs(parent: Path) -> list[Path]:
    """Return missing parents deepest-first, without changing the filesystem."""
    missing = []
    current = parent
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    return missing


def _remove_empty_dirs(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.rmdir()
        except OSError:
            pass


def _create_parent_dirs(missing: list[Path]) -> list[Path]:
    """Create missing parents shallow-first and report only dirs we created."""
    created = []
    for path in reversed(missing):
        try:
            path.mkdir()
        except FileExistsError:
            if not path.is_dir():
                raise
        else:
            created.append(path)
    return created


def _write_archive(path: Path, fmt: str, valid: list[Path]) -> None:
    if fmt == "zip":
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in valid:
                archive.write(source, arcname=source.name)
    elif fmt in ("tar", "gztar"):
        with tarfile.open(path, "w:gz" if fmt == "gztar" else "w") as archive:
            for source in valid:
                archive.add(source, arcname=source.name)
    else:
        with open(valid[0], "rb") as source, gzip.open(path, "wb") as archive:
            shutil.copyfileobj(source, archive)


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return _fail(
            "ERR_ARG_INVALID",
            _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
        )
    paths, failed = _collect_paths(args)
    if not isinstance(paths, list):
        return _fail(
            "ERR_ARG_INVALID",
            _msg("ERR_ARG_NOT_LIST_OF", arg="paths", of="strings"),
            failed=failed,
        )
    dest = args.get("dest")
    if not (isinstance(dest, str) and dest.strip()):
        return _fail("ERR_ARG_MISSING", _msg("ERR_ARG_MISSING", arg="dest"))
    raw_fmt = args.get("format")
    if raw_fmt is not None and not isinstance(raw_fmt, str):
        return _fail(
            "ERR_ARG_INVALID",
            _msg("ERR_ARG_INVALID", arg="format", reason="must be a string"),
        )
    fmt = (raw_fmt or "").strip().lower() or _infer_format(dest)
    if fmt not in _FMTS:
        return _fail(
            "ERR_ARG_INVALID",
            _msg("ERR_ARG_INVALID", arg="format",
                 reason=f"must be one of {', '.join(_FMTS)}"),
        )
    dest_p = Path(os.path.expanduser(dest))
    if not dest_p.is_absolute():
        dest_p = (Path.cwd() / dest_p).resolve()
    valid = []
    seen_names: set[str] = set()
    for index, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            item = {
                "index": index,
                "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="path"),
            }
            if isinstance(p, str):
                item["path"] = p
            failed.append(item)
            continue
        fp = Path(os.path.expanduser(p))
        if not fp.is_absolute():
            fp = (Path.cwd() / fp).resolve()
        if not fp.exists():
            failed.append({
                "index": index, "path": p,
                "error_code": "ERR_PATH_NOT_FOUND",
                "error": _msg("ERR_PATH_NOT_FOUND", path=p),
            })
            continue
        if fp.is_dir():
            failed.append({
                "index": index, "path": p,
                "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="path",
                              reason=f"{p}: directory"),
            })
            continue
        try:
            same_as_dest = fp.resolve() == dest_p.resolve()
        except OSError:
            same_as_dest = fp.absolute() == dest_p.absolute()
        if same_as_dest:
            failed.append({
                "index": index, "path": p,
                "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="dest",
                              reason="destination cannot be an input file"),
            })
            continue
        if fp.name in seen_names:
            failed.append({
                "index": index, "path": p,
                "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="paths",
                              reason=f"duplicate archive name: {fp.name}"),
            })
            continue
        seen_names.add(fp.name)
        valid.append(fp)

    if not valid:
        # §2.1 degenere: nessun file valido → ok solo se non c'erano fallimenti.
        return {"ok": len(failed) == 0, "ok_count": 0, "fail_count": len(failed),
                "results": [], "failed": failed}
    if dest_p.exists():
        return _fail("ERR_DST_EXISTS", _msg("ERR_DST_EXISTS"), failed=failed)
    if fmt == "gz" and len(valid) > 1:
        return _fail(
            "ERR_ARG_INVALID",
            _msg("ERR_ARG_INVALID", arg="format",
                 reason="gz accepts exactly one input file"),
            failed=failed,
        )

    parent = dest_p.parent
    missing_dirs = _missing_parent_dirs(parent)
    created_dirs: list[Path] = []
    tmp_path: Path | None = None
    try:
        created_dirs = _create_parent_dirs(missing_dirs)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{dest_p.name}.metnos-", dir=str(parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        _write_archive(tmp_path, fmt, valid)
        # Pubblicazione create-only portabile: ``xb`` impedisce il replace
        # anche se la destinazione compare dopo il pre-check (Windows incluso).
        with tmp_path.open("rb") as source, dest_p.open("xb") as target:
            shutil.copyfileobj(source, target)
    except FileExistsError:
        return _fail("ERR_DST_EXISTS", _msg("ERR_DST_EXISTS"), failed=failed)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return _fail("ERR_DST_EXISTS", _msg("ERR_DST_EXISTS"), failed=failed)
        return {
            **_fail("ERR_OP_FAILED", _msg("ERR_OP_FAILED", reason=str(exc)),
                    failed=failed),
            "error_class": "unknown",
        }
    except Exception as exc:
        return {
            **_fail("ERR_OP_FAILED", _msg("ERR_OP_FAILED", reason=str(exc)),
                    failed=failed),
            "error_class": "unknown",
        }
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        if not dest_p.exists():
            _remove_empty_dirs(list(reversed(created_dirs)))

    size = dest_p.stat().st_size if dest_p.exists() else 0
    return {
        "ok": True,
        "ok_count": len(valid),
        "fail_count": len(failed),
        "results": [{"path": str(dest_p), "created": True,
                     "file_count": len(valid), "archive_bytes": size, "format": fmt}],
        "added": [str(f) for f in valid],
        "failed": failed,
        "dirs_created": [str(path) for path in created_dirs],
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
