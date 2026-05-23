#!/usr/bin/env python3
"""
workspace_policy.py — policy utente di scope per gli hint dei manifest.

Il manifest dichiara `[[capabilities]] hint = [...]` come tetto tecnico
firmato. Per il daily driver l'utente vuole estendere lo scope effettivo
(es. tutta la home) e dichiarare excludes mirati (chiavi, credenziali,
.ssh, ecc.) che restano off-limits anche se il UID del processo li potrebbe
leggere.

File: `~/.config/metnos/workspace_policy.toml`. Schema:

    [host.fs.read]
    scope    = ["~/**", "/opt/**", "/tmp/**"]
    excludes = ["~/.ssh/**", "~/.gnupg/**", "~/.config/metnos/keys/**", ...]

    [host.fs.write]
    scope    = ["~/Documents/**", "~/Downloads/**", "/tmp/**"]
    excludes = []

    [host.network.http]
    scope    = ["github.com", "*.github.com"]
    excludes = []

API:
    load_workspace_policy(path=None) -> dict
    effective_hints(actor, capability, manifest_hints) -> (scope, excludes)
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import config as _C  # §7.11

DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "METNOS_WORKSPACE_POLICY",
        str(_C.PATH_USER_CONFIG / "workspace_policy.toml"),
    )
)
DEFAULT_ACTOR = "host"

_CACHE: dict | None = None
_LOADED_PATH: Path | None = None


def load_workspace_policy(path: Path | None = None) -> dict:
    global _CACHE, _LOADED_PATH
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    _LOADED_PATH = target
    if not target.exists():
        _CACHE = {}
        return _CACHE
    with open(target, "rb") as f:
        _CACHE = tomllib.load(f)
    return _CACHE


def _section(actor: str, capability_name: str) -> dict:
    if _CACHE is None:
        load_workspace_policy()
    if ":" not in capability_name:
        return {}
    family, action = capability_name.split(":", 1)
    actor_section = _CACHE.get(actor, {}) if isinstance(_CACHE, dict) else {}
    family_section = actor_section.get(family, {})
    section = family_section.get(action, {})
    return section if isinstance(section, dict) else {}


def get_scope(actor: str, capability_name: str) -> list[str]:
    sec = _section(actor, capability_name)
    val = sec.get("scope", [])
    return [s for s in val if isinstance(s, str)] if isinstance(val, list) else []


def get_excludes(actor: str, capability_name: str) -> list[str]:
    sec = _section(actor, capability_name)
    val = sec.get("excludes", [])
    return [s for s in val if isinstance(s, str)] if isinstance(val, list) else []


def effective_hints(
    actor: str,
    capability_name: str,
    manifest_hints: list[str],
) -> tuple[list[str], list[str]]:
    """Ritorna (scope, excludes). scope = unione manifest hints + user scope."""
    extra = get_scope(actor, capability_name)
    seen = set()
    merged: list[str] = []
    for h in list(manifest_hints) + extra:
        if h not in seen:
            seen.add(h)
            merged.append(h)
    return merged, get_excludes(actor, capability_name)


def reset_cache():
    global _CACHE, _LOADED_PATH
    _CACHE = None
    _LOADED_PATH = None
