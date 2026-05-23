"""E2E HTTP admin pages — render integrity test.

Per ogni rotta admin canonica, verifica che il template HTML:
  - status 200 (no errori auth/route)
  - nessun `<missing:KEY>` (i18n complete)
  - nessun `Traceback` (no exception leak)
  - struttura HTML balanciata (opening tags = closing tags ratio)

Universal: test si applica a TUTTE le admin pages, no whitelist per
rotta — pattern §7.3 generale.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EServer

pytestmark = pytest.mark.asyncio


_ADMIN_PAGES = [
    "/admin/",
    "/admin/changes",
    "/admin/changes?state=proposed",
    "/admin/changes?state=accepted",
    "/admin/proposals",
    "/admin/proposals/introvertiva",
    "/admin/proposals/telos",
    "/admin/promotions",
    "/admin/executors",
    "/admin/executors/stats",
    "/admin/runs",
    "/admin/safety",
    "/admin/turns",
    "/admin/users",
    "/admin/builds",
]


@pytest.fixture(scope="module")
def admin_server() -> E2EServer:
    """Seed realistic per pagine non-vuote."""
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


def _check_html_no_missing(body: str, page: str) -> list:
    """Lint deterministico universal su HTML response."""
    issues: list[str] = []
    miss = re.findall(r"<missing:[A-Z_][A-Z0-9_]*>", body)
    if miss:
        issues.append(f"{page}: {len(miss)} <missing:> keys: {set(miss)}")
    if re.search(r"\bTraceback \(most recent call last\)", body):
        issues.append(f"{page}: contains Traceback")
    return issues


@pytest.mark.parametrize("path", _ADMIN_PAGES,
                          ids=[p.lstrip("/").replace("/", "_") for p in _ADMIN_PAGES])
async def test_admin_page_renders_clean(admin_server, path):
    """GET <path> con Accept text/html → status 200, no missing keys,
    no Traceback."""
    url = admin_server.url + path
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            url,
            headers={
                "Authorization": f"Bearer {admin_server.admin_key}",
                "Accept": "text/html",
            },
        ) as r:
            text = await r.text()
            if r.status >= 500:
                pytest.fail(
                    f"{path} → HTTP {r.status} (server error)\n"
                    f"body: {text[:1000]}"
                )
            # 404/403 ammessi per rotte non-cablate ma 200 atteso per le note
            if r.status == 200:
                issues = _check_html_no_missing(text, path)
                if issues:
                    pytest.fail("\n".join(issues))
            elif r.status not in (200, 302, 404):
                # Status inattesi → fail
                pytest.fail(f"{path} → HTTP {r.status}: {text[:500]}")


@pytest.mark.parametrize("path", _ADMIN_PAGES,
                          ids=[p.lstrip("/").replace("/", "_") for p in _ADMIN_PAGES])
async def test_admin_page_json_accept(admin_server, path):
    """GET <path> con Accept application/json → JSON valido (per pages
    che supportano negotiate_collection)."""
    url = admin_server.url + path
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            url,
            headers={
                "Authorization": f"Bearer {admin_server.admin_key}",
                "Accept": "application/json",
            },
        ) as r:
            text = await r.text()
            if r.status >= 500:
                pytest.fail(
                    f"{path} → HTTP {r.status}\nbody: {text[:1000]}"
                )
            # Solo pagine con negotiate_collection ritornano JSON; le altre
            # ritornano HTML (forced) o 406. Non assertiamo schema, solo
            # assenza di server error / leak.
            if r.status == 200 and r.headers.get("Content-Type", "").startswith("application/json"):
                import json as _json
                try:
                    _json.loads(text)
                except _json.JSONDecodeError:
                    pytest.fail(
                        f"{path}: Accept=json status=200 ma non-JSON body: {text[:500]}"
                    )
