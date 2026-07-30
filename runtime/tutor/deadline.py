"""One monotonic budget shared by every phase of a Tutor request."""

from __future__ import annotations

import math
import os
import time


class TutorDeadlineExceeded(TimeoutError):
    """The request exhausted its shared end-to-end budget."""


def _bounded_env_float(name: str, default: float,
                       low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def request_budget_s() -> float:
    return _bounded_env_float("METNOS_TUTOR_DEADLINE_S", 240.0, 10.0, 600.0)


def mode_budget_s() -> float:
    return _bounded_env_float("METNOS_TUTOR_MODE_DEADLINE_S", 45.0, 2.0, 120.0)


def new_deadline(seconds: float | None = None) -> float:
    budget = request_budget_s() if seconds is None else float(seconds)
    if not math.isfinite(budget) or budget <= 0:
        raise TutorDeadlineExceeded("invalid Tutor deadline")
    return time.monotonic() + budget


def phase_deadline(deadline_at: float, cap_s: float) -> float:
    local = time.monotonic() + max(0.0, float(cap_s))
    return min(float(deadline_at), local) if deadline_at else local


def remaining(deadline_at: float, *, cap_s: float | None = None) -> float:
    if not deadline_at:
        raise TutorDeadlineExceeded("Tutor deadline is missing")
    value = float(deadline_at) - time.monotonic()
    if cap_s is not None:
        value = min(value, float(cap_s))
    if not math.isfinite(value) or value <= 0:
        raise TutorDeadlineExceeded("Tutor deadline exhausted")
    return value


def require_commit_window(deadline_at: float, minimum_s: float = 1.0) -> float:
    """Reject new side effects too close to the delivery deadline."""

    value = remaining(deadline_at)
    if value <= max(0.0, float(minimum_s)):
        raise TutorDeadlineExceeded("Tutor commit window exhausted")
    return value
