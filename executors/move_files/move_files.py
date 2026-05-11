#!/usr/bin/env python3
"""
move_files — executor di Metnos v1.1.

Sposta o rinomina una o piu' entries (file/directory) in una sola call.
Vettoriale per costruzione: accetta sempre una lista di entries (anche di
un solo elemento, o vuota).

La destinazione di ogni entry si calcola da `dst_template` espandendo i
placeholder coi campi della entry. Placeholder supportati:

  {path}          - path assoluto della sorgente
  {name}          - basename (es. "046.JPG")
  {stem}          - basename senza estensione (es. "046")
  {ext}           - estensione senza il punto (es. "JPG"); '' se assente
  {parent}        - dirname della sorgente
  {date_year}     - anno YYYY della 'data sensata': `date_epoch` della entry
                    se presente (es. da get_files_metadata → EXIF),
                    altrimenti mtime del filesystem.
  {date_yymmdd}   - YYMMDD della 'data sensata'
  {date_yyyymmdd} - YYYYMMDD della 'data sensata'
  {place}         - nome luogo slug (es. 'roma', 'boulder'); 'unknown' se
                    la entry non ha campo `place` o se `place` e' vuoto.

Le entries devono avere almeno `path` (oppure `src`); per ottenere date
semantiche e luogo chiama `get_files_metadata` prima e passa le entries
arricchite. Senza `date_epoch` i placeholder `{date_*}` cadono su mtime;
senza `place` (o se "unknown") il placeholder `{place}` vale 'unknown'.

Comportamento:
  - overwrite=false (default): se dst esiste, quella entry fallisce
  - overwrite=true: rimpiazza dst se esiste
  - parents=true (default): crea la dir parent di dst se manca
  - best-effort: ogni entry e' indipendente, una failure non blocca le altre

Contratto:
    stdin:  JSON con args (entries: list[dict], dst_template: str, overwrite?, parents?)
    stdout: JSON {ok, ok_count, fail_count, results, failed}
            ok=true sse fail_count==0 (lista vuota: ok=true, ok_count=0).
"""
import datetime
import json
import os
import shutil
import sys
from pathlib import Path


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
    # 'data sensata' arricchita: usa date_epoch se presente (es. da get_file_dates),
    # altrimenti cade su mtime. Cosi' i placeholder {date_*} sono sempre definiti.
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
    """Esegue lo spostamento; ritorna (ok, error, dirs_created).

    `dirs_created` e' la lista di Path di directory effettivamente create
    da QUESTA call (non pre-esistenti). Serve al reverse() per sapere
    quali dir parent sono "nostre" e possono essere rimosse durante undo.
    """
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
        # raccolgo le dir parent che NON esistono ancora (in ordine: piu' alta → piu' profonda)
        ancestors = []
        p = dst_path.parent
        while not p.exists():
            ancestors.append(p)
            p = p.parent
        ancestors.reverse()  # crea dall'alto al basso quando si invoca mkdir(parents=True)
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


sys.path.insert(0, "/opt/myclaw/runtime")
from platform_policy import is_system_file, is_protected_path  # noqa: E402


def invoke(args):
    entries = args.get("entries")
    dst_template = args.get("dst_template")
    overwrite = bool(args.get("overwrite", False))
    parents = bool(args.get("parents", True))
    allow_dirs = bool(args.get("allow_dirs", False))
    allow_system = bool(args.get("allow_system", False))

    if entries is None or not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid required arg 'entries' (must be a list)"}
    if not dst_template or not isinstance(dst_template, str):
        return {"ok": False, "error": "missing or invalid required arg 'dst_template' (must be a string)"}

    results = []
    failed = []
    all_dirs_created = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            failed.append({"index": i, "error": "entry must be a dict"})
            continue
        src_arg = entry.get("path") or entry.get("src")
        if not src_arg or not isinstance(src_arg, str):
            failed.append({"index": i, "error": "entry missing 'path' (or 'src') string"})
            continue
        src_path = Path(os.path.expanduser(src_arg)).resolve()
        # safety net: rifiuta dir e file di sistema se non esplicitamente abilitati.
        # Il pianificatore deve passare allow_dirs=true / allow_system=true se intende
        # davvero spostare directory o file di sistema. Default: solo file utente.
        kind = entry.get("kind") or ""
        if not allow_dirs and (kind == "dir" or src_path.is_dir()):
            failed.append({"index": i, "src": str(src_path), "error": "refusing to move a directory (kind=dir); pass allow_dirs=true to override, or filter entries via filter_entries(kind='image'|'video'|...) before move_files"})
            continue
        if not allow_system and is_system_file(src_path.name):
            failed.append({"index": i, "src": str(src_path), "error": f"refusing to move system file '{src_path.name}'; pass allow_system=true to override, or filter via filter_entries"})
            continue
        try:
            fields = _entry_fields(entry, src_path)
            dst_str = dst_template.format(**fields)
        except KeyError as e:
            failed.append({"index": i, "src": str(src_path), "error": f"unknown placeholder in dst_template: {e}"})
            continue
        except Exception as e:
            failed.append({"index": i, "src": str(src_path), "error": f"template render failed: {e}"})
            continue
        dst_path = Path(os.path.expanduser(dst_str)).resolve()
        ok, err, dirs_created = _move_one(src_path, dst_path, overwrite, parents)
        if ok:
            results.append({"src": str(src_path), "dst": str(dst_path)})
            for d in dirs_created:
                all_dirs_created.add(str(d))
        else:
            failed.append({"index": i, "src": str(src_path), "dst": str(dst_path), "error": err})

    # ordine: dir piu' profonde prima (rimozione bottom-up sicura nell'undo)
    dirs_created_list = sorted(all_dirs_created, key=lambda p: p.count("/"), reverse=True)

    return {
        "ok": len(failed) == 0,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "dirs_created": dirs_created_list,
        "failed": failed,
    }


def reverse(plan, results):
    """Undo multistage: (1) sposta dst→src per ogni pair, (2) rimuove le
    directory create dal forward, in ordine bottom-up, solo se vuote.

    `plan` non e' usato (i src/dst reali e dirs_created sono nei results).
    Best-effort: se una dir e' stata popolata da altre operazioni dopo il
    forward, NON viene rimossa (sicurezza: rmdir solo su dir vuote).
    """
    pairs = (results or {}).get("results") or []
    dirs_created = (results or {}).get("dirs_created") or []
    out_results, failed = [], []

    # Stage 1: sposta indietro dst → src
    for i, p in enumerate(pairs):
        src_now = Path(p["dst"])
        dst_back = Path(p["src"])
        ok, err, _ = _move_one(src_now, dst_back, overwrite=False, parents=True)
        if ok:
            out_results.append({"src": str(src_now), "dst": str(dst_back)})
        else:
            failed.append({"index": i, "src": str(src_now), "dst": str(dst_back), "error": err})

    # Stage 2: rimuove dir create dal forward (ordine: piu' profonde prima)
    dirs_removed = []
    dirs_kept = []
    sorted_dirs = sorted(dirs_created, key=lambda p: p.count("/"), reverse=True)
    for d_str in sorted_dirs:
        d = Path(d_str)
        if not d.exists():
            continue  # gia' rimossa o mai esistita
        if not d.is_dir():
            continue  # qualcosa l'ha sostituita con un file: non tocchiamo
        try:
            if not any(d.iterdir()):
                d.rmdir()
                dirs_removed.append(str(d))
            else:
                dirs_kept.append(str(d))  # popolata da altre op: tenuta
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


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
