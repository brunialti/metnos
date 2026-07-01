#!/usr/bin/env python3
"""skills_paths — helper centralizzato per i path delle skill imported.

ADR 0123 (importer) + ADR 0160 (rename `_imports/` → `skills/`).

Tre root distinti:
  - PATH_SKILLS_BUILTIN      `<install>/executors/skills/` (placeholder repo,
                              future skill builtin shippate con Metnos)
  - PATH_SKILLS_USER         `<user_data>/executors/skills/` (skill imported,
                              destinazione canonica WRITE post-ADR 0160)
  - PATH_SKILLS_USER_LEGACY  `<user_data>/executors/_imports/` (back-compat
                              READ-ONLY per installazioni esistenti)

Tutte le funzioni di scan/lookup ritornano dalla unione dei root attivi.
La directory canonical per i WRITE e' SEMPRE `PATH_SKILLS_USER` (new).
Determinismo §7.9: nessun LLM; pura iterazione filesystem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import config as _C


def skill_roots(*, include_builtin: bool = True,
                include_legacy: bool = True) -> list[Path]:
    """Ritorna i root da cui scansionare le skill imported.

    Ordine: builtin (repo), user (new), legacy (_imports).
    Filtra root inesistenti (no errori, lista vuota possibile).
    """
    roots: list[Path] = []
    if include_builtin and _C.PATH_SKILLS_BUILTIN.is_dir():
        roots.append(_C.PATH_SKILLS_BUILTIN)
    if _C.PATH_SKILLS_USER.is_dir():
        roots.append(_C.PATH_SKILLS_USER)
    if include_legacy and _C.PATH_SKILLS_USER_LEGACY.is_dir():
        roots.append(_C.PATH_SKILLS_USER_LEGACY)
    return roots


def iter_skill_dirs(*, include_builtin: bool = True,
                    include_legacy: bool = True) -> Iterator[Path]:
    """Yield i <skill_dir> (1 livello sotto i root)."""
    for root in skill_roots(include_builtin=include_builtin,
                            include_legacy=include_legacy):
        for skill_dir in sorted(root.iterdir()):
            if skill_dir.is_dir():
                yield skill_dir


def iter_skill_executor_dirs(*, include_builtin: bool = True,
                              include_legacy: bool = True) -> Iterator[Path]:
    """Yield le <executor_dir> contenenti `manifest.toml` (2 livelli sotto root)."""
    for skill_dir in iter_skill_dirs(include_builtin=include_builtin,
                                      include_legacy=include_legacy):
        for ex_dir in sorted(skill_dir.iterdir()):
            if ex_dir.is_dir() and (ex_dir / "manifest.toml").is_file():
                yield ex_dir


def skill_write_root() -> Path:
    """Root canonico per WRITE (import nuovo). Sempre `PATH_SKILLS_USER`."""
    return _C.PATH_SKILLS_USER


def is_skill_path(path: Path) -> bool:
    """True se `path` e' sotto uno dei root skill (qualsiasi)."""
    p = str(path).replace("\\", "/")
    markers = ("/executors/skills/", "/executors/_imports/")
    return any(m in p for m in markers)


def existing_skill_names() -> set[str]:
    """Set di nomi skill (dir name) attualmente presenti sotto qualunque root."""
    out: set[str] = set()
    for skill_dir in iter_skill_dirs():
        out.add(skill_dir.name)
    return out


# Marker di pattern path per filtering rapido (loader, ager, audit).
SKILL_PATH_MARKERS = ("/executors/skills/", "/executors/_imports/")
