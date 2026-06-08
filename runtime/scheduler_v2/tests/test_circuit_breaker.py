"""Circuit-breaker dello scheduler v2: streak di fallimenti consecutivi →
auto-disable di un task ricorrente + hook on_circuit_break (notifica owner).

Copre: storage (incremento/reset consecutive_failures + migrazione additiva),
daemon (_fire_entry disabilita a soglia + invoca hook), client (resume_job +
reset-on-enable di toggle_job).
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from scheduler_v2 import client as sched_client
from scheduler_v2 import daemon as daemon_mod
from scheduler_v2 import daemon_handle
from scheduler_v2.daemon import SchedulerDaemon
from scheduler_v2.models import ScheduleEntry
from scheduler_v2.storage import SchedulerStorage


def _rec_entry(name="cb_task", trigger="every_60s", **kw):
    return ScheduleEntry(
        name=name,
        trigger=trigger,
        next_fire_at=time.time() - 1,   # fire subito
        recurring=True,
        callback_key="boom",
        **kw,
    )


# --- storage: consecutive_failures incremento/reset -----------------------

def test_storage_increments_consecutive_on_failure(db_path):
    s = SchedulerStorage(db_path)
    try:
        eid = s.upsert(_rec_entry())
        s.record_outcome(eid, status="error", duration_ms=1, error="x",
                          next_fire_at=time.time() + 60,
                          decrement_remaining=False, disable=False)
        s.record_outcome(eid, status="timeout", duration_ms=1, error="y",
                          next_fire_at=time.time() + 60,
                          decrement_remaining=False, disable=False)
        got = s.get_by_name("cb_task")
        assert got.consecutive_failures == 2
        assert got.total_failures == 2
    finally:
        s.close()


def test_storage_success_resets_consecutive_not_total(db_path):
    s = SchedulerStorage(db_path)
    try:
        eid = s.upsert(_rec_entry())
        for _ in range(3):
            s.record_outcome(eid, status="error", duration_ms=1, error="x",
                              next_fire_at=time.time() + 60,
                              decrement_remaining=False, disable=False)
        s.record_outcome(eid, status="success", duration_ms=1, error=None,
                         next_fire_at=time.time() + 60,
                         decrement_remaining=False, disable=False)
        got = s.get_by_name("cb_task")
        assert got.consecutive_failures == 0       # streak azzerata
        assert got.total_failures == 3             # cumulativo invariato
    finally:
        s.close()


def test_storage_additive_migration_adds_column(db_path):
    # DB pre-esistente SENZA la colonna: SchedulerStorage la aggiunge al init.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE schedule_entries (id INTEGER PRIMARY KEY, name TEXT UNIQUE,"
        " trigger TEXT NOT NULL, next_fire_at REAL NOT NULL, recurring INTEGER"
        " NOT NULL, callback_key TEXT NOT NULL, payload TEXT NOT NULL DEFAULT"
        " '{}', enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL"
        " DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', total_runs INTEGER"
        " NOT NULL DEFAULT 0, total_failures INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()
    s = SchedulerStorage(db_path)   # deve eseguire _migrate_additive
    try:
        cols = {r[1] for r in s._conn.execute("PRAGMA table_info(schedule_entries)")}
        assert "consecutive_failures" in cols
    finally:
        s.close()


# --- daemon: circuit-break a soglia + hook --------------------------------

def _run_one_fire(db_path, *, seed_consecutive, status_ok, threshold=3):
    """Esegue UN fire di un task ricorrente con N=seed_consecutive fallimenti
    gia' accumulati. Ritorna (enabled_dopo, hook_calls)."""
    hook_calls = []

    async def run():
        d = SchedulerDaemon(db_path)
        d.on_circuit_break = lambda entry, error: hook_calls.append((entry.name, error))

        if status_ok:
            def cb(payload):
                return "ok"
        else:
            def cb(payload):
                raise RuntimeError("kaboom")
        d.callbacks.register("boom", cb)

        e = _rec_entry(consecutive_failures=seed_consecutive)
        d.storage.upsert(e)
        await d.start()
        await asyncio.sleep(1.3)
        await d.stop()
        got = d.storage.get_by_name("cb_task")
        return got.enabled, got.consecutive_failures

    monkey_threshold(threshold)
    try:
        enabled, consec = asyncio.run(run())
    finally:
        monkey_threshold(3)
    return enabled, consec, hook_calls


_ORIG_THRESHOLD = daemon_mod._CIRCUIT_BREAK_AFTER


def monkey_threshold(v):
    daemon_mod._CIRCUIT_BREAK_AFTER = v


def test_daemon_circuit_break_disables_and_calls_hook(db_path):
    # Seed = 2, soglia 3: il fallimento di questo fire porta a 3 → disable.
    enabled, consec, hook_calls = _run_one_fire(
        db_path, seed_consecutive=2, status_ok=False, threshold=3)
    assert enabled is False, "task deve essere auto-disabilitato a soglia"
    assert consec == 3
    assert len(hook_calls) == 1
    assert hook_calls[0][0] == "cb_task"
    assert "kaboom" in (hook_calls[0][1] or "")


def test_daemon_no_break_below_threshold(db_path):
    # Seed = 0, soglia 3: un solo fallimento → resta abilitato, niente hook.
    enabled, consec, hook_calls = _run_one_fire(
        db_path, seed_consecutive=0, status_ok=False, threshold=3)
    assert enabled is True
    assert consec == 1
    assert hook_calls == []


def test_daemon_success_resets_no_break(db_path):
    # Seed = 2 ma il fire ha successo → streak azzerata, niente disable/hook.
    enabled, consec, hook_calls = _run_one_fire(
        db_path, seed_consecutive=2, status_ok=True, threshold=3)
    assert enabled is True
    assert consec == 0
    assert hook_calls == []


# --- client: resume_job + reset-on-enable ---------------------------------

@pytest.fixture
def _client_db(monkeypatch, db_path):
    monkeypatch.setenv("METNOS_SCHEDULER_V2_DB", str(db_path))
    daemon_handle.clear()
    yield db_path
    daemon_handle.clear()


def test_resume_job_reenables_and_resets(_client_db):
    s = SchedulerStorage(_client_db)
    try:
        s.upsert(_rec_entry(name="r1", enabled=False, consecutive_failures=5,
                            trigger="daily@08:00"))
    finally:
        s.close()
    assert sched_client.resume_job("r1") is True
    s = SchedulerStorage(_client_db)
    try:
        got = s.get_by_name("r1")
        assert got.enabled is True
        assert got.consecutive_failures == 0
        assert got.next_fire_at > time.time()   # ricalcolato dal trigger
    finally:
        s.close()


def test_resume_job_unknown_returns_false(_client_db):
    assert sched_client.resume_job("nope") is False


def test_toggle_enable_resets_consecutive(_client_db):
    s = SchedulerStorage(_client_db)
    try:
        s.upsert(_rec_entry(name="t1", enabled=False, consecutive_failures=4))
    finally:
        s.close()
    assert sched_client.toggle_job("t1", True) is True
    s = SchedulerStorage(_client_db)
    try:
        got = s.get_by_name("t1")
        assert got.enabled is True
        assert got.consecutive_failures == 0     # abilitare = ripartenza pulita
    finally:
        s.close()


def test_toggle_disable_keeps_consecutive(_client_db):
    s = SchedulerStorage(_client_db)
    try:
        s.upsert(_rec_entry(name="t2", enabled=True, consecutive_failures=2))
    finally:
        s.close()
    assert sched_client.toggle_job("t2", False) is True
    s = SchedulerStorage(_client_db)
    try:
        got = s.get_by_name("t2")
        assert got.enabled is False
        assert got.consecutive_failures == 2     # disabilitare NON azzera
    finally:
        s.close()
