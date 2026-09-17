"""Contention is bounded admission backpressure, not failed sibling work."""

from concurrent.futures import Future
import sqlite3
import time
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import durable_workloads.service as service_module
from durable_workloads.worker import WorkerRunOutcome, WorkerRunStatus
from test_service import _Bridge, _ParallelWorker, _service


def _database_error(code):
    error = sqlite3.OperationalError("not interpreted by the classifier")
    error.sqlite_errorcode = code
    return error


@pytest.fixture
def clock(monkeypatch):
    current = [100.0]
    monkeypatch.setattr(service_module, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: current[0],
    ))
    return current


def _failed_lanes(service, errors):
    for lane, error in enumerate(errors):
        future = Future()
        future.set_exception(error)
        service._parallel_futures[future] = lane


@pytest.mark.parametrize("code", [
    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
    sqlite3.SQLITE_BUSY | (2 << 8), sqlite3.SQLITE_LOCKED | (1 << 8),
])
def test_only_sqlite_busy_and_locked_codes_are_contention(code):
    assert service_module.DurableWorkerService._is_database_contention(
        _database_error(code),
    )


@pytest.mark.parametrize("error", [
    sqlite3.OperationalError("database is locked"),
    _database_error(sqlite3.SQLITE_CORRUPT),
    _database_error(sqlite3.SQLITE_IOERR),
    _database_error(sqlite3.SQLITE_READONLY),
    _database_error("5"), _database_error(True),
    RuntimeError("database is locked"),
])
def test_message_and_non_contention_failures_are_never_retried_as_busy(error):
    assert not service_module.DurableWorkerService._is_database_contention(error)


def test_four_busy_lanes_pause_admission_without_stopping_live_sibling(
    tmp_path, monkeypatch, clock,
):
    service = _service(tmp_path)
    assert service.start()
    service._effective_parallel_workers = 5
    alive = Future()
    worker = _ParallelWorker("still-running")
    service._parallel_futures[alive] = 4
    service._active_parallel_workers[4] = (worker, object())
    _failed_lanes(service, [_database_error(sqlite3.SQLITE_BUSY) for _ in range(4)])
    try:
        service.run_cycle()
        assert service.health.state == "degraded"
        assert service._consecutive_cycle_failures == 0
        assert service._contention_cycles == 1
        assert service._contention_retry_at == 100.25
        assert service._parallel_futures == {alive: 4}
        assert not worker.stop_requested
        # Repeated completion/progress notifications cannot bypass backoff.
        monkeypatch.setattr(service._store, "service_lane_demand", lambda **_:
                            pytest.fail("admission during contention backoff"))
        for _ in range(20):
            service._notify_progress()
            service.run_cycle()
        assert service._contention_cycles == 1
        assert not worker.stop_requested
    finally:
        service._active_parallel_workers.clear()
        alive.set_result(WorkerRunOutcome(WorkerRunStatus.COMMITTED))
        service.stop()


def test_nontransient_burst_is_one_failed_cycle_and_still_exhausts(tmp_path):
    service = _service(tmp_path)
    assert service.start()
    service._effective_parallel_workers = 4
    try:
        for cycle in range(1, 4):
            _failed_lanes(service, [RuntimeError("synthetic") for _ in range(4)])
            if cycle == 3:
                with pytest.raises(RuntimeError, match="cycle failed repeatedly"):
                    service.run_cycle()
            else:
                service.run_cycle()
            assert service._consecutive_cycle_failures == cycle
            assert service._contention_cycles == 0
    finally:
        service.stop()


@pytest.mark.parametrize("boundary", ["maintenance", "demand", "serial"])
def test_contention_at_each_serial_boundary_recovers_after_pause(
    tmp_path, monkeypatch, clock, boundary,
):
    bridge = _Bridge()
    service = _service(tmp_path, bridge=bridge)
    assert service.start()
    calls = []

    def once_busy(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _database_error(sqlite3.SQLITE_LOCKED)
        return 1 if boundary == "demand" else None

    if boundary == "maintenance":
        monkeypatch.setattr(bridge, "maintain", once_busy, raising=False)
    elif boundary == "demand":
        monkeypatch.setattr(service._store, "service_lane_demand", once_busy)
    else:
        monkeypatch.setattr(bridge, "run_once", once_busy)
    try:
        service.run_cycle()
        assert service._contention_cycles == 1
        service.run_cycle()
        assert len(calls) == 1
        clock[0] = service._contention_retry_at
        service.run_cycle()
        assert len(calls) == 2
        assert service.health.state == "ready"
        assert service._contention_cycles == 0
    finally:
        service.stop()


@pytest.mark.parametrize("wall_bound", [False, True])
def test_persistent_contention_has_attempt_and_elapsed_bounds(
    tmp_path, monkeypatch, clock, wall_bound,
):
    service = _service(tmp_path)
    assert service.start()
    def busy(**_kwargs):
        raise _database_error(sqlite3.SQLITE_BUSY)
    monkeypatch.setattr(service._store, "service_lane_demand", busy)
    try:
        service.run_cycle()
        if wall_bound:
            clock[0] += service_module._MAX_CONTENTION_DURATION_S
        else:
            for _ in range(service_module._MAX_CONTENTION_CYCLES - 2):
                clock[0] = service._contention_retry_at
                service.run_cycle()
            clock[0] = service._contention_retry_at
        with pytest.raises(RuntimeError, match="database contention exhausted"):
            service.run_cycle()
        assert service.health.state == "degraded"
    finally:
        service.stop()


def test_mixed_busy_and_corruption_burst_preserves_fatal_cycle_budget(tmp_path):
    service = _service(tmp_path)
    assert service.start()
    service._effective_parallel_workers = 2
    try:
        _failed_lanes(service, [
            _database_error(sqlite3.SQLITE_BUSY),
            _database_error(sqlite3.SQLITE_CORRUPT),
        ])
        service.run_cycle()
        assert service._consecutive_cycle_failures == 1
        assert service._contention_cycles == 0
    finally:
        service.stop()


def test_native_sqlite_write_lock_release_allows_next_fenced_probe(
    tmp_path, monkeypatch, clock,
):
    """Use SQLite's actual errorcode, not a message or fabricated exception."""

    database = tmp_path / "contention.sqlite3"
    with sqlite3.connect(database) as setup:
        setup.execute("CREATE TABLE control_probe (id INTEGER)")
    holder = sqlite3.connect(database, timeout=0)
    contender = sqlite3.connect(database, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    service = _service(tmp_path)
    assert service.start()
    probes = []
    def probe(**_kwargs):
        probes.append(1)
        contender.execute("BEGIN IMMEDIATE")
        contender.rollback()
        return 0
    monkeypatch.setattr(service._store, "service_lane_demand", probe)
    try:
        service.run_cycle()
        assert service.health.state == "degraded"
        assert service._contention_cycles == 1
        assert not contender.in_transaction
        holder.rollback()
        service.run_cycle()
        assert len(probes) == 1  # Lock release does not bypass the pause.
        clock[0] = service._contention_retry_at
        service.run_cycle()
        assert len(probes) == 2
        assert service.health.state == "ready"
        assert service._contention_cycles == 0
        assert service._bridge.calls == 0  # No replay of execution side effects.
    finally:
        holder.close()
        contender.close()
        service.stop()


def test_successful_sibling_does_not_clear_failed_demand_episode(
    tmp_path, monkeypatch, clock,
):
    service = _service(tmp_path)
    assert service.start()
    service._effective_parallel_workers = 2
    def busy(**_kwargs):
        raise _database_error(sqlite3.SQLITE_BUSY)
    monkeypatch.setattr(service._store, "service_lane_demand", busy)
    try:
        service.run_cycle()
        assert service._contention_cycles == 1
        sibling = Future()
        sibling.set_result(WorkerRunOutcome(WorkerRunStatus.COMMITTED))
        service._parallel_futures[sibling] = 0
        clock[0] = service._contention_retry_at
        service.run_cycle()
        assert service._contention_cycles == 2
        assert service.health.state == "degraded"
    finally:
        service.stop()


def test_run_forever_backoff_ignores_progress_but_stop_interrupts(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    entered, waiting = Event(), Event()
    calls = []

    class StopEvent:
        def __init__(self):
            self.event = Event()
        def is_set(self):
            return self.event.is_set()
        def set(self):
            self.event.set()
        def wait(self, delay):
            assert delay > 0
            waiting.set()
            return self.event.wait(delay)

    service._stop_event = StopEvent()
    def cycle():
        calls.append(1)
        service._contention_retry_at = time.monotonic() + 5
        service._cycle_wakeup.set()
        entered.set()
    monkeypatch.setattr(service, "run_cycle", cycle)
    results = []
    thread = Thread(target=lambda: results.append(service.run_forever()))
    try:
        thread.start()
        assert entered.wait(1)
        assert waiting.wait(1)
        assert calls == [1]
        service.request_stop()
        thread.join(1)
        assert not thread.is_alive()
        assert results == [0]
    finally:
        service.request_stop()
        thread.join(1)
