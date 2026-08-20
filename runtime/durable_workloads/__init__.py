"""Versioned primitives for durable workloads with an explicit lifecycle.

F0-F8 preserve this package's zero-I/O import boundary. The supervised
lifecycle is an explicit ``durable_workloads.service`` entry point: importing
the package does not open a database, start a worker, register a route or
alter scheduling.
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
