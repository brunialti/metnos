#!/usr/bin/env python3
"""
delete_images_indices — executor di Metnos v1.1.

Cancella l'indice persistente unificato delle immagini in `base_path`.
Operazione non reversibile (no blob backup): l'indice si puo' ricostruire da
zero con `create_images_indices`.

Cancella SOLO il derivato persistente in
    `~/.local/share/metnos/index/image/<sha16>/unified/`
Le foto reali sul filesystem non vengono toccate.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

_VALID_IDX_FULL = ("unified",)
_VALID_IDX = _VALID_IDX_FULL + ("all",)


def _index_image_root() -> Path:
    from index_schema import image_index_root
    return image_index_root()


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


def _index_root_for_base(base_path: Path) -> Path:
    # Chiave corpus via SoT condivisa (index_schema.canonical_corpus_path):
    # logica/stabile al mount, coerente con find/create/get (fix 23/6).
    from index_schema import image_corpus_dir
    return image_corpus_dir(base_path)


def _dir_size(path: Path) -> int:
    from index_schema import directory_size
    return directory_size(path)


def invoke(args):
    base_path_arg = args.get("base_path")
    idx = args.get("idx", "all") or "all"
    dry_run = bool(args.get("dry_run", False)) or _is_dry_run()

    if not base_path_arg:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="base_path")}
    if idx not in _VALID_IDX:
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="idx", allowed=", ".join(_VALID_IDX))}

    # NB: NON richiediamo che base_path esista sul filesystem: l'indice
    # potrebbe esistere anche se la collezione e' stata cancellata o
    # spostata altrove. Risolviamo direttamente il path dell'indice.
    base = Path(os.path.expanduser(base_path_arg))
    # NB2: usiamo .resolve(strict=False)-equivalent: Path.resolve() in py3.12
    # con base assente potrebbe sollevare; usiamo un path normalizzato.
    base = Path(os.path.normpath(str(base.absolute() if base.is_absolute()
                                       else (Path.cwd() / base))))
    root = _index_root_for_base(base)

    targets: list[Path] = []
    if idx == "all":
        for sub in _VALID_IDX_FULL:
            targets.append(root / sub)
    else:
        targets.append(root / idx)

    deleted: list[str] = []
    freed_bytes = 0
    for t in targets:
        if not t.exists():
            continue
        size = _dir_size(t)
        freed_bytes += size
        deleted.append(str(t))
        if not dry_run:
            try:
                shutil.rmtree(t)
            except OSError as e:
                return {"ok": False, "error": f"rmtree failed for {t}: {e}"}

    # Se la root e' rimasta vuota dopo aver cancellato tutti i sub,
    # rimuoviamo anche la root (non lasciamo dir orfane in tree).
    if not dry_run and root.exists():
        try:
            if not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass

    return {
        "ok": True,
        "base_path": str(base),
        "idx": idx,
        "deleted": deleted,
        "freed_bytes": int(freed_bytes),
        "dry_run": dry_run,
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
