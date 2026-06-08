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
(the design guide §2.8).

Logica portata 1:1 dagli executor `read_files.py`/`write_files.py`/
`find_files.py`/`move_files.py`/`find_dirs.py`/`create_dirs.py`/
`delete_dirs.py` esistenti (13/5/2026, Q1 canonical+args).
"""
from __future__ import annotations

import base64
import datetime
import fnmatch
import json
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
import config as _C  # noqa: E402 §7.11
# path_alias modulo riusabile (D.1, D.3). Re-export degli alias come moduli
# locali per back-compat con test esistenti che mockano backends.files.local.
from path_alias import (  # noqa: E402
    candidate_roots as _candidate_roots,
    count_files_recursive as _count_files_recursive,
    resolve_path_with_alias as _resolve_path_with_alias,
    list_alias_candidates as _list_alias_candidates,
    check_mutating_path_ambiguity as _check_mutating_path_ambiguity,
    home_dir_suggestions as _home_dir_suggestions,
    USER_DIR_ALIASES as _USER_DIR_ALIASES,
)

# Alias bilingue IT↔EN per i path utente standard (XDG user-dirs). Quando
# l'utente IT scrive "Immagini" su un sistema con LANG=en_US la cartella
# vera e' "Pictures": senza questo mapping find_files fallisce e il planner
# Le funzioni di alias resolver vivono ora in `runtime/path_alias.py` (D.3
# refactor 22/5/2026, modulo riusabile da local.py + list_dirs.py + altri
# executor). Import sopra al modulo. Riferimenti `_*` mantengono back-compat
# con il codice del backend.


# --- read ------------------------------------------------------------------


def read(args: dict) -> dict:
    """Legge il contenuto di UN file dal filesystem locale.

    Args: path (str), encoding (utf-8|latin-1|binary, default utf-8),
          max_bytes (int|None), tail_bytes (int|None), offset (int, default 0).

    `max_bytes` e `tail_bytes` sono mutuamente esclusivi. Encoding 'binary'
    ritorna content come base64. Truncation visibility (§2.7+§2.11): se la
    lettura non copre l'intero file, espone truncated/used/available_total/
    cap_field/cap_value.

    Vettoriale §2.1: con `paths=[...]` o `entries` (da from_step, proietta
    entries[*].path) legge N file e ritorna `entries=[{path, content, ...}]`
    (§2.6: read arricchisce una lista). La forma scalare `path` resta {ok,
    content, metadata} (back-compat).
    """
    # Espansione DIRECTORY → file dentro (universale: "leggi i file in una
    # cartella" in 1 call deterministica, niente find_files+read_files a 2
    # passi). `pattern` (default '*') filtra; ricorsione no. Vale per path
    # scalare-dir e per ogni elemento-dir di paths.
    import glob as _glob

    def _expand_dir(p):
        if not isinstance(p, str) or not p:
            return []
        ap = os.path.abspath(os.path.expanduser(p))
        if os.path.isdir(ap):
            pat = args.get("pattern") or "*"
            return sorted(f for f in _glob.glob(os.path.join(ap, pat))
                          if os.path.isfile(f))
        return [p]

    _vec = None
    _ps = args.get("paths")
    _es = args.get("entries")
    _scalar = args.get("path")
    if isinstance(_ps, list):
        _vec = []
        for p in _ps:
            _vec.extend(_expand_dir(p))
    elif isinstance(_es, list):
        _vec = []
        for e in _es:
            if isinstance(e, dict) and isinstance(e.get("path"), str) and e.get("path"):
                _vec.extend(_expand_dir(e["path"]))
    elif isinstance(_scalar, str) and os.path.isdir(
            os.path.abspath(os.path.expanduser(_scalar))):
        _vec = _expand_dir(_scalar)  # path scalare = directory → vettoriale
    if _vec is not None:
        try:
            _maxf = int(args.get("max_files") or _DEFAULT_MAX_FILES)
        except (TypeError, ValueError):
            _maxf = _DEFAULT_MAX_FILES
        _avail = len(_vec)
        _trunc = _maxf > 0 and len(_vec) > _maxf
        if _trunc:
            _vec = _vec[:_maxf]
        _base = {k: v for k, v in args.items()
                 if k not in ("paths", "entries", "parse")}
        _parse = args.get("parse")
        out_entries = []
        ok_count = fail_count = 0
        for _p in _vec:
            r = read({**_base, "path": _p})  # delega alla logica scalare
            if r.get("ok"):
                ent = {"path": _p, "ok": True}
                # parse="json": fonde i campi del JSON nell'entry (record
                # interrogabile da filter_entries) — simmetrico al write JSON
                # dell'inbound. Default: content come stringa + metadata.
                if _parse == "json":
                    try:
                        _pj = json.loads(r.get("content") or "")
                        if isinstance(_pj, dict):
                            ent.update(_pj)
                        else:
                            ent["content"] = _pj
                    except (ValueError, TypeError):
                        ent["content"] = r.get("content")
                else:
                    ent["content"] = r.get("content")
                    ent.update(r.get("metadata") or {})
                ok_count += 1
            else:
                ent = {"path": _p, "ok": False,
                       "error_code": r.get("error_code"), "error": r.get("error")}
                fail_count += 1
            out_entries.append(ent)
        out = {"ok": fail_count == 0, "ok_count": ok_count,
               "fail_count": fail_count, "entries": out_entries}
        if fail_count:
            _ff = next((e for e in out_entries if not e.get("ok")), None)
            if _ff:
                out["error_code"] = _ff.get("error_code")
                out["error"] = _ff.get("error")
        if _trunc:
            out.update({"truncated": True, "truncated_what": "files",
                        "used": len(_vec), "available_total": _avail,
                        "cap_field": "max_files", "cap_value": _maxf})
        return out

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


_VALID_WRITE_MODES = ("overwrite", "append", "fail_if_exists", "skip_if_exists")
_DEFAULT_MAX_FILES = 500


def _safe_format(template: str, entry: dict):
    """`template.format(**entry)` deterministico §7.9. Ritorna (ok, valore_o_errore).
    Campo mancante o spec invalido → (False, messaggio onesto), niente raise."""
    # §2.4: l'LLM usa spesso la sintassi ${entry.campo} o ${campo} (convenzione
    # di piping) invece del {campo} di str.format → normalizza prima (bug q28
    # 5/6). Lascia intatti i ${X:Y} con due punti (es. ${RUNTIME:..} già risolti).
    if isinstance(template, str) and "${" in template:
        import re as _re
        template = _re.sub(r"\$\{\s*entry\.(\w+)\s*\}", r"{\1}", template)
        template = _re.sub(r"\$\{\s*(\w+)\s*\}", r"{\1}", template)
    try:
        # §2.8: un campo del template ASSENTE nell'entry → stringa vuota, NON
        # hard-fail. L'LLM sceglie i nomi-campo best-effort (es. {memory} ma
        # l'entry processo ha 'mem_pct') → rendi i campi presenti, lascia vuoti
        # i mancanti (bug q28/q34 5/6: il write falliva tutto per un nome
        # leggermente diverso). Spec malformato → errore onesto.
        class _SafeDict(dict):
            def __missing__(self, _k):
                return ""
        flat = entry if isinstance(entry, dict) else {"value": entry}
        return True, template.format_map(_SafeDict(flat))
    except (ValueError, TypeError, IndexError, AttributeError) as ex:
        return False, _msg("ERR_ARG_INVALID", arg="template",
                            reason=f"template non valido: {ex}")


def _derive_content(entry, content_field, content_template, content_format):
    """Deriva il contenuto (stringa) di un file da una `entry`. Precedenza:
    content_field > content_template > content_format(text) > JSON (default).
    Deterministico §7.9. Ritorna (ok, valore_o_errore)."""
    if content_field:
        if isinstance(entry, dict) and content_field in entry:
            v = entry[content_field]
            return True, (v if isinstance(v, str)
                          else json.dumps(v, ensure_ascii=False, indent=2))
        return False, _msg("ERR_ARG_INVALID", arg="content_field",
                            reason=f"'{content_field}' assente nell'entry")
    if content_template:
        return _safe_format(
            content_template,
            entry if isinstance(entry, dict) else {"value": entry})
    if content_format == "text":
        return True, (entry if isinstance(entry, str)
                      else json.dumps(entry, ensure_ascii=False, indent=2))
    return True, json.dumps(entry, ensure_ascii=False, indent=2)


def _collect_write_specs(args: dict):
    """Normalizza gli input in una lista di spec {path, content, encoding,
    mode}. Vettoriale §2.1: la lista ammette anche un solo elemento. Shape
    in ordine di precedenza: files[] esplicito → entries[]+path_template →
    paths[]+contents[] → path+content scalare. Ritorna (specs, errore_o_None)."""
    enc_default = args.get("encoding", "utf-8")
    mode_default = args.get("mode", "overwrite")
    specs: list[dict] = []

    files = args.get("files")
    entries = args.get("entries")
    paths = args.get("paths")
    contents = args.get("contents")

    if isinstance(files, list):
        for i, f in enumerate(files):
            if not isinstance(f, dict) or not f.get("path"):
                return None, _msg("ERR_ARG_INVALID", arg="files",
                                  reason=f"elemento {i} senza 'path'")
            if f.get("content") is None:
                return None, _msg("ERR_ARG_INVALID", arg="files",
                                  reason=f"elemento {i} senza 'content'")
            specs.append({"path": f["path"], "content": f["content"],
                          "encoding": f.get("encoding", enc_default),
                          "mode": f.get("mode", mode_default)})
        return specs, None

    if isinstance(entries, list):
        path_template = args.get("path_template")
        # §2.8: un `path_template` SENZA placeholder (`{campo}`/`${campo}`) NON è
        # un template per-entry — è un literal. Il planner ci mette per errore il
        # CONTENUTO (es. path_template="RISC-V è ...") e, avendo precedenza sul
        # `path` scalare, scriverebbe un file mal-nominato nel cwd invece che nel
        # path dato. Scartalo → si ricade sull'AGGREGATO verso `path` (bug
        # latente write path=contenuto, q4/q27). Deterministico §7.9.
        if isinstance(path_template, str) and "{" not in path_template:
            path_template = None
        content_field = args.get("content_field")
        content_template = args.get("content_template")
        content_format = args.get("content_format")
        # set_fields: campi costanti fusi in OGNI entry prima di serializzare
        # (read-modify-write: es. set_fields={"status":"answered"} per marcare
        # le issue gestite). Universale: persisti la lista con un campo aggiornato.
        set_fields = args.get("set_fields")
        # AGGREGATO (§2.10, 4/6/2026): entries + `path` SCALARE senza
        # `path_template` → UN solo file con TUTTE le entries serializzate
        # (es. extract_entries → write_files(path="/dir/out.txt", from_step=N)).
        # Senza questo, il caso "salva la lista in un unico file" falliva con
        # "path_template/content mancante" (fix q4 live-test).
        scalar_path = args.get("path")
        if not path_template and scalar_path and str(scalar_path).strip():
            parts = []
            for entry in entries:
                if isinstance(set_fields, dict) and isinstance(entry, dict):
                    entry = {**entry, **set_fields}
                ok, c = _derive_content(entry, content_field,
                                        content_template, content_format)
                if not ok:
                    return None, c
                parts.append(c if isinstance(c, str) else str(c))
            specs.append({"path": scalar_path, "content": "\n".join(parts),
                          "encoding": enc_default, "mode": mode_default})
            return specs, None
        for entry in entries:
            if isinstance(set_fields, dict) and isinstance(entry, dict):
                entry = {**entry, **set_fields}
            if path_template:
                ok, p = _safe_format(
                    path_template,
                    entry if isinstance(entry, dict) else {"value": entry})
                if not ok:
                    return None, p
            elif isinstance(entry, dict) and entry.get("path"):
                p = entry["path"]
            else:
                return None, _msg("ERR_ARG_MISSING", arg="path_template")
            ok, c = _derive_content(entry, content_field,
                                    content_template, content_format)
            if not ok:
                return None, c
            specs.append({"path": p, "content": c,
                          "encoding": enc_default, "mode": mode_default})
        return specs, None

    if isinstance(paths, list) and isinstance(contents, list):
        if len(paths) != len(contents):
            return None, _msg("ERR_ARG_INVALID", arg="contents",
                              reason="paths e contents hanno lunghezza diversa")
        for p, c in zip(paths, contents):
            specs.append({"path": p, "content": c,
                          "encoding": enc_default, "mode": mode_default})
        return specs, None

    # Scalare: degenere N=1 (la "lista" ha un solo elemento).
    path = args.get("path")
    content = args.get("content")
    if path is None or not str(path).strip():
        return None, _msg("ERR_ARG_MISSING", arg="path")
    if content is None:
        return None, _msg("ERR_ARG_MISSING", arg="content")
    # content NON-stringa (§2.10, 4/6/2026): il planner spesso pipa una LISTA o
    # un record come `content` (es. content={{stepN.entries}} risolto a lista) →
    # serializza in testo leggibile invece di passare un oggetto a file.write()
    # (TypeError: write() argument must be str, not list). Fix q4 live-test.
    if not isinstance(content, (str, bytes)):
        content = _serialize_content(content)
    specs.append({"path": path, "content": content,
                  "encoding": enc_default, "mode": mode_default})
    return specs, None


def _serialize_content(c):
    """Serializza un content non-stringa (lista/record/scalare) in testo
    leggibile (§2.10). Lista di record monocampo → valori; record multi-campo
    o annidati → JSON; scalari → str."""
    import json as _json
    if isinstance(c, list):
        parts = []
        for x in c:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                if len(x) == 1:
                    parts.append(str(next(iter(x.values()))))
                else:
                    parts.append(_json.dumps(x, ensure_ascii=False))
            else:
                parts.append(str(x))
        return "\n".join(parts)
    if isinstance(c, dict):
        return _json.dumps(c, ensure_ascii=False)
    return str(c)


def _write_one(path, content, encoding, mode):
    """Scrive UN file. Ritorna (ok, entry_o_errordict, created_parents).
    Estratto dal write() scalare per riuso nella forma vettoriale."""
    ambig = _check_mutating_path_ambiguity(path, target_must_exist=False)
    if ambig is not None:
        return False, dict(ambig, path=path), []

    abs_path = os.path.abspath(os.path.expanduser(path))
    pre_existed = os.path.exists(abs_path)

    if mode == "fail_if_exists" and pre_existed:
        return False, {"path": abs_path, "ok": False,
                       "error_code": "ERR_DST_EXISTS",
                       "error": _msg("ERR_DST_EXISTS"),
                       "detail": str(abs_path)}, []
    if mode == "skip_if_exists" and pre_existed:
        # Idempotenza deterministica (dedup): file gia' presente → no-op
        # onesto (created=False, skipped=True), non un errore.
        return True, {"path": abs_path, "created": False, "skipped": True,
                      "bytes_written": 0, "encoding": encoding,
                      "mode": mode}, []

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
            return False, {"path": abs_path, "ok": False,
                           "error_code": "ERR_PARENT_MKDIR_FAIL",
                           "error": _msg("ERR_PARENT_MKDIR_FAIL",
                                         path=str(parent), reason=str(e))}, []

    try:
        if encoding == "binary":
            data = base64.b64decode(content)
            with open(abs_path, "ab" if mode == "append" else "wb") as f:
                f.write(data)
            entry = {"path": abs_path, "created": (not pre_existed),
                     "bytes_written": len(data), "encoding": "binary",
                     "mode": mode}
            return True, entry, created_parents
        else:
            with open(abs_path, "a" if mode == "append" else "w",
                      encoding=encoding) as f:
                f.write(content)
            entry = {"path": abs_path, "created": (not pre_existed),
                     "bytes_written": len(content.encode(encoding)),
                     "encoding": encoding, "mode": mode}
            return True, entry, created_parents
    except PermissionError:
        return False, {"path": abs_path, "ok": False,
                       "error_code": "ERR_PERMISSION_DENIED",
                       "error": _msg("ERR_PERMISSION_DENIED"),
                       "detail": f"path outside allowed scope: {abs_path}"}, []
    except IsADirectoryError:
        return False, {"path": abs_path, "ok": False,
                       "error_code": "ERR_PATH_WRONG_TYPE",
                       "error": _msg("ERR_PATH_WRONG_TYPE", expected="file",
                                     actual="directory", path=str(abs_path))}, []
    except OSError as e:
        return False, {"path": abs_path, "ok": False,
                       "error_code": "ERR_OP_FAILED",
                       "error": _msg("ERR_OP_FAILED", reason=f"os error: {e}")}, []
    except Exception as e:
        return False, {"path": abs_path, "ok": False,
                       "error_code": "ERR_OP_FAILED",
                       "error": _msg("ERR_OP_FAILED",
                                     reason=f"unexpected {type(e).__name__}: {e}")}, []


def write(args: dict) -> dict:
    """Scrive contenuto in uno o piu' file. Vettoriale §2.1: la lista ammette
    anche un solo elemento (degenere N=1). Shape input (precedenza):
      - files=[{path, content, encoding?, mode?}, ...]   vettore esplicito;
      - entries=[...] + path_template (+ content_field | content_template |
        content_format)   persiste record piped via from_step come file;
      - paths=[...] + contents=[...]   array paralleli;
      - path + content   scalare (N=1).
    Output (§2.6 trasformativo → results): {ok, ok_count, fail_count,
    created_count, skipped_count, results:[...], dirs_created:[...]}.
    `mode='skip_if_exists'` rende la scrittura idempotente (dedup)."""
    mode_default = args.get("mode", "overwrite")
    if mode_default not in _VALID_WRITE_MODES:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="mode",
                              reason=f"invalid value '{mode_default}'")}

    specs, err = _collect_write_specs(args)
    if err is not None:
        return {"ok": False, "error_code": "ERR_ARG_INVALID", "error": err}
    for sp in specs:
        if sp["mode"] not in _VALID_WRITE_MODES:
            return {"ok": False, "error_code": "ERR_ARG_INVALID",
                    "error": _msg("ERR_ARG_INVALID", arg="mode",
                                  reason=f"invalid value '{sp['mode']}'")}

    # Cap superiore esplicito §2.1/§2.7.
    try:
        max_files = int(args.get("max_files") or _DEFAULT_MAX_FILES)
    except (TypeError, ValueError):
        max_files = _DEFAULT_MAX_FILES
    available_total = len(specs)
    truncated = max_files > 0 and len(specs) > max_files
    if truncated:
        specs = specs[:max_files]

    results: list[dict] = []
    dirs_created: list[str] = []
    ok_count = fail_count = created_count = skipped_count = 0
    for sp in specs:
        ok, entry, parents = _write_one(
            sp["path"], sp["content"], sp["encoding"], sp["mode"])
        results.append(entry)
        if ok:
            ok_count += 1
            if entry.get("skipped"):
                skipped_count += 1
            elif entry.get("created"):
                created_count += 1
            for d in parents:
                if d not in dirs_created:
                    dirs_created.append(d)
        else:
            fail_count += 1

    out = {
        "ok": fail_count == 0,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "created_count": created_count,
        "skipped_count": skipped_count,
        "results": results,
        "dirs_created": dirs_created,
    }
    # §2.8: superficie l'errore al top-level (parità con la forma scalare
    # storica: error_code/error/detail) cosi' la sintesi error-first del
    # runtime e i matcher non perdono il fallimento dentro results[].
    if fail_count:
        first_fail = next((r for r in results if isinstance(r, dict)
                           and r.get("ok") is False), None)
        if first_fail:
            out["error_code"] = first_fail.get("error_code")
            out["error"] = first_fail.get("error")
            if first_fail.get("detail"):
                out["detail"] = first_fail["detail"]
    # §2.7 truncation visibility.
    if truncated:
        out.update({"truncated": True, "truncated_what": "files",
                    "used": len(specs), "available_total": available_total,
                    "cap_field": "max_files", "cap_value": max_files})
    return out


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

    # Robustezza §2.4 (22/5/2026): se patterns non e' specificato, default
    # ["*"] (= "tutti i file"). Caso live turn 7580b454: planner chiama
    # find_files({base_path: ...}) per query "quanti file" senza patterns;
    # fallisce ERR_ARG_MISSING e ripiega su list_dirs (top-level only).
    if not patterns:
        patterns = ["*"]
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
        # Probe esteso (22/5/2026): per query count-style ("quanti file in X")
        # con max_results piccolo (1000) ma corpus grande (es. NAS 33K+),
        # serve un sondaggio profondo per available_total veritiero.
        # Floor 100k per ~secondi su NAS lenti; senza pattern (`*`) il caller
        # vuole comunque un count globale, quindi paghiamo lo scan.
        probe_cap = max(100 * max_results, max_results + 100000)
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

    # D.3: pre-check ambiguita' alias bilingue su tutti src + dst_template
    # parent. Se anche solo UN path e' ambiguo, fail globale (move never
    # implicit delete: meglio non muovere nessuno che muovere quello
    # sbagliato). Skip se dst_template ha placeholder ({...}).
    if "{" not in dst_template:
        ambig_dst = _check_mutating_path_ambiguity(
            dst_template, target_must_exist=False)
        if ambig_dst is not None:
            ambig_dst["error"] = "dst_template ambiguo: " + ambig_dst["error"]
            return ambig_dst
    for i, entry in enumerate(entries[:50]):  # cap pre-check a 50
        if not isinstance(entry, dict):
            continue
        src_arg = entry.get("path") or entry.get("src")
        if not src_arg or not isinstance(src_arg, str):
            continue
        ambig_src = _check_mutating_path_ambiguity(
            src_arg, target_must_exist=True)
        if ambig_src is not None:
            ambig_src["error"] = (
                f"src[{i}] '{src_arg}' ambiguo: " + ambig_src["error"]
            )
            return ambig_src

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

    # La base NON e' una "directory IN base" (e' il contenitore della ricerca):
    # ne misuriamo i file diretti SOLO per l'aggregato file_count_total, ma non
    # entra in `entries`. recursive=false → figli immediati (iterdir);
    # recursive=true → tutti i discendenti (rglob). Bug 31/5/2026: prima la base
    # era l'unica entry con recursive=false → "quante directory in /etc" = 1.
    base_entry = _scan_dir(base)
    base_file_count = int((base_entry or {}).get("file_count", 0) or 0)
    try:
        iterator = base.rglob("*") if recursive else base.iterdir()
        for p in iterator:
            try:
                if p.is_symlink() or not p.is_dir():
                    continue
                rel_parts = p.relative_to(base).parts
            except (ValueError, OSError):
                continue
            if recursive and len(rel_parts) > max_depth:
                continue
            if not include_hidden and any(seg.startswith(".") for seg in rel_parts):
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
    # Aggregati anti-confusione (turn af6447da 22/5/2026): il LLM ha letto
    # `count` di find_dirs come "count file" e ha risposto "858 file" quando
    # erano 858 directories. Aggiungo nomi non ambigui:
    # - count_dirs = numero di directory trovate
    # - file_count_total = somma dei file diretti su tutte le dirs (= numero
    #   totale di file ricorsivo sotto base_path, senza dover lanciare anche
    #   find_files).
    count_dirs = len(entries)
    file_count_total = base_file_count + sum(int(e.get("file_count", 0) or 0) for e in entries)
    out = {
        "ok": True,
        "entries": entries,
        "matches": matches,
        "metadata": {
            "base_path": str(base),
            "recursive": recursive,
            "include_hidden": include_hidden,
            "count": count_dirs,            # legacy, ambiguo
            "count_dirs": count_dirs,        # esplicito
            "file_count_total": file_count_total,
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
        # D.3: ambiguita' alias bilingue su parent → ERR_AMBIGUOUS_PATH.
        ambig = _check_mutating_path_ambiguity(p, target_must_exist=False)
        if ambig is not None:
            failed.append({"index": i, "path": p, **ambig})
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
        _C.PATH_USER_DATA / "_history")
    turn_id = os.environ.get("METNOS_TURN_ID") or "no_turn"
    blob_dir = Path(history_dir) / turn_id / "blob"

    results = []
    failed = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg="path", reason="must be a non-empty string")})
            continue
        # D.3: ambiguita' alias bilingue (delete = target must exist).
        ambig = _check_mutating_path_ambiguity(p, target_must_exist=True)
        if ambig is not None:
            failed.append({"index": i, "path": p, **ambig})
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
        # D.3: ambiguita' alias bilingue (delete_dirs = target must exist).
        # Rischio massimo: cancellare 33578 file NAS quando si voleva 116 in ~/images.
        ambig = _check_mutating_path_ambiguity(p, target_must_exist=True)
        if ambig is not None:
            failed.append({"index": i, "path": p, **ambig})
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


# --- spreadsheet (LOCAL, default client) -----------------------------------
# §10.3 self-hosted default: lo spreadsheet canonico e' un file LOCALE (.xlsx
# via openpyxl, .csv via stdlib), allegabile a un'email. Google Sheets e' il
# backend provider-qualified opt-in (client="google_workspace"). Per il backend
# locale `spreadsheet_id` == il PATH del file (non un Drive id).

def _spreadsheet_out_dir() -> Path:
    """Directory di default per i nuovi spreadsheet locali (auto-creata)."""
    d = _C.PATH_USER_DATA / "spreadsheets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize_filename(title: str) -> str:
    """title → filename safe (no separatori di path, no caratteri riservati)."""
    name = re.sub(r"[^\w\-. ]", "_", (title or "").strip()) or "spreadsheet"
    return name[:120]


def _resolve_xlsx_path(args: dict, *, must_exist: bool):
    """Risolve il path del file spreadsheet locale da `spreadsheet_id`|`path`.

    Per il backend locale `spreadsheet_id` E' il path. `must_exist` distingue
    read/write (richiedono il file) da create (lo genera).
    """
    raw = args.get("spreadsheet_id") or args.get("path")
    if raw is not None and not isinstance(raw, str):
        return None, {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                      "error": _msg("ERR_ARG_NOT_STRING", arg="spreadsheet_id"),
                      "error_class": "invalid_args"}
    raw = (raw or "").strip()
    if not raw:
        return None, {"ok": False, "error_code": "ERR_ARG_MISSING",
                      "error": _msg("ERR_ARG_MISSING", arg="spreadsheet_id"),
                      "error_class": "invalid_args"}
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _spreadsheet_out_dir() / p
    if must_exist and not p.is_file():
        return None, {"ok": False, "error_code": "ERR_PATH_NOT_FOUND",
                      "error": _msg("ERR_PATH_NOT_FOUND", path=str(p)),
                      "error_class": "not_found"}
    return p, None


def _normalize_rows(values) -> list:
    """values → matrice list[list] (tollera lista piatta = 1 colonna)."""
    if not isinstance(values, list):
        return []
    rows = []
    for r in values:
        rows.append(list(r) if isinstance(r, (list, tuple)) else [r])
    return rows


def _is_csv(path: Path) -> bool:
    return path.suffix.lower() == ".csv"


# Sinonimi di campo bilingui per il mapping entries→colonne (§2.10): il planner
# nomina le colonne in linguaggio utente ("descrizione", "percorso") mentre le
# entries hanno chiavi tecniche ("description", "path"). Riusabile/estendibile.
_FIELD_SYNONYMS = {
    "path": ("percorso", "image_path", "filepath", "file", "src"),
    "description": ("descrizione", "desc", "caption"),
    "name": ("nome", "filename", "title", "titolo"),
    "size_bytes": ("size", "dimensione", "bytes"),
    "score": ("punteggio", "rilevanza", "relevance"),
    "keywords": ("parole_chiave", "tags", "tag"),
    "date": ("data", "datetime", "timestamp"),
}


def _entry_cell(entry: dict, col: str):
    """Valore di `entry` per la colonna `col`, risolvendo i sinonimi di campo."""
    if col in entry:
        return entry[col]
    cl = col.strip().lower()
    if cl in entry:
        return entry[cl]
    for canon, syns in _FIELD_SYNONYMS.items():
        names = (canon,) + syns
        if cl in names:
            for n in names:
                if n in entry:
                    v = entry[n]
                    return ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v
    return ""


def _entries_to_values(entries, columns) -> list:
    """Costruisce la matrice [header + righe] da una LISTA di entries (dict) e
    una lista di colonne (nomi di campo). Risolve la frizione list→matrice che
    il planner non sa esprimere coi placeholder (§2.10). Colonne assenti =
    chiavi non-interne della prima entry."""
    rows_in = [e for e in (entries or []) if isinstance(e, dict)]
    cols = [c for c in (columns or []) if isinstance(c, str) and c.strip()]
    if not cols and rows_in:
        cols = [k for k in rows_in[0].keys() if not str(k).startswith("_")]
    if not cols:
        return []
    out = [list(cols)]
    for e in rows_in:
        out.append([_entry_cell(e, c) for c in cols])
    return out


def _resolve_values(args: dict):
    """Risolve la matrice di celle da `values` (diretta) o `entries`+`columns`
    (pipe §2.10/§4.1). Ritorna list[list] o None se nessuna fonte valida."""
    values = args.get("values")
    if isinstance(values, list) and values:
        return _normalize_rows(values)
    entries = args.get("entries")
    if isinstance(entries, list) and entries:
        return _entries_to_values(entries, args.get("columns"))
    if isinstance(values, list):  # lista vuota esplicita = foglio vuoto
        return []
    return None


def create_spreadsheet(args: dict) -> dict:
    """Crea un nuovo spreadsheet LOCALE (.xlsx default, .csv se path .csv).

    Args:
      - `title`: str (richiesto) → nome file (se `path` assente).
      - `values`: list[list] (opzionale) → righe iniziali (es. header + dati).
      - `sheet_name`: str (opzionale, default "Sheet1").
      - `path`: str (opzionale) → path esplicito; altrimenti
        `~/.local/share/metnos/spreadsheets/<title>.xlsx`.
    Output §2.6 trasformativo: `results: [{spreadsheet_id, path, title, rows}]`.
    Reverse: `delete_created_paths`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "results": [], "used": 0, "n_created": 0}
    title = (args.get("title") or "").strip()
    explicit_path = args.get("path") or args.get("spreadsheet_id")
    if not title and not explicit_path:
        # `title` NON è obbligatorio: auto-nome col timestamp. Rationale: create
        # richiedeva title (che il planner doveva INVENTARE) mentre write
        # richiede values (che la query fornisce) → la frizione spingeva il
        # planner verso write per "metti i valori in uno spreadsheet". Senza
        # title forzato, create ha la stessa bassa frizione di write.
        title = "spreadsheet_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if explicit_path:
        path = Path(str(explicit_path)).expanduser()
        if not path.is_absolute():
            path = _spreadsheet_out_dir() / path
    else:
        path = _spreadsheet_out_dir() / f"{_sanitize_filename(title)}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _resolve_values(args) or []
    sheet_name = (args.get("sheet_name") or "Sheet1").strip() or "Sheet1"
    try:
        if _is_csv(path):
            import csv
            with path.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows(rows)
        else:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = sheet_name[:31]  # Excel cap nome foglio
            for r in rows:
                ws.append(r)
            wb.save(str(path))
    except ImportError:
        return {"ok": False, "error_code": "ERR_DEPENDENCY_MISSING",
                "error": _msg("ERR_DEPENDENCY_MISSING", what="openpyxl"),
                "error_class": "dependency_missing", "results": [], "used": 0, "n_created": 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error_code": "ERR_IO",
                "error": _msg("ERR_IO", detail=str(e)),
                "error_class": "io_error", "results": [], "used": 0, "n_created": 0}
    sid = str(path)
    # `created: True` + `path`: contratto richiesto da reverse_patterns.
    # _delete_created_paths (undo: il file appena creato viene rimosso, §2.8).
    result_row = {"ok": True, "created": True, "spreadsheet_id": sid, "path": sid,
                  "title": title or path.stem, "rows": len(rows), "kind": "spreadsheet"}
    return {
        "ok": True, "n_created": 1, "spreadsheet_id": sid, "path": sid,
        "title": title or path.stem, "results": [result_row], "used": 1,
        "files_source": "local",
    }


def write_spreadsheet(args: dict) -> dict:
    """Scrive/appende righe in uno spreadsheet LOCALE esistente.

    Args:
      - `spreadsheet_id`: str (richiesto) = PATH del file locale.
      - `values`: list[list] (richiesto).
      - `mode`: "overwrite" (default, riscrive il foglio) | "append".
      - `sheet_name`: str (opzionale, default primo foglio).
    Output §2.6: `results: [{spreadsheet_id, updated_cells, mode}]`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "results": [], "used": 0, "n_written": 0}
    # Valida gli ARGS prima di toccare il filesystem (no I/O su input malformato).
    # `rows` da `values` (matrice diretta) o da `entries`+`columns` (pipe §2.10):
    # il planner non sa comporre una matrice con placeholder su una LISTA, quindi
    # accettiamo le entries (from_step) + le colonne e costruiamo la matrice qui.
    rows = _resolve_values(args)
    if rows is None:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="values",
                                reason="provide `values` (matrix) or `entries`+`columns`"),
                "error_class": "invalid_args", "results": [], "used": 0, "n_written": 0}
    mode = (args.get("mode") or "overwrite").strip().lower()
    if mode not in ("overwrite", "append"):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="mode", reason="must be 'overwrite' or 'append'"),
                "error_class": "invalid_args", "results": [], "used": 0, "n_written": 0}
    # UPSERT (§2.4 robustezza NL→determinismo): `spreadsheet_id` OPZIONALE per
    # il backend locale. "metti/salva … in uno spreadsheet" presuppone
    # linguisticamente un contenitore esistente; quando NON esiste (nessun id,
    # o un path inesistente) accomodiamo la presupposizione CREANDO il file,
    # invece di fallire o chiedere l'id. Cosi' l'ambiguita' verbale create-vs-
    # write non rompe la pipeline: qualunque verbo scelga il planner, esce un
    # .xlsx locale. Se invece l'id punta a un file esistente, lo MODIFICA.
    raw = args.get("spreadsheet_id") or args.get("path")
    if raw is not None and not isinstance(raw, str):
        return {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                "error": _msg("ERR_ARG_NOT_STRING", arg="spreadsheet_id"),
                "error_class": "invalid_args", "results": [], "used": 0, "n_written": 0}
    raw = (raw or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _spreadsheet_out_dir() / path
    else:
        path = _spreadsheet_out_dir() / (
            "spreadsheet_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.is_file()
    # Undo §2.3: se il file ESISTE gia', backup blob dei bytes previ PRIMA di
    # modificarlo (overwrite o append), cosi' `restore_blob_backup` puo'
    # ripristinare lo stato pre-write. File NUOVO → niente blob (l'undo lo
    # rimuove via `delete_created_paths`). Stessa convenzione di `delete_files`.
    prev_blob_path = None
    if existed:
        try:
            import shutil as _shutil
            import hashlib as _hashlib
            history_dir = os.environ.get("METNOS_HISTORY_DIR") or str(
                _C.PATH_USER_DATA / "_history")
            turn_id = os.environ.get("METNOS_TURN_ID") or "no_turn"
            blob_dir = Path(history_dir) / turn_id / "blob"
            h = _hashlib.sha256()
            with path.open("rb") as _f:
                for chunk in iter(lambda: _f.read(65536), b""):
                    h.update(chunk)
            blob_sha256 = h.hexdigest()
            blob_dir.mkdir(parents=True, exist_ok=True)
            _bp = blob_dir / f"{blob_sha256}.bin"
            if not _bp.exists():
                _shutil.copy2(str(path), str(_bp))
            prev_blob_path = str(_bp)
        except OSError:
            # Backup fallito → onesti (§2.8): non dichiariamo undo per questo file.
            prev_blob_path = None
    sheet_name = (args.get("sheet_name") or "").strip()
    try:
        if _is_csv(path):
            import csv
            file_mode = "a" if (mode == "append" and existed) else "w"
            with path.open(file_mode, newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows(rows)
        else:
            import openpyxl
            if existed:
                wb = openpyxl.load_workbook(str(path))
                ws = (wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames
                      else wb.active)
                if mode == "overwrite":
                    ws.delete_rows(1, ws.max_row)
            else:
                wb = openpyxl.Workbook()
                ws = wb.active
                if sheet_name:
                    ws.title = sheet_name[:31]
            for r in rows:
                ws.append(r)
            wb.save(str(path))
    except ImportError:
        return {"ok": False, "error_code": "ERR_DEPENDENCY_MISSING",
                "error": _msg("ERR_DEPENDENCY_MISSING", what="openpyxl"),
                "error_class": "dependency_missing", "results": [], "used": 0, "n_written": 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error_code": "ERR_IO",
                "error": _msg("ERR_IO", detail=str(e)),
                "error_class": "io_error", "results": [], "used": 0, "n_written": 0}
    sid = str(path)
    updated_cells = sum(len(r) for r in rows)
    created = not existed
    # Schema result §2.6 con campi-undo §2.3 letti dal catalogo:
    #   - file NUOVO → `created=true`+`path` → `delete_created_paths` lo rimuove.
    #   - file PREESISTENTE → `path`+`prev_blob_path` → `restore_blob_backup`
    #     ripristina i bytes previ (annulla overwrite/append).
    result_row = {"ok": True, "spreadsheet_id": sid, "path": sid,
                  "updated_cells": updated_cells, "mode": mode, "created": created}
    if created:
        result_row["created"] = True
    elif prev_blob_path:
        result_row["prev_blob_path"] = prev_blob_path
    out = {
        "ok": True, "n_written": 1, "updated_cells": updated_cells,
        "updated_rows": len(rows), "spreadsheet_id": sid, "path": sid,
        "mode": mode, "created": created,
        "results": [result_row], "used": 1, "files_source": "local",
    }
    # Undo onesto (§2.8): reverse_pattern multistage nel manifest
    # (`restore_blob_backup` + `delete_created_paths`); ogni stadio agisce sul
    # campo che lo riguarda (prev_blob_path vs created), saltando gli altri.
    if created:
        out["_undo"] = {"reverse_pattern": "delete_created_paths", "paths": [sid]}
    elif prev_blob_path:
        out["_undo"] = {"reverse_pattern": "restore_blob_backup",
                        "paths": [sid], "blob_path": prev_blob_path}
    return out


def append_spreadsheet(args: dict) -> dict:
    """Append-only wrapper di `write_spreadsheet` (mode='append')."""
    a = dict(args or {})
    a["mode"] = "append"
    return write_spreadsheet(a)


def read_spreadsheet(args: dict) -> dict:
    """Legge tutte le righe da uno spreadsheet LOCALE.

    Args:
      - `spreadsheet_id`: str (richiesto) = PATH del file locale.
      - `sheet_name`: str (opzionale, default primo foglio).
    Output: `{ok, values: [[...]], entries, spreadsheet_id, used}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    path, err = _resolve_xlsx_path(args, must_exist=True)
    if err is not None:
        return {**err, "entries": [], "used": 0}
    sheet_name = (args.get("sheet_name") or "").strip()
    try:
        if _is_csv(path):
            import csv
            with path.open("r", newline="", encoding="utf-8") as fh:
                values = [list(r) for r in csv.reader(fh)]
        else:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
            values = [list(row) for row in ws.iter_rows(values_only=True)]
    except ImportError:
        return {"ok": False, "error_code": "ERR_DEPENDENCY_MISSING",
                "error": _msg("ERR_DEPENDENCY_MISSING", what="openpyxl"),
                "error_class": "dependency_missing", "entries": [], "used": 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error_code": "ERR_IO",
                "error": _msg("ERR_IO", detail=str(e)),
                "error_class": "io_error", "entries": [], "used": 0}
    sid = str(path)
    return {"ok": True, "values": values, "spreadsheet_id": sid,
            "entries": values, "used": len(values),
            "available_total": len(values), "files_source": "local"}
