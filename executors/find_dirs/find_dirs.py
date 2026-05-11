#!/usr/bin/env python3
"""find_dirs — walk ricorsivo dell'albero di directory con metadati aggregati.

Ritorna SOLO dirs (mai file). Per ogni dir: file_count diretti, total_bytes
diretti (somma dei file dentro la dir, NON ricorsiva), mtime, name.

Coerente con find_files (parallelo per le directory). Per la lista dei file
contenuti in una dir specifica usa list_dirs(path, recursive=false) o
find_files(base_path=...).

Contratto:
    stdin:  JSON {base_path, recursive?, max_depth?, max_results?, include_hidden?}
    stdout: JSON {ok, entries:[{path, name, file_count, total_bytes, mtime}],
                  matches, truncated, truncated_what?, used?, available_total?,
                  cap_field?, cap_value?, metadata}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def invoke(args):
    base_path = args.get("base_path")
    recursive = args.get("recursive", True)
    max_depth = args.get("max_depth", 10)
    max_results = args.get("max_results", 1000)
    include_hidden = bool(args.get("include_hidden", False))

    if not base_path:
        return {"ok": False, "error": "missing required arg 'base_path'"}
    if not isinstance(max_results, int) or max_results < 1:
        return {"ok": False, "error": "max_results must be a positive integer"}
    if not isinstance(max_depth, int) or max_depth < 0:
        return {"ok": False, "error": "max_depth must be >= 0"}

    base = Path(os.path.expanduser(base_path)).resolve()
    if not base.exists():
        return {"ok": False, "error": f"base_path not found: {base}"}
    if not base.is_dir():
        return {"ok": False, "error": f"base_path is not a directory: {base}"}

    entries: list[dict] = []
    truncated = False
    visited_dirs = 0

    def _scan_dir(d: Path) -> dict | None:
        """Conta file diretti + statistiche size. NON ricorre. Skippa hidden se opt-out."""
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
                        continue  # i symlink ai file non si contano nelle stats
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

    # Walk: BFS via rglob (Path.rglob('*') visita TUTTO; filtriamo a dir).
    try:
        # Includi sempre la base come prima dir.
        base_entry = _scan_dir(base)
        if base_entry is not None:
            entries.append(base_entry)
            visited_dirs += 1
            if len(entries) >= max_results:
                truncated = True

        if recursive and not truncated:
            for p in base.rglob("*"):
                try:
                    if p.is_symlink() or not p.is_dir():
                        continue
                    depth = len(p.relative_to(base).parts)
                except (ValueError, OSError):
                    continue
                if depth > max_depth:
                    continue
                if not include_hidden and any(
                    seg.startswith(".") for seg in p.relative_to(base).parts
                ):
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
                "error": f"permission denied (possibly outside allowed scope): {e}"}
    except OSError as e:
        return {"ok": False, "error": f"os error: {e}"}

    matches = [e["path"] for e in entries]
    out = {
        "ok": True,
        "entries": entries,
        "matches": matches,
        "metadata": {
            "base_path": str(base),
            "recursive": recursive,
            "include_hidden": include_hidden,
            "count": len(entries),
            "visited_dirs": visited_dirs,
            "truncated": truncated,
        },
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "directory"
        out["used"] = len(entries)
        out["cap_field"] = "max_results"
        out["cap_value"] = max_results
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
