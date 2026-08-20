from __future__ import annotations

import ast
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from durable_workloads.models import ExecutionContext
from executor_metadata import DEFAULT_EXECUTION_POLICY
from executor_scheduler import (
    ExecutorScheduler,
    SchedulerAdmissionTimeout,
    SchedulerContextError,
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
    )


class _RecordingSlot:
    def __init__(self, name: str, events: list[str]):
        self.name = name
        self.events = events

    def acquire(self, timeout=None):
        self.events.append(f"+{self.name}")
        return True

    def release(self):
        self.events.append(f"-{self.name}")


def _wait_for_waiters(scheduler: ExecutorScheduler, count: int) -> None:
    limit = datetime.now(timezone.utc) + timedelta(seconds=2)
    while scheduler._fair_gate.waiting_count < count:
        if datetime.now(timezone.utc) >= limit:
            raise AssertionError("scheduler waiters did not reach the expected count")
        threading.Event().wait(0.002)


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


def test_context_resources_acquire_and_release_in_canonical_order():
    events: list[str] = []
    scheduler = ExecutorScheduler(
        max_in_flight=4,
        parallel_enabled=False,
        resource_limits={"cpu": 2, "llm": 2},
    )
    executor = _Executor(execution_policy=dict(DEFAULT_EXECUTION_POLICY))
    scheduler._global_slots = _RecordingSlot("global", events)
    scheduler._resource_slots = {
        "cpu": _RecordingSlot("cpu", events),
        "llm": _RecordingSlot("llm", events),
    }
    scheduler._policy_slots[(executor.name, 1)] = _RecordingSlot("executor", events)

    assert scheduler.invoke(
        executor,
        lambda: {"ok": True},
        execution_context=_context(resources={"cpu": 1, "llm": 1}),
    ) == {"ok": True}
    assert events == [
        "+global", "+cpu", "+llm", "+executor",
        "-executor", "-llm", "-cpu", "-global",
    ]
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

    identity = scheduler._identity_slot(
        executor, concurrency_identity="same"
    )
    assert identity is not None
    identity[1].lock.acquire()
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
        scheduler._release_identity_slot(identity)
    assert scheduler._identity_slots == {}
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
