"""F8 lifecycle proofs for the supervised durable-workload service."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from durable_workloads.coordinator import ReconcileOutcome
from durable_workloads.migrations import SchemaTooNewError
from durable_workloads.service import (
    DurableServiceHealth,
    DurableServiceState,
    DurableWorkerService,
    health_snapshot,
    publish_health,
)


class _Store:
    def __init__(self, materialized: list[int] | None = None) -> None:
        self.materialized = list(materialized or [0])
        self.reused = [0]
        self.settled = [0]
        self.completed = [0]
        self.pruned = [0]
        self.closed = False
        self.peer_factory = _Store

    def open_peer(self):
        return self.peer_factory()

    def service_lane_demand(self, *, limit: int, resource_limits=None) -> int:
        return limit

    def adopt_reusable_results(self, *, limit: int) -> int:
        assert 1 <= limit <= 1000
        return self.reused.pop(0) if self.reused else 0

    def materialize_all_ready_units(self, *, limit: int) -> int:
        assert 1 <= limit <= 1000
        return self.materialized.pop(0) if self.materialized else 0

    def settle_workloads(self, *, limit: int) -> int:
        assert 1 <= limit <= 1000
        return self.settled.pop(0) if self.settled else 0

    def complete_ready_workloads(self, *, limit: int) -> int:
        assert 1 <= limit <= 1000
        return self.completed.pop(0) if self.completed else 0

    def prune_outbox(self, *, limit: int) -> int:
        assert 1 <= limit <= 1000
        return self.pruned.pop(0) if self.pruned else 0

    def close(self) -> None:
        self.closed = True


class _Coordinator:
    def __init__(self, outcomes: list[ReconcileOutcome] | None = None) -> None:
        self.outcomes = list(outcomes or [ReconcileOutcome()])
        self.calls = 0

    def reconcile(self, *, batch_size: int) -> ReconcileOutcome:
        assert 1 <= batch_size <= 1000
        self.calls += 1
        return self.outcomes.pop(0) if self.outcomes else ReconcileOutcome()


@dataclass
class _Worker:
    coordinator: _Coordinator
    stop_requested: bool = False
    execution_overdue: bool = False

    def request_stop(self) -> None:
        self.stop_requested = True


class _Bridge:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self, worker: _Worker) -> None:
        assert not worker.stop_requested
        self.calls += 1


class _BlockingBridge(_Bridge):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def run_once(self, worker: _Worker) -> None:
        assert not worker.stop_requested
        self.entered.set()
        assert self.release.wait(timeout=1)
        self.calls += 1


class _ParallelWorker:
    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self.coordinator = _Coordinator()
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True


class _ParallelBridge:
    def __init__(self, tracker: dict, guard: Lock) -> None:
        self._tracker = tracker
        self._guard = guard

    def run_once(self, worker: _ParallelWorker) -> None:
        assert not worker.stop_requested
        with self._guard:
            self._tracker["active"] += 1
            self._tracker["maximum"] = max(
                self._tracker["maximum"], self._tracker["active"],
            )
            if self._tracker["active"] >= 2:
                self._tracker["both_entered"].set()
        try:
            assert self._tracker["release"].wait(timeout=2)
        finally:
            with self._guard:
                self._tracker["active"] -= 1


class _FailingParallelBridge:
    def run_once(self, _worker: _ParallelWorker) -> None:
        raise RuntimeError("synthetic parallel cycle failure")


def _service(
    tmp_path: Path,
    *,
    store: _Store | None = None,
    worker: _Worker | None = None,
    bridge: _Bridge | None = None,
    max_recovery_batches: int = 5,
) -> DurableWorkerService:
    instance_store = store or _Store()
    instance_worker = worker or _Worker(_Coordinator())
    instance_bridge = bridge or _Bridge()
    return DurableWorkerService(
        enabled=True,
        store_path=tmp_path / "durable" / "state.sqlite3",
        health_path=tmp_path / "durable" / "service_health.json",
        store_factory=lambda _path: instance_store,  # type: ignore[arg-type]
        worker_factory=lambda _store: instance_worker,  # type: ignore[arg-type]
        bridge_factory=lambda _store: instance_bridge,
        poll_interval_s=0.05,
        recovery_batch_size=100,
        max_recovery_batches=max_recovery_batches,
        parallel_workers=1,
    )


def test_health_snapshot_is_closed_private_and_stale_safe(tmp_path):
    path = tmp_path / "private" / "health.json"
    value = DurableServiceHealth.create(
        DurableServiceState.READY,
        enabled=True,
        worker_available=True,
        reason_code="none",
    )
    publish_health(value, path=path)
    current = health_snapshot(path=path)
    assert current["state"] == "ready"
    assert current["worker_available"] is True
    assert set(current) == {
        "schema_version", "state", "enabled", "worker_available",
        "reason_code", "heartbeat_at",
    }
    stale = health_snapshot(path=path, now_epoch=time.time() + 91)
    assert (stale["state"], stale["reason_code"]) == ("degraded", "health_stale")


def test_health_snapshot_rejects_invalid_times(tmp_path):
    path = tmp_path / "private" / "health.json"
    value = DurableServiceHealth.create(
        DurableServiceState.READY,
        enabled=True,
        worker_available=True,
        reason_code="none",
    )
    publish_health(value, path=path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    payload["snapshot"]["heartbeat_at"] = "not-a-timestamp"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert health_snapshot(path=path)["reason_code"] == "health_unavailable"

    payload["snapshot"]["heartbeat_at"] = value.heartbeat_at
    payload["published_at"] = float("nan")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert health_snapshot(path=path)["reason_code"] == "health_unavailable"
    assert health_snapshot(path=path, now_epoch=float("nan"))["reason_code"] == (
        "health_unavailable"
    )


def test_health_snapshot_rejects_oversized_or_linked_files(tmp_path):
    path = tmp_path / "private" / "health.json"
    path.parent.mkdir()
    path.write_bytes(b"x" * 16_385)
    assert health_snapshot(path=path)["reason_code"] == "health_unavailable"

    target = tmp_path / "private" / "target.json"
    target.write_text("{}", encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        return
    assert health_snapshot(path=path)["reason_code"] == "health_unavailable"


def test_start_recovers_in_bounded_batches_before_ready(tmp_path):
    store = _Store([100, 0])
    coordinator = _Coordinator([ReconcileOutcome(expired=100), ReconcileOutcome()])
    worker = _Worker(coordinator)
    service = _service(tmp_path, store=store, worker=worker)
    try:
        assert service.start() is True
        assert service.health.state == "ready"
        assert coordinator.calls == 2
        published = health_snapshot(path=tmp_path / "durable" / "service_health.json")
        assert published["state"] == "ready"
    finally:
        service.stop()
    assert worker.stop_requested is True
    assert store.closed is True


def test_recovery_does_not_count_expired_lease_dispositions_twice(tmp_path):
    coordinator = _Coordinator([
        ReconcileOutcome(expired=60, returned_pending=60),
        ReconcileOutcome(expired=100),
    ])
    service = _service(
        tmp_path,
        store=_Store([0, 0]),
        worker=_Worker(coordinator),
    )
    try:
        assert service.start() is True
        assert service.health.state == "ready"
        assert coordinator.calls == 1
    finally:
        service.stop()


def test_start_recovers_thousands_of_rows_in_bounded_batches(tmp_path):
    batches = 20
    store = _Store([0] * (batches + 1))
    coordinator = _Coordinator(
        [ReconcileOutcome(expired=100)] * batches + [ReconcileOutcome()]
    )
    service = _service(
        tmp_path,
        store=store,
        worker=_Worker(coordinator),
        max_recovery_batches=25,
    )
    try:
        assert service.start() is True
        assert service.health.state == "ready"
        assert coordinator.calls == batches + 1
    finally:
        service.stop()


def test_second_supervisor_exits_without_replacing_owner_health(tmp_path):
    first = _service(tmp_path)
    second = _service(tmp_path)
    try:
        assert first.start() is True
        assert second.start() is False
        assert second.health.reason_code == "already_active"
        published = health_snapshot(path=tmp_path / "durable" / "service_health.json")
        assert published["state"] == "ready"
    finally:
        second.stop()
        first.stop()


def test_feature_disabled_keeps_lifecycle_alive_without_a_worker(tmp_path):
    store = _Store()
    service = DurableWorkerService(
        enabled=False,
        store_path=tmp_path / "durable" / "state.sqlite3",
        health_path=tmp_path / "durable" / "service_health.json",
        store_factory=lambda _path: store,  # type: ignore[arg-type]
    )
    try:
        assert service.start() is True
        assert (service.health.state, service.health.reason_code) == (
            "degraded", "feature_disabled",
        )
        service.run_cycle()
    finally:
        service.stop()
    assert store.closed is True


def test_schema_and_database_start_failures_publish_closed_degradation(tmp_path):
    schema_service = DurableWorkerService(
        enabled=True,
        store_path=tmp_path / "schema" / "state.sqlite3",
        health_path=tmp_path / "schema" / "health.json",
        store_factory=lambda _path: (_ for _ in ()).throw(SchemaTooNewError("newer")),
    )
    assert schema_service.start() is False
    assert schema_service.health.reason_code == "schema_incompatible"

    database_service = DurableWorkerService(
        enabled=True,
        store_path=tmp_path / "db" / "state.sqlite3",
        health_path=tmp_path / "db" / "health.json",
        store_factory=lambda _path: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )
    assert database_service.start() is False
    assert database_service.health.reason_code == "database_unavailable"


def test_stop_prevents_a_new_cycle_and_never_uses_http_as_fallback(tmp_path):
    bridge = _Bridge()
    worker = _Worker(_Coordinator())
    service = _service(tmp_path, worker=worker, bridge=bridge)
    try:
        assert service.start() is True
        service.run_cycle()
        assert bridge.calls == 1
        service.request_stop()
        service.run_cycle()
        assert bridge.calls == 1
    finally:
        service.stop()
    assert worker.stop_requested is True


def test_cooperative_stop_exits_the_service_while_waiting(tmp_path):
    service = DurableWorkerService(
        enabled=False,
        store_path=tmp_path / "durable" / "state.sqlite3",
        health_path=tmp_path / "durable" / "service_health.json",
        store_factory=lambda _path: _Store(),  # type: ignore[arg-type]
        poll_interval_s=1,
    )
    outcome: list[int] = []
    thread = Thread(target=lambda: outcome.append(service.run_forever()))
    thread.start()
    try:
        deadline = time.monotonic() + 1
        while service.health.reason_code != "feature_disabled":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        service.request_stop()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert outcome == [0]
    finally:
        service.stop()


def test_cooperative_stop_waits_for_an_active_attempt_to_finish_safely(tmp_path):
    worker = _Worker(_Coordinator())
    bridge = _BlockingBridge()
    service = _service(tmp_path, worker=worker, bridge=bridge)
    outcome: list[int] = []
    thread = Thread(target=lambda: outcome.append(service.run_forever()))
    thread.start()
    try:
        assert bridge.entered.wait(timeout=1)
        service.request_stop()
        assert worker.stop_requested is True
        bridge.release.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert outcome == [0]
        assert bridge.calls == 1
    finally:
        bridge.release.set()
        service.stop()


def test_direct_stop_never_closes_the_store_under_an_active_serial_cycle(tmp_path):
    store = _Store()
    bridge = _BlockingBridge()
    service = _service(tmp_path, store=store, bridge=bridge)
    cycle = Thread(target=service.run_cycle)
    stopping = Thread(target=service.stop)
    try:
        assert service.start() is True
        cycle.start()
        assert bridge.entered.wait(timeout=1)
        stopping.start()
        time.sleep(0.02)
        assert store.closed is False
        bridge.release.set()
        cycle.join(timeout=1)
        stopping.join(timeout=1)
        assert not cycle.is_alive()
        assert not stopping.is_alive()
        assert store.closed is True
    finally:
        bridge.release.set()
        cycle.join(timeout=1)
        stopping.join(timeout=1)
        service.stop()


def test_parallel_service_uses_bounded_central_lanes_and_thread_owned_stores(
    tmp_path,
    monkeypatch,
):
    import executor_scheduler
    from executor_scheduler import ExecutorScheduler

    scheduler = ExecutorScheduler(
        max_workers=3,
        max_in_flight=4,
        parallel_enabled=True,
        hardware_threads=4,
    )
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    tracker = {
        "active": 0,
        "maximum": 0,
        "both_entered": Event(),
        "release": Event(),
    }
    guard = Lock()
    factory_guard = Lock()
    stores: list[_Store] = []
    workers: list[_ParallelWorker] = []

    def store_factory(_path):
        store = _Store()
        store.peer_factory = lambda: store_factory(_path)
        with factory_guard:
            stores.append(store)
        return store

    def worker_factory(_store):
        with factory_guard:
            worker = _ParallelWorker(f"parallel-worker-{len(workers)}")
            workers.append(worker)
        return worker

    service = DurableWorkerService(
        enabled=True,
        store_path=tmp_path / "durable" / "state.sqlite3",
        health_path=tmp_path / "durable" / "service_health.json",
        store_factory=store_factory,  # type: ignore[arg-type]
        worker_factory=worker_factory,  # type: ignore[arg-type]
        bridge_factory=lambda _store: _ParallelBridge(tracker, guard),
        poll_interval_s=0.05,
        parallel_workers=2,
    )
    try:
        assert service.start() is True
        assert service.parallel_workers == 2
        service.run_cycle()
        assert tracker["both_entered"].wait(timeout=1)
        assert tracker["maximum"] == 2
        assert len(stores) == 3  # recovery connection plus two lane connections
    finally:
        service.request_stop()
        tracker["release"].set()
        service.stop()
        scheduler.shutdown()

    assert all(store.closed for store in stores)
    assert all(worker.stop_requested for worker in workers)


def test_repeated_parallel_failures_stay_degraded_and_converge(tmp_path, monkeypatch):
    import executor_scheduler
    from executor_scheduler import ExecutorScheduler

    scheduler = ExecutorScheduler(
        max_workers=3,
        max_in_flight=4,
        parallel_enabled=True,
        hardware_threads=4,
    )
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    factory_guard = Lock()
    next_worker = 0

    def worker_factory(_store):
        nonlocal next_worker
        with factory_guard:
            worker = _ParallelWorker(f"failing-worker-{next_worker}")
            next_worker += 1
        return worker

    service = DurableWorkerService(
        enabled=True,
        store_path=tmp_path / "durable" / "state.sqlite3",
        health_path=tmp_path / "durable" / "service_health.json",
        store_factory=lambda _path: _Store(),  # type: ignore[arg-type]
        worker_factory=worker_factory,  # type: ignore[arg-type]
        bridge_factory=lambda _store: _FailingParallelBridge(),
        poll_interval_s=0.05,
        parallel_workers=2,
    )

    def wait_until_finished() -> None:
        deadline = time.monotonic() + 1
        while True:
            with service._parallel_guard:
                futures = tuple(service._parallel_futures)
            if futures and all(future.done() for future in futures):
                return
            assert time.monotonic() < deadline
            time.sleep(0.005)

    try:
        assert service.start() is True
        service.run_cycle()
        wait_until_finished()
        service.run_cycle()
        assert service.health.state == "degraded"
        assert service.health.reason_code == "worker_cycle_failed"

        service.run_cycle()
        assert service.health.state == "degraded"
        wait_until_finished()
        # Two failing lanes in one observation are one failed cycle, not two.
        service.run_cycle()
        assert service.health.state == "degraded"
        service.run_cycle()
        wait_until_finished()
        with pytest.raises(RuntimeError, match="cycle failed repeatedly"):
            service.run_cycle()
    finally:
        service.request_stop()
        service.stop()
        scheduler.shutdown()


def test_active_long_run_keeps_the_health_snapshot_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "durable_workloads.service._HEALTH_PULSE_INTERVAL_S", 0.01,
    )
    bridge = _BlockingBridge()
    service = _service(tmp_path, bridge=bridge)
    health_path = tmp_path / "durable" / "service_health.json"
    thread = None
    try:
        assert service.start() is True
        published_before = json.loads(
            health_path.read_text(encoding="utf-8")
        )["published_at"]
        thread = Thread(target=service.run_cycle)
        thread.start()
        assert bridge.entered.wait(timeout=1)
        deadline = time.monotonic() + 1
        while True:
            published_during = json.loads(
                health_path.read_text(encoding="utf-8")
            )["published_at"]
            if published_during > published_before:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert health_snapshot(path=health_path)["state"] == "ready"
    finally:
        bridge.release.set()
        if thread is not None:
            thread.join(timeout=1)
        service.stop()


def test_overdue_in_process_execution_is_never_published_as_ready(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "durable_workloads.service._HEALTH_PULSE_INTERVAL_S", 0.01,
    )
    bridge = _BlockingBridge()
    worker = _Worker(_Coordinator())
    service = _service(tmp_path, worker=worker, bridge=bridge)
    thread = None
    try:
        assert service.start() is True
        thread = Thread(target=service.run_cycle)
        thread.start()
        assert bridge.entered.wait(timeout=1)
        worker.execution_overdue = True
        deadline = time.monotonic() + 1
        while service.health.reason_code != "execution_deadline_exceeded":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert service.health.state == "degraded"
    finally:
        bridge.release.set()
        if thread is not None:
            thread.join(timeout=1)
        service.stop()


def test_a_stuck_health_publisher_cannot_accumulate_pulse_threads(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "durable_workloads.service._HEALTH_PULSE_INTERVAL_S", 0.001,
    )
    monkeypatch.setattr(
        "durable_workloads.service._HEALTH_PULSE_JOIN_TIMEOUT_S", 0.01,
    )
    service = _service(tmp_path)
    entered = Event()
    release = Event()

    def blocked_publish(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=1)

    monkeypatch.setattr(service, "_set_health", blocked_publish)
    service._with_health_pulse(
        lambda: entered.wait(timeout=1),
        state=DurableServiceState.READY,
        reason_code="none",
    )
    pulse = service._health_pulse_thread
    assert pulse is not None and pulse.is_alive()

    try:
        with pytest.raises(RuntimeError, match="pulse is still running"):
            service._with_health_pulse(
                lambda: None,
                state=DurableServiceState.READY,
                reason_code="none",
            )
    finally:
        release.set()
        pulse.join(timeout=1)
