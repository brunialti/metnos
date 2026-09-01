"""Shared bootstrap and non-empty-suite gate for public portable tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


PORTABLE_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PORTABLE_ROOT.parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))


def _bind_runtime_modules_by_name() -> None:
    """Bind the runtime copy of every shadowed module name, once, up front.

    Two product files can share a base name — today
    ``executor_birth_startup_gate.py`` exists under both ``runtime`` and
    ``install``.  A bare import then resolves to whichever module the process
    loaded first, so the same test file collects or fails depending on what
    else runs beside it, and a green run does not say which of the two was
    exercised.  Binding the runtime copy here makes the bare name mean one
    thing for the whole session.  Install-side modules are imported by their
    package path (``install.<name>``) and are unaffected.
    """
    import importlib.util

    install_root = PORTABLE_ROOT.parents[1] / "install"
    for runtime_file in RUNTIME_ROOT.glob("*.py"):
        if not (install_root / runtime_file.name).exists():
            continue
        name = runtime_file.stem
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, runtime_file)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:  # pragma: no cover - leave resolution to the import
            del sys.modules[name]


_bind_runtime_modules_by_name()


def pytest_collection_finish(session: pytest.Session) -> None:
    """Do not let an empty public certification suite appear successful."""
    portable_items = (
        item
        for item in session.items
        if Path(str(item.path)).resolve().is_relative_to(PORTABLE_ROOT)
    )
    if next(portable_items, None) is None:
        raise pytest.UsageError(
            "tests/portable contains no real tests; M4 certification cannot run"
        )
