"""Typed keys for shared aiohttp application state.

aiohttp deprecates string keys because unrelated modules can silently collide.
The helpers retain read compatibility with the small plain-dict fakes used by
unit tests while every real ``web.Application`` write uses ``web.AppKey``.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, MutableMapping
from typing import Any

from aiohttp import web


STARTED_AT = web.AppKey("started_at", float)
ADMIN_KEY = web.AppKey("admin_key", str)
CATALOG_PROVIDER = web.AppKey("catalog_provider", Callable)
SSE_RESPONSES = web.AppKey("sse_responses", set)
SCHEDULER_V2 = web.AppKey("scheduler_v2", object)
BUILD_HEALTHCHECK_TASK = web.AppKey("build_healthcheck_task", asyncio.Task)
BUILD_DISPATCHER_TASK = web.AppKey("build_dispatcher_task", asyncio.Task)
BUILD_SWEEPER_TASK = web.AppKey("build_sweeper_task", asyncio.Task)
DIALOG_SWEEPER_TASK = web.AppKey("dialog_sweeper_task", asyncio.Task)

_LEGACY_NAMES = {
    STARTED_AT: "started_at",
    ADMIN_KEY: "admin_key",
    CATALOG_PROVIDER: "catalog_provider",
    SSE_RESPONSES: "sse_responses",
    SCHEDULER_V2: "scheduler_v2",
    BUILD_HEALTHCHECK_TASK: "build_healthcheck_task",
    BUILD_DISPATCHER_TASK: "build_dispatcher_task",
    BUILD_SWEEPER_TASK: "build_sweeper_task",
    DIALOG_SWEEPER_TASK: "dialog_sweeper_task",
}


def app_get(app: MutableMapping, key: web.AppKey, default: Any = None) -> Any:
    """Read typed production state, falling back to legacy test doubles."""
    if key in app:
        return app[key]
    return app.get(_LEGACY_NAMES[key], default)


def app_setdefault(app: MutableMapping, key: web.AppKey, default: Any) -> Any:
    """Set typed state unless a legacy test double already supplies it."""
    if key in app:
        return app[key]
    legacy = _LEGACY_NAMES[key]
    if legacy in app:
        return app[legacy]
    app[key] = default
    return default
