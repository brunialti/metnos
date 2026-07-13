"""Daemon loop end-to-end: register, fire, observe."""
from __future__ import annotations

import asyncio
import threading
import time


from scheduler_v2.daemon import SchedulerDaemon


def test_every_1s_fires_multiple_times(db_path):
    counter = {"n": 0}
    lock = threading.Lock()

    def cb(payload):
        with lock:
            counter["n"] += 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("inc", cb, "increments")
        d.schedule("t", "every_1s", "inc")
        await d.start()
        await asyncio.sleep(3.5)
        await d.stop()
        return counter["n"]

    fired = asyncio.run(run())
    # Loop wakes immediately on schedule (next_fire_at = now+1), then fires
    # ~3 times in 3.5s; assert >= 2 to avoid flakes on slow CI.
    assert fired >= 2, f"expected >=2 fires, got {fired}"


def test_async_callback_awaited(db_path):
    counter = {"n": 0}

    async def cb(payload):
        counter["n"] += 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("ainc", cb)
        d.schedule("t", "every_1s", "ainc", is_async=True)
        await d.start()
        await asyncio.sleep(2.5)
        await d.stop()
        return counter["n"]

    fired = asyncio.run(run())
    assert fired >= 1


def test_one_shot_disables_after_fire(db_path):
    fired = {"n": 0}

    def cb(payload):
        fired["n"] += 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("once", cb)
        # Schedule one-shot in the very near past; will fire immediately.
        from scheduler_v2.models import ScheduleEntry
        e = ScheduleEntry(
            name="single",
            trigger="every_60s",
            next_fire_at=time.time() - 1,
            recurring=False,
            callback_key="once",
        )
        d.storage.upsert(e)
        await d.start()
        await asyncio.sleep(1.5)
        await d.stop()
        e2 = d.storage.get_by_name("single")
        return fired["n"], e2.enabled

    n, enabled = asyncio.run(run())
    assert n == 1
    assert enabled is False


def test_unknown_callback_marks_error(db_path):
    async def run():
        d = SchedulerDaemon(db_path)
        # NOTE: do NOT register "ghost"
        from scheduler_v2.models import ScheduleEntry
        e = ScheduleEntry(
            name="orphan",
            trigger="every_60s",
            next_fire_at=time.time() - 1,
            recurring=False,
            callback_key="ghost",
        )
        d.storage.upsert(e)
        await d.start()
        await asyncio.sleep(1.0)
        await d.stop()
        e2 = d.storage.get_by_name("orphan")
        return e2.last_status, e2.last_error

    status, err = asyncio.run(run())
    assert status == "error"
    assert err and "ghost" in err


def test_structured_callback_outcome_marks_semantic_error(db_path):
    async def run():
        from scheduler_v2.models import CallbackOutcome, ScheduleEntry
        d = SchedulerDaemon(db_path)
        d.callbacks.register(
            "semantic_fail",
            lambda _payload: CallbackOutcome(
                status="error", output="notification delivered",
                error="upstream network failure"),
        )
        d.storage.upsert(ScheduleEntry(
            name="semantic", trigger="every_60s",
            next_fire_at=time.time() - 1, recurring=False,
            callback_key="semantic_fail"))
        await d.start()
        await asyncio.sleep(1.0)
        await d.stop()
        entry = d.storage.get_by_name("semantic")
        return entry.last_status, entry.last_error

    status, err = asyncio.run(run())
    assert status == "error"
    assert err == "upstream network failure"


def test_structured_callback_outcome_rejects_unknown_status(db_path):
    async def run():
        from scheduler_v2.models import CallbackOutcome, ScheduleEntry
        d = SchedulerDaemon(db_path)
        d.callbacks.register(
            "bad_status", lambda _payload: CallbackOutcome(status="maybe"))
        d.storage.upsert(ScheduleEntry(
            name="bad-status", trigger="every_60s",
            next_fire_at=time.time() - 1, recurring=False,
            callback_key="bad_status"))
        await d.start()
        await asyncio.sleep(1.0)
        await d.stop()
        entry = d.storage.get_by_name("bad-status")
        return entry.last_status, entry.last_error

    status, err = asyncio.run(run())
    assert status == "error"
    assert err == "invalid callback status: 'maybe'"


def test_timeout_marks_timeout_status(db_path):
    async def run():
        d = SchedulerDaemon(db_path)

        async def slow(payload):
            await asyncio.sleep(5)

        d.callbacks.register("slow", slow)
        from scheduler_v2.models import ScheduleEntry
        e = ScheduleEntry(
            name="t_slow",
            trigger="every_60s",
            next_fire_at=time.time() - 1,
            recurring=False,
            callback_key="slow",
            is_async=True,
            timeout_s=1,
        )
        d.storage.upsert(e)
        await d.start()
        await asyncio.sleep(2.0)
        await d.stop()
        return d.storage.get_by_name("t_slow").last_status

    status = asyncio.run(run())
    assert status == "timeout"


def test_kick_wakes_loop_immediately(db_path):
    """If we schedule a near-immediate task after start, kick() should make
    the loop pick it up without waiting for its full sleep cycle.
    """
    fired = {"n": 0}

    def cb(payload):
        fired["n"] += 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("k", cb)
        await d.start()
        # No entry yet — loop is idle. Add one with next_fire_at = now-1
        # and rely on schedule() calling kick().
        from scheduler_v2.models import ScheduleEntry
        e = ScheduleEntry(
            name="k_now",
            trigger="every_60s",
            next_fire_at=time.time() - 1,
            recurring=False,
            callback_key="k",
        )
        d.storage.upsert(e)
        d.kick()
        await asyncio.sleep(1.5)
        await d.stop()
        return fired["n"]

    assert asyncio.run(run()) == 1
