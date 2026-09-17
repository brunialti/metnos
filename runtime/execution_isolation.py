"""Bounded, process-local exclusion of overlapping invocation targets.

The caller must obtain targets from verified runtime facts, not from a job ID
or an untrusted assertion that work is independent. Missing targets exclude
every invocation of that executor. An empty set is reserved for an admitted
read-only policy. This primitive grants neither authority nor resources.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import threading
import time


def validate_targets(value: object) -> tuple[str, ...]:
    """Accept one bounded, canonical set; an empty tuple means no declaration."""
    if not isinstance(value, tuple) or len(value) > 256:
        raise ValueError("execution targets must be a bounded tuple")
    if any(not isinstance(item, str) or not item or "\x00" in item
           or len(item) > 4096 for item in value):
        raise ValueError("execution target identity is invalid")
    if tuple(sorted(set(value))) != value:
        raise ValueError("execution targets must be sorted and unique")
    if sum(len(item.encode("utf-8")) for item in value) > 65536:
        raise ValueError("execution targets exceed the byte limit")
    return value


@dataclass(frozen=True, eq=False, slots=True)
class _Ticket:
    executor: str
    kind: str
    targets: tuple[str, ...] | None


def _conflicts(left: _Ticket, right: _Ticket) -> bool:
    if left.executor != right.executor:
        return False
    if left.targets is None or right.targets is None or left.kind != right.kind:
        return True
    if left.kind == "path":
        # A directory operation and one of its descendants are not independent.
        # Resolvers supply canonical identities, including device/namespace
        # distinctions where required. No filesystem access happens here.
        # Search the descendant prefix, not just the adjacent sorted key:
        # '/a-other' sorts between '/a' and '/a/child'. A two-pointer merge
        # could skip the ancestor and incorrectly admit overlapping writers.
        for paths, candidates in ((left.targets, right.targets),
                                  (right.targets, left.targets)):
            for path in paths:
                position = bisect_left(candidates, path)
                if position < len(candidates) and candidates[position] == path:
                    return True
                prefix = path.rstrip("/") + "/"
                position = bisect_left(candidates, prefix)
                if position < len(candidates) and candidates[position].startswith(prefix):
                    return True
        return False
    return not set(left.targets).isdisjoint(right.targets)


class InvocationIsolation:
    """Atomically reserve all targets, with FIFO among conflicting requests.

    Disjoint requests can pass a blocked request. A later conflicting request
    cannot pass it, including a request with unknown targets, so a stream of
    partitioned work cannot starve a serial operation. The scheduler's global
    slots bound holders plus waiters; this class retains no completed keys.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: list[_Ticket] = []
        self._waiting: list[_Ticket] = []

    def acquire(
        self, executor: str, kind: str, targets: tuple[str, ...] | None,
        deadline: float | None,
    ) -> _Ticket | None:
        if targets is not None:
            validate_targets(targets)
        ticket = _Ticket(executor, kind, targets)
        with self._condition:
            self._waiting.append(ticket)
            try:
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        return None
                    earlier = self._waiting[:self._waiting.index(ticket)]
                    if not any(_conflicts(ticket, other)
                               for other in (*self._active, *earlier)):
                        self._active.append(ticket)
                        return ticket
                    remaining = None if deadline is None else deadline - time.monotonic()
                    self._condition.wait(remaining)
            finally:
                self._waiting.remove(ticket)
                self._condition.notify_all()

    def release(self, ticket: _Ticket) -> None:
        with self._condition:
            self._active.remove(ticket)
            self._condition.notify_all()

    def counts(self) -> tuple[int, int]:
        """Return holder/waiter counts without disclosing target identities."""
        with self._condition:
            return len(self._active), len(self._waiting)
