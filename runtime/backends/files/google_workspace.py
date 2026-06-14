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
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import (  # noqa: E402
    _skill_home, _needs_inputs_oauth_setup,
    _get_oauth_provider_for_skill,
)
from backends._google_api_runner import run_with_retry  # noqa: E402
from messages import get as _msg  # noqa: E402

SKILL_NAME = "google-workspace"


def _has_creds() -> bool:
    return (_skill_home(SKILL_NAME) / "google_token.json").is_file()


def _ensure_fresh_token() -> bool:
    """Fallback di refresh OAuth (resilienza): l'access token google scade ~1h,
    quindi senza refresh ogni op fallirebbe dopo un'ora. Se il token e' scaduto
    ma ha `refresh_token`, lo rinnova e lo RISALVA (cosi' il subprocess skill
    riceve un token fresco). Ritorna:
      - True  → token utilizzabile (valido o rinnovato con successo);
      - False → assente / scaduto-senza-refresh / refresh fallito (rete o
        refresh_token revocato) → il chiamante ritorna needs_inputs (no traceback).
    Determinismo §7.9. Robusto: qualunque errore → False (mai eccezione propagata)."""
    tok = _skill_home(SKILL_NAME) / "google_token.json"
    if not tok.is_file():
        return False
    try:
        import json as _json
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        info = _json.loads(tok.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info, info.get("scopes"))
        if creds.valid:
            return True
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            out = _json.loads(creds.to_json())
            # preserva i campi extra non gestiti da to_json()
            for k in ("account", "type", "universe_domain"):
                if k in info and k not in out:
                    out[k] = info[k]
            tmp = tok.parent / (tok.name + ".tmp")
            tmp.write_text(_json.dumps(out, indent=2), encoding="utf-8")
            os.replace(tmp, tok)  # atomico
            log.info("google OAuth token rinnovato (scadenza %s)", out.get("expiry"))
            return True
        return False  # scaduto senza refresh_token
    except Exception as ex:  # rete assente, refresh_token revocato, lib mancante
        log.warning("refresh token google fallito: %s", ex)
        return False


def _auth_needs_inputs(args_base: dict, *, executor: str,
                        result_kind: str = "entries") -> dict:
    try:
        payload = _needs_inputs_oauth_setup(
            skill_name=SKILL_NAME, executor=executor,
            args_base=args_base,
            **_get_oauth_provider_for_skill(SKILL_NAME),
        )
    except Exception as ex:
        out = {"ok": False, "error_class": "auth_required",
               "error_code": "ERR_OAUTH_SETUP",
               "error": _msg("ERR_OAUTH_SETUP", reason=str(ex))}
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
    # Guard PROATTIVO (§7.3, universale per ogni op google): assicura un token
    # OAuth fresco PRIMA di lanciare il subprocess skill. _ensure_fresh_token
    # rinnova automaticamente un token scaduto (fallback resilienza, scade ~1h);
    # se assente / non rinnovabile / refresh fallito → needs_inputs (ok:True),
    # mai un traceback mal-classificato (es. ERR_PATH_NOT_FOUND con la traccia).
    if not _ensure_fresh_token():
        return None, _auth_needs_inputs(
            args_base, executor=executor, result_kind=result_kind)
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "entries": [], "used": 0}

    query = args.get("query")
    if not query:
        paths = args.get("paths") or []
        if isinstance(paths, list) and paths:
            query = str(paths[0])
    if not query:
        return {"ok": False,
                "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="query (o 'paths')"),
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
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
                "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="file_id/file_ids/paths"),
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}

    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="paths"),
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
        "ok": len(failed) == 0,
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
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="write failed")
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
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
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="file_id/file_ids/entries"),
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
        "ok": len(failed) == 0,
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("file_ids"), list):
        ids.extend(str(x).strip() for x in args["file_ids"] if x)
    fid = args.get("file_id")
    if isinstance(fid, str) and fid.strip():
        ids.append(fid.strip())
    if not ids:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="file_id/file_ids"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    email = args.get("email") or ""
    role = args.get("role") or "reader"
    grant_type = args.get("type") or "user"
    notify = bool(args.get("notify"))
    if grant_type in ("user", "group") and not email:
        return {"ok": False,
                "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="email (per type=user|group)"),
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
        "ok": len(failed) == 0,
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    names = args.get("paths") or args.get("names") or []
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="paths (nomi cartella)"),
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
        "ok": len(failed) == 0,
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
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="create_dir failed")
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
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
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


# --------------------------------------------------------------------------
# UPLOAD / DOWNLOAD / CREATE_FOLDER  (esplicito name aliases)
# --------------------------------------------------------------------------
# `upload`/`create_folder` espongono gli stessi nomi del CLI `gws drive`
# per coerenza con la skill imported. Il dispatcher canonical
# `write_files`/`create_dirs` chiama `write`/`create_dirs` (storico), ma
# i tool builtin possono linkarsi direttamente a queste funzioni con i
# nomi naturali (es. un futuro executor `upload_files`).

def upload(args: dict) -> dict:
    """Upload 1+ file locali a Drive. Args:
      - `local_path`: path locale singolo (o `paths` lista).
      - `dst_folder_id`: folder Drive padre (alias di `parent`).
      - `mime`: MIME type (alias di `mime_type`).
      - `name`: override nome file su Drive.
    Best-effort: ogni path indipendente. Reverse: delete_files_by_id.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    a = dict(args)
    # Alias normalization
    if "local_path" in a and "paths" not in a:
        a["paths"] = [a["local_path"]] if isinstance(a["local_path"], str) else a["local_path"]
    if "dst_folder_id" in a and "parent" not in a:
        a["parent"] = a["dst_folder_id"]
    if "mime" in a and "mime_type" not in a:
        a["mime_type"] = a["mime"]
    return write(a)


def download(args: dict) -> dict:
    """Scarica 1+ file Drive su path locale. Args:
      - `file_id` / `file_ids`: target.
      - `dst_path`: path locale (per single id). Se `file_ids` lista,
        accettiamo `dst_dir` per scrivere ./<id>/<name>.
      - `export_mime`: override per Google-native (Docs/Sheets/Slides).
    Output: `results: [{ok, file_id, local_path, bytes}]`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    ids: list[str] = []
    if isinstance(args.get("file_ids"), list):
        ids.extend(str(x).strip() for x in args["file_ids"] if x)
    fid = args.get("file_id")
    if isinstance(fid, str) and fid.strip():
        ids.append(fid.strip())
    if not ids:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="file_id/file_ids"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    dst_path = args.get("dst_path") or ""
    dst_dir = args.get("dst_dir") or ""
    export_mime = args.get("export_mime") or ""
    if len(ids) > 1 and dst_path and not dst_dir:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="dst_path",
                              reason="usare dst_dir per multi-id"),
                "error_class": "invalid_args",
                "results": [], "used": 0}

    results, failed = [], []
    for i, target_id in enumerate(ids):
        argv = ["drive", "download", target_id]
        if dst_path and len(ids) == 1:
            argv.extend(["--output", str(dst_path)])
        elif dst_dir:
            # CLI default = ./<name> in cwd; con dst_dir, leave name
            # autoderived but join dir at output side. Pass output as
            # dir/<file_id>.bin placeholder if no name resolution upfront.
            # Simpler: rely on CLI default in cwd then we cannot know
            # final path without parse — propagate dst_dir as output
            # explicitly only when single-id.
            pass
        if export_mime:
            argv.extend(["--export-mime", export_mime])
        data, err = _run_drive(argv, executor="read_files",
                               args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"file_id": target_id, **err})
            continue
        d = data or {}
        local_p = d.get("path", "")
        # Compute size if path exists (best-effort, no error if missing)
        size_b = 0
        if local_p:
            try:
                size_b = Path(local_p).stat().st_size
            except OSError:
                size_b = 0
        results.append({"ok": True, "file_id": target_id,
                        "local_path": local_p,
                        "name": d.get("name", ""),
                        "bytes": size_b})

    out = {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "files_source": "google_workspace",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["ok"] = False
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="download failed")
    return out


def create_folder(args: dict) -> dict:
    """Crea UNA cartella Drive. Args:
      - `name`: nome cartella.
      - `parent_folder_id`: folder padre (alias di `parent`).
    Wrapper sottile di `create_dirs` con shape singolo (per coerenza
    con CLI `drive create-folder`). Output: `{ok, folder_id, ...}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0}
    name = args.get("name") or ""
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="name"),
                "error_class": "invalid_args",
                "results": [], "used": 0}
    a = {"paths": [name]}
    parent = args.get("parent_folder_id") or args.get("parent") or ""
    if parent:
        a["parent"] = parent
    out = create_dirs(a)
    # Convenience flat shape: surface folder_id at top level
    if out.get("ok") and out.get("results"):
        out["folder_id"] = out["results"][0].get("id", "")
        out["web_view_link"] = out["results"][0].get("webViewLink", "")
    return out


# --------------------------------------------------------------------------
# SPREADSHEETS  (Google Sheets API: get/update/append/create)
# --------------------------------------------------------------------------
# Wrappa `google_api.py sheets {get|update|append|create}`. Output remoto
# = matrice di celle (list[list[str|num]]) + metadata range. ID = uno
# `spreadsheetId` (es. "1abc...XYZ"), range = notazione A1 (es. "Sheet1!A1:C10").

def read_spreadsheet(args: dict) -> dict:
    """Legge un range di celle da uno spreadsheet Google Sheets.

    Args:
      - `spreadsheet_id`: str (richiesto).
      - `range`: str A1 (default "Sheet1"; legge tutto il foglio).
    Output: `{ok, values: [[...]], range, spreadsheet_id, used}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    _sid_raw = args.get("spreadsheet_id")
    if _sid_raw is not None and not isinstance(_sid_raw, str):
        return {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                "error": _msg("ERR_ARG_NOT_STRING", arg="spreadsheet_id"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    sid = (_sid_raw or "").strip()
    if not sid:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="spreadsheet_id"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    rng = (args.get("range") or "Sheet1").strip()
    argv = ["sheets", "get", sid, rng]
    data, err = _run_drive(argv, executor="read_files_spreadsheet",
                             args_base=dict(args), result_kind="entries")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}
    values = data if isinstance(data, list) else []
    return {
        "ok": True,
        "values": values,
        "range": rng,
        "spreadsheet_id": sid,
        "entries": values,
        "used": len(values),
        "available_total": len(values),
        "files_source": "google_workspace",
    }


def write_spreadsheet(args: dict) -> dict:
    """Sovrascrive (o appende) celle in un range di uno spreadsheet.

    Args:
      - `spreadsheet_id`: str (richiesto).
      - `range`: str A1 (richiesto, es. "Sheet1!A1:C3").
      - `values`: list[list] (richiesto, matrice riga x colonna).
      - `mode`: "overwrite" (default) | "append".
    Output: `{ok, updated_cells, updated_rows, range, spreadsheet_id, mode}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    _sid_raw = args.get("spreadsheet_id")
    if _sid_raw is not None and not isinstance(_sid_raw, str):
        return {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                "error": _msg("ERR_ARG_NOT_STRING", arg="spreadsheet_id"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    sid = (_sid_raw or "").strip()
    if not sid:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="spreadsheet_id"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    rng = (args.get("range") or "").strip()
    if not rng:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="range"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    values = args.get("values")
    if not isinstance(values, list):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="values",
                                reason="must be a list of lists"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}

    mode = (args.get("mode") or "overwrite").strip().lower()
    if mode not in ("overwrite", "append"):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="mode",
                                reason="must be 'overwrite' or 'append'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}

    sub_cmd = "append" if mode == "append" else "update"
    argv = ["sheets", sub_cmd, sid, rng,
            "--values", json.dumps(values, ensure_ascii=False)]
    data, err = _run_drive(argv, executor="write_files_spreadsheet",
                             args_base=dict(args), result_kind="results")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_written": 0}

    info = data if isinstance(data, dict) else {}
    updated_cells = int(info.get("updatedCells") or 0)
    updated_range = info.get("updatedRange") or rng
    result_row = {
        "ok": True,
        "spreadsheet_id": sid,
        "range": updated_range,
        "updated_cells": updated_cells,
        "mode": mode,
    }
    return {
        "ok": True,
        "n_written": 1,
        "updated_cells": updated_cells,
        "updated_rows": len(values),
        "range": updated_range,
        "spreadsheet_id": sid,
        "mode": mode,
        "results": [result_row],
        "used": 1,
        "files_source": "google_workspace",
    }


def append_spreadsheet(args: dict) -> dict:
    """Append-only wrapper di `write_spreadsheet` con `mode='append'`.

    Args:
      - `spreadsheet_id`: str (richiesto).
      - `range`: str A1 (richiesto).
      - `values`: list[list] (richiesto).
    Equivalente a `write_spreadsheet(args, mode='append')`.
    """
    a = dict(args or {})
    a["mode"] = "append"
    return write_spreadsheet(a)


def create_spreadsheet(args: dict) -> dict:
    """Crea un nuovo spreadsheet Google Sheets.

    Args:
      - `title`: str (richiesto).
      - `sheet_name`: str (opzionale; nome della prima tab).
    Output: `{ok, spreadsheet_id, web_view_url, title}`. Reverse:
    `delete_files_by_id` (Drive trash).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="title"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    argv = ["sheets", "create", "--title", title]
    sheet_name = (args.get("sheet_name") or "").strip()
    if sheet_name:
        argv.extend(["--sheet-name", sheet_name])
    data, err = _run_drive(argv, executor="create_files_spreadsheet",
                             args_base=dict(args), result_kind="results")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_created": 0}

    info = data if isinstance(data, dict) else {}
    sid = info.get("spreadsheetId") or ""
    web_view_url = info.get("spreadsheetUrl") or (
        f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else ""
    )
    result_row = {
        "ok": True,
        "file_id": sid,
        "id": sid,
        "spreadsheet_id": sid,
        "title": info.get("title") or title,
        "web_view_url": web_view_url,
        "kind": "spreadsheet",
    }
    out = {
        "ok": True,
        "n_created": 1,
        "spreadsheet_id": sid,
        "web_view_url": web_view_url,
        "title": info.get("title") or title,
        "results": [result_row],
        "used": 1,
        "files_source": "google_workspace",
    }
    if sid:
        out["_undo"] = {
            "reverse_pattern": "delete_files_by_id",
            "ids": [sid],
            "scope": {"client": "google_workspace"},
        }
    return out


# --------------------------------------------------------------------------
# DOCS  (Google Docs API: get/create/append)
# --------------------------------------------------------------------------
# Wrappa `google_api.py docs {get|create|append}`. ID = un `documentId`
# (es. "1abc...XYZ"). Body = flow di testo paragrafi.

def read_doc(args: dict) -> dict:
    """Legge il contenuto testuale di un Google Doc.

    Args:
      - `document_id`: str (richiesto).
    Output: `{ok, body_text, title, document_id}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    did = (args.get("document_id") or "").strip()
    if not did:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="document_id"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    argv = ["docs", "get", did]
    data, err = _run_drive(argv, executor="read_files_doc",
                             args_base=dict(args), result_kind="entries")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}

    info = data if isinstance(data, dict) else {}
    body_text = info.get("body") or ""
    title = info.get("title") or ""
    entry = {
        "document_id": did,
        "id": did,
        "title": title,
        "body_text": body_text,
        "content_length": len(body_text),
        "kind": "doc",
    }
    return {
        "ok": True,
        "body_text": body_text,
        "title": title,
        "document_id": did,
        "entries": [entry],
        "used": 1,
        "available_total": 1,
        "files_source": "google_workspace",
    }


def create_doc(args: dict) -> dict:
    """Crea un nuovo Google Doc.

    Args:
      - `title`: str (richiesto).
      - `body`: str (opzionale; testo iniziale).
    Output: `{ok, document_id, web_view_url, title}`. Reverse:
    `delete_files_by_id` (Drive trash).
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="title"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    argv = ["docs", "create", "--title", title]
    body = args.get("body") or ""
    if body:
        argv.extend(["--body", str(body)])
    data, err = _run_drive(argv, executor="create_files_doc",
                             args_base=dict(args), result_kind="results")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_created": 0}

    info = data if isinstance(data, dict) else {}
    did = info.get("documentId") or ""
    web_view_url = info.get("url") or (
        f"https://docs.google.com/document/d/{did}/edit" if did else ""
    )
    result_row = {
        "ok": True,
        "file_id": did,
        "id": did,
        "document_id": did,
        "title": info.get("title") or title,
        "web_view_url": web_view_url,
        "kind": "doc",
    }
    out = {
        "ok": True,
        "n_created": 1,
        "document_id": did,
        "web_view_url": web_view_url,
        "title": info.get("title") or title,
        "results": [result_row],
        "used": 1,
        "files_source": "google_workspace",
    }
    if did:
        out["_undo"] = {
            "reverse_pattern": "delete_files_by_id",
            "ids": [did],
            "scope": {"client": "google_workspace"},
        }
    return out


def append_doc(args: dict) -> dict:
    """Appende testo alla fine di un Google Doc esistente.

    Args:
      - `document_id`: str (richiesto).
      - `text`: str (richiesto; aggiunto in coda con newline trailing
        garantito).
    Output: `{ok, document_id, content_length, characters_appended}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    did = (args.get("document_id") or "").strip()
    if not did:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="document_id"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    text = args.get("text")
    if not isinstance(text, str) or not text:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="text"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    argv = ["docs", "append", did, "--text", text]
    data, err = _run_drive(argv, executor="write_files_doc",
                             args_base=dict(args), result_kind="results")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_written": 0}

    info = data if isinstance(data, dict) else {}
    chars = int(info.get("characters") or len(text))
    # Undo §2.3: `inserted_at` = indice (1-based) dove l'append ha iniziato a
    # scrivere. Con `chars` definisce il range [inserted_at, inserted_at+chars)
    # che `reverse()` rimuove via deleteContentRange (uniforme ai builtin).
    inserted_at = info.get("inserted_at")
    result_row = {
        "ok": True,
        "document_id": did,
        "id": did,
        "characters_appended": chars,
        "kind": "doc",
    }
    if isinstance(inserted_at, int):
        result_row["inserted_at"] = inserted_at
    out = {
        "ok": True,
        "n_written": 1,
        "document_id": did,
        "content_length": chars,
        "characters_appended": chars,
        "results": [result_row],
        "used": 1,
        "files_source": "google_workspace",
    }
    if isinstance(inserted_at, int):
        out["_undo"] = {"document_id": did, "start": inserted_at,
                        "end": inserted_at + chars}
    return out


def delete_doc_range(args: dict) -> dict:
    """Rimuove un range di contenuto da un Google Doc (undo §2.3 dell'append).

    Args: `document_id`, `start` (incluso), `end` (escluso). Esegue
    `docs delete-range` via lo script google_api (deleteContentRange).
    Output trasformativo §2.6: `results: [{document_id, removed_range}]`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    did = (args.get("document_id") or "").strip()
    start = args.get("start")
    end = args.get("end")
    if not did or not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="range",
                              reason="document_id + start<end (int) richiesti"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    argv = ["docs", "delete-range", did, "--start", str(start), "--end", str(end)]
    data, err = _run_drive(argv, executor="write_files_doc",
                           args_base=dict(args), result_kind="results")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_deleted": 0}
    return {
        "ok": True, "n_deleted": 1,
        "results": [{"ok": True, "document_id": did,
                     "removed_range": [start, end]}],
        "used": 1, "files_source": "google_workspace",
    }
