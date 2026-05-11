#!/usr/bin/env python3
"""
create_dirs — executor di Metnos v1.1.

Crea una o piu' directory in scope di scrittura. Vettoriale per costruzione:
una sola call accetta una lista di path (anche di un solo elemento, o vuota).

  - parents=true (default): crea le dir intermedie mancanti per ciascun path
  - exist_ok=true (default): non fallisce se la dir esiste gia'

Contratto:
    stdin:  JSON con args (paths: list[str], parents?, exist_ok?, mode?)
    stdout: JSON {ok, ok_count, fail_count, results, failed}
            ok=true sse fail_count==0 (lista vuota: ok=true, ok_count=0).
"""
import json
import os
import sys
from pathlib import Path


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


def invoke(args):
    paths = args.get("paths")
    parents = args.get("parents", True)
    exist_ok = args.get("exist_ok", True)
    mode = args.get("mode")

    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error": "missing or invalid required arg 'paths' (must be a list)"}
    if mode is not None:
        if not isinstance(mode, int) or not (0 <= mode <= 0o777):
            return {"ok": False, "error": "mode must be an integer in 0..0o777"}

    results = []
    failed = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error": "path must be a non-empty string"})
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


def reverse(plan, results):
    """Undo: rimuove le dir create dal forward (created=true), solo se vuote.

    Ignora le dir gia' esistenti prima del forward (created=false): non sono
    state create da noi. Se una dir creata e' nel frattempo stata popolata da
    altri, fallisce quella entry (sicurezza: non rmtree senza force esplicito).
    """
    entries = (results or {}).get("results") or []
    out_results, failed = [], []
    # Ordine inverso: rimuove prima le dir piu' profonde (figlie create insieme)
    candidates = [e for e in entries if e.get("created")]
    candidates.sort(key=lambda e: len(e["path"]), reverse=True)
    for i, entry in enumerate(candidates):
        path = Path(entry["path"])
        if not path.exists():
            failed.append({"index": i, "path": str(path), "error": "path no longer exists"})
            continue
        if not path.is_dir():
            failed.append({"index": i, "path": str(path), "error": "not a directory"})
            continue
        try:
            children = list(path.iterdir())
        except OSError as e:
            failed.append({"index": i, "path": str(path), "error": f"cannot list: {e}"})
            continue
        if children:
            failed.append({"index": i, "path": str(path), "error": f"directory not empty ({len(children)} items): not auto-removing"})
            continue
        try:
            path.rmdir()
            out_results.append({"path": str(path), "removed": True})
        except OSError as e:
            failed.append({"index": i, "path": str(path), "error": f"rmdir failed: {e}"})
    return {
        "ok": len(failed) == 0,
        "ok_count": len(out_results),
        "fail_count": len(failed),
        "results": out_results,
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
