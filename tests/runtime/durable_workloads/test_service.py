"""F8 lifecycle proofs for the supervised durable-workload service."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread

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
        self.closed = False

    def materialize_all_ready_units(self, *, limit: int) -> int:
        assert 1 <= limit <= 1000
        return self.materialized.pop(0) if self.materialized else 0

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
