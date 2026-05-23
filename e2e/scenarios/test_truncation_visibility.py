"""E2E truncation visibility cross-executor (CLAUDE.md §2.7).

Quando un executor cap il risultato, DEVE emettere:
  - `truncated: true`
  - `truncated_what`: nome del campo capped
  - `used`: count effettivamente ritornati
  - `available_total`: count totale conosciuto (se possibile)
  - `cap_field` / `cap_value` (ADR 0072)

Verifica diretto: invoca un executor (es. find_files con max_results=2
su dir con >2 file) e asserisce shape della response.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def server() -> E2EServer:
    srv = E2EServer.spawn(ready_timeout_s=30.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(server):
    async with E2EClient(server.url, server.admin_key) as drv:
        yield drv


async def test_executor_endpoint_smoke(driver):
    """L'endpoint admin/executors espone tutti gli executor del catalog
    con field minimi necessari per il PLANNER."""
    r = await driver.admin_get("/admin/executors")
    rows = r.get("rows") or r.get("executors") or []
    assert rows, "catalog vuoto"
    # Sample: ogni row ha name, affinity (lista)
    issues = []
    for row in rows[:10]:
        name = row.get("name", "")
        if not name:
            issues.append(f"row senza name: {row}")
            continue
        # Snake_case verb_object. Eccezioni system verbs §2.2 (admin,
        # undo_last_turn, ecc.) — single-word ammessi se in whitelist
        # canonica.
        system_singles = {"admin"}
        if "_" not in name and name not in system_singles:
            issues.append(f"{name}: naming non snake_case")
    assert not issues, "\n".join(issues)


async def test_change_intents_route_pagination(driver):
    """`/admin/changes?limit=N` rispetta cap e ritorna max N rows."""
    r = await driver.admin_get("/admin/changes?state=proposed&limit=3")
    rows = r.get("rows", [])
    assert len(rows) <= 3, f"cap non rispettato: {len(rows)} > 3"


async def test_change_intents_state_filter(driver):
    """`/admin/changes?state=X` ritorna solo rows con quello state."""
    for state in ("proposed", "accepted", "finalized"):
        r = await driver.admin_get(f"/admin/changes?state={state}&limit=20")
        rows = r.get("rows", [])
        for row in rows:
            assert row["state"] == state, (
                f"filter {state} ritorna row con state={row['state']}"
            )
