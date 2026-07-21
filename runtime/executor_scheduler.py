"""Central execution policy, backpressure and metrics for all executors.

The scheduler is intentionally semantics-preserving by default:

* every missing or incomplete policy is ``serial``;
* the parallel pool is disabled unless ``METNOS_EXECUTOR_PARALLEL=1``;
* an executor must also carry a signed, loader-normalized ``safe`` policy and
  an equivalence admission marker before it can enter the pool;
* the synchronous invocation path remains the source of truth.

This module does not infer safety from names, capabilities or verbs.  Those are
useful planning facts, not proof that reordering calls is behaviorally safe.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from executor_metadata import DEFAULT_EXECUTION_POLICY
from logging_setup import get_logger


log = get_logger(__name__)
T = TypeVar("T")


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _class_overrides(raw: str | None) -> dict[str, int]:
    """Parse ``executor=class`` clamps; malformed entries fail closed.

    Overrides can only lower the signed manifest class.  They are operational
    deployment policy, never a way to grant authority absent from a manifest.
    """
    parsed: dict[str, int] = {}
    for item in str(raw or "").split(","):
        name, separator, value = item.strip().partition("=")
        if not separator or not name:
            continue
        try:
            level = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= level <= 3:
            parsed[name] = level
    return parsed


@dataclass
class _Metric:
    calls: int = 0
    failures: int = 0
    queue_ms_total: int = 0
    run_ms_total: int = 0
    in_flight: int = 0
    max_in_flight: int = 0

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "queue_ms_total": self.queue_ms_total,
            "run_ms_total": self.run_ms_total,
            "in_flight": self.in_flight,
            "max_in_flight": self.max_in_flight,
            "queue_ms_avg": (
                round(self.queue_ms_total / self.calls, 2) if self.calls else 0
            ),
            "run_ms_avg": (
                round(self.run_ms_total / self.calls, 2) if self.calls else 0
            ),
        }


@dataclass
class SchedulerMetrics:
    """Thread-safe, bounded in-memory scheduler telemetry.

    Only executor names and timings are retained; arguments and results are
    deliberately excluded so the metrics authority cannot become a data leak.
    """

    _by_executor: dict[str, _Metric] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def started(self, name: str, queue_ms: int) -> None:
        with self._lock:
            metric = self._by_executor.setdefault(name, _Metric())
            metric.calls += 1
            metric.queue_ms_total += queue_ms
            metric.in_flight += 1
            metric.max_in_flight = max(metric.max_in_flight, metric.in_flight)

    def finished(self, name: str, run_ms: int, *, failed: bool) -> None:
        with self._lock:
            metric = self._by_executor.setdefault(name, _Metric())
            metric.run_ms_total += run_ms
            metric.failures += int(failed)
            metric.in_flight = max(0, metric.in_flight - 1)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                name: metric.as_dict()
                for name, metric in sorted(self._by_executor.items())
            }


@dataclass
class _IdentityGuard:
    """One keyed serialization lock with bounded registry lifetime."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


class ExecutorScheduler:
    """Central scheduler with serial-first policy and bounded resources."""

    def __init__(
            self, *, max_workers: int | None = None,
            max_in_flight: int | None = None,
            parallel_enabled: bool | None = None,
            resource_limits: dict[str, int] | None = None,
            hardware_threads: int | None = None,
            max_parallelism_class: int | None = None,
            class_overrides: dict[str, int] | None = None) -> None:
        detected_threads = max(1, int(os.cpu_count() or 1))
        self.hardware_threads = max(
            1, min(detected_threads, int(hardware_threads or detected_threads)))
        class_3_limit = self.parallelism_limit(3)
        requested_workers = (
            int(max_workers) if max_workers is not None
            else _bounded_env_int(
                "METNOS_EXECUTOR_POOL_WORKERS", class_3_limit, 1,
                class_3_limit)
        )
        self.max_workers = max(1, min(class_3_limit, requested_workers))
        self.max_in_flight = max_in_flight or _bounded_env_int(
            "METNOS_EXECUTOR_MAX_IN_FLIGHT", 32, 1, 256)
        self.parallel_enabled = (
            _env_enabled("METNOS_EXECUTOR_PARALLEL", False)
            if parallel_enabled is None else bool(parallel_enabled)
        )
        self.max_parallelism_class = (
            _bounded_env_int("METNOS_EXECUTOR_MAX_CLASS", 3, 0, 3)
            if max_parallelism_class is None
            else max(0, min(3, int(max_parallelism_class)))
        )
        configured_overrides = _class_overrides(
            os.environ.get("METNOS_EXECUTOR_CLASS_OVERRIDES"))
        if class_overrides:
            configured_overrides.update({
                str(name): max(0, min(3, int(level)))
                for name, level in class_overrides.items()
            })
        self.class_overrides = configured_overrides
        self.metrics = SchedulerMetrics()
        self._global_slots = threading.BoundedSemaphore(self.max_in_flight)
        limits = resource_limits or {
            "default": self.max_in_flight,
            "local_io": _bounded_env_int(
                "METNOS_EXECUTOR_LOCAL_IO_LIMIT", 16, 1, 256),
            "network_io": _bounded_env_int(
                "METNOS_EXECUTOR_NETWORK_IO_LIMIT", 16, 1, 256),
            "cpu": _bounded_env_int(
                "METNOS_EXECUTOR_CPU_LIMIT", 2, 1, 64),
            "llm": _bounded_env_int(
                "METNOS_LLM_MAX_IN_FLIGHT", 1, 1, 32),
            "browser": _bounded_env_int(
                "METNOS_EXECUTOR_BROWSER_LIMIT", 4, 1, 64),
            "device": _bounded_env_int(
                "METNOS_EXECUTOR_DEVICE_LIMIT", 8, 1, 128),
        }
        self._resource_slots = {
            name: threading.BoundedSemaphore(max(1, int(limit)))
            for name, limit in limits.items()
        }
        self._pool: ThreadPoolExecutor | None = None
        self._pool_lock = threading.Lock()
        self._policy_slots: dict[tuple[str, int], threading.BoundedSemaphore] = {}
        self._policy_slots_lock = threading.Lock()
        self._identity_slots: dict[
            tuple[str, str, str], _IdentityGuard] = {}
        self._identity_slots_lock = threading.Lock()

    @staticmethod
    def policy_for(executor: object) -> dict:
        raw = getattr(executor, "execution_policy", None)
        if not isinstance(raw, dict):
            return dict(DEFAULT_EXECUTION_POLICY)
        policy = dict(DEFAULT_EXECUTION_POLICY)
        policy.update({key: raw[key] for key in policy if key in raw})
        # Defence in depth for old test doubles or a bypassed loader.
        level = policy.get("parallelism_class")
        effect = policy.get("effect")
        if (not isinstance(level, int) or isinstance(level, bool) or level <= 0
                or effect in {"unknown", "interactive"}
                or (effect != "read_only"
                    and policy.get("concurrency_key") == "none")
                or policy.get("equivalence_gate") != "verified"):
            policy["parallelism_class"] = 0
        return policy

    def parallelism_limit(self, level: int) -> int:
        """Translate class 0..3 to one hardware-bounded worker budget.

        0: caller thread only; 1: moderate (up to 2); 2: high (half of
        available threads, up to 8); 3: maximum bounded by detected hardware
        and an absolute safety ceiling of 32.
        """
        hw = max(1, int(getattr(self, "hardware_threads", 1)))
        if level <= 0:
            return 1
        if level == 1:
            return min(2, hw)
        if level == 2:
            return min(8, hw, max(2, (hw + 1) // 2))
        return min(32, hw)

    def effective_parallelism_class(self, executor: object) -> int:
        """Clamp the signed maximum with host/deployment capabilities.

        This is evaluated by the central scheduler loaded in the process.  A
        mono-accelerator deployment can therefore pin ``extract_entries=0``
        without changing its manifest, while no environment value can raise a
        legacy or unverified executor above its signed class.
        """
        policy = self.policy_for(executor)
        declared = int(policy.get("parallelism_class") or 0)
        name = str(getattr(executor, "name", "unknown") or "unknown")
        per_executor = self.class_overrides.get(name, 3)
        resource_class = str(policy.get("resource_class") or "default")
        resource_cap = 3
        if resource_class == "llm":
            resource_cap = _bounded_env_int(
                "METNOS_LLM_PARALLELISM_CLASS", 0, 0, 3)
        return max(0, min(
            declared, self.max_parallelism_class, per_executor, resource_cap))

    def can_parallelize(
            self, executor: object, *, concurrency_identity: str | None = None) -> bool:
        policy = self.policy_for(executor)
        key_kind = policy.get("concurrency_key", "none")
        level = self.effective_parallelism_class(executor)
        return bool(
            self.parallel_enabled
            and level > 0
            and policy["effect"] not in {"unknown", "interactive"}
            and (policy["effect"] == "read_only"
                 or key_kind != "none")
            and policy["equivalence_gate"] == "verified"
            and self.parallelism_limit(level) >= 2
            and (key_kind == "none" or bool(concurrency_identity))
        )

    def _executor_slot(
            self, executor: object,
            *, concurrency_identity: str | None = None,
    ) -> threading.BoundedSemaphore | None:
        if not self.can_parallelize(
                executor, concurrency_identity=concurrency_identity):
            return None
        name = str(getattr(executor, "name", "unknown") or "unknown")
        level = self.effective_parallelism_class(executor)
        limit = self.parallelism_limit(level)
        key = (name, limit)
        with self._policy_slots_lock:
            return self._policy_slots.setdefault(
                key, threading.BoundedSemaphore(limit))

    def _identity_slot(
            self, executor: object,
            *, concurrency_identity: str | None = None,
    ) -> tuple[tuple[str, str, str], _IdentityGuard] | None:
        policy = self.policy_for(executor)
        key_kind = str(policy.get("concurrency_key") or "none")
        if (key_kind == "none" or not concurrency_identity
                or not self.can_parallelize(
                    executor, concurrency_identity=concurrency_identity)):
            return None
        name = str(getattr(executor, "name", "unknown") or "unknown")
        key = (name, key_kind, str(concurrency_identity))
        with self._identity_slots_lock:
            guard = self._identity_slots.setdefault(key, _IdentityGuard())
            # Count both holders and waiters before acquiring the lock.  This
            # prevents a just-released key from being removed while another
            # caller is already waiting on the same guard.
            guard.users += 1
            return key, guard

    def _release_identity_slot(
            self, token: tuple[tuple[str, str, str], _IdentityGuard]) -> None:
        key, guard = token
        guard.lock.release()
        with self._identity_slots_lock:
            guard.users = max(0, guard.users - 1)
            if guard.users == 0 and self._identity_slots.get(key) is guard:
                self._identity_slots.pop(key, None)

    def invoke(
            self, executor: object, call: Callable[[], T],
            *, concurrency_identity: str | None = None) -> T:
        """Run one call with universal backpressure and metrics.

        The caller still executes synchronously.  Therefore adopting this
        method alone cannot reorder a plan or alter executor semantics.
        """
        name = str(getattr(executor, "name", "unknown") or "unknown")
        policy = self.policy_for(executor)
        resource_class = str(policy.get("resource_class") or "default")
        resource_slot = self._resource_slots.get(
            resource_class, self._resource_slots.get("default"))
        queued_at = time.perf_counter()
        self._global_slots.acquire()
        if resource_slot is not None:
            resource_slot.acquire()
        executor_slot = self._executor_slot(
            executor, concurrency_identity=concurrency_identity)
        if executor_slot is not None:
            executor_slot.acquire()
        identity_token = self._identity_slot(
            executor, concurrency_identity=concurrency_identity)
        if identity_token is not None:
            identity_token[1].lock.acquire()
        queue_ms = int((time.perf_counter() - queued_at) * 1000)
        self.metrics.started(name, queue_ms)
        started_at = time.perf_counter()
        failed = True
        try:
            result = call()
            failed = not isinstance(result, dict) or not bool(result.get("ok"))
            return result
        finally:
            run_ms = int((time.perf_counter() - started_at) * 1000)
            self.metrics.finished(name, run_ms, failed=failed)
            if identity_token is not None:
                self._release_identity_slot(identity_token)
            if executor_slot is not None:
                executor_slot.release()
            if resource_slot is not None:
                resource_slot.release()
            self._global_slots.release()
            log.info(
                "executor_scheduler name=%s class=%d queue_ms=%d run_ms=%d failed=%s",
                name, self.effective_parallelism_class(executor), queue_ms,
                run_ms, failed,
            )

    def _thread_pool(self) -> ThreadPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="metnos_executor",
                )
            return self._pool

    def submit(
            self, executor: object, call: Callable[[], T],
            *, concurrency_identity: str | None = None) -> Future[T]:
        """Submit only admitted opt-in calls; otherwise execute serially now.

        Returning a completed Future for serial policy gives the engine one API
        without silently moving legacy executors onto background threads.
        """
        if self.can_parallelize(
                executor, concurrency_identity=concurrency_identity):
            return self._thread_pool().submit(
                self.invoke, executor, call,
                concurrency_identity=concurrency_identity)
        future: Future[T] = Future()
        try:
            future.set_result(self.invoke(
                executor, call, concurrency_identity=concurrency_identity))
        except BaseException as exc:  # preserve synchronous exception semantics
            future.set_exception(exc)
        return future

    def shutdown(self, *, wait: bool = True) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=wait, cancel_futures=True)


_DEFAULT_SCHEDULER = ExecutorScheduler()
atexit.register(_DEFAULT_SCHEDULER.shutdown, wait=False)


def concurrency_identity_for(
        executor: object, args: dict | None,
        *, target_device: str | None = None) -> str | None:
    """Resolve one declared isolation identity from invocation facts.

    This function never grants a concurrency class; it only supplies the key
    required by an already signed non-read-only policy.  Ambiguous bulk path
    targets fail closed because one aggregate key could not serialize two
    partially-overlapping path sets.
    """
    policy = _DEFAULT_SCHEDULER.policy_for(executor)
    key_kind = str(policy.get("concurrency_key") or "none")
    values = args if isinstance(args, dict) else {}

    def scalar(*names: str):
        for name in names:
            value = values.get(name)
            if value not in (None, "", []):
                return value
        return None

    value = None
    if key_kind == "none":
        return None
    if key_kind == "device":
        value = target_device or scalar("device_id", "device", "target_device")
    elif key_kind == "account":
        value = scalar("account", "account_id")
    elif key_kind == "browser_session":
        value = scalar("session_id", "browser_session", "session")
    elif key_kind == "path":
        value = scalar("dest", "path", "output_path")
        if value is None:
            paths = values.get("paths")
            if isinstance(paths, (list, tuple)) and len(paths) == 1:
                value = paths[0]
    if isinstance(value, (list, tuple, set, dict)):
        # One-key isolation cannot safely represent partially overlapping
        # target sets.  Bulk targets remain serial until a multi-key scheduler
        # primitive is explicitly introduced and equivalence-tested.
        return None
    rendered = str(value or "").strip()
    return rendered or None


def invoke_scheduled(
        executor: object, call: Callable[[], T],
        *, concurrency_identity: str | None = None) -> T:
    return _DEFAULT_SCHEDULER.invoke(
        executor, call, concurrency_identity=concurrency_identity)


def can_schedule_parallel(
        executor: object, *, concurrency_identity: str | None = None) -> bool:
    """Return the central, deployment-aware admission decision.

    Engines must ask this choke-point instead of reimplementing manifest or
    environment policy.  The answer can only lower signed executor authority
    and is evaluated immediately before a wave is built.
    """
    return _DEFAULT_SCHEDULER.can_parallelize(
        executor, concurrency_identity=concurrency_identity)


def submit_scheduled(
        executor: object, call: Callable[[], T],
        *, concurrency_identity: str | None = None) -> Future[T]:
    return _DEFAULT_SCHEDULER.submit(
        executor, call, concurrency_identity=concurrency_identity)


def scheduler_metrics_snapshot() -> dict[str, dict]:
    return _DEFAULT_SCHEDULER.metrics.snapshot()


def assigned_worker_budget(executor: object) -> int:
    """Return the one centrally governed intra-executor worker allowance."""
    if not _DEFAULT_SCHEDULER.parallel_enabled:
        return 1
    budget = _DEFAULT_SCHEDULER.parallelism_limit(
        _DEFAULT_SCHEDULER.effective_parallelism_class(executor))
    policy = _DEFAULT_SCHEDULER.policy_for(executor)
    if policy.get("resource_class") == "llm":
        budget = min(
            budget,
            _bounded_env_int("METNOS_LLM_MAX_IN_FLIGHT", 1, 1, 32),
        )
    return max(1, budget)


def assigned_worker_environment(executor: object) -> dict[str, str]:
    """Return runtime-owned worker env for declared local or remote executors.

    Legacy executors did not share a worker contract and must retain their
    historical behavior. Generated and explicitly migrated manifests declare
    ``[execution]``; both transports receive the exact same bounded value.
    """
    if not getattr(executor, "execution_policy_declared", False):
        return {}
    return {
        "METNOS_EXECUTOR_ASSIGNED_WORKERS": str(
            assigned_worker_budget(executor)),
    }
