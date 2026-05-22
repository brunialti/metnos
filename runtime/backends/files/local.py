"""Files backend local — filesystem locale (default client="local").

Builtin backend per il `client="local"` dei verbi files/dirs. Riusa
primitive `pathlib`+`os`+`shutil`+`fnmatch`+`mimetypes` di stdlib.
Nessuna dipendenza esterna.

Verbi esposti:
- `read(args)`: legge contenuto di UN file (testo o binary base64).
- `write(args)`: scrive contenuto in UN file (overwrite/append/fail).
- `find(args)`: walk + glob pattern match.
- `move(args)`: sposta/rinomina entries (vettoriale, dst_template).
- `find_dirs(args)`: walk dirs con metadata aggregati.
- `create_dirs(args)`: crea directory (vettoriale).
- `delete_dirs(args)`: rimuove directory (vettoriale, if_empty_only).

Contratto common: tutti ritornano dict con `ok: bool` + campi
verbo-specifici. Errori per-item in `failed[]`, mai silenzio
(CLAUDE.md §2.8).

Logica portata 1:1 dagli executor `read_files.py`/`write_files.py`/
`find_files.py`/`move_files.py`/`find_dirs.py`/`create_dirs.py`/
`delete_dirs.py` esistenti (13/5/2026, Q1 canonical+args).
"""
from __future__ import annotations

import base64
import datetime
import fnmatch
import mimetypes
import os
import re
import shutil
import sys
from pathlib import Path

# Lazy: il modulo move() usa platform_policy per system-file safety net.
_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from platform_policy import is_system_file  # noqa: E402
from messages import get as _msg  # noqa: E402


# Alias bilingue IT↔EN per i path utente standard (XDG user-dirs). Quando
# l'utente IT scrive "Immagini" su un sistema con LANG=en_US la cartella
# vera e' "Pictures": senza questo mapping find_files fallisce e il planner
# ritenta inutilmente lo stesso step (bug osservato turn d39e16bb).
_USER_DIR_ALIASES = {
    # IT lowercase → candidati ordinati per probabilita'
    "immagini":   ["Pictures", "Immagini", "Foto", "Images", "images"],
    "foto":       ["Pictures", "Foto", "Immagini", "images"],
    "documenti":  ["Documents", "Documenti", "Docs"],
    "musica":     ["Music", "Musica"],
    "video":      ["Videos", "Video", "Movies"],
    "scaricati":  ["Downloads", "Scaricati", "Download"],
    "scrivania":  ["Desktop", "Scrivania"],
    "modelli":    ["Templates", "Modelli"],
    "pubblici":   ["Public", "Pubblici"],
    # EN lowercase → candidati (caso utente IT che chiede in EN o opposto)
    "pictures":   ["Pictures", "Immagini", "Foto"],
    "documents":  ["Documents", "Documenti"],
    "music":      ["Music", "Musica"],
    "videos":     ["Videos", "Video", "Movies"],
    "movies":     ["Movies", "Videos", "Video"],
    "downloads":  ["Downloads", "Scaricati"],
    "desktop":    ["Desktop", "Scrivania"],
    "templates":  ["Templates", "Modelli"],
    "public":     ["Public", "Pubblici"],
    "images":     ["Pictures", "Immagini", "images"],
}


def _resolve_path_with_alias(base_path: str) -> tuple[Path, str | None]:
    """Risolve `base_path` provando alias bilingue se path non esiste.

    Ritorna (resolved_path, alias_note | None). Se non trova alternative,
    ritorna il path originale (cosi' il chiamante ritorna error normale).
    """
    expanded = Path(os.path.expanduser(base_path)).resolve()
    if expanded.exists():
        return expanded, None
    # Tenta alias solo se base_path e' un nome semplice (no path absoluto
    # complesso): "Immagini" o "~/Immagini" o "/home/x/Immagini" hanno
    # tutti `.name == "Immagini"`.
    name_key = expanded.name.lower()
    aliases = _USER_DIR_ALIASES.get(name_key, [])
    if not aliases:
        return expanded, None
    home = Path.home()
    for alias in aliases:
        candidate = home / alias
        if candidate.exists() and candidate.is_dir():
            return candidate, (
                f"path '{base_path}' non esiste; risolto a '{candidate}' "
                f"(alias bilingue IT/EN per xdg user-dirs)"
            )
    return expanded, None


def _home_dir_suggestions(missing_name: str, limit: int = 6) -> list[str]:
    """Quando un path utente non esiste, suggerisce cartelle in HOME che
    potrebbero essere ragionevoli alternative. Output sorted per pertinenza:
    1. Cartelle XDG user-dirs esistenti.
    2. Altre cartelle non-hidden in home.

    Il planner usa queste suggestion per chiedere all'utente quale path
    intendeva, evitando il loop_break generico "aggiungi dettaglio".
    """
    home = Path.home()
    if not home.is_dir():
        return []
    xdg_set = {"Pictures", "Documents", "Music", "Videos", "Downloads",
               "Desktop", "Templates", "Public",
               "Immagini", "Documenti", "Musica", "Video", "Scaricati",
               "Scrivania", "Modelli", "Pubblici", "Foto"}
    out_xdg: list[str] = []
    out_other: list[str] = []
    try:
        for entry in sorted(home.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in xdg_set:
                out_xdg.append(str(entry))
            else:
                out_other.append(str(entry))
    except (PermissionError, OSError):
        return []
    return (out_xdg + out_other)[:limit]


# --- read ------------------------------------------------------------------


def read(args: dict) -> dict:
    """Legge il contenuto di UN file dal filesystem locale.

    Args: path (str), encoding (utf-8|latin-1|binary, default utf-8),
          max_bytes (int|None), tail_bytes (int|None), offset (int, default 0).

    `max_bytes` e `tail_bytes` sono mutuamente esclusivi. Encoding 'binary'
    ritorna content come base64. Truncation visibility (§2.7+§2.11): se la
    lettura non copre l'intero file, espone truncated/used/available_total/
    cap_field/cap_value.
    """
    path = args.get("path")
    encoding = args.get("encoding", "utf-8")
    max_bytes = args.get("max_bytes")
    tail_bytes = args.get("tail_bytes")
    offset = args.get("offset", 0)

    # Robustezza §2.4: 0 come placeholder = None (no limit).
    if max_bytes == 0:
        max_bytes = None
    if tail_bytes == 0:
        tail_bytes = None

    if not path:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="path")}
    if max_bytes is not None and tail_bytes is not None:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="max_bytes/tail_bytes", reason="mutuamente esclusivi")}

    abs_path = os.path.abspath(os.path.expanduser(path))

    try:
        file_size = os.path.getsize(abs_path)

        if tail_bytes is not None:
            seek_to = max(0, file_size - tail_bytes)
            read_n = tail_bytes
            mode_str = "tail"
        else:
            seek_to = offset
            read_n = max_bytes
            mode_str = "offset" if offset > 0 else ("head" if max_bytes else "full")

        will_truncate = (read_n is not None) and (seek_to + (read_n or 0) < file_size)

        if encoding == "binary":
            with open(abs_path, "rb") as f:
                if seek_to:
                    f.seek(seek_to)
                data = f.read(read_n) if read_n else f.read()
            out = {
                "ok": True,
                "content": base64.b64encode(data).decode("ascii"),
                "metadata": {
                    "encoding": "binary-base64",
                    "bytes": len(data),
                    "path": abs_path,
                    "file_size": file_size,
                    "read_offset": seek_to,
                    "read_mode": mode_str,
                },
            }
            if will_truncate:
                out["truncated"] = True
                out["truncated_what"] = "byte"
                out["used"] = len(data)
                out["available_total"] = file_size
                out["cap_field"] = "max_bytes"
                out["cap_value"] = read_n
            return out
        else:
            with open(abs_path, "rb") as f:
                if seek_to:
                    f.seek(seek_to)
                raw = f.read(read_n) if read_n else f.read()
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                text = raw.decode(encoding, errors="replace")
            out = {
                "ok": True,
                "content": text,
                "metadata": {
                    "encoding": encoding,
                    "bytes": len(raw),
                    "chars": len(text),
                    "path": abs_path,
                    "file_size": file_size,
                    "read_offset": seek_to,
                    "read_mode": mode_str,
                },
            }
            if will_truncate:
                out["truncated"] = True
                out["truncated_what"] = "byte"
                out["used"] = len(raw)
                out["available_total"] = file_size
                out["cap_field"] = "max_bytes"
                out["cap_value"] = read_n
            return out
    except FileNotFoundError:
        return {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                "error": _msg("ERR_PATH_NOT_FOUND", path=str(abs_path))}
    except PermissionError:
        return {"ok": False, "error_code": "ERR_PERMISSION_DENIED",
                "error": _msg("ERR_PERMISSION_DENIED"), "detail": f"path outside allowed scope: {abs_path}"}
    except IsADirectoryError:
        return {"ok": False, "error_code": "ERR_PATH_WRONG_TYPE",
                "error": _msg("ERR_PATH_WRONG_TYPE", expected="file", actual="directory", path=str(abs_path))}
    except OSError as e:
        return {"ok": False, "error_code": "ERR_OP_FAILED",
                "error": _msg("ERR_OP_FAILED", reason=f"os error: {e}")}


# --- write -----------------------------------------------------------------


def write(args: dict) -> dict:
    """Scrive contenuto in UN file. Args: path, content, encoding, mode."""
    path = args.get("path")
    content = args.get("content")
    encoding = args.get("encoding", "utf-8")
    mode = args.get("mode", "overwrite")

    if not path:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="path")}
    if content is None:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="content")}
    if mode not in ("overwrite", "append", "fail_if_exists"):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="mode", reason=f"invalid value '{mode}'")}

    abs_path = os.path.abspath(os.path.expanduser(path))
    pre_existed = os.path.exists(abs_path)

    if mode == "fail_if_exists" and pre_existed:
        return {"ok": False, "error_code": "ERR_DST_EXISTS",
                "error": _msg("ERR_DST_EXISTS"), "detail": str(abs_path)}

    # mkdir -p del parent + registro per undo (§2.4 robustezza NL→det).
    parent = os.path.dirname(abs_path)
    created_parents: list[str] = []
    if parent and not os.path.isdir(parent):
        chain: list[str] = []
        p = parent
        while p and not os.path.isdir(p):
            chain.append(p)
            np = os.path.dirname(p)
            if np == p:
                break
            p = np
        try:
            os.makedirs(parent, exist_ok=True)
            created_parents = list(reversed(chain))
        except OSError as e:
            return {"ok": False, "error_code": "ERR_PARENT_MKDIR_FAIL",
                    "error": _msg("ERR_PARENT_MKDIR_FAIL", path=str(parent), reason=str(e))}

    try:
        if encoding == "binary":
            data = base64.b64decode(content)
            file_mode = "ab" if mode == "append" else "wb"
            with open(abs_path, file_mode) as f:
                f.write(data)
            bytes_written = len(data)
            entry = {
                "path": abs_path,
                "created": (not pre_existed),
                "bytes_written": bytes_written,
                "encoding": "binary",
                "mode": mode,
            }
            return {
                "ok": True,
                "ok_count": 1,
                "fail_count": 0,
                "results": [entry],
                "dirs_created": created_parents,
            }
        else:
            file_mode = "a" if mode == "append" else "w"
            with open(abs_path, file_mode, encoding=encoding) as f:
                f.write(content)
            bytes_written = len(content.encode(encoding))
            entry = {
                "path": abs_path,
                "created": (not pre_existed),
                "bytes_written": bytes_written,
                "encoding": encoding,
                "mode": mode,
            }
            return {
                "ok": True,
                "ok_count": 1,
                "fail_count": 0,
                "results": [entry],
                "dirs_created": created_parents,
            }
    except PermissionError:
        return {"ok": False, "error_code": "ERR_PERMISSION_DENIED",
                "error": _msg("ERR_PERMISSION_DENIED"), "detail": f"path outside allowed scope: {abs_path}"}
    except IsADirectoryError:
        return {"ok": False, "error_code": "ERR_PATH_WRONG_TYPE",
                "error": _msg("ERR_PATH_WRONG_TYPE", expected="file", actual="directory", path=str(abs_path))}
    except OSError as e:
        return {"ok": False, "error_code": "ERR_OP_FAILED",
                "error": _msg("ERR_OP_FAILED", reason=f"os error: {e}")}
    except Exception as e:
        return {"ok": False, "error_code": "ERR_OP_FAILED",
                "error": _msg("ERR_OP_FAILED", reason=f"unexpected {type(e).__name__}: {e}")}


# --- find ------------------------------------------------------------------

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
    Una str con separatori naturali (virgola, pipe) viene splittata.
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


def find(args: dict) -> dict:
    """Cerca file per pattern dentro base_path. Args: base_path, pattern|patterns, ..."""
    base_path = args.get("base_path")
    recursive = args.get("recursive", True)
    max_results = args.get("max_results", 1000)
    max_depth = args.get("max_depth", 10)
    include_dirs = args.get("include_dirs", False)
    case_sensitive = args.get("case_sensitive", False)

    patterns = _parse_compound_pattern(args.get("pattern")) + _parse_compound_pattern(args.get("patterns"))

    if not patterns:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="pattern (o 'patterns')")}
    if not base_path:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="base_path")}
    if not isinstance(max_results, int) or max_results < 1:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="max_results", reason="must be a positive integer")}
    if not isinstance(max_depth, int) or max_depth < 0:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="max_depth", reason="must be >= 0")}

    base, alias_note = _resolve_path_with_alias(base_path)
    if not base.exists():
        # Suggerisci cartelle home esistenti: il planner puo' chiedere
        # all'utente quale intendeva, evitando loop_break generico.
        return {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                "error": _msg("ERR_PATH_NOT_FOUND", path=str(base)),
                "suggested_paths": _home_dir_suggestions(base.name)}
    if not base.is_dir():
        return {"ok": False, "error_code": "ERR_PATH_WRONG_TYPE",
                "error": _msg("ERR_PATH_WRONG_TYPE", expected="directory", actual="file", path=str(base))}

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
        return {"ok": False, "error_code": "ERR_PERMISSION_DENIED",
                "error": _msg("ERR_PERMISSION_DENIED"), "detail": str(e)}
    except OSError as e:
        return {"ok": False, "error_code": "ERR_OP_FAILED",
                "error": _msg("ERR_OP_FAILED", reason=f"os error: {e}")}

    # Sondaggio post-cap §2.11
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
            # Se il path originale non esisteva ma e' stato risolto via alias
            # bilingue IT/EN, lo segnaliamo nei metadata (planner + UI).
            **({"alias_resolved": alias_note} if alias_note else {}),
        },
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "file"
        out["used"] = len(entries)
        out["cap_field"] = "max_results"
        out["cap_value"] = max_results
        out["available_total"] = len(entries) + extra_matches
    return out


# --- move ------------------------------------------------------------------


def _entry_fields(entry, src_path):
    name = entry.get("name") or src_path.name
    if "." in name and not name.startswith("."):
        stem, ext = name.rsplit(".", 1)
    else:
        stem, ext = name, ""
    parent = entry.get("parent") or str(src_path.parent)
    mtime_epoch = entry.get("mtime_epoch")
    if mtime_epoch is None:
        try:
            mtime_epoch = src_path.stat().st_mtime
        except OSError:
            mtime_epoch = 0
    dt = datetime.datetime.fromtimestamp(float(mtime_epoch))
    de = entry.get("date_epoch")
    if de is None:
        date_dt = dt
    else:
        try:
            date_dt = datetime.datetime.fromtimestamp(float(de))
        except (TypeError, ValueError, OSError):
            date_dt = dt
    place = entry.get("place") or "unknown"
    if not isinstance(place, str) or not place.strip():
        place = "unknown"
    return {
        "path": str(src_path),
        "name": name,
        "stem": stem,
        "ext": ext,
        "parent": parent,
        "date_year": date_dt.strftime("%Y"),
        "date_yymmdd": date_dt.strftime("%y%m%d"),
        "date_yyyymmdd": date_dt.strftime("%Y%m%d"),
        "place": place,
    }


def _move_one(src_path, dst_path, overwrite, parents):
    """Esegue lo spostamento; ritorna (ok, error, dirs_created)."""
    if not src_path.exists():
        return False, f"src not found: {src_path}", []
    if str(dst_path) == str(src_path):
        return False, "src and dst are the same path", []
    if dst_path.exists():
        if not overwrite:
            return False, f"dst already exists (use overwrite=true): {dst_path}", []
        try:
            if dst_path.is_dir() and not dst_path.is_symlink():
                shutil.rmtree(dst_path)
            else:
                dst_path.unlink()
        except OSError as e:
            return False, f"failed to remove existing dst: {e}", []
    dirs_created = []
    if parents:
        ancestors = []
        p = dst_path.parent
        while not p.exists():
            ancestors.append(p)
            p = p.parent
        ancestors.reverse()
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            return False, f"permission denied creating dst parent (possibly outside allowed scope): {e}", []
        except OSError as e:
            return False, f"os error creating dst parent: {e}", []
        dirs_created = ancestors
    try:
        shutil.move(str(src_path), str(dst_path))
    except PermissionError as e:
        return False, f"permission denied (possibly outside allowed scope): {e}", []
    except OSError as e:
        return False, f"os error: {e}", []
    return True, None, dirs_created


def move(args: dict) -> dict:
    """Sposta/rinomina entries (vettoriale). Args: entries, dst_template, ..."""
    entries = args.get("entries")
    dst_template = args.get("dst_template")
    overwrite = bool(args.get("overwrite", False))
    parents = bool(args.get("parents", True))
    allow_dirs = bool(args.get("allow_dirs", False))
    allow_system = bool(args.get("allow_system", False))

    if entries is None or not isinstance(entries, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="entries", reason="must be a list")}
    if not dst_template or not isinstance(dst_template, str):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="dst_template", reason="must be a string")}

    results = []
    failed = []
    all_dirs_created = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            failed.append({"index": i, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg=f"entries[{i}]", reason="must be a dict")})
            continue
        src_arg = entry.get("path") or entry.get("src")
        if not src_arg or not isinstance(src_arg, str):
            failed.append({"index": i, "error_code": "ERR_ARG_MISSING",
                           "error": _msg("ERR_ARG_MISSING", arg=f"entries[{i}].path (o 'src')")})
            continue
        src_path = Path(os.path.expanduser(src_arg)).resolve()
        kind = entry.get("kind") or ""
        if not allow_dirs and (kind == "dir" or src_path.is_dir()):
            failed.append({"index": i, "src": str(src_path),
                           "error_code": "ERR_REFUSE_MOVE",
                           "error": _msg("ERR_REFUSE_MOVE", path=str(src_path),
                                          reason="directory (passa allow_dirs=true o filtra prima con filter_entries)")})
            continue
        if not allow_system and is_system_file(src_path.name):
            failed.append({"index": i, "src": str(src_path),
                           "error_code": "ERR_REFUSE_MOVE",
                           "error": _msg("ERR_REFUSE_MOVE", path=str(src_path),
                                          reason=f"system file '{src_path.name}' (passa allow_system=true o filtra prima)")})
            continue
        try:
            fields = _entry_fields(entry, src_path)
            dst_str = dst_template.format(**fields)
        except KeyError as e:
            failed.append({"index": i, "src": str(src_path),
                           "error_code": "ERR_TEMPLATE_FAIL",
                           "error": _msg("ERR_TEMPLATE_FAIL", stage="placeholder", reason=str(e))})
            continue
        except Exception as e:
            failed.append({"index": i, "src": str(src_path),
                           "error_code": "ERR_TEMPLATE_FAIL",
                           "error": _msg("ERR_TEMPLATE_FAIL", stage="render", reason=str(e))})
            continue
        dst_path = Path(os.path.expanduser(dst_str)).resolve()
        ok, err, dirs_created = _move_one(src_path, dst_path, overwrite, parents)
        if ok:
            results.append({"src": str(src_path), "dst": str(dst_path)})
            for d in dirs_created:
                all_dirs_created.add(str(d))
        else:
            failed.append({"index": i, "src": str(src_path), "dst": str(dst_path), "error": err})

    dirs_created_list = sorted(all_dirs_created, key=lambda p: p.count("/"), reverse=True)

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "dirs_created": dirs_created_list,
        "failed": failed,
    }


def reverse_move(plan, results):
    """Undo multistage di move(): sposta dst→src + rimuove dir create vuote."""
    pairs = (results or {}).get("results") or []
    dirs_created = (results or {}).get("dirs_created") or []
    out_results, failed = [], []

    for i, p in enumerate(pairs):
        src_now = Path(p["dst"])
        dst_back = Path(p["src"])
        ok, err, _ = _move_one(src_now, dst_back, overwrite=False, parents=True)
        if ok:
            out_results.append({"src": str(src_now), "dst": str(dst_back)})
        else:
            failed.append({"index": i, "src": str(src_now), "dst": str(dst_back), "error": err})

    dirs_removed = []
    dirs_kept = []
    sorted_dirs = sorted(dirs_created, key=lambda p: p.count("/"), reverse=True)
    for d_str in sorted_dirs:
        d = Path(d_str)
        if not d.exists():
            continue
        if not d.is_dir():
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                dirs_removed.append(str(d))
            else:
                dirs_kept.append(str(d))
        except OSError:
            dirs_kept.append(str(d))

    return {
        "ok": len(failed) == 0,
        "ok_count": len(out_results),
        "fail_count": len(failed),
        "results": out_results,
        "dirs_removed": dirs_removed,
        "dirs_kept": dirs_kept,
        "failed": failed,
    }


# --- find_dirs -------------------------------------------------------------


def find_dirs(args: dict) -> dict:
    """Walk ricorsivo dell'albero di directory con metadati aggregati."""
    base_path = args.get("base_path")
    recursive = args.get("recursive", True)
    max_depth = args.get("max_depth", 10)
    max_results = args.get("max_results", 1000)
    include_hidden = bool(args.get("include_hidden", False))

    if not base_path:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="base_path")}
    if not isinstance(max_results, int) or max_results < 1:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="max_results", reason="must be a positive integer")}
    if not isinstance(max_depth, int) or max_depth < 0:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="max_depth", reason="must be >= 0")}

    base, alias_note = _resolve_path_with_alias(base_path)
    if not base.exists():
        # Suggerisci cartelle home esistenti: il planner puo' chiedere
        # all'utente quale intendeva, evitando loop_break generico.
        return {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                "error": _msg("ERR_PATH_NOT_FOUND", path=str(base)),
                "suggested_paths": _home_dir_suggestions(base.name)}
    if not base.is_dir():
        return {"ok": False, "error_code": "ERR_PATH_WRONG_TYPE",
                "error": _msg("ERR_PATH_WRONG_TYPE", expected="directory", actual="file", path=str(base))}

    entries: list[dict] = []
    truncated = False
    visited_dirs = 0

    def _scan_dir(d: Path) -> dict | None:
        try:
            file_count = 0
            total_bytes = 0
            size_min: int | None = None
            size_max: int | None = None
            for child in d.iterdir():
                if not include_hidden and child.name.startswith("."):
                    continue
                try:
                    if child.is_symlink():
                        continue
                    if child.is_file():
                        file_count += 1
                        s = child.stat().st_size
                        total_bytes += s
                        if size_min is None or s < size_min:
                            size_min = s
                        if size_max is None or s > size_max:
                            size_max = s
                except OSError:
                    continue
            try:
                mt = d.stat().st_mtime
            except OSError:
                mt = 0.0
            return {
                "path": str(d),
                "name": d.name,
                "file_count": file_count,
                "total_bytes": total_bytes,
                "size_min": size_min if size_min is not None else 0,
                "size_max": size_max if size_max is not None else 0,
                "mtime": float(mt),
            }
        except PermissionError:
            return None
        except OSError:
            return None

    try:
        base_entry = _scan_dir(base)
        if base_entry is not None:
            entries.append(base_entry)
            visited_dirs += 1
            if len(entries) >= max_results:
                truncated = True

        if recursive and not truncated:
            for p in base.rglob("*"):
                try:
                    if p.is_symlink() or not p.is_dir():
                        continue
                    depth = len(p.relative_to(base).parts)
                except (ValueError, OSError):
                    continue
                if depth > max_depth:
                    continue
                if not include_hidden and any(
                    seg.startswith(".") for seg in p.relative_to(base).parts
                ):
                    continue
                entry = _scan_dir(p)
                visited_dirs += 1
                if entry is None:
                    continue
                entries.append(entry)
                if len(entries) >= max_results:
                    truncated = True
                    break
    except PermissionError as e:
        return {"ok": False,
                "error_code": "ERR_PERMISSION_DENIED",
                "error": _msg("ERR_PERMISSION_DENIED"), "detail": str(e)}
    except OSError as e:
        return {"ok": False, "error_code": "ERR_OP_FAILED",
                "error": _msg("ERR_OP_FAILED", reason=f"os error: {e}")}

    matches = [e["path"] for e in entries]
    out = {
        "ok": True,
        "entries": entries,
        "matches": matches,
        "metadata": {
            "base_path": str(base),
            "recursive": recursive,
            "include_hidden": include_hidden,
            "count": len(entries),
            "visited_dirs": visited_dirs,
            "truncated": truncated,
            **({"alias_resolved": alias_note} if alias_note else {}),
        },
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "directory"
        out["used"] = len(entries)
        out["cap_field"] = "max_results"
        out["cap_value"] = max_results
    return out


# --- create_dirs -----------------------------------------------------------


def _create_one(path_arg, parents, exist_ok, mode):
    target = Path(os.path.expanduser(path_arg)).resolve()
    pre_existed = target.exists()
    try:
        kwargs = {"parents": bool(parents), "exist_ok": bool(exist_ok)}
        if mode is not None:
            kwargs["mode"] = mode
        target.mkdir(**kwargs)
    except FileExistsError:
        return False, str(target), f"path already exists and is not a directory: {target}", False
    except FileNotFoundError as e:
        return False, str(target), f"missing parent (use parents=true to auto-create): {e}", False
    except PermissionError as e:
        return False, str(target), f"permission denied (possibly outside allowed scope): {e}", False
    except OSError as e:
        return False, str(target), f"os error: {e}", False
    created = (not pre_existed)
    try:
        st = target.stat()
        return True, str(target), oct(st.st_mode & 0o777), created
    except OSError:
        return True, str(target), None, created


def create_dirs(args: dict) -> dict:
    """Crea directory (vettoriale). Args: paths, parents, exist_ok, mode."""
    paths = args.get("paths")
    parents = args.get("parents", True)
    exist_ok = args.get("exist_ok", True)
    mode = args.get("mode")

    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="paths", reason="must be a list")}
    if mode is not None:
        if not isinstance(mode, int) or not (0 <= mode <= 0o777):
            return {"ok": False, "error_code": "ERR_ARG_INVALID",
                    "error": _msg("ERR_ARG_INVALID", arg="mode", reason="must be an integer in 0..0o777")}

    results = []
    failed = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg="path", reason="must be a non-empty string")})
            continue
        ok, target, info, created = _create_one(p, parents, exist_ok, mode)
        if ok:
            entry = {"path": target, "created": created}
            if info:
                entry["mode_octal"] = info
            results.append(entry)
        else:
            failed.append({"index": i, "path": target, "error": info})

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
    }


def reverse_create_dirs(plan, results):
    """Undo: rimuove le dir create dal forward (created=true), solo se vuote."""
    entries = (results or {}).get("results") or []
    out_results, failed = [], []
    candidates = [e for e in entries if e.get("created")]
    candidates.sort(key=lambda e: len(e["path"]), reverse=True)
    for i, entry in enumerate(candidates):
        path = Path(entry["path"])
        if not path.exists():
            failed.append({"index": i, "path": str(path),
                           "error_code": "ERR_PATH_NOT_FOUND",
                           "error": _msg("ERR_PATH_NOT_FOUND", path=str(path))})
            continue
        if not path.is_dir():
            failed.append({"index": i, "path": str(path),
                           "error_code": "ERR_PATH_WRONG_TYPE",
                           "error": _msg("ERR_PATH_WRONG_TYPE", expected="directory", actual="file", path=str(path))})
            continue
        try:
            children = list(path.iterdir())
        except OSError as e:
            failed.append({"index": i, "path": str(path),
                           "error_code": "ERR_DIR_OP_FAILED",
                           "error": _msg("ERR_DIR_OP_FAILED", op="list", path=str(path), reason=str(e))})
            continue
        if children:
            failed.append({"index": i, "path": str(path),
                           "error_code": "ERR_DIR_OP_FAILED",
                           "error": _msg("ERR_DIR_OP_FAILED", op="rmdir", path=str(path),
                                          reason=f"directory non vuota ({len(children)} items): no auto-remove")})
            continue
        try:
            path.rmdir()
            out_results.append({"path": str(path), "removed": True})
        except OSError as e:
            failed.append({"index": i, "path": str(path),
                           "error_code": "ERR_DIR_OP_FAILED",
                           "error": _msg("ERR_DIR_OP_FAILED", op="rmdir", path=str(path), reason=str(e))})
    return {
        "ok": len(failed) == 0,
        "ok_count": len(out_results),
        "fail_count": len(failed),
        "results": out_results,
        "failed": failed,
    }


# --- delete_dirs -----------------------------------------------------------


def _remove_one(path_arg, if_empty_only, force):
    target = Path(os.path.expanduser(path_arg)).resolve()
    if not target.exists():
        return False, str(target), "path does not exist"
    if not target.is_dir():
        return False, str(target), "not a directory"
    try:
        children = list(target.iterdir())
    except OSError as e:
        return False, str(target), f"cannot list: {e}"
    if children and not force:
        return False, str(target), f"directory not empty ({len(children)} items); use force=true for recursive remove"
    try:
        if force and children:
            shutil.rmtree(target)
        else:
            target.rmdir()
    except PermissionError as e:
        return False, str(target), f"permission denied (possibly outside allowed scope): {e}"
    except OSError as e:
        return False, str(target), f"os error: {e}"
    return True, str(target), None


def delete_files(args: dict) -> dict:
    """Rimuove file (vettoriale, NON directory). Args: paths.

    Reversibile §2.3: ogni file rimosso viene backupped come blob in
    `<METNOS_HISTORY_DIR>/<METNOS_TURN_ID>/blob/<sha256>.bin` PRIMA
    dell'unlink. Il runtime usa `restore_blob_backup` per ripristinare.

    Safety §2.9: rifiuta paths fuori dallo scope di scrittura, rifiuta
    directory (richiede `delete_dirs` esplicito), rifiuta system files
    (override via `allow_system=true` non ancora supportato qui).

    Best-effort §7.4: ogni path e' indipendente, una failure non blocca
    le altre.
    """
    import hashlib
    import shutil
    paths = args.get("paths")
    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="paths", reason="must be a list")}

    # Storage blob: tracciamento turn per reverse pattern §2.3.
    history_dir = os.environ.get("METNOS_HISTORY_DIR") or str(
        Path.home() / ".local" / "share" / "metnos" / "_history")
    turn_id = os.environ.get("METNOS_TURN_ID") or "no_turn"
    blob_dir = Path(history_dir) / turn_id / "blob"

    results = []
    failed = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg="path", reason="must be a non-empty string")})
            continue
        try:
            abs_path = Path(os.path.expanduser(p)).resolve()
        except OSError as e:
            failed.append({"index": i, "path": p, "error_code": "ERR_OP_FAILED",
                           "error": _msg("ERR_OP_FAILED", reason=f"path resolve: {e}")})
            continue
        if not abs_path.exists():
            failed.append({"index": i, "path": str(abs_path),
                           "error_code": "ERR_PATH_NOT_FOUND",
                           "error": _msg("ERR_PATH_NOT_FOUND", path=str(abs_path))})
            continue
        if abs_path.is_dir():
            failed.append({"index": i, "path": str(abs_path),
                           "error_code": "ERR_PATH_WRONG_TYPE",
                           "error": _msg("ERR_PATH_WRONG_TYPE",
                                          expected="file", actual="directory", path=str(abs_path))})
            continue
        if not abs_path.is_file():
            failed.append({"index": i, "path": str(abs_path),
                           "error_code": "ERR_PATH_WRONG_TYPE",
                           "error": _msg("ERR_PATH_WRONG_TYPE",
                                          expected="file", actual="special", path=str(abs_path))})
            continue
        # Safety net: rifiuta system file (whitelisting platform_policy).
        try:
            if is_system_file(abs_path.name):
                failed.append({"index": i, "path": str(abs_path),
                               "error_code": "ERR_REFUSE_MOVE",
                               "error": _msg("ERR_REFUSE_MOVE", path=str(abs_path),
                                              reason="system file (no allow_system override)")})
                continue
        except Exception:
            pass
        # Backup blob → calcolo sha256 streaming, copy preserve metadata.
        try:
            h = hashlib.sha256()
            with abs_path.open("rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            blob_sha256 = h.hexdigest()
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob_path = blob_dir / f"{blob_sha256}.bin"
            if not blob_path.exists():
                shutil.copy2(abs_path, blob_path)
        except OSError as e:
            failed.append({"index": i, "path": str(abs_path),
                           "error_code": "ERR_OP_FAILED",
                           "error": _msg("ERR_OP_FAILED", reason=f"backup blob: {e}")})
            continue
        # Unlink dopo backup confermato (§2.9 spirit).
        try:
            abs_path.unlink()
        except OSError as e:
            failed.append({"index": i, "path": str(abs_path),
                           "error_code": "ERR_OP_FAILED",
                           "error": _msg("ERR_OP_FAILED", reason=f"unlink: {e}")})
            continue
        results.append({
            "path": str(abs_path), "removed": True,
            "blob_path": str(blob_path), "blob_sha256": blob_sha256,
        })

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
    }


def delete_dirs(args: dict) -> dict:
    """Rimuove directory (vettoriale). Args: paths, if_empty_only, force."""
    paths = args.get("paths")
    if_empty_only = args.get("if_empty_only", True)  # informational; force=true overrides
    force = bool(args.get("force", False))

    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="paths", reason="must be a list")}

    results = []
    failed = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg="path", reason="must be a non-empty string")})
            continue
        ok, target, info = _remove_one(p, if_empty_only, force)
        if ok:
            results.append({"path": target, "removed": True})
        else:
            failed.append({"index": i, "path": target, "error": info})

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
    }
