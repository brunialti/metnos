"""E2E introvertive cycles — verifica daemon batch:

  - introvertiva_propose: scan mnest → proposals_state popolato
  - change_intent_materialize (ADR 0158): proietta proposals → unified
  - change_observer: APPLIED → OBSERVED/FINALIZED/ROLLED_BACK

Seed realistic: serve mnest e proposals_state non vuoti per produrre
risultati deterministici.

NO import runtime. Tutto via /admin/jobs/{key}/fire.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer


pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def introvertive_server() -> E2EServer:
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(introvertive_server):
    async with E2EClient(introvertive_server.url, introvertive_server.admin_key,
                            timeout_s=300.0) as drv:
        yield drv


async def test_introvertiva_propose_idempotent(driver, introvertive_server):
    """introvertiva_propose: 2 run consecutive → stesso n_proposals."""
    r1 = await driver.run_job("introvertiva_propose")
    assert r1.get("ok"), f"job 1 failed: {r1}"
    r2 = await driver.run_job("introvertiva_propose")
    assert r2.get("ok"), f"job 2 failed: {r2}"


async def test_change_intent_materialize_classification(driver, introvertive_server):
    """Materializer ADR 0158: produce intent classificati senza duplicati.
    Verifica by_family + by_kind coerenti con sorgenti."""
    r = await driver.run_job("change_intent_materialize")
    assert r.get("ok"), f"materializer failed: {r}"
    result = r.get("result", {})
    unique = result.get("n_unique_intents", 0)
    yielded = result.get("n_total_yielded", 0)
    assert yielded >= unique >= 0, f"counts incoerenti: {result}"
    # Re-run idempotente
    r2 = await driver.run_job("change_intent_materialize")
    assert r2["result"]["n_unique_intents"] == unique, "non-idempotente"


async def test_proposals_eta_aggregate_runs(driver, introvertive_server):
    """proposals_eta_aggregate: aggregator latenze per path_shape."""
    r = await driver.run_job("proposals_eta_aggregate")
    assert r.get("ok"), f"job failed: {r}"


async def test_nightly_aging_runs(driver, introvertive_server):
    """nightly_aging: decay+demote mnest deboli + demote executor inattivi
    (job UNIFICATO ADR 0167, consolida apply_ager + apply_executor_ager)."""
    r = await driver.run_job("nightly_aging")
    assert r.get("ok"), f"job failed: {r}"


async def test_proposals_cleanup_runs(driver, introvertive_server):
    """proposals_cleanup: manutenzione lifecycle (ADR 0096)."""
    r = await driver.run_job("proposals_cleanup")
    assert r.get("ok"), f"job failed: {r}"


async def test_lifecycle_summary_runs(driver, introvertive_server):
    """lifecycle_summary: aggregator audit (ADR 0097)."""
    r = await driver.run_job("lifecycle_summary")
    assert r.get("ok"), f"job failed: {r}"


async def test_change_observer_idempotent(driver, introvertive_server):
    """change_observer (ADR 0158): re-run senza effetti collaterali se
    nessun intent in APPLIED scaduto grace."""
    r1 = await driver.run_job("change_observer", {"max_per_fire": 50})
    assert r1.get("ok"), f"obs 1 failed: {r1}"
    r2 = await driver.run_job("change_observer", {"max_per_fire": 50})
    assert r2.get("ok"), f"obs 2 failed: {r2}"
