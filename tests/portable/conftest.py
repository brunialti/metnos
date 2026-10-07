"""Shared bootstrap and non-empty-suite gate for public portable tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


PORTABLE_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PORTABLE_ROOT.parents[1] / "runtime"
# Shared portable fixtures must also resolve under pytest's importlib mode.
sys.path.insert(0, str(PORTABLE_ROOT))
sys.path.insert(0, str(RUNTIME_ROOT))


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Isolate runtime imports even when the private bootstrap is absent."""
    if getattr(session, "_metnos_test_session_started", False):
        return
    root = session.config._tmp_path_factory.getbasetemp() / "metnos-state"
    paths = {
        "METNOS_USER_DATA": root / "data",
        "METNOS_USER_STATE": root / "state",
        "METNOS_USER_CONFIG": root / "config",
        "METNOS_USER_CACHE": root / "cache",
        "METNOS_WORKSPACE": root / "workspace",
        "METNOS_INDEX_ROOT": root / "data" / "index",
        "METNOS_LOG_FILE": root / "state" / "metnos.log",
        "METNOS_SCHEDULER_V2_DB": root / "state" / "scheduler_v2.sqlite",
        "METNOS_HTTP_LOCKFILE": root / "state" / "http_server.lock",
    }
    isolation = pytest.MonkeyPatch()
    session.config.add_cleanup(isolation.undo)
    for name, path in paths.items():
        (path.parent if path.suffix else path).mkdir(parents=True, exist_ok=True)
        isolation.setenv(name, os.fspath(path))
    isolation.setenv("METNOS_OWNER_USER_ID", "pytest-portable-owner")
    isolation.setenv("METNOS_SITES_FRONTIER_ROUTES", "0")


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
