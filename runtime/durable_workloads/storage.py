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
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    AttemptState,
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
from .transactions import checked_checkpoint, immediate_transaction


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
_OUTBOX_CANCEL_REASONS = frozenset({
    "delivery_ambiguous",
    "event_not_supported",
    "provider_rejected",
    "recipient_unavailable",
    "retry_exhausted",
})
_MAX_OUTBOX_LEASE_SECONDS = 300
_MAX_UNIT_DEPENDENCIES = 1024
_MAX_DEPENDENCY_INPUT_BYTES = 16_777_216
_UNIT_STATE_BATCH_SIZE = 256
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
_MAX_LLM_USAGE_RECORDS = 256
_MAX_LLM_CALL_COUNTER = 10**12
_CLOCK_REGRESSION_TOLERANCE = timedelta(seconds=5)
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_MODEL_USAGE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_JSON_DECODER = json.JSONDecoder()
_TERMINAL_WORKLOAD_STATES = frozenset({
    WorkloadState.CANCELLED,
    WorkloadState.FAILED,
    WorkloadState.COMPLETED_WITH_ERRORS,
    WorkloadState.COMPLETED,
})


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


class ModelUsageContractError(DurableStoreError):
    """Measured model usage conflicts with the frozen execution contract."""


class RetryDecisionConflictError(DurableStoreError):
    """A caller tried to override the retry decision derived from the plan."""


class BudgetExceededError(DurableStoreError):
    """Measured work cannot continue within the frozen revision budget."""

    def __init__(self, reason: Mapping[str, Any]) -> None:
        self.reason = dict(reason)
        super().__init__(str(self.reason.get("reason_code", "budget_exhausted")))


def _validated_attempt_usage(
    value: Mapping[str, Any],
    lease: Lease,
) -> dict[str, Any]:
    """Validate identities and recompute every aggregate from bounded calls."""

    required = {
        "schema_version", "records", "dropped", "input_tokens",
        "output_tokens", "cost_micros", "usage_missing", "cost_unknown",
        "zero_calls_verified",
    }
    records = value.get("records")
    counters = ("dropped", "input_tokens", "output_tokens", "cost_micros")
    if (
        set(value) != required
        or value.get("schema_version") != "metnos.durable-model-usage/2"
        or not isinstance(records, list)
        or len(records) > _MAX_LLM_USAGE_RECORDS
        or any(
            isinstance(value.get(name), bool)
            or not isinstance(value.get(name), int)
            or not 0 <= int(value[name]) <= _SQLITE_INTEGER_MAX
            for name in counters
        )
        or not isinstance(value.get("usage_missing"), bool)
        or not isinstance(value.get("cost_unknown"), bool)
        or not isinstance(value.get("zero_calls_verified"), bool)
    ):
        raise ModelUsageContractError("attempt usage summary is invalid")

    record_keys = {
        "schema_version", "workload_id", "stage_id", "unit_key",
        "attempt_id", "provider", "model_digest", "tier", "kind",
        "in_tokens", "out_tokens", "latency_ms", "cost_micros",
    }
    expected_identity = {
        "workload_id": lease.workload_id,
        "stage_id": lease.stage_id,
        "unit_key": lease.unit_key,
        "attempt_id": lease.attempt_id,
    }

    def optional_counter(item: Mapping[str, Any], name: str) -> bool:
        counter = item.get(name)
        return counter is None or (
            isinstance(counter, int)
            and not isinstance(counter, bool)
            and 0 <= counter <= _MAX_LLM_CALL_COUNTER
        )

    for item in records:
        if (
            not isinstance(item, Mapping)
            or set(item) != record_keys
            or item.get("schema_version") != "metnos.durable-llm-call/1"
            or any(item.get(name) != expected for name, expected in expected_identity.items())
            or not isinstance(item.get("provider"), str)
            or not 1 <= len(item["provider"]) <= 64
            or _MODEL_USAGE_LABEL_RE.fullmatch(item["provider"]) is None
            or not isinstance(item.get("model_digest"), str)
            or _SHA256_RE.fullmatch(item["model_digest"]) is None
            or not isinstance(item.get("tier"), str)
            or len(item["tier"]) > 32
            or (
                item["tier"] != ""
                and _MODEL_USAGE_LABEL_RE.fullmatch(item["tier"]) is None
            )
            or not isinstance(item.get("kind"), str)
            or not 1 <= len(item["kind"]) <= 32
            or _MODEL_USAGE_LABEL_RE.fullmatch(item["kind"]) is None
            or any(
                not optional_counter(item, name)
                for name in ("in_tokens", "out_tokens", "latency_ms", "cost_micros")
            )
        ):
            raise ModelUsageContractError("attempt usage record is invalid")

    expected_input = sum(
        int(item["in_tokens"])
        for item in records
        if item["in_tokens"] is not None
    )
    expected_output = sum(
        int(item["out_tokens"])
        for item in records
        if item["out_tokens"] is not None
    )
    expected_cost = sum(
        int(item["cost_micros"])
        for item in records
        if item["cost_micros"] is not None
    )
    expected_missing = (
        (not records and not value["zero_calls_verified"])
        or int(value["dropped"]) > 0
        or any(
            item["in_tokens"] is None or item["out_tokens"] is None
            for item in records
        )
    )
    expected_cost_unknown = any(
        item["cost_micros"] is None for item in records
    )
    if (
        int(value["input_tokens"]) != expected_input
        or int(value["output_tokens"]) != expected_output
        or int(value["cost_micros"]) != expected_cost
        or value["usage_missing"] is not expected_missing
        or value["cost_unknown"] is not expected_cost_unknown
        or (
            value["zero_calls_verified"]
            and (records or int(value["dropped"]) != 0)
        )
    ):
        raise ModelUsageContractError("attempt usage aggregates are inconsistent")
    return dict(value)


def _validate_usage_against_model_snapshot(
    usage: Mapping[str, Any],
    model_snapshot: Mapping[str, Any],
) -> None:
    """Bind measured calls to the exact model contract frozen at admission."""

    expected_fields = {
        "schema_version", "mode", "runner_name", "binding_digest",
        "prompt_digest", "prompt_language", "provider", "model_digest",
        "tier", "kind", "max_calls", "max_input_tokens",
        "max_output_tokens", "cost_policy",
    }
    if (
        set(model_snapshot) != expected_fields
        or model_snapshot.get("schema_version")
        != "metnos.durable-model-snapshot/2"
        or model_snapshot.get("mode") != "llm"
    ):
        raise ModelUsageContractError("frozen model usage contract is invalid")
    max_calls = model_snapshot.get("max_calls")
    max_input_tokens = model_snapshot.get("max_input_tokens")
    max_output_tokens = model_snapshot.get("max_output_tokens")
    if (
        isinstance(max_calls, bool)
        or not isinstance(max_calls, int)
        or not 1 <= max_calls <= 64
        or isinstance(max_input_tokens, bool)
        or not isinstance(max_input_tokens, int)
        or not 1 <= max_input_tokens <= 16_777_216
        or isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= 1_000_000
        or model_snapshot.get("cost_policy") != "zero"
    ):
        raise ModelUsageContractError("frozen model usage limits are invalid")
    records = usage["records"]
    observed_calls = len(records) + int(usage["dropped"])
    if observed_calls > max_calls:
        raise ModelUsageContractError("attempt exceeded its frozen model call limit")
    expected = {
        "provider": model_snapshot.get("provider"),
        "model_digest": model_snapshot.get("model_digest"),
        "tier": model_snapshot.get("tier"),
        "kind": model_snapshot.get("kind"),
    }
    for record in records:
        if any(record.get(name) != value for name, value in expected.items()):
            raise ModelUsageContractError(
                "observed model binding differs from the frozen contract"
            )
        input_tokens = record.get("in_tokens")
        if input_tokens is not None and int(input_tokens) > max_input_tokens:
            raise ModelUsageContractError(
                "model input exceeded its frozen token limit"
            )
        output_tokens = record.get("out_tokens")
        if output_tokens is not None and int(output_tokens) > max_output_tokens:
            raise ModelUsageContractError(
                "model output exceeded its frozen token limit"
            )
        if record.get("cost_micros") != 0:
            raise ModelUsageContractError(
                "zero-cost model binding reported an unknown or nonzero cost"
            )


def _json_space_end(value: str, offset: int) -> int:
    while offset < len(value) and value[offset].isspace():
        offset += 1
    return offset


def _json_value_end(value: str, offset: int) -> int:
    """Skip one already-validated JSON value without materializing it."""

    offset = _json_space_end(value, offset)
    if offset >= len(value):
        raise DurableStoreError("committed dependency payload ended unexpectedly")
    first = value[offset]
    if first == '"':
        escaped = False
        for index in range(offset + 1, len(value)):
            character = value[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                return index + 1
        raise DurableStoreError("committed dependency string is unterminated")
    if first in "[{":
        closers = ["]" if first == "[" else "}"]
        index = offset + 1
        in_string = False
        escaped = False
        while index < len(value):
            character = value[index]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
            elif character == '"':
                in_string = True
            elif character == "[":
                closers.append("]")
            elif character == "{":
                closers.append("}")
            elif character in "]}":
                if not closers or character != closers.pop():
                    raise DurableStoreError(
                        "committed dependency payload has invalid nesting"
                    )
                if not closers:
                    return index + 1
            index += 1
        raise DurableStoreError("committed dependency payload is unterminated")
    index = offset
    while index < len(value) and value[index] not in ",]} \t\r\n":
        index += 1
    if index == offset:
        raise DurableStoreError("committed dependency payload has an empty value")
    return index


def _entry_array_offset(payload_json: str) -> int:
    """Locate the first item of a top-level ``entries`` array in bounded space."""

    offset = _json_space_end(payload_json, 0)
    if offset >= len(payload_json) or payload_json[offset] != "{":
        raise DurableStoreError("committed dependency payload is not an object")
    offset = _json_space_end(payload_json, offset + 1)
    while offset < len(payload_json) and payload_json[offset] != "}":
        try:
            key, offset = _JSON_DECODER.raw_decode(payload_json, offset)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DurableStoreError(
                "committed dependency payload has an invalid object key"
            ) from exc
        if not isinstance(key, str):
            raise DurableStoreError(
                "committed dependency payload has a non-string object key"
            )
        offset = _json_space_end(payload_json, offset)
        if offset >= len(payload_json) or payload_json[offset] != ":":
            raise DurableStoreError(
                "committed dependency payload has an invalid object member"
            )
        offset = _json_space_end(payload_json, offset + 1)
        if key == "entries":
            if offset >= len(payload_json) or payload_json[offset] != "[":
                raise DurableStoreError(
                    "entry identity fan-out requires dependency entries"
                )
            return _json_space_end(payload_json, offset + 1)
        offset = _json_space_end(
            payload_json,
            _json_value_end(payload_json, offset),
        )
        if offset < len(payload_json) and payload_json[offset] == ",":
            offset = _json_space_end(payload_json, offset + 1)
        elif offset < len(payload_json) and payload_json[offset] == "}":
            break
        else:
            raise DurableStoreError(
                "committed dependency payload has an invalid object separator"
            )
    raise DurableStoreError("entry identity fan-out requires dependency entries")


def _next_entry(
    payload_json: str,
    offset: int,
) -> tuple[Mapping[str, Any] | None, int, bool, int, int]:
    """Decode one array item and return its next byte-independent character cursor."""

    offset = _json_space_end(payload_json, offset)
    if offset >= len(payload_json):
        raise DurableStoreError("dependency entries ended unexpectedly")
    if payload_json[offset] == "]":
        return None, offset + 1, True, offset, offset
    item_start = offset
    try:
        entry, end = _JSON_DECODER.raw_decode(payload_json, offset)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DurableStoreError("dependency entry is not valid JSON") from exc
    if not isinstance(entry, Mapping):
        raise DurableStoreError("entry identity fan-out found a non-object entry")
    end = _json_space_end(payload_json, end)
    if end >= len(payload_json):
        raise DurableStoreError("dependency entries ended unexpectedly")
    if payload_json[end] == ",":
        return (
            entry,
            _json_space_end(payload_json, end + 1),
            False,
            item_start,
            end,
        )
    if payload_json[end] == "]":
        return entry, end + 1, True, item_start, end
    raise DurableStoreError("dependency entries have an invalid separator")


def _require_owner(owner_user_id: str) -> str:
    if (
        not isinstance(owner_user_id, str)
        or not owner_user_id
        or len(owner_user_id) > 160
        or owner_user_id != owner_user_id.strip()
        or "\x00" in owner_user_id
    ):
        raise OwnerRequiredError(
            "owner_user_id must be a canonical string of 1..160 characters"
        )
    return owner_user_id


def _require_key(value: str, *, name: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not (1 <= len(value) <= maximum)
        or "\x00" in value
    ):
        raise ValueError(f"{name} must be a string of 1..{maximum} characters")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


def _require_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected_version must be a positive integer")
    return value


def _require_limit(value: int, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"limit must be an integer in 1..{maximum}")
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

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        if schema_version(connection) != CURRENT_SCHEMA_VERSION:
            raise StoreNotReadyError(
                "durable workload schema is not at the supported version"
            )
        connection.row_factory = sqlite3.Row
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StoreNotReadyError("foreign_keys must be active on every connection")
        self._connection = connection
        database_rows = connection.execute("PRAGMA database_list").fetchall()
        main_file = next(
            (str(row[2]) for row in database_rows if str(row[1]) == "main"),
            "",
        )
        self._database_path = (
            Path(main_file).resolve()
            if main_file not in {"", ":memory:"}
            else None
        )
        self._checkpoint = checked_checkpoint(checkpoint)

    @classmethod
    def open(
        cls,
        path: str | Path | None = None,
        *,
        checkpoint: Callable[[str], None] | None = None,
    ) -> "DurableWorkloadStore":
        connection = open_db(path)
        try:
            migrate(connection)
            return cls(connection, checkpoint=checkpoint)
        except BaseException:
            connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    @property
    def database_path(self) -> Path | None:
        """Return the file backing this store, or ``None`` for private memory."""

        return self._database_path

    def open_peer(self) -> "DurableWorkloadStore":
        """Open the same ready database on an independently owned connection."""

        if self._database_path is None:
            raise StoreNotReadyError(
                "an in-memory durable store cannot open a peer connection"
            )
        connection = open_db(self._database_path)
        try:
            return type(self)(connection, checkpoint=self._checkpoint)
        except BaseException:
            connection.close()
            raise

    def __enter__(self) -> "DurableWorkloadStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._connection.in_transaction:
            raise DurableStoreError("nested durable-workload transactions are forbidden")
        with immediate_transaction(
            self._connection,
            self._checkpoint,
            name="workload_transaction",
        ) as connection:
            yield connection

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

    def source_authority_active(
        self,
        owner_user_id: str,
        workload_id: str,
    ) -> bool:
        """Return whether a source mandate may still be needed by the LRE."""

        owner = _require_owner(owner_user_id)
        selected = _chosen_id(workload_id, prefix="wrk")
        row = self._connection.execute(
            """
            SELECT state FROM workloads
            WHERE owner_user_id=? AND id=?
            """,
            (owner, selected),
        ).fetchone()
        return (
            row is not None
            and WorkloadState(str(row["state"])) not in _TERMINAL_WORKLOAD_STATES
        )

    def list_workloads(
        self,
        owner_user_id: str,
        *,
        state: WorkloadState | str | None = None,
        limit: int = 100,
    ) -> tuple[WorkloadRecord, ...]:
        owner = _require_owner(owner_user_id)
        _require_limit(limit, maximum=200)
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
        _require_limit(limit, maximum=200)
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
        inventory_bytes = sum(int(item["size_bytes"]) for item in sources)
        if inventory_bytes > int(inventory_contract["max_total_bytes"]):
            raise SchemaValidationError(
                "sealed inventory exceeds plan.inventory.max_total_bytes"
            )
        if inventory_json is None and any(
            reference.get("ref") == "revision.inventory"
            for stage in plan["stages"]
            if stage["type"] != "inventory"
            for reference in stage["input_bindings"].values()
        ):
            raise SchemaValidationError(
                "revision.inventory requires a bounded inline inventory; "
                "large inventories must use per-source bindings"
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
        raw_policy_rules = policy.get("rules", [])
        if (
            isinstance(raw_policy_rules, (str, bytes))
            or not isinstance(raw_policy_rules, Sequence)
            or len(raw_policy_rules) > 64
        ):
            raise SchemaValidationError("policy snapshot rules must be bounded")
        invalidation_by_stage: dict[str, str] = {}
        for rule in raw_policy_rules:
            if not isinstance(rule, Mapping):
                raise SchemaValidationError("policy snapshot rule must be an object")
            stage_key = rule.get("stage_key")
            invalidation_digest = rule.get("invalidation_digest")
            if invalidation_digest is None:
                continue
            if (
                not isinstance(stage_key, str)
                or not isinstance(invalidation_digest, str)
                or not _SHA256_RE.fullmatch(invalidation_digest)
                or stage_key in invalidation_by_stage
            ):
                raise SchemaValidationError(
                    "policy snapshot has invalid stage invalidation facts"
                )
            invalidation_by_stage[stage_key] = invalidation_digest
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
                    ?, ?, ?, ?, ?, ?, ?, ?, NULL
                )
                """,
                (
                    owner, chosen_revision, workload_id, number,
                    plan_json, digest, catalog_json, policy_json,
                    inventory_json, inventory_hash, len(sources),
                    int(caps_truncated), int(partial_output_accepted),
                    int(usage_complete), error_policy["mode"], tolerated_json,
                    artifacts_json, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO revision_usage(
                    owner_user_id, revision_id, updated_at
                ) VALUES (?, ?, ?)
                """,
                (owner, chosen_revision, now),
            )

            stage_ids: dict[str, str] = {}
            stage_unit_counts: dict[str, int] = {}
            stage_invalidation_digests: dict[str, str] = {}
            for position, stage in enumerate(plan["stages"]):
                stage_id = _new_id("stg")
                stage_key = str(stage["key"])
                stage_ids[stage_key] = stage_id
                stage_unit_counts[stage_key] = 0
                invalidation_digest = invalidation_by_stage.get(stage_key)
                if invalidation_digest is None:
                    invalidation_digest = digest_json(
                        "durable-stage-invalidation",
                        {"stage": stage},
                        max_bytes=MAX_PLAN_JSON_BYTES,
                    )
                stage_invalidation_digests[stage_key] = invalidation_digest
                cardinality_contract = stage["cardinality"]
                connection.execute(
                    """
                    INSERT INTO stages(
                        owner_user_id, id, revision_id, stage_key, position,
                        stage_type, runner_kind, runner_name, effect_profile,
                        cardinality, max_units, input_bindings_json,
                        output_schema_json, retry_json, timeout_s,
                        invalidation_json, resources_json, required_flag,
                        invalidation_digest, reduction_fan_in,
                        reduction_input, reduction_max_input_bytes, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?
                    )
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
                        int(stage["required"]), invalidation_digest,
                        cardinality_contract.get("fan_in"),
                        cardinality_contract.get("reduction_input"),
                        cardinality_contract.get("max_input_bytes"), now,
                    ),
                )
                placement = stage.get("placement")
                if placement is not None:
                    connection.execute(
                        """
                        INSERT INTO stage_placements(
                            owner_user_id, revision_id, stage_id,
                            target_kind, target_device
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            owner,
                            chosen_revision,
                            stage_id,
                            placement["target"],
                            placement.get("device"),
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

            inventory_stage_key = next(
                str(stage["key"])
                for stage in plan["stages"]
                if stage["type"] == "inventory"
            )
            initial_stages = tuple(
                stage for stage in plan["stages"]
                if stage["type"] != "inventory"
                and not (set(stage["depends_on"]) - {inventory_stage_key})
            )

            def insert_initial_unit(
                stage: Mapping[str, Any],
                source: Mapping[str, Any] | None,
                source_row_id: str | None,
            ) -> None:
                stage_key = str(stage["key"])
                semantic = {
                    "stage_key": stage_key,
                    "stage_invalidation_digest":
                        stage_invalidation_digests[stage_key],
                    "source_digest": (
                        source["content_digest"] if source is not None else None
                    ),
                    "source_id": (
                        source["source_id"] if source is not None else None
                    ),
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
                        stage_ids[stage_key], unit_key, source_row_id, now, now,
                    ),
                )
                stage_unit_counts[stage_key] += 1

            for source in sources:
                source_row_id = _new_id("src")
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
                for stage in initial_stages:
                    if stage["cardinality"]["mode"] == "per_source":
                        insert_initial_unit(stage, source, source_row_id)

            for stage in initial_stages:
                if stage["cardinality"]["mode"] == "singleton":
                    insert_initial_unit(stage, None, None)

            for stage in plan["stages"]:
                completed = (
                    stage["type"] == "inventory"
                    or not (set(stage["depends_on"]) - {inventory_stage_key})
                )
                connection.execute(
                    """
                    INSERT INTO stage_materialization(
                        owner_user_id, revision_id, stage_id, completed,
                        source_ordinal, parent_position, parent_unit_id,
                        entry_offset, legacy_replay, unit_count, updated_at
                    ) VALUES (?, ?, ?, ?, -1, -1, NULL, -1, 0, ?, ?)
                    """,
                    (
                        owner, chosen_revision, stage_ids[stage["key"]],
                        int(completed), stage_unit_counts[str(stage["key"])], now,
                    ),
                )

            admitted_revision = connection.execute(
                """
                UPDATE revisions SET admitted_at=?
                WHERE owner_user_id=? AND id=? AND workload_id=?
                  AND admitted_at IS NULL
                """,
                (now, owner, chosen_revision, workload_id),
            )
            if admitted_revision.rowcount != 1:
                raise DurableStoreError("revision admission seal failed")

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
        _require_limit(limit, maximum=500)
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
        _require_limit(limit, maximum=500)
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
        """Lease due rows; expire a started delivery without sending it twice."""

        selected_channel = self._outbox_channel(channel)
        worker = require_worker_id(worker_id)
        _require_limit(limit, maximum=200)
        duration = require_lease_duration(lease_duration)
        if duration > timedelta(seconds=_MAX_OUTBOX_LEASE_SECONDS):
            raise ValueError("outbox lease duration exceeds maximum")
        current = normalize_instant(now or datetime.now(timezone.utc), name="now")
        current_text = instant_text(current)
        expiry = instant_text(current + duration, name="lease_expires_at")
        with self._transaction() as connection:
            candidates = connection.execute(
                """
                SELECT owner_user_id, id, state, ack_json FROM outbox
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
                delivery_started = False
                if candidate["state"] == OutboxState.LEASED.value:
                    try:
                        acknowledgement = json.loads(
                            str(candidate["ack_json"] or "{}")
                        )
                        delivery_started = (
                            isinstance(acknowledgement, Mapping)
                            and acknowledgement.get("delivery") == "started"
                        )
                    except (TypeError, ValueError):
                        delivery_started = True
                if delivery_started:
                    ambiguous = canonical_json(
                        {
                            "delivery": "cancelled",
                            "reason_code": "delivery_ambiguous",
                        },
                        max_bytes=MAX_EVENT_JSON_BYTES,
                    )
                    recovered = connection.execute(
                        """
                        UPDATE outbox
                        SET state='cancelled', ack_json=?, lease_worker_id=NULL,
                            lease_expires_at=NULL, next_attempt_at=NULL,
                            updated_at=?
                        WHERE owner_user_id=? AND id=? AND state='leased'
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at<=?
                        """,
                        (
                            ambiguous, current_text,
                            candidate["owner_user_id"], candidate["id"],
                            current_text,
                        ),
                    )
                    if recovered.rowcount != 1:
                        raise DurableStoreError(
                            "ambiguous outbox recovery compare-and-set failed"
                        )
                    continue
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

    def mark_outbox_delivery_started(
        self,
        record: OutboxRecord,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Persist the no-retry boundary immediately before a provider call."""

        if not isinstance(record, OutboxRecord):
            raise TypeError("record must be an OutboxRecord")
        worker = require_worker_id(worker_id)
        current = instant_text(normalize_instant(
            now or datetime.now(timezone.utc), name="now",
        ))
        acknowledgement = canonical_json(
            {"delivery": "started"}, max_bytes=MAX_EVENT_JSON_BYTES,
        )
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE outbox SET ack_json=?, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='leased'
                  AND lease_worker_id=? AND fence=?
                  AND (ack_json IS NULL OR json_extract(
                        ack_json, '$.delivery')='started')
                """,
                (
                    acknowledgement, current, record.owner_user_id,
                    record.outbox_id, worker, record.fence,
                ),
            )
            return updated.rowcount == 1

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
                    next_attempt_at=?, ack_json=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='leased'
                  AND lease_worker_id=? AND fence=?
                """,
                (
                    retry, instant_text(current), record.owner_user_id,
                    record.outbox_id, worker, record.fence,
                ),
            )
            return updated.rowcount == 1

    def cancel_outbox(
        self,
        record: OutboxRecord,
        *,
        worker_id: str,
        reason_code: str,
        now: datetime | None = None,
    ) -> bool:
        """Finish a leased delivery whose destination is authoritatively unusable."""

        if not isinstance(record, OutboxRecord):
            raise TypeError("record must be an OutboxRecord")
        worker = require_worker_id(worker_id)
        if reason_code not in _OUTBOX_CANCEL_REASONS:
            raise ValueError("outbox cancellation reason is not closed")
        current = instant_text(normalize_instant(
            now or datetime.now(timezone.utc), name="now",
        ))
        acknowledgement = canonical_json(
            {"delivery": "cancelled", "reason_code": reason_code},
            max_bytes=MAX_EVENT_JSON_BYTES,
        )
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE outbox
                SET state='cancelled', ack_json=?, lease_worker_id=NULL,
                    lease_expires_at=NULL, next_attempt_at=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state='leased'
                  AND lease_worker_id=? AND fence=?
                """,
                (
                    acknowledgement, current, record.owner_user_id,
                    record.outbox_id, worker, record.fence,
                ),
            )
            return updated.rowcount == 1

    def prune_outbox(
        self,
        *,
        older_than: timedelta = timedelta(days=30),
        limit: int = 200,
        now: datetime | None = None,
    ) -> int:
        """Remove a bounded batch of acknowledged delivery bookkeeping.

        Events remain immutable audit facts.  Pending, failed and leased rows
        are never eligible, so retention cannot turn an uncertain send into a
        silent success.
        """

        if (
            not isinstance(older_than, timedelta)
            or not timedelta(days=1) <= older_than <= timedelta(days=3650)
        ):
            raise ValueError("older_than must be between one day and ten years")
        _require_limit(limit, maximum=1000)
        current = normalize_instant(now or datetime.now(timezone.utc), name="now")
        cutoff = instant_text(current - older_than, name="retention cutoff")
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT owner_user_id, id
                FROM outbox
                WHERE state IN ('sent', 'cancelled') AND updated_at<?
                ORDER BY updated_at, owner_user_id, id
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    DELETE FROM outbox
                    WHERE owner_user_id=? AND id=?
                      AND state IN ('sent', 'cancelled') AND updated_at<?
                    """,
                    (row["owner_user_id"], row["id"], cutoff),
                )
            return len(rows)

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
        _require_limit(limit, maximum=200)
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
        now: datetime | None = None,
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
        current, current_text = self._operation_now(now)
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
                now_text=current_text,
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
        now_text: str | None = None,
    ) -> WorkloadRecord:
        transition_time = utc_now() if now_text is None else now_text
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
                target.value, transition_time, terminal_json, owner, workload_id,
                source.value, expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflictError("workload state compare-and-set failed")
        if target not in {WorkloadState.QUEUED, WorkloadState.RUNNING}:
            # Fairness credit is live scheduler state, not audit history.  A
            # quiescent workload receives a fresh position if it is resumed;
            # retaining every historical row would make each future claim scan
            # all completed work.
            connection.execute(
                "DELETE FROM scheduler_credits "
                "WHERE owner_user_id=? AND workload_id=?",
                (owner, workload_id),
            )
        else:
            # Wall-clock authority starts when work first enters the queue.
            # Otherwise a revision with no compatible worker can wait forever
            # without ever consuming its declared time budget.
            usage_started = connection.execute(
                """
                UPDATE revision_usage
                SET started_at=COALESCE(started_at, ?),
                    clock_high_water_at=CASE
                      WHEN clock_high_water_at IS NULL
                        OR clock_high_water_at<? THEN ?
                      ELSE clock_high_water_at
                    END,
                    updated_at=?
                WHERE owner_user_id=? AND revision_id=(
                  SELECT active_revision_id FROM workloads
                  WHERE owner_user_id=? AND id=?
                )
                """,
                (
                    transition_time, transition_time, transition_time,
                    transition_time, owner, owner, workload_id,
                ),
            )
            if usage_started.rowcount != 1:
                raise DurableStoreError("revision usage row is missing")
        return _row_to_workload(
            self._select_workload(connection, owner, workload_id)
        )

    @staticmethod
    def _cancel_waiting_units_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner: str,
        revision_id: str,
        now_text: str,
        limit: int = _UNIT_STATE_BATCH_SIZE,
    ) -> tuple[int, int]:
        """Cancel one bounded batch and count consumed attention facts."""

        rows = connection.execute(
            """
            SELECT id, state
            FROM units
            WHERE owner_user_id=? AND revision_id=?
              AND state IN ('pending', 'retry_wait', 'needs_attention')
              AND active_attempt_id IS NULL
            ORDER BY updated_at, id
            LIMIT ?
            """,
            (owner, revision_id, limit),
        ).fetchall()
        if not rows:
            return 0, 0
        identifiers = tuple(str(row["id"]) for row in rows)
        placeholders = ",".join("?" for _identifier in identifiers)
        changed = connection.execute(
            f"""
            UPDATE units
            SET state='cancelled', next_attempt_at=NULL,
                error_class=COALESCE(error_class, 'cancelled'),
                manual_retry_tokens=0, partial_output=0, updated_at=?
            WHERE owner_user_id=? AND revision_id=?
              AND state IN ('pending', 'retry_wait', 'needs_attention')
              AND active_attempt_id IS NULL
              AND id IN ({placeholders})
            """,
            (now_text, owner, revision_id, *identifiers),
        ).rowcount
        if changed != len(rows):
            raise DurableStoreError("unit cancellation batch changed concurrently")
        attention = sum(row["state"] == "needs_attention" for row in rows)
        return changed, attention

    @staticmethod
    def _waiting_unit_exists_in_transaction(
        connection: sqlite3.Connection,
        owner: str,
        revision_id: str,
    ) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM units
            WHERE owner_user_id=? AND revision_id=?
              AND state IN ('pending', 'retry_wait', 'needs_attention')
              AND active_attempt_id IS NULL
            LIMIT 1
            """,
            (owner, revision_id),
        ).fetchone() is not None

    @staticmethod
    def _repair_exhausted_units_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner: str,
        revision_id: str,
        now_text: str,
        limit: int,
    ) -> int:
        """Materialize one bounded batch of exhausted automatic retries."""

        return connection.execute(
            """
            UPDATE units
            SET state='needs_attention', next_attempt_at=NULL,
                error_class=COALESCE(error_class, 'budget_exhausted'),
                manual_retry_generation=(
                  SELECT revision.manual_retry_generation
                  FROM revisions revision
                  WHERE revision.owner_user_id=units.owner_user_id
                    AND revision.id=units.revision_id
                ),
                updated_at=?
            WHERE owner_user_id=? AND revision_id=? AND id IN (
                SELECT unit.id
                FROM units unit
                JOIN stages stage
                  ON stage.owner_user_id=unit.owner_user_id
                 AND stage.revision_id=unit.revision_id
                 AND stage.id=unit.stage_id
                WHERE unit.owner_user_id=? AND unit.revision_id=?
                  AND unit.state='pending'
                  AND unit.manual_retry_tokens=0
                  AND unit.attempt_count >= CAST(
                    json_extract(stage.retry_json, '$.max_attempts') AS INTEGER
                  )
                ORDER BY unit.updated_at, unit.id
                LIMIT ?
            )
            """,
            (now_text, owner, revision_id, owner, revision_id, limit),
        ).rowcount

    @staticmethod
    def _exhausted_pending_exists_in_transaction(
        connection: sqlite3.Connection,
        owner: str,
        revision_id: str,
    ) -> bool:
        return connection.execute(
            """
            SELECT 1
            FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.revision_id=unit.revision_id
             AND stage.id=unit.stage_id
            WHERE unit.owner_user_id=? AND unit.revision_id=?
              AND unit.state='pending'
              AND unit.manual_retry_tokens=0
              AND unit.attempt_count >= CAST(
                json_extract(stage.retry_json, '$.max_attempts') AS INTEGER
              )
            LIMIT 1
            """,
            (owner, revision_id),
        ).fetchone() is not None

    @staticmethod
    def _active_unit_count_in_transaction(
        connection: sqlite3.Connection,
        owner: str,
        revision_id: str,
    ) -> int:
        return int(connection.execute(
            """
            SELECT COUNT(*) FROM units
            WHERE owner_user_id=? AND revision_id=?
              AND state IN ('leased', 'running')
            """,
            (owner, revision_id),
        ).fetchone()[0])

    @staticmethod
    def _artifact_attention_count_in_transaction(
        connection: sqlite3.Connection,
        owner: str,
        revision_id: str,
    ) -> int:
        return int(connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM artifacts
               WHERE owner_user_id=? AND revision_id=?
                 AND state='needs_attention')
              +
              (SELECT COUNT(*)
               FROM publications publication
               JOIN artifacts artifact
                 ON artifact.owner_user_id=publication.owner_user_id
                AND artifact.id=publication.artifact_id
               WHERE artifact.owner_user_id=? AND artifact.revision_id=?
                 AND publication.state='needs_attention')
            """,
            (owner, revision_id, owner, revision_id),
        ).fetchone()[0])

    @staticmethod
    def _add_revision_usage_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner: str,
        revision_id: str,
        input_bytes: int = 0,
        output_bytes: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros: int = 0,
        artifacts: int = 0,
        usage_unknown: bool = False,
        now_text: str,
    ) -> None:
        increments = (
            input_bytes, output_bytes, input_tokens,
            output_tokens, cost_micros, artifacts,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in increments
        ):
            raise DurableStoreError("revision usage increment is invalid")
        row = connection.execute(
            """
            SELECT input_bytes, output_bytes, input_tokens, output_tokens,
                   cost_micros, artifact_count
            FROM revision_usage
            WHERE owner_user_id=? AND revision_id=?
            """,
            (owner, revision_id),
        ).fetchone()
        if row is None:
            raise DurableStoreError("revision usage row is missing")
        current = tuple(int(row[index]) for index in range(6))
        if any(
            value > _SQLITE_INTEGER_MAX - increment
            for value, increment in zip(current, increments, strict=True)
        ):
            raise DurableStoreError("revision usage counter overflow")
        updated = connection.execute(
            """
            UPDATE revision_usage
            SET input_bytes=input_bytes+?, output_bytes=output_bytes+?,
                input_tokens=input_tokens+?, output_tokens=output_tokens+?,
                cost_micros=cost_micros+?, artifact_count=artifact_count+?,
                usage_unknown=MAX(usage_unknown, ?), updated_at=?
            WHERE owner_user_id=? AND revision_id=?
            """,
            (
                input_bytes, output_bytes, input_tokens, output_tokens,
                cost_micros, artifacts, int(usage_unknown), now_text,
                owner, revision_id,
            ),
        )
        if updated.rowcount != 1:
            raise DurableStoreError("revision usage update failed")
        if usage_unknown:
            connection.execute(
                """
                UPDATE revisions SET usage_complete=0
                WHERE owner_user_id=? AND id=?
                """,
                (owner, revision_id),
            )

    @staticmethod
    def _budget_violation_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner: str,
        workload_id: str,
        revision_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT revision.plan_json,
                   usage.input_bytes, usage.output_bytes,
                   usage.input_tokens, usage.output_tokens,
                   usage.cost_micros, usage.usage_unknown,
                   usage.artifact_count, usage.started_at,
                   usage.clock_high_water_at
            FROM revisions revision
            JOIN revision_usage usage
              ON usage.owner_user_id=revision.owner_user_id
             AND usage.revision_id=revision.id
            WHERE revision.owner_user_id=? AND revision.id=?
              AND revision.workload_id=?
            """,
            (owner, revision_id, workload_id),
        ).fetchone()
        if row is None:
            return {
                "reason_code": "budget_accounting_incomplete",
                "budget": "accounting",
            }
        if bool(row["usage_unknown"]):
            return {
                "reason_code": "budget_accounting_incomplete",
                "budget": "accounting",
            }
        if DurableWorkloadStore._clock_regressed(
            row["clock_high_water_at"], now,
        ):
            return {
                "reason_code": "budget_accounting_incomplete",
                "budget": "max_wall_time_s",
                "clock_regressed": True,
            }
        budgets = json.loads(str(row["plan_json"]))["budgets"]
        observed = {
            "max_bytes_read": int(row["input_bytes"]),
            "max_bytes_written": int(row["output_bytes"]),
            "max_tokens": int(row["input_tokens"]) + int(row["output_tokens"]),
            "max_cost_micros": int(row["cost_micros"]),
            "max_artifacts": int(row["artifact_count"]),
        }
        for name, value in observed.items():
            limit = int(budgets[name])
            if value > limit:
                return {
                    "reason_code": "budget_limit_exceeded",
                    "budget": name,
                    "observed": value,
                    "limit": limit,
                }
        started_at = row["started_at"]
        if started_at is not None:
            elapsed = max(
                0,
                int((now - parse_instant(str(started_at))).total_seconds()),
            )
            limit = int(budgets["max_wall_time_s"])
            if elapsed >= limit:
                return {
                    "reason_code": "budget_limit_exceeded",
                    "budget": "max_wall_time_s",
                    "observed": elapsed,
                    "limit": limit,
                }
        return None

    def budget_violation(
        self,
        lease: Lease,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return a bounded reason when an active lease cannot continue safely."""

        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        current, _current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                raise DurableStoreError("budget check requires the active fence")
            return self._budget_violation_in_transaction(
                connection,
                owner=lease.owner_user_id,
                workload_id=lease.workload_id,
                revision_id=lease.revision_id,
                now=current,
            )

    def remaining_model_budget(self, lease: Lease) -> dict[str, int]:
        """Return the transactionally observed token and cost remainder."""

        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                raise DurableStoreError(
                    "model budget requires the active attempt fence"
                )
            budget = connection.execute(
                """
                SELECT revision.plan_json, usage.input_tokens,
                       usage.output_tokens, usage.cost_micros,
                       usage.usage_unknown
                FROM revisions revision
                JOIN revision_usage usage
                  ON usage.owner_user_id=revision.owner_user_id
                 AND usage.revision_id=revision.id
                WHERE revision.owner_user_id=? AND revision.id=?
                  AND revision.workload_id=?
                """,
                (
                    lease.owner_user_id,
                    lease.revision_id,
                    lease.workload_id,
                ),
            ).fetchone()
            if budget is None or bool(budget["usage_unknown"]):
                raise BudgetExceededError({
                    "reason_code": "budget_accounting_incomplete",
                    "budget": "accounting",
                })
            limits = json.loads(str(budget["plan_json"]))["budgets"]
            used_tokens = int(budget["input_tokens"]) + int(
                budget["output_tokens"]
            )
            return {
                "max_tokens": max(0, int(limits["max_tokens"]) - used_tokens),
                "max_cost_micros": max(
                    0,
                    int(limits["max_cost_micros"])
                    - int(budget["cost_micros"]),
                ),
            }

    def _automatic_workload_transition_in_transaction(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        target: WorkloadState,
        *,
        reason_code: str,
        details: Mapping[str, Any] | None = None,
        now_text: str | None = None,
    ) -> WorkloadRecord:
        """Apply one engine-derived state change with its durable evidence."""

        owner = str(row["owner_user_id"])
        workload_id = str(row["id"])
        source = WorkloadState(row["state"])
        if not can_transition_workload(source, target):
            raise InvalidTransitionError(
                f"workload transition {source.value}->{target.value} is forbidden"
            )
        evidence = dict(details or {})
        evidence.update({
            "reason_code": reason_code,
            "previous_state": source.value,
            "new_state": target.value,
            "new_version": int(row["version"]) + 1,
        })
        result = self._update_state_in_transaction(
            connection,
            owner=owner,
            workload_id=workload_id,
            source=source,
            target=target,
            expected_version=int(row["version"]),
            terminal_reason=(
                {"reason_code": reason_code, **dict(details or {})}
                if target in {WorkloadState.CANCELLED, WorkloadState.FAILED}
                else None
            ),
            now_text=now_text,
        )
        event_type = _EVENT_BY_TARGET[target]
        event = self.append_event_in_transaction(
            connection,
            owner_user_id=owner,
            workload_id=workload_id,
            event_type=event_type,
            payload=evidence,
        )
        if target in {WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED}:
            self._enqueue_notification_in_transaction(
                connection,
                owner_user_id=owner,
                workload_id=workload_id,
                event_id=event.event_id,
            )
        return result

    def _settle_workload_in_transaction(
        self,
        connection: sqlite3.Connection,
        owner_user_id: str,
        workload_id: str,
        *,
        now_text: str,
        repair_exhausted: bool = False,
        mutation_limit: int = _UNIT_STATE_BATCH_SIZE,
    ) -> tuple[WorkloadRecord, int]:
        """Converge control and failure states from authoritative unit facts.

        An explicit cancellation drains attempts already in flight and then
        terminates even when attention facts remain; those facts stay stored
        and are summarized in the terminal reason.  Outside that explicit
        path, attention takes precedence over pause and ordinary failure.
        """

        row = self._select_workload(connection, owner_user_id, workload_id)
        state = WorkloadState(row["state"])
        revision_id = row["active_revision_id"]
        if revision_id is None:
            return _row_to_workload(row), 0

        if state in {
            WorkloadState.CANCELLED,
            WorkloadState.FAILED,
            WorkloadState.COMPLETED,
            WorkloadState.COMPLETED_WITH_ERRORS,
        }:
            changed = 0
            if state in {WorkloadState.CANCELLED, WorkloadState.FAILED}:
                changed, _attention = self._cancel_waiting_units_in_transaction(
                    connection,
                    owner=owner_user_id,
                    revision_id=str(revision_id),
                    now_text=now_text,
                    limit=mutation_limit,
                )
            return _row_to_workload(row), changed

        if state is WorkloadState.CANCEL_REQUESTED:
            cancelled, attention_cancelled = (
                self._cancel_waiting_units_in_transaction(
                    connection,
                    owner=owner_user_id,
                    revision_id=str(revision_id),
                    now_text=now_text,
                    limit=mutation_limit,
                )
            )
            reason = json.loads(str(row["terminal_reason_json"] or "{}"))
            previous_attention = reason.get("unresolved_unit_attention", 0)
            if (
                isinstance(previous_attention, bool)
                or not isinstance(previous_attention, int)
                or previous_attention < 0
            ):
                raise DurableStoreError("cancellation attention counter is invalid")
            unit_attention = previous_attention + attention_cancelled
            if attention_cancelled:
                reason.update({
                    "reason": str(reason.get("reason", "owner_cancelled")),
                    "unresolved_unit_attention": unit_attention,
                })
                connection.execute(
                    """
                    UPDATE workloads
                    SET terminal_reason_json=?, updated_at=?
                    WHERE owner_user_id=? AND id=? AND state='cancel_requested'
                    """,
                    (
                        canonical_json(reason, max_bytes=MAX_EVENT_JSON_BYTES),
                        now_text, owner_user_id, workload_id,
                    ),
                )
            active = self._active_unit_count_in_transaction(
                connection, owner_user_id, str(revision_id),
            )
            waiting = self._waiting_unit_exists_in_transaction(
                connection, owner_user_id, str(revision_id),
            )
            if active or waiting:
                return _row_to_workload(
                    self._select_workload(connection, owner_user_id, workload_id)
                ), cancelled
            artifact_attention = self._artifact_attention_count_in_transaction(
                connection, owner_user_id, str(revision_id),
            )
            current_row = self._select_workload(
                connection, owner_user_id, workload_id,
            )
            result = self._automatic_workload_transition_in_transaction(
                connection,
                current_row,
                WorkloadState.CANCELLED,
                reason_code="owner_cancellation_settled",
                details={
                    "unresolved_unit_attention": unit_attention,
                    "unresolved_artifact_attention": artifact_attention,
                },
                now_text=now_text,
            )
            return result, cancelled + 1

        if state in {
            WorkloadState.ADMITTED,
            WorkloadState.QUEUED,
            WorkloadState.RUNNING,
            WorkloadState.PAUSE_REQUESTED,
            WorkloadState.PAUSED,
        }:
            violation = self._budget_violation_in_transaction(
                connection,
                owner=owner_user_id,
                workload_id=workload_id,
                revision_id=str(revision_id),
                now=parse_instant(now_text, name="now"),
            )
            if violation is not None:
                result = self._automatic_workload_transition_in_transaction(
                    connection,
                    row,
                    WorkloadState.NEEDS_ATTENTION,
                    reason_code=str(violation["reason_code"]),
                    details=violation,
                    now_text=now_text,
                )
                return result, 1

        # A pending unit without either automatic budget or a one-shot manual
        # grant is not executable.  Materialize that fact as attention instead
        # of allowing a queued workload to stall silently.
        exhausted = 0
        if repair_exhausted:
            exhausted = self._repair_exhausted_units_in_transaction(
                connection,
                owner=owner_user_id,
                revision_id=str(revision_id),
                now_text=now_text,
                limit=mutation_limit,
            )
            if self._exhausted_pending_exists_in_transaction(
                connection, owner_user_id, str(revision_id),
            ):
                return _row_to_workload(row), exhausted

        if state is WorkloadState.NEEDS_ATTENTION:
            return _row_to_workload(row), exhausted

        materialization_attention = int(connection.execute(
            """
            SELECT COUNT(*) FROM stage_materialization
            WHERE owner_user_id=? AND revision_id=?
              AND attention_code IS NOT NULL
            """,
            (owner_user_id, revision_id),
        ).fetchone()[0])
        if materialization_attention:
            result = self._automatic_workload_transition_in_transaction(
                connection,
                row,
                WorkloadState.NEEDS_ATTENTION,
                reason_code="materialization_attention_required",
                details={"affected_stages": materialization_attention},
                now_text=now_text,
            )
            return result, exhausted + 1

        unit_attention = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM units unit
            JOIN revisions revision
              ON revision.owner_user_id=unit.owner_user_id
             AND revision.id=unit.revision_id
            WHERE unit.owner_user_id=? AND unit.revision_id=?
              AND unit.state='needs_attention'
              AND unit.manual_retry_generation >=
                  revision.manual_retry_generation
            """,
            (owner_user_id, revision_id),
        ).fetchone()[0])
        artifact_attention = self._artifact_attention_count_in_transaction(
            connection, owner_user_id, str(revision_id),
        )
        if unit_attention or artifact_attention:
            result = self._automatic_workload_transition_in_transaction(
                connection,
                row,
                WorkloadState.NEEDS_ATTENTION,
                reason_code="execution_attention_required",
                details={
                    "unit_attention": unit_attention,
                    "artifact_attention": artifact_attention,
                },
                now_text=now_text,
            )
            return result, exhausted + 1

        revision = connection.execute(
            """
            SELECT failure_policy, tolerated_error_classes_json
            FROM revisions
            WHERE owner_user_id=? AND id=? AND workload_id=?
            """,
            (owner_user_id, revision_id, workload_id),
        ).fetchone()
        if revision is None:
            raise DurableStoreError("active revision is missing")
        untolerated = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM units
            WHERE owner_user_id=? AND revision_id=?
              AND state='failed_permanent'
              AND (
                ?<>'declared'
                OR error_class IS NULL
                OR error_class NOT IN (SELECT value FROM json_each(?))
              )
            """,
            (
                owner_user_id, revision_id, revision["failure_policy"],
                revision["tolerated_error_classes_json"],
            ),
        ).fetchone()[0])
        if untolerated:
            cancelled, _attention = self._cancel_waiting_units_in_transaction(
                connection,
                owner=owner_user_id,
                revision_id=str(revision_id),
                now_text=now_text,
                limit=mutation_limit,
            )
            if self._active_unit_count_in_transaction(
                connection, owner_user_id, str(revision_id),
            ) or self._waiting_unit_exists_in_transaction(
                connection, owner_user_id, str(revision_id),
            ):
                return _row_to_workload(row), exhausted + cancelled
            result = self._automatic_workload_transition_in_transaction(
                connection,
                row,
                WorkloadState.FAILED,
                reason_code="untolerated_unit_failure",
                details={"failed_units": untolerated},
                now_text=now_text,
            )
            return result, exhausted + cancelled + 1

        if state is WorkloadState.PAUSE_REQUESTED:
            if not self._active_unit_count_in_transaction(
                connection, owner_user_id, str(revision_id),
            ):
                result = self._automatic_workload_transition_in_transaction(
                    connection,
                    row,
                    WorkloadState.PAUSED,
                    reason_code="active_attempts_drained",
                    now_text=now_text,
                )
                return result, exhausted + 1
        return _row_to_workload(row), exhausted

    def settle_workload(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        now: datetime | None = None,
    ) -> WorkloadRecord:
        """Converge one workload after a worker, recovery or control event."""

        owner = _require_owner(owner_user_id)
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            result, _changed = self._settle_workload_in_transaction(
                connection,
                owner,
                workload_id,
                now_text=current_text,
                repair_exhausted=True,
            )
            return result

    def settle_workloads(
        self,
        *,
        limit: int = 200,
        now: datetime | None = None,
    ) -> int:
        """Perform at most ``limit`` unit repairs plus constant state changes."""

        _require_limit(limit, maximum=1000)
        _current, current_text = self._operation_now(now)
        rows = self._connection.execute(
            """
            SELECT workload.owner_user_id, workload.id
            FROM workloads workload
            WHERE workload.active_revision_id IS NOT NULL AND (
                (
                  workload.state IN ('cancelled', 'failed')
                  AND EXISTS (
                    SELECT 1 FROM units residual_unit
                    WHERE residual_unit.owner_user_id=workload.owner_user_id
                      AND residual_unit.revision_id=workload.active_revision_id
                      AND residual_unit.state IN (
                        'pending', 'retry_wait', 'needs_attention'
                      )
                      AND residual_unit.active_attempt_id IS NULL
                  )
                )
                OR (
                  workload.state NOT IN (
                    'cancelled', 'failed', 'completed',
                    'completed_with_errors', 'needs_attention'
                  )
                  AND (
                    workload.state IN ('pause_requested', 'cancel_requested')
                OR EXISTS (
                    SELECT 1
                    FROM revisions budget_revision
                    JOIN revision_usage usage
                      ON usage.owner_user_id=budget_revision.owner_user_id
                     AND usage.revision_id=budget_revision.id
                    WHERE budget_revision.owner_user_id=workload.owner_user_id
                      AND budget_revision.id=workload.active_revision_id
                      AND (
                        usage.usage_unknown=1
                        OR (
                          usage.clock_high_water_at IS NOT NULL
                          AND julianday(usage.clock_high_water_at)
                              > julianday(?)
                                + (? / 86400.0)
                        )
                        OR usage.input_bytes > CAST(json_extract(
                          budget_revision.plan_json,
                          '$.budgets.max_bytes_read'
                        ) AS INTEGER)
                        OR usage.output_bytes > CAST(json_extract(
                          budget_revision.plan_json,
                          '$.budgets.max_bytes_written'
                        ) AS INTEGER)
                        OR usage.input_tokens > CAST(json_extract(
                          budget_revision.plan_json,
                          '$.budgets.max_tokens'
                        ) AS INTEGER) - usage.output_tokens
                        OR usage.cost_micros > CAST(json_extract(
                          budget_revision.plan_json,
                          '$.budgets.max_cost_micros'
                        ) AS INTEGER)
                        OR usage.artifact_count > CAST(json_extract(
                          budget_revision.plan_json,
                          '$.budgets.max_artifacts'
                        ) AS INTEGER)
                        OR (
                          usage.started_at IS NOT NULL
                          AND (julianday(?) - julianday(usage.started_at))
                              * 86400 >= CAST(json_extract(
                                budget_revision.plan_json,
                                '$.budgets.max_wall_time_s'
                              ) AS INTEGER)
                        )
                      )
                )
                OR EXISTS (
                    SELECT 1 FROM stage_materialization progress
                    WHERE progress.owner_user_id=workload.owner_user_id
                      AND progress.revision_id=workload.active_revision_id
                      AND progress.attention_code IS NOT NULL
                )
                OR EXISTS (
                    SELECT 1 FROM units unit
                    WHERE unit.owner_user_id=workload.owner_user_id
                      AND unit.revision_id=workload.active_revision_id
                      AND (
                        unit.state='failed_permanent'
                        OR (
                          unit.state='needs_attention'
                          AND unit.manual_retry_generation >= COALESCE((
                            SELECT retry_revision.manual_retry_generation
                            FROM revisions retry_revision
                            WHERE retry_revision.owner_user_id=
                                  unit.owner_user_id
                              AND retry_revision.id=unit.revision_id
                          ), 0)
                        )
                      )
                )
                OR EXISTS (
                    SELECT 1 FROM artifacts artifact
                    LEFT JOIN publications publication
                      ON publication.owner_user_id=artifact.owner_user_id
                     AND publication.artifact_id=artifact.id
                    WHERE artifact.owner_user_id=workload.owner_user_id
                      AND artifact.revision_id=workload.active_revision_id
                      AND (
                        artifact.state='needs_attention'
                        OR publication.state='needs_attention'
                      )
                )
                OR EXISTS (
                    SELECT 1
                    FROM units unit
                    JOIN stages stage
                      ON stage.owner_user_id=unit.owner_user_id
                     AND stage.revision_id=unit.revision_id
                     AND stage.id=unit.stage_id
                    WHERE unit.owner_user_id=workload.owner_user_id
                      AND unit.revision_id=workload.active_revision_id
                      AND unit.state='pending'
                      AND unit.manual_retry_tokens=0
                      AND unit.attempt_count >= CAST(
                        json_extract(stage.retry_json, '$.max_attempts') AS INTEGER
                      )
                )
                  )
                )
              )
            ORDER BY workload.updated_at, workload.owner_user_id, workload.id
            LIMIT ?
            """,
            (
                current_text,
                _CLOCK_REGRESSION_TOLERANCE.total_seconds(),
                current_text,
                limit,
            ),
        ).fetchall()
        changed = 0
        for row in rows:
            remaining = limit - min(changed, limit)
            if remaining <= 0:
                break
            with self._transaction() as connection:
                _result, did_change = self._settle_workload_in_transaction(
                    connection,
                    str(row["owner_user_id"]),
                    str(row["id"]),
                    now_text=current_text,
                    repair_exhausted=True,
                    mutation_limit=remaining,
                )
                changed += did_change
        return changed

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
        now_text: str | None = None,
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
                utc_now() if now_text is None else now_text,
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
        now: datetime | None = None,
    ) -> WorkloadRecord:
        owner = _require_owner(owner_user_id)
        key = _require_key(idempotency_key, name="idempotency_key")
        expected = _require_version(expected_version)
        command_payload = {"command": command, "expected_version": expected}
        payload_digest = digest_json(
            "durable-command", command_payload, max_bytes=MAX_EVENT_JSON_BYTES
        )
        _current, current_text = self._operation_now(now)
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
                        {
                            "reason": "owner_cancelled",
                            "unresolved_unit_attention": 0,
                        }
                        if target in {
                            WorkloadState.CANCEL_REQUESTED,
                            WorkloadState.CANCELLED,
                        }
                        else None
                    ),
                    now_text=current_text,
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
            if (
                result.active_revision_id is not None
                and result.state in {
                    WorkloadState.PAUSE_REQUESTED,
                    WorkloadState.CANCEL_REQUESTED,
                }
            ):
                result, _settled = self._settle_workload_in_transaction(
                    connection,
                    owner,
                    workload_id,
                    now_text=current_text,
                )
            elif (
                result.active_revision_id is not None
                and result.state is WorkloadState.CANCELLED
            ):
                self._cancel_waiting_units_in_transaction(
                    connection,
                    owner=owner,
                    revision_id=result.active_revision_id,
                    now_text=current_text,
                )
            self._record_command(
                connection,
                owner=owner,
                workload_id=workload_id,
                idempotency_key=key,
                command=command,
                payload_digest=payload_digest,
                result=result,
                now_text=current_text,
            )
            return result

    def request_pause(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> WorkloadRecord:
        return self._control(
            owner_user_id, workload_id, command="pause",
            idempotency_key=idempotency_key, expected_version=expected_version,
            now=now,
        )

    def request_resume(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> WorkloadRecord:
        return self._control(
            owner_user_id, workload_id, command="resume",
            idempotency_key=idempotency_key, expected_version=expected_version,
            now=now,
        )

    def request_cancel(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> WorkloadRecord:
        return self._control(
            owner_user_id, workload_id, command="cancel",
            idempotency_key=idempotency_key, expected_version=expected_version,
            now=now,
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
        now: datetime | None = None,
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
        current, current_text = self._operation_now(now)
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
            revision_id = row["active_revision_id"]
            if revision_id is None:
                raise InvalidTransitionError(
                    "attention resolution requires an active revision"
                )
            active_units = self._active_unit_count_in_transaction(
                connection, owner, str(revision_id),
            )
            if decision == "retry":
                artifact_attention = self._artifact_attention_count_in_transaction(
                    connection, owner, str(revision_id),
                )
                if artifact_attention:
                    raise InvalidTransitionError(
                        "artifact attention requires reconciliation or a new revision"
                    )
                blocked_dependency_attention = int(connection.execute(
                    """
                    SELECT COUNT(*) FROM units
                    WHERE owner_user_id=? AND revision_id=?
                      AND state='needs_attention'
                      AND error_class IN (
                        'budget_exhausted',
                        'dependency_fan_in_exceeded',
                        'dependency_result_missing',
                        'dependency_result_unavailable'
                      )
                    """,
                    (owner, revision_id),
                ).fetchone()[0])
                if blocked_dependency_attention:
                    raise InvalidTransitionError(
                        "dependency attention requires a new revision or cancellation"
                    )
                materialization_attention = int(connection.execute(
                    """
                    SELECT COUNT(*) FROM stage_materialization
                    WHERE owner_user_id=? AND revision_id=?
                      AND attention_code IS NOT NULL
                    """,
                    (owner, revision_id),
                ).fetchone()[0])
                if materialization_attention:
                    raise InvalidTransitionError(
                        "materialization attention requires a new revision "
                        "or cancellation"
                    )
                budget_violation = self._budget_violation_in_transaction(
                    connection,
                    owner=owner,
                    workload_id=workload_id,
                    revision_id=str(revision_id),
                    now=current,
                )
                if budget_violation is not None:
                    raise InvalidTransitionError(
                        "budget attention requires changed accounting, "
                        "a new revision, or cancellation"
                    )
                retry_authority = connection.execute(
                    """
                    SELECT manual_retry_generation
                    FROM revisions
                    WHERE owner_user_id=? AND id=? AND workload_id=?
                    """,
                    (owner, revision_id, workload_id),
                ).fetchone()
                if retry_authority is None:
                    raise DurableStoreError("active revision is missing")
                retry_generation = int(
                    retry_authority["manual_retry_generation"]
                )
                if retry_generation >= 1_000_000:
                    raise InvalidTransitionError(
                        "manual retry authority has reached its safety cap"
                    )
                changed_authority = connection.execute(
                    """
                    UPDATE revisions
                    SET manual_retry_generation=manual_retry_generation+1
                    WHERE owner_user_id=? AND id=? AND workload_id=?
                      AND manual_retry_generation=?
                    """,
                    (owner, revision_id, workload_id, retry_generation),
                )
                if changed_authority.rowcount != 1:
                    raise DurableStoreError(
                        "manual retry authority compare-and-set failed"
                    )
                target = (
                    WorkloadState.RUNNING
                    if active_units
                    else WorkloadState.QUEUED
                )
            else:
                target = WorkloadState.CANCEL_REQUESTED
            result = self._update_state_in_transaction(
                connection,
                owner=owner,
                workload_id=workload_id,
                source=source,
                target=target,
                expected_version=expected,
                terminal_reason=(
                    {
                        "reason": "attention_cancelled",
                        "unresolved_unit_attention": 0,
                    }
                    if decision == "cancel" else None
                ),
                now_text=current_text,
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
            if result.state is WorkloadState.CANCEL_REQUESTED:
                result, _settled = self._settle_workload_in_transaction(
                    connection,
                    owner,
                    workload_id,
                    now_text=current_text,
                )
            self._record_command(
                connection,
                owner=owner,
                workload_id=workload_id,
                idempotency_key=key,
                command="resolve_attention",
                payload_digest=payload_digest,
                result=result,
                now_text=current_text,
            )
            connection.execute(
                """
                INSERT INTO attention_resolutions(
                    owner_user_id, workload_id, idempotency_key,
                    decision, note_redacted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (owner, workload_id, key, decision, note_redacted, current_text),
            )
            return result

    def unit_counters_many(
        self,
        owner_user_id: str,
        workload_ids: Sequence[str],
    ) -> dict[str, UnitCounters]:
        """Return counters for a bounded workload page in one SQL statement."""

        owner = _require_owner(owner_user_id)
        if isinstance(workload_ids, (str, bytes)) or not isinstance(
            workload_ids,
            Sequence,
        ):
            raise TypeError("workload_ids must be a sequence")
        identifiers = tuple(workload_ids)
        if len(identifiers) > 200:
            raise ValueError("workload_ids exceeds 200 items")
        if (
            any(
                not isinstance(value, str) or not _ID_RE.fullmatch(value)
                for value in identifiers
            )
            or len(identifiers) != len(set(identifiers))
        ):
            raise ValueError("workload_ids contains invalid or duplicate identifiers")
        if not identifiers:
            return {}

        placeholders = ",".join("?" for _value in identifiers)
        rows = self._connection.execute(
            f"""
            WITH selected AS (
                SELECT owner_user_id, id AS workload_id, active_revision_id
                FROM workloads
                WHERE owner_user_id=? AND id IN ({placeholders})
            ),
            source_counts AS (
                SELECT selected.workload_id, COUNT(source.id) AS discovered
                FROM selected
                LEFT JOIN sources source
                  ON source.owner_user_id=selected.owner_user_id
                 AND source.revision_id=selected.active_revision_id
                GROUP BY selected.workload_id
            ),
            unit_counts AS (
                SELECT selected.workload_id,
                       SUM(CASE WHEN unit.state='committed' THEN 1 ELSE 0 END) AS committed,
                       SUM(CASE WHEN unit.state IN ('failed_permanent', 'cancelled') THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN unit.state='skipped' THEN 1 ELSE 0 END) AS skipped,
                       SUM(CASE WHEN unit.state='needs_attention' THEN 1 ELSE 0 END) AS attention,
                       SUM(CASE WHEN unit.state IN ('pending', 'leased', 'running', 'retry_wait') THEN 1 ELSE 0 END) AS pending
                FROM selected
                LEFT JOIN units unit
                  ON unit.owner_user_id=selected.owner_user_id
                 AND unit.revision_id=selected.active_revision_id
                GROUP BY selected.workload_id
            )
            SELECT selected.workload_id, source_counts.discovered,
                   unit_counts.committed, unit_counts.failed,
                   unit_counts.skipped, unit_counts.attention,
                   unit_counts.pending
            FROM selected
            JOIN source_counts USING (workload_id)
            JOIN unit_counts USING (workload_id)
            """,
            (owner, *identifiers),
        ).fetchall()
        if len(rows) != len(identifiers):
            raise WorkloadNotFoundError("workload not found")
        return {
            str(row["workload_id"]): UnitCounters(
                discovered=int(row["discovered"] or 0),
                committed=int(row["committed"] or 0),
                failed=int(row["failed"] or 0),
                skipped=int(row["skipped"] or 0),
                attention=int(row["attention"] or 0),
                pending=int(row["pending"] or 0),
            )
            for row in rows
        }

    def unit_counters(
        self, owner_user_id: str, workload_id: str,
    ) -> UnitCounters:
        return self.unit_counters_many(owner_user_id, (workload_id,))[workload_id]

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
                   progress.attention_code AS materialization_attention_code,
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
            LEFT JOIN stage_materialization progress
              ON progress.owner_user_id=s.owner_user_id
             AND progress.revision_id=s.revision_id
             AND progress.stage_id=s.id
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
                "materialization_attention": (
                    row["materialization_attention_code"] is not None
                ),
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
            WITH error_facts(error_code) AS (
              SELECT error_class
              FROM units
              WHERE owner_user_id=? AND revision_id=?
                AND error_class IS NOT NULL
              UNION ALL
              SELECT attention_code
              FROM stage_materialization
              WHERE owner_user_id=? AND revision_id=?
                AND attention_code IS NOT NULL
            )
            SELECT error_code, COUNT(*) AS count
            FROM error_facts
            GROUP BY error_code
            ORDER BY count DESC, error_code ASC
            LIMIT 20
            """,
            (owner, revision_id, owner, revision_id),
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
                {"error_code": str(row["error_code"]), "count": int(row["count"])}
                for row in errors
            ],
            "warnings": warnings,
        }

    def evaluate_completion(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        now: datetime | None = None,
    ) -> CompletionAssessment:
        """Apply all nine RM-0004 completion checks in one transaction."""
        owner = _require_owner(owner_user_id)
        current, current_text = self._operation_now(now)
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

            incomplete_materialization = int(connection.execute(
                """
                SELECT COUNT(*) FROM stage_materialization
                WHERE owner_user_id=? AND revision_id=?
                  AND (completed=0 OR attention_code IS NOT NULL)
                """,
                (owner, revision_id),
            ).fetchone()[0])
            if incomplete_materialization:
                block("materialization_incomplete")

            # 3. Required stages have their cardinality and terminal units.
            stage_rows = connection.execute(
                """
                SELECT stage.id, stage.stage_type, stage.cardinality,
                       stage.required_flag, COUNT(unit.id) AS unit_count
                FROM stages stage
                LEFT JOIN units unit
                  ON unit.owner_user_id=stage.owner_user_id
                 AND unit.revision_id=stage.revision_id
                 AND unit.stage_id=stage.id
                WHERE stage.owner_user_id=? AND stage.revision_id=?
                GROUP BY stage.owner_user_id, stage.id
                ORDER BY stage.position
                """,
                (owner, revision_id),
            ).fetchall()
            for stage in stage_rows:
                if not stage["required_flag"] or stage["stage_type"] == "inventory":
                    continue
                unit_count = int(stage["unit_count"])
                expected_units = (
                    int(revision["expected_source_count"])
                    if stage["cardinality"] == "per_source"
                    else (1 if stage["cardinality"] == "singleton" else 0)
                )
                if unit_count < expected_units:
                    block("required_units_missing")

            tolerated_json = str(revision["tolerated_error_classes_json"])
            accepted_partial = False
            unit_summary = connection.execute(
                """
                WITH unit_facts AS (
                    SELECT
                        unit.state, unit.error_class, unit.partial_output,
                        unit.committed_result_id,
                        unit.expected_dependency_count,
                        stage.required_flag,
                        CASE
                          WHEN unit.state='failed_permanent'
                           AND ?='declared'
                           AND unit.error_class IS NOT NULL
                           AND EXISTS (
                             SELECT 1
                             FROM json_each(?) tolerated
                             WHERE tolerated.value=unit.error_class
                           )
                          THEN 1 ELSE 0
                        END AS accepted_failure,
                        CASE
                          WHEN unit.committed_result_id IS NULL THEN 0
                          ELSE (
                            SELECT COUNT(*)
                            FROM dependencies dependency
                            WHERE dependency.owner_user_id=unit.owner_user_id
                              AND dependency.revision_id=unit.revision_id
                              AND dependency.child_result_id=
                                  unit.committed_result_id
                          )
                        END AS dependency_count
                    FROM units unit
                    JOIN stages stage
                      ON stage.owner_user_id=unit.owner_user_id
                     AND stage.revision_id=unit.revision_id
                     AND stage.id=unit.stage_id
                    WHERE unit.owner_user_id=? AND unit.revision_id=?
                )
                SELECT
                    COALESCE(SUM(CASE WHEN state NOT IN (
                        'committed', 'failed_permanent', 'skipped', 'cancelled'
                    ) THEN 1 ELSE 0 END), 0) AS nonterminal_count,
                    COALESCE(SUM(state='cancelled'), 0) AS cancelled_count,
                    COALESCE(SUM(
                        state='skipped' AND required_flag=1
                    ), 0) AS required_skipped_count,
                    COALESCE(SUM(accepted_failure), 0) AS accepted_failure_count,
                    COALESCE(SUM(
                        state='failed_permanent' AND accepted_failure=0
                    ), 0) AS untolerated_failure_count,
                    COALESCE(SUM(
                        state='committed' AND committed_result_id IS NULL
                    ), 0) AS missing_result_count,
                    COALESCE(SUM(
                        state='committed'
                        AND committed_result_id IS NOT NULL
                        AND dependency_count != expected_dependency_count
                    ), 0) AS dependency_deficit_count,
                    COALESCE(SUM(partial_output=1), 0) AS partial_count
                FROM unit_facts
                """,
                (
                    str(revision["failure_policy"]), tolerated_json,
                    owner, revision_id,
                ),
            ).fetchone()
            assert unit_summary is not None
            if int(unit_summary["nonterminal_count"]):
                block("units_not_terminal")
            if int(unit_summary["cancelled_count"]):
                block("cancelled_unit")
            if int(unit_summary["required_skipped_count"]):
                if revision["partial_output_accepted"]:
                    accepted_partial = True
                else:
                    block("required_unit_skipped")
            accepted_failures = int(unit_summary["accepted_failure_count"])
            if int(unit_summary["untolerated_failure_count"]):
                block("untolerated_unit_failure")
            if int(unit_summary["missing_result_count"]):
                block("committed_unit_without_result")
            if int(unit_summary["dependency_deficit_count"]):
                block("result_dependencies_unresolved")
            if int(unit_summary["partial_count"]):
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
                JOIN stages child_stage
                  ON child_stage.owner_user_id=child_unit.owner_user_id
                 AND child_stage.revision_id=child_unit.revision_id
                 AND child_stage.id=child_unit.stage_id
                JOIN stages source_stage
                  ON source_stage.owner_user_id=source_unit.owner_user_id
                 AND source_stage.revision_id=source_unit.revision_id
                 AND source_stage.id=source_unit.stage_id
                LEFT JOIN stages role_stage
                  ON role_stage.owner_user_id=d.owner_user_id
                 AND role_stage.revision_id=d.revision_id
                 AND role_stage.stage_key=d.role
                LEFT JOIN stage_dependencies declared
                  ON declared.owner_user_id=d.owner_user_id
                 AND declared.revision_id=d.revision_id
                 AND declared.stage_id=child_unit.stage_id
                 AND declared.depends_on_stage_id=role_stage.id
                WHERE d.owner_user_id=? AND d.revision_id=?
                  AND (
                    declared.stage_id IS NULL
                    OR NOT (
                      source_stage.id=role_stage.id
                      OR (
                        child_stage.reduction_fan_in IS NOT NULL
                        AND source_stage.id=child_stage.id
                        AND child_unit.reduction_level>0
                        AND source_unit.reduction_level=
                            child_unit.reduction_level - 1
                      )
                    )
                  )
                """,
                (owner, revision_id),
            ).fetchone()[0])
            if invalid_dependency_count:
                block("result_dependency_not_declared")

            # 6-7. Every named artifact exists in the required state and its
            # digest, schema and postconditions have been verified.
            required_artifacts = json.loads(revision["required_artifacts_json"])
            artifact_rows = connection.execute(
                """
                SELECT logical_name, state, digest_verified, schema_valid,
                       postconditions_valid, schema_version, mime_type
                FROM artifacts
                WHERE owner_user_id=? AND revision_id=?
                """,
                (owner, revision_id),
            ).fetchall()
            artifacts_by_name = {
                str(row["logical_name"]): row
                for row in artifact_rows
            }
            for requirement in required_artifacts:
                artifact = artifacts_by_name.get(str(requirement["name"]))
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
            budget_violation = self._budget_violation_in_transaction(
                connection,
                owner=owner,
                workload_id=workload_id,
                revision_id=revision_id,
                now=current,
            )
            if budget_violation is not None:
                block(str(budget_violation["reason_code"]))

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
            updated = connection.execute(
                """
                UPDATE workloads
                SET state=?, version=version+1, updated_at=?,
                    terminal_reason_json=?
                WHERE owner_user_id=? AND id=? AND state='running' AND version=?
                """,
                (
                    target.value, current_text,
                    canonical_json(
                        {"completion": "verified", "with_errors": has_errors},
                        max_bytes=MAX_EVENT_JSON_BYTES,
                    ),
                    owner, workload_id, workload.version,
                ),
            )
            if updated.rowcount != 1:
                raise VersionConflictError("completion compare-and-set failed")
            connection.execute(
                "DELETE FROM scheduler_credits "
                "WHERE owner_user_id=? AND workload_id=?",
                (owner, workload_id),
            )
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
    def _clock_regressed(high_water: object, current: datetime) -> bool:
        if high_water is None:
            return False
        return current + _CLOCK_REGRESSION_TOLERANCE < parse_instant(
            str(high_water), name="clock_high_water_at",
        )

    @staticmethod
    def _advance_revision_clock_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner: str,
        revision_id: str,
        now_text: str,
    ) -> None:
        updated = connection.execute(
            """
            UPDATE revision_usage
            SET clock_high_water_at=CASE
                  WHEN clock_high_water_at IS NULL
                    OR clock_high_water_at<? THEN ?
                  ELSE clock_high_water_at
                END
            WHERE owner_user_id=? AND revision_id=?
            """,
            (now_text, now_text, owner, revision_id),
        )
        if updated.rowcount != 1:
            raise DurableStoreError("revision usage row is missing")

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
    def _model_usage_is_complete(row: sqlite3.Row) -> bool:
        """Return whether a model attempt has a durable, exact usage fact."""

        model = json.loads(str(row["model_snapshot_json"]))
        if model.get("mode") != "llm":
            catalog = json.loads(str(row["catalog_snapshot_json"]))
            entries = catalog.get("entries", [])
            frozen_model_contract = any(
                isinstance(entry, Mapping)
                and entry.get("stage_key") == row["stage_key"]
                and entry.get("model_binding_digest") is not None
                for entry in entries
            )
            return not frozen_model_contract
        metrics = json.loads(str(row["metrics_json"]))
        usage = metrics.get("llm_usage")
        return (
            isinstance(usage, Mapping)
            and usage.get("schema_version") == "metnos.durable-model-usage/2"
            and usage.get("usage_missing") is False
            and usage.get("cost_unknown") is False
        )

    @classmethod
    def _mark_unaccounted_model_usage_unknown_in_transaction(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now_text: str,
    ) -> bool:
        """Poison a revision when an executed model attempt lacks exact usage.

        Once the model boundary has been entered, a missing ledger entry is
        indistinguishable from an unreported provider call. Persisting that
        uncertainty prevents later retries from presenting an unsafe token or
        cost remainder as authoritative.
        """

        model = json.loads(str(row["model_snapshot_json"]))
        if model.get("mode") != "llm" or cls._model_usage_is_complete(row):
            return False
        cls._add_revision_usage_in_transaction(
            connection,
            owner=str(row["owner_user_id"]),
            revision_id=str(row["revision_id"]),
            usage_unknown=True,
            now_text=now_text,
        )
        return True

    @staticmethod
    def _validated_result_dependencies_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        revision_id: str,
        stage_id: str,
        unit_id: str,
        expected_count: int,
        reduction_level: int | None,
        reduction_fan_in: int | None,
    ) -> tuple[sqlite3.Row, ...]:
        if expected_count > _MAX_UNIT_DEPENDENCIES:
            raise ResultContractError("result dependency count exceeds the bound")
        rows = connection.execute(
            """
            SELECT result.id AS result_id, planned.role,
                   physical.id AS physical_stage_id,
                   role_stage.id AS role_stage_id,
                   parent_unit.reduction_level AS parent_level,
                   declared.stage_id IS NOT NULL AS role_declared
            FROM unit_dependencies planned
            JOIN results result
              ON result.owner_user_id=planned.owner_user_id
             AND result.revision_id=planned.revision_id
             AND result.id=planned.source_result_id
            JOIN units parent_unit
              ON parent_unit.owner_user_id=result.owner_user_id
             AND parent_unit.id=result.unit_id
             AND parent_unit.revision_id=result.revision_id
            JOIN stages physical
              ON physical.owner_user_id=parent_unit.owner_user_id
             AND physical.id=parent_unit.stage_id
             AND physical.revision_id=parent_unit.revision_id
            JOIN stages role_stage
              ON role_stage.owner_user_id=planned.owner_user_id
             AND role_stage.revision_id=planned.revision_id
             AND role_stage.stage_key=planned.role
            LEFT JOIN stage_dependencies declared
              ON declared.owner_user_id=planned.owner_user_id
             AND declared.revision_id=planned.revision_id
             AND declared.stage_id=?
             AND declared.depends_on_stage_id=role_stage.id
            WHERE planned.owner_user_id=?
              AND planned.revision_id=? AND planned.unit_id=?
            ORDER BY planned.ordinal
            LIMIT ?
            """,
            (
                stage_id, owner_user_id, revision_id, unit_id,
                expected_count + 1,
            ),
        ).fetchall()
        if len(rows) != expected_count:
            raise ResultContractError("result dependency lineage is incomplete")
        for dependency in rows:
            direct = dependency["physical_stage_id"] == dependency["role_stage_id"]
            recursive = (
                reduction_fan_in is not None
                and reduction_level is not None
                and reduction_level > 0
                and dependency["physical_stage_id"] == stage_id
                and dependency["parent_level"] is not None
                and int(dependency["parent_level"]) == reduction_level - 1
            )
            if not bool(dependency["role_declared"]) or not (direct or recursive):
                raise ResultContractError(
                    "result dependency is outside the admitted graph"
                )
        return tuple(rows)

    @staticmethod
    def _insert_result_dependencies_in_transaction(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        revision_id: str,
        result_id: str,
        dependency_rows: Sequence[sqlite3.Row],
    ) -> None:
        role_ordinals: dict[str, int] = {}
        for dependency in dependency_rows:
            role = str(dependency["role"])
            ordinal = role_ordinals.get(role, 0)
            connection.execute(
                """
                INSERT INTO dependencies(
                    owner_user_id, revision_id, child_result_id,
                    source_result_id, role, ordinal
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_user_id, revision_id, result_id,
                    dependency["result_id"], role, ordinal,
                ),
            )
            role_ordinals[role] = ordinal + 1

    def _start_queued_workload_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        workload_id: str,
        workload_state: str,
        workload_version: int,
        now_text: str,
        reason_code: str,
    ) -> None:
        if workload_state != WorkloadState.QUEUED.value:
            return
        started = connection.execute(
            """
            UPDATE workloads
            SET state='running', version=version+1, updated_at=?
            WHERE owner_user_id=? AND id=? AND state='queued' AND version=?
            """,
            (now_text, owner_user_id, workload_id, workload_version),
        )
        if started.rowcount != 1:
            raise DurableStoreError("workload start compare-and-set failed")
        self.append_event_in_transaction(
            connection,
            owner_user_id=owner_user_id,
            workload_id=workload_id,
            event_type=EventType.RUNNING,
            payload={
                "previous_state": WorkloadState.QUEUED.value,
                "new_state": WorkloadState.RUNNING.value,
                "new_version": workload_version + 1,
                "reason_code": reason_code,
            },
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
                u.reduction_level, u.reduction_ordinal, u.reduction_root,
                u.manual_retry_tokens,
                u.lease_worker_id, u.active_attempt_id, u.fence,
                u.lease_expires_at, u.committed_result_id,
                r.workload_id,
                r.catalog_snapshot_json,
                usage.clock_high_water_at AS revision_clock_high_water_at,
                r.manual_retry_generation AS revision_retry_generation,
                w.priority AS workload_priority,
                s.stage_key, s.runner_kind, s.runner_name, s.effect_profile,
                s.input_bindings_json, s.output_schema_json, s.retry_json,
                s.resources_json, s.timeout_s, s.reduction_fan_in,
                s.reduction_input, s.reduction_max_input_bytes,
                placement.target_kind AS placement_target,
                placement.target_device AS placement_device,
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
            JOIN revision_usage usage
              ON usage.owner_user_id=r.owner_user_id
             AND usage.revision_id=r.id
            JOIN stages s
              ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
             AND s.revision_id=u.revision_id
            LEFT JOIN stage_placements placement
              ON placement.owner_user_id=s.owner_user_id
             AND placement.revision_id=s.revision_id
             AND placement.stage_id=s.id
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
        metrics = json.loads(str(row["metrics_json"]))
        manual_retry = metrics.get("manual_retry", False)
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
            and int(row["timeout_s"]) == lease.timeout_s
            and isinstance(manual_retry, bool)
            and manual_retry is lease.manual_retry
        )

    def adopt_reusable_results(self, *, limit: int = 200) -> int:
        """Commit prior results for unchanged pure units without re-execution.

        Reuse is owner-scoped, contract-exact and bounded. A semantic key with
        multiple historical digests moves to attention: latest-wins would hide
        nondeterminism or corruption.
        """

        _require_limit(limit, maximum=1000)
        progressed = 0
        for _index in range(limit):
            now_text = utc_now()
            with self._transaction() as connection:
                candidate = connection.execute(
                    """
                    SELECT
                        current_unit.owner_user_id,
                        current_unit.id AS unit_id,
                        current_unit.revision_id,
                        current_unit.stage_id,
                        current_unit.unit_key,
                        current_unit.attempt_count,
                        current_unit.fence,
                        current_unit.expected_dependency_count,
                        current_unit.reduction_level,
                        workload.id AS workload_id,
                        workload.state AS workload_state,
                        workload.version AS workload_version,
                        current_stage.stage_key,
                        current_stage.runner_kind,
                        current_stage.runner_name,
                        current_stage.output_schema_json,
                        current_stage.reduction_fan_in,
                        usage.output_bytes,
                        CAST(json_extract(
                            current_revision.plan_json,
                            '$.budgets.max_bytes_written'
                        ) AS INTEGER) AS output_limit,
                        prior_result.id AS prior_result_id,
                        prior_result.revision_id AS prior_revision_id,
                        prior_result.digest AS prior_digest,
                        prior_result.schema_version AS prior_schema_version,
                        prior_result.payload_json AS prior_payload_json,
                        prior_result.provenance_json AS prior_provenance_json
                    FROM units current_unit
                    JOIN revisions current_revision
                      ON current_revision.owner_user_id=current_unit.owner_user_id
                     AND current_revision.id=current_unit.revision_id
                    JOIN workloads workload
                      ON workload.owner_user_id=current_revision.owner_user_id
                     AND workload.id=current_revision.workload_id
                     AND workload.active_revision_id=current_revision.id
                    JOIN revision_usage usage
                      ON usage.owner_user_id=current_revision.owner_user_id
                     AND usage.revision_id=current_revision.id
                    JOIN stages current_stage
                      ON current_stage.owner_user_id=current_unit.owner_user_id
                     AND current_stage.revision_id=current_unit.revision_id
                     AND current_stage.id=current_unit.stage_id
                    JOIN units prior_unit
                      ON prior_unit.owner_user_id=current_unit.owner_user_id
                     AND prior_unit.unit_key=current_unit.unit_key
                     AND prior_unit.revision_id<>current_unit.revision_id
                     AND prior_unit.state='committed'
                    JOIN revisions prior_revision
                      ON prior_revision.owner_user_id=prior_unit.owner_user_id
                     AND prior_revision.id=prior_unit.revision_id
                     AND prior_revision.admitted_at IS NOT NULL
                    JOIN stages prior_stage
                      ON prior_stage.owner_user_id=prior_unit.owner_user_id
                     AND prior_stage.revision_id=prior_unit.revision_id
                     AND prior_stage.id=prior_unit.stage_id
                    JOIN results prior_result
                      ON prior_result.owner_user_id=prior_unit.owner_user_id
                     AND prior_result.revision_id=prior_unit.revision_id
                     AND prior_result.id=prior_unit.committed_result_id
                     AND prior_result.payload_json IS NOT NULL
                    WHERE workload.state IN ('queued', 'running')
                      AND current_unit.state='pending'
                      AND current_unit.attempt_count=0
                      AND current_unit.active_attempt_id IS NULL
                      AND current_stage.effect_profile='pure'
                      AND current_unit.expected_dependency_count <= ?
                      AND current_unit.expected_dependency_count=(
                        SELECT COUNT(*) FROM unit_dependencies dependency
                        WHERE dependency.owner_user_id=current_unit.owner_user_id
                          AND dependency.revision_id=current_unit.revision_id
                          AND dependency.unit_id=current_unit.id
                      )
                      AND prior_stage.stage_key=current_stage.stage_key
                      AND prior_stage.stage_type=current_stage.stage_type
                      AND prior_stage.runner_kind=current_stage.runner_kind
                      AND prior_stage.runner_name=current_stage.runner_name
                      AND prior_stage.effect_profile=current_stage.effect_profile
                      AND prior_stage.output_schema_json=current_stage.output_schema_json
                      AND prior_stage.invalidation_digest
                          IS current_stage.invalidation_digest
                      AND prior_stage.reduction_fan_in
                          IS current_stage.reduction_fan_in
                      AND prior_stage.reduction_input
                          IS current_stage.reduction_input
                      AND prior_stage.reduction_max_input_bytes
                          IS current_stage.reduction_max_input_bytes
                    ORDER BY workload.updated_at, workload.owner_user_id,
                             workload.id, current_unit.created_at,
                             current_unit.unit_key, prior_result.committed_at,
                             prior_result.id
                    LIMIT 1
                    """,
                    (_MAX_UNIT_DEPENDENCIES,),
                ).fetchone()
                if candidate is None:
                    break

                owner = str(candidate["owner_user_id"])
                revision_id = str(candidate["revision_id"])
                workload_id = str(candidate["workload_id"])
                unit_id = str(candidate["unit_id"])

                budget_violation = self._budget_violation_in_transaction(
                    connection,
                    owner=owner,
                    workload_id=workload_id,
                    revision_id=revision_id,
                    now=parse_instant(now_text, name="now"),
                )
                if budget_violation is not None:
                    _workload, settled = self._settle_workload_in_transaction(
                        connection,
                        owner,
                        workload_id,
                        now_text=now_text,
                    )
                    if settled < 1:
                        raise DurableStoreError(
                            "budget violation did not settle the workload"
                        )
                    progressed += settled
                    continue

                def require_attention(reason_code: str) -> None:
                    detail = canonical_json(
                        {
                            "schema_version": "metnos.durable-unit-terminal/1",
                            "reason_code": reason_code,
                        },
                        max_bytes=MAX_EVENT_JSON_BYTES,
                    )
                    changed = connection.execute(
                        """
                        UPDATE units
                        SET state='needs_attention', error_class=?,
                            terminal_detail_json=?, updated_at=?
                        WHERE owner_user_id=? AND id=? AND revision_id=?
                          AND state='pending' AND attempt_count=0
                          AND active_attempt_id IS NULL
                        """,
                        (
                            reason_code, detail, now_text,
                            owner, unit_id, revision_id,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise DurableStoreError(
                            "semantic reuse attention compare-and-set failed"
                        )
                    self._settle_workload_in_transaction(
                        connection, owner, workload_id, now_text=now_text,
                    )

                digest_bounds = connection.execute(
                    """
                    SELECT MIN(result.digest) AS minimum_digest,
                           MAX(result.digest) AS maximum_digest
                    FROM units prior_unit
                    JOIN stages prior_stage
                      ON prior_stage.owner_user_id=prior_unit.owner_user_id
                     AND prior_stage.revision_id=prior_unit.revision_id
                     AND prior_stage.id=prior_unit.stage_id
                    JOIN results result
                      ON result.owner_user_id=prior_unit.owner_user_id
                     AND result.revision_id=prior_unit.revision_id
                     AND result.id=prior_unit.committed_result_id
                    JOIN stages current_stage
                      ON current_stage.owner_user_id=?
                     AND current_stage.revision_id=?
                     AND current_stage.id=?
                    WHERE prior_unit.owner_user_id=?
                      AND prior_unit.unit_key=?
                      AND prior_unit.revision_id<>?
                      AND prior_unit.state='committed'
                      AND prior_stage.stage_key=current_stage.stage_key
                      AND prior_stage.stage_type=current_stage.stage_type
                      AND prior_stage.runner_kind=current_stage.runner_kind
                      AND prior_stage.runner_name=current_stage.runner_name
                      AND prior_stage.effect_profile=current_stage.effect_profile
                      AND prior_stage.output_schema_json=current_stage.output_schema_json
                      AND prior_stage.invalidation_digest
                          IS current_stage.invalidation_digest
                      AND prior_stage.reduction_fan_in
                          IS current_stage.reduction_fan_in
                      AND prior_stage.reduction_input
                          IS current_stage.reduction_input
                      AND prior_stage.reduction_max_input_bytes
                          IS current_stage.reduction_max_input_bytes
                    """,
                    (
                        owner, revision_id, candidate["stage_id"], owner,
                        candidate["unit_key"], revision_id,
                    ),
                ).fetchone()
                assert digest_bounds is not None
                if (
                    digest_bounds["minimum_digest"]
                    != digest_bounds["maximum_digest"]
                ):
                    require_attention("result_digest_conflict")
                    progressed += 1
                    continue

                try:
                    validated = ValidatedResult(
                        schema_version=str(candidate["prior_schema_version"]),
                        payload_json=str(candidate["prior_payload_json"]),
                        digest=str(candidate["prior_digest"]),
                    )
                    output_schema = json.loads(
                        str(candidate["output_schema_json"])
                    )["name"]
                except (KeyError, TypeError, ValueError, SchemaValidationError):
                    require_attention("reusable_result_invalid")
                    progressed += 1
                    continue
                if validated.schema_version != output_schema:
                    require_attention("reusable_result_invalid")
                    progressed += 1
                    continue

                payload_bytes = len(validated.payload_json.encode("utf-8"))
                if int(candidate["output_bytes"]) + payload_bytes > int(
                    candidate["output_limit"]
                ):
                    require_attention("budget_limit_exceeded")
                    progressed += 1
                    continue

                dependency_rows = self._validated_result_dependencies_in_transaction(
                    connection,
                    owner_user_id=owner,
                    revision_id=revision_id,
                    stage_id=str(candidate["stage_id"]),
                    unit_id=unit_id,
                    expected_count=int(candidate["expected_dependency_count"]),
                    reduction_level=(
                        None if candidate["reduction_level"] is None
                        else int(candidate["reduction_level"])
                    ),
                    reduction_fan_in=(
                        None if candidate["reduction_fan_in"] is None
                        else int(candidate["reduction_fan_in"])
                    ),
                )
                attempt_number = int(candidate["attempt_count"]) + 1
                fence = int(candidate["fence"]) + 1
                attempt_id = _new_id("att")
                result_id = _new_id("res")
                executor_snapshot = {
                    "schema_version": "metnos.durable-executor-snapshot/1",
                    "mode": "result_reuse",
                    "runner_kind": str(candidate["runner_kind"]),
                    "runner_name": str(candidate["runner_name"]),
                }
                model_snapshot = {
                    "schema_version": "metnos.durable-model-snapshot/1",
                    "mode": "result_reuse",
                    "binding": None,
                }
                metrics = {
                    "schema_version": "metnos.durable-attempt-metrics/1",
                    "attempt_number": attempt_number,
                    "execution_started": False,
                    "manual_retry": False,
                    "committed": True,
                    "dependency_count": len(dependency_rows),
                    "result_digest": validated.digest,
                    "result_reused": True,
                    "usage_missing": False,
                }
                source_provenance = json.loads(
                    str(candidate["prior_provenance_json"])
                )
                provenance = canonical_json(
                    {
                        "schema_version": "metnos.durable-result-provenance/1",
                        "attempt_id": attempt_id,
                        "fence": fence,
                        "runner_kind": str(candidate["runner_kind"]),
                        "runner_name": str(candidate["runner_name"]),
                        "validation": "reused_validated_result",
                        "output_schema": validated.schema_version,
                        "source_result_id": str(candidate["prior_result_id"]),
                        "source_revision_id": str(candidate["prior_revision_id"]),
                        "source_result_digest": validated.digest,
                        "source_provenance_digest": digest_json(
                            "durable-result-provenance",
                            source_provenance,
                            max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                        ),
                        "executor_snapshot_digest": digest_json(
                            "durable-executor-snapshot",
                            executor_snapshot,
                            max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                        ),
                        "model_snapshot_digest": digest_json(
                            "durable-model-snapshot",
                            model_snapshot,
                            max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                        ),
                        "metrics_digest": digest_json(
                            "durable-attempt-metrics",
                            metrics,
                            max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
                        ),
                        "usage_missing": False,
                    },
                    max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                )
                connection.execute(
                    """
                    INSERT INTO attempts(
                        owner_user_id, id, unit_id, number, fence, worker_id,
                        state, started_at, ended_at, executor_snapshot_json,
                        model_snapshot_json, metrics_json
                    ) VALUES (?, ?, ?, ?, ?, 'result-reuse', 'succeeded',
                              ?, ?, ?, ?, ?)
                    """,
                    (
                        owner, attempt_id, unit_id, attempt_number, fence,
                        now_text, now_text,
                        canonical_json(
                            executor_snapshot, max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                        ),
                        canonical_json(
                            model_snapshot, max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                        ),
                        canonical_json(
                            metrics, max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
                        ),
                    ),
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
                        owner, result_id, revision_id, unit_id, attempt_id,
                        fence, validated.digest, validated.schema_version,
                        validated.payload_json, provenance, now_text,
                    ),
                )
                self._insert_result_dependencies_in_transaction(
                    connection,
                    owner_user_id=owner,
                    revision_id=revision_id,
                    result_id=result_id,
                    dependency_rows=dependency_rows,
                )
                changed = connection.execute(
                    """
                    UPDATE units
                    SET state='committed', attempt_count=?, fence=?,
                        committed_result_id=?, next_attempt_at=NULL,
                        error_class=NULL, partial_output=0,
                        terminal_detail_json=NULL, updated_at=?
                    WHERE owner_user_id=? AND id=? AND revision_id=?
                      AND state='pending' AND attempt_count=? AND fence=?
                      AND active_attempt_id IS NULL
                    """,
                    (
                        attempt_number, fence, result_id, now_text,
                        owner, unit_id, revision_id,
                        candidate["attempt_count"], candidate["fence"],
                    ),
                )
                if changed.rowcount != 1:
                    raise DurableStoreError("semantic reuse compare-and-set failed")
                self._add_revision_usage_in_transaction(
                    connection,
                    owner=owner,
                    revision_id=revision_id,
                    output_bytes=payload_bytes,
                    now_text=now_text,
                )
                self._start_queued_workload_in_transaction(
                    connection,
                    owner_user_id=owner,
                    workload_id=workload_id,
                    workload_state=str(candidate["workload_state"]),
                    workload_version=int(candidate["workload_version"]),
                    now_text=now_text,
                    reason_code="first_result_reused",
                )
                self._settle_workload_in_transaction(
                    connection, owner, workload_id, now_text=now_text,
                )
                progressed += 1
        return progressed

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
            candidate = connection.execute(
                f"""
                WITH candidate AS (
                    SELECT
                        w.owner_user_id, w.id AS workload_id,
                        w.state AS workload_state, w.version AS workload_version,
                        w.priority,
                        w.created_at AS workload_created_at,
                        r.id AS revision_id,
                        COALESCE((
                          SELECT MAX(owner_scope.last_selected_seq)
                          FROM scheduler_credits owner_scope
                          WHERE owner_scope.owner_user_id=w.owner_user_id
                        ), 0) AS owner_selected_seq,
                        COALESCE(sc.last_selected_seq, 0) AS selected_seq,
                        (
                            SELECT s.id
                            FROM stages s
                            WHERE s.owner_user_id=w.owner_user_id
                              AND s.revision_id=r.id
                              AND EXISTS (
                                SELECT 1 FROM units ready_unit
                                WHERE ready_unit.owner_user_id=s.owner_user_id
                                  AND ready_unit.revision_id=s.revision_id
                                  AND ready_unit.stage_id=s.id
                                  AND (
                                    (
                                      ready_unit.state='pending'
                                      AND (
                                        ready_unit.next_attempt_at IS NULL
                                        OR ready_unit.next_attempt_at<=?
                                      )
                                      AND (
                                        ready_unit.attempt_count < CAST(
                                          json_extract(
                                            s.retry_json, '$.max_attempts'
                                          ) AS INTEGER
                                        )
                                        OR ready_unit.manual_retry_tokens > 0
                                      )
                                    )
                                    OR (
                                      ready_unit.state='needs_attention'
                                      AND ready_unit.manual_retry_generation <
                                          r.manual_retry_generation
                                    )
                                  )
                                  AND ready_unit.expected_dependency_count <= ?
                                  AND ready_unit.expected_dependency_count = (
                                    SELECT COUNT(*)
                                    FROM unit_dependencies ready_dependency
                                    WHERE ready_dependency.owner_user_id=
                                            ready_unit.owner_user_id
                                      AND ready_dependency.revision_id=
                                            ready_unit.revision_id
                                      AND ready_dependency.unit_id=ready_unit.id
                                  )
                              )
                              AND s.effect_profile IN ({effect_clause})
                              AND ({runner_clause})
                              AND CAST(
                                json_extract(s.resources_json, '$.cpu') AS INTEGER
                              )<=?
                              AND CAST(
                                json_extract(s.resources_json, '$.device') AS INTEGER
                              )<=?
                              AND CAST(
                                json_extract(s.resources_json, '$.llm') AS INTEGER
                              )<=?
                              AND CAST(
                                json_extract(s.resources_json, '$.local_io') AS INTEGER
                              )<=?
                              AND CAST(
                                json_extract(s.resources_json, '$.network_io') AS INTEGER
                              )<=?
                              AND CAST(
                                json_extract(s.resources_json, '$.vlm') AS INTEGER
                              )<=?
                              AND (
                                (
                                  CAST(json_extract(
                                    s.resources_json, '$.llm'
                                  ) AS INTEGER)=0
                                  AND CAST(json_extract(
                                    s.resources_json, '$.vlm'
                                  ) AS INTEGER)=0
                                )
                                OR (
                                  revision_usage.input_tokens
                                    + revision_usage.output_tokens
                                    < CAST(json_extract(
                                      r.plan_json, '$.budgets.max_tokens'
                                    ) AS INTEGER)
                                  AND (
                                    CAST(json_extract(
                                      r.plan_json,
                                      '$.budgets.max_cost_micros'
                                    ) AS INTEGER)=0
                                    OR revision_usage.cost_micros
                                      < CAST(json_extract(
                                        r.plan_json,
                                        '$.budgets.max_cost_micros'
                                      ) AS INTEGER)
                                  )
                                  AND NOT EXISTS (
                                    SELECT 1
                                    FROM units active_model_unit
                                    JOIN stages active_model_stage
                                      ON active_model_stage.owner_user_id=
                                           active_model_unit.owner_user_id
                                     AND active_model_stage.revision_id=
                                           active_model_unit.revision_id
                                     AND active_model_stage.id=
                                           active_model_unit.stage_id
                                    WHERE active_model_unit.owner_user_id=
                                            r.owner_user_id
                                      AND active_model_unit.revision_id=r.id
                                      AND active_model_unit.state IN (
                                        'leased', 'running'
                                      )
                                      AND (
                                        CAST(json_extract(
                                          active_model_stage.resources_json,
                                          '$.llm'
                                        ) AS INTEGER)>0
                                        OR CAST(json_extract(
                                          active_model_stage.resources_json,
                                          '$.vlm'
                                        ) AS INTEGER)>0
                                      )
                                  )
                                )
                              )
                            ORDER BY s.position, s.id
                            LIMIT 1
                        ) AS stage_id
                    FROM workloads w
                    JOIN revisions r
                      ON r.owner_user_id=w.owner_user_id
                     AND r.id=w.active_revision_id
                    JOIN revision_usage revision_usage
                      ON revision_usage.owner_user_id=r.owner_user_id
                     AND revision_usage.revision_id=r.id
                    LEFT JOIN scheduler_credits sc
                      ON sc.owner_user_id=w.owner_user_id
                     AND sc.workload_id=w.id
                    WHERE w.state IN ('queued', 'running')
                      AND revision_usage.usage_unknown=0
                      AND (
                        revision_usage.clock_high_water_at IS NULL
                        OR julianday(revision_usage.clock_high_water_at)
                            <= julianday(?) + (? / 86400.0)
                      )
                      AND revision_usage.input_bytes <= CAST(json_extract(
                        r.plan_json, '$.budgets.max_bytes_read'
                      ) AS INTEGER)
                      AND revision_usage.output_bytes <= CAST(json_extract(
                        r.plan_json, '$.budgets.max_bytes_written'
                      ) AS INTEGER)
                      AND revision_usage.input_tokens <= CAST(json_extract(
                        r.plan_json, '$.budgets.max_tokens'
                      ) AS INTEGER) - revision_usage.output_tokens
                      AND revision_usage.cost_micros <= CAST(json_extract(
                        r.plan_json, '$.budgets.max_cost_micros'
                      ) AS INTEGER)
                      AND revision_usage.artifact_count <= CAST(json_extract(
                        r.plan_json, '$.budgets.max_artifacts'
                      ) AS INTEGER)
                      AND (
                        revision_usage.started_at IS NULL
                        OR (julianday(?) - julianday(revision_usage.started_at))
                           * 86400 < CAST(json_extract(
                             r.plan_json, '$.budgets.max_wall_time_s'
                           ) AS INTEGER)
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM units failed_unit
                        WHERE failed_unit.owner_user_id=w.owner_user_id
                          AND failed_unit.revision_id=r.id
                          AND failed_unit.state='failed_permanent'
                          AND (
                            r.failure_policy<>'declared'
                            OR failed_unit.error_class IS NULL
                            OR failed_unit.error_class NOT IN (
                              SELECT value
                              FROM json_each(r.tolerated_error_classes_json)
                            )
                          )
                      )
                      AND (
                        SELECT COUNT(*)
                        FROM units active_unit
                        WHERE active_unit.owner_user_id=w.owner_user_id
                          AND active_unit.revision_id=r.id
                          AND active_unit.state IN ('leased', 'running')
                      ) < CAST(
                        json_extract(r.plan_json, '$.budgets.max_concurrency')
                        AS INTEGER
                      )
                )
                SELECT * FROM candidate
                WHERE stage_id IS NOT NULL
                ORDER BY owner_selected_seq, selected_seq,
                         CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                         workload_created_at, owner_user_id, workload_id
                LIMIT 1
                """,
                (
                    current_text,
                    _MAX_UNIT_DEPENDENCIES,
                    *capabilities.accepted_effects(),
                    *runner_parameters,
                    limits["cpu"], limits["device"], limits["llm"],
                    limits["local_io"], limits["network_io"], limits["vlm"],
                    current_text,
                    _CLOCK_REGRESSION_TOLERANCE.total_seconds(),
                    current_text,
                ),
            ).fetchone()
            if candidate is None:
                return None

            row = connection.execute(
                """
                SELECT
                    u.owner_user_id, u.id AS unit_id, u.revision_id,
                    u.stage_id, u.unit_key, u.attempt_count,
                    u.state AS unit_state, u.manual_retry_tokens,
                    u.manual_retry_generation, u.fence,
                    r.manual_retry_generation AS revision_retry_generation,
                    w.id AS workload_id, w.state AS workload_state,
                    w.version AS workload_version, w.priority,
                    w.created_at AS workload_created_at,
                    s.stage_key, s.position, s.runner_kind, s.runner_name,
                    s.effect_profile, s.output_schema_json, s.retry_json,
                    s.resources_json, s.timeout_s
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
                WHERE u.owner_user_id=? AND u.revision_id=? AND u.stage_id=?
                  AND (
                    (
                      u.state='pending'
                      AND (u.next_attempt_at IS NULL OR u.next_attempt_at<=?)
                      AND (
                        u.attempt_count < CAST(
                          json_extract(s.retry_json, '$.max_attempts') AS INTEGER
                        )
                        OR u.manual_retry_tokens > 0
                      )
                    )
                    OR (
                      u.state='needs_attention'
                      AND u.manual_retry_generation <
                          r.manual_retry_generation
                    )
                  )
                  AND u.expected_dependency_count <= ?
                  AND u.expected_dependency_count = (
                    SELECT COUNT(*) FROM unit_dependencies dependency
                    WHERE dependency.owner_user_id=u.owner_user_id
                      AND dependency.revision_id=u.revision_id
                      AND dependency.unit_id=u.id
                  )
                ORDER BY u.next_attempt_at, u.created_at, u.unit_key
                LIMIT 1
                """,
                (
                    candidate["owner_user_id"], candidate["revision_id"],
                    candidate["stage_id"], current_text,
                    _MAX_UNIT_DEPENDENCIES,
                ),
            ).fetchone()
            if row is None:
                raise DurableStoreError("ready stage has no claimable unit")

            attempt_number = int(row["attempt_count"]) + 1
            fence = int(row["fence"]) + 1
            attempt_id = _new_id("att")
            retry_policy = RetryPolicy.from_mapping(
                json.loads(str(row["retry_json"]))
            )
            attention_retry = row["unit_state"] == "needs_attention"
            token_retry = (
                row["unit_state"] == "pending"
                and int(row["manual_retry_tokens"]) > 0
            )
            manual_retry = attention_retry or token_retry
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
                    "manual_retry": manual_retry,
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
                    manual_retry_tokens=manual_retry_tokens-?,
                    manual_retry_generation=CASE
                      WHEN state='needs_attention' THEN ?
                      ELSE manual_retry_generation
                    END,
                    error_class=NULL, partial_output=0,
                    terminal_detail_json=NULL,
                    next_attempt_at=NULL, updated_at=?
                WHERE owner_user_id=? AND id=? AND state=?
                  AND attempt_count=? AND fence=?
                  AND (?=0 OR manual_retry_tokens>0)
                  AND (
                    ?=0 OR manual_retry_generation<?
                  )
                """,
                (
                    attempt_number, worker, attempt_id, fence, expiry_text,
                    int(token_retry), row["revision_retry_generation"],
                    current_text, row["owner_user_id"], row["unit_id"],
                    row["unit_state"], row["attempt_count"], row["fence"],
                    int(token_retry), int(attention_retry),
                    row["revision_retry_generation"],
                ),
            )
            if updated.rowcount != 1:
                raise DurableStoreError("claim compare-and-set failed")
            wall_started_at = current_text
            started_usage = connection.execute(
                """
                UPDATE revision_usage
                SET started_at=COALESCE(started_at, ?),
                    clock_high_water_at=CASE
                      WHEN clock_high_water_at IS NULL
                        OR clock_high_water_at<? THEN ?
                      ELSE clock_high_water_at
                    END,
                    updated_at=?
                WHERE owner_user_id=? AND revision_id=?
                """,
                (
                    wall_started_at, wall_started_at, wall_started_at,
                    wall_started_at,
                    row["owner_user_id"], row["revision_id"],
                ),
            )
            if started_usage.rowcount != 1:
                raise DurableStoreError("revision usage row is missing")

            self._start_queued_workload_in_transaction(
                connection,
                owner_user_id=str(row["owner_user_id"]),
                workload_id=str(row["workload_id"]),
                workload_state=str(row["workload_state"]),
                workload_version=int(row["workload_version"]),
                now_text=current_text,
                reason_code="first_unit_claimed",
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
                retry_policy=retry_policy,
                timeout_s=int(row["timeout_s"]),
                manual_retry=manual_retry,
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
            if self._clock_regressed(
                row["revision_clock_high_water_at"], current,
            ):
                return LeaseMutationStatus.LEASE_EXPIRED
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            if row["unit_state"] == "running" and row["attempt_state"] == "running":
                return LeaseMutationStatus.ALREADY_APPLIED
            if row["unit_state"] != "leased" or row["attempt_state"] != "leased":
                return LeaseMutationStatus.INVALID_STATE
            stored_expiry = parse_instant(str(row["lease_expires_at"]))
            execution_deadline = current + timedelta(seconds=lease.timeout_s)
            running_expiry_text = instant_text(
                min(stored_expiry, execution_deadline),
                name="running lease expiry",
            )
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
                UPDATE units
                SET state='running', lease_expires_at=?, updated_at=?
                WHERE owner_user_id=? AND id=? AND active_attempt_id=?
                  AND fence=? AND lease_worker_id=? AND state='leased'
                """,
                (
                    running_expiry_text, current_text,
                    lease.owner_user_id, lease.unit_id,
                    lease.attempt_id, lease.fence, lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("mark-running compare-and-set failed")
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )
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
            if self._clock_regressed(
                row["revision_clock_high_water_at"], current,
            ):
                return LeaseMutationStatus.LEASE_EXPIRED
            if row["unit_state"] not in {"leased", "running"}:
                return LeaseMutationStatus.INVALID_STATE
            stored_expiry = parse_instant(str(row["lease_expires_at"]))
            if stored_expiry <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            if row["unit_state"] == "running":
                metrics = json.loads(str(row["metrics_json"]))
                execution_started_at = metrics.get("execution_started_at")
                if not isinstance(execution_started_at, str):
                    return LeaseMutationStatus.INVALID_STATE
                execution_deadline = parse_instant(
                    execution_started_at,
                    name="execution_started_at",
                ) + timedelta(seconds=lease.timeout_s)
                if expiry > execution_deadline:
                    raise ValueError(
                        "new_expiry cannot exceed the frozen execution deadline"
                    )
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
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )
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
            dependency_summary = connection.execute(
                """
                SELECT COUNT(*) AS dependency_count,
                       COALESCE(SUM(length(CAST(
                         CASE
                           WHEN ? IS NOT NULL THEN json_object(
                             'entries',
                             json_extract(result.payload_json, '$.entries')
                           )
                           WHEN planned.entry_offset IS NULL
                           THEN result.payload_json
                           ELSE substr(
                             result.payload_json,
                             planned.entry_offset + 1,
                             planned.entry_length
                           )
                         END AS BLOB
                       ))), 0) AS payload_bytes
                FROM unit_dependencies planned
                JOIN results result
                  ON result.owner_user_id=planned.owner_user_id
                 AND result.revision_id=planned.revision_id
                 AND result.id=planned.source_result_id
                WHERE planned.owner_user_id=?
                  AND planned.revision_id=?
                  AND planned.unit_id=?
                """,
                (
                    row["reduction_fan_in"], lease.owner_user_id,
                    lease.revision_id, lease.unit_id,
                ),
            ).fetchone()
            assert dependency_summary is not None
            expected_dependency_count = int(row["expected_dependency_count"])
            dependency_count = int(dependency_summary["dependency_count"])
            dependency_bytes = int(dependency_summary["payload_bytes"])
            if (
                expected_dependency_count > _MAX_UNIT_DEPENDENCIES
                or dependency_count != expected_dependency_count
            ):
                raise DurableStoreError(
                    "execution dependency lineage is incomplete or oversized"
                )
            dependency_byte_limit = _MAX_DEPENDENCY_INPUT_BYTES
            if row["reduction_max_input_bytes"] is not None:
                dependency_byte_limit = min(
                    _MAX_DEPENDENCY_INPUT_BYTES,
                    int(row["reduction_max_input_bytes"])
                    + 32 * expected_dependency_count,
                )
            if dependency_bytes > dependency_byte_limit:
                raise DurableStoreError(
                    "execution dependency payload exceeds the bounded input"
                )
            dependencies = connection.execute(
                """
                SELECT planned.role AS stage_key,
                       result.id AS result_id, result.digest,
                       result.schema_version,
                       CASE
                         WHEN ? IS NOT NULL THEN json_object(
                           'entries',
                           json_extract(result.payload_json, '$.entries')
                         )
                         WHEN planned.entry_offset IS NULL
                         THEN result.payload_json
                         ELSE substr(
                           result.payload_json,
                           planned.entry_offset + 1,
                           planned.entry_length
                         )
                       END AS payload_json,
                       planned.entry_offset IS NOT NULL AS entry_selected,
                       parent_unit.source_row_id,
                       source.source_id, source.ordinal AS source_ordinal
                FROM unit_dependencies planned
                JOIN results result
                  ON result.owner_user_id=planned.owner_user_id
                 AND result.revision_id=planned.revision_id
                 AND result.id=planned.source_result_id
                JOIN units parent_unit
                  ON parent_unit.owner_user_id=result.owner_user_id
                 AND parent_unit.revision_id=result.revision_id
                 AND parent_unit.id=result.unit_id
                JOIN stages parent
                  ON parent.owner_user_id=parent_unit.owner_user_id
                 AND parent.revision_id=parent_unit.revision_id
                 AND parent.id=parent_unit.stage_id
                LEFT JOIN sources source
                  ON source.owner_user_id=parent_unit.owner_user_id
                 AND source.id=parent_unit.source_row_id
                 AND source.revision_id=parent_unit.revision_id
                WHERE planned.owner_user_id=?
                  AND planned.revision_id=?
                  AND planned.unit_id=?
                ORDER BY planned.ordinal
                LIMIT ?
                """,
                (
                    row["reduction_fan_in"], lease.owner_user_id,
                    lease.revision_id, lease.unit_id,
                    _MAX_UNIT_DEPENDENCIES + 1,
                ),
            ).fetchall()
            if len(dependencies) != expected_dependency_count:
                raise DurableStoreError(
                    "execution dependency lineage is incomplete or oversized"
                )
            input_bindings = json.loads(str(row["input_bindings_json"]))
            literal_bytes = sum(
                len(canonical_json(
                    reference["value"], max_bytes=MAX_PLAN_JSON_BYTES,
                ).encode("utf-8"))
                for reference in input_bindings.values()
                if isinstance(reference, Mapping)
                and reference.get("ref") == "literal"
            )
            needs_inventory = any(
                isinstance(reference, Mapping)
                and reference.get("ref") == "revision.inventory"
                for reference in input_bindings.values()
            )
            inventory = None
            inventory_bytes = 0
            if needs_inventory:
                inventory_row = connection.execute(
                    """
                    SELECT inventory_json FROM revisions
                    WHERE owner_user_id=? AND id=? AND workload_id=?
                    """,
                    (
                        lease.owner_user_id, lease.revision_id,
                        lease.workload_id,
                    ),
                ).fetchone()
                if inventory_row is None or inventory_row["inventory_json"] is None:
                    raise DurableStoreError(
                        "execution inventory is not sealed"
                    )
                inventory_text = str(inventory_row["inventory_json"])
                inventory_bytes = len(inventory_text.encode("utf-8"))
                inventory = json.loads(inventory_text)
            catalog_rows = connection.execute(
                """
                SELECT entry.value AS entry_json
                FROM revisions revision,
                     json_each(revision.catalog_snapshot_json, '$.entries') entry
                WHERE revision.owner_user_id=? AND revision.id=?
                  AND revision.workload_id=?
                  AND json_extract(entry.value, '$.stage_key')=?
                LIMIT 2
                """,
                (
                    lease.owner_user_id, lease.revision_id,
                    lease.workload_id, row["stage_key"],
                ),
            ).fetchall()
            if len(catalog_rows) > 1:
                raise DurableStoreError(
                    "execution catalog snapshot has duplicate stage contracts"
                )
            catalog_entries = [
                json.loads(str(item["entry_json"])) for item in catalog_rows
            ]
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
            source_bytes = 0
            if source is not None:
                needs_source_path = any(
                    isinstance(reference, Mapping)
                    and reference.get("ref") == "source.path"
                    for reference in input_bindings.values()
                )
                needs_source_record = any(
                    isinstance(reference, Mapping)
                    and reference.get("ref") == "source.record"
                    for reference in input_bindings.values()
                )
                if needs_source_path:
                    source_bytes += int(source["size_bytes"])
                if needs_source_record:
                    source_bytes += len(canonical_json(
                        source, max_bytes=MAX_EVENT_JSON_BYTES,
                    ).encode("utf-8"))
            input_bytes = (
                inventory_bytes + source_bytes + dependency_bytes
                + literal_bytes
            )
            attempt_metrics = json.loads(str(row["metrics_json"]))
            already_accounted = attempt_metrics.get(
                "input_bytes_accounted", False,
            )
            if already_accounted:
                if attempt_metrics.get("input_bytes") != input_bytes:
                    raise DurableStoreError(
                        "attempt input accounting changed for the active fence"
                    )
            else:
                metrics = self._attempt_metrics(
                    row,
                    input_bytes=input_bytes,
                    input_bytes_accounted=True,
                )
                updated_attempt = connection.execute(
                    """
                    UPDATE attempts
                    SET metrics_json=?
                    WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                      AND worker_id=? AND state IN ('leased', 'running')
                      AND ended_at IS NULL
                    """,
                    (
                        metrics, lease.owner_user_id, lease.attempt_id,
                        lease.unit_id, lease.fence, lease.worker_id,
                    ),
                )
                if updated_attempt.rowcount != 1:
                    raise DurableStoreError(
                        "attempt input accounting compare-and-set failed"
                    )
                self._add_revision_usage_in_transaction(
                    connection,
                    owner=lease.owner_user_id,
                    revision_id=lease.revision_id,
                    input_bytes=input_bytes,
                    now_text=utc_now(),
                )
            return {
                "inventory": inventory,
                "priority": str(row["workload_priority"]),
                "execution_started_at": attempt_metrics.get(
                    "execution_started_at"
                ),
                "catalog_snapshot": {
                    "schema_version": "metnos.catalog-snapshot/1",
                    "entries": catalog_entries,
                },
                "stage": {
                    "id": str(row["stage_id"]),
                    "key": str(row["stage_key"]),
                    "source_row_id": row["source_row_id"],
                    "shard_key": row["shard_key"],
                    "runner_kind": str(row["runner_kind"]),
                    "runner_name": str(row["runner_name"]),
                    "effect_profile": str(row["effect_profile"]),
                    "placement_target": row["placement_target"],
                    "placement_device": row["placement_device"],
                    "input_bindings": input_bindings,
                    "output_schema": json.loads(str(row["output_schema_json"])),
                    "timeout_s": int(row["timeout_s"]),
                    "reduction_fan_in": row["reduction_fan_in"],
                    "reduction_input": row["reduction_input"],
                    "reduction_max_input_bytes":
                        row["reduction_max_input_bytes"],
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
                    "entry_selected": bool(item["entry_selected"]),
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
            if self._clock_regressed(
                row["revision_clock_high_water_at"], current,
            ):
                return LeaseMutationStatus.LEASE_EXPIRED
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
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )
            return LeaseMutationStatus.APPLIED

    def record_attempt_usage(
        self,
        lease: Lease,
        usage: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> LeaseMutationStatus:
        """Store one idempotent, bounded and content-free LLM usage summary."""
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if not isinstance(usage, Mapping):
            raise TypeError("usage must be an object")
        usage_value = _validated_attempt_usage(
            json.loads(canonical_json(
                dict(usage), max_bytes=_MAX_ATTEMPT_METRICS_JSON_BYTES,
            )),
            lease,
        )
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return LeaseMutationStatus.STALE_FENCE
            assert row is not None
            if row["unit_state"] != "running" or row["attempt_state"] != "running":
                return LeaseMutationStatus.INVALID_STATE
            if self._clock_regressed(
                row["revision_clock_high_water_at"], current,
            ):
                return LeaseMutationStatus.LEASE_EXPIRED
            if parse_instant(str(row["lease_expires_at"])) <= current:
                return LeaseMutationStatus.LEASE_EXPIRED
            model_snapshot = json.loads(str(row["model_snapshot_json"]))
            if model_snapshot.get("mode") != "llm":
                raise ModelUsageContractError(
                    "attempt usage requires a frozen model execution"
                )
            _validate_usage_against_model_snapshot(
                usage_value, model_snapshot,
            )
            existing_metrics = json.loads(str(row["metrics_json"]))
            existing_usage = existing_metrics.get("llm_usage")
            if existing_usage is not None:
                if existing_usage == usage_value:
                    return LeaseMutationStatus.ALREADY_APPLIED
                raise DurableStoreError(
                    "attempt usage is already frozen for the active fence"
                )
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
            self._add_revision_usage_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                input_tokens=int(usage_value["input_tokens"]),
                output_tokens=int(usage_value["output_tokens"]),
                cost_micros=int(usage_value["cost_micros"]),
                usage_unknown=(
                    bool(usage_value["usage_missing"])
                    or bool(usage_value["cost_unknown"])
                ),
                now_text=current_text,
            )
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )
            return LeaseMutationStatus.APPLIED

    @staticmethod
    def _next_materialization_stage(
        connection: sqlite3.Connection,
        owner_user_id: str,
        revision_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT
                stage.id, stage.stage_key, stage.position, stage.stage_type,
                stage.cardinality, stage.max_units, stage.required_flag,
                stage.invalidation_digest, stage.reduction_fan_in,
                stage.reduction_input, stage.reduction_max_input_bytes,
                progress.source_ordinal, progress.parent_position,
                progress.parent_unit_id, progress.entry_offset,
                progress.legacy_replay, progress.unit_count,
                progress.reduction_level, progress.reduction_ordinal,
                progress.reduction_waiting, progress.reduction_input_count,
                progress.attention_code,
                json_extract(
                    revision.plan_json,
                    '$.stages[' || stage.position ||
                    '].cardinality.entry_identity_field'
                ) AS entry_identity_field
            FROM stage_materialization progress
            JOIN stages stage
              ON stage.owner_user_id=progress.owner_user_id
             AND stage.revision_id=progress.revision_id
             AND stage.id=progress.stage_id
            JOIN revisions revision
              ON revision.owner_user_id=stage.owner_user_id
             AND revision.id=stage.revision_id
            WHERE progress.owner_user_id=? AND progress.revision_id=?
              AND progress.completed=0
              AND progress.attention_code IS NULL
              AND (
                progress.reduction_waiting=0
                OR NOT EXISTS (
                  SELECT 1 FROM units reduction_unit
                  WHERE reduction_unit.owner_user_id=progress.owner_user_id
                    AND reduction_unit.revision_id=progress.revision_id
                    AND reduction_unit.stage_id=progress.stage_id
                    AND reduction_unit.reduction_level=progress.reduction_level
                    AND reduction_unit.state NOT IN (
                      'committed', 'failed_permanent', 'needs_attention',
                      'cancelled', 'skipped'
                    )
                )
              )
              AND NOT EXISTS (
                SELECT 1
                FROM stage_dependencies dependency
                JOIN stages parent
                  ON parent.owner_user_id=dependency.owner_user_id
                 AND parent.revision_id=dependency.revision_id
                 AND parent.id=dependency.depends_on_stage_id
                LEFT JOIN stage_materialization parent_progress
                  ON parent_progress.owner_user_id=parent.owner_user_id
                 AND parent_progress.revision_id=parent.revision_id
                 AND parent_progress.stage_id=parent.id
                WHERE dependency.owner_user_id=stage.owner_user_id
                  AND dependency.revision_id=stage.revision_id
                  AND dependency.stage_id=stage.id
                  AND parent.stage_type<>'inventory'
                  AND (
                    parent_progress.completed IS NOT 1
                    OR EXISTS (
                      SELECT 1 FROM units parent_unit
                      WHERE parent_unit.owner_user_id=parent.owner_user_id
                        AND parent_unit.revision_id=parent.revision_id
                        AND parent_unit.stage_id=parent.id
                        AND parent_unit.state NOT IN (
                          'committed', 'failed_permanent', 'cancelled', 'skipped'
                        )
                    )
                  )
              )
            ORDER BY stage.position, stage.id
            LIMIT 1
            """,
            (owner_user_id, revision_id),
        ).fetchone()

    @staticmethod
    def _reduction_level_status(
        connection: sqlite3.Connection,
        owner_user_id: str,
        revision_id: str,
        stage_id: str,
        level: int,
    ) -> tuple[int, int, sqlite3.Row | None]:
        """Summarize an arbitrarily wide reduction level in constant memory."""

        summary = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(state='committed'), 0) AS committed
            FROM units
            WHERE owner_user_id=? AND revision_id=?
              AND stage_id=? AND reduction_level=?
            """,
            (owner_user_id, revision_id, stage_id, level),
        ).fetchone()
        assert summary is not None
        total = int(summary["total"])
        committed = int(summary["committed"])
        root = None
        if total and not (committed == total and total > 1):
            root = connection.execute(
                """
                SELECT id, state, unit_key
                FROM units
                WHERE owner_user_id=? AND revision_id=?
                  AND stage_id=? AND reduction_level=?
                ORDER BY CASE WHEN state='committed' THEN 1 ELSE 0 END,
                         reduction_ordinal, unit_key
                LIMIT 1
                """,
                (owner_user_id, revision_id, stage_id, level),
            ).fetchone()
        return total, committed, root

    def _materialize_ready_units_batch(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        limit: int,
    ) -> tuple[int, int]:
        """Return ``(created, advanced)`` for a genuinely bounded batch."""

        created = 0
        advanced = 0
        with self._transaction() as connection:
            workload = self._select_workload(
                connection, owner_user_id, workload_id,
            )
            revision_id = workload["active_revision_id"]
            if revision_id is None or workload["state"] not in {
                WorkloadState.ADMITTED.value,
                WorkloadState.QUEUED.value,
                WorkloadState.RUNNING.value,
            }:
                return 0, 0
            revision = connection.execute(
                """
                SELECT partial_output_accepted, manual_retry_generation
                FROM revisions
                WHERE owner_user_id=? AND id=? AND workload_id=?
                """,
                (owner_user_id, revision_id, workload_id),
            ).fetchone()
            if revision is None:
                raise DurableStoreError("active revision is missing")
            partial_accepted = bool(revision["partial_output_accepted"])

            while advanced < limit:
                stage = self._next_materialization_stage(
                    connection, owner_user_id, str(revision_id),
                )
                if stage is None:
                    break
                stage_id = str(stage["id"])
                existing_count = int(stage["unit_count"])
                legacy_replay = bool(stage["legacy_replay"])
                blocked_code: str | None = None

                def save_cursor(
                    *,
                    completed: bool = False,
                    source_ordinal: int | None = None,
                    parent_position: int | None = None,
                    parent_unit_id: str | None = None,
                    entry_offset: int | None = None,
                    clear_parent: bool = False,
                    reduction_level: int | None = None,
                    reduction_ordinal: int | None = None,
                    reduction_waiting: bool | None = None,
                    reduction_input_count: int | None = None,
                    attention_code: str | None = None,
                ) -> None:
                    updated = connection.execute(
                        """
                        UPDATE stage_materialization
                        SET completed=?, source_ordinal=?, parent_position=?,
                            parent_unit_id=?, entry_offset=?,
                            legacy_replay=?, unit_count=?,
                            reduction_level=?, reduction_ordinal=?,
                            reduction_waiting=?, reduction_input_count=?,
                            attention_code=?, updated_at=?
                        WHERE owner_user_id=? AND revision_id=? AND stage_id=?
                        """,
                        (
                            int(completed),
                            int(stage["source_ordinal"])
                            if source_ordinal is None else source_ordinal,
                            int(stage["parent_position"])
                            if parent_position is None else parent_position,
                            None if clear_parent else (
                                stage["parent_unit_id"]
                                if parent_unit_id is None else parent_unit_id
                            ),
                            int(stage["entry_offset"])
                            if entry_offset is None else entry_offset,
                            0 if completed else int(legacy_replay),
                            existing_count,
                            int(stage["reduction_level"])
                            if reduction_level is None else reduction_level,
                            int(stage["reduction_ordinal"])
                            if reduction_ordinal is None else reduction_ordinal,
                            int(stage["reduction_waiting"])
                            if reduction_waiting is None
                            else int(reduction_waiting),
                            int(stage["reduction_input_count"])
                            if reduction_input_count is None
                            else reduction_input_count,
                            blocked_code if attention_code is None
                            else attention_code,
                            utc_now(), owner_user_id, revision_id, stage_id,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise DurableStoreError(
                            "materialization cursor update failed"
                        )

                def insert_candidate(
                    source: sqlite3.Row | None,
                    parents: Sequence[sqlite3.Row],
                    shard_key: str | None,
                    *,
                    unavailable_reason: str | None = None,
                    force_attention: bool = False,
                    duplicate_identity: bool = False,
                    dependency_slice: tuple[int, int] | None = None,
                    semantic_shard_key: str | None = None,
                    reduction_level: int | None = None,
                    reduction_ordinal: int | None = None,
                    reduction_root: bool = False,
                ) -> bool:
                    nonlocal existing_count, legacy_replay, blocked_code
                    semantic = {
                        "stage_key": str(stage["stage_key"]),
                        "stage_invalidation_digest": str(
                            stage["invalidation_digest"]
                        ),
                        "source_digest": (
                            str(source["content_digest"])
                            if source is not None else None
                        ),
                        "source_id": (
                            str(source["source_id"])
                            if source is not None else None
                        ),
                        "dependencies": [
                            {
                                "role": str(parent["dependency_role"]),
                                "unit_key": str(parent["parent_unit_key"]),
                                "digest": str(parent["digest"]),
                            }
                            for parent in parents
                        ],
                        "shard_key": (
                            shard_key
                            if semantic_shard_key is None
                            else semantic_shard_key
                        ),
                    }
                    unit_key = digest_json(
                        "durable-unit-key",
                        semantic,
                        max_bytes=MAX_PLAN_JSON_BYTES,
                    )
                    existing = connection.execute(
                        """
                        SELECT id, expected_dependency_count FROM units
                        WHERE owner_user_id=? AND revision_id=?
                          AND stage_id=? AND unit_key=?
                        """,
                        (owner_user_id, revision_id, stage_id, unit_key),
                    ).fetchone()

                    def bind_dependencies(unit_id: str) -> None:
                        if dependency_slice is not None and len(parents) != 1:
                            raise DurableStoreError(
                                "an entry slice requires exactly one dependency"
                            )
                        expected_bindings = tuple(
                            (
                                str(parent["result_id"]),
                                dependency_slice[0]
                                if dependency_slice is not None else None,
                                dependency_slice[1]
                                if dependency_slice is not None else None,
                                str(parent["dependency_role"]),
                            )
                            for parent in parents
                        )
                        for ordinal, (
                            result_id, entry_offset, entry_length, role,
                        ) in enumerate(expected_bindings):
                            connection.execute(
                                """
                                INSERT INTO unit_dependencies(
                                    owner_user_id, revision_id, unit_id,
                                    source_result_id, ordinal,
                                    entry_offset, entry_length, role
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT DO NOTHING
                                """,
                                (
                                    owner_user_id, revision_id, unit_id,
                                    result_id, ordinal,
                                    entry_offset, entry_length, role,
                                ),
                            )
                        stored_bindings = tuple(
                            (
                                str(row["source_result_id"]),
                                row["entry_offset"],
                                row["entry_length"],
                                str(row["role"]),
                            )
                            for row in connection.execute(
                                """
                                SELECT source_result_id,
                                       entry_offset, entry_length, role
                                FROM unit_dependencies
                                WHERE owner_user_id=? AND revision_id=?
                                  AND unit_id=?
                                ORDER BY ordinal
                                LIMIT ?
                                """,
                                (
                                    owner_user_id, revision_id, unit_id,
                                    _MAX_UNIT_DEPENDENCIES + 1,
                                ),
                            ).fetchall()
                        )
                        if stored_bindings != expected_bindings:
                            raise DurableStoreError(
                                "materialized dependency lineage conflicts "
                                "with the durable unit"
                            )

                    if existing is not None:
                        if legacy_replay:
                            if int(existing["expected_dependency_count"]) != len(
                                parents
                            ):
                                raise DurableStoreError(
                                    "legacy unit dependency count is inconsistent"
                                )
                            bind_dependencies(str(existing["id"]))
                            return False
                        if duplicate_identity:
                            blocked_code = "duplicate_entry_identity"
                            return False
                        raise DurableStoreError(
                            "materialization cursor replayed a completed candidate"
                        )
                    if existing_count >= int(stage["max_units"]):
                        blocked_code = "stage_unit_cap_exceeded"
                        return False
                    state = "pending"
                    error_class = None
                    terminal_detail = None
                    if unavailable_reason is not None:
                        state = (
                            "needs_attention"
                            if force_attention or (
                                bool(stage["required_flag"])
                                and not partial_accepted
                            )
                            else "skipped"
                        )
                        error_class = unavailable_reason
                        terminal_detail = canonical_json(
                            {
                                "schema_version":
                                    "metnos.durable-unit-terminal/1",
                                "reason_code": unavailable_reason,
                            },
                            max_bytes=MAX_EVENT_JSON_BYTES,
                        )
                    now_text = utc_now()
                    unit_id = _new_id("unt")
                    connection.execute(
                        """
                        INSERT INTO units(
                            owner_user_id, id, revision_id, stage_id, unit_key,
                            source_row_id, shard_key, state,
                            expected_dependency_count, error_class,
                            manual_retry_generation, terminal_detail_json,
                            reduction_level, reduction_ordinal, reduction_root,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            owner_user_id, unit_id, revision_id,
                            stage_id, unit_key,
                            None if source is None else source["id"],
                            shard_key, state,
                            0 if unavailable_reason is not None else len(parents),
                            error_class,
                            int(revision["manual_retry_generation"]),
                            terminal_detail, reduction_level,
                            reduction_ordinal, int(reduction_root),
                            now_text, now_text,
                        ),
                    )
                    bind_dependencies(unit_id)
                    existing_count += 1
                    legacy_replay = False
                    return True

                reduction_fan_in = stage["reduction_fan_in"]
                if reduction_fan_in is not None:
                    level = int(stage["reduction_level"])
                    output_ordinal = int(stage["reduction_ordinal"])

                    if bool(stage["reduction_waiting"]):
                        total, committed, root = self._reduction_level_status(
                            connection,
                            owner_user_id,
                            str(revision_id),
                            stage_id,
                            level,
                        )
                        if total == 0:
                            created += int(insert_candidate(
                                None,
                                (),
                                f"reduction:{level}:unavailable",
                                unavailable_reason="dependency_result_unavailable",
                                force_attention=True,
                                reduction_level=level,
                                reduction_ordinal=0,
                                reduction_root=True,
                            ))
                            save_cursor(
                                completed=True,
                                reduction_waiting=False,
                            )
                        elif committed == total and total > 1:
                            save_cursor(
                                clear_parent=True,
                                entry_offset=-1,
                                reduction_level=level + 1,
                                reduction_ordinal=-1,
                                reduction_waiting=False,
                                reduction_input_count=-1,
                            )
                        else:
                            # One committed result is the reduction root.  If a
                            # tolerated terminal failure exists, exposing one
                            # deterministic failed root lets downstream stages
                            # materialize an explicit unavailable outcome.
                            assert root is not None
                            connection.execute(
                                """
                                UPDATE units SET reduction_root=1, updated_at=?
                                WHERE owner_user_id=? AND revision_id=?
                                  AND stage_id=? AND id=?
                                """,
                                (
                                    utc_now(), owner_user_id, revision_id,
                                    stage_id, root["id"],
                                ),
                            )
                            save_cursor(
                                completed=True,
                                reduction_waiting=False,
                            )
                        advanced += 1
                        continue

                    dependency = connection.execute(
                        """
                        SELECT parent.id, parent.stage_key
                        FROM stage_dependencies declared
                        JOIN stages parent
                          ON parent.owner_user_id=declared.owner_user_id
                         AND parent.revision_id=declared.revision_id
                         AND parent.id=declared.depends_on_stage_id
                        WHERE declared.owner_user_id=?
                          AND declared.revision_id=?
                          AND declared.stage_id=?
                          AND parent.stage_type<>'inventory'
                        """,
                        (owner_user_id, revision_id, stage_id),
                    ).fetchall()
                    if len(dependency) != 1:
                        raise DurableStoreError(
                            "hierarchical reduction needs one persisted dependency"
                        )
                    dependency_stage_id = str(dependency[0]["id"])
                    dependency_role = str(dependency[0]["stage_key"])

                    input_count = int(stage["reduction_input_count"])
                    if input_count < 0:
                        if level == 0:
                            input_count = int(connection.execute(
                                """
                                SELECT COUNT(*)
                                FROM units parent_unit
                                JOIN stages parent
                                  ON parent.owner_user_id=parent_unit.owner_user_id
                                 AND parent.revision_id=parent_unit.revision_id
                                 AND parent.id=parent_unit.stage_id
                                WHERE parent_unit.owner_user_id=?
                                  AND parent_unit.revision_id=?
                                  AND parent_unit.stage_id=?
                                  AND (
                                    parent.reduction_fan_in IS NULL
                                    OR parent_unit.reduction_root=1
                                  )
                                """,
                                (
                                    owner_user_id, revision_id,
                                    dependency_stage_id,
                                ),
                            ).fetchone()[0])
                        else:
                            input_count = int(connection.execute(
                                """
                                SELECT COUNT(*) FROM units
                                WHERE owner_user_id=? AND revision_id=?
                                  AND stage_id=? AND reduction_level=?
                                """,
                                (
                                    owner_user_id, revision_id, stage_id,
                                    level - 1,
                                ),
                            ).fetchone()[0])
                    if input_count == 0:
                        created += int(insert_candidate(
                            None,
                            (),
                            f"reduction:{level}:unavailable",
                            unavailable_reason="dependency_result_unavailable",
                            force_attention=True,
                            reduction_level=level,
                            reduction_ordinal=0,
                            reduction_root=True,
                        ))
                        save_cursor(
                            completed=True,
                            reduction_input_count=0,
                        )
                        advanced += 1
                        continue

                    cursor_id = stage["parent_unit_id"]
                    if level == 0:
                        candidates = connection.execute(
                            """
                            SELECT
                                parent_unit.id AS parent_unit_id,
                                parent_unit.unit_key AS parent_unit_key,
                                ? AS dependency_role,
                                parent_unit.state AS parent_unit_state,
                                result.id AS result_id, result.digest,
                                json_type(result.payload_json, '$.entries')
                                    AS entries_type,
                                length(CAST(
                                    json_extract(result.payload_json, '$.entries')
                                    AS BLOB
                                )) AS entries_bytes,
                                json_array_length(
                                    result.payload_json, '$.entries'
                                ) AS entry_count
                            FROM units parent_unit
                            JOIN stages parent
                              ON parent.owner_user_id=parent_unit.owner_user_id
                             AND parent.revision_id=parent_unit.revision_id
                             AND parent.id=parent_unit.stage_id
                            LEFT JOIN results result
                              ON result.owner_user_id=parent_unit.owner_user_id
                             AND result.revision_id=parent_unit.revision_id
                             AND result.id=parent_unit.committed_result_id
                            WHERE parent_unit.owner_user_id=?
                              AND parent_unit.revision_id=?
                              AND parent_unit.stage_id=?
                              AND (
                                parent.reduction_fan_in IS NULL
                                OR parent_unit.reduction_root=1
                              )
                              AND parent_unit.unit_key>COALESCE((
                                SELECT cursor_unit.unit_key
                                FROM units cursor_unit
                                WHERE cursor_unit.owner_user_id=?
                                  AND cursor_unit.revision_id=?
                                  AND cursor_unit.id=?
                              ), '')
                            ORDER BY parent_unit.unit_key
                            LIMIT ?
                            """,
                            (
                                dependency_role, owner_user_id, revision_id,
                                dependency_stage_id, owner_user_id,
                                revision_id, cursor_id,
                                int(reduction_fan_in) + 1,
                            ),
                        ).fetchall()
                    else:
                        candidates = connection.execute(
                            """
                            SELECT
                                parent_unit.id AS parent_unit_id,
                                parent_unit.unit_key AS parent_unit_key,
                                ? AS dependency_role,
                                parent_unit.state AS parent_unit_state,
                                result.id AS result_id, result.digest,
                                json_type(result.payload_json, '$.entries')
                                    AS entries_type,
                                length(CAST(
                                    json_extract(result.payload_json, '$.entries')
                                    AS BLOB
                                )) AS entries_bytes,
                                json_array_length(
                                    result.payload_json, '$.entries'
                                ) AS entry_count
                            FROM units parent_unit
                            LEFT JOIN results result
                              ON result.owner_user_id=parent_unit.owner_user_id
                             AND result.revision_id=parent_unit.revision_id
                             AND result.id=parent_unit.committed_result_id
                            WHERE parent_unit.owner_user_id=?
                              AND parent_unit.revision_id=?
                              AND parent_unit.stage_id=?
                              AND parent_unit.reduction_level=?
                              AND parent_unit.reduction_ordinal>COALESCE((
                                SELECT cursor_unit.reduction_ordinal
                                FROM units cursor_unit
                                WHERE cursor_unit.owner_user_id=?
                                  AND cursor_unit.revision_id=?
                                  AND cursor_unit.id=?
                              ), -1)
                            ORDER BY parent_unit.reduction_ordinal,
                                     parent_unit.unit_key
                            LIMIT ?
                            """,
                            (
                                dependency_role, owner_user_id, revision_id,
                                stage_id, level - 1, owner_user_id,
                                revision_id, cursor_id,
                                int(reduction_fan_in) + 1,
                            ),
                        ).fetchall()

                    # The extra row is fetched only to decide whether another
                    # group follows.  It must not invalidate the current,
                    # bounded fan-in group.
                    invalid_parent = next((
                        row for row in candidates[:int(reduction_fan_in)]
                        if row["parent_unit_state"] != "committed"
                        or row["result_id"] is None
                        or row["entries_type"] != "array"
                        or row["entries_bytes"] is None
                    ), None)
                    if invalid_parent is not None:
                        created += int(insert_candidate(
                            None,
                            (),
                            f"reduction:{level}:invalid-input",
                            unavailable_reason="dependency_result_unavailable",
                            force_attention=True,
                            reduction_level=level,
                            reduction_ordinal=output_ordinal + 1,
                            reduction_root=True,
                        ))
                        save_cursor(
                            completed=True,
                            reduction_input_count=input_count,
                        )
                        advanced += 1
                        continue

                    selected: list[sqlite3.Row] = []
                    content_bytes = 0
                    nonempty_arrays = 0
                    byte_limit = int(stage["reduction_max_input_bytes"])
                    reason: str | None = None
                    for candidate in candidates[:int(reduction_fan_in)]:
                        entry_bytes = int(candidate["entries_bytes"])
                        entry_count = int(candidate["entry_count"] or 0)
                        next_content = content_bytes + max(0, entry_bytes - 2)
                        next_nonempty = nonempty_arrays + int(entry_count > 0)
                        combined_bytes = (
                            2 + next_content + max(0, next_nonempty - 1)
                        )
                        if combined_bytes > byte_limit:
                            if not selected:
                                reason = "dependency_input_too_large"
                            elif len(selected) == 1:
                                reason = "reduction_not_converging"
                            else:
                                break
                            created += int(insert_candidate(
                                None,
                                (),
                                f"reduction:{level}:{reason}",
                                unavailable_reason=reason,
                                force_attention=True,
                                reduction_level=level,
                                reduction_ordinal=output_ordinal + 1,
                                reduction_root=True,
                            ))
                            save_cursor(
                                completed=True,
                                reduction_input_count=input_count,
                            )
                            advanced += 1
                            break
                        selected.append(candidate)
                        content_bytes = next_content
                        nonempty_arrays = next_nonempty
                    if reason is not None:
                        continue
                    if not selected:
                        save_cursor(
                            reduction_waiting=True,
                            reduction_input_count=input_count,
                        )
                        advanced += 1
                        continue

                    next_ordinal = output_ordinal + 1
                    created += int(insert_candidate(
                        None,
                        tuple(selected),
                        f"reduction:{level}:{next_ordinal}",
                        reduction_level=level,
                        reduction_ordinal=next_ordinal,
                    ))
                    has_more = len(candidates) > len(selected)
                    save_cursor(
                        parent_unit_id=str(selected[-1]["parent_unit_id"]),
                        entry_offset=-1,
                        reduction_ordinal=next_ordinal,
                        reduction_waiting=not has_more,
                        reduction_input_count=input_count,
                    )
                    advanced += 1
                    continue

                mode = str(stage["cardinality"])
                if mode == "singleton":
                    parent_units = connection.execute(
                        """
                        SELECT
                            parent.position AS parent_position,
                            parent_unit.id AS parent_unit_id,
                            parent_unit.unit_key AS parent_unit_key,
                            parent.stage_key AS dependency_role,
                            parent_unit.state AS parent_unit_state,
                            result.id AS result_id, result.digest
                        FROM stage_dependencies dependency
                        JOIN stages parent
                          ON parent.owner_user_id=dependency.owner_user_id
                         AND parent.revision_id=dependency.revision_id
                         AND parent.id=dependency.depends_on_stage_id
                        JOIN units parent_unit
                          ON parent_unit.owner_user_id=parent.owner_user_id
                         AND parent_unit.revision_id=parent.revision_id
                         AND parent_unit.stage_id=parent.id
                        LEFT JOIN results result
                          ON result.owner_user_id=parent_unit.owner_user_id
                         AND result.revision_id=parent_unit.revision_id
                         AND result.id=parent_unit.committed_result_id
                        WHERE dependency.owner_user_id=?
                          AND dependency.revision_id=?
                          AND dependency.stage_id=?
                          AND parent.stage_type<>'inventory'
                          AND (
                            parent.reduction_fan_in IS NULL
                            OR parent_unit.reduction_root=1
                          )
                        ORDER BY parent.position, parent_unit.unit_key
                        LIMIT ?
                        """,
                        (
                            owner_user_id, revision_id, stage_id,
                            _MAX_UNIT_DEPENDENCIES + 1,
                        ),
                    ).fetchall()
                    reason = None
                    force_attention = False
                    parents: tuple[sqlite3.Row, ...] = ()
                    if len(parent_units) > _MAX_UNIT_DEPENDENCIES:
                        reason = "dependency_fan_in_exceeded"
                        force_attention = True
                    elif not parent_units:
                        reason = "dependency_result_unavailable"
                    elif any(
                        row["parent_unit_state"] == "committed"
                        and row["result_id"] is None
                        for row in parent_units
                    ):
                        reason = "dependency_result_missing"
                        force_attention = True
                    elif any(
                        row["parent_unit_state"] != "committed"
                        for row in parent_units
                    ):
                        reason = "dependency_result_unavailable"
                    else:
                        parents = tuple(parent_units)
                    created += int(insert_candidate(
                        None,
                        parents,
                        None if reason is None else "unavailable:singleton",
                        unavailable_reason=reason,
                        force_attention=force_attention,
                        semantic_shard_key=(
                            None if reason is None
                            else "unavailable:singleton"
                        ),
                    ))
                    save_cursor(completed=True, entry_offset=-1)
                    advanced += 1
                    continue

                if mode == "per_source":
                    source = connection.execute(
                        """
                        SELECT id, source_id, content_digest, ordinal
                        FROM sources
                        WHERE owner_user_id=? AND revision_id=? AND ordinal>?
                        ORDER BY ordinal, id
                        LIMIT 1
                        """,
                        (
                            owner_user_id, revision_id,
                            int(stage["source_ordinal"]),
                        ),
                    ).fetchone()
                    if source is None:
                        save_cursor(completed=True, entry_offset=-1)
                        advanced += 1
                        continue
                    parent_stages = connection.execute(
                        """
                        SELECT parent.id, parent.cardinality, parent.position,
                               parent.stage_key
                        FROM stage_dependencies dependency
                        JOIN stages parent
                          ON parent.owner_user_id=dependency.owner_user_id
                         AND parent.revision_id=dependency.revision_id
                         AND parent.id=dependency.depends_on_stage_id
                        WHERE dependency.owner_user_id=?
                          AND dependency.revision_id=?
                          AND dependency.stage_id=?
                          AND parent.stage_type<>'inventory'
                        ORDER BY dependency.ordinal
                        """,
                        (owner_user_id, revision_id, stage_id),
                    ).fetchall()
                    parent_units: list[sqlite3.Row] = []
                    too_many = False
                    missing_parent = False
                    for parent in parent_stages:
                        remaining = (
                            _MAX_UNIT_DEPENDENCIES + 1 - len(parent_units)
                        )
                        rows = connection.execute(
                            """
                            SELECT
                                parent_unit.id AS parent_unit_id,
                                parent_unit.unit_key AS parent_unit_key,
                                ? AS dependency_role,
                                parent_unit.state AS parent_unit_state,
                                result.id AS result_id, result.digest
                            FROM units parent_unit
                            LEFT JOIN results result
                              ON result.owner_user_id=parent_unit.owner_user_id
                             AND result.revision_id=parent_unit.revision_id
                             AND result.id=parent_unit.committed_result_id
                            WHERE parent_unit.owner_user_id=?
                              AND parent_unit.revision_id=?
                              AND parent_unit.stage_id=?
                              AND (?='singleton' OR parent_unit.source_row_id=?)
                              AND (
                                NOT EXISTS (
                                  SELECT 1 FROM stages selected_parent
                                  WHERE selected_parent.owner_user_id=parent_unit.owner_user_id
                                    AND selected_parent.revision_id=parent_unit.revision_id
                                    AND selected_parent.id=parent_unit.stage_id
                                    AND selected_parent.reduction_fan_in IS NOT NULL
                                )
                                OR parent_unit.reduction_root=1
                              )
                            ORDER BY parent_unit.unit_key
                            LIMIT ?
                            """,
                            (
                                parent["stage_key"], owner_user_id,
                                revision_id, parent["id"],
                                parent["cardinality"], source["id"], remaining,
                            ),
                        ).fetchall()
                        if not rows:
                            missing_parent = True
                        parent_units.extend(rows)
                        if len(parent_units) > _MAX_UNIT_DEPENDENCIES:
                            too_many = True
                            break
                    reason = None
                    force_attention = False
                    parents = ()
                    if too_many:
                        reason = "dependency_fan_in_exceeded"
                        force_attention = True
                    elif missing_parent or not parent_units:
                        reason = "dependency_result_unavailable"
                    elif any(
                        row["parent_unit_state"] == "committed"
                        and row["result_id"] is None
                        for row in parent_units
                    ):
                        reason = "dependency_result_missing"
                        force_attention = True
                    elif any(
                        row["parent_unit_state"] != "committed"
                        for row in parent_units
                    ):
                        reason = "dependency_result_unavailable"
                    else:
                        parents = tuple(parent_units)
                    created += int(insert_candidate(
                        source,
                        parents,
                        None if reason is None else (
                            "unavailable:source:" + str(source["id"])
                        ),
                        unavailable_reason=reason,
                        force_attention=force_attention,
                        semantic_shard_key=(
                            None if reason is None else (
                                "unavailable:source:" + str(source["source_id"])
                            )
                        ),
                    ))
                    save_cursor(source_ordinal=int(source["ordinal"]))
                    advanced += 1
                    continue

                parent_position = int(stage["parent_position"])
                parent_unit_id = stage["parent_unit_id"]
                entry_offset = int(stage["entry_offset"])
                if entry_offset >= 0 and parent_unit_id is not None:
                    parent_unit = connection.execute(
                        """
                        SELECT
                            parent.position AS parent_position,
                            parent_unit.id AS parent_unit_id,
                            parent_unit.unit_key AS parent_unit_key,
                            parent.stage_key AS dependency_role,
                            parent_unit.state AS parent_unit_state,
                            source.id, source.source_id, source.content_digest,
                            result.id AS result_id, result.digest,
                            result.payload_json
                        FROM units parent_unit
                        JOIN stages parent
                          ON parent.owner_user_id=parent_unit.owner_user_id
                         AND parent.revision_id=parent_unit.revision_id
                         AND parent.id=parent_unit.stage_id
                        JOIN stage_dependencies dependency
                          ON dependency.owner_user_id=parent.owner_user_id
                         AND dependency.revision_id=parent.revision_id
                         AND dependency.depends_on_stage_id=parent.id
                        LEFT JOIN sources source
                          ON source.owner_user_id=parent_unit.owner_user_id
                         AND source.revision_id=parent_unit.revision_id
                         AND source.id=parent_unit.source_row_id
                        LEFT JOIN results result
                          ON result.owner_user_id=parent_unit.owner_user_id
                         AND result.revision_id=parent_unit.revision_id
                         AND result.id=parent_unit.committed_result_id
                        WHERE parent_unit.owner_user_id=?
                          AND parent_unit.revision_id=?
                          AND dependency.stage_id=?
                          AND parent.position=? AND parent_unit.id=?
                        """,
                        (
                            owner_user_id, revision_id, stage_id,
                            parent_position, parent_unit_id,
                        ),
                    ).fetchone()
                else:
                    parent_unit = connection.execute(
                        """
                        SELECT
                            parent.position AS parent_position,
                            parent_unit.id AS parent_unit_id,
                            parent_unit.unit_key AS parent_unit_key,
                            parent.stage_key AS dependency_role,
                            parent_unit.state AS parent_unit_state,
                            source.id, source.source_id, source.content_digest,
                            result.id AS result_id, result.digest,
                            result.payload_json
                        FROM stage_dependencies dependency
                        JOIN stages parent
                          ON parent.owner_user_id=dependency.owner_user_id
                         AND parent.revision_id=dependency.revision_id
                         AND parent.id=dependency.depends_on_stage_id
                        JOIN units parent_unit
                          ON parent_unit.owner_user_id=parent.owner_user_id
                         AND parent_unit.revision_id=parent.revision_id
                         AND parent_unit.stage_id=parent.id
                        LEFT JOIN sources source
                          ON source.owner_user_id=parent_unit.owner_user_id
                         AND source.revision_id=parent_unit.revision_id
                         AND source.id=parent_unit.source_row_id
                        LEFT JOIN results result
                          ON result.owner_user_id=parent_unit.owner_user_id
                         AND result.revision_id=parent_unit.revision_id
                         AND result.id=parent_unit.committed_result_id
                        WHERE dependency.owner_user_id=?
                          AND dependency.revision_id=?
                          AND dependency.stage_id=?
                          AND parent.stage_type<>'inventory'
                          AND (
                            parent.reduction_fan_in IS NULL
                            OR parent_unit.reduction_root=1
                          )
                          AND (
                            parent.position>?
                            OR (
                              parent.position=? AND parent_unit.unit_key>COALESCE((
                                SELECT cursor_unit.unit_key
                                FROM units cursor_unit
                                WHERE cursor_unit.owner_user_id=parent_unit.owner_user_id
                                  AND cursor_unit.revision_id=parent_unit.revision_id
                                  AND cursor_unit.id=?
                              ), '')
                            )
                          )
                        ORDER BY parent.position, parent_unit.unit_key
                        LIMIT 1
                        """,
                        (
                            owner_user_id, revision_id, stage_id,
                            parent_position, parent_position, parent_unit_id,
                        ),
                    ).fetchone()
                if parent_unit is None:
                    save_cursor(completed=True, entry_offset=-1)
                    advanced += 1
                    continue
                source = (
                    parent_unit if parent_unit["id"] is not None else None
                )
                position = int(parent_unit["parent_position"])
                unit_id = str(parent_unit["parent_unit_id"])
                if parent_unit["parent_unit_state"] != "committed":
                    created += int(insert_candidate(
                        source,
                        (),
                        "unavailable:unit:" + unit_id,
                        unavailable_reason="dependency_result_unavailable",
                        semantic_shard_key=(
                            "unavailable:unit:"
                            + str(parent_unit["parent_unit_key"])
                        ),
                    ))
                    save_cursor(
                        parent_position=position,
                        parent_unit_id=unit_id,
                        entry_offset=-1,
                    )
                    advanced += 1
                    continue
                if parent_unit["result_id"] is None:
                    created += int(insert_candidate(
                        source,
                        (),
                        "unavailable:unit:" + unit_id,
                        unavailable_reason="dependency_result_missing",
                        force_attention=True,
                        semantic_shard_key=(
                            "unavailable:unit:"
                            + str(parent_unit["parent_unit_key"])
                        ),
                    ))
                    save_cursor(
                        parent_position=position,
                        parent_unit_id=unit_id,
                        entry_offset=-1,
                    )
                    advanced += 1
                    continue
                entry_identity_field = stage["entry_identity_field"]
                if entry_identity_field is None:
                    created += int(insert_candidate(
                        source,
                        (parent_unit,),
                        "result:" + str(parent_unit["result_id"]),
                        semantic_shard_key=(
                            "result:" + str(parent_unit["parent_unit_key"])
                        ),
                    ))
                    save_cursor(
                        parent_position=position,
                        parent_unit_id=unit_id,
                        entry_offset=-1,
                    )
                    advanced += 1
                    continue
                payload_json = str(parent_unit["payload_json"])
                try:
                    offset = (
                        entry_offset
                        if entry_offset >= 0
                        else _entry_array_offset(payload_json)
                    )
                    entry, next_offset, finished, item_start, item_end = _next_entry(
                        payload_json, offset,
                    )
                except DurableStoreError:
                    blocked_code = "dependency_payload_invalid"
                    save_cursor()
                    advanced += 1
                    continue
                if entry is None:
                    save_cursor(
                        parent_position=position,
                        parent_unit_id=unit_id,
                        entry_offset=-1,
                    )
                    advanced += 1
                    continue
                identity = entry.get(str(entry_identity_field))
                if not isinstance(identity, str) or not identity:
                    blocked_code = "entry_identity_invalid"
                    save_cursor()
                    advanced += 1
                    continue
                entry_digest = digest_json(
                    "durable-entry-shard",
                    {"field": str(entry_identity_field), "value": identity},
                    max_bytes=MAX_EVENT_JSON_BYTES,
                )
                shard_key = (
                    f"entry:{parent_unit['result_id']}:"
                    f"{entry_identity_field}:{entry_digest}"
                )
                created += int(insert_candidate(
                    source,
                    (parent_unit,),
                    shard_key,
                    duplicate_identity=True,
                    dependency_slice=(item_start, item_end - item_start),
                    semantic_shard_key=(
                        f"entry:{entry_identity_field}:{entry_digest}"
                    ),
                ))
                save_cursor(
                    parent_position=position,
                    parent_unit_id=unit_id,
                    entry_offset=-1 if finished else next_offset,
                )
                advanced += 1
        return created, advanced

    def materialize_ready_units(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        limit: int = 200,
    ) -> int:
        """Materialize at most ``limit`` durable candidate steps."""

        owner = _require_owner(owner_user_id)
        _require_limit(limit, maximum=1000)
        created, _advanced = self._materialize_ready_units_batch(
            owner, workload_id, limit=limit,
        )
        return created

    def materialization_complete(
        self,
        owner_user_id: str,
        workload_id: str,
    ) -> bool:
        """Return whether every stage has exhausted its durable candidate cursor."""

        owner = _require_owner(owner_user_id)
        workload = self._select_workload(
            self._connection, owner, workload_id,
        )
        revision_id = workload["active_revision_id"]
        if revision_id is None:
            return False
        pending = self._connection.execute(
            """
            SELECT 1 FROM stage_materialization
            WHERE owner_user_id=? AND revision_id=?
              AND (completed=0 OR attention_code IS NOT NULL)
            LIMIT 1
            """,
            (owner, revision_id),
        ).fetchone()
        return pending is None

    def complete_ready_workloads(self, *, limit: int = 200) -> int:
        """Evaluate bounded workloads whose durable graph is already terminal."""

        _require_limit(limit, maximum=1000)
        rows = self._connection.execute(
            """
            SELECT workload.owner_user_id, workload.id
            FROM workloads workload
            JOIN revisions revision
              ON revision.owner_user_id=workload.owner_user_id
             AND revision.id=workload.active_revision_id
            WHERE workload.state='running'
              AND NOT EXISTS (
                SELECT 1 FROM stage_materialization progress
                WHERE progress.owner_user_id=workload.owner_user_id
                  AND progress.revision_id=workload.active_revision_id
                  AND (progress.completed=0 OR progress.attention_code IS NOT NULL)
              )
              AND NOT EXISTS (
                SELECT 1 FROM units unit
                WHERE unit.owner_user_id=workload.owner_user_id
                  AND unit.revision_id=workload.active_revision_id
                  AND unit.state NOT IN (
                    'committed', 'failed_permanent', 'skipped', 'cancelled'
                  )
              )
              AND NOT EXISTS (
                SELECT 1
                FROM json_each(revision.required_artifacts_json) requirement
                WHERE NOT EXISTS (
                  SELECT 1 FROM artifacts artifact
                  WHERE artifact.owner_user_id=workload.owner_user_id
                    AND artifact.revision_id=workload.active_revision_id
                    AND artifact.logical_name=json_extract(
                      requirement.value, '$.name'
                    )
                    AND artifact.mime_type=json_extract(
                      requirement.value, '$.mime_type'
                    )
                    AND artifact.schema_version=json_extract(
                      requirement.value, '$.schema_version'
                    )
                    AND artifact.state IN ('committed', 'published')
                    AND artifact.digest_verified=1
                    AND artifact.schema_valid=1
                    AND artifact.postconditions_valid=1
                )
              )
            ORDER BY workload.updated_at, workload.owner_user_id, workload.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        completed = 0
        for row in rows:
            owner = str(row["owner_user_id"])
            workload_id = str(row["id"])
            self.refresh_usage_complete(owner, workload_id)
            if self.evaluate_completion(owner, workload_id).eligible:
                completed += 1
        return completed

    def materialize_all_ready_units(self, *, limit: int = 200) -> int:
        """Advance a fair, bounded set of ready materialization cursors."""

        _require_limit(limit, maximum=1000)
        rows = self._connection.execute(
            """
            SELECT workload.owner_user_id, workload.id,
                   MIN(progress.updated_at) AS progress_at
            FROM workloads workload
            JOIN stage_materialization progress
              ON progress.owner_user_id=workload.owner_user_id
             AND progress.revision_id=workload.active_revision_id
             AND progress.completed=0
             AND progress.attention_code IS NULL
            JOIN stages stage
              ON stage.owner_user_id=progress.owner_user_id
             AND stage.revision_id=progress.revision_id
             AND stage.id=progress.stage_id
            WHERE workload.state IN ('admitted', 'queued', 'running')
              AND (
                progress.reduction_waiting=0
                OR NOT EXISTS (
                  SELECT 1 FROM units reduction_unit
                  WHERE reduction_unit.owner_user_id=progress.owner_user_id
                    AND reduction_unit.revision_id=progress.revision_id
                    AND reduction_unit.stage_id=progress.stage_id
                    AND reduction_unit.reduction_level=progress.reduction_level
                    AND reduction_unit.state NOT IN (
                      'committed', 'failed_permanent', 'needs_attention',
                      'cancelled', 'skipped'
                    )
                )
              )
              AND NOT EXISTS (
                SELECT 1
                FROM stage_dependencies dependency
                JOIN stages parent
                  ON parent.owner_user_id=dependency.owner_user_id
                 AND parent.revision_id=dependency.revision_id
                 AND parent.id=dependency.depends_on_stage_id
                LEFT JOIN stage_materialization parent_progress
                  ON parent_progress.owner_user_id=parent.owner_user_id
                 AND parent_progress.revision_id=parent.revision_id
                 AND parent_progress.stage_id=parent.id
                WHERE dependency.owner_user_id=stage.owner_user_id
                  AND dependency.revision_id=stage.revision_id
                  AND dependency.stage_id=stage.id
                  AND parent.stage_type<>'inventory'
                  AND (
                    parent_progress.completed IS NOT 1
                    OR EXISTS (
                      SELECT 1 FROM units parent_unit
                      WHERE parent_unit.owner_user_id=parent.owner_user_id
                        AND parent_unit.revision_id=parent.revision_id
                        AND parent_unit.stage_id=parent.id
                        AND parent_unit.state NOT IN (
                          'committed', 'failed_permanent', 'cancelled', 'skipped'
                        )
                    )
                  )
              )
            GROUP BY workload.owner_user_id, workload.id
            ORDER BY progress_at, workload.owner_user_id, workload.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        advanced = 0
        for row in rows:
            if advanced >= limit:
                break
            _created, progressed = self._materialize_ready_units_batch(
                str(row["owner_user_id"]),
                str(row["id"]),
                limit=min(100, limit - advanced),
            )
            advanced += progressed
        return advanced

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
            usage_unknown = int(connection.execute(
                """
                SELECT usage_unknown FROM revision_usage
                WHERE owner_user_id=? AND revision_id=?
                """,
                (owner, revision_id),
            ).fetchone()[0])
            complete = missing == 0 and usage_unknown == 0
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
            metrics_value = json.loads(str(row["metrics_json"]))
            execution_started_at = metrics_value.get("execution_started_at")
            if not isinstance(execution_started_at, str):
                return CommitOutcome(
                    CommitStatus.INVALID_STATE, None, None,
                    validated_result.digest,
                )
            execution_deadline = parse_instant(
                execution_started_at, name="execution_started_at",
            ) + timedelta(seconds=lease.timeout_s)
            if execution_deadline <= current:
                return CommitOutcome(
                    CommitStatus.DEADLINE_EXPIRED, None, None,
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
            dependency_rows = self._validated_result_dependencies_in_transaction(
                connection,
                owner_user_id=lease.owner_user_id,
                revision_id=lease.revision_id,
                stage_id=lease.stage_id,
                unit_id=lease.unit_id,
                expected_count=expected_dependency_count,
                reduction_level=(
                    None if row["reduction_level"] is None
                    else int(row["reduction_level"])
                ),
                reduction_fan_in=(
                    None if row["reduction_fan_in"] is None
                    else int(row["reduction_fan_in"])
                ),
            )
            planned_dependency_ids = tuple(
                str(item["result_id"]) for item in dependency_rows
            )
            if frozenset(dependency_ids) != frozenset(planned_dependency_ids):
                raise ResultContractError(
                    "result dependencies do not match the materialized unit"
                )

            if not self._model_usage_is_complete(row):
                raise BudgetExceededError({
                    "reason_code": "budget_accounting_incomplete",
                    "budget": "accounting",
                })

            self._add_revision_usage_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                output_bytes=len(validated_result.payload_json.encode("utf-8")),
                now_text=current_text,
            )
            budget_violation = self._budget_violation_in_transaction(
                connection,
                owner=lease.owner_user_id,
                workload_id=lease.workload_id,
                revision_id=lease.revision_id,
                now=current,
            )
            if budget_violation is not None:
                raise BudgetExceededError(budget_violation)
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )

            metrics = self._attempt_metrics(
                row,
                result_digest=validated_result.digest,
                committed=True,
                dependency_count=len(dependency_rows),
            )
            metrics_value = json.loads(metrics)
            result_id = _new_id("res")
            executor_snapshot = json.loads(str(row["executor_snapshot_json"]))
            model_snapshot = json.loads(str(row["model_snapshot_json"]))
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
            self._insert_result_dependencies_in_transaction(
                connection,
                owner_user_id=lease.owner_user_id,
                revision_id=lease.revision_id,
                result_id=result_id,
                dependency_rows=dependency_rows,
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
            self._settle_workload_in_transaction(
                connection,
                lease.owner_user_id,
                lease.workload_id,
                now_text=current_text,
            )
            return CommitOutcome(
                CommitStatus.COMMITTED,
                result_id,
                validated_result.digest,
                validated_result.digest,
                stage_terminal=self._stage_is_terminal_in_connection(
                    connection,
                    lease.owner_user_id,
                    lease.revision_id,
                    lease.stage_id,
                ),
            )

    @staticmethod
    def _stage_is_terminal_in_connection(
        connection: sqlite3.Connection,
        owner_user_id: str,
        revision_id: str,
        stage_id: str,
    ) -> bool:
        terminal_states = tuple(state.value for state in TERMINAL_UNIT_STATES)
        placeholders = ",".join("?" for _state in terminal_states)
        remaining = connection.execute(
            f"""
            SELECT 1 FROM units
            WHERE owner_user_id=? AND revision_id=? AND stage_id=?
              AND state NOT IN ({placeholders})
            LIMIT 1
            """,
            (owner_user_id, revision_id, stage_id, *terminal_states),
        ).fetchone()
        return remaining is None

    def stage_is_terminal(self, lease: Lease) -> bool:
        """Return whether every unit in a lease's frozen stage is terminal.

        This is intentionally a narrow indexed check, used by the execution
        bridge to decide when a whole-workload materialisation or completion
        scan is warranted after an unsuccessful attempt.
        """
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        return self._stage_is_terminal_in_connection(
            self._connection,
            lease.owner_user_id,
            lease.revision_id,
            lease.stage_id,
        )

    def fail_attempt(
        self,
        lease: Lease,
        structured_error: StructuredAttemptError,
        retry_decision: RetryDecision,
        *,
        attempt_state: AttemptState = AttemptState.FAILED,
        now: datetime | None = None,
    ) -> FailureOutcome:
        if not isinstance(lease, Lease):
            raise TypeError("lease must be Lease")
        if not isinstance(structured_error, StructuredAttemptError):
            raise TypeError("structured_error must be StructuredAttemptError")
        if not isinstance(retry_decision, RetryDecision):
            raise TypeError("retry_decision must be RetryDecision")
        if attempt_state not in {AttemptState.FAILED, AttemptState.TIMED_OUT}:
            raise ValueError("attempt_state must be failed or timed_out")
        current, current_text = self._operation_now(now)
        with self._transaction() as connection:
            row = self._select_lease_row(connection, lease)
            if not self._lease_matches(row, lease):
                return FailureOutcome(FailureStatus.STALE_FENCE)
            assert row is not None
            if row["unit_state"] != "running" or row["attempt_state"] != "running":
                return FailureOutcome(FailureStatus.INVALID_STATE)
            if parse_instant(str(row["lease_expires_at"])) <= current:
                metrics_value = json.loads(str(row["metrics_json"]))
                execution_started_at = metrics_value.get("execution_started_at")
                deadline_reached = (
                    isinstance(execution_started_at, str)
                    and parse_instant(
                        execution_started_at,
                        name="execution_started_at",
                    ) + timedelta(seconds=lease.timeout_s) <= current
                )
                if (
                    attempt_state is not AttemptState.TIMED_OUT
                    or not deadline_reached
                ):
                    return FailureOutcome(FailureStatus.LEASE_EXPIRED)
            policy = RetryPolicy.from_mapping(json.loads(str(row["retry_json"])))
            derived = decide_retry(
                effect_profile=lease.effect_profile,
                retry_policy=policy,
                attempt_number=lease.attempt_number,
                error_class=structured_error.error_class,
                manual_retry=lease.manual_retry,
            )
            if retry_decision is not derived:
                raise RetryDecisionConflictError(
                    "retry decision does not match the frozen stage policy"
                )

            clock_regressed = self._clock_regressed(
                row["revision_clock_high_water_at"], current,
            )
            usage_unknown = (
                self._mark_unaccounted_model_usage_unknown_in_transaction(
                    connection, row, now_text=current_text,
                )
            )
            if usage_unknown or clock_regressed:
                # Policy-derived retries are valid only while resource
                # accounting and time are authoritative.
                derived = RetryDecision.NEEDS_ATTENTION

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

            terminal_metric = (
                {"timed_out": True}
                if attempt_state is AttemptState.TIMED_OUT
                else {"failed": True}
            )
            metrics = self._attempt_metrics(
                row,
                **terminal_metric,
                error_class=structured_error.error_class,
                retry_decision=derived.value,
                usage_accounting_unknown=usage_unknown,
                clock_regressed=clock_regressed,
            )
            changed_attempt = connection.execute(
                """
                UPDATE attempts
                SET state=?, ended_at=?, structured_error_json=?,
                    metrics_json=?
                WHERE owner_user_id=? AND id=? AND unit_id=? AND fence=?
                  AND worker_id=? AND state='running' AND ended_at IS NULL
                """,
                (
                    attempt_state.value, current_text,
                    structured_error.payload_json, metrics,
                    lease.owner_user_id, lease.attempt_id, lease.unit_id,
                    lease.fence, lease.worker_id,
                ),
            )
            changed_unit = connection.execute(
                """
                UPDATE units
                SET state=?, next_attempt_at=?, lease_worker_id=NULL,
                    active_attempt_id=NULL, lease_expires_at=NULL,
                    error_class=?, terminal_detail_json=?,
                    manual_retry_generation=CASE
                      WHEN ?='needs_attention' THEN ?
                      ELSE manual_retry_generation
                    END,
                    updated_at=?
                WHERE owner_user_id=? AND id=? AND state='running'
                  AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                """,
                (
                    unit_state, next_attempt_at, structured_error.error_class,
                    structured_error.payload_json, unit_state,
                    row["revision_retry_generation"], current_text,
                    lease.owner_user_id, lease.unit_id, lease.attempt_id,
                    lease.fence, lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("attempt failure compare-and-set failed")
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )
            self._settle_workload_in_transaction(
                connection,
                lease.owner_user_id,
                lease.workload_id,
                now_text=current_text,
            )
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
            before_execution = row["unit_state"] == "leased"
            clock_regressed = self._clock_regressed(
                row["revision_clock_high_water_at"], current,
            )
            usage_unknown = (
                not before_execution
                and self._mark_unaccounted_model_usage_unknown_in_transaction(
                    connection, row, now_text=current_text,
                )
            )
            # A manual grant authorises one execution, not an unbounded retry
            # chain.  It may be returned only while the runner has not started;
            # after that boundary another explicit decision is required.
            manual_grant_returned = lease.manual_retry and before_execution
            retry_available = (
                lease.attempt_number < lease.retry_policy.max_attempts
                or manual_grant_returned
            ) and not usage_unknown and not clock_regressed
            if not retry_available:
                target_state = "needs_attention"
                next_attempt_at = None
            elif before_execution:
                target_state = "pending"
                next_attempt_at = None
            else:
                target_state = "retry_wait"
                next_attempt_at = current_text
            error = StructuredAttemptError.create(
                "cancelled",
                code=f"attempt.{reason_code}",
                message_key="ERR_DURABLE_ATTEMPT_ABANDONED",
                retry="automatic" if retry_available else "manual",
                occurred_at=current,
                details_redacted={
                    "retry_safe": not usage_unknown and not clock_regressed,
                    "retry_budget_available": retry_available,
                },
            )
            metrics = self._attempt_metrics(
                row,
                abandoned=True,
                abandonment_reason=reason_code,
                usage_accounting_unknown=usage_unknown,
                clock_regressed=clock_regressed,
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
                    manual_retry_tokens=manual_retry_tokens+?,
                    manual_retry_generation=CASE
                      WHEN ?='needs_attention' THEN ?
                      ELSE manual_retry_generation
                    END,
                    partial_output=0, terminal_detail_json=?, updated_at=?
                WHERE owner_user_id=? AND id=?
                  AND state IN ('leased', 'running')
                  AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                """,
                (
                    target_state, next_attempt_at, unit_error,
                    int(manual_grant_returned), target_state,
                    row["revision_retry_generation"], unit_detail,
                    current_text,
                    lease.owner_user_id, lease.unit_id,
                    lease.attempt_id, lease.fence, lease.worker_id,
                ),
            )
            if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                raise DurableStoreError("attempt abandonment compare-and-set failed")
            self._advance_revision_clock_in_transaction(
                connection,
                owner=lease.owner_user_id,
                revision_id=lease.revision_id,
                now_text=current_text,
            )
            self._settle_workload_in_transaction(
                connection,
                lease.owner_user_id,
                lease.workload_id,
                now_text=current_text,
            )
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
                    u.owner_user_id, u.id AS unit_id, u.revision_id, u.unit_key,
                    u.state AS unit_state, u.attempt_count,
                    u.manual_retry_tokens,
                    u.active_attempt_id, u.fence,
                    u.lease_worker_id, u.lease_expires_at,
                    u.updated_at AS unit_updated_at,
                    r.workload_id,
                    r.manual_retry_generation AS revision_retry_generation,
                    usage.clock_high_water_at,
                    s.effect_profile, s.retry_json, s.resources_json,
                    a.state AS attempt_state, a.model_snapshot_json,
                    a.metrics_json
                FROM units u
                JOIN revisions r
                  ON r.owner_user_id=u.owner_user_id AND r.id=u.revision_id
                JOIN revision_usage usage
                  ON usage.owner_user_id=u.owner_user_id
                 AND usage.revision_id=u.revision_id
                JOIN stages s
                  ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
                 AND s.revision_id=u.revision_id
                JOIN attempts a
                  ON a.owner_user_id=u.owner_user_id
                 AND a.id=u.active_attempt_id AND a.unit_id=u.id
                 AND a.fence=u.fence
                WHERE u.state IN ('leased', 'running')
                  AND (
                    u.lease_expires_at<=?
                    OR julianday(usage.clock_high_water_at)
                        > julianday(?) + (? / 86400.0)
                    OR julianday(u.updated_at)
                        > julianday(?) + (? / 86400.0)
                  )
                ORDER BY u.lease_expires_at, u.owner_user_id, u.id
                LIMIT ?
                """,
                (
                    current_text,
                    current_text,
                    _CLOCK_REGRESSION_TOLERANCE.total_seconds(),
                    current_text,
                    _CLOCK_REGRESSION_TOLERANCE.total_seconds(),
                    batch_size,
                ),
            ).fetchall()
            affected_workloads: set[tuple[str, str]] = set()
            for row in rows:
                expired += 1
                affected_workloads.add((
                    str(row["owner_user_id"]),
                    str(row["workload_id"]),
                ))
                attempt_number = int(row["attempt_count"])
                policy = RetryPolicy.from_mapping(
                    json.loads(str(row["retry_json"]))
                )
                metrics_value = json.loads(str(row["metrics_json"]))
                manual_retry = metrics_value.get("manual_retry") is True
                before_execution = row["unit_state"] == "leased"
                clock_regressed = (
                    self._clock_regressed(row["clock_high_water_at"], current)
                    or self._clock_regressed(row["unit_updated_at"], current)
                )
                usage_unknown = (
                    not before_execution
                    and self._mark_unaccounted_model_usage_unknown_in_transaction(
                        connection, row, now_text=current_text,
                    )
                )
                error_code = (
                    "lease.clock_regressed"
                    if clock_regressed
                    else (
                        "lease.expired_before_execution"
                        if before_execution
                        else "lease.expired_during_execution"
                    )
                )
                retry_mode = "automatic"
                if usage_unknown or clock_regressed:
                    retry_mode = "manual"
                elif (
                    not before_execution
                    and row["effect_profile"] == "reconcilable"
                ):
                    retry_mode = "reconcile"
                elif not before_execution and row["effect_profile"] == "manual_only":
                    retry_mode = "manual"
                elif manual_retry:
                    retry_mode = "manual"
                elif attempt_number >= policy.max_attempts:
                    retry_mode = "manual" if before_execution else "never"
                error = StructuredAttemptError.create(
                    "lease_lost",
                    code=error_code,
                    message_key="ERR_DURABLE_LEASE_EXPIRED",
                    retry=retry_mode,
                    occurred_at=current,
                    details_redacted={
                        "execution_started": not before_execution,
                        "attempt_number": attempt_number,
                        "max_attempts": policy.max_attempts,
                        "usage_accounting_unknown": usage_unknown,
                        "clock_regressed": clock_regressed,
                    },
                )
                metrics_value.update({
                    "lease_expired": True,
                    "reconciled_at": current_text,
                    "usage_accounting_unknown": usage_unknown,
                    "clock_regressed": clock_regressed,
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
                if usage_unknown or clock_regressed:
                    target = "needs_attention"
                    next_attempt_at = None
                    attention += 1
                elif before_execution:
                    next_attempt_at = None
                    if attempt_number < policy.max_attempts or manual_retry:
                        target = "pending"
                        returned += 1
                    else:
                        target = "needs_attention"
                        attention += 1
                else:
                    effect = row["effect_profile"]
                    if effect in {"reconcilable", "manual_only"} or manual_retry:
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
                        manual_retry_tokens=manual_retry_tokens+?,
                        manual_retry_generation=CASE
                          WHEN ?='needs_attention' THEN ?
                          ELSE manual_retry_generation
                        END,
                        error_class=?, terminal_detail_json=?, updated_at=?
                    WHERE owner_user_id=? AND id=? AND state=?
                      AND active_attempt_id=? AND fence=? AND lease_worker_id=?
                    """,
                    (
                        target, next_attempt_at,
                        int(before_execution and manual_retry), target,
                        row["revision_retry_generation"], "lease_lost",
                        error.payload_json, current_text, row["owner_user_id"],
                        row["unit_id"], row["unit_state"],
                        row["active_attempt_id"], row["fence"],
                        row["lease_worker_id"],
                    ),
                )
                if changed_attempt.rowcount != 1 or changed_unit.rowcount != 1:
                    raise DurableStoreError("expired lease compare-and-set failed")
                self._advance_revision_clock_in_transaction(
                    connection,
                    owner=str(row["owner_user_id"]),
                    revision_id=str(row["revision_id"]),
                    now_text=current_text,
                )
            promoted = self._promote_due_retries_in_transaction(
                connection,
                current_text,
                limit=max(0, batch_size - len(rows)),
            )
            for owner, workload_id in sorted(affected_workloads):
                self._settle_workload_in_transaction(
                    connection,
                    owner,
                    workload_id,
                    now_text=current_text,
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
                "workloads", "revisions", "stages", "stage_placements",
                "stage_dependencies",
                "stage_materialization", "revision_usage", "sources",
                "units", "attempts",
                "results", "unit_dependencies", "dependencies",
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
