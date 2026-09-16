"""Idle supervision must stay alive without launching empty database workers."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import durable_workloads.service as service_module
from durable_workloads.service import DurableWorkerService, health_snapshot
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.coordinator import WorkerCapabilities
from durable_workloads.runtime_bindings import BoundExecutionBridge
from test_service import _Bridge, _Coordinator, _Worker
from test_service_parallel_progress import _Resolver, _admit


def test_disabled_heartbeat_survives_ten_watchdog_windows(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(service_module.time, "time", lambda: clock[0])

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock[0], tz=tz or timezone.utc)

    monkeypatch.setattr(service_module, "datetime", ClockDateTime)
    service = DurableWorkerService(
        enabled=False, store_path=tmp_path / "state.sqlite3",
        health_path=tmp_path / "health.json",
    )
    try:
        assert service.start()
        for _ in range(10):
            clock[0] += 100
            service.run_cycle()
            snapshot = health_snapshot(path=tmp_path / "health.json")
            assert snapshot["reason_code"] == "feature_disabled"
            assert snapshot["enabled"] is False
            assert snapshot["worker_available"] is False
            assert snapshot["heartbeat_at"] == ClockDateTime.now(timezone.utc).isoformat(
                timespec="seconds",
            ).replace("+00:00", "Z")
    finally:
        service.stop()


@pytest.mark.parametrize("lanes", [1, 31])
def test_empty_database_launches_no_execution_or_peer_stores(tmp_path, lanes, monkeypatch):
    stores = []
    bridges = []

    def open_store(path):
        store = DurableWorkloadStore.open(path)
        stores.append(store)
        return store

    def make_bridge(_store):
        bridge = _Bridge()
        bridges.append(bridge)
        return bridge

    service = DurableWorkerService(
        enabled=True, store_path=tmp_path / "state.sqlite3",
        health_path=tmp_path / "health.json", store_factory=open_store,
        worker_factory=lambda _store: _Worker(_Coordinator()),
        bridge_factory=make_bridge, parallel_workers=1,
    )
    try:
        assert service.start()
        # Exercise both scheduling paths without borrowing a real executor pool.
        service._effective_parallel_workers = lanes
        monkeypatch.setattr(
            "executor_scheduler.submit_orchestration",
            lambda *_args, **_kwargs: pytest.fail("idle service submitted execution"),
        )
        statements = []
        stores[0]._connection.set_trace_callback(statements.append)
        for _ in range(100):
            service.run_cycle()
        assert len(stores) == 1
        assert sum(bridge.calls for bridge in bridges) == 0
        assert service.health.state == "ready"
        assert not any("BEGIN IMMEDIATE" in statement.upper() for statement in statements)
        assert sum("FROM units" in statement for statement in statements) < 10
    finally:
        service.stop()


def test_idle_demand_observes_external_admission_and_local_changes(tmp_path):
    path = tmp_path / "state.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        assert store.service_lane_demand(limit=31) == 0
        workload_id, _revision_id = _admit(path, _Resolver(1), count=5)
        assert store.service_lane_demand(limit=31) >= 1
        # A quiescent workload alone must not cause empty parallel cycles.
        store._connection.execute(
            "UPDATE workloads SET state='needs_attention' WHERE id=?", (workload_id,),
        )
        store._connection.commit()
        assert store.service_lane_demand(limit=31) == 0
        store._connection.execute(
            "UPDATE workloads SET state='queued' WHERE id=?", (workload_id,),
        )
        store._connection.commit()
        assert store.service_lane_demand(limit=31) >= 1


@pytest.mark.parametrize(("resource_limit", "plan_limit", "expected"), [
    (1, 31, 1), (3, 31, 3), (31, 2, 2),
])
def test_lane_hint_uses_host_capacity_and_plan_limits(
    tmp_path, resource_limit, plan_limit, expected,
):
    path = tmp_path / "state.sqlite3"
    _admit(path, _Resolver(3), count=31, max_concurrency=plan_limit, resource="cpu")
    with DurableWorkloadStore.open(path) as store:
        assert store.service_lane_demand(
            limit=31, resource_limits={"cpu": resource_limit},
        ) == expected


def test_large_saturated_group_does_not_hide_independent_resource(tmp_path):
    path = tmp_path / "state.sqlite3"
    _admit(path, _Resolver(3), count=80, max_concurrency=31, resource="cpu")
    with DurableWorkloadStore.open(path) as store:
        assert store.service_lane_demand(
            limit=31, resource_limits={"cpu": 1, "local_io": 1},
        ) == 1
        _admit(path, _Resolver(3), count=1, max_concurrency=1, resource="local_io",
               request_key="independent-local-io")
        assert store.service_lane_demand(
            limit=31, resource_limits={"cpu": 1, "local_io": 1},
        ) == 2


@pytest.mark.parametrize("state", ["needs_attention", "failed", "cancelled"])
def test_attention_leases_and_terminal_residuals_keep_recovery_alive(tmp_path, state):
    path = tmp_path / "state.sqlite3"
    resolver = _Resolver(1)
    workload_id, _revision_id = _admit(path, resolver, count=5)
    with DurableWorkloadStore.open(path) as store:
        if state == "needs_attention":
            capabilities = WorkerCapabilities.create(
                (("workload", resolver.contract.name),),
                {"cpu": 1, "device": 0, "llm": 0, "local_io": 0, "network_io": 0, "vlm": 0},
            )
            lease = store.claim_next(
                "fixture-worker", datetime.now(timezone.utc), timedelta(seconds=60), capabilities,
            )
            assert lease is not None
        store._connection.execute(
            "UPDATE workloads SET state=? WHERE id=?", (state, workload_id),
        )
        store._connection.commit()
        assert store.service_lane_demand(limit=31) == 1
        if state != "needs_attention":
            assert store.settle_workloads() > 0
            assert store.service_lane_demand(limit=31) == 0


def test_repeated_demand_failure_is_not_published_as_ready(tmp_path, monkeypatch):
    service = DurableWorkerService(
        enabled=True, store_path=tmp_path / "state.sqlite3",
        health_path=tmp_path / "health.json",
        worker_factory=lambda _store: _Worker(_Coordinator()),
        bridge_factory=lambda _store: _Bridge(), parallel_workers=1,
    )
    try:
        assert service.start()
        service._effective_parallel_workers = 31

        def fail(**_kwargs):
            raise RuntimeError("synthetic database failure")

        monkeypatch.setattr(service._store, "service_lane_demand", fail)
        for _ in range(2):
            service.run_cycle()
            assert service.health.reason_code == "worker_cycle_failed"
        with pytest.raises(RuntimeError, match="demand failed repeatedly"):
            service.run_cycle()
    finally:
        service.stop()


@pytest.mark.parametrize("lanes", [1, 31])
def test_idle_keeps_bounded_maintenance_without_execution(tmp_path, monkeypatch, lanes):
    clock = [100.0]
    monkeypatch.setattr(
        "durable_workloads.runtime_bindings.time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    maintenance = []
    execution = _Bridge()
    bound = BoundExecutionBridge(
        execution, (), maintenance=(lambda: maintenance.append(clock[0]),),
    )
    service = DurableWorkerService(
        enabled=True, store_path=tmp_path / "state.sqlite3",
        health_path=tmp_path / "health.json",
        worker_factory=lambda _store: _Worker(_Coordinator()),
        bridge_factory=lambda _store: bound, parallel_workers=1,
    )
    try:
        assert service.start()
        service._effective_parallel_workers = lanes
        for second in range(180):
            clock[0] = 100 + second
            service.run_cycle()
        assert maintenance == [100, 160, 220]
        assert execution.calls == 0
        assert service.health.state == "ready"
    finally:
        service.stop()
