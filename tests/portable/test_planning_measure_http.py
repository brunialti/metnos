"""HTTP wiring only: real authentication/pool, substituted measurement/catalog.

The catalog sentinel represents the provider's already-verified output; these
tests do not qualify signatures, planner behavior, or the measurement lock.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import threading
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


PATH = "/admin/plans/measure"
QUERY = "  Elenca i file del progetto senza modificarli.  "
BODY = {"query": QUERY, "memory": "off"}
KEY = "s017-portable-http-admin-key"


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    # Imports follow the portable session's path isolation, never make_app().
    import config
    import http_auth
    import http_routes_admin
    import planning_measure
    import users

    monkeypatch.setenv("METNOS_USERS_DB", str(tmp_path / "users.db"))
    monkeypatch.setenv("METNOS_TRUST_LAN_ANONYMOUS", "0")
    monkeypatch.setattr(config, "PATH_USER_STATE", tmp_path / "state")
    users.init_db()
    host, = users.list_users(role="host")
    value = SimpleNamespace(
        auth=http_auth, routes=http_routes_admin, users=users,
        owner=host["id"], catalog=[object()], calls=[], provider_calls=[],
        report={"memory": "off", "http_boundary": "measured"},
    )

    def provider():
        value.provider_calls.append(threading.get_ident())
        assert value.pool.stats()["admitted"] > 0
        return value.catalog

    def measure(**kwargs):
        value.calls.append((kwargs, threading.get_ident()))
        return value.report

    value.provider = provider
    monkeypatch.setattr(planning_measure, "run", measure)
    return value


@asynccontextmanager
async def http_client(boundary, *, missing=None, cancelled=None):
    from http_app_state import ADMIN_KEY, CATALOG_PROVIDER, TURN_POOL
    from http_turn_pool import HttpTurnPool

    @web.middleware
    async def observe_disconnect(request, handler):
        try:
            return await handler(request)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.set()
            raise

    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1,
                        admission_timeout_s=0.01)
    boundary.pool = pool
    app = web.Application(
        middlewares=[boundary.auth.auth_middleware, observe_disconnect])
    app[ADMIN_KEY] = KEY
    if missing != "pool":
        app[TURN_POOL] = pool
    if missing != "provider":
        app[CATALOG_PROVIDER] = boundary.provider
    method, path, handler = next(
        route for route in boundary.routes.ROUTES if route[1] == PATH)
    app.router.add_route(method, path, handler)
    try:
        async with TestClient(TestServer(app)) as client:
            yield client
    finally:
        pool.close()


def admin_headers():
    return {"Authorization": f"Bearer {KEY}"}


async def wait_completed(pool, count):
    async def wait():
        while pool.stats()["completed"] < count:
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout=3)


def test_measure_http_passes_query_catalog_and_authenticated_owner(boundary):
    async def run():
        loop_thread = threading.get_ident()
        async with http_client(boundary) as client:
            response = await client.post(PATH, json=BODY, headers=admin_headers())
            assert response.status == 200, await response.text()
            assert await response.json() == boundary.report
            assert response.headers["Cache-Control"] == "no-store"
            (kwargs, worker_thread), = boundary.calls
            assert kwargs == {"query": QUERY, "owner_user_id": boundary.owner,
                              "catalog": boundary.catalog}
            assert kwargs["catalog"] is boundary.catalog
            assert len(boundary.owner) == 16
            int(boundary.owner, 16)
            assert worker_thread != loop_thread
            assert boundary.provider_calls == [worker_thread]
            assert boundary.pool.stats()["completed"] == 1
            assert not boundary.pool._principals
    asyncio.run(run())


@pytest.mark.parametrize("identity,status,error", [
    ("anonymous", 401, "admin_session_required"),
    ("wrong_bearer", 401, "admin_session_required"),
    ("guest_device", 403, "admin_role_required"),
    ("host_device", 403, "admin_role_required"),
])
def test_measure_http_rejects_non_admin_before_admission(
        boundary, identity, status, error):
    headers = {}
    if identity == "wrong_bearer":
        headers = {"Authorization": "Bearer invalid-http-admin-key"}
    elif identity.endswith("_device"):
        owner = boundary.owner
        if identity == "guest_device":
            owner = boundary.users.create_user(
                "portable_guest", owner_user_id=boundary.owner)["id"]
        device = "http_s017_portable_device"
        boundary.users.add_channel(owner, "http", device, verified=True)
        cookie = boundary.auth.issue_user_cookie(KEY, device)
        headers = {"Cookie": f"{boundary.auth.USER_COOKIE}={cookie}"}

    async def run():
        async with http_client(boundary) as client:
            response = await client.post(PATH, json=BODY, headers=headers)
            assert response.status == status, await response.text()
            assert (await response.json())["error"] == error
            assert boundary.pool.stats()["admitted"] == 0
            assert boundary.calls == boundary.provider_calls == []
    asyncio.run(run())


@pytest.mark.parametrize("body", [
    {"query": QUERY, "memory": "on"},
    {"query": QUERY},
    {"query": "  ", "memory": "off"},
    {"query": 7, "memory": "off"},
    {**BODY, "owner_user_id": "forged-owner"},
    [],
])
def test_measure_http_rejects_invalid_memory_query_or_owner(boundary, body):
    async def run():
        async with http_client(boundary) as client:
            response = await client.post(PATH, json=body, headers=admin_headers())
            assert response.status == 400, await response.text()
            assert await response.json() == {"error": "invalid_measure_request"}
            assert boundary.pool.stats()["admitted"] == 0
            assert boundary.calls == boundary.provider_calls == []
    asyncio.run(run())


@pytest.mark.parametrize("missing", ["provider", "pool"])
def test_measure_http_requires_provider_and_real_pool(boundary, missing):
    async def run():
        async with http_client(boundary, missing=missing) as client:
            response = await client.post(PATH, json=BODY, headers=admin_headers())
            assert response.status == 503, await response.text()
            assert await response.json() == {"error": "measure_unavailable"}
            assert boundary.pool.stats()["admitted"] == 0
            assert boundary.calls == boundary.provider_calls == []
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["exception", "empty"])
def test_measure_http_catalog_failure_releases_capacity_without_fallback(
        boundary, failure):
    fault = [failure]
    real_provider = boundary.provider

    def provider():
        if fault[0] == "exception":
            raise RuntimeError("catalog-provider-private-detail")
        if fault[0] == "empty":
            return []
        return real_provider()

    boundary.provider = provider

    async def run():
        async with http_client(boundary) as client:
            response = await client.post(PATH, json=BODY, headers=admin_headers())
            assert response.status == 503, await response.text()
            assert await response.json() == {"error": "planning_measure_failed"}
            assert boundary.calls == []
            assert boundary.pool.stats()["completed"] == 1
            assert not boundary.pool._principals
            fault[0] = None
            response = await client.post(PATH, json=BODY, headers=admin_headers())
            assert response.status == 200, await response.text()
            assert len(boundary.calls) == 1
            assert boundary.pool.stats()["completed"] == 2
    asyncio.run(run())


def test_measure_http_disconnect_keeps_capacity_until_worker_finishes(
        boundary, monkeypatch):
    import planning_measure

    started, finish = threading.Event(), threading.Event()

    def blocking_measure(**kwargs):
        boundary.calls.append((kwargs, threading.get_ident()))
        started.set()
        if not finish.wait(timeout=10):
            raise AssertionError("HTTP test did not release its worker")
        return boundary.report

    monkeypatch.setattr(planning_measure, "run", blocking_measure)

    async def run():
        cancelled = asyncio.Event()
        async with http_client(boundary, cancelled=cancelled) as client:
            _, writer = await asyncio.open_connection(
                client.server.host, client.server.port)
            payload = json.dumps(BODY).encode("utf-8")
            writer.write((
                f"POST {PATH} HTTP/1.1\r\nHost: localhost\r\n"
                f"Authorization: Bearer {KEY}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode("ascii") + payload)
            try:
                await writer.drain()
                assert await asyncio.to_thread(started.wait, 3)
                for disconnected in (False, True):
                    if disconnected:
                        writer.close()
                        await writer.wait_closed()
                        await asyncio.wait_for(cancelled.wait(), timeout=3)
                    response = await client.post(
                        PATH, json=BODY, headers=admin_headers())
                    assert response.status == 503, await response.text()
                    assert await response.json() == {
                        "error": "turn_capacity_exhausted", "retry_after_s": 1}
                    assert response.headers["Retry-After"] == "1"
                    assert boundary.pool.stats()["admitted"] == 1
                    assert boundary.pool.stats()["completed"] == 0
                    assert len(boundary.calls) == len(boundary.provider_calls) == 1
                finish.set()
                await wait_completed(boundary.pool, 1)
                response = await client.post(
                    PATH, json=BODY, headers=admin_headers())
                assert response.status == 200, await response.text()
                assert await response.json() == boundary.report
                assert boundary.pool.stats()["completed"] == 2
                assert not boundary.pool._principals
            finally:
                finish.set()
                writer.close()
                await writer.wait_closed()
                if started.is_set():
                    await wait_completed(boundary.pool, 1)
    asyncio.run(run())
