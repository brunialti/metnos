from __future__ import annotations

import threading
import time

from executor_workers import assigned_workers, map_ordered, worker_budget
from worker_policy import bounded_worker_count


def test_missing_or_invalid_worker_budget_is_serial(monkeypatch) -> None:
    monkeypatch.delenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", raising=False)
    assert assigned_workers(item_count=8) == 1
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "invalid")
    assert assigned_workers(item_count=8) == 1


def test_in_process_budget_is_context_local_and_overrides_environment(
        monkeypatch) -> None:
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "7")
    assert assigned_workers() == 7
    with worker_budget(2):
        assert assigned_workers() == 2
    assert assigned_workers() == 7


def test_shared_worker_policy_preserves_ceiling_rules() -> None:
    assert bounded_worker_count("bad", default=2) == 2
    assert bounded_worker_count(99, maximum=8, cpu_count=4) == 4
    assert bounded_worker_count(99, item_count=3) == 3
    assert bounded_worker_count(0) == 1


def test_bounded_map_runs_concurrently_but_recomposes_in_input_order(
        monkeypatch) -> None:
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "3")
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work(value: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01 * (4 - value))
        with lock:
            active -= 1
        return value * 10

    completed, skipped = map_ordered(work, [1, 2, 3])

    assert maximum == 3
    assert completed == [(0, 10), (1, 20), (2, 30)]
    assert skipped == []


def test_deadline_stops_queued_work_without_abandoning_running_work(
        monkeypatch) -> None:
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "2")
    started = []

    def work(value: int) -> int:
        started.append(value)
        time.sleep(0.03)
        return value

    completed, skipped = map_ordered(
        work, [0, 1, 2], deadline_s=0.005)

    assert [index for index, _value in completed] == [0, 1]
    assert started == [0, 1]
    assert skipped == [2]
