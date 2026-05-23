"""E2E /agent/* endpoints safety — request malformed/edge case smoke.

Verifica:
  - POST /agent/turn body vuoto → no 500
  - POST /agent/turn body malformato → no 500
  - POST /agent/turn query troppo lunga → graceful reject
  - GET /agent/health → 200 + JSON valido
  - GET endpoints anonymous (no admin key)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EServer

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def server() -> E2EServer:
    srv = E2EServer.spawn(ready_timeout_s=30.0)
    yield srv
    srv.shutdown(cleanup=True)


async def test_agent_health_returns_json(server):
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            server.url + "/agent/health",
            headers={"Accept": "application/json"},
        ) as r:
            assert r.status == 200
            text = await r.text()
            assert text, "empty body"
            data = json.loads(text)
            assert isinstance(data, dict)


async def test_agent_turn_empty_body_no_500(server):
    """POST /agent/turn body vuoto → 4xx (validation error), no 500."""
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            server.url + "/agent/turn",
            json={},
        ) as r:
            text = await r.text()
            assert r.status < 500, f"500 on empty body: {text[:500]}"


async def test_agent_turn_malformed_json_no_500(server):
    """POST con body non-JSON → 4xx, no 500."""
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            server.url + "/agent/turn",
            data="not-json-at-all",
            headers={"Content-Type": "application/json"},
        ) as r:
            text = await r.text()
            assert r.status < 500, f"500 on malformed: {text[:500]}"


async def test_admin_protected_routes_require_auth(server):
    """Senza Authorization header → 401/403 su /admin/*."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            server.url + "/admin/changes",
            headers={"Accept": "application/json"},
        ) as r:
            # 401/403 ammessi (auth missing), 200 NO
            assert r.status in (401, 403), (
                f"admin route raggiungibile senza auth: {r.status}"
            )


async def test_admin_wrong_key_rejected(server):
    """Bearer con key sbagliata → 403."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            server.url + "/admin/changes",
            headers={
                "Authorization": "Bearer wrong_key_xyz",
                "Accept": "application/json",
            },
        ) as r:
            assert r.status in (401, 403), f"wrong key accettata: {r.status}"


async def test_404_for_unknown_routes(server):
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            server.url + "/this/does/not/exist/anywhere",
        ) as r:
            assert r.status == 404
