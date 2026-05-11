"""Many entries due at the same instant must all fire promptly."""
from __future__ import annotations

import asyncio
import threading
import time

from scheduler_v2.daemon import SchedulerDaemon
from scheduler_v2.models import ScheduleEntry


def test_50_entries_fire_within_2s(db_path):
    counter = {"n": 0}
    lock = threading.Lock()

    def cb(payload):
        with lock:
            counter["n"] += 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("inc", cb)
        now = time.time() - 1
        for i in range(50):
            e = ScheduleEntry(
                name=f"job_{i}",
                trigger="every_60s",
                next_fire_at=now,
                recurring=False,
                callback_key="inc",
            )
            d.storage.upsert(e)
        await d.start()
        await asyncio.sleep(2.0)
        await d.stop()
        return counter["n"]

    fired = asyncio.run(run())
    assert fired == 50, f"expected all 50 to fire, got {fired}"


def test_max_concurrent_serialization(db_path):
    """A recurring slow job with max_concurrent=1 must not overlap; each
    invocation completes before the next begins.
    """
    overlap = {"max_inflight": 0, "current": 0}
    lock = threading.Lock()

    def slow(payload):
        with lock:
            overlap["current"] += 1
            overlap["max_inflight"] = max(
                overlap["max_inflight"], overlap["current"]
            )
        time.sleep(0.4)
        with lock:
            overlap["current"] -= 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("slow", slow)
        d.schedule("s", "every_1s", "slow", max_concurrent=1)
        await d.start()
        await asyncio.sleep(3.0)
        await d.stop()
        return overlap["max_inflight"]

    assert asyncio.run(run()) == 1
