"""Supervised lifecycle for the generic durable-workload worker.

This module owns process lifecycle only. It never accepts user requests and
never runs work in the HTTP process. The database fence remains authoritative
for every attempt; the process lock only prevents an accidental second local
supervisor from adding unnecessary contention.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import threading
import time
from collections.abc import Callable
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
        if current.tzinfo is None:
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


def health_snapshot(
    *,
    path: Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Read the bounded health observation without opening the workload DB."""

    try:
        raw = json.loads((path or default_health_path()).read_text(encoding="utf-8"))
        published_at = float(raw["published_at"])
        value = dict(raw["snapshot"])
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return _unavailable("health_unavailable")
    current = time.time() if now_epoch is None else float(now_epoch)
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
        or not isinstance(value.get("heartbeat_at"), str)
    ):
        return _unavailable("health_unavailable")
    return value


WorkerFactory = Callable[[DurableWorkloadStore], DurableWorker]
BridgeFactory = Callable[[DurableWorkloadStore], Any]
StoreFactory = Callable[[str | Path | None], DurableWorkloadStore]


class DurableWorkerService:
    """Cooperatively run a single generic bridge outside HTTP and chat."""

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
        self.enabled = feature_enabled() if enabled is None else bool(enabled)
        self._store_path = default_db_path() if store_path is None else store_path
        self._worker_factory = worker_factory
        self._bridge_factory = bridge_factory
        self._store_factory = store_factory
        self._health_path = health_path or default_health_path()
        self._poll_interval_s = float(poll_interval_s)
        self._recovery_batch_size = recovery_batch_size
        self._max_recovery_batches = max_recovery_batches
        self._store: DurableWorkloadStore | None = None
        self._worker: DurableWorker | None = None
        self._bridge: Any | None = None
        self._lock: ProcessLock | None = None
        self._started = False
        self._restart_required = False
        self._stop_event = threading.Event()
        self._consecutive_cycle_failures = 0
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

    def _set_health(
        self,
        state: DurableServiceState,
        reason_code: str,
        *,
        publish: bool = True,
    ) -> None:
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

    @staticmethod
    def _startup_reason(exc: Exception) -> str:
        if isinstance(exc, (SchemaTooNewError, MigrationError, StoreNotReadyError)):
            return "schema_incompatible"
        if isinstance(exc, sqlite3.Error):
            return "database_unavailable"
        return "startup_failed"

    @staticmethod
    def _reconcile_size(outcome: ReconcileOutcome) -> int:
        return (
            outcome.expired
            + outcome.returned_pending
            + outcome.retry_scheduled
            + outcome.failed_permanent
            + outcome.needs_attention
            + outcome.retry_promoted
        )

    def _recover(self) -> bool:
        """Run bounded recovery batches before permitting a new execution."""

        assert self._store is not None
        assert self._worker is not None
        for batch_number in range(1, self._max_recovery_batches + 1):
            outcome = self._worker.coordinator.reconcile(
                batch_size=self._recovery_batch_size,
            )
            materialized = self._store.materialize_all_ready_units(
                limit=self._recovery_batch_size,
            )
            log.info(
                "durable_worker_recovery batch=%d reconciled=%d materialized=%d",
                batch_number,
                self._reconcile_size(outcome),
                materialized,
            )
            if (
                self._reconcile_size(outcome) < self._recovery_batch_size
                and materialized < self._recovery_batch_size
            ):
                return True
        return False

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
            self._set_health(DurableServiceState.RECOVERING, "recovery_incomplete")
            if self._recover():
                self._set_health(DurableServiceState.READY, "none")
            else:
                self._set_health(DurableServiceState.RECOVERING, "recovery_incomplete")
        except Exception:
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

    def run_cycle(self) -> None:
        """Advance bounded recovery or run exactly one bridged unit."""

        if self._stop_event.is_set() or not self._started:
            return
        if self._health.state == DurableServiceState.RECOVERING.value:
            try:
                if self._recover():
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
        assert self._worker is not None
        assert self._bridge is not None
        try:
            self._bridge.run_once(self._worker)
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
        if self._store is not None:
            try:
                self._store.close()
            finally:
                self._store = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        self._worker = None
        self._bridge = None
        if self._started:
            self._set_health(DurableServiceState.DEGRADED, "stopped")
            log.info("durable_worker_stop")
        self._started = False


def default_service() -> DurableWorkerService:
    """Create the shipped dormant service; F11 supplies runtime bindings later."""

    return DurableWorkerService()


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
