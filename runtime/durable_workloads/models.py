"""Closed state vocabularies and immutable durable-workload records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ClosedStringEnum(str, Enum):
    """String enum with stable storage values and readable serialization."""

    def __str__(self) -> str:
        return self.value


class WorkloadState(ClosedStringEnum):
    DRAFT = "draft"
    ADMITTED = "admitted"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    COMPLETED = "completed"


class UnitState(ClosedStringEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMMITTED = "committed"
    FAILED_PERMANENT = "failed_permanent"
    NEEDS_ATTENTION = "needs_attention"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class AttemptState(ClosedStringEnum):
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    LATE_REJECTED = "late_rejected"
    ABANDONED = "abandoned"


class StageType(ClosedStringEnum):
    INVENTORY = "inventory"
    MAP = "map"
    REDUCE = "reduce"
    VALIDATE = "validate"
    PUBLISH = "publish"


class RunnerKind(ClosedStringEnum):
    INTERNAL = "internal"
    EXECUTOR = "executor"
    WORKLOAD = "workload"


class DurableEffect(ClosedStringEnum):
    PURE = "pure"
    IDEMPOTENT = "idempotent"
    RECONCILABLE = "reconcilable"
    MANUAL_ONLY = "manual_only"


class SourceState(ClosedStringEnum):
    READY = "ready"
    UNSTABLE = "unstable"
    MISSING = "missing"
    SKIPPED = "skipped"


class ArtifactState(ClosedStringEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    PUBLISHED = "published"
    NEEDS_ATTENTION = "needs_attention"
    EXPIRED = "expired"


class PublicationState(ClosedStringEnum):
    PREPARED = "prepared"
    PUBLISHED = "published"
    NEEDS_ATTENTION = "needs_attention"
    CANCELLED = "cancelled"


class OutboxState(ClosedStringEnum):
    PENDING = "pending"
    LEASED = "leased"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(ClosedStringEnum):
    DRAFT_CREATED = "draft_created"
    REVISION_ADMITTED = "revision_admitted"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUMED = "resumed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"
    ATTENTION_RESOLVED = "attention_resolved"
    FAILED = "failed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    COMPLETED = "completed"


# Canonical resource order shared by plan validation, worker capabilities,
# leases and execution context. Tuple identity is part of the closed contract.
RESOURCE_KEYS = (
    "cpu", "device", "llm", "local_io", "network_io", "vlm",
)


WORKLOAD_TRANSITIONS: Mapping[WorkloadState, frozenset[WorkloadState]] = {
    WorkloadState.DRAFT: frozenset({
        WorkloadState.ADMITTED,
        WorkloadState.CANCELLED,
        WorkloadState.FAILED,
    }),
    WorkloadState.ADMITTED: frozenset({
        WorkloadState.QUEUED,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
    }),
    WorkloadState.QUEUED: frozenset({
        WorkloadState.RUNNING,
        WorkloadState.PAUSED,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
    }),
    WorkloadState.RUNNING: frozenset({
        WorkloadState.PAUSE_REQUESTED,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
        WorkloadState.COMPLETED_WITH_ERRORS,
        WorkloadState.COMPLETED,
    }),
    WorkloadState.PAUSE_REQUESTED: frozenset({
        WorkloadState.PAUSED,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
    }),
    WorkloadState.PAUSED: frozenset({
        WorkloadState.QUEUED,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
    }),
    WorkloadState.CANCEL_REQUESTED: frozenset({
        WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
    }),
    WorkloadState.NEEDS_ATTENTION: frozenset({
        WorkloadState.QUEUED,
        WorkloadState.RUNNING,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED,
        WorkloadState.FAILED,
    }),
    WorkloadState.CANCELLED: frozenset(),
    WorkloadState.FAILED: frozenset(),
    WorkloadState.COMPLETED_WITH_ERRORS: frozenset(),
    WorkloadState.COMPLETED: frozenset(),
}


# ``None`` means an explicit idempotent no-op.  An absent state/command pair
# is illegal and must never infer a transition from the general state graph.
CONTROL_STATE_MATRIX: Mapping[
    str, Mapping[WorkloadState, WorkloadState | None]
] = {
    "pause": {
        WorkloadState.QUEUED: WorkloadState.PAUSED,
        WorkloadState.RUNNING: WorkloadState.PAUSE_REQUESTED,
        WorkloadState.PAUSE_REQUESTED: None,
        WorkloadState.PAUSED: None,
    },
    "resume": {
        WorkloadState.QUEUED: None,
        WorkloadState.PAUSED: WorkloadState.QUEUED,
    },
    "cancel": {
        WorkloadState.DRAFT: WorkloadState.CANCELLED,
        WorkloadState.ADMITTED: WorkloadState.CANCEL_REQUESTED,
        WorkloadState.QUEUED: WorkloadState.CANCEL_REQUESTED,
        WorkloadState.RUNNING: WorkloadState.CANCEL_REQUESTED,
        WorkloadState.PAUSE_REQUESTED: WorkloadState.CANCEL_REQUESTED,
        WorkloadState.PAUSED: WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCEL_REQUESTED: None,
        WorkloadState.CANCELLED: None,
        WorkloadState.NEEDS_ATTENTION: WorkloadState.CANCEL_REQUESTED,
    },
}


UNIT_TRANSITIONS: Mapping[UnitState, frozenset[UnitState]] = {
    UnitState.PENDING: frozenset({
        UnitState.LEASED,
        UnitState.CANCELLED,
        UnitState.SKIPPED,
    }),
    UnitState.LEASED: frozenset({
        UnitState.RUNNING,
        UnitState.PENDING,
        UnitState.NEEDS_ATTENTION,
        UnitState.CANCELLED,
    }),
    UnitState.RUNNING: frozenset({
        UnitState.COMMITTED,
        UnitState.RETRY_WAIT,
        UnitState.FAILED_PERMANENT,
        UnitState.NEEDS_ATTENTION,
        UnitState.CANCELLED,
    }),
    UnitState.RETRY_WAIT: frozenset({
        UnitState.PENDING,
        UnitState.CANCELLED,
    }),
    UnitState.NEEDS_ATTENTION: frozenset({
        UnitState.PENDING,
        UnitState.CANCELLED,
    }),
    UnitState.COMMITTED: frozenset(),
    UnitState.FAILED_PERMANENT: frozenset(),
    UnitState.CANCELLED: frozenset(),
    UnitState.SKIPPED: frozenset(),
}


TERMINAL_WORKLOAD_STATES = frozenset({
    WorkloadState.CANCELLED,
    WorkloadState.FAILED,
    WorkloadState.COMPLETED_WITH_ERRORS,
    WorkloadState.COMPLETED,
})

TERMINAL_UNIT_STATES = frozenset({
    UnitState.COMMITTED,
    UnitState.FAILED_PERMANENT,
    UnitState.CANCELLED,
    UnitState.SKIPPED,
})


def can_transition_workload(
    source: WorkloadState | str, destination: WorkloadState | str,
) -> bool:
    try:
        current = WorkloadState(source)
        target = WorkloadState(destination)
    except ValueError:
        return False
    return target in WORKLOAD_TRANSITIONS[current]


def control_transition(
    command: str,
    source: WorkloadState | str,
) -> WorkloadState | None:
    """Return one declared control outcome, or reject an illegal command."""

    try:
        current = WorkloadState(source)
    except ValueError as exc:
        raise ValueError("source workload state is invalid") from exc
    matrix = CONTROL_STATE_MATRIX.get(command)
    if matrix is None or current not in matrix:
        raise ValueError("control command is illegal for the workload state")
    return matrix[current]


def can_transition_unit(
    source: UnitState | str, destination: UnitState | str,
) -> bool:
    try:
        current = UnitState(source)
        target = UnitState(destination)
    except ValueError:
        return False
    return target in UNIT_TRANSITIONS[current]


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    owner_user_id: str
    workload_id: str
    revision_id: str
    stage_id: str
    unit_key: str
    attempt_id: str
    priority: str
    resource_claims: tuple[tuple[str, int], ...]
    deadline_at: str | None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class SourceResolution:
    """Authority-backed reopening of one source from a sealed inventory.

    The authority must populate the observed identity after a local rehash or
    after verifying an equivalent device attestation.  ``value`` is the
    ephemeral runner input and is never persisted by the LRE.
    """

    value: object
    source_id: str
    device_id: str
    content_digest: str
    size_bytes: int
    mtime_ns: int
    authority: str


@dataclass(frozen=True, slots=True)
class WorkloadRecord:
    owner_user_id: str
    workload_id: str
    request_key: str
    state: WorkloadState
    priority: str
    active_revision_id: str | None
    version: int
    created_at: str
    updated_at: str
    terminal_reason_json: str | None


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    owner_user_id: str
    revision_id: str
    workload_id: str
    number: int
    plan_digest: str
    inventory_digest: str | None
    inventory_sealed: bool
    expected_source_count: int
    admitted_at: str | None


@dataclass(frozen=True, slots=True)
class EventRecord:
    owner_user_id: str
    workload_id: str
    event_id: int
    event_type: EventType
    payload_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One durable, channel-neutral delivery request.

    ``recipient_key`` identifies the logical recipient, never a provider
    address.  An adapter resolves any provider association only while it owns
    the delivery lease.
    """

    owner_user_id: str
    outbox_id: str
    workload_id: str
    event_id: int
    channel: str
    recipient_key: str
    state: OutboxState
    attempt_count: int
    next_attempt_at: str | None
    lease_worker_id: str | None
    lease_expires_at: str | None
    fence: int
    coalesce_key: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class UnitCounters:
    discovered: int = 0
    committed: int = 0
    failed: int = 0
    skipped: int = 0
    attention: int = 0
    pending: int = 0

    @property
    def total(self) -> int:
        return (
            self.committed + self.failed + self.skipped
            + self.attention + self.pending
        )


@dataclass(frozen=True, slots=True)
class UnitReadRecord:
    """Redacted, owner-scoped unit projection for control-plane readers."""

    owner_user_id: str
    unit_id: str
    revision_id: str
    stage_key: str
    state: UnitState
    attempt_count: int
    next_attempt_at: str | None
    error_class: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    eligible: bool
    target_state: WorkloadState | None
    reasons: tuple[str, ...]
    counters: UnitCounters
    event_id: int | None = None
    workload_version: int | None = None
