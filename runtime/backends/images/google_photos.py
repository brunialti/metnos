"""runtime/backends/images/google_photos.py — backend Google Photos (Library API).

Wrappa `google_api.py photos {upload|album-create|album-list|search|download}`
via il runner condiviso `run_with_retry` + il prologo OAuth
`_google_auth_common`. Semantica post 31/3/2025: SOLO contenuti creati dall'app
(`*.appcreateddata`) — la libreria intera arriva da Takeout (spec §4, P2).

Verbi canonical Metnos → backend:
- write_images_google_photos → upload   (paths locali → Photos, album opz.)
- find_images_google_photos  → find     (year/album opz.; albums=true → list_albums)
- get_images_google_photos   → download (media_item_ids → file locali)

L'upload NON e' reversibile (l'API non permette delete di mediaItems):
niente `_undo`, `revertible=false` a livello executor. Il download SI'
(`delete_created_paths`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from backends._google_api_runner import run_with_retry  # noqa: E402
from backends._google_auth_common import (  # noqa: E402
    has_creds as _has_creds,
    ensure_fresh_token as _ensure_fresh_token,
    auth_needs_inputs as _auth_needs_inputs,
)
from executor_helpers import IMAGE_EXTS  # noqa: E402
from messages import get as _msg  # noqa: E402
import config as C  # noqa: E402

# Workspace foto default (memoria feedback_default_photo_workspace).
_DEFAULT_DST_DIR = C.PATH_USER_DATA / "Immagini" / "google-photos"


def _run_photos(argv: list[str], *, executor: str, args_base: dict,
                result_kind: str = "results"
                ) -> tuple[dict | list | None, dict | None]:
    """Runner CLI `photos ...`: token fresco PROATTIVO (§7.3) → needs_inputs se
    assente/non-rinnovabile; altrimenti run_with_retry con auth_handler."""
    if not _ensure_fresh_token():
        return None, _auth_needs_inputs(
            args_base, executor=executor, result_kind=result_kind)
    return run_with_retry(
        argv, executor=executor, args_base=args_base,
        auth_handler=lambda ab: _auth_needs_inputs(
            ab, executor=executor, result_kind=result_kind),
    )


def _paths_from_args(args: dict) -> list[str]:
    """Path locali da caricare: `paths` (str o list) + `entries[*].path` (piping
    from_step). Dedup preservando l'ordine (§2.1)."""
    out: list[str] = []
    raw = args.get("paths")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        out.extend(str(p).strip() for p in raw
                   if isinstance(p, str) and p.strip())
    for e in (args.get("entries") or []):
        if isinstance(e, dict):
            p = e.get("path") or e.get("local_path") or e.get("file_path")
            if isinstance(p, str) and p.strip():
                out.append(p.strip())
    return list(dict.fromkeys(out))


def _ids_from_args(args: dict) -> list[str]:
    """media_item_id da caricare/scaricare: `ids`/`media_item_ids` + `entries[*].id`."""
    out: list[str] = []
    for key in ("ids", "media_item_ids"):
        raw = args.get(key)
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            out.extend(str(x).strip() for x in raw
                       if isinstance(x, str) and x.strip())
    for e in (args.get("entries") or []):
        if isinstance(e, dict):
            i = e.get("id") or e.get("media_item_id")
            if isinstance(i, str) and i.strip():
                out.append(i.strip())
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------
# ALBUMS
# --------------------------------------------------------------------------

def list_albums(args: dict) -> dict:
    """Elenca gli album app-created. LIMITE API: NON la lista completa
    dell'utente (post 31/3/2025) — quella arriva da Takeout (spec §4.3-bis)."""
    if not isinstance(args, dict):
        args = {}
    data, err = _run_photos(["photos", "album-list"],
                            executor="find_images_google_photos",
                            args_base=dict(args), result_kind="entries")
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}
    albums = data if isinstance(data, list) else []
    return {
        "ok": True,
        "entries": albums,
        "used": len(albums),
        "available_total": len(albums),
        "images_source": "google_photos",
        "albums_app_created_only": True,
        # Perimetro DICHIARATO all'utente (§2.8, turn e2b0e529): l'API vede
        # SOLO l'app-created — né gli album posseduti né i condivisi.
        "message": _msg("MSG_GPHOTOS_APP_CREATED_ONLY"),
    }


def _match_album_id(album_name: str, args: dict) -> tuple[str, dict | None]:
    """ID di un album app-created per NOME (esatto, case-insensitive). Ritorna
    ("", None) se non esiste (nessun errore: il chiamante decide). Propaga
    needs_inputs / errori come secondo elemento."""
    lst = list_albums(args)
    if lst.get("decision") == "needs_inputs":
        return "", lst
    if not lst.get("ok"):
        return "", lst
    target = album_name.strip().casefold()
    for a in lst.get("entries", []):
        if str(a.get("title", "")).strip().casefold() == target:
            return str(a.get("id", "")), None
    return "", None


def _resolve_or_create_album(album_name: str, args: dict) -> tuple[str, dict | None]:
    """Come `_match_album_id` ma CREA l'album se non esiste (per l'upload)."""
    album_id, err = _match_album_id(album_name, args)
    if err is not None:
        return "", err
    if album_id:
        return album_id, None
    data, err = _run_photos(["photos", "album-create", album_name],
                            executor="write_images_google_photos",
                            args_base=dict(args), result_kind="results")
    if err is not None:
        return "", err
    return str((data or {}).get("id", "")), None


# --------------------------------------------------------------------------
# WRITE  (upload)
# --------------------------------------------------------------------------

def upload(args: dict) -> dict:
    """Carica 1+ foto su Google Photos. Args: `paths`|`entries` (from_step),
    `album` (nome, creato se manca), `max_total` (cap, default 200). L'upload
    e' IRREVERSIBILE (l'API non permette delete): nessun `_undo`."""
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "ok_count": 0}

    paths = _paths_from_args(args)
    if not paths:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="paths"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "ok_count": 0}

    # §2.4 (confine NL): «carica le foto della cartella X» arriva con la DIR
    # in paths — espandi ai file immagine contenuti (non ricorsivo, ordinato).
    # Dir senza immagini → fallimento onesto per-path, gli altri proseguono.
    expanded: list[str] = []
    dir_misses: list[dict] = []
    for p in paths:
        pp = Path(p).expanduser()
        if pp.is_dir():
            imgs = sorted(str(f) for f in pp.iterdir()
                          if f.is_file() and f.suffix.lower() in IMAGE_EXTS)
            if imgs:
                expanded.extend(imgs)
            else:
                dir_misses.append({"path": p, "ok": False,
                                   "error_class": "not_found",
                                   "error_code": "ERR_PATH_NOT_FOUND",
                                   "error": _msg("ERR_PATH_NOT_FOUND", path=p)})
        else:
            expanded.append(p)
    paths = list(dict.fromkeys(expanded))

    max_total = int(args.get("max_total") or 200)
    selected = paths[:max_total] if max_total > 0 else paths
    truncated = len(paths) > len(selected)

    album_name = (args.get("album") or "").strip()
    album_id = ""
    if album_name:
        album_id, err = _resolve_or_create_album(album_name, args)
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "results": [], "used": 0, "ok_count": 0}

    if not paths and dir_misses:
        # SOLO cartelle vuote/senza immagini: esito onesto §2.8, niente upload.
        return {"ok": False, "ok_count": 0, "fail_count": len(dir_misses),
                "results": [], "failed": dir_misses, "used": 0,
                "images_source": "google_photos", "revertible": False,
                "error_class": "not_found",
                "error": dir_misses[0]["error"]}

    # FASE 1 — bytes per file → uploadToken (fallimento per-file NON ferma gli
    # altri §2.1; token da usare subito, il batchCreate segue nello stesso invoke).
    staged, failed = [], list(dir_misses)
    for p in selected:
        data, err = _run_photos(["photos", "upload-bytes", str(p)],
                                executor="write_images_google_photos",
                                args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"path": p, "ok": False, **err})
            continue
        d = data or {}
        token = d.get("uploadToken", "")
        if not token:
            failed.append({"path": p, "ok": False, "error_class": "server_error",
                           "error": _msg("ERR_GPHOTOS_UPLOAD", name=p,
                                         reason="empty uploadToken")})
            continue
        staged.append({"path": p, "uploadToken": token,
                       "fileName": d.get("fileName") or Path(p).name})

    # FASE 2 — mediaItems:batchCreate a CHUNK di 50 (contratto API, spec
    # §3.2/§3.3: chunking nel backend, non nel CLI). Esito per-item onesto.
    results = []
    for i in range(0, len(staged), 50):
        chunk = staged[i:i + 50]
        items = [{"uploadToken": s["uploadToken"], "fileName": s["fileName"]}
                 for s in chunk]
        argv = ["photos", "batch-create",
                "--items", json.dumps(items, ensure_ascii=False)]
        if album_id:
            argv.extend(["--album-id", album_id])
        data, err = _run_photos(argv, executor="write_images_google_photos",
                                args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.extend({"path": s["path"], "ok": False, **err}
                          for s in chunk)
            continue
        rows = (data or {}).get("results") or []
        for j, s in enumerate(chunk):
            r = rows[j] if j < len(rows) else {}
            if r.get("ok"):
                results.append({"ok": True, "path": s["path"],
                                "media_item_id": r.get("media_item_id", ""),
                                "filename": r.get("filename", ""),
                                "album": album_name})
            else:
                failed.append({"path": s["path"], "ok": False,
                               "error_class": "server_error",
                               "error": _msg("ERR_GPHOTOS_UPLOAD",
                                             name=s["path"],
                                             reason=r.get("status_message")
                                             or "batchCreate item failed")})

    out = {
        "ok": len(failed) == 0 and len(results) > 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "images_source": "google_photos",
        "revertible": False,
    }
    if results:
        # Riepilogo + nota IRREVERSIBILE (spec §3.6): una volta PER STEP di
        # upload, nel result `message` — mai ripetuta per-file. L'executor e'
        # stateless (§ sandbox): lo stato-turno non esiste qui; un turno tipico
        # ha UN solo step di upload, quindi per-step ≡ per-turno.
        out["message"] = (_msg("MSG_GPHOTOS_UPLOADED", n=len(results))
                          + " " + _msg("MSG_GPHOTOS_IRREVERSIBLE"))
    if failed:
        out["failed"] = failed
        if not results:
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = _msg("ERR_GPHOTOS_UPLOAD",
                                name=failed[0].get("path", ""),
                                reason=failed[0].get("error", ""))
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "paths"
        out["truncated_intentional"] = True
        out["cap_field"] = "max_total"
        out["cap_value"] = max_total
        out["available_total"] = len(paths)
    return out


# --------------------------------------------------------------------------
# FIND  (search app-created)
# --------------------------------------------------------------------------

def find(args: dict) -> dict:
    """Cerca fra le foto caricate da Metnos. Args: `year` (int opz.),
    `album` (nome opz.), `albums` (bool → elenca gli album), `max_results`
    (cap, default 100). SOLO contenuti app-created (limite API)."""
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "entries": [], "used": 0}

    if args.get("albums"):
        return list_albums(args)

    max_results = int(args.get("max_results") or 100)
    album_name = (args.get("album") or "").strip()
    year = args.get("year")

    album_id = ""
    if album_name:
        album_id, err = _match_album_id(album_name, args)
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "entries": [], "used": 0}
        if not album_id:
            # Album app-created inesistente → risultato vuoto onesto §2.8
            # (find = ricerca: nessun match = lista vuota, come Drive).
            return {"ok": True, "entries": [], "used": 0,
                    "available_total": 0, "images_source": "google_photos"}

    entries: list[dict] = []
    page_token = ""
    more_available = False
    # pageSize COSTANTE su TUTTE le pagine: con un pageToken l'API esige gli
    # stessi parametri della richiesta precedente (HTTP 400 «must use the same
    # parameters», visto live 10/7). Cap applicato client-side a valle.
    while True:
        argv = ["photos", "search", "--max", "100"]
        if album_id:
            argv.extend(["--album-id", album_id])
        elif year:
            argv.extend(["--year", str(int(year))])
        if page_token:
            argv.extend(["--page-token", page_token])
        data, err = _run_photos(argv, executor="find_images_google_photos",
                                args_base=dict(args), result_kind="entries")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "entries": [], "used": 0}
        d = data if isinstance(data, dict) else {}
        for m in d.get("items", []):
            entries.append({
                "id": m.get("id", ""),
                "filename": m.get("filename", ""),
                "mime": m.get("mime", ""),
                "created_at": m.get("created_at", ""),
                "width": m.get("width", 0),
                "height": m.get("height", 0),
                "album": album_name,
            })
        page_token = d.get("nextPageToken", "")
        if len(entries) >= max_results:
            more_available = bool(page_token) or len(entries) > max_results
            break
        if not page_token:
            break
    if len(entries) > max_results:
        entries = entries[:max_results]

    out = {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "images_source": "google_photos",
        # Stesso perimetro degli album (§2.8): solo foto caricate da Metnos.
        "message": _msg("MSG_GPHOTOS_APP_CREATED_ONLY"),
    }
    if more_available:
        out["truncated"] = True
        out["truncated_what"] = "images"
        out["cap_field"] = "max_results"
        out["cap_value"] = max_results
    return out


# --------------------------------------------------------------------------
# PICKER (P3, D8) — l'utente seleziona nella UI Google, Metnos scarica
# --------------------------------------------------------------------------
# Riusa il meccanismo dialog/gate-resume (spec §5: NIENTE poller nuovo):
# 1° invoke → sessions.create → needs_inputs col LINK pickerUri; l'utente
# apre, seleziona (anche dentro un album, multi-selezione), conferma qui;
# resume → sessions.get: non pronta → stesso dialog (onesto), pronta →
# picker-download nel workspace → results (+ gallery via attachments).


def _picker_dialog(args: dict, session_id: str, picker_uri: str,
                   *, not_ready: bool = False) -> dict:
    key = "MSG_GPHOTOS_PICKER_NOT_READY" if not_ready else "MSG_GPHOTOS_PICKER_OPEN"
    prompt = _msg(key, url=picker_uri)
    args_base = {k: v for k, v in dict(args).items()
                 if k.startswith("_") or k in ("dst_dir", "max_total")}
    args_base.update({"picker": True, "picker_session_id": session_id,
                      "picker_uri": picker_uri})
    return {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": _msg("MSG_GPHOTOS_PICKER_TITLE"),
            "dialog": [{
                "var": "picker_done",
                "prompt": prompt,
                "schema": {"kind": "choice",
                           "choices": [_msg("MSG_GPHOTOS_PICKER_CONFIRM")]},
            }],
            "fmt": "auto",
            "on_complete": {
                "type": "resume_executor_with_values",
                "executor": "get_images_google_photos",
                "args_base": args_base,
            },
            "timeout_s": 3600,
        },
        "final_message_hint": prompt,
        "results": [], "used": 0,
    }


def picker(args: dict) -> dict:
    """Flusso Picker: senza `picker_session_id` crea la sessione e chiede
    all'utente di selezionare (link); con session_id (resume) scarica i
    selezionati. Il download locale e' reversibile (delete_created_paths)."""
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "ok_count": 0}
    session_id = (args.get("picker_session_id") or "").strip()
    if not session_id:
        data, err = _run_photos(["photos", "picker-create"],
                                executor="get_images_google_photos",
                                args_base=dict(args), result_kind="results")
        if err is not None:
            return err
        d = data or {}
        sid, uri = d.get("session_id", ""), d.get("picker_uri", "")
        if not sid or not uri:
            return {"ok": False, "error_class": "server_error",
                    "error": _msg("ERR_OP_FAILED", reason="picker session"),
                    "results": [], "used": 0, "ok_count": 0}
        return _picker_dialog(args, sid, uri)

    # RESUME: la selezione e' completata?
    data, err = _run_photos(["photos", "picker-get", session_id],
                            executor="get_images_google_photos",
                            args_base=dict(args), result_kind="results")
    if err is not None:
        return err
    d = data or {}
    if not d.get("media_items_set"):
        uri = d.get("picker_uri") or args.get("picker_uri") or ""
        return _picker_dialog(args, session_id, uri, not_ready=True)

    dst_dir = str(args.get("dst_dir") or (_DEFAULT_DST_DIR / "picker"))
    Path(dst_dir).expanduser().mkdir(parents=True, exist_ok=True)
    argv = ["photos", "picker-download", session_id, "--output", dst_dir]
    max_total = int(args.get("max_total") or 0)
    if max_total:
        argv.extend(["--max", str(max_total)])
    data, err = _run_photos(argv, executor="get_images_google_photos",
                            args_base=dict(args), result_kind="results")
    if err is not None:
        return err
    rows = (data or {}).get("results") or []
    results = [{"ok": True, "id": r.get("id", ""),
                "local_path": r.get("path", ""),
                "filename": r.get("filename", ""),
                "bytes": int(r.get("bytes", 0) or 0)}
               for r in rows if r.get("ok")]
    failed = [{"ok": False, "id": r.get("id", ""),
               "filename": r.get("filename", ""),
               "error_class": "server_error",
               "error": _msg("ERR_OP_FAILED", reason=r.get("error", "download"))}
              for r in rows if not r.get("ok")]
    out = {
        "ok": len(failed) == 0 and len(results) > 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "images_source": "google_photos",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["error_class"] = failed[0]["error_class"]
            out["error"] = failed[0]["error"]
    if results:
        # Il DOVE dichiarato all'utente (richiesta Roberto 10/7: «operazione
        # completata» non diceva il path): conteggio + cartella completa.
        out["message"] = _msg("MSG_GPHOTOS_DOWNLOADED", n=len(results),
                              dir=str(Path(dst_dir).expanduser()))
        out["_undo"] = {
            "reverse_pattern": "delete_created_paths",
            "paths": [r["local_path"] for r in results if r.get("local_path")],
        }
    return out


# --------------------------------------------------------------------------
# GET  (download by media_item_id)
# --------------------------------------------------------------------------

def download(args: dict) -> dict:
    """Scarica 1+ foto app-created per id. Args: `ids`|`entries` (from_step),
    `dst_dir` (default workspace foto). Reversibile: delete_created_paths."""
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "ok_count": 0}

    ids = _ids_from_args(args)
    if not ids:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="ids"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "ok_count": 0}

    dst_dir = str(args.get("dst_dir") or _DEFAULT_DST_DIR)
    Path(dst_dir).expanduser().mkdir(parents=True, exist_ok=True)

    results, failed = [], []
    for mid in ids:
        argv = ["photos", "download", mid, "--output", dst_dir]
        data, err = _run_photos(argv, executor="get_images_google_photos",
                                args_base=dict(args), result_kind="results")
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": mid, "ok": False, **err})
            continue
        d = data or {}
        results.append({"ok": True, "id": mid,
                        "local_path": d.get("path", ""),
                        "filename": d.get("filename", ""),
                        "bytes": int(d.get("bytes", 0) or 0)})

    out = {
        "ok": len(failed) == 0 and len(results) > 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "used": len(results),
        "images_source": "google_photos",
    }
    if failed:
        out["failed"] = failed
        if not results:
            out["error_class"] = failed[0].get("error_class") or "server_error"
            out["error"] = failed[0].get("error") or _msg("ERR_OP_FAILED", reason="download failed")
    if results:
        # DOVE (§2.8/UX): conteggio + cartella completa nel result message.
        out["message"] = _msg("MSG_GPHOTOS_DOWNLOADED", n=len(results),
                              dir=str(Path(dst_dir).expanduser()))
        out["_undo"] = {
            "reverse_pattern": "delete_created_paths",
            "paths": [r["local_path"] for r in results if r.get("local_path")],
        }
    return out
