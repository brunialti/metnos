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
        bound = sys.modules.get(name)
        if bound is not None:
            bound_file = getattr(bound, "__file__", None)
            if (
                not isinstance(bound_file, str)
                or Path(bound_file).resolve() != runtime_file.resolve()
            ):
                raise RuntimeError(
                    f"ambiguous portable module was already bound: {name}"
                )
            continue
        spec = importlib.util.spec_from_file_location(name, runtime_file)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise RuntimeError(f"cannot bind portable runtime module: {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:  # pragma: no cover - preserve the import failure
            del sys.modules[name]
            raise


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Bind shadowed modules only after the repository sandbox is active.

    The repository-wide session hook redirects every mutable Metnos root.
    Running this binding while conftest files are still being imported would
    let product modules freeze the caller's live paths before that redirect.
    ``pytest_sessionstart`` still precedes test collection, while ``trylast``
    makes the ordering against the sandbox hook explicit.
    """
    del session
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
