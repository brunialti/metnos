"""E2E isolation invariant — verifica che server isolato NON modifichi
storage live durante l'intera sessione test.

Snapshot digest pre/post per ogni file critico di esercizio:
  - ~/.local/share/metnos/i18n.sqlite
  - ~/.local/share/metnos/turn_feedback.jsonl
  - ~/.local/share/metnos/telos_proposals.jsonl
  - ~/.local/share/metnos/skills/google-workspace/google_token.json
  - ~/.local/state/metnos/scheduler_v2.sqlite
  - ~/.local/state/metnos/executor_stats.db
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer

pytestmark = pytest.mark.asyncio


_LIVE_FILES = [
    Path.home() / ".local/share/metnos/i18n.sqlite",
    Path.home() / ".local/share/metnos/turn_feedback.jsonl",
    Path.home() / ".local/share/metnos/telos_proposals.jsonl",
    Path.home() / ".local/share/metnos/skills/google-workspace/google_token.json",
    Path.home() / ".local/share/metnos/skills/google-workspace/SKILL.md",
    Path.home() / ".local/state/metnos/scheduler_v2.sqlite",
    Path.home() / ".local/state/metnos/executor_stats.db",
    Path.home() / ".config/metnos/admin.key",
]


def _digest(p: Path):
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def test_live_files_unchanged_after_full_e2e_session():
    """Snapshot digest dei file live → spawn server seed_realistic +
    operazioni → shutdown → re-snapshot. Nessun digest deve cambiare."""
    before = {str(p): _digest(p) for p in _LIVE_FILES}

    # Sessione tipica: spawn server, run alcuni job, chat, shutdown
    srv = E2EServer.spawn(seed_realistic=True, ready_timeout_s=45.0)
    try:
        async with E2EClient(srv.url, srv.admin_key, timeout_s=60.0) as drv:
            await drv.run_job("change_intent_materialize")
            await drv.run_job("nightly_aging")
            await drv.admin_get("/admin/changes?state=proposed&limit=5")
            await drv.admin_get("/admin/executors")
    finally:
        srv.shutdown(cleanup=True)

    after = {str(p): _digest(p) for p in _LIVE_FILES}

    drift = []
    for path, dig_before in before.items():
        dig_after = after[path]
        if dig_before != dig_after:
            drift.append(
                f"{path}: digest changed ({dig_before} → {dig_after})"
            )
    assert not drift, (
        "Contaminazione esercizio rilevata:\n  " + "\n  ".join(drift)
    )
