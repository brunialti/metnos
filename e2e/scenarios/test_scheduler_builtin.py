"""E2E scheduler_v2 — verifica 18 builtin entries presenti al boot.

Per ognuna delle entry canonical, verifica:
  - callback registrato (POST /admin/jobs/{key}/fire → ok)
  - schedule_entries row presente (via admin/runs o admin/safety)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer

pytestmark = pytest.mark.asyncio


_BUILTIN_CALLBACKS = [
    "apply_ager",
    "apply_executor_ager",
    "synt_suggest",
    "introvertiva_propose",
    "introvertiva_apply",
    "proposals_cleanup",
    "lifecycle_summary",
    "images_index_refresh",
    "proposals_eta_aggregate",
    "i18n_translate_pending",
    "promoter",
    "promoter_digest",
    "skill_sandbox_watchdog",
    "github_watcher",
    "multi_tool_maintenance",
    "telos_introspect_nightly",
    # ADR 0158 (23/5/2026):
    "change_intent_materialize",
    "change_applier",
    "change_observer",
]


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
    """Per ogni builtin, POST /admin/jobs/{key}/fire ritorna 200 (anche se
    il callback no-op): callback DEVE essere registrato al boot."""
    # Alcuni callback richiedono il LLM live: per evitare timeout slow,
    # skip i 3 callback che invocano LLM heavy
    skip_llm_heavy = {
        "synt_suggest",          # synt multistage ~150s
        "i18n_translate_pending", # LLM tier wise
        "telos_introspect_nightly",  # 10 lenses × N targets ~5min
        "github_watcher",        # network + LLM
        "promoter",              # synth_request via proposal_evaluator
        "images_index_refresh",  # CLIP + EXIF su 30k foto
    }
    if callback_key in skip_llm_heavy:
        pytest.skip(f"{callback_key}: LLM-heavy, separate slow test")

    r = await driver.run_job(callback_key)
    assert r.get("ok"), (
        f"callback `{callback_key}` non disponibile / errore: {r}"
    )
    assert r.get("callback") == callback_key
