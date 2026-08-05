"""Missed-fire recovery on daemon boot."""
from __future__ import annotations

import asyncio
import threading
import time

from scheduler_v2.daemon import SchedulerDaemon
from scheduler_v2.models import ScheduleEntry
from scheduler_v2.storage import SchedulerStorage


def test_within_grace_fires_on_boot(db_path):
    fired = {"n": 0}
    lock = threading.Lock()

    def cb(payload):
        with lock:
            fired["n"] += 1

    # Pre-seed the DB without starting a daemon: a one-shot whose fire was
    # 5s ago, with a generous grace window.
    s = SchedulerStorage(db_path)
    e = ScheduleEntry(
        name="missed_in_grace",
        trigger="every_60s",
        next_fire_at=time.time() - 5,
        recurring=False,
        callback_key="cb",
        grace_window_s=300,
    )
    s.upsert(e)
    s.close()

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("cb", cb)
        await d.start()
        await asyncio.sleep(1.5)
        await d.stop()

    asyncio.run(run())
    assert fired["n"] == 1


def test_past_grace_skipped_one_shot(db_path):
    fired = {"n": 0}

    def cb(payload):
        fired["n"] += 1

    s = SchedulerStorage(db_path)
    e = ScheduleEntry(
        name="missed_past_grace",
        trigger="every_60s",
        next_fire_at=time.time() - 3600,
        recurring=False,
        callback_key="cb",
        grace_window_s=60,
    )
    s.upsert(e)
    s.close()

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("cb", cb)
        await d.start()
        await asyncio.sleep(1.0)
        await d.stop()
        return d.storage.get_by_name("missed_past_grace")

    e_after = asyncio.run(run())
    assert fired["n"] == 0
    # one-shot past-grace -> disabled
    assert e_after.enabled is False


def test_past_grace_recurring_advances(db_path):
    fired = {"n": 0}

    def cb(payload):
        fired["n"] += 1

    s = SchedulerStorage(db_path)
    e = ScheduleEntry(
        name="missed_recurring",
        trigger="every_60s",
        next_fire_at=time.time() - 3600,
        recurring=True,
        callback_key="cb",
        grace_window_s=60,
    )
    s.upsert(e)
    s.close()

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("cb", cb)
        await d.start()
        await asyncio.sleep(1.0)
        await d.stop()
        return d.storage.get_by_name("missed_recurring")

    e_after = asyncio.run(run())
    assert fired["n"] == 0  # past grace, no fire
    assert e_after.enabled is True
    # next_fire_at advanced into the future
    assert e_after.next_fire_at > time.time() - 5


def test_no_grace_means_unlimited(db_path):
    """grace_window_s=None should mean 'always within grace' i.e. fire."""
    fired = {"n": 0}

    def cb(payload):
        fired["n"] += 1

    s = SchedulerStorage(db_path)
    e = ScheduleEntry(
        name="no_grace",
        trigger="every_60s",
        next_fire_at=time.time() - 1_000_000,  # ages ago
        recurring=False,
        callback_key="cb",
        grace_window_s=None,
    )
    s.upsert(e)
    s.close()

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("cb", cb)
        await d.start()
        await asyncio.sleep(1.0)
        await d.stop()

    asyncio.run(run())
    assert fired["n"] == 1


def test_crashed_runs_marked_on_boot(db_path):
    """A 'running' row that survived a process death must be marked crashed
    by the loop's first action.
    """
    s = SchedulerStorage(db_path)
    eid = s.upsert(
        ScheduleEntry(
            name="ghostly",
            trigger="every_60s",
            next_fire_at=time.time() + 3600,
            recurring=True,
            callback_key="cb",
        )
    )
    s.begin_run(eid, "ghostly")  # leak: never end_run
    s.close()

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("cb", lambda p: None)
        await d.start()
        await asyncio.sleep(0.3)
        await d.stop()
        return d.storage.list_runs()

    runs = asyncio.run(run())
    assert runs and runs[0].status == "crashed"
