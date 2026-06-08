"""kick() must be safe to call from a thread other than the loop's thread."""
from __future__ import annotations

import asyncio
import threading

from scheduler_v2.daemon import SchedulerDaemon


def test_kick_from_worker_thread_wakes_loop(db_path):
    fired = {"n": 0}
    fire_evt = threading.Event()

    def cb(payload):
        fired["n"] += 1
        fire_evt.set()

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("inc", cb)
        await d.start()
        # Schedule far in the future; the only way to fire fast is via kick
        # forcing the loop to re-check (we then bring the timer forward via
        # a direct storage update + kick from a worker thread).
        d.schedule("t", "every_3600s", "inc")
        # Move next_fire_at to "now" without going through the loop's API.
        entry = d.storage.get_by_name("t")
        assert entry is not None and entry.id is not None
        d.storage.update_next_fire(entry.id, 0.0)

        kick_error: list = []

        def worker():
            try:
                d.kick()
            except Exception as exc:  # noqa: BLE001
                kick_error.append(exc)

        threading.Thread(target=worker, daemon=True).start()
        # Wait for the fire to actually happen (or timeout).
        await asyncio.sleep(2.0)
        await d.stop()
        return fired["n"], kick_error

    n, errors = asyncio.run(run())
    assert errors == [], f"kick from worker thread raised: {errors!r}"
    assert n >= 1, f"expected fire after cross-thread kick, got n={n}"


def test_kick_from_loop_thread_still_works(db_path):
    """Same-thread kick path: must still set the event without raising."""
    fired = {"n": 0}

    def cb(payload):
        fired["n"] += 1

    async def run():
        d = SchedulerDaemon(db_path)
        d.callbacks.register("inc", cb)
        await d.start()
        d.schedule("t", "every_1s", "inc")
        # Manually kick from inside the loop thread.
        d.kick()
        await asyncio.sleep(1.5)
        await d.stop()
        return fired["n"]

    n = asyncio.run(run())
    assert n >= 1


def test_kick_before_start_is_noop(db_path):
    d = SchedulerDaemon(db_path)
    # No exception, no effect: wake_evt is None.
    d.kick()
