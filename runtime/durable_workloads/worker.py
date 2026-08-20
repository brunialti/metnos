"""Cooperative F3 worker for dummy callables only.

This module is deliberately dormant: it starts no thread, process, timer or
service.  A caller may drive ``run_once`` from an explicit test loop.  SQLite
lease/fence checks remain authoritative even if this process disappears.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
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
    require_lease_duration,
    require_worker_id,
)
from .storage import DurableWorkloadStore


class _ClosedString(str, Enum):
    def __str__(self) -> str:
        return self.value


class WorkerRunStatus(_ClosedString):
    IDLE = "idle"
    STOPPED = "stopped"
    ABANDONED = "abandoned"
    COMMITTED = "committed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    FAILED = "failed"
    LOST_LEASE = "lost_lease"


class DummyExecutionAdapter(Protocol):
    def __call__(self, lease: Lease) -> ValidatedResult: ...


class DummyExecutionFailure(RuntimeError):
    """A test adapter's bounded, already-redacted execution failure."""

    def __init__(self, error: StructuredAttemptError) -> None:
        if not isinstance(error, StructuredAttemptError):
            raise TypeError("error must be StructuredAttemptError")
        super().__init__(error.error_class)
        self.error = error


@dataclass(frozen=True, slots=True)
class WorkerRunOutcome:
    status: WorkerRunStatus
    lease: Lease | None = None
    commit: CommitOutcome | None = None
    failure: FailureOutcome | None = None


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
        clock: Callable[[], datetime] | None = None,
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
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.coordinator = DurableCoordinator(
            store,
            capabilities,
            lease_duration=self.lease_duration,
            clock=self._clock,
        )
        self._stop_requested_at: datetime | None = None
        self._shutdown_deadline: datetime | None = None
        self._active_lease: Lease | None = None

    @property
    def stopping(self) -> bool:
        return self._stop_requested_at is not None

    @property
    def active_lease(self) -> Lease | None:
        return self._active_lease

    @property
    def shutdown_complete(self) -> bool:
        return self.stopping and self._active_lease is None

    def request_stop(self, *, now: datetime | None = None) -> None:
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
        } and self._active_lease == lease:
            self._active_lease = None
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
            self._active_lease = None
        return status

    def _record_failure(
        self,
        lease: Lease,
        error: StructuredAttemptError,
    ) -> WorkerRunOutcome:
        decision = decide_retry(
            effect_profile=lease.effect_profile,
            retry_policy=lease.retry_policy,
            attempt_number=lease.attempt_number,
            error_class=error.error_class,
        )
        failure = self.store.fail_attempt(
            lease,
            error,
            decision,
            now=self._clock(),
        )
        if self._active_lease == lease:
            self._active_lease = None
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

    def run_claimed(
        self,
        lease: Lease,
        adapter: DummyExecutionAdapter,
    ) -> WorkerRunOutcome:
        """Run one dummy callable with no open storage transaction."""
        if self.stopping:
            status = self.store.abandon_attempt(
                lease,
                now=self._clock(),
                reason_code="execution_not_started",
            )
            if self._active_lease == lease:
                self._active_lease = None
            if status in {
                LeaseMutationStatus.APPLIED,
                LeaseMutationStatus.ALREADY_APPLIED,
            }:
                return WorkerRunOutcome(WorkerRunStatus.ABANDONED, lease=lease)
            return WorkerRunOutcome(WorkerRunStatus.LOST_LEASE, lease=lease)

        running = self.store.mark_running(lease, now=self._clock())
        if running not in {
            LeaseMutationStatus.APPLIED,
            LeaseMutationStatus.ALREADY_APPLIED,
        }:
            if self._active_lease == lease:
                self._active_lease = None
            return WorkerRunOutcome(WorkerRunStatus.LOST_LEASE, lease=lease)

        if self.store._connection.in_transaction:
            raise RuntimeError("dummy execution must run outside a DB transaction")
        try:
            result = adapter(lease)
        except DummyExecutionFailure as exc:
            return self._record_failure(lease, exc.error)
        except Exception:
            error = StructuredAttemptError.create(
                "executor_permanent",
                code="dummy.execution_failed",
                message_key="DURABLE_DUMMY_EXECUTION_FAILED",
                retry="never",
                occurred_at=self._clock(),
                details_redacted={"exception_redacted": True},
            )
            return self._record_failure(lease, error)

        if not isinstance(result, ValidatedResult):
            error = StructuredAttemptError.create(
                "contract_violation",
                code="result.invalid_dummy_adapter_type",
                message_key="DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
                occurred_at=self._clock(),
                details_redacted={"result_type_valid": False},
            )
            return self._record_failure(lease, error)
        if result.schema_version != lease.output_schema_version:
            error = StructuredAttemptError.create(
                "contract_violation",
                code="result.schema_mismatch",
                message_key="DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
                occurred_at=self._clock(),
                details_redacted={
                    "expected_schema": lease.output_schema_version,
                    "received_schema": result.schema_version,
                },
            )
            return self._record_failure(lease, error)

        commit = self.store.commit_result(lease, result, now=self._clock())
        if self._active_lease == lease:
            self._active_lease = None
        if commit.status is CommitStatus.COMMITTED:
            status = WorkerRunStatus.COMMITTED
        elif commit.status is CommitStatus.IDEMPOTENT_REPLAY:
            status = WorkerRunStatus.IDEMPOTENT_REPLAY
        else:
            status = WorkerRunStatus.LOST_LEASE
        return WorkerRunOutcome(status, lease=lease, commit=commit)

    def run_once(self, adapter: DummyExecutionAdapter) -> WorkerRunOutcome:
        if self.stopping:
            return WorkerRunOutcome(WorkerRunStatus.STOPPED)
        lease = self.claim_next()
        if lease is None:
            return WorkerRunOutcome(WorkerRunStatus.IDLE)
        return self.run_claimed(lease, adapter)


__all__ = [
    "DummyExecutionAdapter",
    "DummyExecutionFailure",
    "DurableWorker",
    "WorkerRunOutcome",
    "WorkerRunStatus",
]
