"""F13 HTTP boundary for the closed LRE deployment gate."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

import http_routes_admin


def test_lre_feature_route_is_exact_and_closed():
    routes = {
        (method, path, handler.__name__)
        for method, path, handler in http_routes_admin.ROUTES
    }

    assert (
        "POST",
        r"/admin/services/durable_workloads/feature/{action:enable|disable}",
        "admin_lre_feature_action",
    ) in routes


@pytest.mark.parametrize(
    ("action", "enabled"), (("enable", True), ("disable", False)),
)
def test_lre_feature_route_passes_only_a_boolean_and_redirects(
        monkeypatch, action, enabled):
    seen = []

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    # This unit test verifies only the closed HTTP boundary.  Keep the actual
    # thread-pool lifecycle in the service integration tests: restricted test
    # sandboxes may not provide a worker thread to ``asyncio.to_thread``.
    monkeypatch.setattr(http_routes_admin.asyncio, "to_thread", run_inline)
    monkeypatch.setattr(
        http_routes_admin.services_registry,
        "configure_lre_feature",
        lambda value: (seen.append(value) or (True, "")),
    )

    with pytest.raises(web.HTTPFound) as raised:
        asyncio.run(http_routes_admin.admin_lre_feature_action(
            SimpleNamespace(match_info={"action": action}),
        ))

    assert seen == [enabled]
    assert raised.value.location == (
        "/admin/services?notice=accepted&service=durable_workloads&"
        f"action={action}_feature#service-durable_workloads"
    )
