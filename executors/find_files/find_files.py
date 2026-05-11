#!/usr/bin/env python3
"""
find_file — executor di Metnos v1.1 (era fs_find).

Cerca file per nome o pattern dentro un base_path autorizzato. Ricorsivo
opzionale, max_results e max_depth come tetti.

Robustezza:
- Accetta `pattern` (str) o `patterns` (list[str]).
- Una stringa con virgole/pipe e' splittata in piu' pattern.
- Match case-insensitive di default.

Output uniformato a list_dir (componibile con filter_entries):
- `entries`: list[{path, name, type, mime, kind, size, mtime}]
- `matches`: list[str] (gli stessi path, comodi se serve solo la lista)

Contratto:
    stdin:  JSON con args (pattern? patterns?, base_path, recursive?, ...)
    stdout: JSON {ok, entries, matches, metadata} oppure {ok=false, error}
"""
import fnmatch
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

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


def _parse_compound_pattern(value):
    """Accetta str o list[str] e ritorna list[str] di pattern atomici.
    Una str con separatori naturali (virgola, pipe, spazi) viene splittata.
    """
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, list):
        for v in value:
            out.extend(_parse_compound_pattern(v))
        return out
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return out
        if any(sep in s for sep in (",", "|")):
            parts = re.split(r"[,|]", s)
        else:
            parts = [s]
        for p in parts:
            p = p.strip()
            if p:
                out.append(p)
    return out


def invoke(args):
    base_path = args.get("base_path")
    recursive = args.get("recursive", True)
    max_results = args.get("max_results", 1000)
    max_depth = args.get("max_depth", 10)
    include_dirs = args.get("include_dirs", False)
    case_sensitive = args.get("case_sensitive", False)

    patterns = _parse_compound_pattern(args.get("pattern")) + _parse_compound_pattern(args.get("patterns"))

    if not patterns:
        return {"ok": False, "error": "missing required arg 'pattern' (or 'patterns')"}
    if not base_path:
        return {"ok": False, "error": "missing required arg 'base_path'"}
    if not isinstance(max_results, int) or max_results < 1:
        return {"ok": False, "error": "max_results must be a positive integer"}
    if not isinstance(max_depth, int) or max_depth < 0:
        return {"ok": False, "error": "max_depth must be >= 0"}

    base = Path(os.path.expanduser(base_path)).resolve()
    if not base.exists():
        return {"ok": False, "error": f"base_path not found: {base}"}
    if not base.is_dir():
        return {"ok": False, "error": f"base_path is not a directory: {base}"}

    def name_matches(name: str) -> bool:
        if case_sensitive:
            return any(fnmatch.fnmatchcase(name, p) for p in patterns)
        nlower = name.lower()
        return any(fnmatch.fnmatchcase(nlower, p.lower()) for p in patterns)

    walker = base.rglob("*") if recursive else base.iterdir()
    entries: list[dict] = []
    truncated = False
    visited = 0

    try:
        for p in walker:
            visited += 1
            try:
                depth = len(p.relative_to(base).parts)
            except ValueError:
                continue
            if depth > max_depth:
                continue
            try:
                is_link = p.is_symlink()
                is_dir = p.is_dir() and not is_link
            except OSError:
                continue
            ftype = "symlink" if is_link else ("dir" if is_dir else "file")
            if ftype != "file" and not include_dirs:
                continue
            if not name_matches(p.name):
                continue
            if ftype == "file":
                mime = _mime_for(p.name)
                kind = _kind_for(mime)
            elif ftype == "dir":
                mime = ""
                kind = "dir"
            else:
                mime = ""
                kind = "symlink"
            try:
                st = p.lstat() if is_link else p.stat()
                size = int(st.st_size)
                mtime = float(st.st_mtime)
            except OSError:
                size = 0
                mtime = 0.0
            entries.append({
                "path": str(p),
                "name": p.name,
                "type": ftype,
                "mime": mime,
                "kind": kind,
                "size": size,
                "mtime": mtime,
            })
            if len(entries) >= max_results:
                truncated = True
                break
    except PermissionError as e:
        return {"ok": False, "error": f"permission denied (possibly outside allowed scope): {e}"}
    except OSError as e:
        return {"ok": False, "error": f"os error: {e}"}

    # Sondaggio post-cap §2.11: continua a contare i match restanti senza
    # accumularli in memoria, per esporre `available_total` accurato. Probing
    # cap a 10× max_results per evitare costi su tree giganti.
    extra_matches = 0
    if truncated:
        probe_cap = max(10 * max_results, max_results + 1000)
        try:
            for p in walker:
                visited += 1
                try:
                    depth = len(p.relative_to(base).parts)
                except ValueError:
                    continue
                if depth > max_depth:
                    continue
                try:
                    is_link = p.is_symlink()
                    is_dir = p.is_dir() and not is_link
                except OSError:
                    continue
                ftype = "symlink" if is_link else ("dir" if is_dir else "file")
                if ftype != "file" and not include_dirs:
                    continue
                if not name_matches(p.name):
                    continue
                extra_matches += 1
                if extra_matches >= probe_cap:
                    break
        except (PermissionError, OSError):
            pass

    matches = [e["path"] for e in entries]
    out = {
        "ok": True,
        "entries": entries,
        "matches": matches,
        "metadata": {
            "base_path": str(base),
            "patterns": patterns,
            "recursive": recursive,
            "case_sensitive": case_sensitive,
            "count": len(entries),
            "visited": visited,
            "truncated": truncated,
        },
    }
    # Convenzione cross-executor (CLAUDE.md §2.7 + §2.11): cap raggiunto = campi
    # di truncation a livello top, inclusi cap_field/cap_value/available_total
    # per ricostruire la chiamata in caso di allargamento esplicito.
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "file"
        out["used"] = len(entries)
        out["cap_field"] = "max_results"
        out["cap_value"] = max_results
        out["available_total"] = len(entries) + extra_matches
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return

    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
