"""Generic multi-target admission: no domain names or job-shaped lock keys."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading
import time
from types import SimpleNamespace

import pytest

from durable_workloads.models import ExecutionContext, RESOURCE_KEYS
from execution_isolation import InvocationIsolation, validate_targets
from executor_scheduler import ExecutorScheduler, SchedulerAdmissionTimeout, SchedulerContextError


def _context(targets=()):
    return ExecutionContext(
        "owner", "workload", "revision", "stage", "unit", "attempt", "normal",
        tuple((name, int(name in {"cpu", "vlm"})) for name in RESOURCE_KEYS),
        (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(),
        concurrency_targets=targets,
    )


def _executor(**overrides):
    return SimpleNamespace(name="write_partitioned_fixture", execution_policy={
        "effect": "mutating", "parallelism_class": 3, "resource_class": "local_io",
        "concurrency_key": "path", "equivalence_gate": "verified", **overrides,
    })


@pytest.fixture
def scheduler():
    instance = ExecutorScheduler(
        max_workers=8, max_in_flight=8, hardware_threads=8, parallel_enabled=True,
        resource_limits={name: 4 for name in (*RESOURCE_KEYS, "default")})
    yield instance
    assert instance._isolation.counts() == (0, 0)
    assert instance._fair_gate._active == 0
    instance.shutdown()


def _wait_counts(gate, expected):
    deadline = time.monotonic() + 2
    while gate.counts() != expected:
        assert time.monotonic() < deadline, gate.counts()
        threading.Event().wait(0.001)


def test_four_disjoint_target_sets_really_execute_together(scheduler):
    entered = threading.Barrier(5)
    release = threading.Event()
    executor = _executor()

    def call():
        entered.wait(timeout=2)
        assert release.wait(timeout=2)
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(scheduler.invoke, executor, call, execution_context=_context(
            (f"/records/{index}/a", f"/records/{index}/b"))) for index in range(4)]
        try:
            entered.wait(timeout=2)
            assert scheduler._isolation.counts() == (4, 0)
        finally:
            release.set()
        assert all(future.result(timeout=2)["ok"] for future in futures)


@pytest.mark.parametrize("first,second", [
    (("/records/a", "/records/b"), ("/records/b", "/records/c")),
    (("/records",), ("/records/child",)),
    (("/records/child",), ("/records",)),
    (("/records",), ("/records-other", "/records/child")),
    (("/records-other", "/records/child"), ("/records",)),
    (("/",), ("/records",)),
    ((), ("/records/a",)),
    (("/records/a",), ()),
])
def test_overlapping_or_unknown_targets_serialize_without_dispatch(scheduler, first, second):
    entered, release = threading.Event(), threading.Event()
    executor = _executor()

    def hold():
        entered.set()
        assert release.wait(timeout=2)
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler.invoke, executor, hold, execution_context=_context(first))
        try:
            assert entered.wait(timeout=2)
            with pytest.raises(SchedulerAdmissionTimeout):
                scheduler.invoke(executor, lambda: pytest.fail("overlapping work was dispatched"),
                                 execution_context=_context(second), admission_timeout_s=0.03)
        finally:
            release.set()
        assert future.result(timeout=2)["ok"]


def test_ordinary_serial_call_also_excludes_partitioned_durable_work(scheduler):
    entered, release = threading.Event(), threading.Event()
    executor = _executor()

    def hold():
        entered.set()
        assert release.wait(timeout=2)
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler.invoke, executor, hold)
        try:
            assert entered.wait(timeout=2)
            with pytest.raises(SchedulerAdmissionTimeout):
                scheduler.invoke(executor, lambda: pytest.fail("unknown writer overlapped"),
                                 execution_context=_context(("/records/a",)), admission_timeout_s=0.03)
        finally:
            release.set()
        assert future.result(timeout=2)["ok"]


@pytest.mark.parametrize("policy", [
    {"parallelism_class": 0}, {"equivalence_gate": "unverified"}, {"effect": "unknown"},
])
def test_target_facts_never_raise_signed_authority(scheduler, policy):
    executor = _executor(**policy)
    kind, targets, _identity = scheduler._isolation_targets(
        executor, concurrency_identity=None, targets=("/records/a",))
    assert kind == "path" and targets is None


def test_targets_cannot_replace_the_declared_identity_or_invent_a_policy(scheduler):
    with pytest.raises(SchedulerContextError):
        scheduler.invoke(_executor(), lambda: pytest.fail("conflict was dispatched"),
                         concurrency_identity="/elsewhere", execution_context=_context(("/records/a",)))
    with pytest.raises(SchedulerContextError):
        scheduler.invoke(_executor(concurrency_key="none"), lambda: pytest.fail("no policy"),
                         execution_context=_context(("/records/a",)))


@pytest.mark.parametrize("targets", [
    ["/a"], ("/b", "/a"), ("/a", "/a"), ("",), ("a\x00b",), (1,),
    ("a" * 4097,), tuple(f"{index:04}" for index in range(257)),
    tuple(f"{index:04}" + "a" * 4090 for index in range(17)),
])
def test_target_declarations_are_canonical_and_bounded(targets):
    with pytest.raises(ValueError):
        validate_targets(targets)


def test_all_targets_are_reserved_atomically_and_disjoint_waiters_can_pass():
    gate = InvocationIsolation()
    first = gate.acquire("writer", "path", ("/a",), None)
    acquired, release = threading.Event(), threading.Event()

    def wait_overlap():
        token = gate.acquire("writer", "path", ("/a", "/b"), time.monotonic() + 2)
        assert token is not None
        try:
            acquired.set()
            assert release.wait(timeout=2)
        finally:
            gate.release(token)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(wait_overlap)
        try:
            _wait_counts(gate, (1, 1))
            disjoint = gate.acquire("writer", "path", ("/c",), time.monotonic() + 0.1)
            assert disjoint is not None
            gate.release(disjoint)
            # The queued /a,/b operation must not be starved by new /b writers.
            assert gate.acquire("writer", "path", ("/b",), time.monotonic() + 0.03) is None
        finally:
            gate.release(first)
            release.set()
        future.result(timeout=2)
    assert acquired.is_set() and gate.counts() == (0, 0)


def test_path_overlap_matches_all_pairs_including_lexically_interleaved_siblings():
    from itertools import combinations
    from execution_isolation import _Ticket, _conflicts

    paths = ("/", "/a", "/a-0", "/a-0/child", "/a/child", "/a/child/z", "/ab", "/b")
    groups = [(), *(tuple(sorted(group)) for size in (1, 2)
                    for group in combinations(paths, size))]
    for left in groups:
        for right in groups:
            expected = any(a == b or a.startswith(b.rstrip("/") + "/")
                           or b.startswith(a.rstrip("/") + "/")
                           for a in left for b in right)
            assert _conflicts(_Ticket("writer", "path", left),
                              _Ticket("writer", "path", right)) == expected, (left, right)


def test_serial_waiter_is_not_starved_by_new_disjoint_work():
    gate = InvocationIsolation()
    first = gate.acquire("writer", "path", ("/a",), None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(gate.acquire, "writer", "path", None, time.monotonic() + 2)
        try:
            _wait_counts(gate, (1, 1))
            assert gate.acquire("writer", "path", ("/b",), time.monotonic() + 0.03) is None
        finally:
            gate.release(first)
        serial = future.result(timeout=2)
        assert serial is not None
        gate.release(serial)
    assert gate.counts() == (0, 0)


def test_timeout_or_exception_retains_no_targets(scheduler):
    context = _context(("/a", "/b"))
    def fail():
        raise RuntimeError("fixture failure")
    with pytest.raises(RuntimeError, match="fixture failure"):
        scheduler.invoke(_executor(), fail, execution_context=context)
    assert scheduler.invoke(_executor(), lambda: {"ok": True}, execution_context=context)["ok"]
    expired = replace(context, deadline_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    with pytest.raises(SchedulerAdmissionTimeout):
        scheduler.invoke(_executor(), lambda: pytest.fail("expired"), execution_context=expired)


def test_target_paths_are_host_only_not_part_of_device_context():
    from invocations import _normalize_execution_context
    context = replace(_context(("/private/target",)), deadline_at=None)
    wire = _normalize_execution_context(context)
    assert "concurrency_targets" not in wire
    assert "/private/target" not in str(wire)
