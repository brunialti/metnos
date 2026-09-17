"""Supervised parallel progress with real fences and synthetic non-model work."""

from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import Future
from datetime import timedelta
from dataclasses import replace
from itertools import count
from threading import Event, Lock, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

import executor_scheduler
import durable_workloads.service as service_module
from durable_workloads.admission import admit_candidate
from durable_workloads.compiler import (
    ApprovedOutputSchema,
    FrozenRunnerContract,
    OutputSchemaRegistry,
    _freeze_execution_policy,
)
from durable_workloads.coordinator import WorkerCapabilities
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.models import WorkloadState
from durable_workloads.service import DurableWorkerService
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import DurableWorker, WorkerRunOutcome, WorkerRunStatus
from executor_scheduler import ExecutorScheduler
from helpers import inventory, plan, source
from test_service import _Bridge, _ParallelWorker, _Store


@pytest.fixture
def scheduler_factory(monkeypatch):
    schedulers = []
    monkeypatch.delenv("METNOS_DURABLE_WORKERS", raising=False)

    def create(*, enabled=True, cpu=3):
        scheduler = ExecutorScheduler(
            max_workers=4,
            max_in_flight=8,
            parallel_enabled=enabled,
            hardware_threads=4,
            resource_limits={"cpu": cpu, "default": 8},
        )
        monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
        schedulers.append(scheduler)
        return scheduler

    yield create
    for scheduler in schedulers:
        scheduler.shutdown()


@pytest.mark.parametrize(("enabled", "override", "expected"), [
    (True, None, 3),
    (False, None, 1),
    (True, "1", 1),
    (True, "2", 2),
    (True, "32", 3),
    (True, "invalid", 1),
])
def test_automatic_lanes_preserve_central_gate_and_explicit_overrides(
    tmp_path, monkeypatch, scheduler_factory, enabled, override, expected,
):
    scheduler_factory(enabled=enabled)
    if override is not None:
        monkeypatch.setenv("METNOS_DURABLE_WORKERS", override)
    service = DurableWorkerService(
        enabled=True,
        store_path=tmp_path / "state.sqlite3",
        health_path=tmp_path / "health.json",
        store_factory=lambda _path: _Store(),
        worker_factory=lambda _store: _ParallelWorker(uuid4().hex),
        bridge_factory=lambda _store: _Bridge(),
    )
    try:
        assert service.start()
        assert service.parallel_workers == expected
    finally:
        service.stop()


def test_idle_completion_does_not_spin_but_overtaken_idle_probe_wakes(tmp_path):
    service = DurableWorkerService(
        enabled=False, store_path=tmp_path / "state.sqlite3",
        health_path=tmp_path / "health.json",
    )
    idle = Future()
    idle.set_result(WorkerRunOutcome(WorkerRunStatus.IDLE))
    service._parallel_completed(idle, 0)
    assert not service._cycle_wakeup.is_set()

    service._notify_progress()
    service._cycle_wakeup.clear()
    service._parallel_completed(idle, 0)
    assert service._cycle_wakeup.is_set()

    service._cycle_wakeup.clear()
    service._parallel_completed(idle, 1)
    assert not service._cycle_wakeup.is_set()
    service.request_stop()
    assert service._cycle_wakeup.is_set()


class _Resolver:
    def __init__(self, parallelism_class):
        self.contract = FrozenRunnerContract(
            kind="workload", name="fixture.parallel_map",
            contract_digest="sha256:" + "1" * 64,
            implementation_digest="sha256:" + "2" * 64,
            allowed_effects=("pure",), input_names=("record",),
            required_input_names=("record",), input_types=(("record", "object"),),
            output_schema_names=("metnos.fixture-parallel-map/1",),
            execution_policy=_freeze_execution_policy({
                "effect": "read_only", "parallelism_class": parallelism_class,
                "resource_class": "cpu", "concurrency_key": "none",
                "equivalence_gate": "verified",
            }),
            execution_policy_declared=True,
        )

    def resolve(self, kind, name):
        assert (kind, name) == (self.contract.kind, self.contract.name)
        return self.contract


def _schemas():
    return OutputSchemaRegistry((
        ApprovedOutputSchema.create("metnos.inventory-seal/1", {
            "type": "object",
            "properties": {"digest": {"type": "string"}, "sources": {"type": "array"}},
            "required": ["digest", "sources"], "additionalProperties": False,
        }),
        ApprovedOutputSchema.create("metnos.fixture-parallel-map/1", {
            "type": "object", "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"], "additionalProperties": False,
        }),
    ))


def _admit(path, resolver, *, count=6, max_concurrency=3, resource="cpu",
           request_key="parallel-progress", resources=None, workload_id=None,
           timeout_s=60, effect="pure"):
    candidate = plan(with_map=True)
    candidate["budgets"]["max_concurrency"] = max_concurrency
    mapping = candidate["stages"][1]
    mapping["effect_profile"] = effect
    mapping["timeout_s"] = timeout_s
    mapping["runner"] = {"kind": "workload", "name": resolver.contract.name}
    mapping["input_bindings"] = {"record": {"ref": "source.record"}}
    mapping["output_schema"]["name"] = "metnos.fixture-parallel-map/1"
    mapping["resources"][resource] = 1
    mapping["resources"].update(resources or {})
    if any(mapping["resources"].get(key, 0) for key in ("llm", "vlm")):
        mapping["invalidation_keys"].extend(["model_binding.digest", "prompt.digest"])
    with DurableWorkloadStore.open(path) as store:
        draft = store.create_draft(
            "fixture-owner", request_key,
            redacted_request={"summary": "Synthetic independent work"},
            workload_id=workload_id,
        )
        admitted = admit_candidate(
            store, "fixture-owner", draft.workload_id,
            candidate, inventory([source(index) for index in range(count)]),
            expected_version=draft.version,
            runners=resolver, output_schemas=_schemas(),
        )
        store.transition_workload(
            "fixture-owner", draft.workload_id, WorkloadState.QUEUED,
            expected_version=store.get_workload("fixture-owner", draft.workload_id).version,
        )
    return draft.workload_id, admitted.revision.revision_id


def _launch(path, resolver, invoke, *, poll_interval_s=10, parallel_workers=None,
            worker_resources=None, effect_profiles=("pure",)):
    capabilities = WorkerCapabilities.create(
        (("workload", resolver.contract.name),),
        worker_resources or {
            "cpu": 1, "device": 0, "llm": 0, "local_io": 0, "network_io": 0, "vlm": 0,
        },
        effect_profiles=effect_profiles,
    )
    stores = []
    guard = Lock()

    class TrackedStore(DurableWorkloadStore):
        def open_peer(self):
            peer = super().open_peer()
            # Count supervisor-created lanes, not each lane's lease-heartbeat
            # connection (which was never opened via the old store factory).
            if self is stores[0]:
                with guard:
                    stores.append(peer)
            return peer

    def store_factory(selected_path):
        store = TrackedStore.open(selected_path)
        stores.append(store)
        return store

    service = DurableWorkerService(
        enabled=True, store_path=path, health_path=path.with_suffix(".health.json"),
        store_factory=store_factory,
        worker_factory=lambda store: DurableWorker(
            store, uuid4().hex, capabilities, lease_duration=timedelta(seconds=120),
        ),
        bridge_factory=lambda store: DurableExecutionBridge(
            store, runners=resolver, output_schemas=_schemas(),
            workload_invoker=invoke,
        ),
        poll_interval_s=poll_interval_s, parallel_workers=parallel_workers,
    )
    exit_codes = []
    thread = Thread(target=lambda: exit_codes.append(service.run_forever()))
    thread.start()
    return service, thread, exit_codes, stores


def _stop(service, thread, exit_codes):
    service.request_stop()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert exit_codes == [0]


def _wait_completed(path, workload_id):
    deadline = time.monotonic() + 5
    with DurableWorkloadStore.open(path) as store:
        while time.monotonic() < deadline:
            if store.get_workload("fixture-owner", workload_id).state is WorkloadState.COMPLETED:
                return
            time.sleep(0.005)
        pytest.fail("synthetic workload did not complete before the idle polling interval")


def test_same_job_changes_from_serial_to_four_independent_writers_without_losing_results(
    tmp_path, monkeypatch,
):
    scheduler = ExecutorScheduler(max_workers=8, max_in_flight=8, hardware_threads=8,
                                  parallel_enabled=True, resource_limits={"cpu": 4, "default": 8})
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    resolver = _Resolver(3)
    resolver.contract = replace(resolver.contract, allowed_effects=("idempotent",),
                                execution_policy=_freeze_execution_policy({
        "effect": "mutating", "parallelism_class": 3, "resource_class": "cpu",
        "concurrency_key": "path", "equivalence_gate": "verified",
    }))
    frozen_contract = resolver.contract
    path = tmp_path / "state.sqlite3"
    workload_id, revision_id = _admit(path, resolver, count=6, max_concurrency=4, effect="idempotent")
    output = tmp_path / "output"
    output.mkdir()
    calls = []

    def invoke(_name, args, context):
        key = args["record"]["source_id"]
        calls.append(key)
        (output / key).write_text(key)
        return {"source_id": key}

    resources = {"cpu": 4, "device": 0, "llm": 0, "local_io": 0, "network_io": 0, "vlm": 0}
    capabilities = WorkerCapabilities.create((("workload", resolver.contract.name),), resources,
                                               effect_profiles=("pure", "idempotent"))
    with DurableWorkloadStore.open(path) as store:
        worker = DurableWorker(store, "before-parallelism", capabilities,
                               lease_duration=timedelta(seconds=120))
        bridge = DurableExecutionBridge(store, runners=resolver, output_schemas=_schemas(), workload_invoker=invoke)
        for _ in range(20):
            bridge.run_once(worker)
            if len(calls) == 2:
                break
        assert len(calls) == 2
        before_plan = store._connection.execute("SELECT plan_json FROM revisions WHERE id=?", (revision_id,)).fetchone()[0]
        before = set(tuple(row) for row in store._connection.execute(
            "SELECT id,committed_result_id FROM units WHERE revision_id=? AND state='committed'", (revision_id,)))

    # New scheduling evidence, but exactly the same signed/frozen contract,
    # original revision, pending units, and output files.
    resolver.concurrency_targets_for = lambda contract, args, context, device: (
        (str(output / args["record"]["source_id"]),)
        if contract.name == resolver.contract.name else ())
    assert resolver.contract == frozen_contract
    entered, release, guard = Event(), Event(), Lock()
    active = maximum = 0

    def parallel_invoke(name, args, context):
        nonlocal active, maximum
        assert context.concurrency_targets == (str(output / args["record"]["source_id"]),)
        with guard:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                entered.set()
        try:
            assert release.wait(timeout=5)
            return invoke(name, args, context)
        finally:
            with guard:
                active -= 1

    service, thread, exit_codes, _stores = _launch(
        path, resolver, parallel_invoke, poll_interval_s=0.05, parallel_workers=4,
        worker_resources=resources, effect_profiles=("pure", "idempotent"))
    try:
        assert entered.wait(timeout=5)
        release.set()
        _wait_completed(path, workload_id)
    finally:
        release.set()
        _stop(service, thread, exit_codes)
        scheduler.shutdown()
    assert maximum == 4 and len(calls) == len(set(calls)) == 6
    with DurableWorkloadStore.open(path) as store:
        assert store.get_workload("fixture-owner", workload_id).active_revision_id == revision_id
        assert store._connection.execute("SELECT plan_json FROM revisions WHERE id=?", (revision_id,)).fetchone()[0] == before_plan
        after = set(tuple(row) for row in store._connection.execute(
            "SELECT id,committed_result_id FROM units WHERE revision_id=? AND state='committed'", (revision_id,)))
        assert before <= after
        assert not store._connection.execute(
            "SELECT id FROM attempts WHERE state IN ('leased','running','failed')").fetchall()
    assert scheduler._isolation.counts() == (0, 0)


def test_real_overdue_lane_returns_and_service_executes_new_work_without_restart(
    tmp_path, scheduler_factory,
):
    scheduler_factory(cpu=2)
    resolver = _Resolver(3)
    path = tmp_path / "state.sqlite3"
    timed_out_job, timed_out_revision = _admit(
        path, resolver, count=1, timeout_s=1, request_key="deadline-first",
    )
    entered, release = Event(), Event()

    def invoke(_name, args, context):
        if context.workload_id == timed_out_job:
            entered.set()
            assert release.wait(timeout=5)
        return {"source_id": args["record"]["source_id"]}

    service, thread, exit_codes, _stores = _launch(
        path, resolver, invoke, poll_interval_s=0.05, parallel_workers=2,
    )
    try:
        assert entered.wait(timeout=2)
        deadline = time.monotonic() + 3
        while service.health.reason_code != "execution_deadline_exceeded":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        next_job, _revision = _admit(
            path, resolver, count=1, request_key="deadline-following",
        )
        release.set()
        _wait_completed(path, next_job)
        assert thread.is_alive()  # The same supervisor recovered.
        with DurableWorkloadStore.open(path) as store:
            assert store._connection.execute(
                "SELECT COUNT(*) FROM results WHERE revision_id=?",
                (timed_out_revision,),
            ).fetchone()[0] == 0
            attempt = store._connection.execute(
                "SELECT a.state FROM attempts a JOIN units u "
                "ON u.owner_user_id=a.owner_user_id AND u.id=a.unit_id "
                "WHERE u.revision_id=?",
                (timed_out_revision,),
            ).fetchone()
            assert attempt["state"] == "timed_out"
    finally:
        release.set()
        _stop(service, thread, exit_codes)


@pytest.mark.parametrize(("plan_limit", "cpu_limit", "policy_class", "expected"), [
    (3, 3, 3, 3),
    (2, 3, 3, 2),
    (1, 3, 3, 1),
    (3, 1, 3, 1),
    (3, 3, 0, 1),
])
def test_real_units_overlap_only_within_plan_resource_and_policy_limits(
    tmp_path, scheduler_factory, plan_limit, cpu_limit, policy_class, expected,
):
    scheduler_factory(cpu=cpu_limit)
    resolver = _Resolver(policy_class)
    path = tmp_path / "state.sqlite3"
    workload_id, _revision_id = _admit(path, resolver, max_concurrency=plan_limit)
    guard, admitted, release = Lock(), Event(), Event()
    active = maximum = 0
    calls = []

    def invoke(_name, args, _context):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            calls.append(args["record"]["source_id"])
            if active >= expected:
                admitted.set()
        try:
            assert release.wait(timeout=5)
            return {"source_id": args["record"]["source_id"]}
        finally:
            with guard:
                active -= 1

    service, thread, exit_codes, _stores = _launch(path, resolver, invoke)
    try:
        assert admitted.wait(timeout=5)
        release.set()
        _wait_completed(path, workload_id)
    finally:
        release.set()
        _stop(service, thread, exit_codes)
    assert maximum == expected
    assert len(calls) == 6
    assert max(Counter(calls).values()) == 1


@pytest.mark.parametrize("expire_slices", [False, True])
def test_fast_lane_drains_while_peer_runs_with_reuse_or_completion_driven_refill(
    tmp_path, monkeypatch, scheduler_factory, expire_slices,
):
    if expire_slices:
        # Force each scheduling slice to finish after one unit, while real
        # leases and the ten-second idle wait retain their normal clocks.
        clock_ticks = count(step=11)
        monkeypatch.setattr(service_module, "time", SimpleNamespace(
            time=time.time, monotonic=lambda: next(clock_ticks),
        ))
    scheduler_factory(cpu=2)
    resolver = _Resolver(3)
    path = tmp_path / "state.sqlite3"
    workload_id, _revision_id = _admit(path, resolver, max_concurrency=2)
    slow_entered, release_slow, fast_drained = Event(), Event(), Event()
    guard = Lock()
    fast_calls = []

    def invoke(_name, args, _context):
        record = args["record"]
        with guard:
            slow = not slow_entered.is_set()
            if slow:
                slow_entered.set()
        if slow:
            assert release_slow.wait(timeout=5)
        else:
            fast_calls.append(record["source_id"])
            if len(fast_calls) == 5:
                fast_drained.set()
        return {"source_id": record["source_id"]}

    service, thread, exit_codes, stores = _launch(
        path, resolver, invoke, parallel_workers=2,
    )
    try:
        assert fast_drained.wait(timeout=5)
        assert not release_slow.is_set()
        if expire_slices:
            assert len(stores) >= 7  # recovery, slow lane, five fast slices
        else:
            # One recovery store plus two lanes and at most an idle refill.
            assert len(stores) <= 4
        release_slow.set()
        _wait_completed(path, workload_id)
    finally:
        release_slow.set()
        _stop(service, thread, exit_codes)
    assert len(fast_calls) == 5


def test_parallel_shutdown_commits_active_units_and_restart_skips_them(
    tmp_path, scheduler_factory,
):
    scheduler_factory(cpu=2)
    resolver = _Resolver(3)
    path = tmp_path / "state.sqlite3"
    workload_id, revision_id = _admit(path, resolver, max_concurrency=2)
    guard, both_active, release = Lock(), Event(), Event()
    first_calls, resumed_calls = [], []

    def first_invoke(_name, args, _context):
        with guard:
            first_calls.append(args["record"]["source_id"])
            if len(first_calls) == 2:
                both_active.set()
        assert release.wait(timeout=5)
        return {"source_id": args["record"]["source_id"]}

    first, thread, exit_codes, _stores = _launch(
        path, resolver, first_invoke, parallel_workers=2,
    )
    try:
        assert both_active.wait(timeout=5)
        first.request_stop()
    finally:
        release.set()
        _stop(first, thread, exit_codes)
    assert len(first_calls) == 2
    with DurableWorkloadStore.open(path) as store:
        assert store.get_workload("fixture-owner", workload_id).state is not WorkloadState.COMPLETED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE revision_id=?", (revision_id,),
        ).fetchone()[0] == 2

    def resumed_invoke(_name, args, _context):
        resumed_calls.append(args["record"]["source_id"])
        return {"source_id": args["record"]["source_id"]}

    second, thread, exit_codes, _stores = _launch(path, resolver, resumed_invoke)
    try:
        _wait_completed(path, workload_id)
    finally:
        _stop(second, thread, exit_codes)
    assert len(resumed_calls) == 4
    assert not set(first_calls) & set(resumed_calls)
    with DurableWorkloadStore.open(path) as store:
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE revision_id=?", (revision_id,),
        ).fetchone()[0] == 6
        assert store._connection.execute(
            "SELECT COUNT(*) FROM attempts attempt JOIN units unit "
            "ON unit.id=attempt.unit_id AND unit.owner_user_id=attempt.owner_user_id "
            "WHERE unit.revision_id=?", (revision_id,),
        ).fetchone()[0] == 6


def test_serial_progress_does_not_wait_for_the_idle_poll(tmp_path, scheduler_factory):
    scheduler_factory(enabled=False)
    resolver = _Resolver(3)
    path = tmp_path / "state.sqlite3"
    workload_id, _revision_id = _admit(path, resolver)
    service, thread, exit_codes, _stores = _launch(
        path, resolver,
        lambda _name, args, _context: {"source_id": args["record"]["source_id"]},
    )
    try:
        _wait_completed(path, workload_id)
        assert service.parallel_workers == 1
    finally:
        _stop(service, thread, exit_codes)


@pytest.mark.parametrize("lanes", [1, 3])
def test_synthetic_throughput_observation(
    tmp_path, scheduler_factory, record_property, lanes,
):
    """Measure the same bounded work without a flaky speed-ratio assertion."""

    scheduler_factory(cpu=3)
    resolver = _Resolver(3)
    path = tmp_path / "state.sqlite3"
    workload_id, _revision_id = _admit(path, resolver, count=24)
    guard = Lock()
    active = peak = 0
    calls = []

    def invoke(_name, args, _context):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.025)  # Synthetic work, never an external model call.
            calls.append(args["record"]["source_id"])
            return {"source_id": args["record"]["source_id"]}
        finally:
            with guard:
                active -= 1

    started = time.monotonic()
    service, thread, exit_codes, _stores = _launch(
        path, resolver, invoke, parallel_workers=lanes,
    )
    try:
        _wait_completed(path, workload_id)
        elapsed = time.monotonic() - started
    finally:
        _stop(service, thread, exit_codes)
    assert len(set(calls)) == len(calls) == 24
    assert peak == lanes
    record_property("lanes", lanes)
    record_property("peak", peak)
    record_property("units", len(calls))
    record_property("elapsed_s", round(elapsed, 6))
