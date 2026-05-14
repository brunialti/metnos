"""runtime/backends/files/google_workspace.py — Drive backend.

Wrappa `~/.local/share/metnos/skills/google-workspace/scripts/google_api.py`
sub-commands `drive search | get | upload | download | create-folder |
share | delete`.

Mapping verb canonical Metnos → Drive sub-command:
- find_files  → drive search (vettoriale, paths→query)
- read_files  → drive get + drive download (metadata + body)
- write_files → drive upload (paths locali → Drive)
- delete_files → drive delete (trash reversibile o permanent)
- share_files → drive share (ACL grant — ADR 0128)
- create_dirs → drive create-folder (handled in dirs/google_workspace.py)

Identificatori Drive: `file_id` (es. "1abc...XYZ"). Per coerenza con
local fs, accettiamo `paths` come query string (es. nome file, MIME)
nel find e come local source nel write. `find_files_*` ritorna entries
con `id`, `name`, `mimeType`, `size`, `modifiedTime`, `webViewLink`.

auth_required → `decision="needs_inputs"` con OAuth setup (uguale a
gmail/calendar google_workspace backend).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import (  # noqa: E402
    _skill_home, _needs_inputs_oauth_setup, _get_skill_oauth_config,
)
from backends._google_api_runner import run_with_retry  # noqa: E402

SKILL_NAME = "google-workspace"


def _has_creds() -> bool:
    return (_skill_home(SKILL_NAME) / "google_token.json").is_file()


def _auth_needs_inputs(args_base: dict, *, executor: str,
                        result_kind: str = "entries") -> dict:
    try:
        payload = _needs_inputs_oauth_setup(
            skill_name=SKILL_NAME, executor=executor,
            args_base=args_base,
            **_get_skill_oauth_config(__file__),
        )
    except Exception as ex:
        out = {"ok": False, "error_class": "auth_required",
               "error": f"OAuth setup payload fallito: {ex}"}
        if result_kind == "entries":
            out["entries"] = []; out["used"] = 0
        else:
            out["results"] = []; out["used"] = 0
        return out
    out = {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": payload,
        "error_class": "auth_required",
        "final_message_hint": payload.get("title", ""),
    }
    if result_kind == "entries":
        out["entries"] = []; out["used"] = 0
    else:
        out["results"] = []; out["used"] = 0
    return out


def _run_drive(argv: list[str], *, executor: str, args_base: dict,
               result_kind: str = "entries"
               ) -> tuple[dict | list | None, dict | None]:
    """Thin wrapper su `run_with_retry` per CLI `google_api.py drive ...`.
    `result_kind` propagato all'`_auth_needs_inputs` per shape return
    (entries vs results) coerente con il verb canonical."""
    return run_with_retry(
        argv, executor=executor, args_base=args_base,
        auth_handler=lambda ab: _auth_needs_inputs(
            ab, executor=executor, result_kind=result_kind),
    )


# --------------------------------------------------------------------------
# FIND  (search)
# --------------------------------------------------------------------------

def find(args: dict) -> dict:
    """Cerca file su Drive. Args:
      - `query`: nome file o full-text (es. 'budget report').
      - `raw_query`: bool — se True, la query e' raw Drive API
        (es. "mimeType='application/pdf' and modifiedTime > '2026-01-01'").
      - `max_results`: cap (default 10).
      - `paths` (back-compat con find_files local): se presente e
        `query` mancante, prendiamo il primo elemento come query.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args", "entries": [], "used": 0}

    query = args.get("query")
    if not query:
        paths = args.get("paths") or []
        if isinstance(paths, list) and paths:
            query = str(paths[0])
    if not query:
        return {"ok": False,
                "error": "missing 'query' (o 'paths' come fallback)",
                "error_class": "invalid_args",
                "entries": [], "used": 0}

    max_results = int(args.get("max_results") or 10)
    raw = bool(args.get("raw_query"))
    argv = ["drive", "search", str(query), "--max", str(max_results)]
    if raw:
        argv.append("--raw-query")

    data, err = _run_drive(argv, executor="find_files",
                            args_base=dict(args), result_kind="entries")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}

    entries = data if isinstance(data, list) else []
    return {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "available_total": len(entries),
        "files_source": "google_workspace",
    }


# --------------------------------------------------------------------------
# READ  (get metadata; download solo se richiesto)
# --------------------------------------------------------------------------

def read(args: dict) -> dict:
    """Legge metadata di 1+ file Drive per id (vettoriale §2.1)."""
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args", "entries": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("file_ids"), list):
        ids.extend(str(x).strip() for x in args["file_ids"] if x)
    fid = args.get("file_id")
    if isinstance(fid, str) and fid.strip():
        ids.append(fid.strip())
    if not ids:
        # Back-compat: paths come list di file_id
        for p in (args.get("paths") or []):
            if isinstance(p, str) and p.strip():
                ids.append(p.strip())
    if not ids:
        return {"ok": False,
                "error": "missing 'file_id' / 'file_ids' / 'paths'",
                "error_class": "invalid_args",
                "entries": [], "used": 0}

    entries: list[dict] = []
    for fid in ids:
        data, err = _run_drive(["drive", "get", fid], executor="read_files",
                                 args_base=dict(args), result_kind="entries")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            entries.append({"id": fid,
                             "error_class": err.get("error_class"),
                             "error": err.get("error")})
            continue
        if isinstance(data, dict):
            data.setdefault("id", fid)
            entries.append(data)

    return {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "available_total": len(entries),
        "files_source": "google_workspace",
    }


# --------------------------------------------------------------------------
# WRITE  (upload local paths to Drive)
# --------------------------------------------------------------------------

def write(args: dict) -> dict:
    """Upload 1+ file locali a Drive. Args:
      - `paths`: list[str] (local paths).
      - `parent`: str (folder id Drive, opzionale).
      - `mime_type`: str (override, opzionale, applicato a tutti).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}

    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": "missing 'paths' (list)",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}

    parent = args.get("parent") or ""
    mime = args.get("mime_type") or ""

    results, failed = [], []
    for p in paths:
        argv = ["drive", "upload", str(p)]
        if args.get("name"):
            argv.extend(["--name", str(args["name"])])
        if parent:
            argv.extend(["--parent", parent])
        if mime:
            argv.extend(["--mime-type", mime])
        data, err = _run_drive(argv, executor="write_files",
                                 args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"path": p, **err})
            continue
        results.append({"ok": True, "path": p,
                         "id": (data or {}).get("id", ""),
                         "name": (data or {}).get("name", ""),
                         "webViewLink": (data or {}).get("webViewLink", "")})

    out = {
        "ok": len(results) > 0 or not failed,
        "n_written": len(results),
        "results": results,
        "used": len(results),
        "files_source": "google_workspace",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or "write failed"
    if results:
        out["_undo"] = {
            "reverse_pattern": "delete_files_by_id",
            "ids": [r["id"] for r in results if r.get("id")],
            "scope": {"client": "google_workspace"},
        }
    return out


# --------------------------------------------------------------------------
# DELETE  (trash o permanent)
# --------------------------------------------------------------------------

def delete(args: dict) -> dict:
    """Cancella 1+ file Drive. Default `trash` (reversibile);
    `permanent: true` per cancellazione definitiva."""
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}

    ids: list[str] = []
    if isinstance(args.get("file_ids"), list):
        ids.extend(str(x).strip() for x in args["file_ids"] if x)
    fid = args.get("file_id")
    if isinstance(fid, str) and fid.strip():
        ids.append(fid.strip())
    entries = args.get("entries") or []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                v = e.get("id") or e.get("uid")
                if isinstance(v, str) and v.strip():
                    ids.append(v.strip())
    if not ids:
        return {"ok": False, "error": "missing 'file_id' / 'file_ids' / 'entries'",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}

    permanent = bool(args.get("permanent"))
    results, failed = [], []
    for fid in ids:
        argv = ["drive", "delete", fid]
        if permanent:
            argv.append("--permanent")
        _, err = _run_drive(argv, executor="delete_files",
                              args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": fid, **err})
            continue
        results.append({"ok": True, "id": fid,
                         "status": "permanently_deleted" if permanent else "trashed"})

    return {
        "ok": len(results) > 0 or not failed,
        "n_deleted": len(results),
        "results": results,
        "failed": failed,
        "used": len(results),
        "files_source": "google_workspace",
    }


# --------------------------------------------------------------------------
# SHARE  (ACL grant — ADR 0128)
# --------------------------------------------------------------------------

def share(args: dict) -> dict:
    """Grant ACL su 1+ file Drive. Args:
      - `file_id` / `file_ids`: target.
      - `email`: destinatario (per `type=user|group`).
      - `role`: 'reader' (default) | 'commenter' | 'writer' | ...
      - `type`: 'user' (default) | 'group' | 'domain' | 'anyone'.
      - `notify`: bool (default False).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("file_ids"), list):
        ids.extend(str(x).strip() for x in args["file_ids"] if x)
    fid = args.get("file_id")
    if isinstance(fid, str) and fid.strip():
        ids.append(fid.strip())
    if not ids:
        return {"ok": False, "error": "missing 'file_id' / 'file_ids'",
                "error_class": "invalid_args",
                "results": [], "used": 0}

    email = args.get("email") or ""
    role = args.get("role") or "reader"
    grant_type = args.get("type") or "user"
    notify = bool(args.get("notify"))
    if grant_type in ("user", "group") and not email:
        return {"ok": False,
                "error": "missing 'email' for type=user|group",
                "error_class": "invalid_args",
                "results": [], "used": 0}

    results, failed = [], []
    for fid in ids:
        argv = ["drive", "share", fid, "--role", role,
                "--type", grant_type]
        if email:
            argv.extend(["--email", email])
        if notify:
            argv.append("--notify")
        data, err = _run_drive(argv, executor="share_files",
                                 args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": fid, **err})
            continue
        results.append({"ok": True, "id": fid, "role": role,
                         "type": grant_type, "email": email,
                         "permission_id": (data or {}).get("permissionId", "")})

    return {
        "ok": len(results) > 0 or not failed,
        "n_shared": len(results),
        "results": results,
        "failed": failed,
        "used": len(results),
        "files_source": "google_workspace",
    }


# --------------------------------------------------------------------------
# DIRS  (Drive folders: mimeType='application/vnd.google-apps.folder')
# --------------------------------------------------------------------------
# Le dirs su Drive sono normali file con MIME folder. I dispatcher
# canonical `create_dirs.py`/`find_dirs.py`/`delete_dirs.py` chiamano
# queste funzioni (vedi `backends.files.local` per il pattern).

_FOLDER_MIME = "application/vnd.google-apps.folder"


def create_dirs(args: dict) -> dict:
    """Crea 1+ cartella su Drive. Args:
      - `paths`: list[str] (nomi cartella).
      - `parent`: folder_id del padre (opzionale).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    names = args.get("paths") or args.get("names") or []
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return {"ok": False, "error": "missing 'paths' (list di nomi cartella)",
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    parent = args.get("parent") or ""

    results, failed = [], []
    for n in names:
        argv = ["drive", "create-folder", str(n)]
        if parent:
            argv.extend(["--parent", parent])
        data, err = _run_drive(argv, executor="create_dirs",
                                 args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"name": n, **err})
            continue
        results.append({"ok": True, "name": n,
                         "id": (data or {}).get("id", ""),
                         "webViewLink": (data or {}).get("webViewLink", "")})

    out = {
        "ok": len(results) > 0 or not failed,
        "n_created": len(results),
        "results": results,
        "used": len(results),
        "files_source": "google_workspace",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or "create_dir failed"
    if results:
        out["_undo"] = {
            "reverse_pattern": "delete_files_by_id",
            "ids": [r["id"] for r in results if r.get("id")],
            "scope": {"client": "google_workspace"},
        }
    return out


def find_dirs(args: dict) -> dict:
    """Cerca cartelle su Drive (mimeType=folder)."""
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args", "entries": [], "used": 0}
    query = args.get("query") or ""
    name_match = args.get("name") or query
    raw_query = f"mimeType='{_FOLDER_MIME}'"
    if name_match:
        # Escape singolari per non rompere la query API
        safe = str(name_match).replace("'", "\\'")
        raw_query += f" and name contains '{safe}'"
    max_results = int(args.get("max_results") or 25)
    argv = ["drive", "search", raw_query, "--max", str(max_results),
            "--raw-query"]
    data, err = _run_drive(argv, executor="find_dirs",
                             args_base=dict(args), result_kind="entries")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}
    entries = data if isinstance(data, list) else []
    return {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "available_total": len(entries),
        "files_source": "google_workspace",
    }


def delete_dirs(args: dict) -> dict:
    """Cancella 1+ cartelle (=file folder mime) su Drive.
    Alias di `delete(args)` (Drive cancella file e folders allo stesso modo)."""
    return delete(args)
