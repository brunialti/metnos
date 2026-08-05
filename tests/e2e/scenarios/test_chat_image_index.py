"""High-fidelity chat regression for the semantic image index."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer, lint  # noqa: E402


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("METNOS_E2E_RUN_SLOW", "0") != "1",
        reason="semantic image chat uses the realistic index",
    ),
]


@pytest.fixture(scope="module")
def image_server() -> E2EServer:
    srv = E2EServer.spawn(seed_realistic=True)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def image_driver(image_server: E2EServer):
    async with E2EClient(image_server.url, image_server.admin_key,
                         timeout_s=300.0) as drv:
        yield drv


async def test_semantic_image_query_runs_inside_sandbox(image_driver):
    response = await image_driver.chat("cerca foto con primi piani", lang="it")
    assert not response.error, response.error
    text = response.final_text or response.final_html or ""
    clean = lint.check_response(text, expected_lang="it")
    assert clean.ok, clean.fail_message()
    tools = [
        step.get("tool") or step.get("chosen_tool") or ""
        for step in response.steps
    ]
    assert "find_images_indices" in tools, tools
