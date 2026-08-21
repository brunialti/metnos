"""Supervised lifecycle for the generic durable-workload worker.

This module owns process lifecycle only. It never accepts user requests and
never runs work in the HTTP process. The database fence remains authoritative
for every attempt; the process lock only prevents an accidental second local
supervisor from adding unnecessary contention.
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import config
from process_lock import ProcessLock

from .coordinator import ReconcileOutcome
from .migrations import MigrationError, SchemaTooNewError, default_db_path
from .storage import DurableWorkloadStore, StoreNotReadyError
from .worker import DurableWorker


log = logging.getLogger("metnos.durable_workloads.service")

HEALTH_SCHEMA_VERSION = "metnos.durable-worker-health/1"
_PUBLISHED_MAX_AGE_S = 90.0
_HEALTH_PULSE_INTERVAL_S = 30.0
_HEALTH_PULSE_JOIN_TIMEOUT_S = 1.0
_PARALLEL_SHUTDOWN_TIMEOUT_S = 30.0
_MAX_PARALLEL_WORKERS = 32
_HEALTH_MAX_BYTES = 16_384
_HEALTH_REASONS = frozenset({
    "none",
    "feature_disabled",
    "runtime_bindings_unavailable",
    "already_active",
    "schema_incompatible",
    "database_unavailable",
    "startup_failed",
    "recovery_incomplete",
    "recovery_failed",
    "worker_cycle_failed",
    "execution_deadline_exceeded",
    "stopped",
    "health_unavailable",
    "health_stale",
})


class DurableServiceState(str, Enum):
    READY = "ready"
    RECOVERING = "recovering"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class DurableServiceHealth:
    """Closed, non-sensitive observation published by the worker process."""

    schema_version: str
    state: str
    enabled: bool
    worker_available: bool
    reason_code: str
    heartbeat_at: str

    @classmethod
    def create(
        cls,
        state: DurableServiceState,
        *,
        enabled: bool,
        worker_available: bool,
        reason_code: str,
        now: datetime | None = None,
    ) -> "DurableServiceHealth":
        if reason_code not in _HEALTH_REASONS:
            raise ValueError("durable service health reason is not closed")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("durable service health time must be timezone-aware")
        return cls(
            schema_version=HEALTH_SCHEMA_VERSION,
            state=state.value,
            enabled=bool(enabled),
            worker_available=bool(worker_available),
            reason_code=reason_code,
            heartbeat_at=current.astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
        )


def feature_enabled() -> bool:
    """Read the narrow lifecycle gate; an unknown value fails closed."""

    return os.environ.get("METNOS_DURABLE_WORKLOADS_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def default_health_path() -> Path:
    return Path(config.PATH_DURABLE_WORKLOADS) / "service_health.json"


def _unavailable(reason_code: str) -> dict[str, Any]:
    return asdict(DurableServiceHealth.create(
        DurableServiceState.DEGRADED,
        enabled=feature_enabled(),
        worker_available=False,
        reason_code=reason_code,
    ))


def publish_health(value: DurableServiceHealth, *, path: Path | None = None) -> None:
    """Atomically publish one validated health snapshot to local observers."""

    if not isinstance(value, DurableServiceHealth):
        raise TypeError("value must be DurableServiceHealth")
    payload = {
        "published_at": time.time(),
        "snapshot": asdict(value),
    }
    config.write_private_text(
        path or default_health_path(),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def _read_bounded_health(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _HEALTH_MAX_BYTES:
            raise ValueError("durable health snapshot is not a bounded file")
        chunks: list[bytes] = []
        total = 0
        while total <= _HEALTH_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(4096, _HEALTH_MAX_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > _HEALTH_MAX_BYTES:
            raise ValueError("durable health snapshot exceeds its boundary")
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(descriptor)


def health_snapshot(
    *,
    path: Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Read the bounded health observation without opening the workload DB."""

    try:
        raw = json.loads(_read_bounded_health(path or default_health_path()))
        published_at = float(raw["published_at"])
        value = dict(raw["snapshot"])
        current = time.time() if now_epoch is None else float(now_epoch)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return _unavailable("health_unavailable")
    if not math.isfinite(published_at) or not math.isfinite(current):
        return _unavailable("health_unavailable")
    if published_at > current + 5.0 or current - published_at > _PUBLISHED_MAX_AGE_S:
        return _unavailable("health_stale")
    expected = set(DurableServiceHealth.__dataclass_fields__)
    if set(value) != expected:
        return _unavailable("health_unavailable")
    if (
        value.get("schema_version") != HEALTH_SCHEMA_VERSION
        or value.get("state") not in {state.value for state in DurableServiceState}
        or not isinstance(value.get("enabled"), bool)
        or not isinstance(value.get("worker_available"), bool)
        or value.get("reason_code") not in _HEALTH_REASONS
        or not _valid_heartbeat(value.get("heartbeat_at"))
    ):
        return _unavailable("health_unavailable")
    return value


def _valid_heartbeat(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


WorkerFactory = Callable[[DurableWorkloadStore], DurableWorker]
BridgeFactory = Callable[[DurableWorkloadStore], Any]
StoreFactory = Callable[[str | Path | None], DurableWorkloadStore]


class DurableWorkerService:
    """Cooperatively run bounded generic bridge lanes outside HTTP and chat."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        store_path: str | Path | None = None,
        worker_factory: WorkerFactory | None = None,
        bridge_factory: BridgeFactory | None = None,
        store_factory: StoreFactory = DurableWorkloadStore.open,
        health_path: Path | None = None,
        poll_interval_s: float = 1.0,
        recovery_batch_size: int = 100,
        max_recovery_batches: int = 50,
        parallel_workers: int | None = None,
    ) -> None:
        if (
            isinstance(poll_interval_s, bool)
            or not isinstance(poll_interval_s, (int, float))
            or not 0.05 <= poll_interval_s <= 60
        ):
            raise ValueError("poll_interval_s must be between 0.05 and 60")
        if isinstance(recovery_batch_size, bool) or not 1 <= recovery_batch_size <= 1000:
            raise ValueError("recovery_batch_size must be an integer in 1..1000")
        if isinstance(max_recovery_batches, bool) or not 1 <= max_recovery_batches <= 1000:
            raise ValueError("max_recovery_batches must be an integer in 1..1000")
        if parallel_workers is None:
            try:
                parallel_workers = int(
                    os.environ.get("METNOS_DURABLE_WORKERS", "1")
                )
            except (TypeError, ValueError):
                parallel_workers = 1
        if (
            isinstance(parallel_workers, bool)
            or not isinstance(parallel_workers, int)
            or not 1 <= parallel_workers <= _MAX_PARALLEL_WORKERS
        ):
            raise ValueError(
                f"parallel_workers must be an integer in 1..{_MAX_PARALLEL_WORKERS}"
            )
        self.enabled = feature_enabled() if enabled is None else bool(enabled)
        self._store_path = default_db_path() if store_path is None else store_path
        self._worker_factory = worker_factory
        self._bridge_factory = bridge_factory
        self._store_factory = store_factory
        self._health_path = health_path or default_health_path()
        self._poll_interval_s = float(poll_interval_s)
        self._recovery_batch_size = recovery_batch_size
        self._max_recovery_batches = max_recovery_batches
        self._requested_parallel_workers = parallel_workers
        self._effective_parallel_workers = 1
        self._store: DurableWorkloadStore | None = None
        self._worker: DurableWorker | None = None
        self._bridge: Any | None = None
        self._lock: ProcessLock | None = None
        self._started = False
        self._restart_required = False
        self._stop_event = threading.Event()
        self._consecutive_cycle_failures = 0
        self._cycle_guard = threading.Lock()
        self._parallel_guard = threading.Lock()
        self._parallel_futures: dict[Future[Any], int] = {}
        self._active_parallel_workers: dict[int, tuple[Any, Any]] = {}
        self._health_pulse_guard = threading.Lock()
        self._health_pulse_thread: threading.Thread | None = None
        self._health_pulse_stop: threading.Event | None = None
        self._health = DurableServiceHealth.create(
            DurableServiceState.DEGRADED,
            enabled=self.enabled,
            worker_available=False,
            reason_code="health_unavailable",
        )

    @property
    def health(self) -> DurableServiceHealth:
        return self._health

    @property
    def worker(self) -> DurableWorker | None:
        return self._worker

    @property
    def parallel_workers(self) -> int:
        """Effective lanes after the central scheduler's deployment clamp."""

        return self._effective_parallel_workers

    def _set_health(
        self,
        state: DurableServiceState,
        reason_code: str,
        *,
        publish: bool = True,
    ) -> None:
        if state is DurableServiceState.READY and self._execution_overdue():
            state = DurableServiceState.DEGRADED
            reason_code = "execution_deadline_exceeded"
        previous = self._health
        value = DurableServiceHealth.create(
            state,
            enabled=self.enabled,
            worker_available=self._worker is not None and self._bridge is not None,
            reason_code=reason_code,
        )
        self._health = value
        if publish:
            try:
                publish_health(value, path=self._health_path)
            except Exception:
                log.warning(
                    "durable_worker_health_publish_failed state=%s reason_code=%s",
                    state.value,
                    reason_code,
                )
        if previous.state != state.value or previous.reason_code != reason_code:
            log.info(
                "durable_worker_state state=%s reason_code=%s",
                state.value,
                reason_code,
            )

    def _execution_overdue(self) -> bool:
        """Detect a lane that can no longer make an authoritative commit."""

        workers: list[Any] = []
        if self._worker is not None:
            workers.append(self._worker)
        with self._parallel_guard:
            workers.extend(
                worker for worker, _store in self._active_parallel_workers.values()
            )
        return any(bool(getattr(worker, "execution_overdue", False)) for worker in workers)

    def _with_health_pulse(
        self,
        operation: Callable[[], Any],
        *,
        state: DurableServiceState,
        reason_code: str,
    ) -> Any:
        """Keep long synchronous work observable without moving DB ownership."""

        stopped = threading.Event()

        def pulse() -> None:
            while not stopped.wait(_HEALTH_PULSE_INTERVAL_S):
                self._set_health(state, reason_code)

        thread = threading.Thread(
            target=pulse,
            name="metnos-lre-health-pulse",
            daemon=True,
        )
        with self._health_pulse_guard:
            previous = self._health_pulse_thread
            if previous is not None and previous.is_alive():
                raise RuntimeError("durable health pulse is still running")
            self._health_pulse_thread = thread
            self._health_pulse_stop = stopped
        thread.start()
        try:
            return operation()
        finally:
            stopped.set()
            thread.join(timeout=_HEALTH_PULSE_JOIN_TIMEOUT_S)
            if not thread.is_alive():
                with self._health_pulse_guard:
                    if self._health_pulse_thread is thread:
                        self._health_pulse_thread = None
                        self._health_pulse_stop = None

    @staticmethod
    def _startup_reason(exc: Exception) -> str:
        if isinstance(exc, (SchemaTooNewError, MigrationError, StoreNotReadyError)):
            return "schema_incompatible"
        if isinstance(exc, sqlite3.Error):
            return "database_unavailable"
        return "startup_failed"

    @staticmethod
    def _reconcile_size(outcome: ReconcileOutcome) -> int:
        # The disposition counters partition ``expired``; summing both would
        # count each expired lease twice and force unnecessary recovery scans.
        return outcome.expired + outcome.retry_promoted

    def _recover(self) -> bool:
        """Run bounded recovery batches before permitting a new execution."""

        assert self._store is not None
        assert self._worker is not None
        for batch_number in range(1, self._max_recovery_batches + 1):
            outcome = self._worker.coordinator.reconcile(
                batch_size=self._recovery_batch_size,
            )
            reused = self._store.adopt_reusable_results(
                limit=self._recovery_batch_size,
            )
            materialized = self._store.materialize_all_ready_units(
                limit=self._recovery_batch_size,
            )
            settled = self._store.settle_workloads(
                limit=self._recovery_batch_size,
            )
            completed = self._store.complete_ready_workloads(
                limit=self._recovery_batch_size,
            )
            pruned = self._store.prune_outbox(
                limit=self._recovery_batch_size,
            )
            log.info(
                "durable_worker_recovery batch=%d reconciled=%d "
                "reused=%d materialized=%d settled=%d completed=%d "
                "outbox_pruned=%d",
                batch_number,
                self._reconcile_size(outcome),
                reused,
                materialized,
                settled,
                completed,
                pruned,
            )
            if (
                self._reconcile_size(outcome) < self._recovery_batch_size
                and reused < self._recovery_batch_size
                and materialized < self._recovery_batch_size
                and settled < self._recovery_batch_size
                and completed < self._recovery_batch_size
                and pruned < self._recovery_batch_size
            ):
                return True
        return False

    def _configure_parallelism(self) -> None:
        """Clamp controller lanes to the one central scheduler pool."""

        if self._requested_parallel_workers <= 1:
            self._effective_parallel_workers = 1
            return
        from executor_scheduler import orchestration_capacity

        self._effective_parallel_workers = max(
            1,
            min(
                self._requested_parallel_workers,
                orchestration_capacity(),
            ),
        )
        if self._effective_parallel_workers < self._requested_parallel_workers:
            log.info(
                "durable_worker_parallelism_clamped requested=%d effective=%d",
                self._requested_parallel_workers,
                self._effective_parallel_workers,
            )

    def _run_parallel_once(self, lane: int) -> Any:
        """Open thread-owned bindings, execute one unit, then close them."""

        store = None
        worker = None
        bridge = None
        registered = False
        try:
            store = self._store_factory(self._store_path)
            if self._worker_factory is None or self._bridge_factory is None:
                raise RuntimeError("durable parallel bindings are unavailable")
            worker = self._worker_factory(store)
            bridge = self._bridge_factory(store)
            worker_id = getattr(worker, "worker_id", None)
            if (
                not isinstance(worker_id, str)
                or not worker_id
                or len(worker_id) > 128
            ):
                raise RuntimeError(
                    "parallel durable workers need a unique bounded worker_id"
                )
            with self._parallel_guard:
                active_ids = {
                    str(getattr(active, "worker_id", ""))
                    for active, _store in self._active_parallel_workers.values()
                }
                if worker_id in active_ids or lane in self._active_parallel_workers:
                    raise RuntimeError("parallel durable worker identity is already active")
                self._active_parallel_workers[lane] = (worker, store)
                registered = True
            if self._stop_event.is_set():
                worker.request_stop()
                return None
            return bridge.run_once(worker)
        finally:
            if registered:
                with self._parallel_guard:
                    self._active_parallel_workers.pop(lane, None)
            try:
                if bridge is not None:
                    close = getattr(bridge, "close", None)
                    if callable(close):
                        close()
            finally:
                if store is not None:
                    store.close()

    def _reap_parallel(self) -> tuple[int, int]:
        """Return ``(completed, failed)`` for finished controller futures."""

        with self._parallel_guard:
            completed = tuple(
                future for future in self._parallel_futures if future.done()
            )
            for future in completed:
                self._parallel_futures.pop(future, None)
        failures = 0
        for future in completed:
            try:
                future.result()
            except CancelledError:
                if not self._stop_event.is_set():
                    failures += 1
            except Exception:
                failures += 1
                log.exception("durable_worker_parallel_cycle_failed")
        return len(completed), failures

    def _run_parallel_cycle(self) -> None:
        completed, failures = self._reap_parallel()
        if failures:
            self._consecutive_cycle_failures += failures
            self._set_health(DurableServiceState.DEGRADED, "worker_cycle_failed")
            if self._consecutive_cycle_failures >= 3:
                raise RuntimeError("durable worker cycle failed repeatedly")
            return
        if completed:
            self._consecutive_cycle_failures = 0
        if self._stop_event.is_set():
            return

        from executor_scheduler import (
            SchedulerOrchestrationSaturated,
            submit_orchestration,
        )

        with self._parallel_guard:
            busy_lanes = set(self._parallel_futures.values())
        for lane in range(self._effective_parallel_workers):
            if lane in busy_lanes:
                continue
            try:
                future = submit_orchestration(
                    lambda selected=lane: self._run_parallel_once(selected)
                )
            except SchedulerOrchestrationSaturated:
                break
            with self._parallel_guard:
                self._parallel_futures[future] = lane
            busy_lanes.add(lane)
        if self._consecutive_cycle_failures:
            self._set_health(
                DurableServiceState.DEGRADED,
                "worker_cycle_failed",
            )
        else:
            self._set_health(DurableServiceState.READY, "none")

    def start(self) -> bool:
        """Migrate, acquire the local supervisor lock, then recover in batches."""

        if self._started:
            return True
        log.info("durable_worker_start enabled=%s", self.enabled)
        if self.enabled and self._worker_factory is not None and self._bridge_factory is not None:
            lock = ProcessLock(
                Path(self._store_path).with_name("worker_supervisor.lock"),
                owner="durable_worker",
            )
            try:
                config.ensure_private_dir(lock.path.parent)
                lock.acquire()
            except RuntimeError:
                # A second process must never replace the healthy owner's snapshot.
                self._set_health(
                    DurableServiceState.DEGRADED,
                    "already_active",
                    publish=False,
                )
                return False
            self._lock = lock
        try:
            self._store = self._store_factory(self._store_path)
        except Exception as exc:
            reason_code = self._startup_reason(exc)
            self._set_health(DurableServiceState.DEGRADED, reason_code)
            self._restart_required = reason_code != "schema_incompatible"
            if self._lock is not None:
                self._lock.release()
                self._lock = None
            return False
        self._started = True
        if not self.enabled:
            self._set_health(DurableServiceState.DEGRADED, "feature_disabled")
            return True
        if self._worker_factory is None or self._bridge_factory is None:
            self._set_health(
                DurableServiceState.DEGRADED,
                "runtime_bindings_unavailable",
            )
            return True

        try:
            self._worker = self._worker_factory(self._store)
            self._bridge = self._bridge_factory(self._store)
            self._configure_parallelism()
            self._set_health(DurableServiceState.RECOVERING, "recovery_incomplete")
            recovered = self._with_health_pulse(
                self._recover,
                state=DurableServiceState.RECOVERING,
                reason_code="recovery_incomplete",
            )
            if recovered:
                self._set_health(DurableServiceState.READY, "none")
            else:
                self._set_health(DurableServiceState.RECOVERING, "recovery_incomplete")
        except Exception:
            if self._bridge is not None:
                close = getattr(self._bridge, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        log.exception("durable_worker_bridge_close_failed")
            self._worker = None
            self._bridge = None
            self._set_health(DurableServiceState.DEGRADED, "recovery_failed")
            self._restart_required = True
            self._store.close()
            self._store = None
            if self._lock is not None:
                self._lock.release()
                self._lock = None
            self._started = False
            return False
        return True

    def request_stop(self) -> None:
        """Request cooperative shutdown; active attempt fencing stays in the DB."""

        self._stop_event.set()
        if self._worker is not None:
            self._worker.request_stop()
        with self._parallel_guard:
            active = tuple(
                worker for worker, _store in self._active_parallel_workers.values()
            )
        for worker in active:
            try:
                worker.request_stop()
            except Exception:
                log.warning("durable_parallel_worker_stop_request_failed")

    def run_cycle(self) -> None:
        """Advance bounded recovery or run exactly one bridged unit."""

        with self._cycle_guard:
            self._run_cycle_locked()

    def _run_cycle_locked(self) -> None:
        """Cycle implementation serialized against direct shutdown."""

        if self._stop_event.is_set() or not self._started:
            return
        if self._health.state == DurableServiceState.RECOVERING.value:
            try:
                recovered = self._with_health_pulse(
                    self._recover,
                    state=DurableServiceState.RECOVERING,
                    reason_code="recovery_incomplete",
                )
                if recovered:
                    self._set_health(DurableServiceState.READY, "none")
                else:
                    self._set_health(DurableServiceState.RECOVERING, "recovery_incomplete")
            except Exception:
                self._set_health(DurableServiceState.DEGRADED, "recovery_failed")
                raise RuntimeError("durable worker recovery failed")
            return
        if (
            self._health.state != DurableServiceState.READY.value
            and self._health.reason_code != "worker_cycle_failed"
        ):
            return
        if self._effective_parallel_workers > 1:
            self._run_parallel_cycle()
            return
        assert self._worker is not None
        assert self._bridge is not None
        try:
            self._with_health_pulse(
                lambda: self._bridge.run_once(self._worker),
                state=DurableServiceState.READY,
                reason_code="none",
            )
            self._consecutive_cycle_failures = 0
            self._set_health(DurableServiceState.READY, "none")
        except Exception:
            self._consecutive_cycle_failures += 1
            self._set_health(DurableServiceState.DEGRADED, "worker_cycle_failed")
            if self._consecutive_cycle_failures >= 3:
                raise RuntimeError("durable worker cycle failed repeatedly")

    def run_forever(self) -> int:
        """Run until SIGTERM/interrupt; a duplicate supervisor exits cleanly."""

        if not self.start():
            return 1 if self._restart_required else 0
        exit_code = 0
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_cycle()
                except RuntimeError:
                    exit_code = 1
                    break
                self._stop_event.wait(self._poll_interval_s)
        finally:
            self.stop()
        return exit_code

    def stop(self) -> None:
        """Release local lifecycle resources without modifying durable results."""

        self.request_stop()
        with self._health_pulse_guard:
            pulse_stop = self._health_pulse_stop
            pulse_thread = self._health_pulse_thread
        if pulse_stop is not None:
            pulse_stop.set()
        if pulse_thread is not None and pulse_thread is not threading.current_thread():
            pulse_thread.join(timeout=_HEALTH_PULSE_JOIN_TIMEOUT_S)
        cycle_acquired = self._cycle_guard.acquire(
            timeout=_PARALLEL_SHUTDOWN_TIMEOUT_S
        )
        if not cycle_acquired:
            self._set_health(DurableServiceState.DEGRADED, "worker_cycle_failed")
            log.error("durable_worker_stop_timed_out_waiting_for_cycle")
            return
        try:
            with self._parallel_guard:
                futures = tuple(self._parallel_futures)
            for future in futures:
                future.cancel()
            unfinished: set[Future[Any]] = set()
            if futures:
                _done, unfinished = wait(
                    futures,
                    timeout=_PARALLEL_SHUTDOWN_TIMEOUT_S,
                )
            if unfinished:
                self._set_health(
                    DurableServiceState.DEGRADED,
                    "worker_cycle_failed",
                )
                log.error(
                    "durable_worker_parallel_stop_timed_out active=%d",
                    len(unfinished),
                )
            if self._bridge is not None:
                close = getattr(self._bridge, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        log.exception("durable_worker_bridge_close_failed")
                self._bridge = None
            if self._store is not None:
                try:
                    self._store.close()
                finally:
                    self._store = None
            if unfinished:
                # Keep the process lock and lifecycle bindings: a replacement
                # supervisor must not overlap work that ignored cooperative
                # shutdown.  The external service manager remains the final
                # bounded process-group terminator.
                return
            if self._lock is not None:
                self._lock.release()
                self._lock = None
            self._worker = None
            self._bridge = None
            if self._started and not unfinished:
                self._set_health(DurableServiceState.DEGRADED, "stopped")
                log.info("durable_worker_stop")
            self._started = False
            self._effective_parallel_workers = 1
        finally:
            self._cycle_guard.release()


def default_service() -> DurableWorkerService:
    """Create the gated service with lazily composed production bindings."""

    enabled = feature_enabled()
    if not enabled:
        return DurableWorkerService(enabled=False)
    from .runtime_bindings import production_factories

    worker_factory, bridge_factory = production_factories()
    return DurableWorkerService(
        enabled=True,
        worker_factory=worker_factory,
        bridge_factory=bridge_factory,
    )


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("METNOS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = default_service()

    def _stop(_signum: int, _frame: object) -> None:
        service.request_stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return service.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DurableServiceHealth",
    "DurableServiceState",
    "DurableWorkerService",
    "default_health_path",
    "default_service",
    "feature_enabled",
    "health_snapshot",
    "main",
    "publish_health",
]
