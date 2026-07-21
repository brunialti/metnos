"""Shared, side-effect-free worker-budget calculation."""
from __future__ import annotations

ABSOLUTE_WORKER_CAP = 32


def bounded_worker_count(
        value: object, *, default: int = 1, maximum: int = ABSOLUTE_WORKER_CAP,
        cpu_count: int | None = None, item_count: int | None = None,
) -> int:
    """Return a fail-closed worker count with all applicable ceilings."""
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = int(default)
    upper = max(1, min(ABSOLUTE_WORKER_CAP, int(maximum)))
    if cpu_count is not None:
        try:
            upper = min(upper, max(1, int(cpu_count)))
        except (TypeError, ValueError):
            upper = min(upper, 1)
    if item_count is not None:
        try:
            upper = min(upper, max(1, int(item_count)))
        except (TypeError, ValueError):
            upper = min(upper, 1)
    return max(1, min(upper, workers))
