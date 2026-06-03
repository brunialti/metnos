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
import json
import mimetypes
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
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
    path = args.get("path")
    recursive = args.get("recursive", False)
    sort_by = args.get("sort", "name")
    max_results = args.get("max_results", 1000)
    max_depth = args.get("max_depth", 10)

    if not path:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="path")}
    if max_results == 0:
        max_results = 1000
    if not isinstance(max_results, int) or max_results < 1:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="max_results")}
    if not isinstance(max_depth, int) or max_depth < 0:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="max_depth", reason=">= 0")}
    if sort_by not in ("name", "mtime", "size"):
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="sort", allowed="name | mtime | size")}

    # path_alias resolver: workspace-default + bilingue IT/EN + multi-root.
    base, alias_note = resolve_path_with_alias(path)
    if not base.exists():
        return {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                "error": _msg("ERR_PATH_NOT_FOUND", path=base)}
    if not base.is_dir():
        return {"ok": False, "error": _msg("ERR_PATH_WRONG_TYPE", expected="dir", actual="file", path=base)}

    entries: list[dict] = []
    truncated = False

    try:
        iterator = base.rglob("*") if recursive else base.iterdir()
        for p in iterator:
            try:
                depth = len(p.relative_to(base).parts)
            except ValueError:
                continue
            if depth > max_depth:
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            is_link = p.is_symlink()
            try:
                is_dir = p.is_dir() and not is_link
            except OSError:
                is_dir = False
            ftype = "symlink" if is_link else ("dir" if is_dir else "file")
            if ftype == "file":
                mime = _mime_for(p.name)
                kind = _kind_for(mime)
            elif ftype == "dir":
                mime = ""
                kind = "dir"
            else:
                mime = ""
                kind = "symlink"
            mtime_iso = _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc).isoformat()
            entries.append({
                "path": str(p),
                "name": p.name,
                "type": ftype,
                "kind": kind,
                "mime": mime,
                "size": st.st_size,
                "mtime": mtime_iso,
                "mtime_epoch": st.st_mtime,
            })
            if len(entries) >= max_results:
                truncated = True
                break
    except PermissionError as e:
        return {"ok": False, "error": _msg("ERR_PERMISSION_DENIED", path=str(e))}
    except OSError as e:
        return {"ok": False, "error": f"os error: {e}"}

    if sort_by == "name":
        entries.sort(key=lambda e: e["name"])
    elif sort_by == "mtime":
        entries.sort(key=lambda e: e["mtime_epoch"], reverse=True)
    elif sort_by == "size":
        entries.sort(key=lambda e: e["size"], reverse=True)

    return {
        "ok": True,
        "entries": entries,
        "metadata": {
            "path": str(base),
            "recursive": recursive,
            "count": len(entries),
            "truncated": truncated,
            "sort": sort_by,
            **({"alias_resolved": alias_note} if alias_note else {}),
        },
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
