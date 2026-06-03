"""E2E scheduler_v2 — verifica che ogni builtin entry sia presente al boot.

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
# runtime su path per derivare la lista builtin dalla FONTE DI VERITA'
# (§7.3 universale): cosi' il test non resta stale a ogni consolidamento
# (ADR 0167 ha fuso apply_*ager+synt_suggest→nightly_aging, +state_reaper, ...).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))

from driver import E2EClient, E2EServer
from scheduler_v2.builtin_callbacks import _BUILTIN_JOBS

pytestmark = pytest.mark.asyncio


_BUILTIN_CALLBACKS = [j["callback_key"] for j in _BUILTIN_JOBS]


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
        "i18n_translate_pending",      # LLM tier wise
        "telos_introspect_nightly",    # 10 lenses × N targets ~5min
        "intent_classifier_retrain",   # retrain Qwen-Emb (ADR 0167)
        "github_watcher",              # network + LLM
        "promoter",                    # synth_request via proposal_evaluator
        "images_index_refresh",        # CLIP + EXIF su 30k foto
    }
    if callback_key in skip_llm_heavy:
        pytest.skip(f"{callback_key}: LLM-heavy, separate slow test")

    r = await driver.run_job(callback_key)
    assert r.get("ok"), (
        f"callback `{callback_key}` non disponibile / errore: {r}"
    )
    assert r.get("callback") == callback_key
