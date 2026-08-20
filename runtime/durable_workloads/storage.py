"""Owner-scoped transactional repository and explicit state machines.

This module persists states but never executes a unit, calls an LLM, touches a
provider or publishes a blob.  Lease/fence execution APIs begin in F3.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .migrations import (
    CURRENT_SCHEMA_VERSION,
    migrate,
    open_db,
    schema_version,
    utc_now,
)
from .models import (
    CompletionAssessment,
    EventRecord,
    EventType,
    RevisionRecord,
    TERMINAL_UNIT_STATES,
    UnitCounters,
    UnitState,
    WorkloadRecord,
    WorkloadState,
    can_transition_workload,
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
            self.append_event_in_transaction(
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
            self.append_event_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_type=event_type,
                payload=merged_payload,
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
            target: WorkloadState | None
            event_type: EventType | None
            if command == "pause":
                if source in {WorkloadState.PAUSED, WorkloadState.PAUSE_REQUESTED}:
                    target, event_type = None, None
                elif source is WorkloadState.RUNNING:
                    target, event_type = WorkloadState.PAUSE_REQUESTED, EventType.PAUSE_REQUESTED
                elif source is WorkloadState.QUEUED:
                    target, event_type = WorkloadState.PAUSED, EventType.PAUSED
                else:
                    raise InvalidTransitionError(f"cannot pause {source.value}")
            elif command == "resume":
                if source is WorkloadState.QUEUED:
                    target, event_type = None, None
                elif source is WorkloadState.PAUSED:
                    target, event_type = WorkloadState.QUEUED, EventType.RESUMED
                else:
                    raise InvalidTransitionError(f"cannot resume {source.value}")
            elif command == "cancel":
                if source in {WorkloadState.CANCELLED, WorkloadState.CANCEL_REQUESTED}:
                    target, event_type = None, None
                elif source in {WorkloadState.RUNNING, WorkloadState.PAUSE_REQUESTED}:
                    target, event_type = WorkloadState.CANCEL_REQUESTED, EventType.CANCEL_REQUESTED
                elif source in {
                    WorkloadState.DRAFT,
                    WorkloadState.ADMITTED,
                    WorkloadState.QUEUED,
                    WorkloadState.PAUSED,
                    WorkloadState.NEEDS_ATTENTION,
                }:
                    target, event_type = WorkloadState.CANCELLED, EventType.CANCELLED
                else:
                    raise InvalidTransitionError(f"cannot cancel {source.value}")
            else:
                raise ValueError(f"unknown control command: {command}")

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
            now = utc_now()
            connection.execute(
                """
                INSERT INTO outbox(
                    owner_user_id, id, workload_id, event_id, channel,
                    recipient_key, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'owner_event', ?, 'pending', ?, ?)
                """,
                (
                    owner, _new_id("obx"), workload_id,
                    terminal_event.event_id, owner, now, now,
                ),
            )
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
    "ReservedCompletionTransitionError",
    "RevisionNotFoundError",
    "StoreNotReadyError",
    "VersionConflictError",
    "WorkloadNotFoundError",
]
