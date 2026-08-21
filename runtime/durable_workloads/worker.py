"""Cooperative worker for fenced durable execution adapters.

This module is deliberately dormant until a caller drives ``run_once``.  While
an admitted unit is executing, a bounded helper thread renews file-backed
leases; it stops at completion, shutdown or the frozen stage deadline.  SQLite
lease/fence checks remain authoritative even if this process disappears.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from time import monotonic
from typing import Protocol

from .coordinator import (
    CommitOutcome,
    CommitStatus,
    DurableCoordinator,
    FailureOutcome,
    FailureStatus,
    Lease,
    LeaseMutationStatus,
    StructuredAttemptError,
    ValidatedResult,
    WorkerCapabilities,
    decide_retry,
    normalize_instant,
    parse_instant,
    require_lease_duration,
    require_worker_id,
)
from .migrations import BUSY_TIMEOUT_MS
from .models import AttemptState, ClosedStringEnum
from .storage import BudgetExceededError, DurableWorkloadStore


class WorkerRunStatus(ClosedStringEnum):
    IDLE = "idle"
    CONTROL_PROGRESS = "control_progress"
    STOPPED = "stopped"
    ABANDONED = "abandoned"
    COMMITTED = "committed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    FAILED = "failed"
    LOST_LEASE = "lost_lease"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """A validated payload plus the committed direct result lineage."""

    result: ValidatedResult
    dependency_result_ids: tuple[str, ...] = ()


class ExecutionAdapter(Protocol):
    def __call__(self, lease: Lease) -> ValidatedResult | ExecutionResult: ...


class ExecutionFailure(RuntimeError):
    """A bounded, already-redacted execution failure."""

    def __init__(
        self,
        error: StructuredAttemptError,
        *,
        attempt_state: AttemptState = AttemptState.FAILED,
    ) -> None:
        if not isinstance(error, StructuredAttemptError):
            raise TypeError("error must be StructuredAttemptError")
        if attempt_state not in {AttemptState.FAILED, AttemptState.TIMED_OUT}:
            raise ValueError("attempt_state must be failed or timed_out")
        super().__init__(error.error_class)
        self.error = error
        self.attempt_state = attempt_state


@dataclass(frozen=True, slots=True)
class WorkerRunOutcome:
    status: WorkerRunStatus
    lease: Lease | None = None
    commit: CommitOutcome | None = None
    failure: FailureOutcome | None = None


@dataclass(slots=True)
class _HeartbeatMonitor:
    stop: Event
    lease_lost: Event
    thread: Thread | None = None


class DurableWorker:
    """One cooperatively-driven worker with a bounded shutdown lifecycle."""

    def __init__(
        self,
        store: DurableWorkloadStore,
        worker_id: str,
        capabilities: WorkerCapabilities,
        *,
        lease_duration: timedelta,
        shutdown_grace: timedelta = timedelta(seconds=30),
        heartbeat_interval: timedelta | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.worker_id = require_worker_id(worker_id)
        self.capabilities = capabilities
        self.lease_duration = require_lease_duration(lease_duration)
        if not isinstance(shutdown_grace, timedelta):
            raise TypeError("shutdown_grace must be a timedelta")
        if shutdown_grace < timedelta(0) or shutdown_grace > timedelta(days=1):
            raise ValueError("shutdown_grace must be between zero and one day")
        self.shutdown_grace = shutdown_grace
        interval = (
            min(self.lease_duration / 3, timedelta(seconds=30))
            if heartbeat_interval is None
            else heartbeat_interval
        )
        if not isinstance(interval, timedelta):
            raise TypeError("heartbeat_interval must be a timedelta")
        if interval <= timedelta(0) or interval >= self.lease_duration:
            raise ValueError(
                "heartbeat_interval must be positive and shorter than the lease"
            )
        self.heartbeat_interval = interval
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or monotonic
        self.coordinator = DurableCoordinator(
            store,
            capabilities,
            lease_duration=self.lease_duration,
            clock=self._clock,
        )
        self._stop_requested_at: datetime | None = None
        self._shutdown_deadline: datetime | None = None
        self._active_lease: Lease | None = None
        self._execution_deadline_monotonic: float | None = None
        self._heartbeat_monitor: _HeartbeatMonitor | None = None

    @property
    def stopping(self) -> bool:
        return self._stop_requested_at is not None

    @property
    def active_lease(self) -> Lease | None:
        return self._active_lease

    @property
    def execution_overdue(self) -> bool:
        """Whether an in-process adapter has crossed its frozen deadline."""

        deadline = self._execution_deadline_monotonic
        return deadline is not None and self._monotonic_clock() >= deadline

    def _release_active_lease(self, lease: Lease) -> None:
        if self._active_lease == lease:
            self._active_lease = None
            self._execution_deadline_monotonic = None

    @property
    def shutdown_complete(self) -> bool:
        monitor = self._heartbeat_monitor
        heartbeat_done = (
            monitor is None
            or monitor.thread is None
            or not monitor.thread.is_alive()
        )
        return self.stopping and self._active_lease is None and heartbeat_done

    def request_stop(self, *, now: datetime | None = None) -> None:
        monitor = self._heartbeat_monitor
        if monitor is not None:
            monitor.stop.set()
        requested = normalize_instant(now or self._clock(), name="now")
        if self._stop_requested_at is None:
            self._stop_requested_at = requested
            self._shutdown_deadline = requested + self.shutdown_grace

    def claim_next(self) -> Lease | None:
        if self.stopping:
            return None
        lease = self.coordinator.claim(self.worker_id)
        if lease is not None:
            self._active_lease = lease
        return lease

    def heartbeat(
        self,
        lease: Lease,
        new_expiry: datetime,
        *,
        now: datetime | None = None,
    ) -> LeaseMutationStatus:
        if self.stopping:
            return LeaseMutationStatus.STOP_REQUESTED
        status = self.store.heartbeat(lease, new_expiry, now=now)
        if status in {
            LeaseMutationStatus.STALE_FENCE,
            LeaseMutationStatus.LEASE_EXPIRED,
        }:
            self._release_active_lease(lease)
        return status

    def abandon_if_shutdown_due(
        self,
        *,
        now: datetime | None = None,
    ) -> LeaseMutationStatus | None:
        if not self.stopping or self._active_lease is None:
            return None
        current = normalize_instant(now or self._clock(), name="now")
        assert self._shutdown_deadline is not None
        if current < self._shutdown_deadline:
            return None
        lease = self._active_lease
        status = self.store.abandon_attempt(
            lease,
            now=current,
            reason_code="worker_shutdown",
        )
        if status in {
            LeaseMutationStatus.APPLIED,
            LeaseMutationStatus.ALREADY_APPLIED,
            LeaseMutationStatus.STALE_FENCE,
        }:
            self._release_active_lease(lease)
        return status

    def _record_failure(
        self,
        lease: Lease,
        error: StructuredAttemptError,
        *,
        attempt_state: AttemptState = AttemptState.FAILED,
        now: datetime | None = None,
    ) -> WorkerRunOutcome:
        decision = decide_retry(
            effect_profile=lease.effect_profile,
            retry_policy=lease.retry_policy,
            attempt_number=lease.attempt_number,
            error_class=error.error_class,
            manual_retry=lease.manual_retry,
        )
        failure = self.store.fail_attempt(
            lease,
            error,
            decision,
            attempt_state=attempt_state,
            now=now or self._clock(),
        )
        self._release_active_lease(lease)
        status = (
            WorkerRunStatus.LOST_LEASE
            if failure.status in {
                FailureStatus.STALE_FENCE,
                FailureStatus.LEASE_EXPIRED,
                FailureStatus.INVALID_STATE,
            }
            else WorkerRunStatus.FAILED
        )
        return WorkerRunOutcome(status, lease=lease, failure=failure)

    def _start_heartbeat(
        self,
        lease: Lease,
        *,
        deadline: datetime,
        monotonic_deadline: float,
    ) -> _HeartbeatMonitor | None:
        """Renew a file-backed lease, bounded by the stage deadline."""

        if self.store.database_path is None:
            return None
        previous = self._heartbeat_monitor
        if (
            previous is not None
            and previous.thread is not None
            and previous.thread.is_alive()
        ):
            raise RuntimeError("a durable worker cannot run two heartbeat loops")
        monitor = _HeartbeatMonitor(Event(), Event())
        self._heartbeat_monitor = monitor
        persisted_expiry = min(
            parse_instant(lease.lease_expires_at, name="lease_expires_at"),
            deadline,
        )

        def renew() -> None:
            nonlocal persisted_expiry
            try:
                with self.store.open_peer() as heartbeat_store:
                    while True:
                        current = normalize_instant(
                            self._clock(), name="heartbeat clock",
                        )
                        remaining = min(
                            (deadline - current).total_seconds(),
                            monotonic_deadline - self._monotonic_clock(),
                        )
                        if remaining <= 0:
                            return
                        new_expiry = min(current + self.lease_duration, deadline)
                        if new_expiry > persisted_expiry:
                            status = heartbeat_store.heartbeat(
                                lease,
                                new_expiry,
                                now=current,
                            )
                            if status is not LeaseMutationStatus.APPLIED:
                                monitor.lease_lost.set()
                                return
                            persisted_expiry = new_expiry
                        wait_s = min(
                            self.heartbeat_interval.total_seconds(), remaining,
                        )
                        if monitor.stop.wait(wait_s):
                            return
                        current = normalize_instant(
                            self._clock(), name="heartbeat clock",
                        )
                        if (
                            monitor.stop.is_set()
                            or self.stopping
                            or current >= deadline
                            or self._monotonic_clock() >= monotonic_deadline
                        ):
                            return
            except Exception:
                # A renewal failure is indistinguishable from a lease at risk.
                # Discard the local result and let persistent reconciliation
                # decide the next safe action without leaking exception data.
                monitor.lease_lost.set()

        monitor.thread = Thread(
            target=renew,
            name="metnos-lre-heartbeat",
            daemon=True,
        )
        monitor.thread.start()
        return monitor

    def _finish_heartbeat(
        self,
        monitor: _HeartbeatMonitor | None,
    ) -> tuple[bool, bool]:
        if monitor is None:
            return False, False
        monitor.stop.set()
        assert monitor.thread is not None
        monitor.thread.join(timeout=BUSY_TIMEOUT_MS / 1000 + 1)
        still_running = monitor.thread.is_alive()
        if still_running:
            monitor.lease_lost.set()
            self.request_stop()
        elif self._heartbeat_monitor is monitor:
            self._heartbeat_monitor = None
        return monitor.lease_lost.is_set(), still_running

    def _record_timeout(
        self,
        lease: Lease,
        *,
        occurred_at: datetime,
        result_discarded: bool,
    ) -> WorkerRunOutcome:
        error = StructuredAttemptError.create(
            "executor_transient",
            code="execution.timeout",
            message_key="ERR_DURABLE_EXECUTION_FAILED",
            retry="automatic",
            occurred_at=occurred_at,
            details_redacted={
                "timeout_s": lease.timeout_s,
                "result_discarded": result_discarded,
            },
        )
        return self._record_failure(
            lease,
            error,
            attempt_state=AttemptState.TIMED_OUT,
            now=occurred_at,
        )

    def run_claimed(
        self,
        lease: Lease,
        adapter: ExecutionAdapter,
    ) -> WorkerRunOutcome:
        """Run one adapter with no open storage transaction."""
        if self.stopping:
            status = self.store.abandon_attempt(
                lease,
                now=self._clock(),
                reason_code="execution_not_started",
            )
            self._release_active_lease(lease)
            if status in {
                LeaseMutationStatus.APPLIED,
                LeaseMutationStatus.ALREADY_APPLIED,
            }:
                return WorkerRunOutcome(WorkerRunStatus.ABANDONED, lease=lease)
            return WorkerRunOutcome(WorkerRunStatus.LOST_LEASE, lease=lease)

        execution_started_at = normalize_instant(
            self._clock(), name="execution start",
        )
        monotonic_deadline = (
            self._monotonic_clock() + lease.timeout_s
        )
        running = self.store.mark_running(lease, now=execution_started_at)
        if running is not LeaseMutationStatus.APPLIED:
            self._release_active_lease(lease)
            return WorkerRunOutcome(WorkerRunStatus.LOST_LEASE, lease=lease)

        if self.store._connection.in_transaction:
            raise RuntimeError("durable execution must run outside a DB transaction")
        deadline = execution_started_at + timedelta(seconds=lease.timeout_s)
        self._execution_deadline_monotonic = monotonic_deadline
        monitor = self._start_heartbeat(
            lease,
            deadline=deadline,
            monotonic_deadline=monotonic_deadline,
        )
        adapter_result: ValidatedResult | ExecutionResult | None = None
        adapter_error: StructuredAttemptError | None = None
        adapter_attempt_state = AttemptState.FAILED
        try:
            adapter_result = adapter(lease)
        except ExecutionFailure as exc:
            adapter_error = exc.error
            adapter_attempt_state = exc.attempt_state
        except TimeoutError:
            adapter_error = StructuredAttemptError.create(
                "executor_transient",
                code="execution.timeout",
                message_key="ERR_DURABLE_EXECUTION_FAILED",
                retry="automatic",
                occurred_at=self._clock(),
                details_redacted={"transport_timeout": True},
            )
            adapter_attempt_state = AttemptState.TIMED_OUT
        except Exception:
            adapter_error = StructuredAttemptError.create(
                "executor_permanent",
                code="execution.unhandled_exception",
                message_key="ERR_DURABLE_EXECUTION_FAILED",
                retry="never",
                occurred_at=self._clock(),
                details_redacted={"exception_redacted": True},
            )
        finally:
            lease_lost, heartbeat_stuck = self._finish_heartbeat(monitor)

        if lease_lost or heartbeat_stuck:
            self._release_active_lease(lease)
            return WorkerRunOutcome(WorkerRunStatus.LOST_LEASE, lease=lease)
        finished_at = normalize_instant(self._clock(), name="execution finish")
        if (
            finished_at >= deadline
            or self._monotonic_clock() >= monotonic_deadline
        ):
            return self._record_timeout(
                lease,
                occurred_at=max(finished_at, deadline),
                result_discarded=adapter_error is None,
            )
        if adapter_error is not None:
            return self._record_failure(
                lease,
                adapter_error,
                attempt_state=adapter_attempt_state,
            )
        if isinstance(adapter_result, ExecutionResult):
            result = adapter_result.result
            dependency_result_ids = adapter_result.dependency_result_ids
        else:
            result = adapter_result
            dependency_result_ids = ()
        if not isinstance(result, ValidatedResult):
            error = StructuredAttemptError.create(
                "contract_violation",
                code="result.invalid_adapter_type",
                message_key="ERR_DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
                occurred_at=self._clock(),
                details_redacted={"result_type_valid": False},
            )
            return self._record_failure(lease, error)
        if result.schema_version != lease.output_schema_version:
            error = StructuredAttemptError.create(
                "contract_violation",
                code="result.schema_mismatch",
                message_key="ERR_DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
                occurred_at=self._clock(),
                details_redacted={
                    "expected_schema": lease.output_schema_version,
                    "received_schema": result.schema_version,
                },
            )
            return self._record_failure(lease, error)

        try:
            commit = self.store.commit_result(
                lease,
                result,
                dependency_result_ids=dependency_result_ids,
                now=self._clock(),
            )
        except BudgetExceededError as exc:
            error = StructuredAttemptError.create(
                "budget_exhausted",
                code="result.output_budget_exhausted",
                message_key="ERR_DURABLE_BUDGET_EXHAUSTED",
                retry="manual",
                occurred_at=self._clock(),
                details_redacted=exc.reason,
            )
            return self._record_failure(lease, error)
        if commit.status is CommitStatus.DEADLINE_EXPIRED:
            return self._record_timeout(
                lease,
                occurred_at=normalize_instant(
                    self._clock(), name="execution finish",
                ),
                result_discarded=True,
            )
        self._release_active_lease(lease)
        if commit.status is CommitStatus.COMMITTED:
            status = WorkerRunStatus.COMMITTED
        elif commit.status is CommitStatus.IDEMPOTENT_REPLAY:
            status = WorkerRunStatus.IDEMPOTENT_REPLAY
        else:
            status = WorkerRunStatus.LOST_LEASE
        return WorkerRunOutcome(status, lease=lease, commit=commit)

    def run_once(self, adapter: ExecutionAdapter) -> WorkerRunOutcome:
        if self.stopping:
            return WorkerRunOutcome(WorkerRunStatus.STOPPED)
        lease = self.claim_next()
        if lease is None:
            return WorkerRunOutcome(WorkerRunStatus.IDLE)
        return self.run_claimed(lease, adapter)


__all__ = [
    "ExecutionAdapter",
    "ExecutionFailure",
    "ExecutionResult",
    "DurableWorker",
    "WorkerRunOutcome",
    "WorkerRunStatus",
]
