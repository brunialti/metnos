"""Owner-scoped transactional repository and explicit state machines.

This module persists states but never executes a unit, calls an LLM, touches a
provider or publishes a blob.  F3 lease/fence APIs accept only frozen dummy
execution contracts; the callable remains outside every database transaction.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .coordinator import (
    RESOURCE_KEYS,
    CommitOutcome,
    CommitStatus,
    FailureOutcome,
    FailureStatus,
    Lease,
    LeaseMutationStatus,
    ReconcileOutcome,
    RetryDecision,
    RetryPolicy,
    StructuredAttemptError,
    ValidatedResult,
    WorkerCapabilities,
    decide_retry,
    deterministic_retry_delay_ms,
    instant_text,
    normalize_instant,
    parse_instant,
    require_lease_duration,
    require_worker_id,
)
from .migrations import (
    CURRENT_SCHEMA_VERSION,
    migrate,
    open_db,
    schema_version,
    utc_now,
)
from .models import (
    CompletionAssessment,
    DurableEffect,
    EventRecord,
    EventType,
    OutboxRecord,
    OutboxState,
    RevisionRecord,
    RunnerKind,
    TERMINAL_UNIT_STATES,
    UnitCounters,
    UnitReadRecord,
    UnitState,
    WorkloadRecord,
    WorkloadState,
    can_transition_workload,
    control_transition,
)
from .schema import (
    MAX_EVENT_JSON_BYTES,
    MAX_PLAN_JSON_BYTES,
    MAX_SNAPSHOT_JSON_BYTES,
    SchemaValidationError,
    canonical_json,
    digest_json,
    plan_digest,
    validate_event_payload,
    validate_inventory,
    validate_plan,
)


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_MAX_ATTEMPT_METRICS_JSON_BYTES = 1_048_576
_EVENT_BY_TARGET = {
    WorkloadState.ADMITTED: EventType.REVISION_ADMITTED,
    WorkloadState.QUEUED: EventType.QUEUED,
    WorkloadState.RUNNING: EventType.RUNNING,
    WorkloadState.PAUSE_REQUESTED: EventType.PAUSE_REQUESTED,
    WorkloadState.PAUSED: EventType.PAUSED,
    WorkloadState.CANCEL_REQUESTED: EventType.CANCEL_REQUESTED,
    WorkloadState.CANCELLED: EventType.CANCELLED,
    WorkloadState.NEEDS_ATTENTION: EventType.NEEDS_ATTENTION,
    WorkloadState.FAILED: EventType.FAILED,
}
_OUTBOX_CHANNELS = frozenset({"owner_event", "telegram"})
_MAX_OUTBOX_LEASE_SECONDS = 300


class DurableStoreError(RuntimeError):
    """Base class for repository failures with stable caller semantics."""


class StoreNotReadyError(DurableStoreError):
    """The supplied connection does not have the exact supported schema."""


class OwnerRequiredError(DurableStoreError, ValueError):
    """An owner-scoped operation omitted or malformed the authenticated ID."""


class WorkloadNotFoundError(DurableStoreError, LookupError):
    """No workload exists for the owner/id pair."""


class RevisionNotFoundError(DurableStoreError, LookupError):
    """No revision exists for the owner/id pair."""


class VersionConflictError(DurableStoreError):
    """The optimistic precondition did not match the current version."""


class IdempotencyConflictError(DurableStoreError):
    """A reused idempotency key carries a different canonical payload."""


class IdentifierConflictError(DurableStoreError):
    """A caller-supplied identifier already names another owner object."""


class InvalidTransitionError(DurableStoreError):
    """The requested source/destination pair is outside the closed graph."""


class ReservedCompletionTransitionError(InvalidTransitionError):
    """Only evaluate_completion may produce a completed state."""


class ResultContractError(DurableStoreError):
    """A prevalidated result does not match the frozen stage output contract."""


class RetryDecisionConflictError(DurableStoreError):
    """A caller tried to override the retry decision derived from the plan."""


def _require_owner(owner_user_id: str) -> str:
    if (
        not isinstance(owner_user_id, str)
        or not owner_user_id
        or len(owner_user_id) > 160
        or owner_user_id != owner_user_id.strip()
    ):
        raise OwnerRequiredError(
            "owner_user_id must be a canonical string of 1..160 characters"
        )
    return owner_user_id


def _require_key(value: str, *, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= maximum):
        raise ValueError(f"{name} must be a string of 1..{maximum} characters")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


def _require_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected_version must be a positive integer")
    return value


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _chosen_id(value: str | None, *, prefix: str) -> str:
    identifier = _new_id(prefix) if value is None else value
    if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
        raise ValueError(f"{prefix} identifier must match {_ID_RE.pattern}")
    return identifier


def _row_to_workload(row: sqlite3.Row) -> WorkloadRecord:
    return WorkloadRecord(
        owner_user_id=str(row["owner_user_id"]),
        workload_id=str(row["id"]),
        request_key=str(row["request_key"]),
        state=WorkloadState(row["state"]),
        priority=str(row["priority"]),
        active_revision_id=row["active_revision_id"],
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        terminal_reason_json=row["terminal_reason_json"],
    )


def _row_to_revision(row: sqlite3.Row) -> RevisionRecord:
    return RevisionRecord(
        owner_user_id=str(row["owner_user_id"]),
        revision_id=str(row["id"]),
        workload_id=str(row["workload_id"]),
        number=int(row["number"]),
        plan_digest=str(row["plan_digest"]),
        inventory_digest=row["inventory_digest"],
        inventory_sealed=bool(row["inventory_sealed"]),
        expected_source_count=int(row["expected_source_count"]),
        admitted_at=row["admitted_at"],
    )


def _row_to_outbox(row: sqlite3.Row) -> OutboxRecord:
    return OutboxRecord(
        owner_user_id=str(row["owner_user_id"]),
        outbox_id=str(row["id"]),
        workload_id=str(row["workload_id"]),
        event_id=int(row["event_id"]),
        channel=str(row["channel"]),
        recipient_key=str(row["recipient_key"]),
        state=OutboxState(row["state"]),
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=row["next_attempt_at"],
        lease_worker_id=row["lease_worker_id"],
        lease_expires_at=row["lease_expires_at"],
        fence=int(row["fence"]),
        coalesce_key=row["coalesce_key"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _workload_snapshot(record: WorkloadRecord) -> dict[str, Any]:
    return {
        "schema_version": "metnos.durable-command-result/1",
        "owner_user_id": record.owner_user_id,
        "workload_id": record.workload_id,
        "request_key": record.request_key,
        "state": record.state.value,
        "priority": record.priority,
        "active_revision_id": record.active_revision_id,
        "version": record.version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "terminal_reason_json": record.terminal_reason_json,
    }


def _snapshot_to_workload(raw: str) -> WorkloadRecord:
    value = json.loads(raw)
    if value.get("schema_version") != "metnos.durable-command-result/1":
        raise DurableStoreError("stored command result has an incompatible schema")
    return WorkloadRecord(
        owner_user_id=value["owner_user_id"],
        workload_id=value["workload_id"],
        request_key=value["request_key"],
        state=WorkloadState(value["state"]),
        priority=value["priority"],
        active_revision_id=value.get("active_revision_id"),
        version=int(value["version"]),
        created_at=value["created_at"],
        updated_at=value["updated_at"],
        terminal_reason_json=value.get("terminal_reason_json"),
    )


class DurableWorkloadStore:
    """One explicitly-owned repository connection; never a process singleton."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if schema_version(connection) != CURRENT_SCHEMA_VERSION:
            raise StoreNotReadyError(
                "durable workload schema is not at the supported version"
            )
        connection.row_factory = sqlite3.Row
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StoreNotReadyError("foreign_keys must be active on every connection")
        self._connection = connection

    @classmethod
    def open(cls, path: str | Path | None = None) -> "DurableWorkloadStore":
        connection = open_db(path)
        try:
            migrate(connection)
            return cls(connection)
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DurableWorkloadStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._connection.in_transaction:
            raise DurableStoreError("nested durable-workload transactions are forbidden")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _select_workload(
        connection: sqlite3.Connection,
        owner_user_id: str,
        workload_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT owner_user_id, id, request_key, state, priority,
                   active_revision_id, version, created_at, updated_at,
                   terminal_reason_json
            FROM workloads
            WHERE owner_user_id=? AND id=?
            """,
            (owner_user_id, workload_id),
        ).fetchone()
        if row is None:
            raise WorkloadNotFoundError("workload not found")
        return row

    def create_draft(
        self,
        owner_user_id: str,
        request_key: str,
        *,
        redacted_request: Mapping[str, Any],
        priority: str = "normal",
        budget: Mapping[str, Any] | None = None,
        workload_id: str | None = None,
    ) -> WorkloadRecord:
        owner = _require_owner(owner_user_id)
        key = _require_key(request_key, name="request_key")
        if priority not in {"low", "normal", "high"}:
            raise ValueError("priority must be low, normal or high")
        if not isinstance(redacted_request, Mapping):
            raise SchemaValidationError("redacted_request must be an object")
        budget_value: Mapping[str, Any] = {} if budget is None else budget
        if not isinstance(budget_value, Mapping):
            raise SchemaValidationError("budget must be an object")
        request_json = canonical_json(
            {
                "schema_version": "metnos.redacted-request/1",
                "payload": redacted_request,
            },
            max_bytes=MAX_PLAN_JSON_BYTES,
        )
        budget_json = canonical_json(
            {
                "schema_version": "metnos.durable-draft-budget/1",
                "limits": budget_value,
            },
            max_bytes=MAX_PLAN_JSON_BYTES,
        )
        request_digest = digest_json(
            "durable-submit",
            {
                "redacted_request": redacted_request,
                "priority": priority,
                "budget": budget_value,
            },
            max_bytes=MAX_PLAN_JSON_BYTES,
        )
        chosen = _chosen_id(workload_id, prefix="wrk")
        now = utc_now()

        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT owner_user_id, id, request_key, state, priority,
                       active_revision_id, version, created_at, updated_at,
                       terminal_reason_json, request_digest
                FROM workloads
                WHERE owner_user_id=? AND request_key=?
                """,
                (owner, key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise IdempotencyConflictError(
                        "request_key was already used with a different payload"
                    )
                return _row_to_workload(existing)
            collision = connection.execute(
                "SELECT 1 FROM workloads WHERE owner_user_id=? AND id=?",
                (owner, chosen),
            ).fetchone()
            if collision is not None:
                raise IdentifierConflictError("workload_id already exists for owner")
            connection.execute(
                """
                INSERT INTO workloads(
                    owner_user_id, id, request_key, request_digest,
                    redacted_request_json, state, priority, budget_json,
                    version, next_event_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, 1, 1, ?, ?)
                """,
                (
                    owner, chosen, key, request_digest, request_json,
                    priority, budget_json, now, now,
                ),
            )
            self.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=chosen,
                event_type=EventType.DRAFT_CREATED,
                payload={"version": 1},
            )
            return _row_to_workload(
                self._select_workload(connection, owner, chosen)
            )

    def get_workload(
        self, owner_user_id: str, workload_id: str,
    ) -> WorkloadRecord:
        owner = _require_owner(owner_user_id)
        return _row_to_workload(
            self._select_workload(self._connection, owner, workload_id)
        )

    def list_workloads(
        self,
        owner_user_id: str,
        *,
        state: WorkloadState | str | None = None,
        limit: int = 100,
    ) -> tuple[WorkloadRecord, ...]:
        owner = _require_owner(owner_user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer in 1..200")
        parameters: list[Any] = [owner]
        where = "owner_user_id=?"
        if state is not None:
            normalized = WorkloadState(state)
            where += " AND state=?"
            parameters.append(normalized.value)
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT owner_user_id, id, request_key, state, priority,
                   active_revision_id, version, created_at, updated_at,
                   terminal_reason_json
            FROM workloads
            WHERE {where}
            ORDER BY updated_at DESC, id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(_row_to_workload(row) for row in rows)

    def list_workloads_page(
        self,
        owner_user_id: str,
        *,
        state: WorkloadState | str | None = None,
        before: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> tuple[WorkloadRecord, ...]:
        """List one owner-scoped page ordered by update time and identifier."""

        owner = _require_owner(owner_user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer in 1..200")
        normalized_state = WorkloadState(state) if state is not None else None
        parameters: list[Any] = [owner]
        clauses = ["owner_user_id=?"]
        if normalized_state is not None:
            clauses.append("state=?")
            parameters.append(normalized_state.value)
        if before is not None:
            if (
                not isinstance(before, tuple)
                or len(before) != 2
                or not all(isinstance(value, str) and value for value in before)
            ):
                raise ValueError("before must contain an update time and workload ID")
            updated_at, workload_id = before
            clauses.append("(updated_at<? OR (updated_at=? AND id<?))")
            parameters.extend((updated_at, updated_at, workload_id))
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT owner_user_id, id, request_key, state, priority,
                   active_revision_id, version, created_at, updated_at,
                   terminal_reason_json
            FROM workloads
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(_row_to_workload(row) for row in rows)

    def get_revision(
        self, owner_user_id: str, revision_id: str,
    ) -> RevisionRecord:
        owner = _require_owner(owner_user_id)
        row = self._connection.execute(
            """
            SELECT owner_user_id, id, workload_id, number, plan_digest,
                   inventory_digest, inventory_sealed, expected_source_count,
                   admitted_at
            FROM revisions
            WHERE owner_user_id=? AND id=?
            """,
            (owner, revision_id),
        ).fetchone()
        if row is None:
            raise RevisionNotFoundError("revision not found")
        return _row_to_revision(row)

    def admit_revision(
        self,
        owner_user_id: str,
        workload_id: str,
        plan: Mapping[str, Any],
        inventory: Mapping[str, Any],
        *,
        expected_version: int,
        catalog_snapshot: Mapping[str, Any] | None = None,
        policy_snapshot: Mapping[str, Any] | None = None,
        caps_truncated: bool = False,
        partial_output_accepted: bool = False,
        usage_complete: bool = False,
        revision_id: str | None = None,
    ) -> RevisionRecord:
        owner = _require_owner(owner_user_id)
        expected = _require_version(expected_version)
        if not all(
            isinstance(value, bool)
            for value in (caps_truncated, partial_output_accepted, usage_complete)
        ):
            raise ValueError("revision completion flags must be boolean")
        plan_json = validate_plan(plan)
        digest = plan_digest(plan)
        inventory_json, sources = validate_inventory(inventory)
        inventory_hash = str(inventory["digest"])
        inventory_contract = plan["inventory"]
        if len(sources) > int(inventory_contract["max_sources"]):
            raise SchemaValidationError(
                "sealed inventory exceeds plan.inventory.max_sources"
            )
        if sum(int(item["size_bytes"]) for item in sources) > int(
            inventory_contract["max_total_bytes"]
        ):
            raise SchemaValidationError(
                "sealed inventory exceeds plan.inventory.max_total_bytes"
            )
        undersized_stages = sorted(
            str(stage["key"])
            for stage in plan["stages"]
            if stage["required"]
            and stage["cardinality"]["mode"] == "per_source"
            and int(stage["cardinality"]["max_units"]) < len(sources)
        )
        if undersized_stages:
            raise SchemaValidationError(
                "sealed inventory exceeds required per-source stage caps: "
                f"{undersized_stages}"
            )
        catalog = (
            {"schema_version": "metnos.catalog-snapshot/1", "entries": []}
            if catalog_snapshot is None else catalog_snapshot
        )
        policy = (
            {"schema_version": "metnos.policy-snapshot/1", "rules": []}
            if policy_snapshot is None else policy_snapshot
        )
        if not isinstance(catalog, Mapping) or not isinstance(policy, Mapping):
            raise SchemaValidationError("catalog and policy snapshots must be objects")
        if catalog.get("schema_version") != "metnos.catalog-snapshot/1":
            raise SchemaValidationError("catalog snapshot has an incompatible schema_version")
        if policy.get("schema_version") != "metnos.policy-snapshot/1":
            raise SchemaValidationError("policy snapshot has an incompatible schema_version")
        catalog_json = canonical_json(catalog, max_bytes=MAX_SNAPSHOT_JSON_BYTES)
        policy_json = canonical_json(policy, max_bytes=MAX_SNAPSHOT_JSON_BYTES)
        chosen_revision = _chosen_id(revision_id, prefix="rev")
        error_policy = plan["error_policy"]
        tolerated_json = canonical_json(
            error_policy["allowed_error_classes"], max_bytes=MAX_EVENT_JSON_BYTES
        )
        artifacts_json = canonical_json(
            plan["required_artifacts"], max_bytes=MAX_EVENT_JSON_BYTES
        )
        now = utc_now()

        with self._transaction() as connection:
            workload_row = self._select_workload(connection, owner, workload_id)
            duplicate = connection.execute(
                """
                SELECT owner_user_id, id, workload_id, number, plan_digest,
                       inventory_digest, inventory_sealed,
                       expected_source_count, admitted_at,
                       catalog_snapshot_json, policy_snapshot_json,
                       caps_truncated, partial_output_accepted,
                       usage_complete
                FROM revisions
                WHERE owner_user_id=? AND workload_id=?
                  AND plan_digest=? AND inventory_digest=?
                """,
                (owner, workload_id, digest, inventory_hash),
            ).fetchone()
            if duplicate is not None:
                duplicate_inputs = (
                    duplicate["catalog_snapshot_json"],
                    duplicate["policy_snapshot_json"],
                    bool(duplicate["caps_truncated"]),
                    bool(duplicate["partial_output_accepted"]),
                    bool(duplicate["usage_complete"]),
                )
                requested_inputs = (
                    catalog_json,
                    policy_json,
                    caps_truncated,
                    partial_output_accepted,
                    usage_complete,
                )
                if duplicate_inputs != requested_inputs:
                    raise IdempotencyConflictError(
                        "plan and inventory were already admitted with "
                        "different snapshots or completion facts"
                    )
                return _row_to_revision(duplicate)
            if int(workload_row["version"]) != expected:
                raise VersionConflictError("workload version precondition failed")
            if WorkloadState(workload_row["state"]) is not WorkloadState.DRAFT:
                raise InvalidTransitionError("only a draft can admit its first revision")
            collision = connection.execute(
                "SELECT 1 FROM revisions WHERE owner_user_id=? AND id=?",
                (owner, chosen_revision),
            ).fetchone()
            if collision is not None:
                raise IdentifierConflictError("revision_id already exists for owner")
            number = int(connection.execute(
                """
                SELECT COALESCE(MAX(number), 0) + 1
                FROM revisions WHERE owner_user_id=? AND workload_id=?
                """,
                (owner, workload_id),
            ).fetchone()[0])
            connection.execute(
                """
                INSERT INTO revisions(
                    owner_user_id, id, workload_id, number,
                    plan_schema_version, plan_json, plan_digest,
                    catalog_snapshot_json, policy_snapshot_json,
                    inventory_json, inventory_digest, inventory_sealed,
                    expected_source_count, caps_truncated,
                    partial_output_accepted, usage_complete, failure_policy,
                    tolerated_error_classes_json, required_artifacts_json,
                    created_at, admitted_at
                ) VALUES (
                    ?, ?, ?, ?, 'metnos.durable-plan/1', ?, ?, ?, ?, ?, ?, 1,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    owner, chosen_revision, workload_id, number,
                    plan_json, digest, catalog_json, policy_json,
                    inventory_json, inventory_hash, len(sources),
                    int(caps_truncated), int(partial_output_accepted),
                    int(usage_complete), error_policy["mode"], tolerated_json,
                    artifacts_json, now, now,
                ),
            )

            stage_ids: dict[str, str] = {}
            for position, stage in enumerate(plan["stages"]):
                stage_id = _new_id("stg")
                stage_ids[str(stage["key"])] = stage_id
                connection.execute(
                    """
                    INSERT INTO stages(
                        owner_user_id, id, revision_id, stage_key, position,
                        stage_type, runner_kind, runner_name, effect_profile,
                        cardinality, max_units, input_bindings_json,
                        output_schema_json, retry_json, timeout_s,
                        invalidation_json, resources_json, required_flag,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner, stage_id, chosen_revision, stage["key"], position,
                        stage["type"], stage["runner"]["kind"],
                        stage["runner"]["name"], stage["effect_profile"],
                        stage["cardinality"]["mode"],
                        stage["cardinality"]["max_units"],
                        canonical_json(stage["input_bindings"], max_bytes=MAX_PLAN_JSON_BYTES),
                        canonical_json(stage["output_schema"], max_bytes=MAX_EVENT_JSON_BYTES),
                        canonical_json(stage["retry"], max_bytes=MAX_EVENT_JSON_BYTES),
                        stage["timeout_s"],
                        canonical_json(stage["invalidation_keys"], max_bytes=MAX_EVENT_JSON_BYTES),
                        canonical_json(stage["resources"], max_bytes=MAX_EVENT_JSON_BYTES),
                        int(stage["required"]), now,
                    ),
                )
            for stage in plan["stages"]:
                for ordinal, dependency in enumerate(stage["depends_on"]):
                    connection.execute(
                        """
                        INSERT INTO stage_dependencies(
                            owner_user_id, revision_id, stage_id,
                            depends_on_stage_id, ordinal
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            owner, chosen_revision, stage_ids[stage["key"]],
                            stage_ids[dependency], ordinal,
                        ),
                    )

            source_rows: list[tuple[Mapping[str, Any], str]] = []
            for source in sources:
                source_row_id = _new_id("src")
                source_rows.append((source, source_row_id))
                connection.execute(
                    """
                    INSERT INTO sources(
                        owner_user_id, id, revision_id, source_id, ordinal,
                        device_id, locator_redacted, kind, size_bytes, mtime_ns,
                        content_digest, state, accounted, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner, source_row_id, chosen_revision,
                        source["source_id"], source["ordinal"],
                        source["device_id"], source["locator_redacted"],
                        source["kind"], source["size_bytes"], source["mtime_ns"],
                        source["content_digest"], source["state"],
                        int(source["accounted"]), now, now,
                    ),
                )

            inventory_stage_key = next(
                str(stage["key"])
                for stage in plan["stages"]
                if stage["type"] == "inventory"
            )
            for stage in plan["stages"]:
                dependencies = set(stage["depends_on"])
                if (
                    stage["type"] == "inventory"
                    or dependencies - {inventory_stage_key}
                ):
                    continue
                cardinality = stage["cardinality"]["mode"]
                candidates: Sequence[tuple[Mapping[str, Any] | None, str | None]]
                if cardinality == "per_source":
                    candidates = tuple(source_rows)
                elif cardinality == "singleton":
                    candidates = ((None, None),)
                else:
                    candidates = ()
                for source, source_row_id in candidates:
                    semantic = {
                        "stage": stage,
                        "source_digest": (
                            source["content_digest"] if source is not None else None
                        ),
                        "source_id": source["source_id"] if source is not None else None,
                    }
                    unit_key = digest_json(
                        "durable-unit-key", semantic, max_bytes=MAX_PLAN_JSON_BYTES
                    )
                    connection.execute(
                        """
                        INSERT INTO units(
                            owner_user_id, id, revision_id, stage_id, unit_key,
                            source_row_id, state, expected_dependency_count,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                        """,
                        (
                            owner, _new_id("unt"), chosen_revision,
                            stage_ids[stage["key"]], unit_key, source_row_id,
                            now, now,
                        ),
                    )

            updated = connection.execute(
                """
                UPDATE workloads
                SET active_revision_id=?, state='admitted', version=version+1,
                    updated_at=?
                WHERE owner_user_id=? AND id=? AND version=? AND state='draft'
                """,
                (chosen_revision, now, owner, workload_id, expected),
            )
            if updated.rowcount != 1:
                raise VersionConflictError("workload changed during admission")
            event = self.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_type=EventType.REVISION_ADMITTED,
                payload={
                    "revision_id": chosen_revision,
                    "revision_number": number,
                    "plan_digest": digest,
                    "inventory_digest": inventory_hash,
                    "source_count": len(sources),
                    "new_version": expected + 1,
                },
            )
            self._enqueue_notification_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_id=event.event_id,
            )
            row = connection.execute(
                """
                SELECT owner_user_id, id, workload_id, number, plan_digest,
                       inventory_digest, inventory_sealed,
                       expected_source_count, admitted_at
                FROM revisions WHERE owner_user_id=? AND id=?
                """,
                (owner, chosen_revision),
            ).fetchone()
            assert row is not None
            return _row_to_revision(row)

    def append_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        workload_id: str,
        event_type: EventType | str,
        payload: Mapping[str, Any],
    ) -> EventRecord:
        owner = _require_owner(owner_user_id)
        if connection is not self._connection or not connection.in_transaction:
            raise DurableStoreError(
                "append_event_in_transaction requires this store's active transaction"
            )
        normalized_type = EventType(event_type)
        payload_json = validate_event_payload(payload)
        row = connection.execute(
            """
            SELECT next_event_id FROM workloads
            WHERE owner_user_id=? AND id=?
            """,
            (owner, workload_id),
        ).fetchone()
        if row is None:
            raise WorkloadNotFoundError("workload not found")
        event_id = int(row["next_event_id"])
        updated = connection.execute(
            """
            UPDATE workloads SET next_event_id=next_event_id+1
            WHERE owner_user_id=? AND id=? AND next_event_id=?
            """,
            (owner, workload_id, event_id),
        )
        if updated.rowcount != 1:
            raise DurableStoreError("event sequence compare-and-set failed")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO events(
                owner_user_id, workload_id, event_id, type,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (owner, workload_id, event_id, normalized_type.value, payload_json, now),
        )
        return EventRecord(
            owner_user_id=owner,
            workload_id=workload_id,
            event_id=event_id,
            event_type=normalized_type,
            payload_json=payload_json,
            created_at=now,
        )

    def list_events(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> tuple[EventRecord, ...]:
        owner = _require_owner(owner_user_id)
        if (
            isinstance(after_event_id, bool)
            or not isinstance(after_event_id, int)
            or after_event_id < 0
        ):
            raise ValueError("after_event_id must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in 1..500")
        self._select_workload(self._connection, owner, workload_id)
        rows = self._connection.execute(
            """
            SELECT owner_user_id, workload_id, event_id, type,
                   payload_json, created_at
            FROM events
            WHERE owner_user_id=? AND workload_id=? AND event_id>?
            ORDER BY event_id
            LIMIT ?
            """,
            (owner, workload_id, after_event_id, limit),
        ).fetchall()
        return tuple(
            EventRecord(
                owner_user_id=row["owner_user_id"],
                workload_id=row["workload_id"],
                event_id=int(row["event_id"]),
                event_type=EventType(row["type"]),
                payload_json=row["payload_json"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def list_recent_events(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        limit: int = 200,
    ) -> tuple[EventRecord, ...]:
        """Return the newest bounded event window in chronological order."""

        owner = _require_owner(owner_user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in 1..500")
        self._select_workload(self._connection, owner, workload_id)
        rows = self._connection.execute(
            """
            SELECT owner_user_id, workload_id, event_id, type,
                   payload_json, created_at
            FROM events
            WHERE owner_user_id=? AND workload_id=?
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (owner, workload_id, limit),
        ).fetchall()
        return tuple(
            EventRecord(
                owner_user_id=row["owner_user_id"],
                workload_id=row["workload_id"],
                event_id=int(row["event_id"]),
                event_type=EventType(row["type"]),
                payload_json=row["payload_json"],
                created_at=row["created_at"],
            )
            for row in reversed(rows)
        )

    def get_event(
        self,
        owner_user_id: str,
        workload_id: str,
        event_id: int,
    ) -> EventRecord:
        """Read one owner-scoped persisted event for an internal dispatcher."""

        owner = _require_owner(owner_user_id)
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
            raise ValueError("event_id must be a positive integer")
        row = self._connection.execute(
            """
            SELECT owner_user_id, workload_id, event_id, type, payload_json, created_at
            FROM events
            WHERE owner_user_id=? AND workload_id=? AND event_id=?
            """,
            (owner, workload_id, event_id),
        ).fetchone()
        if row is None:
            raise WorkloadNotFoundError("event not found")
        return EventRecord(
            owner_user_id=str(row["owner_user_id"]),
            workload_id=str(row["workload_id"]),
            event_id=int(row["event_id"]),
            event_type=EventType(row["type"]),
            payload_json=str(row["payload_json"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _outbox_row(
        connection: sqlite3.Connection,
        owner_user_id: str,
        outbox_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT owner_user_id, id, workload_id, event_id, channel,
                   recipient_key, state, attempt_count, next_attempt_at,
                   lease_worker_id, lease_expires_at, fence, coalesce_key,
                   created_at, updated_at
            FROM outbox WHERE owner_user_id=? AND id=?
            """,
            (owner_user_id, outbox_id),
        ).fetchone()
        if row is None:
            raise WorkloadNotFoundError("outbox record not found")
        return row

    @staticmethod
    def _outbox_channel(value: str) -> str:
        if value not in _OUTBOX_CHANNELS:
            raise ValueError("outbox channel is not supported")
        return value

    def enqueue_outbox_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        workload_id: str,
        event_id: int,
        channel: str,
        recipient_key: str,
        coalesce_key: str | None = None,
        next_attempt_at: datetime | None = None,
        delivered: bool = False,
    ) -> OutboxRecord:
        """Insert one delivery row beside its event, or replace pending progress.

        This low-level primitive deliberately requires the caller's active
        workload transaction.  Therefore state, event and notification either
        commit together or none of them does.  Coalescing can only replace an
        unleased pending row, never a message a worker is already sending.
        """

        owner = _require_owner(owner_user_id)
        if connection is not self._connection or not connection.in_transaction:
            raise DurableStoreError(
                "enqueue_outbox_in_transaction requires this store's active transaction"
            )
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
            raise ValueError("event_id must be a positive integer")
        selected_channel = self._outbox_channel(channel)
        recipient = _require_key(recipient_key, name="recipient_key")
        if coalesce_key is not None:
            coalesce_key = _require_key(
                coalesce_key, name="coalesce_key", maximum=128,
            )
        if not isinstance(delivered, bool):
            raise TypeError("delivered must be a boolean")
        due = (
            instant_text(next_attempt_at, name="next_attempt_at")
            if next_attempt_at is not None else None
        )
        # The foreign key is authoritative, but this explicit owner-scoped
        # check yields a stable repository failure before an opaque SQL error.
        event = connection.execute(
            """
            SELECT 1 FROM events
            WHERE owner_user_id=? AND workload_id=? AND event_id=?
            """,
            (owner, workload_id, event_id),
        ).fetchone()
        if event is None:
            raise WorkloadNotFoundError("event not found for outbox delivery")
        now = utc_now()
        if coalesce_key is not None and not delivered:
            existing = connection.execute(
                """
                SELECT id FROM outbox
                WHERE owner_user_id=? AND workload_id=? AND channel=?
                  AND recipient_key=? AND coalesce_key=? AND state='pending'
                """,
                (owner, workload_id, selected_channel, recipient, coalesce_key),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE outbox
                    SET event_id=?, next_attempt_at=?, updated_at=?
                    WHERE owner_user_id=? AND id=? AND state='pending'
                    """,
                    (event_id, due, now, owner, existing["id"]),
                )
                return _row_to_outbox(self._outbox_row(
                    connection, owner, str(existing["id"]),
                ))
        outbox_id = _new_id("obx")
        state = OutboxState.SENT.value if delivered else OutboxState.PENDING.value
        ack_json = (
            canonical_json({"delivery": "recorded"}, max_bytes=MAX_EVENT_JSON_BYTES)
            if delivered else None
        )
        connection.execute(
            """
            INSERT INTO outbox(
                owner_user_id, id, workload_id, event_id, channel,
                recipient_key, state, next_attempt_at, ack_json, coalesce_key,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner, outbox_id, workload_id, event_id, selected_channel,
                recipient, state, due, ack_json, coalesce_key, now, now,
            ),
        )
        return _row_to_outbox(self._outbox_row(connection, owner, outbox_id))

    def enqueue_progress_outbox_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        workload_id: str,
        event_id: int,
        channel: str,
        recipient_key: str,
        minimum_interval_s: int,
        now: datetime | None = None,
    ) -> OutboxRecord:
        """Coalesce progress and delay it until the configured send interval."""

        if (
            isinstance(minimum_interval_s, bool)
            or not isinstance(minimum_interval_s, int)
            or not 0 <= minimum_interval_s <= 86_400
        ):
            raise ValueError("minimum_interval_s must be an integer in 0..86400")
        owner = _require_owner(owner_user_id)
        current = normalize_instant(
            now or datetime.now(timezone.utc), name="now"
        )
        previous = connection.execute(
            """
            SELECT updated_at FROM outbox
            WHERE owner_user_id=? AND workload_id=? AND channel=?
              AND recipient_key=? AND coalesce_key='progress' AND state='sent'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (owner, workload_id, self._outbox_channel(channel), recipient_key),
        ).fetchone()
        due = current
        if previous is not None:
            due = max(
                due,
                parse_instant(str(previous["updated_at"]), name="outbox updated_at")
                + timedelta(seconds=minimum_interval_s),
            )
        return self.enqueue_outbox_in_transaction(
            connection,
            owner_user_id=owner,
            workload_id=workload_id,
            event_id=event_id,
            channel=channel,
            recipient_key=recipient_key,
            coalesce_key="progress",
            next_attempt_at=due,
        )

    def _enqueue_notification_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        workload_id: str,
        event_id: int,
    ) -> None:
        """Record one visible event and its durable Telegram delivery together.

        The internal marker keeps the database invariant explicit; the pending
        Telegram row is the independently leased delivery request.  Both rows
        reference the immutable event, so a daemon restart cannot disconnect a
        state change from its user-facing notification.
        """

        self.enqueue_outbox_in_transaction(
            connection,
            owner_user_id=owner_user_id,
            workload_id=workload_id,
            event_id=event_id,
            channel="owner_event",
            recipient_key=owner_user_id,
            delivered=True,
        )
        self.enqueue_outbox_in_transaction(
            connection,
            owner_user_id=owner_user_id,
            workload_id=workload_id,
            event_id=event_id,
            channel="telegram",
            recipient_key=owner_user_id,
        )

    def claim_outbox(
        self,
        *,
        channel: str,
        worker_id: str,
        limit: int = 50,
        lease_duration: timedelta = timedelta(seconds=60),
        now: datetime | None = None,
    ) -> tuple[OutboxRecord, ...]:
        """Atomically lease bounded due rows; expired leases are recoverable."""

        selected_channel = self._outbox_channel(channel)
        worker = require_worker_id(worker_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer in 1..200")
        duration = require_lease_duration(lease_duration)
        if duration > timedelta(seconds=_MAX_OUTBOX_LEASE_SECONDS):
            raise ValueError("outbox lease duration exceeds maximum")
        current = normalize_instant(now or datetime.now(timezone.utc), name="now")
        current_text = instant_text(current)
        expiry = instant_text(current + duration, name="lease_expires_at")
        with self._transaction() as connection:
            candidates = connection.execute(
                """
                SELECT owner_user_id, id FROM outbox
                WHERE channel=? AND (
                    (state IN ('pending', 'failed')
                     AND (next_attempt_at IS NULL OR next_attempt_at<=?))
                    OR (state='leased' AND lease_expires_at IS NOT NULL
                        AND lease_expires_at<=?)
                )
                ORDER BY created_at, id
                LIMIT ?
                """,
                (selected_channel, current_text, current_text, limit),
            ).fetchall()
            records: list[OutboxRecord] = []
            for candidate in candidates:
                updated = connection.execute(
                    """
                    UPDATE outbox
                    SET state='leased', attempt_count=attempt_count+1,
                        lease_worker_id=?, lease_expires_at=?, fence=fence+1,
                        updated_at=?
                    WHERE owner_user_id=? AND id=? AND (
                        (state IN ('pending', 'failed')
                         AND (next_attempt_at IS NULL OR next_attempt_at<=?))
                        OR (state='leased' AND lease_expires_at IS NOT NULL
                            AND lease_expires_at<=?)
                    )
                    """,
                    (
                        worker, expiry, current_text,
                        candidate["owner_user_id"], candidate["id"],
                        current_text, current_text,
                    ),
                )
                if updated.rowcount == 1:
                    records.append(_row_to_outbox(self._outbox_row(
                        connection,
                        str(candidate["owner_user_id"]),
                        str(candidate["id"]),
                    )))
            return tuple(records)

    def confirm_outbox(
        self,
        record: OutboxRecord,
        *,
        worker_id: str,
        acknowledgement: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Confirm only the worker/fence that still owns the delivery lease."""

        if not isinstance(record, OutboxRecord):
            raise TypeError("record must be an OutboxRecord")
        worker = require_worker_id(worker_id)
        ack = canonical_json(
            dict(acknowledgement or {"delivery": "sent"}),
            max_bytes=MAX_EVENT_JSON_BYTES,
        )
        current = instant_text(normalize_instant(
            now or datetime.now(timezone.utc), name="now"
        ))
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE outbox
                SET state='sent', ack_json=?, lease_worker_id=NULL,
                    lease_expires_at=NULL, next_attempt_at=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='leased'
                  AND lease_worker_id=? AND fence=?
                """,
                (ack, current, record.owner_user_id, record.outbox_id,
                 worker, record.fence),
            )
            return updated.rowcount == 1

    def release_outbox(
        self,
        record: OutboxRecord,
        *,
        worker_id: str,
        retry_at: datetime | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Release a failed/undeliverable lease without deleting its row."""

        if not isinstance(record, OutboxRecord):
            raise TypeError("record must be an OutboxRecord")
        worker = require_worker_id(worker_id)
        current = normalize_instant(now or datetime.now(timezone.utc), name="now")
        retry = instant_text(retry_at or current, name="retry_at")
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE outbox
                SET state='pending', lease_worker_id=NULL, lease_expires_at=NULL,
                    next_attempt_at=?, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='leased'
                  AND lease_worker_id=? AND fence=?
                """,
                (
                    retry, instant_text(current), record.owner_user_id,
                    record.outbox_id, worker, record.fence,
                ),
            )
            return updated.rowcount == 1

    def list_units(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        state: UnitState | str | None = None,
        before: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> tuple[UnitReadRecord, ...]:
        """Read a redacted page of units without exposing result payloads."""

        owner = _require_owner(owner_user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer in 1..200")
        workload = self._select_workload(self._connection, owner, workload_id)
        revision_id = workload["active_revision_id"]
        if revision_id is None:
            return ()
        normalized_state = UnitState(state) if state is not None else None
        parameters: list[Any] = [owner, revision_id]
        clauses = ["u.owner_user_id=?", "u.revision_id=?"]
        if normalized_state is not None:
            clauses.append("u.state=?")
            parameters.append(normalized_state.value)
        if before is not None:
            if (
                not isinstance(before, tuple)
                or len(before) != 2
                or not all(isinstance(value, str) and value for value in before)
            ):
                raise ValueError("before must contain an update time and unit ID")
            updated_at, unit_id = before
            clauses.append("(u.updated_at<? OR (u.updated_at=? AND u.id<?))")
            parameters.extend((updated_at, updated_at, unit_id))
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT u.owner_user_id, u.id, u.revision_id, s.stage_key,
                   u.state, u.attempt_count, u.next_attempt_at,
                   u.error_class, u.updated_at
            FROM units u
            JOIN stages s
              ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
            WHERE {' AND '.join(clauses)}
            ORDER BY u.updated_at DESC, u.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(
            UnitReadRecord(
                owner_user_id=str(row["owner_user_id"]),
                unit_id=str(row["id"]),
                revision_id=str(row["revision_id"]),
                stage_key=str(row["stage_key"]),
                state=UnitState(row["state"]),
                attempt_count=int(row["attempt_count"]),
                next_attempt_at=row["next_attempt_at"],
                error_class=row["error_class"],
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def transition_workload(
        self,
        owner_user_id: str,
        workload_id: str,
        destination: WorkloadState | str,
        *,
        expected_version: int,
        payload: Mapping[str, Any] | None = None,
    ) -> WorkloadRecord:
        """Apply a non-command internal transition with CAS and one event.

        Completion is intentionally absent: only `evaluate_completion` may
        produce either completed state.
        """
        owner = _require_owner(owner_user_id)
        expected = _require_version(expected_version)
        target = WorkloadState(destination)
        if target in {
            WorkloadState.COMPLETED,
            WorkloadState.COMPLETED_WITH_ERRORS,
        }:
            raise ReservedCompletionTransitionError(
                "completion state is reserved for evaluate_completion"
            )
        event_type = _EVENT_BY_TARGET.get(target)
        if event_type is None:
            raise InvalidTransitionError(f"no internal event maps to {target.value}")
        event_payload = {} if payload is None else payload
        validate_event_payload(event_payload)
        with self._transaction() as connection:
            row = self._select_workload(connection, owner, workload_id)
            if int(row["version"]) != expected:
                raise VersionConflictError("workload version precondition failed")
            source = WorkloadState(row["state"])
            if not can_transition_workload(source, target):
                raise InvalidTransitionError(
                    f"workload transition {source.value}->{target.value} is forbidden"
                )
            updated = self._update_state_in_transaction(
                connection,
                owner=owner,
                workload_id=workload_id,
                source=source,
                target=target,
                expected_version=expected,
                terminal_reason=(event_payload if target in {
                    WorkloadState.CANCELLED, WorkloadState.FAILED,
                } else None),
            )
            merged_payload = dict(event_payload)
            merged_payload.update({
                "previous_state": source.value,
                "new_state": target.value,
                "new_version": updated.version,
            })
            event = self.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_type=event_type,
                payload=merged_payload,
            )
            if target in {WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED}:
                self._enqueue_notification_in_transaction(
                    connection,
                    owner_user_id=owner,
                    workload_id=workload_id,
                    event_id=event.event_id,
                )
            return updated

    def _update_state_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner: str,
        workload_id: str,
        source: WorkloadState,
        target: WorkloadState,
        expected_version: int,
        terminal_reason: Mapping[str, Any] | None = None,
    ) -> WorkloadRecord:
        now = utc_now()
        terminal_json = (
            canonical_json(terminal_reason, max_bytes=MAX_EVENT_JSON_BYTES)
            if terminal_reason is not None else None
        )
        updated = connection.execute(
            """
            UPDATE workloads
            SET state=?, version=version+1, updated_at=?,
                terminal_reason_json=COALESCE(?, terminal_reason_json)
            WHERE owner_user_id=? AND id=? AND state=? AND version=?
            """,
            (
                target.value, now, terminal_json, owner, workload_id,
                source.value, expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflictError("workload state compare-and-set failed")
        return _row_to_workload(
            self._select_workload(connection, owner, workload_id)
        )

    def _existing_command(
        self,
        connection: sqlite3.Connection,
        *,
        owner: str,
        workload_id: str,
        idempotency_key: str,
        command: str,
        payload_digest: str,
    ) -> WorkloadRecord | None:
        row = connection.execute(
            """
            SELECT command, payload_digest, result_json
            FROM commands
            WHERE owner_user_id=? AND workload_id=? AND idempotency_key=?
            """,
            (owner, workload_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["command"] != command or row["payload_digest"] != payload_digest:
            raise IdempotencyConflictError(
                "idempotency_key was already used with a different command payload"
            )
        return _snapshot_to_workload(row["result_json"])

    def _record_command(
        self,
        connection: sqlite3.Connection,
        *,
        owner: str,
        workload_id: str,
        idempotency_key: str,
        command: str,
        payload_digest: str,
        result: WorkloadRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO commands(
                owner_user_id, workload_id, idempotency_key, command,
                payload_digest, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner, workload_id, idempotency_key, command, payload_digest,
                canonical_json(_workload_snapshot(result), max_bytes=MAX_EVENT_JSON_BYTES),
                utc_now(),
            ),
        )

    def _control(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        command: str,
        idempotency_key: str,
        expected_version: int,
    ) -> WorkloadRecord:
        owner = _require_owner(owner_user_id)
        key = _require_key(idempotency_key, name="idempotency_key")
        expected = _require_version(expected_version)
        command_payload = {"command": command, "expected_version": expected}
        payload_digest = digest_json(
            "durable-command", command_payload, max_bytes=MAX_EVENT_JSON_BYTES
        )
        with self._transaction() as connection:
            replay = self._existing_command(
                connection,
                owner=owner,
                workload_id=workload_id,
                idempotency_key=key,
                command=command,
                payload_digest=payload_digest,
            )
            if replay is not None:
                return replay
            row = self._select_workload(connection, owner, workload_id)
            if int(row["version"]) != expected:
                raise VersionConflictError("workload version precondition failed")
            source = WorkloadState(row["state"])
            try:
                target = control_transition(command, source)
            except ValueError as exc:
                raise InvalidTransitionError(
                    f"cannot {command} {source.value}"
                ) from exc
            event_type = _EVENT_BY_TARGET.get(target) if target is not None else None

            if target is None:
                result = _row_to_workload(row)
            else:
                if not can_transition_workload(source, target):
                    raise InvalidTransitionError(
                        f"workload transition {source.value}->{target.value} is forbidden"
                    )
                result = self._update_state_in_transaction(
                    connection,
                    owner=owner,
                    workload_id=workload_id,
                    source=source,
                    target=target,
                    expected_version=expected,
                    terminal_reason=(
                        {"reason": "owner_cancelled"}
                        if target is WorkloadState.CANCELLED else None
                    ),
                )
                assert event_type is not None
                self.append_event_in_transaction(
                    connection,
                    owner_user_id=owner,
                    workload_id=workload_id,
                    event_type=event_type,
                    payload={
                        "previous_state": source.value,
                        "new_state": target.value,
                        "new_version": result.version,
                    },
                )
            self._record_command(
                connection,
                owner=owner,
                workload_id=workload_id,
                idempotency_key=key,
                command=command,
                payload_digest=payload_digest,
                result=result,
            )
            return result

    def request_pause(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> WorkloadRecord:
        return self._control(
            owner_user_id, workload_id, command="pause",
            idempotency_key=idempotency_key, expected_version=expected_version,
        )

    def request_resume(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> WorkloadRecord:
        return self._control(
            owner_user_id, workload_id, command="resume",
            idempotency_key=idempotency_key, expected_version=expected_version,
        )

    def request_cancel(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> WorkloadRecord:
        return self._control(
            owner_user_id, workload_id, command="cancel",
            idempotency_key=idempotency_key, expected_version=expected_version,
        )

    def record_attention_resolution(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        decision: str,
        expected_version: int,
        idempotency_key: str,
        note_redacted: str | None = None,
    ) -> WorkloadRecord:
        owner = _require_owner(owner_user_id)
        key = _require_key(idempotency_key, name="idempotency_key")
        expected = _require_version(expected_version)
        if decision not in {"retry", "cancel"}:
            raise ValueError("attention decision must be retry or cancel")
        if note_redacted is not None:
            if not isinstance(note_redacted, str) or len(note_redacted) > 2048:
                raise ValueError("note_redacted must be at most 2048 characters")
        payload = {
            "command": "resolve_attention",
            "decision": decision,
            "expected_version": expected,
            "note_redacted": note_redacted,
        }
        payload_digest = digest_json(
            "durable-command", payload, max_bytes=MAX_EVENT_JSON_BYTES
        )
        with self._transaction() as connection:
            replay = self._existing_command(
                connection,
                owner=owner,
                workload_id=workload_id,
                idempotency_key=key,
                command="resolve_attention",
                payload_digest=payload_digest,
            )
            if replay is not None:
                return replay
            row = self._select_workload(connection, owner, workload_id)
            if int(row["version"]) != expected:
                raise VersionConflictError("workload version precondition failed")
            source = WorkloadState(row["state"])
            if source is not WorkloadState.NEEDS_ATTENTION:
                raise InvalidTransitionError(
                    "attention can be resolved only from needs_attention"
                )
            target = (
                WorkloadState.QUEUED if decision == "retry"
                else WorkloadState.CANCELLED
            )
            result = self._update_state_in_transaction(
                connection,
                owner=owner,
                workload_id=workload_id,
                source=source,
                target=target,
                expected_version=expected,
                terminal_reason=(
                    {"reason": "attention_cancelled"}
                    if target is WorkloadState.CANCELLED else None
                ),
            )
            self.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_type=EventType.ATTENTION_RESOLVED,
                payload={
                    "decision": decision,
                    "new_state": target.value,
                    "new_version": result.version,
                },
            )
            self._record_command(
                connection,
                owner=owner,
                workload_id=workload_id,
                idempotency_key=key,
                command="resolve_attention",
                payload_digest=payload_digest,
                result=result,
            )
            connection.execute(
                """
                INSERT INTO attention_resolutions(
                    owner_user_id, workload_id, idempotency_key,
                    decision, note_redacted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (owner, workload_id, key, decision, note_redacted, utc_now()),
            )
            return result

    def unit_counters(
        self, owner_user_id: str, workload_id: str,
    ) -> UnitCounters:
        owner = _require_owner(owner_user_id)
        workload = self._select_workload(self._connection, owner, workload_id)
        revision_id = workload["active_revision_id"]
        if revision_id is None:
            return UnitCounters()
        discovered = int(self._connection.execute(
            """
            SELECT COUNT(*) FROM sources
            WHERE owner_user_id=? AND revision_id=?
            """,
            (owner, revision_id),
        ).fetchone()[0])
        row = self._connection.execute(
            """
            SELECT
                SUM(CASE WHEN state='committed' THEN 1 ELSE 0 END) AS committed,
                SUM(CASE WHEN state IN ('failed_permanent', 'cancelled') THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN state='skipped' THEN 1 ELSE 0 END) AS skipped,
                SUM(CASE WHEN state='needs_attention' THEN 1 ELSE 0 END) AS attention,
                SUM(CASE WHEN state IN ('pending', 'leased', 'running', 'retry_wait') THEN 1 ELSE 0 END) AS pending
            FROM units WHERE owner_user_id=? AND revision_id=?
            """,
            (owner, revision_id),
        ).fetchone()
        return UnitCounters(
            discovered=discovered,
            committed=int(row["committed"] or 0),
            failed=int(row["failed"] or 0),
            skipped=int(row["skipped"] or 0),
            attention=int(row["attention"] or 0),
            pending=int(row["pending"] or 0),
        )

    def execution_summary(
        self, owner_user_id: str, workload_id: str,
    ) -> dict[str, Any]:
        """Return a bounded, redacted execution view for the control façade.

        This deliberately projects the frozen plan instead of returning its
        JSON.  It exposes only operational facts that a workload owner needs
        to interpret progress: admitted limits, stages, aggregate outcomes and
        policy warnings.  Inputs, prompts, locators, result payloads and
        catalog snapshots remain private repository data.
        """

        owner = _require_owner(owner_user_id)
        workload = self._select_workload(self._connection, owner, workload_id)
        revision_id = workload["active_revision_id"]
        if revision_id is None:
            return {
                "budget": {},
                "stages": [],
                "error_categories": [],
                "warnings": [],
            }
        revision = self._connection.execute(
            """
            SELECT plan_json, caps_truncated, partial_output_accepted,
                   failure_policy
            FROM revisions
            WHERE owner_user_id=? AND id=? AND workload_id=?
            """,
            (owner, revision_id, workload_id),
        ).fetchone()
        if revision is None:
            raise DurableStoreError("active revision is missing")
        try:
            budget_source = json.loads(str(revision["plan_json"])).get("budgets", {})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DurableStoreError("frozen plan cannot be summarized") from exc
        budget_keys = (
            "max_units",
            "max_attempts_per_unit",
            "max_wall_time_s",
            "max_bytes_read",
            "max_bytes_written",
            "max_tokens",
            "max_cost_micros",
            "max_artifacts",
            "max_concurrency",
        )
        budget = {
            key: value
            for key in budget_keys
            if isinstance((value := budget_source.get(key)), int) and not isinstance(value, bool)
        }
        rows = self._connection.execute(
            """
            SELECT s.stage_key, s.stage_type, s.runner_kind, s.runner_name,
                   s.max_units, s.timeout_s, s.required_flag, s.resources_json,
                   COUNT(u.id) AS total,
                   SUM(CASE WHEN u.state='committed' THEN 1 ELSE 0 END) AS committed,
                   SUM(CASE WHEN u.state IN ('failed_permanent', 'cancelled') THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN u.state='skipped' THEN 1 ELSE 0 END) AS skipped,
                   SUM(CASE WHEN u.state='needs_attention' THEN 1 ELSE 0 END) AS attention,
                   SUM(CASE WHEN u.state IN ('pending', 'leased', 'running', 'retry_wait') THEN 1 ELSE 0 END) AS pending
            FROM stages s
            LEFT JOIN units u
              ON u.owner_user_id=s.owner_user_id
             AND u.revision_id=s.revision_id
             AND u.stage_id=s.id
            WHERE s.owner_user_id=? AND s.revision_id=?
            GROUP BY s.owner_user_id, s.id
            ORDER BY s.position, s.stage_key
            """,
            (owner, revision_id),
        ).fetchall()
        stages: list[dict[str, Any]] = []
        for row in rows:
            try:
                resource_source = json.loads(str(row["resources_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DurableStoreError("frozen stage resources are invalid") from exc
            stages.append({
                "stage_key": str(row["stage_key"]),
                "stage_type": str(row["stage_type"]),
                "runner_kind": str(row["runner_kind"]),
                "runner_name": str(row["runner_name"]),
                "max_units": int(row["max_units"]),
                "timeout_s": int(row["timeout_s"]),
                "required": bool(row["required_flag"]),
                "resources": {
                    key: value
                    for key in sorted(RESOURCE_KEYS)
                    if isinstance((value := resource_source.get(key)), int)
                    and not isinstance(value, bool)
                    and value > 0
                },
                "counters": {
                    key: int(row[key] or 0)
                    for key in ("total", "committed", "failed", "skipped", "attention", "pending")
                },
            })
        errors = self._connection.execute(
            """
            SELECT error_class, COUNT(*) AS count
            FROM units
            WHERE owner_user_id=? AND revision_id=? AND error_class IS NOT NULL
            GROUP BY error_class
            ORDER BY count DESC, error_class ASC
            LIMIT 20
            """,
            (owner, revision_id),
        ).fetchall()
        warnings: list[str] = []
        if bool(revision["caps_truncated"]):
            warnings.append("inventory_truncated")
        if bool(revision["partial_output_accepted"]):
            warnings.append("partial_output_accepted")
        if str(revision["failure_policy"]) == "declared":
            warnings.append("declared_failures_allowed")
        unavailable = self._connection.execute(
            """
            SELECT COUNT(*) FROM sources
            WHERE owner_user_id=? AND revision_id=? AND state<>'ready'
            """,
            (owner, revision_id),
        ).fetchone()[0]
        if int(unavailable):
            warnings.append("source_coverage_incomplete")
        return {
            "budget": budget,
            "stages": stages,
            "error_categories": [
                {"error_code": str(row["error_class"]), "count": int(row["count"])}
                for row in errors
            ],
            "warnings": warnings,
        }

    def evaluate_completion(
        self, owner_user_id: str, workload_id: str,
    ) -> CompletionAssessment:
        """Apply all nine RM-0004 completion checks in one transaction."""
        owner = _require_owner(owner_user_id)
        with self._transaction() as connection:
            workload_row = self._select_workload(connection, owner, workload_id)
            workload = _row_to_workload(workload_row)
            counters = self.unit_counters(owner, workload_id)
            if workload.state in {
                WorkloadState.COMPLETED,
                WorkloadState.COMPLETED_WITH_ERRORS,
            }:
                return CompletionAssessment(
                    eligible=True,
                    target_state=workload.state,
                    reasons=(),
                    counters=counters,
                    workload_version=workload.version,
                )

            reasons: list[str] = []

            def block(reason: str) -> None:
                if reason not in reasons:
                    reasons.append(reason)

            if workload.state is not WorkloadState.RUNNING:
                block("workload_not_running")
            revision_id = workload.active_revision_id
            if revision_id is None:
                block("inventory_not_sealed")
                return CompletionAssessment(
                    eligible=False,
                    target_state=None,
                    reasons=tuple(reasons),
                    counters=counters,
                    workload_version=workload.version,
                )
            revision = connection.execute(
                """
                SELECT inventory_sealed, inventory_digest,
                       expected_source_count, caps_truncated,
                       partial_output_accepted, usage_complete, failure_policy,
                       tolerated_error_classes_json, required_artifacts_json
                FROM revisions
                WHERE owner_user_id=? AND id=? AND workload_id=?
                """,
                (owner, revision_id, workload_id),
            ).fetchone()
            if revision is None:
                block("inventory_not_sealed")
                return CompletionAssessment(
                    eligible=False, target_state=None, reasons=tuple(reasons),
                    counters=counters, workload_version=workload.version,
                )

            # 1. A sealed inventory with a canonical digest exists.
            if not revision["inventory_sealed"] or not revision["inventory_digest"]:
                block("inventory_not_sealed")

            # 2. The denominator is exact and every source is accounted.
            source_summary = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN accounted=0 THEN 1 ELSE 0 END) AS unaccounted,
                       SUM(CASE WHEN state IN ('unstable', 'missing') THEN 1 ELSE 0 END) AS unavailable,
                       SUM(CASE WHEN state='skipped' THEN 1 ELSE 0 END) AS skipped
                FROM sources WHERE owner_user_id=? AND revision_id=?
                """,
                (owner, revision_id),
            ).fetchone()
            if int(source_summary["total"]) != int(revision["expected_source_count"]):
                block("source_count_mismatch")
            if int(source_summary["unaccounted"] or 0) > 0:
                block("sources_unaccounted")
            if int(source_summary["unavailable"] or 0) > 0:
                block("source_unstable_or_missing")
            if int(source_summary["skipped"] or 0) > 0:
                if revision["partial_output_accepted"]:
                    accepted_source_skip = True
                else:
                    accepted_source_skip = False
                    block("source_skip_unaccepted")
            else:
                accepted_source_skip = False

            # 3. Required stages have their cardinality and terminal units.
            stage_rows = connection.execute(
                """
                SELECT id, stage_type, cardinality, required_flag
                FROM stages
                WHERE owner_user_id=? AND revision_id=?
                ORDER BY position
                """,
                (owner, revision_id),
            ).fetchall()
            for stage in stage_rows:
                if not stage["required_flag"] or stage["stage_type"] == "inventory":
                    continue
                unit_count = int(connection.execute(
                    """
                    SELECT COUNT(*) FROM units
                    WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                    """,
                    (owner, revision_id, stage["id"]),
                ).fetchone()[0])
                expected_units = (
                    int(revision["expected_source_count"])
                    if stage["cardinality"] == "per_source" else 1
                )
                if unit_count < expected_units:
                    block("required_units_missing")

            tolerated = frozenset(json.loads(revision["tolerated_error_classes_json"]))
            accepted_failures = 0
            accepted_partial = False
            unit_rows = connection.execute(
                """
                SELECT u.id, u.state, u.error_class, u.partial_output,
                       u.committed_result_id, u.expected_dependency_count,
                       s.required_flag
                FROM units u
                JOIN stages s
                  ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
                WHERE u.owner_user_id=? AND u.revision_id=?
                """,
                (owner, revision_id),
            ).fetchall()
            for unit in unit_rows:
                state = UnitState(unit["state"])
                if state not in TERMINAL_UNIT_STATES:
                    block("units_not_terminal")
                    continue
                if state is UnitState.CANCELLED:
                    block("cancelled_unit")
                elif state is UnitState.SKIPPED and unit["required_flag"]:
                    block("required_unit_skipped")
                elif state is UnitState.FAILED_PERMANENT:
                    if (
                        revision["failure_policy"] == "declared"
                        and unit["error_class"] in tolerated
                    ):
                        accepted_failures += 1
                    else:
                        block("untolerated_unit_failure")
                elif state is UnitState.COMMITTED:
                    if unit["committed_result_id"] is None:
                        block("committed_unit_without_result")
                    else:
                        dependency_count = int(connection.execute(
                            """
                            SELECT COUNT(*) FROM dependencies
                            WHERE owner_user_id=? AND revision_id=?
                              AND child_result_id=?
                            """,
                            (owner, revision_id, unit["committed_result_id"]),
                        ).fetchone()[0])
                        if dependency_count < int(unit["expected_dependency_count"]):
                            block("result_dependencies_unresolved")
                if unit["partial_output"]:
                    if revision["partial_output_accepted"]:
                        accepted_partial = True
                    else:
                        block("partial_output_unaccepted")

            # 4. Caps and truncation need an explicit revision-level acceptance.
            if revision["caps_truncated"]:
                if revision["partial_output_accepted"]:
                    accepted_partial = True
                else:
                    block("cap_or_truncation_unaccepted")

            # 5 is enforced above through each result's expected dependency count.
            invalid_dependency_count = int(connection.execute(
                """
                SELECT COUNT(*)
                FROM dependencies d
                JOIN results child
                  ON child.owner_user_id=d.owner_user_id
                 AND child.id=d.child_result_id
                 AND child.revision_id=d.revision_id
                JOIN units child_unit
                  ON child_unit.owner_user_id=child.owner_user_id
                 AND child_unit.id=child.unit_id
                JOIN results source_result
                  ON source_result.owner_user_id=d.owner_user_id
                 AND source_result.id=d.source_result_id
                 AND source_result.revision_id=d.revision_id
                JOIN units source_unit
                  ON source_unit.owner_user_id=source_result.owner_user_id
                 AND source_unit.id=source_result.unit_id
                LEFT JOIN stage_dependencies declared
                  ON declared.owner_user_id=d.owner_user_id
                 AND declared.revision_id=d.revision_id
                 AND declared.stage_id=child_unit.stage_id
                 AND declared.depends_on_stage_id=source_unit.stage_id
                WHERE d.owner_user_id=? AND d.revision_id=?
                  AND declared.stage_id IS NULL
                """,
                (owner, revision_id),
            ).fetchone()[0])
            if invalid_dependency_count:
                block("result_dependency_not_declared")

            # 6-7. Every named artifact exists in the required state and its
            # digest, schema and postconditions have been verified.
            required_artifacts = json.loads(revision["required_artifacts_json"])
            for requirement in required_artifacts:
                artifact = connection.execute(
                    """
                    SELECT state, digest_verified, schema_valid,
                           postconditions_valid, schema_version, mime_type
                    FROM artifacts
                    WHERE owner_user_id=? AND revision_id=? AND logical_name=?
                    """,
                    (owner, revision_id, requirement["name"]),
                ).fetchone()
                if artifact is None:
                    block("required_artifact_missing")
                    continue
                if artifact["state"] not in {"committed", "published"}:
                    block("required_artifact_not_committed")
                if artifact["schema_version"] != requirement["schema_version"]:
                    block("artifact_schema_version_mismatch")
                if artifact["mime_type"] != requirement["mime_type"]:
                    block("artifact_mime_type_mismatch")
                if not (
                    artifact["digest_verified"]
                    and artifact["schema_valid"]
                    and artifact["postconditions_valid"]
                ):
                    block("artifact_validation_incomplete")

            # 8. Final usage/cost accounting is a materialized revision fact.
            if not revision["usage_complete"]:
                block("usage_not_materialized")

            if reasons:
                return CompletionAssessment(
                    eligible=False,
                    target_state=None,
                    reasons=tuple(reasons),
                    counters=counters,
                    workload_version=workload.version,
                )

            has_errors = bool(
                accepted_failures
                or accepted_partial
                or accepted_source_skip
                or counters.skipped
            )
            target = (
                WorkloadState.COMPLETED_WITH_ERRORS
                if has_errors else WorkloadState.COMPLETED
            )
            event_type = (
                EventType.COMPLETED_WITH_ERRORS
                if has_errors else EventType.COMPLETED
            )

            # 9. Allocate the terminal event and outbox before the guarded
            # state update; all three are still inside this one transaction.
            terminal_event = self.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_type=event_type,
                payload={
                    "revision_id": revision_id,
                    "target_state": target.value,
                    "coverage": {
                        "discovered": counters.discovered,
                        "committed": counters.committed,
                        "failed": counters.failed,
                        "skipped": counters.skipped,
                        "attention": counters.attention,
                        "pending": counters.pending,
                    },
                    "accepted_failures": accepted_failures,
                    "partial_accepted": accepted_partial,
                },
            )
            # The durable event marker satisfies the database completion
            # guard immediately.  The Telegram row is independently leased;
            # a daemon restart can recover the same immutable notification.
            self._enqueue_notification_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_id=terminal_event.event_id,
            )
            now = utc_now()
            updated = connection.execute(
                """
                UPDATE workloads
                SET state=?, version=version+1, updated_at=?,
                    terminal_reason_json=?
                WHERE owner_user_id=? AND id=? AND state='running' AND version=?
                """,
                (
                    target.value, now,
                    canonical_json(
                        {"completion": "verified", "with_errors": has_errors},
                        max_bytes=MAX_EVENT_JSON_BYTES,
                    ),
                    owner, workload_id, workload.version,
                ),
            )
            if updated.rowcount != 1:
                raise VersionConflictError("completion compare-and-set failed")
            return CompletionAssessment(
                eligible=True,
                target_state=target,
                reasons=(),
                counters=counters,
                event_id=terminal_event.event_id,
                workload_version=workload.version + 1,
            )

    @staticmethod
    def _operation_now(now: datetime | None) -> tuple[datetime, str]:
        value = (
            datetime.now(timezone.utc)
            if now is None
            else normalize_instant(now, name="now")
        )
        return value, instant_text(value, name="now")

    @staticmethod
    def _attempt_metrics(
        row: sqlite3.Row,
        **updates: Any,
    ) -> str:
        value = json.loads(str(row["metrics_json"]))
        if not isinstance(value, dict):
            raise DurableStoreError("attempt metrics are not an object")
        value.update(updates)
        return canonical_json(
            value, max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
        )

    @staticmethod
    def _select_lease_row(
        connection: sqlite3.Connection,
        lease: Lease,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT
                u.owner_user_id, u.id AS unit_id, u.revision_id, u.stage_id,
                u.unit_key, u.source_row_id, u.shard_key,
                u.expected_dependency_count, u.state AS unit_state, u.attempt_count,
                u.lease_worker_id, u.active_attempt_id, u.fence,
                u.lease_expires_at, u.committed_result_id,
                r.workload_id, w.priority AS workload_priority,
                r.plan_json, r.inventory_json, r.catalog_snapshot_json,
                r.policy_snapshot_json,
                s.stage_key, s.runner_kind, s.runner_name, s.effect_profile,
                s.input_bindings_json, s.output_schema_json, s.retry_json,
                s.resources_json, s.timeout_s,
                src.source_id, src.ordinal AS source_ordinal,
                src.device_id AS source_device_id,
                src.locator_redacted AS source_locator_redacted,
                src.kind AS source_kind, src.size_bytes AS source_size_bytes,
                src.mtime_ns AS source_mtime_ns,
                src.content_digest AS source_content_digest,
                a.number AS attempt_number, a.fence AS attempt_fence,
                a.worker_id AS attempt_worker_id,
                a.state AS attempt_state, a.ended_at,
                a.executor_snapshot_json, a.model_snapshot_json, a.metrics_json,
                a.device_id AS attempt_device_id,
                a.invocation_id AS attempt_invocation_id
            FROM units u
            JOIN revisions r
              ON r.owner_user_id=u.owner_user_id AND r.id=u.revision_id
            JOIN workloads w
              ON w.owner_user_id=r.owner_user_id AND w.id=r.workload_id
            JOIN stages s
              ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
             AND s.revision_id=u.revision_id
            LEFT JOIN attempts a
              ON a.owner_user_id=u.owner_user_id AND a.id=?
             AND a.unit_id=u.id
            LEFT JOIN sources src
              ON src.owner_user_id=u.owner_user_id AND src.id=u.source_row_id
             AND src.revision_id=u.revision_id
            WHERE u.owner_user_id=? AND u.id=?
            """,
            (lease.attempt_id, lease.owner_user_id, lease.unit_id),
        ).fetchone()

    @staticmethod
    def _lease_matches(row: sqlite3.Row | None, lease: Lease) -> bool:
        if row is None or row["attempt_number"] is None:
            return False
        output = json.loads(str(row["output_schema_json"]))
        resources = json.loads(str(row["resources_json"]))
        retry = RetryPolicy.from_mapping(json.loads(str(row["retry_json"])))
        resource_claims = tuple((key, int(resources[key])) for key in RESOURCE_KEYS)
        return (
            row["owner_user_id"] == lease.owner_user_id
            and row["workload_id"] == lease.workload_id
            and row["revision_id"] == lease.revision_id
            and row["stage_id"] == lease.stage_id
            and row["stage_key"] == lease.stage_key
            and row["unit_id"] == lease.unit_id
            and row["unit_key"] == lease.unit_key
            and row["active_attempt_id"] == lease.attempt_id
            and int(row["attempt_number"]) == lease.attempt_number
            and int(row["fence"]) == lease.fence
            and int(row["attempt_fence"]) == lease.fence
            and row["lease_worker_id"] == lease.worker_id
            and row["attempt_worker_id"] == lease.worker_id
            and row["runner_kind"] == lease.runner_kind.value
            and row["runner_name"] == lease.runner_name
            and row["effect_profile"] == lease.effect_profile.value
            and output.get("name") == lease.output_schema_version
            and resource_claims == lease.resource_claims
            and retry == lease.retry_policy
        )

    @staticmethod
    def _promote_due_retries_in_transaction(
        connection: sqlite3.Connection,
        now_text: str,
        *,
        limit: int,
    ) -> int:
        rows = connection.execute(
            """
            SELECT owner_user_id, id
            FROM units
            WHERE state='retry_wait' AND next_attempt_at<=?
            ORDER BY next_attempt_at, owner_user_id, id
            LIMIT ?
            """,
            (now_text, limit),
        ).fetchall()
        for row in rows:
            updated = connection.execute(
                """
                UPDATE units
                SET state='pending', next_attempt_at=NULL,
                    error_class=NULL, partial_output=0,
                    terminal_detail_json=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='retry_wait'
                  AND next_attempt_at<=?
                """,
                (now_text, row["owner_user_id"], row["id"], now_text),
            )
            if updated.rowcount != 1:
                raise DurableStoreError("retry promotion compare-and-set failed")
        return len(rows)

    def claim_next(
        self,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        capabilities: WorkerCapabilities,
    ) -> Lease | None:
        """Atomically choose and fence one admitted unit."""
        worker = require_worker_id(worker_id)
        current = normalize_instant(now, name="now")
        current_text = instant_text(current, name="now")
        duration = require_lease_duration(lease_duration)
        expiry_text = instant_text(current + duration, name="lease expiry")
        if not isinstance(capabilities, WorkerCapabilities):
            raise TypeError("capabilities must be WorkerCapabilities")

        runner_clause = " OR ".join(
            "(s.runner_kind=? AND s.runner_name=?)"
            for _binding in capabilities.runner_bindings
        )
        runner_parameters: list[Any] = []
        for kind, name in capabilities.runner_bindings:
            runner_parameters.extend((kind.value, name))
        effect_clause = ",".join("?" for _ in capabilities.effect_profiles)
        limits = capabilities.resource_map()

        with self._transaction() as connection:
            self._promote_due_retries_in_transaction(
                connection, current_text, limit=1000,
            )
            row = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        u.owner_user_id, u.id AS unit_id, u.revision_id,
                        u.stage_id, u.unit_key, u.attempt_count, u.fence,
                        w.id AS workload_id, w.state AS workload_state,
                        w.version AS workload_version, w.priority,
                        w.created_at AS workload_created_at,
                        s.stage_key, s.position, s.runner_kind, s.runner_name,
                        s.effect_profile, s.output_schema_json, s.retry_json,
                        s.resources_json,
                        COALESCE(sc.last_selected_seq, 0) AS selected_seq,
                        ROW_NUMBER() OVER (
                            PARTITION BY u.owner_user_id, w.id
                            ORDER BY s.position, u.created_at, u.unit_key
                        ) AS unit_rank
                    FROM units u
                    JOIN revisions r
                      ON r.owner_user_id=u.owner_user_id AND r.id=u.revision_id
                    JOIN workloads w
                      ON w.owner_user_id=r.owner_user_id
                     AND w.id=r.workload_id
                     AND w.active_revision_id=r.id
                    JOIN stages s
                      ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
                     AND s.revision_id=u.revision_id
                    LEFT JOIN scheduler_credits sc
                      ON sc.owner_user_id=w.owner_user_id
                     AND sc.workload_id=w.id
                    WHERE u.state='pending'
                      AND (u.next_attempt_at IS NULL OR u.next_attempt_at<=?)
                      AND u.attempt_count < CAST(
                          json_extract(s.retry_json, '$.max_attempts') AS INTEGER
                      )
                      AND w.state IN ('queued', 'running')
                      AND s.effect_profile IN ({effect_clause})
                      AND ({runner_clause})
                      AND CAST(json_extract(s.resources_json, '$.cpu') AS INTEGER)<=?
                      AND CAST(json_extract(s.resources_json, '$.device') AS INTEGER)<=?
                      AND CAST(json_extract(s.resources_json, '$.llm') AS INTEGER)<=?
                      AND CAST(json_extract(s.resources_json, '$.local_io') AS INTEGER)<=?
                      AND CAST(json_extract(s.resources_json, '$.network_io') AS INTEGER)<=?
                      AND CAST(json_extract(s.resources_json, '$.vlm') AS INTEGER)<=?
                )
                SELECT * FROM ranked
                WHERE unit_rank=1
                ORDER BY selected_seq,
                         CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                         workload_created_at, owner_user_id, workload_id,
                         position, unit_key
                LIMIT 1
                """,
                (
                    current_text,
                    *capabilities.accepted_effects(),
                    *runner_parameters,
                    limits["cpu"], limits["device"], limits["llm"],
                    limits["local_io"], limits["network_io"], limits["vlm"],
                ),
            ).fetchone()
            if row is None:
                return None

            attempt_number = int(row["attempt_count"]) + 1
            fence = int(row["fence"]) + 1
            attempt_id = _new_id("att")
            executor_snapshot = canonical_json(
                {
                    "schema_version": "metnos.durable-executor-snapshot/1",
                    "mode": "dummy",
                    "runner_kind": row["runner_kind"],
                    "runner_name": row["runner_name"],
                },
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            model_snapshot = canonical_json(
                {
                    "schema_version": "metnos.durable-model-snapshot/1",
                    "binding": None,
                },
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            metrics = canonical_json(
                {
                    "schema_version": "metnos.durable-attempt-metrics/1",
                    "attempt_number": attempt_number,
                    "execution_started": False,
                },
                max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
            )
            connection.execute(
                """
                INSERT INTO attempts(
                    owner_user_id, id, unit_id, number, fence, worker_id,
                    state, started_at, executor_snapshot_json,
                    model_snapshot_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'leased', ?, ?, ?, ?)
                """,
                (
                    row["owner_user_id"], attempt_id, row["unit_id"],
                    attempt_number, fence, worker, current_text,
                    executor_snapshot, model_snapshot, metrics,
                ),
            )
            updated = connection.execute(
                """
                UPDATE units
                SET state='leased', attempt_count=?, lease_worker_id=?,
                    active_attempt_id=?, fence=?, lease_expires_at=?,
                    next_attempt_at=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='pending'
                  AND attempt_count=? AND fence=?
                """,
                (
                    attempt_number, worker, attempt_id, fence, expiry_text,
                    current_text, row["owner_user_id"], row["unit_id"],
                    row["attempt_count"], row["fence"],
                ),
            )
            if updated.rowcount != 1:
                raise DurableStoreError("claim compare-and-set failed")

            if row["workload_state"] == WorkloadState.QUEUED.value:
                started = connection.execute(
                    """
                    UPDATE workloads
                    SET state='running', version=version+1, updated_at=?
                    WHERE owner_user_id=? AND id=? AND state='queued' AND version=?
                    """,
                    (
                        current_text, row["owner_user_id"], row["workload_id"],
                        row["workload_version"],
                    ),
                )
                if started.rowcount != 1:
                    raise DurableStoreError("workload start compare-and-set failed")
                self.append_event_in_transaction(
                    connection,
                    owner_user_id=row["owner_user_id"],
                    workload_id=row["workload_id"],
                    event_type=EventType.RUNNING,
                    payload={
                        "previous_state": WorkloadState.QUEUED.value,
                        "new_state": WorkloadState.RUNNING.value,
                        "new_version": int(row["workload_version"]) + 1,
                        "reason_code": "first_unit_claimed",
                    },
                )

            connection.execute(
                """
                INSERT INTO scheduler_credits(
                    owner_user_id, workload_id, deficit,
                    last_selected_seq, quota, updated_at
                ) VALUES (?, ?, 0, 0, 1, ?)
                ON CONFLICT(owner_user_id, workload_id) DO NOTHING
                """,
                (row["owner_user_id"], row["workload_id"], current_text),
            )
            connection.execute(
                """
                UPDATE scheduler_credits
                SET last_selected_seq=(
                        SELECT COALESCE(MAX(last_selected_seq), 0) + 1
                        FROM scheduler_credits
                    ),
                    updated_at=?
                WHERE owner_user_id=? AND workload_id=?
                """,
                (current_text, row["owner_user_id"], row["workload_id"]),
            )

            output = json.loads(str(row["output_schema_json"]))
            resources = json.loads(str(row["resources_json"]))
            return Lease(
                owner_user_id=str(row["owner_user_id"]),
                workload_id=str(row["workload_id"]),
                revision_id=str(row["revision_id"]),
                stage_id=str(row["stage_id"]),
                stage_key=str(row["stage_key"]),
                unit_id=str(row["unit_id"]),
                unit_key=str(row["unit_key"]),
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                fence=fence,
                worker_id=worker,
                lease_expires_at=expiry_text,
                runner_kind=RunnerKind(row["runner_kind"]),
                runner_name=str(row["runner_name"]),
                effect_profile=DurableEffect(row["effect_profile"]),
                output_schema_version=str(output["name"]),
                resource_claims=tuple(
                    (key, int(resources[key])) for key in RESOURCE_KEYS
                ),
                retry_policy=RetryPolicy.from_mapping(
                    json.loads(str(row["retry_json"]))
                ),
            )

    def mark_running(
        self,
        lease: Lease,
        *,
        now: datetime | None = None,
    ) -> LeaseMutationStatus:
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return LeaseMutationStatus.STALE_FENCE
            assert row is not None
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            if row["unit_state"] == "running" and row["attempt_state"] == "running":
                return LeaseMutationStatus.ALREADY_APPLIED
            if row["unit_state"] != "leased" or row["attempt_state"] != "leased":
                return LeaseMutationStatus.INVALID_STATE
            metrics = self._attempt_metrics(
                row,
                execution_started=True,
                execution_started_at=current_text,
            )
            changed_attempt = connection.execute(
                """
                UPDATE attempts
                SET state='running', metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state='leased' AND ended_at IS NULL
                """,
                (
                    metrics, lease.owner_user_id, lease.attempt_id,
                    lease.unit_id, lease.fence, lease.worker_id,
                ),
            )
            changed_unit = connection.execute(
                """
                UPDATE units SET state='running', updated_at=?
                WHERE owner_user_id=? AND id=? AND active_attempt_id=?
                  AND fence=? AND lease_worker_id=? AND state='leased'
                """,
                (
                    current_text, lease.owner_user_id, lease.unit_id,
                    lease.attempt_id, lease.fence, lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("mark-running compare-and-set failed")
            return LeaseMutationStatus.APPLIED

    def heartbeat(
        self,
        lease: Lease,
        new_expiry: datetime,
        *,
        now: datetime | None = None,
    ) -> LeaseMutationStatus:
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        current, current_text = self._operation_now(now)
        expiry = normalize_instant(new_expiry, name="new_expiry")
        require_lease_duration(expiry - current)
        expiry_text = instant_text(expiry, name="new_expiry")
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return LeaseMutationStatus.STALE_FENCE
            assert row is not None
            if row["unit_state"] not in {"leased", "running"}:
                return LeaseMutationStatus.INVALID_STATE
            stored_expiry = parse_instant(str(row["lease_expires_at"]))
            if stored_expiry <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            if expiry <= stored_expiry:
                raise ValueError("new_expiry must extend the persisted lease")
            updated = connection.execute(
                """
                UPDATE units
                SET lease_expires_at=?, updated_at=?
                WHERE owner_user_id=? AND id=? AND active_attempt_id=?
                  AND fence=? AND lease_worker_id=?
                  AND state IN ('leased', 'running')
                  AND lease_expires_at>?
                """,
                (
                    expiry_text, current_text, lease.owner_user_id,
                    lease.unit_id, lease.attempt_id, lease.fence,
                    lease.worker_id, current_text,
                ),
            )
            if updated.rowcount != 1:
                raise DurableStoreError("heartbeat compare-and-set failed")
            return LeaseMutationStatus.APPLIED

    def execution_inputs(self, lease: Lease) -> dict[str, Any]:
        """Read the immutable execution facts for one fenced attempt.

        This is the only repository read used by the real bridge.  It exposes
        redacted source identity and already committed direct dependencies, not
        paths, credentials or an unrestricted database connection.
        """
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                raise DurableStoreError("execution inputs require the active fence")
            assert row is not None
            dependencies = connection.execute(
                """
                SELECT parent.stage_key, result.id AS result_id, result.digest,
                       result.schema_version, result.payload_json,
                       parent_unit.source_row_id,
                       source.source_id, source.ordinal AS source_ordinal
                FROM stage_dependencies declared
                JOIN stages parent
                  ON parent.owner_user_id=declared.owner_user_id
                 AND parent.id=declared.depends_on_stage_id
                 AND parent.revision_id=declared.revision_id
                JOIN units parent_unit
                  ON parent_unit.owner_user_id=parent.owner_user_id
                 AND parent_unit.revision_id=parent.revision_id
                 AND parent_unit.stage_id=parent.id
                JOIN results result
                  ON result.owner_user_id=parent_unit.owner_user_id
                 AND result.id=parent_unit.committed_result_id
                 AND result.revision_id=parent_unit.revision_id
                LEFT JOIN sources source
                  ON source.owner_user_id=parent_unit.owner_user_id
                 AND source.id=parent_unit.source_row_id
                 AND source.revision_id=parent_unit.revision_id
                WHERE declared.owner_user_id=?
                  AND declared.revision_id=?
                  AND declared.stage_id=?
                ORDER BY parent.position, source.ordinal, result.id
                """,
                (lease.owner_user_id, lease.revision_id, lease.stage_id),
            ).fetchall()
            source = None
            if row["source_row_id"] is not None:
                source = {
                    "source_id": str(row["source_id"]),
                    "ordinal": int(row["source_ordinal"]),
                    "device_id": str(row["source_device_id"]),
                    "locator_redacted": str(row["source_locator_redacted"]),
                    "kind": str(row["source_kind"]),
                    "size_bytes": int(row["source_size_bytes"]),
                    "mtime_ns": int(row["source_mtime_ns"]),
                    "content_digest": str(row["source_content_digest"]),
                }
            return {
                "plan": json.loads(str(row["plan_json"])),
                "inventory": json.loads(str(row["inventory_json"])),
                "priority": str(row["workload_priority"]),
                "catalog_snapshot": json.loads(str(row["catalog_snapshot_json"])),
                "policy_snapshot": json.loads(str(row["policy_snapshot_json"])),
                "stage": {
                    "id": str(row["stage_id"]),
                    "key": str(row["stage_key"]),
                    "source_row_id": row["source_row_id"],
                    "shard_key": row["shard_key"],
                    "runner_kind": str(row["runner_kind"]),
                    "runner_name": str(row["runner_name"]),
                    "effect_profile": str(row["effect_profile"]),
                    "input_bindings": json.loads(str(row["input_bindings_json"])),
                    "output_schema": json.loads(str(row["output_schema_json"])),
                    "timeout_s": int(row["timeout_s"]),
                    "resource_claims": json.loads(str(row["resources_json"])),
                    "expected_dependency_count": int(row["expected_dependency_count"]),
                },
                "source": source,
                "dependencies": tuple({
                    "stage_key": str(item["stage_key"]),
                    "result_id": str(item["result_id"]),
                    "digest": str(item["digest"]),
                    "schema_version": str(item["schema_version"]),
                    "payload": (
                        json.loads(str(item["payload_json"]))
                        if item["payload_json"] is not None else None
                    ),
                    "source_row_id": item["source_row_id"],
                    "source_id": item["source_id"],
                    "source_ordinal": item["source_ordinal"],
                } for item in dependencies),
            }

    def record_execution_facts(
        self,
        lease: Lease,
        *,
        executor_snapshot: Mapping[str, Any],
        model_snapshot: Mapping[str, Any],
        device_id: str | None = None,
        invocation_id: str | None = None,
        now: datetime | None = None,
    ) -> LeaseMutationStatus:
        """Persist frozen real-runner facts before the universal invocation.

        A retry or a stale worker cannot replace the snapshot of another
        attempt.  Repeating the same call is idempotent; changing facts after
        they have been recorded is rejected.
        """
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if not isinstance(executor_snapshot, Mapping) or not isinstance(
            model_snapshot, Mapping
        ):
            raise TypeError("execution snapshots must be objects")
        if device_id is not None and (
            not isinstance(device_id, str) or not 1 <= len(device_id) <= 128
        ):
            raise ValueError("device_id is invalid")
        if invocation_id is not None and (
            not isinstance(invocation_id, str) or not 1 <= len(invocation_id) <= 128
        ):
            raise ValueError("invocation_id is invalid")
        executor_json = canonical_json(
            dict(executor_snapshot), max_bytes=MAX_SNAPSHOT_JSON_BYTES,
        )
        model_json = canonical_json(
            dict(model_snapshot), max_bytes=MAX_SNAPSHOT_JSON_BYTES,
        )
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return LeaseMutationStatus.STALE_FENCE
            assert row is not None
            if row["unit_state"] != "running" or row["attempt_state"] != "running":
                return LeaseMutationStatus.INVALID_STATE
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            existing_executor = str(row["executor_snapshot_json"])
            existing_model = str(row["model_snapshot_json"])
            try:
                existing_mode = json.loads(existing_executor).get("mode")
            except (TypeError, ValueError, AttributeError) as exc:
                raise DurableStoreError("attempt executor snapshot is invalid") from exc
            if existing_mode != "dummy" and (
                existing_executor != executor_json or existing_model != model_json
            ):
                raise DurableStoreError("execution facts are already frozen")
            existing_device_id = row["attempt_device_id"]
            if (
                existing_device_id is not None
                and device_id is not None
                and str(existing_device_id) != device_id
            ):
                raise DurableStoreError("attempt device is already frozen")
            existing_invocation_id = row["attempt_invocation_id"]
            if (
                existing_invocation_id is not None
                and invocation_id is not None
                and str(existing_invocation_id) != invocation_id
            ):
                raise DurableStoreError("attempt invocation is already frozen")
            metrics = self._attempt_metrics(
                row,
                execution_contract_recorded=True,
                execution_contract_recorded_at=current_text,
            )
            updated = connection.execute(
                """
                UPDATE attempts
                SET executor_snapshot_json=?, model_snapshot_json=?,
                    device_id=COALESCE(device_id, ?),
                    invocation_id=COALESCE(invocation_id, ?), metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state='running' AND ended_at IS NULL
                """,
                (
                    executor_json, model_json, device_id, invocation_id, metrics,
                    lease.owner_user_id, lease.attempt_id, lease.unit_id,
                    lease.fence, lease.worker_id,
                ),
            )
            if updated.rowcount != 1:
                raise DurableStoreError("execution facts compare-and-set failed")
            return LeaseMutationStatus.APPLIED

    def record_attempt_usage(
        self,
        lease: Lease,
        usage: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> LeaseMutationStatus:
        """Store bounded, content-free LLM accounting without affecting work."""
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if not isinstance(usage, Mapping):
            raise TypeError("usage must be an object")
        usage_value = json.loads(canonical_json(
            dict(usage), max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
        ))
        current, _current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return LeaseMutationStatus.STALE_FENCE
            assert row is not None
            if row["unit_state"] != "running" or row["attempt_state"] != "running":
                return LeaseMutationStatus.INVALID_STATE
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            metrics = self._attempt_metrics(
                row,
                llm_usage=usage_value,
                usage_missing=bool(usage_value.get("usage_missing", True)),
            )
            updated = connection.execute(
                """
                UPDATE attempts SET metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state='running' AND ended_at IS NULL
                """,
                (
                    metrics, lease.owner_user_id, lease.attempt_id,
                    lease.unit_id, lease.fence, lease.worker_id,
                ),
            )
            if updated.rowcount != 1:
                raise DurableStoreError("attempt usage compare-and-set failed")
            return LeaseMutationStatus.APPLIED

    def materialize_ready_units(
        self,
        owner_user_id: str,
        workload_id: str,
    ) -> int:
        """Materialize admitted downstream units from committed parent results.

        The procedure is domain-neutral: cardinality and input lineage are
        taken solely from the frozen stage rows.  Repeating it after a crash is
        safe because every unit has a stable semantic key and a database unique
        constraint.
        """
        owner = _require_owner(owner_user_id)
        created = 0
        with self._transaction() as connection:
            workload = self._select_workload(connection, owner, workload_id)
            revision_id = workload["active_revision_id"]
            if revision_id is None or workload["state"] not in {
                WorkloadState.ADMITTED.value,
                WorkloadState.QUEUED.value,
                WorkloadState.RUNNING.value,
            }:
                return 0
            stages = connection.execute(
                """
                SELECT id, stage_key, position, stage_type, cardinality, max_units
                FROM stages
                WHERE owner_user_id=? AND revision_id=?
                ORDER BY position, stage_key
                """,
                (owner, revision_id),
            ).fetchall()
            by_id = {str(stage["id"]): stage for stage in stages}
            dependencies = {
                str(stage["id"]): tuple(
                    str(item["depends_on_stage_id"])
                    for item in connection.execute(
                        """
                        SELECT depends_on_stage_id FROM stage_dependencies
                        WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                        ORDER BY ordinal
                        """,
                        (owner, revision_id, stage["id"]),
                    ).fetchall()
                )
                for stage in stages
            }
            sources = tuple(connection.execute(
                """
                SELECT id, source_id, content_digest
                FROM sources
                WHERE owner_user_id=? AND revision_id=?
                ORDER BY ordinal, id
                """,
                (owner, revision_id),
            ).fetchall())
            result_rows = tuple(connection.execute(
                """
                SELECT result.id AS result_id, result.digest, unit.stage_id,
                       unit.source_row_id, unit.state AS unit_state
                FROM results result
                JOIN units unit
                  ON unit.owner_user_id=result.owner_user_id
                 AND unit.id=result.unit_id
                 AND unit.revision_id=result.revision_id
                WHERE result.owner_user_id=? AND result.revision_id=?
                ORDER BY unit.stage_id, unit.source_row_id, result.id
                """,
                (owner, revision_id),
            ).fetchall())
            results_by_stage: dict[str, list[sqlite3.Row]] = {}
            for result in result_rows:
                results_by_stage.setdefault(str(result["stage_id"]), []).append(result)
            terminal_by_stage = {
                str(stage["id"]): int(connection.execute(
                    """
                    SELECT COUNT(*) FROM units
                    WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                      AND state NOT IN ('committed', 'failed_permanent',
                                        'needs_attention', 'cancelled', 'skipped')
                    """,
                    (owner, revision_id, stage["id"]),
                ).fetchone()[0]) == 0
                for stage in stages
            }

            for stage in stages:
                stage_id = str(stage["id"])
                if stage["stage_type"] == "inventory":
                    continue
                parent_ids = tuple(
                    item for item in dependencies[stage_id]
                    if by_id[item]["stage_type"] != "inventory"
                )
                # Root units and units depending only on the sealed inventory
                # are materialized during admission.
                if not parent_ids:
                    continue
                mode = str(stage["cardinality"])
                candidates: list[tuple[sqlite3.Row | None, tuple[sqlite3.Row, ...], str | None]] = []
                if mode == "singleton":
                    if not all(terminal_by_stage[parent] for parent in parent_ids):
                        continue
                    parents = tuple(
                        result
                        for parent in parent_ids
                        for result in results_by_stage.get(parent, ())
                    )
                    parent_unit_count = sum(int(connection.execute(
                        """
                        SELECT COUNT(*) FROM units
                        WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                        """,
                        (owner, revision_id, parent),
                    ).fetchone()[0]) for parent in parent_ids)
                    if len(parents) != parent_unit_count:
                        continue
                    candidates.append((None, parents, None))
                elif mode == "per_source":
                    for source in sources:
                        parents: list[sqlite3.Row] = []
                        complete = True
                        for parent in parent_ids:
                            parent_stage = by_id[parent]
                            parent_results = results_by_stage.get(parent, ())
                            if parent_stage["cardinality"] == "singleton":
                                if not terminal_by_stage[parent] or len(parent_results) != 1:
                                    complete = False
                                    break
                                parents.extend(parent_results)
                            else:
                                matching = tuple(
                                    result for result in parent_results
                                    if result["source_row_id"] == source["id"]
                                )
                                if len(matching) != 1:
                                    complete = False
                                    break
                                parents.extend(matching)
                        if complete:
                            candidates.append((source, tuple(parents), None))
                else:  # per_dependency
                    for parent in parent_ids:
                        for result in results_by_stage.get(parent, ()):
                            source = next((item for item in sources if item["id"] == result["source_row_id"]), None)
                            candidates.append((source, (result,), f"result:{result['result_id']}"))

                existing_count = int(connection.execute(
                    """
                    SELECT COUNT(*) FROM units
                    WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                    """,
                    (owner, revision_id, stage_id),
                ).fetchone()[0])
                now = utc_now()
                for source, parents, shard_key in candidates:
                    semantic = {
                        "stage_key": str(stage["stage_key"]),
                        "source_digest": (
                            str(source["content_digest"]) if source is not None else None
                        ),
                        "source_id": (
                            str(source["source_id"]) if source is not None else None
                        ),
                        "dependencies": [
                            {"result_id": str(parent["result_id"]), "digest": str(parent["digest"])}
                            for parent in parents
                        ],
                        "shard_key": shard_key,
                    }
                    unit_key = digest_json(
                        "durable-unit-key", semantic, max_bytes=MAX_PLAN_JSON_BYTES,
                    )
                    exists = connection.execute(
                        """
                        SELECT 1 FROM units
                        WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                          AND unit_key=?
                        """,
                        (owner, revision_id, stage_id, unit_key),
                    ).fetchone()
                    if exists is not None:
                        continue
                    if existing_count >= int(stage["max_units"]):
                        raise DurableStoreError(
                            "materialized units exceed the frozen stage cardinality cap"
                        )
                    inserted = connection.execute(
                        """
                        INSERT INTO units(
                            owner_user_id, id, revision_id, stage_id, unit_key,
                            source_row_id, shard_key, state,
                            expected_dependency_count, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                        ON CONFLICT(owner_user_id, revision_id, stage_id, unit_key)
                        DO NOTHING
                        """,
                        (
                            owner, _new_id("unt"), revision_id, stage_id, unit_key,
                            None if source is None else source["id"], shard_key,
                            len(parents), now, now,
                        ),
                    )
                    if inserted.rowcount == 1:
                        created += 1
                        existing_count += 1
        return created

    def materialize_all_ready_units(self, *, limit: int = 200) -> int:
        """Run bounded, idempotent downstream materialization after recovery."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")
        rows = self._connection.execute(
            """
            SELECT owner_user_id, id FROM workloads
            WHERE state IN ('admitted', 'queued', 'running')
            ORDER BY updated_at, owner_user_id, id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return sum(
            self.materialize_ready_units(str(row["owner_user_id"]), str(row["id"]))
            for row in rows
        )

    def refresh_usage_complete(
        self,
        owner_user_id: str,
        workload_id: str,
    ) -> bool:
        """Materialize whether all model attempts have bounded usage facts."""
        owner = _require_owner(owner_user_id)
        with self._transaction() as connection:
            workload = self._select_workload(connection, owner, workload_id)
            revision_id = workload["active_revision_id"]
            if revision_id is None:
                return False
            missing = int(connection.execute(
                """
                SELECT COUNT(*) FROM attempts attempt
                JOIN units unit
                  ON unit.owner_user_id=attempt.owner_user_id
                 AND unit.id=attempt.unit_id
                WHERE attempt.owner_user_id=? AND unit.revision_id=?
                  AND json_extract(attempt.model_snapshot_json, '$.mode')='llm'
                  AND (
                    json_extract(attempt.metrics_json, '$.usage_missing') IS NOT 0
                    OR json_type(attempt.metrics_json, '$.llm_usage') IS NULL
                  )
                """,
                (owner, revision_id),
            ).fetchone()[0])
            complete = missing == 0
            connection.execute(
                """
                UPDATE revisions SET usage_complete=?
                WHERE owner_user_id=? AND id=? AND workload_id=?
                """,
                (int(complete), owner, revision_id, workload_id),
            )
            return complete

    def commit_result(
        self,
        lease: Lease,
        validated_result: ValidatedResult,
        *,
        dependency_result_ids: Sequence[str] = (),
        now: datetime | None = None,
    ) -> CommitOutcome:
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if not isinstance(validated_result, ValidatedResult):
            raise TypeError("validated_result must be ValidatedResult")
        if isinstance(dependency_result_ids, (str, bytes)) or not isinstance(
            dependency_result_ids, Sequence
        ):
            raise TypeError("dependency_result_ids must be a sequence")
        dependency_ids = tuple(dependency_result_ids)
        if any(
            not isinstance(value, str) or not _ID_RE.fullmatch(value)
            for value in dependency_ids
        ) or len(dependency_ids) != len(set(dependency_ids)):
            raise ResultContractError("result dependency identifiers are invalid")
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            existing = connection.execute(
                """
                SELECT id, attempt_id, fence, digest
                FROM results
                WHERE owner_user_id=? AND unit_id=?
                """,
                (lease.owner_user_id, lease.unit_id),
            ).fetchone()
            if existing is not None:
                same_attempt = (
                    existing["attempt_id"] == lease.attempt_id
                    and int(existing["fence"]) == lease.fence
                )
                if not same_attempt:
                    status = CommitStatus.STALE_FENCE
                elif existing["digest"] == validated_result.digest:
                    status = CommitStatus.IDEMPOTENT_REPLAY
                else:
                    status = CommitStatus.DIGEST_CONFLICT
                return CommitOutcome(
                    status=status,
                    result_id=str(existing["id"]),
                    winning_digest=str(existing["digest"]),
                    proposed_digest=validated_result.digest,
                )
            if not self._lease_matches(row, lease):
                return CommitOutcome(
                    CommitStatus.STALE_FENCE, None, None,
                    validated_result.digest,
                )
            assert row is not None
            if row["unit_state"] != "running" or row["attempt_state"] != "running":
                return CommitOutcome(
                    CommitStatus.INVALID_STATE, None, None,
                    validated_result.digest,
                )
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return CommitOutcome(
                    CommitStatus.LEASE_EXPIRED, None, None,
                    validated_result.digest,
                )
            output = json.loads(str(row["output_schema_json"]))
            if output.get("name") != validated_result.schema_version:
                raise ResultContractError(
                    "result schema does not match the frozen stage output schema"
                )
            expected_dependency_count = int(row["expected_dependency_count"])
            if len(dependency_ids) != expected_dependency_count:
                raise ResultContractError(
                    "result dependencies do not match the materialized unit"
                )
            dependency_rows: tuple[sqlite3.Row, ...] = ()
            if dependency_ids:
                placeholders = ",".join("?" for _ in dependency_ids)
                rows = connection.execute(
                    f"""
                    SELECT result.id AS result_id, parent.stage_key,
                           parent.position, source.ordinal AS source_ordinal
                    FROM results result
                    JOIN units parent_unit
                      ON parent_unit.owner_user_id=result.owner_user_id
                     AND parent_unit.id=result.unit_id
                     AND parent_unit.revision_id=result.revision_id
                    JOIN stages parent
                      ON parent.owner_user_id=parent_unit.owner_user_id
                     AND parent.id=parent_unit.stage_id
                     AND parent.revision_id=parent_unit.revision_id
                    JOIN stage_dependencies declared
                      ON declared.owner_user_id=parent.owner_user_id
                     AND declared.revision_id=parent.revision_id
                     AND declared.depends_on_stage_id=parent.id
                    LEFT JOIN sources source
                      ON source.owner_user_id=parent_unit.owner_user_id
                     AND source.id=parent_unit.source_row_id
                     AND source.revision_id=parent_unit.revision_id
                    WHERE result.owner_user_id=? AND result.revision_id=?
                      AND declared.stage_id=? AND result.id IN ({placeholders})
                    ORDER BY parent.position, source.ordinal, result.id
                    """,
                    (
                        lease.owner_user_id, lease.revision_id, lease.stage_id,
                        *dependency_ids,
                    ),
                ).fetchall()
                if len(rows) != len(dependency_ids):
                    raise ResultContractError(
                        "result dependencies are not committed direct parents"
                    )
                dependency_rows = tuple(rows)

            result_id = _new_id("res")
            executor_snapshot = json.loads(str(row["executor_snapshot_json"]))
            model_snapshot = json.loads(str(row["model_snapshot_json"]))
            metrics_value = json.loads(str(row["metrics_json"]))
            validation = (
                "dummy_contract"
                if executor_snapshot.get("mode") == "dummy"
                else "approved_output_schema"
            )
            provenance = canonical_json(
                {
                    "schema_version": "metnos.durable-result-provenance/1",
                    "attempt_id": lease.attempt_id,
                    "fence": lease.fence,
                    "runner_kind": lease.runner_kind.value,
                    "runner_name": lease.runner_name,
                    "validation": validation,
                    "output_schema": validated_result.schema_version,
                    "executor_snapshot_digest": digest_json(
                        "durable-executor-snapshot", executor_snapshot,
                        max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                    ),
                    "model_snapshot_digest": digest_json(
                        "durable-model-snapshot", model_snapshot,
                        max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                    ),
                    "metrics_digest": digest_json(
                        "durable-attempt-metrics", metrics_value,
                        max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
                    ),
                    "usage_missing": bool(metrics_value.get("usage_missing", False)),
                },
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            connection.execute(
                """
                INSERT INTO results(
                    owner_user_id, id, revision_id, unit_id, attempt_id,
                    fence, digest, schema_version, payload_json,
                    provenance_json, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease.owner_user_id, result_id, lease.revision_id,
                    lease.unit_id, lease.attempt_id, lease.fence,
                    validated_result.digest, validated_result.schema_version,
                    validated_result.payload_json, provenance, current_text,
                ),
            )
            role_ordinals: dict[str, int] = {}
            for dependency in dependency_rows:
                role = str(dependency["stage_key"])
                ordinal = role_ordinals.get(role, 0)
                connection.execute(
                    """
                    INSERT INTO dependencies(
                        owner_user_id, revision_id, child_result_id,
                        source_result_id, role, ordinal
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.owner_user_id, lease.revision_id, result_id,
                        dependency["result_id"], role, ordinal,
                    ),
                )
                role_ordinals[role] = ordinal + 1
            metrics = self._attempt_metrics(
                row,
                result_digest=validated_result.digest,
                committed=True,
                dependency_count=len(dependency_rows),
            )
            changed_attempt = connection.execute(
                """
                UPDATE attempts
                SET state='succeeded', ended_at=?, metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state='running' AND ended_at IS NULL
                """,
                (
                    current_text, metrics, lease.owner_user_id,
                    lease.attempt_id, lease.unit_id, lease.fence,
                    lease.worker_id,
                ),
            )
            changed_unit = connection.execute(
                """
                UPDATE units
                SET state='committed', committed_result_id=?,
                    lease_worker_id=NULL, active_attempt_id=NULL,
                    lease_expires_at=NULL, next_attempt_at=NULL,
                    error_class=NULL, partial_output=0,
                    terminal_detail_json=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='running'
                  AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                """,
                (
                    result_id, current_text, lease.owner_user_id,
                    lease.unit_id, lease.attempt_id, lease.fence,
                    lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("result commit compare-and-set failed")
            return CommitOutcome(
                CommitStatus.COMMITTED,
                result_id,
                validated_result.digest,
                validated_result.digest,
            )

    def fail_attempt(
        self,
        lease: Lease,
        structured_error: StructuredAttemptError,
        retry_decision: RetryDecision,
        *,
        now: datetime | None = None,
    ) -> FailureOutcome:
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if not isinstance(structured_error, StructuredAttemptError):
            raise TypeError("structured_error must be StructuredAttemptError")
        if not isinstance(retry_decision, RetryDecision):
            raise TypeError("retry_decision must be RetryDecision")
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return FailureOutcome(FailureStatus.STALE_FENCE)
            assert row is not None
            if row["unit_state"] != "running" or row["attempt_state"] != "running":
                return FailureOutcome(FailureStatus.INVALID_STATE)
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return FailureOutcome(FailureStatus.LEASE_EXPIRED)
            policy = RetryPolicy.from_mapping(json.loads(str(row["retry_json"])))
            derived = decide_retry(
                effect_profile=lease.effect_profile,
                retry_policy=policy,
                attempt_number=lease.attempt_number,
                error_class=structured_error.error_class,
            )
            if retry_decision is not derived:
                raise RetryDecisionConflictError(
                    "retry decision does not match the frozen stage policy"
                )

            next_attempt_at: str | None = None
            if derived is RetryDecision.RETRY:
                delay = deterministic_retry_delay_ms(
                    lease.unit_key,
                    lease.attempt_number,
                    base_delay_ms=policy.base_delay_ms,
                    max_delay_ms=policy.max_delay_ms,
                )
                next_attempt_at = instant_text(
                    current + timedelta(milliseconds=delay),
                    name="next_attempt_at",
                )
                unit_state = "retry_wait"
                status = FailureStatus.RETRY_SCHEDULED
            elif derived is RetryDecision.NEEDS_ATTENTION:
                unit_state = "needs_attention"
                status = FailureStatus.NEEDS_ATTENTION
            else:
                unit_state = "failed_permanent"
                status = FailureStatus.FAILED_PERMANENT

            metrics = self._attempt_metrics(
                row,
                failed=True,
                error_class=structured_error.error_class,
                retry_decision=derived.value,
            )
            changed_attempt = connection.execute(
                """
                UPDATE attempts
                SET state='failed', ended_at=?, structured_error_json=?,
                    metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state='running' AND ended_at IS NULL
                """,
                (
                    current_text, structured_error.payload_json, metrics,
                    lease.owner_user_id, lease.attempt_id, lease.unit_id,
                    lease.fence, lease.worker_id,
                ),
            )
            changed_unit = connection.execute(
                """
                UPDATE units
                SET state=?, next_attempt_at=?, lease_worker_id=NULL,
                    active_attempt_id=NULL, lease_expires_at=NULL,
                    error_class=?, terminal_detail_json=?, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='running'
                  AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                """,
                (
                    unit_state, next_attempt_at, structured_error.error_class,
                    structured_error.payload_json, current_text,
                    lease.owner_user_id, lease.unit_id, lease.attempt_id,
                    lease.fence, lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("attempt failure compare-and-set failed")
            return FailureOutcome(status, next_attempt_at)

    def abandon_attempt(
        self,
        lease: Lease,
        *,
        now: datetime | None = None,
        reason_code: str = "worker_shutdown",
    ) -> LeaseMutationStatus:
        """Relinquish a F3 pure lease without relying on an in-memory lock."""
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if reason_code not in {"worker_shutdown", "execution_not_started"}:
            raise ValueError("reason_code is not a closed F3 abandonment reason")
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if row is not None and (
                int(row["attempt_fence"] or 0) == lease.fence
                and row["attempt_state"] == "abandoned"
            ):
                return LeaseMutationStatus.ALREADY_APPLIED
            if not self._lease_matches(row, lease):
                return LeaseMutationStatus.STALE_FENCE
            assert row is not None
            if row["unit_state"] not in {"leased", "running"}:
                return LeaseMutationStatus.INVALID_STATE
            if lease.effect_profile.value != "pure":
                return LeaseMutationStatus.INVALID_STATE
            retry_available = (
                lease.attempt_number < lease.retry_policy.max_attempts
            )
            if not retry_available:
                target_state = "needs_attention"
                next_attempt_at = None
            elif row["unit_state"] == "leased":
                target_state = "pending"
                next_attempt_at = None
            else:
                target_state = "retry_wait"
                next_attempt_at = current_text
            error = StructuredAttemptError.create(
                "cancelled",
                code=f"attempt.{reason_code}",
                message_key="DURABLE_ATTEMPT_ABANDONED",
                retry="automatic" if retry_available else "manual",
                occurred_at=current,
                details_redacted={
                    "retry_safe": True,
                    "retry_budget_available": retry_available,
                },
            )
            metrics = self._attempt_metrics(
                row,
                abandoned=True,
                abandonment_reason=reason_code,
            )
            unit_error = None if retry_available else "cancelled"
            unit_detail = None if retry_available else error.payload_json
            changed_attempt = connection.execute(
                """
                UPDATE attempts
                SET state='abandoned', ended_at=?, structured_error_json=?,
                    metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state IN ('leased', 'running')
                  AND ended_at IS NULL
                """,
                (
                    current_text, error.payload_json, metrics,
                    lease.owner_user_id, lease.attempt_id, lease.unit_id,
                    lease.fence, lease.worker_id,
                ),
            )
            changed_unit = connection.execute(
                """
                UPDATE units
                SET state=?, next_attempt_at=?,
                    lease_worker_id=NULL, active_attempt_id=NULL,
                    lease_expires_at=NULL, error_class=?,
                    partial_output=0, terminal_detail_json=?, updated_at=?
                WHERE owner_user_id=? AND id=?
                  AND state IN ('leased', 'running')
                  AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                """,
                (
                    target_state, next_attempt_at, unit_error, unit_detail,
                    current_text,
                    lease.owner_user_id, lease.unit_id,
                    lease.attempt_id, lease.fence, lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("attempt abandonment compare-and-set failed")
            return LeaseMutationStatus.APPLIED

    def reconcile_expired(
        self,
        now: datetime,
        batch_size: int,
    ) -> ReconcileOutcome:
        current = normalize_instant(now, name="now")
        current_text = instant_text(current, name="now")
        if (
            isinstance(batch_size, bool) or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 1000
        ):
            raise ValueError("batch_size must be an integer in 1..1000")
        expired = returned = retrying = permanent = attention = 0
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT
                    u.owner_user_id, u.id AS unit_id, u.unit_key,
                    u.state AS unit_state, u.attempt_count,
                    u.active_attempt_id, u.fence,
                    u.lease_worker_id, u.lease_expires_at,
                    s.effect_profile, s.retry_json,
                    a.state AS attempt_state, a.metrics_json
                FROM units u
                JOIN stages s
                  ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
                 AND s.revision_id=u.revision_id
                JOIN attempts a
                  ON a.owner_user_id=u.owner_user_id
                 AND a.id=u.active_attempt_id AND a.unit_id=u.id
                 AND a.fence=u.fence
                WHERE u.state IN ('leased', 'running')
                  AND u.lease_expires_at<=?
                ORDER BY u.lease_expires_at, u.owner_user_id, u.id
                LIMIT ?
                """,
                (current_text, batch_size),
            ).fetchall()
            for row in rows:
                expired += 1
                attempt_number = int(row["attempt_count"])
                policy = RetryPolicy.from_mapping(
                    json.loads(str(row["retry_json"]))
                )
                before_execution = row["unit_state"] == "leased"
                error_code = (
                    "lease.expired_before_execution"
                    if before_execution else "lease.expired_during_execution"
                )
                retry_mode = "automatic"
                if not before_execution and row["effect_profile"] == "reconcilable":
                    retry_mode = "reconcile"
                elif not before_execution and row["effect_profile"] == "manual_only":
                    retry_mode = "manual"
                elif attempt_number >= policy.max_attempts:
                    retry_mode = "manual" if before_execution else "never"
                error = StructuredAttemptError.create(
                    "lease_lost",
                    code=error_code,
                    message_key="DURABLE_LEASE_EXPIRED",
                    retry=retry_mode,
                    occurred_at=current,
                    details_redacted={
                        "execution_started": not before_execution,
                        "attempt_number": attempt_number,
                        "max_attempts": policy.max_attempts,
                    },
                )
                metrics_value = json.loads(str(row["metrics_json"]))
                metrics_value.update({
                    "lease_expired": True,
                    "reconciled_at": current_text,
                })
                metrics = canonical_json(
                    metrics_value,
                    max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
                )
                attempt_terminal = "abandoned" if before_execution else "timed_out"
                changed_attempt = connection.execute(
                    """
                    UPDATE attempts
                    SET state=?, ended_at=?, structured_error_json=?, metrics_json=?
                    WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                      AND worker_id=? AND state=? AND ended_at IS NULL
                    """,
                    (
                        attempt_terminal, current_text, error.payload_json, metrics,
                        row["owner_user_id"], row["active_attempt_id"],
                        row["unit_id"], row["fence"], row["lease_worker_id"],
                        row["attempt_state"],
                    ),
                )
                if before_execution:
                    next_attempt_at = None
                    if attempt_number < policy.max_attempts:
                        target = "pending"
                        returned += 1
                    else:
                        target = "needs_attention"
                        attention += 1
                else:
                    effect = row["effect_profile"]
                    if effect in {"reconcilable", "manual_only"}:
                        target = "needs_attention"
                        next_attempt_at = None
                        attention += 1
                    elif attempt_number < policy.max_attempts:
                        delay = deterministic_retry_delay_ms(
                            str(row["unit_key"]),
                            attempt_number,
                            base_delay_ms=policy.base_delay_ms,
                            max_delay_ms=policy.max_delay_ms,
                        )
                        next_attempt_at = instant_text(
                            parse_instant(str(row["lease_expires_at"]))
                            + timedelta(milliseconds=delay),
                            name="next_attempt_at",
                        )
                        target = "retry_wait"
                        retrying += 1
                    else:
                        target = "failed_permanent"
                        next_attempt_at = None
                        permanent += 1
                changed_unit = connection.execute(
                    """
                    UPDATE units
                    SET state=?, next_attempt_at=?, lease_worker_id=NULL,
                        active_attempt_id=NULL, lease_expires_at=NULL,
                        error_class=?, terminal_detail_json=?, updated_at=?
                    WHERE owner_user_id=? AND id=? AND state=?
                      AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                    """,
                    (
                        target, next_attempt_at, "lease_lost",
                        error.payload_json, current_text, row["owner_user_id"],
                        row["unit_id"], row["unit_state"],
                        row["active_attempt_id"], row["fence"],
                        row["lease_worker_id"],
                    ),
                )
                if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                    raise DurableStoreError("expired lease compare-and-set failed")
            promoted = self._promote_due_retries_in_transaction(
                connection,
                current_text,
                limit=max(0, batch_size - len(rows)),
            )
        return ReconcileOutcome(
            expired=expired,
            returned_pending=returned,
            retry_scheduled=retrying,
            failed_permanent=permanent,
            needs_attention=attention,
            retry_promoted=promoted,
        )

    def purge_owner(self, owner_user_id: str) -> int:
        """Delete only one owner's durable rows; repeating it returns zero."""
        owner = _require_owner(owner_user_id)
        with self._transaction() as connection:
            count = int(connection.execute(
                "SELECT COUNT(*) FROM workloads WHERE owner_user_id=?",
                (owner,),
            ).fetchone()[0])
            connection.execute(
                "DELETE FROM workloads WHERE owner_user_id=?", (owner,)
            )
            for table in (
                "workloads", "revisions", "stages", "stage_dependencies",
                "sources", "units", "attempts", "results", "dependencies",
                "artifacts", "publications", "events", "outbox",
                "scheduler_credits", "commands", "attention_resolutions",
            ):
                residue = int(connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE owner_user_id=?",
                    (owner,),
                ).fetchone()[0])
                if residue:
                    raise DurableStoreError(
                        f"owner purge left {residue} rows in {table}"
                    )
            return count


__all__ = [
    "DurableStoreError",
    "DurableWorkloadStore",
    "IdentifierConflictError",
    "IdempotencyConflictError",
    "InvalidTransitionError",
    "OwnerRequiredError",
    "ResultContractError",
    "ReservedCompletionTransitionError",
    "RetryDecisionConflictError",
    "RevisionNotFoundError",
    "StoreNotReadyError",
    "VersionConflictError",
    "WorkloadNotFoundError",
]
