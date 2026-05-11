#!/usr/bin/env python3
"""
fs_read — executor di Metnos v1.1.

Contratto POC:
    stdin:  JSON con args (path, encoding?, max_bytes?)
    stdout: JSON con esito {ok, content, metadata} oppure {ok=false, error}
    exit:   0 sempre (l'esito sta nel JSON, non nell'exit code)
"""
import base64
import json
import os
import sys


def invoke(args):
    path = args.get("path")
    encoding = args.get("encoding", "utf-8")
    max_bytes = args.get("max_bytes")
    tail_bytes = args.get("tail_bytes")
    offset = args.get("offset", 0)

    # Robustezza: alcuni LLM passano 0 come "non specificato" invece di omettere
    # il campo. Tratta 0 come None.
    if max_bytes == 0:
        max_bytes = None
    if tail_bytes == 0:
        tail_bytes = None

    if not path:
        return {"ok": False, "error": "missing required arg 'path'"}
    if max_bytes is not None and tail_bytes is not None:
        return {"ok": False, "error": "max_bytes e tail_bytes sono mutuamente esclusivi"}

    abs_path = os.path.abspath(os.path.expanduser(path))

    try:
        file_size = os.path.getsize(abs_path)

        # Calcola posizione di lettura e quantita'
        if tail_bytes is not None:
            seek_to = max(0, file_size - tail_bytes)
            read_n = tail_bytes
            mode_str = "tail"
        else:
            seek_to = offset
            read_n = max_bytes  # None = leggi tutto fino a EOF
            mode_str = "offset" if offset > 0 else ("head" if max_bytes else "full")

        # Truncation visibility (CLAUDE.md 2.7+2.11): se la lettura non
        # copre l'intero file (max_bytes/tail_bytes/offset hanno tagliato),
        # dichiara il cap a livello top per il runtime auto-cap-expand.
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
            # Per testo: leggiamo come bytes, poi decodifichiamo (per supportare seek byte-wise)
            with open(abs_path, "rb") as f:
                if seek_to:
                    f.seek(seek_to)
                raw = f.read(read_n) if read_n else f.read()
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                # Per tail/offset puo' capitare di tagliare un char multibyte: skip iniziale
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
        return {"ok": False, "error": f"file not found: {abs_path}"}
    except PermissionError:
        return {"ok": False, "error": f"permission denied (possibly outside allowed scope): {abs_path}"}
    except IsADirectoryError:
        return {"ok": False, "error": f"is a directory, not a file: {abs_path}"}
    except OSError as e:
        return {"ok": False, "error": f"os error: {e}"}


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return

    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
