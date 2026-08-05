"""E2E pipeline shape invariant (ADR 0154): `E+ (F|A)?`.

Verifica universal sui turn log post-chat: ogni turno ha shape valida
(executor+ optional final_answer/answer). Test parametrizzato su query
deterministiche.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer

_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _RUN_SLOW,
                        reason="pipeline shape test usa chat reale (slow)"),
]


@pytest.fixture(scope="module")
def server() -> E2EServer:
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(server):
    async with E2EClient(server.url, server.admin_key, timeout_s=180.0) as drv:
        yield drv


_FAST_QUERIES = [
    "che ore sono",
    "che ora e'",
    "where am i",
    "stato del sistema",
]


@pytest.mark.parametrize("query", _FAST_QUERIES)
async def test_pipeline_shape_E_plus_F(driver, query: str):
    """Pipeline post-chat: sequence di chosen_tool deve matchare `E+ F?`.
    Anti-pattern: E F E (final_answer in mezzo)."""
    r = await driver.chat(query, lang="it")
    if r.error:
        pytest.skip(f"chat unreachable: {r.error}")
    tools = [(s.get("tool") or s.get("chosen_tool") or "") for s in r.steps
             if s.get("tool") or s.get("chosen_tool")]
    if not tools:
        pytest.skip(f"no tools called for «{query}» (fast-path L0)")
    seq = ""
    for t in tools:
        seq += "F" if t == "final_answer" else "E"
    assert re.match(r"^E+F?$", seq), (
        f"pipeline shape invalid for «{query}»: {seq} (tools={tools})"
    )


async def test_no_e_after_final_answer(driver):
    """Una volta emesso final_answer, il planner NON deve emettere altri
    executor step (ADR 0154 invariante)."""
    r = await driver.chat("che ore sono", lang="it")
    if r.error or not r.steps:
        pytest.skip("no chat or no steps")
    seen_final = False
    for s in r.steps:
        t = s.get("tool") or s.get("chosen_tool") or ""
        if seen_final and t and t != "final_answer":
            pytest.fail(
                f"executor `{t}` invoked AFTER final_answer (shape violation)"
            )
        if t == "final_answer":
            seen_final = True
