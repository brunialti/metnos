"""Conservative admission for cross-step executor waves.

This module intentionally does not build a general speculative DAG.  The
first production-safe increment is smaller: two or more contiguous, static,
root read-only steps may overlap when every executor is already admitted by
the central scheduler.  Any ambiguity is a hard serial barrier.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Sequence


_DYNAMIC_REF_RE = re.compile(
    r"(?:\$\{|\{\{)\s*(?:step\d+|steps\.|FILLER:|RUNTIME:)",
    re.IGNORECASE,
)
_PIPE_KEYS = frozenset({"from_step", "from_steps", "entries"})


def enabled() -> bool:
    return os.environ.get("METNOS_ENGINE_PARALLEL_STEPS", "0").strip().lower() \
        in {"1", "true", "yes", "on"}


def _has_dynamic_input(args: object) -> bool:
    if not isinstance(args, dict):
        return True
    if _PIPE_KEYS.intersection(args):
        return True
    try:
        blob = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        return True
    return bool(_DYNAMIC_REF_RE.search(blob))


def static_read_candidate(step: object, executor: object | None) -> bool:
    """Fail-closed eligibility independent of deployment capacity."""
    if not enabled() or executor is None:
        return False
    if bool(getattr(step, "if_prev_entries_nonempty", False)):
        return False
    if _has_dynamic_input(getattr(step, "args", None)):
        return False
    if not bool(getattr(executor, "execution_policy_declared", False)):
        return False
    if getattr(executor, "standard_state", "") != "declared":
        return False
    if getattr(executor, "transport", "") == "in-process":
        return False
    policy = getattr(executor, "execution_policy", None)
    if not isinstance(policy, dict):
        return False
    return bool(
        policy.get("effect") == "read_only"
        and int(policy.get("parallelism_class") or 0) > 0
        and policy.get("equivalence_gate") == "verified"
    )


def contiguous_wave(
        steps: Sequence[object], start: int, catalog: dict[str, object],
        can_parallelize: Callable[[object], bool], *, max_wave: int = 8,
) -> list[int]:
    """Return a stable contiguous wave or ``[]`` when fewer than two qualify.

    A non-eligible step is a barrier.  We do not jump across it, even when a
    later step looks independent: implicit ordering remains serial by default.
    """
    indexes: list[int] = []
    upper = min(len(steps), start + max(2, int(max_wave)))
    for index in range(start, upper):
        step = steps[index]
        executor = catalog.get(str(getattr(step, "tool", "") or ""))
        if (not static_read_candidate(step, executor)
                or not can_parallelize(executor)):
            break
        indexes.append(index)
    return indexes if len(indexes) >= 2 else []
