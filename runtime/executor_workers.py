"""Lightweight intra-executor concurrency governed by the runtime.

Executors never choose their own pool size.  The central scheduler injects
``METNOS_EXECUTOR_ASSIGNED_WORKERS`` from the signed execution policy; this
module is the single consumer-side adapter for local and remote processes.
Missing, malformed or disabled policy always means one worker.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, TypeVar

from worker_policy import bounded_worker_count


T = TypeVar("T")
R = TypeVar("R")
_WORKERS_ENV = "METNOS_EXECUTOR_ASSIGNED_WORKERS"
_IN_PROCESS_WORKERS: ContextVar[int | None] = ContextVar(
    "metnos_executor_assigned_workers", default=None)


@contextmanager
def worker_budget(workers: int):
    """Install one context-local budget for an in-process executor call."""
    bounded = bounded_worker_count(workers)
    token = _IN_PROCESS_WORKERS.set(bounded)
    try:
        yield bounded
    finally:
        _IN_PROCESS_WORKERS.reset(token)


def assigned_workers(*, item_count: int | None = None) -> int:
    """Return the fail-closed runtime worker allowance.

    ``item_count`` only lowers the central allowance; it can never raise it.
    This keeps empty/single-item calls on the historical calling thread.
    """
    context_budget = _IN_PROCESS_WORKERS.get()
    raw = (context_budget if context_budget is not None
           else os.environ.get(_WORKERS_ENV, "1"))
    return bounded_worker_count(raw, item_count=item_count)


def map_ordered(
        fn: Callable[[T], R], items: list[T] | tuple[T, ...], *,
        deadline_s: float | None = None,
) -> tuple[list[tuple[int, R]], list[int]]:
    """Apply ``fn`` with bounded fan-out and deterministic recomposition.

    Results are returned as ``(input_index, value)`` pairs in input order.
    The second value contains indexes that were not started because the
    optional deadline elapsed.  Running work is allowed to finish cleanly;
    no thread or provider operation is abandoned mid-flight.
    """
    values = list(items)
    if not values:
        return [], []
    workers = assigned_workers(item_count=len(values))
    started_at = time.monotonic()

    def _deadline_hit() -> bool:
        return bool(
            deadline_s is not None
            and time.monotonic() - started_at > max(0.0, deadline_s)
        )

    if workers == 1:
        completed: list[tuple[int, R]] = []
        for index, item in enumerate(values):
            if _deadline_hit():
                return completed, list(range(index, len(values)))
            completed.append((index, fn(item)))
        return completed, []

    completed_by_index: dict[int, R] = {}
    next_index = 0
    with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="metnos_item") as pool:
        pending = {}
        while next_index < len(values) and len(pending) < workers:
            future = pool.submit(fn, values[next_index])
            pending[future] = next_index
            next_index += 1

        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                completed_by_index[index] = future.result()
            while (next_index < len(values) and len(pending) < workers
                   and not _deadline_hit()):
                future = pool.submit(fn, values[next_index])
                pending[future] = next_index
                next_index += 1

    completed = sorted(completed_by_index.items())
    return completed, list(range(next_index, len(values)))
