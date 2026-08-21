"""Versioned primitives for durable workloads with an explicit lifecycle.

F0-F9 preserve this package's zero-I/O import boundary. The supervised
lifecycle is an explicit ``durable_workloads.service`` entry point: importing
the package does not open a database, start a worker, register a route or
alter scheduling.
"""

from .migrations import CURRENT_SCHEMA_VERSION, migrate, open_db, schema_version
from .models import (
    CompletionAssessment,
    CONTROL_STATE_MATRIX,
    DurableEffect,
    EventRecord,
    EventType,
    ExecutionContext,
    OutboxRecord,
    RevisionRecord,
    SourceResolution,
    SourceState,
    StageType,
    UnitCounters,
    UnitReadRecord,
    UnitState,
    WorkloadRecord,
    WorkloadState,
    control_transition,
)
from .storage import DurableWorkloadStore

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "CompletionAssessment",
    "CONTROL_STATE_MATRIX",
    "DurableEffect",
    "DurableWorkloadStore",
    "EventRecord",
    "EventType",
    "ExecutionContext",
    "OutboxRecord",
    "RevisionRecord",
    "SourceResolution",
    "SourceState",
    "StageType",
    "UnitCounters",
    "UnitReadRecord",
    "UnitState",
    "WorkloadRecord",
    "WorkloadState",
    "control_transition",
    "migrate",
    "open_db",
    "schema_version",
]
