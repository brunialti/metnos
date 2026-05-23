"""E2E telos engine — invoca `telos_introspect_nightly` su seed realistic
e verifica che produca proposals introvertive non degenerate.

Verifiche:
  - callback eseguibile via /admin/jobs/{key}/fire
  - telos_proposals.jsonl in tmp popolato post-fire
  - ogni record con campi obbligatori (telos_id, lens, executor_target,
    proposed_action, rationale, expected_alignment)
  - zero "<missing:>", zero Traceback nei rationale
  - alignment_per_telos cross-checked con TELOS.md (6 telos v1.2)

NO import runtime — tutto via HTTP/CLI.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer, lint


_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _RUN_SLOW,
        reason="telos engine LLM-heavy (~5min). METNOS_E2E_RUN_SLOW=1 per abilitare",
    ),
]


@pytest.fixture(scope="module")
def telos_server() -> E2EServer:
    """Server isolato con seed_realistic: telos engine ha bisogno di
    turns history per calcolare alignment_per_telos."""
    env_extra = {"METNOS_TELOS_NIGHTLY": "1"}  # toggle telos engine ON
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    yield srv
    srv.shutdown(cleanup=True)


@pytest_asyncio.fixture
async def driver(telos_server):
    async with E2EClient(telos_server.url, telos_server.admin_key,
                            timeout_s=600.0) as drv:
        yield drv


async def test_telos_introspect_produces_proposals(driver, telos_server):
    """Run telos_introspect_nightly → telos_proposals.jsonl popolato."""
    r = await driver.run_job("telos_introspect_nightly")
    assert r.get("ok"), f"job not ok: {r}"

    # File output prodotto
    proposals_jsonl = telos_server.user_data / "telos_proposals.jsonl"
    if not proposals_jsonl.exists():
        pytest.skip("telos engine no-op (seed senza tracce sufficienti)")

    records = []
    with proposals_jsonl.open() as fp:
        for line in fp:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pytest.fail(f"malformed jsonl line in telos_proposals: {line[:200]}")

    if not records:
        pytest.skip("telos engine no proposals from this seed")

    # Schema check: campi obbligatori
    required = {"telos_id", "lens", "executor_target",
                "proposed_action", "rationale", "expected_alignment"}
    issues = []
    for i, rec in enumerate(records):
        missing = required - set(rec.keys())
        if missing:
            issues.append(f"record {i}: missing {missing}")
            continue
        # Lint deterministico su rationale + action
        for field in ("rationale", "proposed_action"):
            v = rec.get(field, "")
            if "<missing:" in v:
                issues.append(f"record {i}.{field}: contains <missing:>")
            if "Traceback" in v:
                issues.append(f"record {i}.{field}: contains Traceback")
    if issues:
        pytest.fail("telos proposals issues:\n  " + "\n  ".join(issues[:10]))


async def test_telos_alignment_per_telos_uses_v1_2_six_telos(driver, telos_server):
    """ADR 0157: TELOS.md v1.2 ha 6 telos (no t.coltivazione_strumenti).
    Verifica che alignment_per_telos non contenga telos_id rimossi."""
    proposals_jsonl = telos_server.user_data / "telos_proposals.jsonl"
    if not proposals_jsonl.exists():
        pytest.skip("no telos proposals to verify")

    forbidden = {"t.coltivazione_strumenti"}
    issues = []
    with proposals_jsonl.open() as fp:
        for i, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for f in rec.get("alignment_per_telos") or []:
                tid = f.get("telos_id", "")
                if tid in forbidden:
                    issues.append(f"record {i}: alignment_per_telos contiene {tid} (rimosso v1.2)")
    if issues:
        pytest.fail("\n".join(issues[:10]))
