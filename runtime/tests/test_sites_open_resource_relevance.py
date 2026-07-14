"""Il gate open_sites promuove solo navigazioni documento top-level."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


class _Frame:
    def __init__(self, url: str, parent_frame=None):
        self.url = url
        self.parent_frame = parent_frame


class _Request:
    resource_type = "document"

    def __init__(self, url: str, frame):
        self.url = url
        self.frame = frame

    def is_navigation_request(self):
        return True


class _Route:
    def __init__(self):
        self.action = ""

    async def continue_(self):
        self.action = "continue"

    async def abort(self):
        self.action = "abort"


class _Context:
    def __init__(self, request_factory):
        self.guard = None
        self.closed = False
        self.page = _Page(self, request_factory)

    async def add_init_script(self, _script):
        return None

    async def route(self, _pattern, guard):
        self.guard = guard

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class _Page:
    def __init__(self, context, request_factory):
        self.context = context
        self.request_factory = request_factory
        self.url = "about:blank"

    async def goto(self, url, **_kwargs):
        self.url = url
        route = _Route()
        await self.context.guard(route, self.request_factory(url))
        assert route.action == "abort"

    async def wait_for_timeout(self, _milliseconds):
        return None

    async def title(self):
        return "Page"


class _Browser:
    def __init__(self, request_factory):
        self.request_factory = request_factory
        self.contexts = []

    async def new_context(self, **_kwargs):
        context = _Context(self.request_factory)
        self.contexts.append(context)
        return context


def _provider_of(browser):
    # ADR 0191 B1: il broker riceve un BrowserProvider async, non un browser.
    async def _p(_stealth=False):
        return browser
    return _p


def test_third_party_subframe_document_is_blocked_without_open_gate(
        monkeypatch):
    from playwright_sidecar import session_broker as broker

    def request_factory(root_url):
        top = _Frame(root_url)
        child = _Frame("https://frame.external.test/widget", top)
        return _Request("https://frame.external.test/widget", child)

    browser = _Browser(request_factory)
    monkeypatch.setattr(broker, "_browser_provider", _provider_of(browser))
    broker._sessions.clear()
    broker._pending_opens.clear()

    out = asyncio.run(broker.op_open(
        owner="resource-test", url="https://shop.example"))

    assert out["ok"] is True
    observation = broker._sessions[out["session_id"]]["blocked_requests"][
        "frame.external.test"]
    assert observation["types"] == {"document"}
    assert observation["navigation"] is True
    assert observation["main_frame"] is False
    asyncio.run(broker.op_close(
        owner="resource-test", session_id=out["session_id"]))


def test_blocked_top_level_document_still_requires_exact_gate(monkeypatch):
    from playwright_sidecar import session_broker as broker

    def request_factory(root_url):
        top = _Frame(root_url)
        return _Request("https://login.shop.example/start", top)

    browser = _Browser(request_factory)
    monkeypatch.setattr(broker, "_browser_provider", _provider_of(browser))
    broker._sessions.clear()
    broker._pending_opens.clear()

    out = asyncio.run(broker.op_open(
        owner="resource-test", url="https://shop.example"))

    assert out["ok"] is False
    assert out["error_class"] == "approval_required"
    assert out["extra_hosts"] == ["login.shop.example"]
    assert out["blocked_resource_types"] == {
        "login.shop.example": ["document"]}
    assert browser.contexts[0].closed is True
    assert broker._sessions == {}
    broker._pending_opens.clear()
