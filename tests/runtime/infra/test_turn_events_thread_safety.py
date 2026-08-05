"""Concurrent producers must observe a coherent TurnEventLog snapshot."""
from __future__ import annotations

import threading
import sys
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from turn_events import TurnEventLog


def test_concurrent_append_assigns_unique_monotonic_ids():
    log = TurnEventLog()
    log.create("turn-1", conversation_id="conv", actor="host", query="test")

    def _produce(worker):
        for index in range(100):
            log.append("turn-1", "update", {"worker": worker, "index": index})

    threads = [threading.Thread(target=_produce, args=(worker,))
               for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    snapshot = log.snapshot("turn-1")
    assert snapshot is not None
    ids = [event["id"] for event in snapshot["events"]]
    assert ids == list(range(1, 801))
    assert log.stats() == {"total_turns": 1, "active": 1, "closed": 0}


def test_snapshot_is_detached_from_internal_payload():
    log = TurnEventLog()
    log.create("turn-2")
    log.append("turn-2", "update", {"message": "original"})

    snapshot = log.snapshot("turn-2")
    snapshot["events"][0]["payload"]["message"] = "changed"

    assert log.snapshot("turn-2")["events"][0]["payload"]["message"] == "original"
