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
import re
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from backends._google_api_runner import run_with_retry  # noqa: E402
from backends._google_auth_common import (  # noqa: E402
    SKILL_NAME,
    has_creds as _has_creds,
    ensure_fresh_token as _ensure_fresh_token,
    auth_needs_inputs as _auth_needs_inputs,
)
from messages import get as _msg  # noqa: E402


_DRIVE_ID_RE = re.compile(r"[A-Za-z0-9_-]{12,}")


def _looks_like_drive_id(value) -> bool:
    """Forma sintattica di un id Drive: token OPACO (>=12 char, alnum/-/_), NIENTE
    spazi. Un valore con spazi o troppo corto NON e' un id: e' un NOME che il
    proposer ha messo nell'arg id (es. `spreadsheet_id="KAKEBO SPESE 2026"`) →
    va trattato come LOCATORE nominale, non come id (che darebbe 404). §7.9."""
    return bool(isinstance(value, str) and _DRIVE_ID_RE.fullmatch(value.strip()))


def _entry_file_id(entry: dict) -> str | None:
    """ID Drive da una entry prodotta da find/get.

    Nei flussi compound il runtime espande `from_step` in `entries`. Drive pero'
    non legge per path locale: legge per id. Accettiamo i nomi campo emessi dai
    backend Google e dai wrapper storici, evitando valori vuoti.
    """
    for key in ("id", "file_id", "document_id", "spreadsheet_id", "uid"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _ids_from_args(args: dict) -> list[str]:
    ids: list[str] = []
    if isinstance(args.get("file_ids"), list):
        ids.extend(str(x).strip() for x in args["file_ids"]
                   if x is not None and str(x).strip())
    for key in ("file_id", "document_id", "spreadsheet_id"):
        fid = args.get(key)
        # Solo se e' un id OPACO: un titolo finito qui (es. il proposer che
        # scrive il NOME in spreadsheet_id) NON e' un id → lo raccoglie
        # `_locator_query_from_args` come locatore.
        if _looks_like_drive_id(fid):
            ids.append(fid.strip())
    paths = args.get("paths")
    if isinstance(paths, list):
        for p in paths:
            # Stessa regola degli id-arg scalari (fix 7/7, era preso TUTTO):
            # solo forma id OPACA. Un NOME in paths («report budget.pdf»,
            # «prova-77») NON è un id → lo risolve il locatore nominale,
            # non un delete-by-id cieco che fa 404.
            if isinstance(p, str) and _looks_like_drive_id(p):
                ids.append(p.strip())
    entries = args.get("entries") or []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                fid = _entry_file_id(e)
                if fid:
                    ids.append(fid)
    return list(dict.fromkeys(ids))


def _locator_query_from_args(args: dict) -> str:
    """Nome/query Drive fornito esplicitamente al backend.

    Non deriva dalla frase utente: qui il backend accetta solo locatori nominali
    gia' strutturati (`query`, `name`, `pattern`, `patterns`). L'estrazione dalla
    lingua naturale resta responsabilita' dell'engine/intent.
    """
    for key in ("query", "name", "pattern"):
        value = args.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in ("*", "*.*", "**"):
            return value.strip().strip("*")
    patterns = args.get("patterns")
    if isinstance(patterns, list):
        for value in patterns:
            if isinstance(value, str) and value.strip() and value.strip() not in ("*", "*.*", "**"):
                return value.strip().strip("*")
    # Un id-arg che NON e' id-shaped (il proposer ha messo il TITOLO in
    # document_id/spreadsheet_id) e' un locatore nominale, non un id.
    for key in ("document_id", "spreadsheet_id", "file_id"):
        value = args.get(key)
        if isinstance(value, str) and value.strip() and not _looks_like_drive_id(value):
            return value.strip()
    # `path`/`paths` (arg canonico dei dispatcher files/dirs): su Drive un
    # valore SENZA '/' e' un NOME, non un percorso — «cancella la cartella
    # prova-metnos-77 da drive» arriva come paths=["prova-metnos-77"] (7/7,
    # turno reale). Path-shaped (con '/') = locale, NON locatore Drive.
    _p = args.get("path")
    _ps = args.get("paths")
    if _p is None and isinstance(_ps, list) and len(_ps) == 1:
        _p = _ps[0]
    if (isinstance(_p, str) and _p.strip() and "/" not in _p
            and not _looks_like_drive_id(_p)):
        return _p.strip()
    return ""


_MIME_BY_KIND = {
    "document": "application/vnd.google-apps.document",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
}


def _narrow_locator_entries(entries: list[dict], *, locator: str,
                            mime_kind: str | None) -> list[dict]:
    """Restringe i risultati di ricerca Drive per una risoluzione SINGLE-target.

    La ricerca e' `fullText contains` (larga) e Doc/Sheet possono condividere il
    nome: (1) filtro per TIPO (read_files_doc→document, ..._spreadsheet→
    spreadsheet); (2) preferenza NOME ESATTO (case-insensitive) fra gli omonimi.
    Deterministico §7.9. Se il filtro-tipo azzera → lista vuota → not_found
    onesto (mai leggere un Sheet quando l'utente ha chiesto un Doc)."""
    out = entries
    want = _MIME_BY_KIND.get(mime_kind or "")
    if want:
        out = [e for e in out
               if (e.get("mimeType") or e.get("mime_type")) == want]
    loc = (locator or "").strip().casefold()
    if loc:
        exact = [e for e in out
                 if str(e.get("name") or e.get("title") or "").strip().casefold() == loc]
        if exact:
            out = exact
    return out


def _search_entries_for_locator(args: dict, *, max_results: int | None = None,
                                mime_kind: str | None = None
                                ) -> tuple[list[dict], dict | None]:
    query = _locator_query_from_args(args)
    if not query:
        return [], None
    search_args = dict(args)
    search_args["query"] = query
    search_args.pop("pattern", None)
    search_args.pop("patterns", None)
    if max_results is not None:
        search_args["max_results"] = max_results
    out = find(search_args)
    if out.get("decision") == "needs_inputs":
        return [], out
    if not out.get("ok"):
        return [], out
    entries = [e for e in (out.get("entries") or []) if isinstance(e, dict)]
    # Lettori single-target (mime_kind noto): filtro tipo + nome esatto.
    if mime_kind:
        entries = _narrow_locator_entries(entries, locator=query,
                                          mime_kind=mime_kind)
    else:
        # `read()` vettoriale (mime_kind=None): NIENTE filtro-tipo, ma se il
        # locatore e' un `name` ESPLICITO (l'utente ha nominato UN file) e fra
        # gli omonimi ce n'e' uno col nome ESATTO, risolvi a quello — «leggi il
        # file KAKEBO SPESE 2026» = l'unico esatto, non ogni substring
        # (Doc + fogli-omonimi «… - Dati»/«… — estratto»). No-op senza `name` o
        # senza match esatto → resta vettoriale §2.1. §7.9 deterministico.
        _nm = args.get("name")
        if isinstance(_nm, str) and _nm.strip():
            entries = _narrow_locator_entries(entries, locator=_nm.strip(),
                                              mime_kind=None)
    return entries, None


def _ids_from_args_or_locator(args: dict, *, max_results: int | None = None,
                              mime_kind: str | None = None
                              ) -> tuple[list[str], list[dict], dict | None]:
    ids = _ids_from_args(args)
    if ids:
        entries = [e for e in (args.get("entries") or [])
                   if isinstance(e, dict)]
        return ids, entries, None
    entries, err = _search_entries_for_locator(args, max_results=max_results,
                                               mime_kind=mime_kind)
    if err is not None:
        return [], [], err
    ids = [_entry_file_id(e) for e in entries]
    return [i for i in dict.fromkeys(ids) if i], entries, None


def _not_found_for_locator(args: dict, *, result_kind: str = "entries") -> dict:
    query = _locator_query_from_args(args)
    out = {"ok": False,
           "error_code": "ERR_PATH_NOT_FOUND",
           "error": _msg("ERR_PATH_NOT_FOUND", path=query or "file"),
           "error_class": "not_found",
           "used": 0}
    if result_kind == "entries":
        out["entries"] = []
    else:
        out["results"] = []
    return out


def _drive_choice_needed(args: dict, entries: list[dict], *,
                         executor: str, title: str) -> dict:
    choices = []
    for e in entries[:25]:
        fid = _entry_file_id(e)
        if not fid:
            continue
        label = e.get("name") or e.get("title") or fid
        mime = e.get("mimeType") or e.get("mime_type")
        if mime:
            label = f"{label} ({mime})"
        choices.append({"value": fid, "label": label})
    return {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": title,
            "dialog": [{
                "var": "file_id",
                "prompt": "Scegli il file Drive da usare",
                "schema": {"kind": "choice", "choices": choices},
            }],
            "fmt": "auto",
            "on_complete": {
                "type": "resume_executor_with_values",
                "executor": executor,
                "args_base": {k: v for k, v in dict(args).items()
                              if k not in ("query", "name", "pattern", "patterns")},
            },
            "timeout_s": 3600,
        },
        "entries": entries,
        "used": 0,
        "error_class": "ambiguous",
    }


def _single_id_from_args_or_locator(args: dict, *, id_arg: str,
                                    executor: str, result_kind: str,
                                    title: str,
                                    mime_kind: str | None = None
                                    ) -> tuple[str, dict | None]:
    """Resolve un singolo ID Drive per operazioni by-id.

    Accetta ID diretto, `entries` da `from_step`, oppure un locatore nominale
    (`query`, `name`, `pattern`, o un titolo finito nell'arg id). `mime_kind`
    (document/spreadsheet) restringe la ricerca al tipo giusto + preferisce il
    nome esatto, cosi' fra omonimi Doc/Sheet non chiede quando non serve.
    """
    ids, entries, err = _ids_from_args_or_locator(args, max_results=25,
                                                  mime_kind=mime_kind)
    if err is not None:
        return "", err
    if not ids:
        if _locator_query_from_args(args):
            return "", _not_found_for_locator(args, result_kind=result_kind)
        out = {"ok": False, "error_code": "ERR_ARG_MISSING",
               "error": _msg("ERR_ARG_MISSING", arg=id_arg),
               "error_class": "invalid_args", "used": 0}
        if result_kind == "entries":
            out["entries"] = []
        else:
            out["results"] = []
            out["n_written"] = 0
        return "", out
    if len(ids) > 1 and not args.get("_confirmed"):
        return "", _drive_choice_needed(args, entries, executor=executor,
                                        title=title)
    return ids[0], None


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
        # find_files(local) usa paths/patterns (liste) o pattern (str) per il
        # nome-file: mappali a query Drive (name contains). Glob universali
        # ignorati (cercherebbero tutto).
        for _k in ("paths", "patterns"):
            _v = args.get(_k)
            if isinstance(_v, list) and _v and str(_v[0]).strip():
                query = str(_v[0]).strip().strip("*")
                break
    if not query:
        pat = args.get("pattern")
        if isinstance(pat, str) and pat.strip() and pat.strip() not in ("*", "*.*", "**"):
            query = pat.strip().strip("*")
    if not query:
        return {"ok": False,
                "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="query (o 'paths'/'pattern')"),
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
    # Confine oggetti §2.2 (non-raw): find_FILES non ritorna CARTELLE — per le
    # cartelle c'e' find_dirs. Senza, un folder col nome esatto della query
    # oscurava i file omonimi-fuzzy (preferenza sotto, type-blind) → il reader
    # single-target andava in not_found invece di proporre la scelta (turn
    # 9fc0111a, cartella «Richieste effettuate» vs 2 fogli). Il raw resta
    # sotto controllo del chiamante (find_dirs usa raw_query mimeType=folder).
    if not raw and entries:
        entries = [e for e in entries
                   if (e.get("mimeType") or e.get("mime_type")) != _FOLDER_MIME]
    # Preferenza NOME ESATTO (non-raw): la ricerca Drive e' `fullText contains`
    # (fuzzy: matcha anche i doc che MENZIONANO il termine, es. «KAKEBO SPESE
    # 2025-26» il cui contenuto cita «2026»). Se l'utente ha nominato UN file e
    # c'e' un match col nome ESATTO, restringi a quello — «cerca il file X» = il
    # file X, non ogni doc che lo cita. No-op senza esatto → resta vettoriale §2.1.
    if not raw and entries:
        q_exact = str(query).strip().casefold()
        exact = [e for e in entries
                 if str(e.get("name") or e.get("title") or "").strip().casefold() == q_exact]
        if exact:
            entries = exact
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
    """Legge il CONTENUTO di 1+ file Drive per id (vettoriale §2.1): i Google-native
    (Doc/Sheet/Slides) sono ESPORTATI a testo, i binari scaricati. Ogni entry porta
    `content` (testo inline) + metadata. Era metadata-only (`drive get`) fino al
    4/7/2026: `read` non tornava il testo dei Doc (bug «scrive-ma-non-legge»)."""
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args", "entries": [], "used": 0}

    ids, _resolved_entries, err = _ids_from_args_or_locator(args)
    if err is not None:
        return err
    if not ids:
        if _locator_query_from_args(args):
            return _not_found_for_locator(args, result_kind="entries")
        return {"ok": False,
                "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING",
                              arg="file_id/file_ids/paths/entries/query"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}

    entries: list[dict] = []
    for fid in ids:
        data, err = _run_drive(["drive", "read", fid], executor="read_files",
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

    ids, resolved_entries, err = _ids_from_args_or_locator(args, max_results=25)
    if err is not None:
        return err
    if not ids:
        if _locator_query_from_args(args):
            out = _not_found_for_locator(args, result_kind="results")
            out["n_deleted"] = 0
            return out
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING",
                              arg="file_id/file_ids/entries/query"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    if len(ids) > 1 and resolved_entries and not args.get("_confirmed"):
        return _drive_choice_needed(
            args, resolved_entries, executor="delete_files",
            title="Scegli il file Drive da cancellare")

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

    # Ordine (§2.8 no-silent, §7.9): valida la FORMA degli arg obbligatori
    # PRIMA di risolvere/toccare il file. Per type=user|group l'email e'
    # obbligatoria: mancante = errore d'arg deterministico, indipendente da
    # credenziali/I/O (prima la risoluzione locator del file_id mascherava
    # questo con un fuorviante ERR_PATH_NOT_FOUND). Il check OAuth avviene
    # nella risoluzione DOPO (find→_run_drive→needs_inputs).
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

    ids, resolved_entries, err = _ids_from_args_or_locator(args, max_results=25)
    if err is not None:
        return err
    if not ids:
        if _locator_query_from_args(args):
            return _not_found_for_locator(args, result_kind="results")
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="file_id/file_ids/query"),
                "error_class": "invalid_args",
                "results": [], "used": 0}
    if len(ids) > 1 and resolved_entries:
        return _drive_choice_needed(
            args, resolved_entries, executor="share_files",
            title="Scegli il file Drive da condividere")

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
    # `trashed=false` come la ricerca FILE (fix 5/7 turno bb977a14, esteso ai
    # folder 7/7: una cartella cestinata riappariva nel find — misurato e2e).
    raw_query = f"mimeType='{_FOLDER_MIME}' and trashed=false"
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
    """Cancella 1+ cartelle su Drive. Senza id espliciti risolve i NOMI
    (`paths`/`path`/`name`) via `find_dirs` (mime folder + trashed=false),
    preferenza nome esatto; un valore id-shaped SENZA match nominale è usato
    come id opaco (i nomi utente tipo «prova-77» passano il test id-shape —
    name-first è l'ordine giusto al confine NL §2.4, misurato 7/7). Default
    trash (reversibile) come `delete`."""
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_deleted": 0}
    a = dict(args)
    # Id ESPLICITI = solo file_ids/file_id/entries (piping): i valori in
    # `paths` sono NOMI-utente per costruzione qui — anche quelli id-shaped
    # («prova-metnos-77») vanno risolti name-first via find_dirs.
    _probe = {k: a[k] for k in ("file_ids", "file_id", "entries") if k in a}
    if not _ids_from_args(_probe):
        names = a.get("paths") if isinstance(a.get("paths"), list) else None
        if names is None:
            one = a.get("path") or a.get("name")
            names = [one] if isinstance(one, str) and one.strip() else []
        ids: list = []
        misses: list = []
        for n in names:
            nn = n.strip() if isinstance(n, str) else ""
            if not nn or "/" in nn:      # path locale, non un nome Drive
                misses.append(str(n))
                continue
            fr = find_dirs({"name": nn, "max_results": 25})
            if fr.get("decision") == "needs_inputs":
                return fr
            entries = [e for e in (fr.get("entries") or [])
                       if isinstance(e, dict)]
            exact = [e for e in entries
                     if str(e.get("name") or "").strip().casefold()
                     == nn.casefold()]
            got = [i for i in dict.fromkeys(
                _entry_file_id(e) for e in (exact or entries)) if i]
            if got:
                ids.extend(got)
            elif _looks_like_drive_id(nn):
                ids.append(nn)
            else:
                misses.append(nn)
        if not ids:
            return _not_found_for_locator(a, result_kind="results")
        a["file_ids"] = list(dict.fromkeys(ids))
        for k in ("path", "paths", "pattern", "patterns", "query", "name"):
            a.pop(k, None)
        out = delete(a)
        if misses and isinstance(out, dict):
            # §2.8: i nomi non risolti NON spariscono in silenzio.
            out.setdefault("failed", []).extend(
                {"id": m, "ok": False, "error_class": "not_found",
                 "error_code": "ERR_PATH_NOT_FOUND",
                 "error": _msg("ERR_PATH_NOT_FOUND", path=m)} for m in misses)
            out["ok"] = False
        return out
    return delete(a)


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
      - `range`: str A1 (default "A:ZZ" non qualificato = tutta la prima tab).
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
    sid, sid_err = _single_id_from_args_or_locator(
        args,
        id_arg="spreadsheet_id",
        executor="read_files_spreadsheet",
        result_kind="entries",
        title="Scegli il foglio Google da leggere",
        mime_kind="spreadsheet",
    )
    if sid_err is not None:
        return sid_err
    # Default = range NON qualificato (senza nome-tab): Sheets lo applica alla
    # PRIMA tab qualunque sia il suo nome (Foglio1/Sheet1/...). "Sheet1" come
    # default rompeva i fogli a locale IT (tab "Foglio1"). Le celle vuote di coda
    # sono troncate dall'API, quindi "A:ZZ" = "tutta la prima tab". §7.3 generale.
    rng = (args.get("range") or "A:ZZ").strip()
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
    sid, sid_err = _single_id_from_args_or_locator(
        args,
        id_arg="spreadsheet_id",
        executor="write_files_spreadsheet",
        result_kind="results",
        title="Scegli il foglio Google da modificare",
        mime_kind="spreadsheet",
    )
    if sid_err is not None:
        return sid_err
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
    _did_raw = args.get("document_id")
    if _did_raw is not None and not isinstance(_did_raw, str):
        return {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                "error": _msg("ERR_ARG_NOT_STRING", arg="document_id"),
                "error_class": "invalid_args", "entries": [], "used": 0}
    did, did_err = _single_id_from_args_or_locator(
        args,
        id_arg="document_id",
        executor="read_files_doc",
        result_kind="entries",
        title="Scegli il documento Google da leggere",
        mime_kind="document",
    )
    if did_err is not None:
        return did_err
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
    _did_raw = args.get("document_id")
    if _did_raw is not None and not isinstance(_did_raw, str):
        return {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                "error": _msg("ERR_ARG_NOT_STRING", arg="document_id"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    # Ordine (§2.8 no-silent, §7.9): valida gli arg OBBLIGATORI PRIMA di
    # risolvere/toccare il doc. `text` mancante/vuoto = errore d'arg
    # deterministico, indipendente da credenziali e da qualsiasi I/O Drive
    # (prima invece la risoluzione locator del document_id mascherava questo
    # con un fuorviante ERR_PATH_NOT_FOUND). La risoluzione — che fa il check
    # OAuth internamente (find→_run_drive→_ensure_fresh_token→needs_inputs) —
    # viene DOPO.
    text = args.get("text")
    if not isinstance(text, str) or not text:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error": _msg("ERR_ARG_MISSING", arg="text"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_written": 0}
    did, did_err = _single_id_from_args_or_locator(
        args,
        id_arg="document_id",
        executor="write_files_doc",
        result_kind="results",
        title="Scegli il documento Google da modificare",
        mime_kind="document",
    )
    if did_err is not None:
        return did_err
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
