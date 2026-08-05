from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from executor_metadata import DEFAULT_EXECUTION_POLICY, execution_policy
from executor_scheduler import ExecutorScheduler


@dataclass
class _Executor:
    name: str = "read_test"
    execution_policy: dict = field(
        default_factory=lambda: dict(DEFAULT_EXECUTION_POLICY))


def _safe_policy(level: int = 1) -> dict:
    return {
        "effect": "read_only",
        "parallelism_class": level,
        "resource_class": "local_io",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    }


def test_missing_and_incomplete_policy_degrade_to_serial() -> None:
    assert execution_policy({}) == DEFAULT_EXECUTION_POLICY
    assert execution_policy({"execution": {
        "parallelism_class": 3,
    }}) == DEFAULT_EXECUTION_POLICY


def test_serial_submit_runs_on_calling_thread_without_reordering() -> None:
    scheduler = ExecutorScheduler(
        max_workers=4, max_in_flight=4, parallel_enabled=True)
    executor = _Executor()
    calling_thread = threading.current_thread().name

    first = scheduler.submit(executor, lambda: threading.current_thread().name)
    second = scheduler.submit(executor, lambda: threading.current_thread().name)

    assert first.result() == calling_thread
    assert second.result() == calling_thread
    assert scheduler.metrics.snapshot()[executor.name]["max_in_flight"] == 1
    scheduler.shutdown()


def test_parallel_requires_both_global_switch_and_verified_opt_in() -> None:
    executor = _Executor(execution_policy=_safe_policy())
    disabled = ExecutorScheduler(
        max_workers=2, max_in_flight=2, parallel_enabled=False)
    calling_thread = threading.current_thread().name
    assert disabled.submit(
        executor, lambda: threading.current_thread().name).result() == calling_thread
    disabled.shutdown()

    enabled = ExecutorScheduler(
        max_workers=2, max_in_flight=4, parallel_enabled=True)
    release = threading.Event()
    entered = threading.Event()
    active = 0
    maximum = 0
    lock = threading.Lock()

    def work() -> str:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if maximum >= 2:
                entered.set()
        release.wait(timeout=1)
        with lock:
            active -= 1
        return threading.current_thread().name

    futures = [enabled.submit(executor, work) for _ in range(2)]
    assert entered.wait(timeout=1)
    release.set()
    names = [future.result(timeout=1) for future in futures]
    assert maximum == 2
    assert all(name.startswith("metnos_executor") for name in names)
    enabled.shutdown()


def test_parallelism_classes_are_hardware_bounded() -> None:
    scheduler = ExecutorScheduler(
        hardware_threads=12, max_in_flight=32, parallel_enabled=True)
    assert [scheduler.parallelism_limit(level) for level in range(4)] == [
        1, 2, 6, 12,
    ]
    scheduler.shutdown()


def test_intra_executor_budget_cannot_exceed_instance_total(monkeypatch) -> None:
    import executor_scheduler

    scheduler = ExecutorScheduler(
        hardware_threads=32, max_workers=5,
        max_in_flight=32, parallel_enabled=True)
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    executor = _Executor(execution_policy=_safe_policy(level=3))

    assert executor_scheduler.assigned_worker_budget(executor) == 5
    scheduler.shutdown()


def test_environment_class_clamps_cannot_raise_signed_policy() -> None:
    admitted = _Executor(
        name="extract_entries", execution_policy=_safe_policy(level=2))
    legacy = _Executor(name="legacy_reader")
    scheduler = ExecutorScheduler(
        hardware_threads=16, parallel_enabled=True,
        max_parallelism_class=2,
        class_overrides={"extract_entries": 0, "legacy_reader": 3},
    )

    assert scheduler.effective_parallelism_class(admitted) == 0
    assert scheduler.can_parallelize(admitted) is False
    assert scheduler.effective_parallelism_class(legacy) == 0
    scheduler.shutdown()


def test_global_environment_class_cap_lowers_all_admitted_executors() -> None:
    executor = _Executor(execution_policy=_safe_policy(level=3))
    scheduler = ExecutorScheduler(
        hardware_threads=16, parallel_enabled=True,
        max_parallelism_class=1,
    )

    assert scheduler.effective_parallelism_class(executor) == 1
    assert scheduler.parallelism_limit(
        scheduler.effective_parallelism_class(executor)) == 2
    scheduler.shutdown()


def test_llm_class_cap_only_lowers_llm_executors(monkeypatch) -> None:
    monkeypatch.setenv("METNOS_LLM_PARALLELISM_CLASS", "1")
    llm_policy = _safe_policy(level=3)
    llm_policy["resource_class"] = "llm"
    llm_executor = _Executor(name="extract_entries", execution_policy=llm_policy)
    io_executor = _Executor(name="read_messages", execution_policy=_safe_policy(3))
    scheduler = ExecutorScheduler(
        hardware_threads=16, parallel_enabled=True,
        max_parallelism_class=3,
    )

    assert scheduler.effective_parallelism_class(llm_executor) == 1
    assert scheduler.effective_parallelism_class(io_executor) == 3
    scheduler.shutdown()


def test_global_backpressure_bounds_admitted_parallel_calls() -> None:
    executor = _Executor(execution_policy=_safe_policy(level=3))
    scheduler = ExecutorScheduler(
        max_workers=4, max_in_flight=1, parallel_enabled=True,
        hardware_threads=4)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work() -> bool:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return True

    futures = [scheduler.submit(executor, work) for _ in range(3)]
    assert all(future.result(timeout=1) for future in futures)
    snapshot = scheduler.metrics.snapshot()[executor.name]
    assert maximum == 1
    assert snapshot["calls"] == 3
    assert snapshot["max_in_flight"] == 1
    assert snapshot["queue_ms_total"] >= 0
    scheduler.shutdown()


def test_keyed_policy_requires_identity_and_serializes_same_identity() -> None:
    policy = _safe_policy(level=3)
    policy["concurrency_key"] = "path"
    executor = _Executor(execution_policy=policy)
    scheduler = ExecutorScheduler(
        max_workers=4, max_in_flight=4, parallel_enabled=True,
        hardware_threads=4)

    # Missing a runtime-resolved key cannot acquire parallel authority.
    calling_thread = threading.current_thread().name
    assert scheduler.submit(
        executor, lambda: threading.current_thread().name).result() == calling_thread

    lock = threading.Lock()
    active = 0
    maximum = 0

    def work() -> bool:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return True

    futures = [
        scheduler.submit(
            executor, work, concurrency_identity="/same/path")
        for _ in range(3)
    ]
    assert all(future.result(timeout=1) for future in futures)
    assert maximum == 1
    assert scheduler._identity_slots == {}
    scheduler.shutdown()


def test_keyed_identity_registry_is_reclaimed_after_many_unique_keys() -> None:
    policy = _safe_policy(level=2)
    policy["concurrency_key"] = "path"
    executor = _Executor(name="read_keyed", execution_policy=policy)
    scheduler = ExecutorScheduler(
        max_workers=4, max_in_flight=8, parallel_enabled=True,
        hardware_threads=8)

    futures = [
        scheduler.submit(
            executor, lambda: {"ok": True},
            concurrency_identity=f"/document/{index}")
        for index in range(200)
    ]
    assert all(future.result(timeout=2)["ok"] for future in futures)
    assert scheduler._identity_slots == {}
    scheduler.shutdown()


def test_create_only_can_parallelize_only_with_isolation_identity() -> None:
    policy = _safe_policy(level=1)
    policy.update({"effect": "create_only", "concurrency_key": "account"})
    executor = _Executor(name="create_records", execution_policy=policy)
    scheduler = ExecutorScheduler(
        max_workers=2, max_in_flight=4, parallel_enabled=True,
        hardware_threads=4)

    assert scheduler.can_parallelize(executor) is False
    assert scheduler.can_parallelize(
        executor, concurrency_identity="account-a") is True
    scheduler.shutdown()


def test_runtime_identity_resolution_is_declared_and_fail_closed(
        monkeypatch) -> None:
    import executor_scheduler

    policy = _safe_policy(level=1)
    policy.update({"effect": "create_only", "concurrency_key": "path"})
    executor = _Executor(name="create_files", execution_policy=policy)
    scheduler = ExecutorScheduler(parallel_enabled=True, hardware_threads=4)
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)

    assert executor_scheduler.concurrency_identity_for(
        executor, {"path": "/tmp/new-a"}) == "/tmp/new-a"
    assert executor_scheduler.concurrency_identity_for(
        executor, {"dest": "/tmp/archive", "paths": ["a", "b"]}) == (
            "/tmp/archive")
    assert executor_scheduler.concurrency_identity_for(
        executor, {"paths": ["/tmp/a", "/tmp/b"]}) is None
    assert executor_scheduler.concurrency_identity_for(executor, {}) is None
    scheduler.shutdown()


def test_metrics_do_not_swallow_exceptions() -> None:
    scheduler = ExecutorScheduler(parallel_enabled=False)
    executor = _Executor()

    def fail() -> None:
        raise RuntimeError("boom")

    future = scheduler.submit(executor, fail)
    try:
        future.result()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:  # pragma: no cover
        raise AssertionError("exception was swallowed")
    assert scheduler.metrics.snapshot()[executor.name]["failures"] == 1
    scheduler.shutdown()


def test_agent_runtime_chokepoint_preserves_arguments_and_result(
        monkeypatch) -> None:
    import agent_runtime
    import executor_scheduler

    scheduler = ExecutorScheduler(
        max_workers=2, max_in_flight=2, parallel_enabled=False)
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    executor = _Executor(name="read_probe")
    args = {"paths": ["a", "b"], "nested": {"value": 7}}
    seen = {}

    def fake_impl(actual_executor, actual_args, **kwargs):
        seen["executor"] = actual_executor
        seen["args"] = actual_args
        seen["kwargs"] = kwargs
        return {"ok": True, "entries": [{"path": "a"}]}

    monkeypatch.setattr(agent_runtime, "_invoke_executor_impl", fake_impl)
    result = agent_runtime.invoke_executor(
        executor, args, timeout_s=17, autonomy="autonomous",
        turn_id="turn-1", actor="actor-1", channel="test",
        target_device="device-1",
    )

    assert result == {"ok": True, "entries": [{"path": "a"}]}
    assert seen["executor"] is executor
    assert seen["args"] is args
    assert seen["kwargs"] == {
        "timeout_s": 17,
        "autonomy": "autonomous",
        "turn_id": "turn-1",
        "actor": "actor-1",
        "owner_user_id": None,
        "channel": "test",
        "target_device": "device-1",
    }
    assert scheduler.metrics.snapshot()[executor.name]["calls"] == 1
    scheduler.shutdown()


def test_agent_runtime_async_chokepoint_uses_same_impl(monkeypatch) -> None:
    import agent_runtime
    import executor_scheduler

    scheduler = ExecutorScheduler(
        max_workers=2, max_in_flight=2, parallel_enabled=True,
        hardware_threads=2)
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    executor = _Executor(
        name="read_async", execution_policy=_safe_policy(level=1))
    args = {"query": "atlas"}
    seen = {}

    def fake_impl(actual_executor, actual_args, **kwargs):
        seen["executor"] = actual_executor
        seen["args"] = actual_args
        seen["kwargs"] = kwargs
        return {"ok": True, "entries": ["atlas"]}

    monkeypatch.setattr(agent_runtime, "_invoke_executor_impl", fake_impl)
    result = agent_runtime.submit_executor(
        executor, args, timeout_s=19, actor="actor-1").result(timeout=1)

    assert result == {"ok": True, "entries": ["atlas"]}
    assert seen["executor"] is executor
    assert seen["args"] is args
    assert seen["kwargs"]["timeout_s"] == 19
    assert seen["kwargs"]["actor"] == "actor-1"
    assert scheduler.metrics.snapshot()[executor.name]["calls"] == 1
    scheduler.shutdown()


def test_agent_runtime_async_passes_create_only_isolation_key(monkeypatch) -> None:
    import agent_runtime
    import executor_scheduler

    scheduler = ExecutorScheduler(
        max_workers=2, max_in_flight=2, parallel_enabled=True,
        hardware_threads=2)
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    policy = _safe_policy(level=1)
    policy.update({"effect": "create_only", "concurrency_key": "path"})
    executor = _Executor(name="create_probe", execution_policy=policy)
    monkeypatch.setattr(
        agent_runtime, "_invoke_executor_impl",
        lambda *_args, **_kwargs: {
            "ok": True, "thread": threading.current_thread().name})

    result = agent_runtime.submit_executor(
        executor, {"path": "/new/output"}).result(timeout=1)

    assert result["ok"] is True
    assert result["thread"].startswith("metnos_executor")
    assert scheduler._identity_slots == {}
    scheduler.shutdown()
