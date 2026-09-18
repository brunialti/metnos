"""Bound native CPU pools in isolated executor children, never in their parent.

Scheduler CPU tokens are logical shares, not cores. Divide the host's effective
CPU allowance by those shares and by item-level fan-out before creating BLAS or
ONNX pools. This is a concurrency ceiling, not exclusive CPU reservation.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV = "METNOS_EXECUTOR_NATIVE_THREADS"
_LIBRARIES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
              "RAYON_NUM_THREADS")


def effective_cpus() -> int:
    """Respect affinity and all visible cgroup-v2 CPU quotas on this host."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    root = Path("/sys/fs/cgroup")
    paths = [root]
    try:
        relative = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
                        if line.startswith("0::"))
        parts = Path(relative).parts
        if ".." not in parts:
            current = root.joinpath(*parts[1:])
            while current != root and root in current.parents:
                paths.append(current)
                current = current.parent
    except (OSError, StopIteration):
        pass
    for path in paths:
        try:
            quota, period = (path / "cpu.max").read_text().split()
            if quota != "max" and int(period) > 0 and int(quota) > 0:
                cpus = min(cpus, max(1, int(quota) // int(period)))
        except (OSError, ValueError):
            continue
    return max(1, cpus)


def _cap(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
        return min(default, value) if value > 0 else 1
    except (ValueError, TypeError):
        return 1


def child_environment(*, cpu_slots: int, claimed_cpu: int = 1,
                      item_workers: int = 1) -> dict[str, str]:
    """Project a fresh budget into a child without altering daemon threads."""
    slots = max(1, cpu_slots)
    share = max(1, min(claimed_cpu, slots))
    threads = max(1, effective_cpus() * share // slots // max(1, item_workers))
    threads = _cap(os.environ.get(_ENV), threads)
    environment = {_ENV: str(threads)}
    for name in _LIBRARIES:
        environment[name] = str(_cap(os.environ.get(name), threads))
    # Avoid nested OpenMP teams multiplying the item and native pool budgets.
    environment["OMP_MAX_ACTIVE_LEVELS"] = "1"
    return environment


def configure_onnx_threads(options) -> None:
    """Apply a child budget before ORT creates its per-session thread pools."""
    if _ENV not in os.environ:
        return
    threads = _cap(os.environ[_ENV], effective_cpus())
    existing = options.intra_op_num_threads
    options.intra_op_num_threads = min(existing, threads) if existing > 0 else threads
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
