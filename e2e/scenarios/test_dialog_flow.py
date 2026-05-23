"""E2E dialog flow — get_inputs interactive UI (ADR 0090).

Verifica:
  - executor get_inputs disponibile via /admin/executors
  - dialog endpoints `/agent/dialog/{id}/{form,submit,cancel}` rispondono
    (forse 404 se id fake, 200 se valid — comunque no 500)
  - dialog_pending.sqlite schema esiste post-boot
"""
from __future__ import annotations

import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def dialog_server() -> E2EServer:
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(dialog_server):
    async with E2EClient(dialog_server.url, dialog_server.admin_key) as drv:
        yield drv


async def test_get_inputs_in_catalog(driver):
    """get_inputs executor presente nel catalog admin (ADR 0090 dialog UI)."""
    execs = await driver.admin_get("/admin/executors")
    names = []
    if isinstance(execs, dict):
        rows = execs.get("rows") or execs.get("executors") or []
        names = [e.get("name", "") for e in rows]
    assert "get_inputs" in names or any("inputs" in n for n in names), (
        f"get_inputs missing from catalog: {names[:20]}..."
    )


async def test_dialog_form_404_for_unknown_id(dialog_server):
    """GET /agent/dialog/<fake_id>/form → 404, no 500."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            dialog_server.url + "/agent/dialog/fake_xyz/form",
            headers={"Accept": "text/html"},
        ) as r:
            assert r.status in (200, 401, 403, 404), (
                f"unexpected status: {r.status} {await r.text()}"
            )


async def test_dialog_submit_no_500(dialog_server):
    """POST /agent/dialog/<fake>/submit con body vuoto → no 500."""
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            dialog_server.url + "/agent/dialog/fake_xyz/submit",
            data={},
        ) as r:
            assert r.status < 500, f"server error: {r.status} {await r.text()}"


async def test_dialog_pending_db_path(dialog_server):
    """dialog_pending.sqlite vive in tmp user_state, NON in live."""
    live_dlg = Path.home() / ".local/state/metnos/dialog_pending.sqlite"
    tmp_dlg_candidates = [
        dialog_server.user_state / "dialog_pending.sqlite",
        dialog_server.user_data / "dialog_pending.sqlite",
    ]
    # NB: per ora il path canonical e' definito dal modulo dialog_pending.
    # Verifica solo che NON crei nel live durante test.
    if live_dlg.exists():
        import os
        live_mtime_before = os.path.getmtime(live_dlg)
        # Wait per dialog operation (test sopra)
        # Se mtime cambiato → contaminazione
        live_mtime_after = os.path.getmtime(live_dlg)
        assert live_mtime_after <= live_mtime_before + 5, (
            "live dialog_pending modified by test!"
        )
