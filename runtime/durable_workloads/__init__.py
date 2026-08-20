"""Dormant persistence kernel for versioned, durable workloads.

F0-F2 deliberately expose storage primitives only.  Importing this package
does not open a database, start a worker, register a route or alter scheduling.
"""

from .migrations import CURRENT_SCHEMA_VERSION, migrate, open_db, schema_version
from .models import (
    CompletionAssessment,
    DurableEffect,
    EventRecord,
    EventType,
    ExecutionContext,
    RevisionRecord,
    SourceState,
    StageType,
    UnitCounters,
    UnitState,
    WorkloadRecord,
    WorkloadState,
)
from .storage import DurableWorkloadStore

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "CompletionAssessment",
    "DurableEffect",
    "DurableWorkloadStore",
    "EventRecord",
    "EventType",
    "ExecutionContext",
    "RevisionRecord",
    "SourceState",
    "StageType",
    "UnitCounters",
    "UnitState",
    "WorkloadRecord",
    "WorkloadState",
    "migrate",
    "open_db",
    "schema_version",
]
