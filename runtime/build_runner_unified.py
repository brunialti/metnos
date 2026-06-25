"""build_runner_unified — runner standalone per build/migrazione unified v4.

Invocato via systemd-run --user (riuso pattern ADR 0093). Esegue:
1. Se la cartella ha legacy v3 (scene/persons/gps) ma non unified → migration.
2. Se unified esiste ma e' incompleto (force=True o file mancanti) → rebuild.

Esce con exit-code 0 = success, 1 = fail. Stampa progress JSONL su stdout
(systemd-run --user lo cattura nel journal). Atomic write meta.json al
termine (resume da checkpoint ogni 500 foto).

Usage:
    build_runner_unified --base-path <path> [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Add runtime to path
_THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("build_runner_unified")


def _emit_progress(stage: str, **kwargs) -> None:
    """Stampa una riga progress JSONL su stdout."""
    rec = {"ts": time.time(), "stage": stage, **kwargs}
    sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _has_legacy_v3(corpus_dir: Path) -> bool:
    return any(
        (corpus_dir / d / "meta.json").exists()
        for d in ("scene", "persons", "gps")
    )


def _has_unified_v4(corpus_dir: Path) -> bool:
    meta_p = corpus_dir / "unified" / "meta.json"
    if not meta_p.exists():
        return False
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        return int(meta.get("schema_version", 0)) >= 4
    except (json.JSONDecodeError, OSError):
        return False


def _resolve_corpus_dir(base_path: Path) -> Path:
    """Risolve la corpus dir indice da base_path.
    Path LOGICAL (no .resolve()): coerente con _index_dir di
    find/create/get/delete_images_indices."""
    import hashlib
    import os
    import config as _C  # §7.11
    from index_schema import canonical_corpus_path
    digest = hashlib.sha256(
        canonical_corpus_path(base_path).encode("utf-8")).hexdigest()
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image" / digest[:16]
    return _C.PATH_USER_DATA / "index" / "image" / digest[:16]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-path", required=True,
                        help="Directory radice della collezione foto.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild totale (ignora indice esistente).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Stima senza scrivere.")
    parser.add_argument("--max-files", type=int, default=50000)
    args = parser.parse_args()

    base_path = Path(args.base_path).expanduser().resolve()
    if not base_path.exists() or not base_path.is_dir():
        _emit_progress("error", error=f"base_path not found or not dir: {base_path}")
        return 1

    corpus_dir = _resolve_corpus_dir(base_path)
    _emit_progress("start",
                    base_path=str(base_path),
                    corpus_dir=str(corpus_dir),
                    has_legacy=_has_legacy_v3(corpus_dir),
                    has_unified=_has_unified_v4(corpus_dir),
                    force=args.force,
                    dry_run=args.dry_run)

    # Path 1: migration legacy → unified
    if _has_legacy_v3(corpus_dir) and not _has_unified_v4(corpus_dir) and not args.force:
        _emit_progress("migrate_v3_to_v4_begin")
        from index_schema_upgrade_v4 import migrate_one
        try:
            from executors.create_images_indices.create_images_indices import _call_vlm
        except Exception:
            _call_vlm = None
        try:
            from virt import get_embedder
            text_embedder = get_embedder("text")
        except Exception as e:
            _emit_progress("warn", note=f"BGE init fallito: {e!r}")
            text_embedder = None
        out = migrate_one(
            corpus_dir,
            vlm_caller=_call_vlm,
            text_embedder=text_embedder,
            dry_run=args.dry_run,
        )
        _emit_progress("migrate_v3_to_v4_done", **out)
        return 0 if out.get("ok") else 1

    # Path 2: full build (force or no legacy)
    _emit_progress("build_unified_begin")
    sys.path.insert(0, str(_THIS.parent / "executors" / "create_images_indices"))
    import create_images_indices as cii
    out = cii.invoke({
        "base_path": str(base_path),
        "force": args.force,
        "dry_run": args.dry_run,
        "max_files": args.max_files,
    })
    _emit_progress("build_unified_done", **{
        k: v for k, v in out.items() if not k.startswith("_") and k != "entries"
    })
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
