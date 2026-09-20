"""Shared bootstrap and non-empty-suite gate for public portable tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


PORTABLE_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PORTABLE_ROOT.parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))


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
