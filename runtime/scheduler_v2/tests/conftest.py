"""Shared fixtures for scheduler_v2 tests."""
from __future__ import annotations

import asyncio
import socket
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


@pytest.fixture
def require_asyncio_thread_wakeup():
    """Skip where a sandbox forbids asyncio's cross-thread wakeup socket.

    ``loop.run_in_executor`` completes through the event-loop self-pipe.  A
    sandbox may allow Python threads yet deny ``socketpair()``, leaving that
    completion permanently unreadable.  This is an infrastructure limit, not
    an orchestrator failure; authorized/full environments exercise the path.
    """
    left = right = None
    try:
        left, right = socket.socketpair()
        left.settimeout(0.25)
        right.settimeout(0.25)
        left.sendall(b"x")
        if right.recv(1) != b"x":
            pytest.skip("asyncio thread wakeup socketpair is not usable")
    except OSError as exc:
        pytest.skip(f"asyncio thread wakeup unavailable: {exc}")
    finally:
        if left is not None:
            left.close()
        if right is not None:
            right.close()
