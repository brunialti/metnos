#!/usr/bin/env python3
"""
fs_list — executor di Metnos v1.1.

Elenca file e directory di una path autorizzata. Per ognuno: name, type
(file|dir|symlink), size, mtime (ISO 8601), mime (mimetype derivato dal
nome), kind (categoria semantica: image|video|audio|text|document|archive|binary|dir|symlink).

Principio (vedi feedback_robust_executors): fs_list e' "uso generale" e
NON filtra. Restituisce TUTTO il contenuto della directory, arricchito
con metadata utili. Il filtraggio (per kind, regex, size, ...) e'
responsabilita' dell'executor `filter_entries`, componibile via data
piping (`from_step=N` per riferire lo step di list_dirs).

Contratto:
    stdin:  JSON con args (path, recursive?, sort?, max_results?, max_depth?)
    stdout: JSON {ok, entries, metadata} oppure {ok=false, error}
"""
import datetime as _dt
import mimetypes
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from parallel_walk import parallel_walk  # noqa: E402
from path_alias import resolve_path_with_alias  # noqa: E402

_KIND_PREFIX = {
    "image": ("image/",),
    "video": ("video/",),
    "audio": ("audio/",),
    "text": ("text/",),
    "document": ("application/pdf", "application/msword",
                  "application/vnd.openxmlformats-officedocument",
                  "application/vnd.oasis.opendocument",
                  "application/rtf"),
    "archive": ("application/zip", "application/x-tar", "application/gzip",
                 "application/x-7z-compressed", "application/x-rar"),
}


def _mime_for(name: str) -> str:
    mt, _ = mimetypes.guess_type(name.lower())
    return mt or "application/octet-stream"


def _kind_for(mime: str) -> str:
    for kind, prefixes in _KIND_PREFIX.items():
        if any(mime.startswith(pref) for pref in prefixes):
            return kind
    return "binary"


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
        }
    path = args.get("path")
    recursive = args.get("recursive", False)
    sort_by = args.get("sort", "name")
    max_results = args.get("max_results", 1000)
    max_depth = args.get("max_depth", 10)

    if not path:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="path")}
    if (not isinstance(max_results, int) or isinstance(max_results, bool)
            or max_results < 0):
        return {"ok": False,
                "error": _msg("ERR_ARG_NOT_NONNEGATIVE_INT",
                              arg="max_results")}
    if not isinstance(max_depth, int) or max_depth < 0:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="max_depth", reason=">= 0")}
    if sort_by not in ("name", "mtime", "size"):
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="sort", allowed="name | mtime | size")}

    # path_alias resolver: workspace-default + bilingue IT/EN + multi-root.
    base, alias_note = resolve_path_with_alias(path)
    # Un accesso fs puo' ALZARE (permessi/IO): sotto AppContainer una dir NON
    # granted da' PermissionError (WinError 5), non False. Trattala come
    # non-accessibile → errore pulito (§2.8), mai crash (stdout vuoto).
    try:
        if not base.exists():
            return {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                    "error": _msg("ERR_PATH_NOT_FOUND", path=base)}
        if not base.is_dir():
            return {"ok": False, "error": _msg("ERR_PATH_WRONG_TYPE", expected="dir", actual="file", path=base)}
    except OSError:
        return {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                "error": _msg("ERR_PATH_NOT_FOUND", path=base)}

    def _entry(path, ftype, _depth, directory_entry):
        if ftype == "other":
            ftype = "file"
        stat = directory_entry.stat(follow_symlinks=False)
        if ftype == "file":
            mime = _mime_for(path.name)
            kind = _kind_for(mime)
        elif ftype == "dir":
            mime = ""
            kind = "dir"
        else:
            mime = ""
            kind = "symlink"
        return {
            "path": str(path),
            "name": path.name,
            "type": ftype,
            "kind": kind,
            "mime": mime,
            "size": stat.st_size,
            "mtime": _dt.datetime.fromtimestamp(
                stat.st_mtime, _dt.timezone.utc).isoformat(),
            "mtime_epoch": stat.st_mtime,
        }

    walk = parallel_walk(
        base,
        transform=_entry,
        recursive=recursive,
        max_depth=max_depth,
    )
    all_entries = walk.items

    if sort_by == "name":
        all_entries.sort(key=lambda e: (e["name"].casefold(), e["path"]))
    elif sort_by == "mtime":
        all_entries.sort(
            key=lambda e: (e["mtime_epoch"], e["path"]), reverse=True)
    elif sort_by == "size":
        all_entries.sort(key=lambda e: (e["size"], e["path"]), reverse=True)

    available_total = len(all_entries)
    entries = (all_entries if max_results == 0
               else all_entries[:max_results])
    truncated = len(entries) < available_total
    failed = [{
        "path": str(error.path),
        "error_class": "permission_denied"
        if error.reason == "permission_denied" else "io_error",
        "error_code": "ERR_PERMISSION_DENIED"
        if error.reason == "permission_denied" else "ERR_FILE_READ_FAILED",
        "error": _msg("ERR_PERMISSION_DENIED")
        if error.reason == "permission_denied"
        else _msg("ERR_FILE_READ_FAILED", path=str(error.path)),
        "detail": error.reason,
    } for error in walk.errors]

    out = {
        "ok": not failed,
        "entries": entries,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "failed": failed,
        "metadata": {
            "path": str(base),
            "recursive": recursive,
            "count": len(entries),
            "available_total": available_total,
            "truncated": truncated,
            "sort": sort_by,
            "visited_dirs": walk.visited_dirs,
            "walk_workers": walk.workers,
            "source_complete": walk.source_complete and not failed,
            **({"alias_resolved": alias_note} if alias_note else {}),
        },
    }
    if failed:
        out["error"] = failed[0]["error"]
        if entries:
            out["partial"] = True
    if truncated:
        out.update({
            "truncated": True,
            "truncated_what": "entries",
            "used": len(entries),
            "available_total": available_total,
            "cap_field": "max_results",
            "cap_value": max_results,
        })
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
