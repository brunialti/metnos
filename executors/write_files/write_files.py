#!/usr/bin/env python3
"""fs_write — executor di Metnos v1.1."""
import base64
import json
import os
import sys


def invoke(args):
    path = args.get("path")
    content = args.get("content")
    encoding = args.get("encoding", "utf-8")
    mode = args.get("mode", "overwrite")

    if not path:
        return {"ok": False, "error": "missing required arg 'path'"}
    if content is None:
        return {"ok": False, "error": "missing required arg 'content'"}
    if mode not in ("overwrite", "append", "fail_if_exists"):
        return {"ok": False, "error": f"invalid mode '{mode}'"}

    abs_path = os.path.abspath(os.path.expanduser(path))
    pre_existed = os.path.exists(abs_path)

    if mode == "fail_if_exists" and pre_existed:
        return {"ok": False, "error": f"file already exists: {abs_path}"}

    # mkdir -p del parent: per CLAUDE.md 2.4 robustezza al confine NL→det,
    # write_files su path nidificato deve creare le directory mancanti senza
    # richiedere una call separata a create_dirs. I parent creati vengono
    # registrati nel result per supportare l'undo (delete_created_paths).
    parent = os.path.dirname(abs_path)
    created_parents: list[str] = []
    if parent and not os.path.isdir(parent):
        # Walk verso l'alto per registrare ogni livello creato.
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
            return {"ok": False, "error": f"cannot create parent dir {parent!r}: {e}"}

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
        return {"ok": False, "error": f"permission denied (possibly outside allowed scope): {abs_path}"}
    except IsADirectoryError:
        return {"ok": False, "error": f"is a directory, not a file: {abs_path}"}
    except OSError as e:
        return {"ok": False, "error": f"os error: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"unexpected: {type(e).__name__}: {e}"}


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        result = {"ok": False, "error": "empty input"}
    else:
        try:
            args = json.loads(raw)
            result = invoke(args)
        except json.JSONDecodeError as e:
            result = {"ok": False, "error": f"invalid input json: {e}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
