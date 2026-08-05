from __future__ import annotations

import asyncio
import threading

import pytest

from http_turn_pool import HttpTurnPool, TurnPoolBusy


@pytest.mark.asyncio
async def test_global_capacity_is_bounded_and_reusable():
    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1,
                        admission_timeout_s=0.005)
    first = await pool.reserve("alice")
    with pytest.raises(TurnPoolBusy):
        await pool.reserve("bob")
    pool.release(first)
    second = await pool.reserve("bob")
    pool.release(second)
    pool.close()


@pytest.mark.asyncio
async def test_principal_limit_does_not_block_other_principals():
    pool = HttpTurnPool(workers=2, queue_slots=2, per_principal=1,
                        admission_timeout_s=0.005)
    alice = await pool.reserve("alice")
    with pytest.raises(TurnPoolBusy):
        await pool.reserve("alice")
    bob = await pool.reserve("bob")
    pool.release(alice)
    pool.release(bob)
    pool.close()


@pytest.mark.asyncio
async def test_cancelled_wait_holds_capacity_until_thread_finishes():
    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1,
                        admission_timeout_s=0.005)
    started = threading.Event()
    finish = threading.Event()

    def blocking():
        started.set()
        finish.wait(timeout=2)

    reservation = await pool.reserve("alice")
    task = asyncio.create_task(pool.run_reserved(reservation, blocking))
    await asyncio.to_thread(started.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(TurnPoolBusy):
        await pool.reserve("bob")

    finish.set()
    for _ in range(50):
        if pool.stats()["completed"]:
            break
        await asyncio.sleep(0.01)
    followup = await pool.reserve("bob")
    pool.release(followup)
    assert pool.stats()["completed"] == 1
    pool.close()


@pytest.mark.asyncio
async def test_run_reports_completion():
    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1)
    assert await pool.run("alice", lambda: 42) == 42
    assert pool.stats()["admitted"] == 1
    assert pool.stats()["completed"] == 1
    pool.close()


@pytest.mark.asyncio
async def test_idle_principals_are_evicted_from_registry():
    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1)
    for index in range(100):
        reservation = await pool.reserve(f"principal-{index}")
        pool.release(reservation)
    assert pool._principals == {}
    pool.close()


@pytest.mark.asyncio
async def test_submit_failure_releases_reserved_capacity():
    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1)
    reservation = await pool.reserve("alice")
    pool._executor.shutdown(wait=False)
    with pytest.raises(RuntimeError):
        await pool.run_reserved(reservation, lambda: None)
    assert pool._principals == {}
    assert pool._global._value == 1


@pytest.mark.asyncio
async def test_turn_worker_inherits_request_language_context():
    import i18n

    pool = HttpTurnPool(workers=1, queue_slots=0, per_principal=1)
    with i18n.language_context("fr"):
        observed = await pool.run("alice", i18n.current_lang)
    assert observed == "fr"
    assert i18n.current_lang() != "fr"
    pool.close()
