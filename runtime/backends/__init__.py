"""Resolve registered built-in providers without constructing import paths."""
from __future__ import annotations

_LOADED: dict[tuple[str, str], object | None] = {}


def _load_declared(area: str, provider: str):
    """Keep lazy, statically reviewable provider wiring in one release-owned place.

    A new provider adds one declaration here, never a branch in every executor.
    Imports stay lazy so an unused provider cannot break another provider's
    startup. Missing dependencies of a declared provider remain real errors.
    """
    match (area, provider):
        case ("files", "local"):
            from .files import local as module
        case ("files", "google_workspace"):
            from .files import google_workspace as module
        case ("files", "github"):
            from .files import github as module
        case ("issues", "github"):
            from .issues import github as module
        case ("pulls", "github"):
            from .pulls import github as module
        case ("comments", "github"):
            from .comments import github as module
        case ("workflows", "github"):
            from .workflows import github as module
        case _:
            return None
    return module


def load(area: str, provider: str):
    """Return a provider, or None if absent; expose broken installed imports."""
    key = (area, provider)
    if key in _LOADED:
        return _LOADED[key]
    module = _load_declared(area, provider)
    _LOADED[key] = module
    return module
