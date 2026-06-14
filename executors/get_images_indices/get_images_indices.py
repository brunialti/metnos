#!/usr/bin/env python3
"""
get_images_indices — executor di Metnos v1.1.

Status degli indici immagini per `base_path`. Per ogni `idx`
(scene/persons/gps) ritorna: se esiste, n_entries, last_refresh_at,
size_mb, model, dim. NON costruisce indici (introspection-only).

Output: `entries=[{idx, exists, n_entries, last_refresh_at, size_mb, ...}]`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402

_VALID_IDX_FULL = ("scene", "persons", "gps")
_VALID_IDX = _VALID_IDX_FULL + ("all",)


def _index_image_root() -> Path:
    """Test isolation via env vars (8/5/2026): vedi runtime/config.py."""
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    base = os.environ.get("METNOS_USER_DATA")
    base_p = Path(base) if base else Path.home() / ".local" / "share" / "metnos"
    return base_p / "index" / "image"


def _index_root_for_base(base_path: Path) -> Path:
    # Identita' corpus = path LOGICAL (no .resolve()), coerente con
    # find_images_indices._index_dir. Symlink → NAS non cambia indice.
    digest = hashlib.sha256(str(base_path).encode("utf-8")).hexdigest()
    return _index_image_root() / digest[:16]


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _status_for(idx_dir: Path, idx: str) -> dict:
    """Status di un singolo indice."""
    entry = {
        "idx": idx,
        "exists": False,
    }
    if not idx_dir.exists():
        return entry
    entry["exists"] = True
    entry["index_path"] = str(idx_dir)
    entry["size_bytes"] = _dir_size(idx_dir)
    entry["size_mb"] = round(entry["size_bytes"] / (1024 * 1024), 3)
    meta_path = idx_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            entry["n_entries"] = int(meta.get("n_entries", 0))
            entry["last_refresh_at"] = meta.get("last_refresh_at")
            entry["model"] = meta.get("model")
            entry["dim"] = meta.get("dim")
            entry["version"] = meta.get("version")
        except (json.JSONDecodeError, OSError):
            entry["meta_error"] = True
    return entry


def invoke(args):
    base_path_arg = args.get("base_path")
    idx = args.get("idx", "all") or "all"

    if not base_path_arg:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="base_path")}
    if idx not in _VALID_IDX:
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="idx", allowed=", ".join(_VALID_IDX))}

    # Path normalizzato (non serve esista realmente: si puo' interrogare
    # uno status di indice anche per dir cancellata).
    base = Path(os.path.expanduser(base_path_arg))
    base = Path(os.path.normpath(str(base.absolute() if base.is_absolute()
                                       else (Path.cwd() / base))))
    root = _index_root_for_base(base)

    if idx == "all":
        which = list(_VALID_IDX_FULL)
    else:
        which = [idx]

    entries = [_status_for(root / x, x) for x in which]
    return {
        "ok": True,
        "base_path": str(base),
        "index_root": str(root),
        "entries": entries,
        "n_entries": len(entries),
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
