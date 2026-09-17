from __future__ import annotations

import ast
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from durable_workloads.models import ExecutionContext
from executor_metadata import DEFAULT_EXECUTION_POLICY
from executor_scheduler import (
    ExecutorScheduler,
    SchedulerAdmissionTimeout,
    SchedulerContextError,
    SchedulerOrchestrationSaturated,
    assigned_worker_environment,
)


@dataclass
class _Executor:
    name: str = "read_durable_fixture"
    execution_policy: dict = field(
        default_factory=lambda: dict(DEFAULT_EXECUTION_POLICY)
    )
    execution_policy_declared: bool = True


def _safe_policy(level: int = 3) -> dict:
    return {
        "effect": "read_only",
        "parallelism_class": level,
        "resource_class": "local_io",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    }


def _context(
    owner: str = "owner-a",
    *,
    priority: str = "normal",
    resources: dict[str, int] | None = None,
    deadline_at: str | None = None,
    language: str | None = None,
) -> ExecutionContext:
    values = {
        "cpu": 0,
        "device": 0,
        "llm": 0,
        "local_io": 0,
        "network_io": 0,
        "vlm": 0,
    }
    values.update(resources or {})
    return ExecutionContext(
        owner_user_id=owner,
        workload_id="wrk-fixture",
        revision_id="rev-fixture",
        stage_id="stg-fixture",
        unit_key="unit-fixture",
        attempt_id="att-fixture",
        priority=priority,
        resource_claims=tuple(values.items()),
        deadline_at=deadline_at,
        language=language,
    )


def _wait_for_waiters(scheduler: ExecutorScheduler, count: int) -> None:
    limit = datetime.now(timezone.utc) + timedelta(seconds=2)
    while scheduler._fair_gate.waiting_count < count:
        if datetime.now(timezone.utc) >= limit:
            raise AssertionError("scheduler waiters did not reach the expected count")
        threading.Event().wait(0.002)


@pytest.mark.parametrize("blocked_resource", ["vlm", "network_io", "local_io"])
def test_waiting_for_one_resource_does_not_hold_unrelated_cpu(blocked_resource, monkeypatch):
    """A mixed-resource job must not obstruct a different CPU-only job."""
    scheduler = ExecutorScheduler(
        max_in_flight=8, resource_limits={"cpu": 1, blocked_resource: 1})
    blocked = scheduler._resource_slots[blocked_resource]
    assert blocked.acquire(timeout=0)
    waiting = threading.Event()
    finished = threading.Event()
    errors = []

    original_wait = blocked._condition.wait

    def observe_wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(blocked._condition, "wait", observe_wait)

    def mixed_job():
        try:
            scheduler.invoke(
                _Executor(name="mixed", execution_policy=_safe_policy()),
                lambda: {"ok": True}, admission_timeout_s=2,
                execution_context=replace(
                    _context("mixed-owner", resources={"cpu": 1, blocked_resource: 1}),
                    workload_id="wrk-mixed"),
            )
            finished.set()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=mixed_job)
    thread.start()
    try:
        assert waiting.wait(1)
        assert not finished.is_set()
        def compute():
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", "print(sum(range(10000)))"],
                capture_output=True, text=True, check=True, timeout=2)
            return {"ok": result.stdout.strip() == "49995000"}

        assert scheduler.invoke(
            _Executor(name="cpu-only", execution_policy=_safe_policy()),
            compute, admission_timeout_s=0.2,
            execution_context=replace(_context("cpu-owner", resources={"cpu": 1}),
                                      workload_id="wrk-cpu"),
        ) == {"ok": True}
    finally:
        blocked.release()
        thread.join(3)
        scheduler.shutdown()
    assert not thread.is_alive() and not errors and finished.is_set()


def test_scheduler_has_no_runtime_import_of_the_durable_package():
    path = Path(__file__).resolve().parents[3] / "runtime" / "executor_scheduler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("durable_workloads") for name in imported)


@pytest.mark.parametrize("durable", [True, False])
def test_executor_exclusion_wait_does_not_reserve_cpu(durable, monkeypatch):
    scheduler = ExecutorScheduler(max_in_flight=8, parallel_enabled=True,
                                  hardware_threads=4, resource_limits={"cpu": 1})
    policy = {**_safe_policy(1), "resource_class": "cpu"}
    executor = _Executor(name="busy-capability", execution_policy=policy)
    slot = scheduler._executor_slot(executor)
    capacity = scheduler.parallelism_limit(scheduler.effective_parallelism_class(executor))
    for _ in range(capacity):
        assert slot.acquire(timeout=0)
    waiting = threading.Event()
    original_acquire = slot.acquire

    def observe_acquire(*args, **kwargs):
        waiting.set()
        return original_acquire(*args, **kwargs)

    monkeypatch.setattr(slot, "acquire", observe_acquire)
    errors = []

    def blocked_call():
        try:
            scheduler.invoke(executor, lambda: {"ok": True}, admission_timeout_s=2,
                             execution_context=_context(resources={"cpu": 1}) if durable else None)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=blocked_call)
    thread.start()
    try:
        assert waiting.wait(1)
        assert scheduler.invoke(
            _Executor(name="unrelated", execution_policy=policy), lambda: {"ok": True},
            admission_timeout_s=0.2, execution_context=_context(resources={"cpu": 1}),
        )["ok"]
    finally:
        for _ in range(capacity):
            slot.release()
        thread.join(3)
        scheduler.shutdown()
    assert not thread.is_alive() and not errors


def test_context_resources_are_reserved_together_and_released_after_failure():
    # Partial resource acquisition was deliberately removed: observe real
    # capacities inside the call rather than mocking independent acquires.
    scheduler = ExecutorScheduler(
        max_in_flight=4, parallel_enabled=False,
        resource_limits={"cpu": 3, "llm": 2},
    )
    executor = _Executor(execution_policy=dict(DEFAULT_EXECUTION_POLICY))

    def fail_after_admission():
        assert scheduler._resource_slots["cpu"]._available == 1
        assert scheduler._resource_slots["llm"]._available == 1
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        scheduler.invoke(executor, fail_after_admission,
                         execution_context=_context(resources={"cpu": 2, "llm": 1}))
    assert scheduler._resource_slots["cpu"]._available == 3
    assert scheduler._resource_slots["llm"]._available == 2
    assert scheduler._fair_gate._active == 0
    scheduler.shutdown()


def test_central_orchestration_lanes_are_bounded_and_reserve_one_pool_thread():
    scheduler = ExecutorScheduler(
        max_workers=3,
        max_in_flight=4,
        parallel_enabled=True,
        hardware_threads=4,
    )
    entered = threading.Barrier(3)
    release = threading.Event()

    def hold():
        entered.wait(timeout=1)
        assert release.wait(timeout=1)
        return "done"

    first = scheduler.submit_orchestration(hold)
    second = scheduler.submit_orchestration(hold)
    entered.wait(timeout=1)
    assert scheduler.orchestration_capacity == 2
    with pytest.raises(SchedulerOrchestrationSaturated, match="saturated"):
        scheduler.submit_orchestration(lambda: None)

    release.set()
    assert first.result(timeout=1) == "done"
    assert second.result(timeout=1) == "done"
    scheduler.shutdown()


def test_timeout_and_exception_release_every_context_slot():
    scheduler = ExecutorScheduler(
        max_in_flight=2,
        parallel_enabled=False,
        resource_limits={"cpu": 1},
    )
    executor = _Executor(execution_policy=dict(DEFAULT_EXECUTION_POLICY))
    scheduler._resource_slots["cpu"].acquire()
    try:
        with pytest.raises(SchedulerAdmissionTimeout):
            scheduler.invoke(
                executor,
                lambda: {"ok": True},
                admission_timeout_s=0.02,
                execution_context=_context(resources={"cpu": 1}),
            )
    finally:
        scheduler._resource_slots["cpu"].release()
    assert scheduler._fair_gate._active == 0

    def fail():
        raise RuntimeError("fixture")

    with pytest.raises(RuntimeError, match="fixture"):
        scheduler.invoke(
            executor,
            fail,
            execution_context=_context(resources={"cpu": 1}),
        )
    assert scheduler.invoke(
        executor,
        lambda: {"ok": True},
        execution_context=_context(resources={"cpu": 1}),
    )["ok"] is True
    scheduler.shutdown()


def test_one_deadline_covers_global_executor_and_identity_slots():
    scheduler = ExecutorScheduler(
        max_in_flight=2,
        parallel_enabled=True,
        hardware_threads=4,
    )
    policy = _safe_policy()
    policy["concurrency_key"] = "path"
    executor = _Executor(execution_policy=policy)

    scheduler._global_slots.acquire()
    scheduler._global_slots.acquire()
    try:
        with pytest.raises(SchedulerAdmissionTimeout):
            scheduler.invoke(
                executor,
                lambda: {"ok": True},
                concurrency_identity="same",
                admission_timeout_s=0.01,
                execution_context=_context(),
            )
    finally:
        scheduler._global_slots.release()
        scheduler._global_slots.release()

    executor_slot = scheduler._context_executor_slot(
        executor, concurrency_identity="same"
    )
    executor_limit = scheduler.parallelism_limit(
        scheduler.effective_parallelism_class(executor)
    )
    for _index in range(executor_limit):
        executor_slot.acquire()
    try:
        with pytest.raises(SchedulerAdmissionTimeout):
            scheduler.invoke(
                executor,
                lambda: {"ok": True},
                concurrency_identity="same",
                admission_timeout_s=0.01,
                execution_context=_context(),
            )
    finally:
        for _index in range(executor_limit):
            executor_slot.release()

    identity = scheduler._isolation.acquire(executor.name, "path", ("same",), None)
    assert identity is not None
    try:
        with pytest.raises(SchedulerAdmissionTimeout):
            scheduler.invoke(
                executor,
                lambda: {"ok": True},
                concurrency_identity="same",
                admission_timeout_s=0.01,
                execution_context=_context(),
            )
    finally:
        scheduler._isolation.release(identity)
    assert scheduler._isolation.counts() == (0, 0)
    assert scheduler.invoke(
        executor,
        lambda: {"ok": True},
        concurrency_identity="same",
        execution_context=_context(),
    )["ok"] is True
    scheduler.shutdown()


def test_weighted_resource_claims_are_atomic():
    scheduler = ExecutorScheduler(
        max_in_flight=3,
        parallel_enabled=True,
        hardware_threads=4,
        resource_limits={"cpu": 2},
    )
    executor = _Executor(execution_policy=_safe_policy())
    active = 0
    maximum = 0
    lock = threading.Lock()

    def work():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        threading.Event().wait(0.02)
        with lock:
            active -= 1
        return {"ok": True}

    threads = [threading.Thread(target=lambda owner=f"owner-{index}": scheduler.invoke(
        executor,
        work,
        execution_context=_context(owner, resources={"cpu": 2}),
    )) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert maximum == 1
    scheduler.shutdown()


def test_expired_context_and_claim_above_host_limit_fail_closed():
    scheduler = ExecutorScheduler(
        max_in_flight=2,
        parallel_enabled=False,
        resource_limits={"cpu": 1},
    )
    executor = _Executor()
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(SchedulerAdmissionTimeout):
        scheduler.invoke(
            executor, lambda: {"ok": True},
            execution_context=_context(deadline_at=expired),
        )
    with pytest.raises(SchedulerContextError, match="host limit"):
        scheduler.invoke(
            executor, lambda: {"ok": True},
            execution_context=_context(resources={"cpu": 2}),
        )
    scheduler.shutdown()


def test_owner_round_robin_prevents_background_starvation():
    scheduler = ExecutorScheduler(
        max_workers=4,
        max_in_flight=2,
        parallel_enabled=True,
        hardware_threads=4,
    )
    executor = _Executor(execution_policy=_safe_policy())
    blocker_entered = threading.Event()
    release = threading.Event()
    order: list[str] = []
    lock = threading.Lock()

    def blocker():
        blocker_entered.set()
        release.wait(2)
        return {"ok": True}

    first = threading.Thread(
        target=lambda: scheduler.invoke(
            executor, blocker, execution_context=_context("owner-a")
        )
    )
    first.start()
    assert blocker_entered.wait(1)

    threads = []
    for label, owner in (("a1", "owner-a"), ("a2", "owner-a"), ("b1", "owner-b")):
        def run(item=label, selected_owner=owner):
            def work():
                with lock:
                    order.append(item)
                return {"ok": True}

            scheduler.invoke(
                executor, work, execution_context=_context(selected_owner)
            )

        thread = threading.Thread(target=run)
        thread.start()
        threads.append(thread)
        _wait_for_waiters(scheduler, len(threads))
    release.set()
    first.join(2)
    for thread in threads:
        thread.join(2)
    assert order == ["a1", "b1", "a2"]
    scheduler.shutdown()


def test_priority_is_bounded_within_one_owner():
    scheduler = ExecutorScheduler(
        max_workers=5,
        max_in_flight=2,
        parallel_enabled=True,
        hardware_threads=8,
    )
    executor = _Executor(execution_policy=_safe_policy())
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def blocker():
        entered.set()
        release.wait(2)
        return {"ok": True}

    first = threading.Thread(target=lambda: scheduler.invoke(
        executor, blocker, execution_context=_context("owner-a")
    ))
    first.start()
    assert entered.wait(1)
    threads = []
    for label, priority in (("low", "low"), ("high-1", "high"),
                            ("high-2", "high"), ("high-3", "high")):
        thread = threading.Thread(target=lambda item=label, selected=priority: scheduler.invoke(
            executor,
            lambda: (order.append(item), {"ok": True})[1],
            execution_context=_context("owner-a", priority=selected),
        ))
        thread.start()
        threads.append(thread)
        _wait_for_waiters(scheduler, len(threads))
    release.set()
    first.join(2)
    for thread in threads:
        thread.join(2)
    assert order == ["high-1", "high-2", "low", "high-3"]
    scheduler.shutdown()


def test_interactive_call_keeps_one_slot_under_durable_saturation():
    scheduler = ExecutorScheduler(
        max_in_flight=2,
        parallel_enabled=True,
        hardware_threads=4,
    )
    durable_executor = _Executor(execution_policy=_safe_policy())
    interactive_executor = _Executor(
        name="interactive_fixture",
        execution_policy=dict(DEFAULT_EXECUTION_POLICY),
    )
    entered = threading.Event()
    release = threading.Event()

    thread = threading.Thread(target=lambda: scheduler.invoke(
        durable_executor,
        lambda: (entered.set(), release.wait(2), {"ok": True})[2],
        execution_context=_context(),
    ))
    thread.start()
    assert entered.wait(1)
    assert scheduler.invoke(interactive_executor, lambda: {"ok": True}) == {"ok": True}
    release.set()
    thread.join(2)
    scheduler.shutdown()


def test_two_owners_and_fake_remote_device_cannot_starve_interactive_calls():
    """F12 saturation probe with a fixed seed and bounded wait at every edge."""

    seed = 0xF12
    latencies = (0.0, 0.001, 0.003)
    generator = random.Random(seed)

    for round_index in range(12):
        scheduler = ExecutorScheduler(
            max_workers=4,
            max_in_flight=3,
            parallel_enabled=True,
            hardware_threads=4,
            resource_limits={"device": 1, "network_io": 1},
        )
        remote_policy = _safe_policy()
        remote_policy["resource_class"] = "device"
        remote_executor = _Executor(
            name="fake_remote_device",
            execution_policy=remote_policy,
        )
        interactive_executor = _Executor(
            name="interactive_fixture",
            execution_policy=dict(DEFAULT_EXECUTION_POLICY),
        )
        first_entered = threading.Event()
        release_first = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum = 0
        completed = []
        errors = []
        delays = [generator.choice(latencies) for _index in range(3)]

        def invoke_remote(label, owner, delay, *, hold=False):
            nonlocal active, maximum

            def fake_device():
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    if hold:
                        first_entered.set()
                        assert release_first.wait(timeout=2)
                    time.sleep(delay)
                    with lock:
                        completed.append((label, owner))
                    return {"ok": True, "device_id": "device-f12"}
                finally:
                    with lock:
                        active -= 1

            try:
                scheduler.invoke(
                    remote_executor,
                    fake_device,
                    admission_timeout_s=1,
                    execution_context=_context(
                        owner,
                        resources={"device": 1, "network_io": 1},
                    ),
                )
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(
                target=invoke_remote,
                args=("a-1", "owner-a", delays[0]),
                kwargs={"hold": True},
            ),
            threading.Thread(
                target=invoke_remote,
                args=("b-1", "owner-b", delays[1]),
            ),
            threading.Thread(
                target=invoke_remote,
                args=("a-2", "owner-a", delays[2]),
            ),
        ]
        try:
            threads[0].start()
            assert first_entered.wait(timeout=1)
            threads[1].start()
            deadline = time.monotonic() + 1
            while scheduler._fair_gate._active < 2:
                assert time.monotonic() < deadline
                threading.Event().wait(0.001)
            threads[2].start()
            _wait_for_waiters(scheduler, 1)

            started = time.monotonic()
            assert scheduler.invoke(
                interactive_executor,
                lambda: {"ok": True, "turn": round_index},
                admission_timeout_s=0.2,
            ) == {"ok": True, "turn": round_index}
            assert time.monotonic() - started < 0.2
        finally:
            release_first.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=2)
            scheduler.shutdown()

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert maximum == 1
        assert len(completed) == 3
        assert {owner for _label, owner in completed} == {"owner-a", "owner-b"}
        metrics = scheduler.metrics.snapshot()
        assert metrics["fake_remote_device"]["calls"] == 3
        assert metrics["interactive_fixture"]["calls"] == 1


def test_incomplete_executor_policy_remains_serial_for_context_calls():
    scheduler = ExecutorScheduler(max_in_flight=4, parallel_enabled=True)
    executor = _Executor(execution_policy={})
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        threading.Event().wait(0.02)
        with lock:
            active -= 1
        return {"ok": True}

    threads = [threading.Thread(target=lambda: scheduler.invoke(
        executor, work, execution_context=_context("owner-a")
    )) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert maximum == 1
    scheduler.shutdown()


def test_only_bounded_resource_budgets_enter_the_worker_environment():
    executor = _Executor(execution_policy=_safe_policy())
    environment = assigned_worker_environment(
        executor,
        _context(
            "private-owner",
            resources={"cpu": 2, "device": 1, "network_io": 1},
        ),
    )
    assert environment == {
        "METNOS_EXECUTOR_ASSIGNED_WORKERS": "1",
        "METNOS_EXECUTOR_ASSIGNED_CPU": "2",
        "METNOS_EXECUTOR_ASSIGNED_DEVICE": "1",
        "METNOS_EXECUTOR_ASSIGNED_LLM": "0",
        "METNOS_EXECUTOR_ASSIGNED_LOCAL_IO": "0",
        "METNOS_EXECUTOR_ASSIGNED_NETWORK_IO": "1",
        "METNOS_EXECUTOR_ASSIGNED_VLM": "0",
    }
    assert "private-owner" not in repr(environment)
    assert "unit-fixture" not in repr(environment)


def test_frozen_language_enters_worker_environment_without_identity_data():
    environment = assigned_worker_environment(
        _Executor(execution_policy=_safe_policy()),
        _context("private-owner", language="en-us"),
    )

    assert environment["METNOS_LANG"] == "en-us"
    assert "private-owner" not in repr(environment)

    with pytest.raises(SchedulerContextError, match="language"):
        assigned_worker_environment(
            _Executor(execution_policy=_safe_policy()),
            _context(language="EN_US"),
        )


def test_agent_runtime_propagates_context_separately_from_arguments(monkeypatch):
    import agent_runtime
    import executor_scheduler

    scheduler = ExecutorScheduler(max_in_flight=2, parallel_enabled=False)
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    executor = _Executor(execution_policy=dict(DEFAULT_EXECUTION_POLICY))
    args = {"paths": ["fixture"]}
    context = _context()
    seen = {}

    def fake_impl(actual_executor, actual_args, **kwargs):
        seen["executor"] = actual_executor
        seen["args"] = actual_args
        seen["context"] = kwargs.get("execution_context")
        return {"ok": True}

    monkeypatch.setattr(agent_runtime, "_invoke_executor_impl", fake_impl)
    result = agent_runtime.invoke_executor(
        executor,
        args,
        owner_user_id="owner-a",
        execution_context=context,
    )
    assert result == {"ok": True}
    assert seen == {"executor": executor, "args": args, "context": context}
    assert "execution_context" not in args
    assert "owner-a" not in repr(scheduler.metrics.snapshot())
    assert "unit-fixture" not in repr(scheduler.metrics.snapshot())
    scheduler.shutdown()
