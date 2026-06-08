"""Shared fixtures for scheduler_v2 tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make the package importable as `scheduler_v2` regardless of working dir.
_ROOT = Path(__file__).resolve().parents[2]  # <install_root>/runtime
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "scheduler_v2.sqlite"


def run_async(coro):
    """Helper: run a coroutine to completion. Avoids pytest-asyncio dep."""
    return asyncio.run(coro)
