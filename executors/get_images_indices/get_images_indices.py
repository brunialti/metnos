#!/usr/bin/env python3
"""
get_images_indices — executor di Metnos v1.1.

Status dell'indice immagini unificato v4 per uno o tutti i corpus. Ritorna:
se esiste, n_entries, last_refresh_at, size_mb, modelli e dimensioni.
NON costruisce indici (introspection-only).

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
from executor_helpers import run_stdio  # noqa: E402

_ACTIVE_IDX = "unified"
_LEGACY_IDX = ("scene", "persons", "gps")
_VALID_IDX = (_ACTIVE_IDX, *_LEGACY_IDX, "all")


def _index_image_root() -> Path:
    """Test isolation via env vars (8/5/2026): vedi runtime/config.py."""
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    base = os.environ.get("METNOS_USER_DATA")
    base_p = Path(base) if base else Path.home() / ".local" / "share" / "metnos"
    return base_p / "index" / "image"


def _index_root_for_base(base_path: Path) -> Path:
    # Chiave corpus via SoT condivisa (index_schema.canonical_corpus_path):
    # logica/stabile al mount per il symlink-corpus, coerente con
    # find/create/delete_images_indices (fix 23/6).
    from index_schema import canonical_corpus_path
    digest = hashlib.sha256(
        canonical_corpus_path(base_path).encode("utf-8")).hexdigest()
    return _index_image_root() / digest[:16]


def _path_identity(value) -> str:
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = Path.cwd() / path
    return os.path.normcase(os.path.normpath(str(path)))


def _materialized_index_dir(base_path: Path, idx: str) -> Path:
    """Find a persisted index without requiring the corpus bind.

    Direct lookup covers normal corpora and a still-visible logical symlink.
    Metadata lookup covers a logical-symlink keyed index whose recorded
    ``base_path`` is the offline target visible only as a string.
    """
    direct = _index_root_for_base(base_path) / idx
    if (direct / "meta.json").exists():
        return direct
    wanted = _path_identity(base_path)
    for meta_path in sorted(_index_image_root().glob(f"*/{idx}/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw = meta.get("base_path")
        if (isinstance(raw, str) and raw.strip()
                and _path_identity(raw) == wanted):
            return meta_path.parent
    return direct


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


def _status_for(idx_dir: Path, idx: str, *, base_path: str = "") -> tuple[dict | None, dict | None]:
    """Status di un singolo indice."""
    entry = {
        "idx": idx,
        "exists": False,
    }
    if base_path:
        entry["base_path"] = base_path
    if not idx_dir.exists():
        return entry, None
    entry["exists"] = True
    entry["index_path"] = str(idx_dir)
    entry["size_bytes"] = _dir_size(idx_dir)
    entry["size_mb"] = round(entry["size_bytes"] / (1024 * 1024), 3)
    meta_path = idx_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            entry["n_entries"] = int(meta.get("n_entries", 0))
            entry["n_faces"] = int(meta.get("n_faces", 0) or 0)
            entry["last_refresh_at"] = (
                meta.get("last_refresh_at") or meta.get("updated_at")
            )
            entry["model"] = meta.get("model") or meta.get("model_text")
            entry["model_face"] = meta.get("model_face")
            entry["model_image"] = meta.get("model_image")
            entry["dim"] = meta.get("dim") or meta.get("dim_text")
            entry["version"] = meta.get("schema_version") or meta.get("version")
            if not entry.get("base_path") and isinstance(meta.get("base_path"), str):
                entry["base_path"] = meta["base_path"]
        except json.JSONDecodeError as exc:
            return None, {
                "idx": idx,
                "index_path": str(idx_dir),
                "error": _msg("ERR_FILE_READ_FAILED", path=str(meta_path)),
                "error_class": "invalid_content",
                "error_code": "index_metadata_invalid",
                "detail": str(exc),
            }
        except OSError as exc:
            return None, {
                "idx": idx,
                "index_path": str(idx_dir),
                "error": _msg("ERR_FILE_READ_FAILED", path=str(meta_path)),
                "error_class": "io_error",
                "error_code": "index_metadata_read_failed",
                "detail": str(exc),
            }
    return entry, None


def _discover_index_dirs(idx: str) -> list[Path]:
    """Enumerate materialized corpus indexes without reading photo trees."""
    return sorted(_index_image_root().glob(f"*/{idx}"))


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
            "error_class": "invalid_input",
            "error_code": "args_not_object",
        }
    base_path_arg = args.get("base_path")
    idx = args.get("idx", "all") or "all"

    if base_path_arg is not None and (
        not isinstance(base_path_arg, str) or not base_path_arg.strip()
    ):
        return {
            "ok": False,
            "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="base_path"),
            "error_class": "invalid_input",
            "error_code": "base_path_not_nonempty_string",
        }
    if idx not in _VALID_IDX:
        return {
            "ok": False,
            "error": _msg("ERR_ARG_ENUM", arg="idx", allowed=", ".join(_VALID_IDX)),
            "error_class": "invalid_input",
            "error_code": "idx_invalid",
        }

    selected_idx = _ACTIVE_IDX if idx == "all" else idx
    base = None
    roots: list[tuple[Path, str]] = []
    if isinstance(base_path_arg, str):
        # Path normalizzato (non serve esista realmente: lo status del
        # derivato resta interrogabile anche se il corpus e' offline).
        base = Path(os.path.expanduser(base_path_arg))
        base = Path(os.path.normpath(str(
            base.absolute() if base.is_absolute() else (Path.cwd() / base)
        )))
        roots.append((_materialized_index_dir(base, selected_idx), str(base)))
    else:
        roots.extend((path, "") for path in _discover_index_dirs(selected_idx))

    entries, failed = [], []
    # Per un corpus esplicito conserviamo una entry exists=false; nella
    # discovery globale, zero directory significa semplicemente lista vuota.
    for index_dir, base_label in roots:
        entry, error = _status_for(
            index_dir, selected_idx, base_path=base_label,
        )
        if error is not None:
            failed.append(error)
        else:
            entries.append(entry)
    indexed_entries_total = sum(
        int(entry.get("n_entries", 0) or 0)
        for entry in entries if entry.get("exists")
    )
    out = {
        "ok": not failed,
        "index_root": str(
            roots[0][0].parent if base is not None and roots
            else _index_image_root()
        ),
        "entries": entries,
        "n_entries": len(entries),
        "indexed_entries_total": indexed_entries_total,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "failed": failed,
    }
    if base is not None:
        out["base_path"] = str(base)
    if entries and failed:
        out["partial"] = True
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
