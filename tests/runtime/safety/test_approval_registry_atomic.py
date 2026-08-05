"""Approval capability consumption is one-shot across concurrent workers."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import approval_registry as registry


def test_resolve_is_atomic_for_same_token(tmp_path):
    db = tmp_path / "approvals.db"
    pending = registry.create_pending(
        channel="telegram", sender_id="42", capability_class="outbound",
        action_verb="send", target_summary="message", db_path=db,
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def _resolve():
        barrier.wait()
        try:
            registry.resolve(
                pending.token, "approved", by_channel="telegram",
                by_sender="42", db_path=db)
            outcomes.append("ok")
        except registry.ApprovalError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=_resolve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["ok", "rejected"]
    assert registry.get_pending(pending.token, db_path=db).status == "approved"
