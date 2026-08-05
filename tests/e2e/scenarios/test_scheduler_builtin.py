"""E2E scheduler_v2 — verifica che ogni builtin entry sia presente al boot.

Per ognuna delle entry canoniche verifica che il callback sia registrato
tramite introspezione HTTP, senza eseguire operazioni di manutenzione.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# runtime su path per derivare la lista builtin dalla FONTE DI VERITA'
# (§7.3 universale): cosi' il test non resta stale a ogni consolidamento
# (ADR 0167 ha fuso apply_*ager+synt_suggest→nightly_aging, +state_reaper, ...).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))

from driver import E2EClient, E2EServer
from scheduler_v2.builtin_callbacks import _BUILTIN_JOBS

pytestmark = pytest.mark.asyncio


_BUILTIN_CALLBACKS = [j["callback_key"] for j in _BUILTIN_JOBS]


@pytest.fixture(scope="module")
def server() -> E2EServer:
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(server):
    async with E2EClient(server.url, server.admin_key, timeout_s=300.0) as drv:
        yield drv


@pytest.mark.parametrize("callback_key", _BUILTIN_CALLBACKS)
async def test_callback_registered(driver, callback_key: str):
    """Every builtin callback is observable without executing side effects."""
    r = await driver.admin_get(f"/admin/jobs/{callback_key}")
    assert r.get("ok"), (
        f"callback `{callback_key}` non disponibile / errore: {r}"
    )
    assert r.get("callback") == callback_key
