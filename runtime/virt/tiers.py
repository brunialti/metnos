"""virt.tiers — risoluzione config dei modelli, stile `llm_router`.

`~/.config/metnos/<kind>_tiers.toml` con sezioni flat per ruolo:

    [text]            # embedding_tiers.toml
    provider = "bge"

    [default]         # vlm_tiers.toml
    provider = "llamacpp"
    base_url = "http://127.0.0.1:8081"

Cambiare modello = editare il TOML, mai il codice. Niente registry/DI: una
funzione che legge il config e fonde sui default. `base_url`/`endpoint` alias.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover (py<3.11)
    tomllib = None  # type: ignore

import config as _C  # §7.11 rename-resilient


def _config_path(kind: str) -> Path:
    """env METNOS_<KIND>_TIERS_CONFIG > ~/.config/metnos/<kind>_tiers.toml."""
    import os
    env = os.environ.get(f"METNOS_{kind.upper()}_TIERS_CONFIG")
    if env:
        return Path(env)
    return _C.PATH_USER_CONFIG / f"{kind}_tiers.toml"


def _load(kind: str) -> dict:
    path = _config_path(kind)
    if not path.exists() or tomllib is None:
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def spec(kind: str, role: str, defaults: dict[str, dict]) -> dict[str, Any]:
    """Spec risolta per (kind, role): config flat `[role]` sovrascrive i
    default baked-in. `base_url`→`endpoint` alias. Fallback al ruolo di
    default del kind se `role` assente."""
    cfg = _load(kind)
    section = cfg.get(role) if isinstance(cfg.get(role), dict) else None
    base = dict(defaults.get(role) or next(iter(defaults.values()), {}))
    if section:
        base.update(section)
    if "endpoint" not in base and "base_url" in base:
        base["endpoint"] = base["base_url"]
    return base
