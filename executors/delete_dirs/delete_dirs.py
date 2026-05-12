#!/usr/bin/env python3
"""
delete_dirs — executor di Metnos v1.1 (rinominato da remove_dirs, 12/5/2026).

Rimuove una o piu' directory. Vettoriale per costruzione: una sola call
accetta una lista di path (anche di un solo elemento, o vuota).

  - if_empty_only=true (default): rimuove SOLO se la dir e' vuota.
    Sicuro: niente cancellazioni accidentali di alberi popolati.
  - force=true: rmtree ricorsivo (rimuove anche se contiene file).
    Default false. L'utente DEVE chiedere esplicitamente la rimozione
    ricorsiva.

Best-effort: ogni dir e' indipendente, una failure non blocca le altre.

Contratto:
    stdin:  JSON con args (paths: list[str], if_empty_only?, force?)
    stdout: JSON {ok, ok_count, fail_count, results, failed}
            ok=true sse fail_count==0 (lista vuota: ok=true, ok_count=0).
"""
import json
import os
import shutil
import sys
from pathlib import Path


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


def invoke(args):
    paths = args.get("paths")
    if_empty_only = args.get("if_empty_only", True)  # informational; if force=true overrides
    force = bool(args.get("force", False))

    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error": "missing or invalid required arg 'paths' (must be a list)"}

    results = []
    failed = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error": "path must be a non-empty string"})
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


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
