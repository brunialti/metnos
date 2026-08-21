"""Owner-scoped, transport-neutral control façade for durable workloads.

The façade is the only F9 read/control surface.  It returns closed DTOs rather
than store rows, plans, source locators, result payloads or execution snapshots.
HTTP, chat and any future approved executor are therefore thin adapters over
the same owner-scoped operations.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    EventRecord,
    RevisionRecord,
    UnitCounters,
    UnitReadRecord,
    UnitState,
    WorkloadRecord,
    WorkloadState,
)
from .storage import (
    DurableStoreError,
    DurableWorkloadStore,
    IdempotencyConflictError,
    InvalidTransitionError,
    VersionConflictError,
    WorkloadNotFoundError,
)


log = logging.getLogger("metnos.durable_workloads.control")

DTO_SCHEMA_VERSION = "metnos.durable-control/1"
MAX_PAGE_SIZE = 100
MAX_STREAM_EVENTS = 500
_CURSOR_VERSION = 1


class DurableControlError(RuntimeError):
    """A stable, non-sensitive failure exposed by the control façade."""

    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WorkloadDTO:
    workload_id: str
    state: str
    priority: str
    version: int
    active_revision_id: str | None
    created_at: str
    updated_at: str
    counters: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload_id,
            "state": self.state,
            "priority": self.priority,
            "version": self.version,
            "active_revision_id": self.active_revision_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "counters": dict(self.counters),
        }


@dataclass(frozen=True, slots=True)
class RevisionDTO:
    revision_id: str
    number: int
    plan_digest: str
    inventory_digest: str | None
    inventory_sealed: bool
    expected_source_count: int
    admitted_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "number": self.number,
            "plan_digest": self.plan_digest,
            "inventory_digest": self.inventory_digest,
            "inventory_sealed": self.inventory_sealed,
            "expected_source_count": self.expected_source_count,
            "admitted_at": self.admitted_at,
        }


@dataclass(frozen=True, slots=True)
class EventDTO:
    event_id: int
    event_type: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class UnitDTO:
    unit_id: str
    revision_id: str
    stage_key: str
    state: str
    attempt_count: int
    next_attempt_at: str | None
    error_code: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "revision_id": self.revision_id,
            "stage_key": self.stage_key,
            "state": self.state,
            "attempt_count": self.attempt_count,
            "next_attempt_at": self.next_attempt_at,
            "error_code": self.error_code,
            "updated_at": self.updated_at,
        }


def _counters(value: UnitCounters) -> dict[str, int]:
    return {
        "discovered": value.discovered,
        "committed": value.committed,
        "failed": value.failed,
        "skipped": value.skipped,
        "attention": value.attention,
        "pending": value.pending,
        "total": value.total,
    }


def _workload_dto(record: WorkloadRecord, counters: UnitCounters) -> WorkloadDTO:
    return WorkloadDTO(
        workload_id=record.workload_id,
        state=record.state.value,
        priority=record.priority,
        version=record.version,
        active_revision_id=record.active_revision_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        counters=_counters(counters),
    )


def _revision_dto(record: RevisionRecord) -> RevisionDTO:
    return RevisionDTO(
        revision_id=record.revision_id,
        number=record.number,
        plan_digest=record.plan_digest,
        inventory_digest=record.inventory_digest,
        inventory_sealed=record.inventory_sealed,
        expected_source_count=record.expected_source_count,
        admitted_at=record.admitted_at,
    )


def _event_dto(record: EventRecord) -> EventDTO:
    return EventDTO(
        event_id=record.event_id,
        event_type=record.event_type.value,
        created_at=record.created_at,
    )


def _unit_dto(record: UnitReadRecord) -> UnitDTO:
    return UnitDTO(
        unit_id=record.unit_id,
        revision_id=record.revision_id,
        stage_key=record.stage_key,
        state=record.state.value,
        attempt_count=record.attempt_count,
        next_attempt_at=record.next_attempt_at,
        error_code=record.error_class,
        updated_at=record.updated_at,
    )


class DurableWorkloadControl:
    """Closed control/read model backed by one explicitly-owned store."""

    def __init__(self, store: DurableWorkloadStore, *, cursor_secret: str | bytes) -> None:
        if not isinstance(store, DurableWorkloadStore):
            raise TypeError("store must be DurableWorkloadStore")
        secret = cursor_secret.encode("utf-8") if isinstance(cursor_secret, str) else cursor_secret
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("cursor_secret must be non-empty bytes or text")
        self._store = store
        self._cursor_secret = secret

    @staticmethod
    def _page_size(value: int | None) -> int:
        if value is None:
            return 50
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGE_SIZE:
            raise DurableControlError("durable_workload.invalid_limit", 400)
        return value

    def _encode_cursor(self, *, kind: str, position: dict[str, Any], state: str | None) -> str:
        payload = {
            "kind": kind,
            "position": position,
            "state": state,
            "version": _CURSOR_VERSION,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
        return "{}.{}".format(
            base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        )

    def _decode_cursor(self, value: str | None, *, kind: str, state: str | None) -> dict[str, Any] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or len(value) > 1024:
            raise DurableControlError("durable_workload.invalid_cursor", 400)
        try:
            encoded, encoded_signature = value.split(".", 1)
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            signature = base64.urlsafe_b64decode(
                encoded_signature + "=" * (-len(encoded_signature) % 4)
            )
            expected = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
            raise DurableControlError("durable_workload.invalid_cursor", 400) from None
        if (
            not hmac.compare_digest(signature, expected)
            or not isinstance(payload, dict)
            or set(payload) != {"kind", "position", "state", "version"}
            or payload["kind"] != kind
            or payload["state"] != state
            or payload["version"] != _CURSOR_VERSION
            or not isinstance(payload["position"], dict)
        ):
            raise DurableControlError("durable_workload.invalid_cursor", 400)
        return payload["position"]

    @staticmethod
    def _map_failure(exc: Exception) -> DurableControlError:
        if isinstance(exc, WorkloadNotFoundError):
            return DurableControlError("durable_workload.not_found", 404)
        if isinstance(exc, VersionConflictError):
            return DurableControlError("durable_workload.version_conflict", 409)
        if isinstance(exc, IdempotencyConflictError):
            return DurableControlError("durable_workload.idempotency_conflict", 409)
        if isinstance(exc, InvalidTransitionError):
            return DurableControlError("durable_workload.illegal_state", 409)
        if isinstance(exc, (DurableStoreError, ValueError, TypeError)):
            return DurableControlError("durable_workload.invalid_request", 400)
        return DurableControlError("durable_workload.unavailable", 503)

    def _read(self, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except DurableControlError:
            raise
        except Exception as exc:
            raise self._map_failure(exc) from None

    def list_workloads(
        self,
        owner_user_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        page_size = self._page_size(limit)
        try:
            normalized_state = WorkloadState(state).value if state is not None else None
        except ValueError:
            raise DurableControlError("durable_workload.invalid_request", 400) from None
        position = self._decode_cursor(cursor, kind="workloads", state=normalized_state)
        before = None
        if position is not None:
            if (
                set(position) != {"updated_at", "workload_id"}
                or not isinstance(position["updated_at"], str)
                or not isinstance(position["workload_id"], str)
            ):
                raise DurableControlError("durable_workload.invalid_cursor", 400)
            before = (position["updated_at"], position["workload_id"])

        def operation() -> dict[str, Any]:
            records = self._store.list_workloads_page(
                owner_user_id, state=normalized_state, before=before, limit=page_size + 1,
            )
            visible = records[:page_size]
            counters = self._store.unit_counters_many(
                owner_user_id,
                tuple(record.workload_id for record in visible),
            )
            next_cursor = None
            if len(records) > page_size and visible:
                last = visible[-1]
                next_cursor = self._encode_cursor(
                    kind="workloads",
                    position={"updated_at": last.updated_at, "workload_id": last.workload_id},
                    state=normalized_state,
                )
            return {
                "schema_version": DTO_SCHEMA_VERSION,
                "items": [
                    _workload_dto(
                        record,
                        counters[record.workload_id],
                    ).to_dict()
                    for record in visible
                ],
                "next_cursor": next_cursor,
            }
        return self._read(operation)

    def detail(self, owner_user_id: str, workload_id: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            record = self._store.get_workload(owner_user_id, workload_id)
            revision = None
            if record.active_revision_id is not None:
                revision = _revision_dto(
                    self._store.get_revision(owner_user_id, record.active_revision_id)
                ).to_dict()
                # The execution summary is a closed projection, not the raw
                # frozen plan or its catalog/policy snapshots.
                revision["execution"] = self._store.execution_summary(
                    owner_user_id, workload_id,
                )
            return {
                "schema_version": DTO_SCHEMA_VERSION,
                "workload": _workload_dto(
                    record, self._store.unit_counters(owner_user_id, workload_id),
                ).to_dict(),
                "revision": revision,
            }
        return self._read(operation)

    def list_events(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        recent: bool = False,
    ) -> dict[str, Any]:
        page_size = self._page_size(limit)
        if not isinstance(recent, bool):
            raise DurableControlError("durable_workload.invalid_request", 400)
        if recent:
            if cursor is not None:
                raise DurableControlError("durable_workload.invalid_cursor", 400)
            records = self._read(lambda: self._store.list_recent_events(
                owner_user_id, workload_id, limit=page_size,
            ))
            return {
                "schema_version": DTO_SCHEMA_VERSION,
                "items": [_event_dto(record).to_dict() for record in records],
                "next_cursor": None,
            }
        position = self._decode_cursor(cursor, kind="events", state=None)
        after_event_id = 0
        if position is not None:
            if (
                set(position) != {"event_id"}
                or isinstance(position["event_id"], bool)
                or not isinstance(position["event_id"], int)
                or position["event_id"] < 0
            ):
                raise DurableControlError("durable_workload.invalid_cursor", 400)
            after_event_id = position["event_id"]
        records = self._read(lambda: self._store.list_events(
            owner_user_id, workload_id, after_event_id=after_event_id, limit=page_size + 1,
        ))
        visible = records[:page_size]
        next_cursor = None
        if len(records) > page_size and visible:
            next_cursor = self._encode_cursor(
                kind="events", position={"event_id": visible[-1].event_id}, state=None,
            )
        return {
            "schema_version": DTO_SCHEMA_VERSION,
            "items": [_event_dto(record).to_dict() for record in visible],
            "next_cursor": next_cursor,
        }

    def stream_events(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        after_event_id: int,
    ) -> tuple[EventDTO, ...]:
        """Return the next bounded persistent event batch for an SSE adapter.

        Unlike the paged JSON endpoint this method deliberately takes the
        monotonic database ID used by ``Last-Event-ID``.  It remains internal
        to the façade: no event payload, plan, source or result crosses the
        transport boundary.
        """

        if (
            isinstance(after_event_id, bool)
            or not isinstance(after_event_id, int)
            or after_event_id < 0
        ):
            raise DurableControlError("durable_workload.invalid_last_event_id", 400)
        records = self._read(lambda: self._store.list_events(
            owner_user_id,
            workload_id,
            after_event_id=after_event_id,
            limit=MAX_STREAM_EVENTS,
        ))
        return tuple(_event_dto(record) for record in records)

    def list_units(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        page_size = self._page_size(limit)
        try:
            normalized_state = state if state is None else str(state)
            if normalized_state is not None:
                normalized_state = UnitState(normalized_state).value
        except ValueError:
            raise DurableControlError("durable_workload.invalid_request", 400) from None
        position = self._decode_cursor(cursor, kind="units", state=normalized_state)
        before = None
        if position is not None:
            if (
                set(position) != {"updated_at", "unit_id"}
                or not isinstance(position["updated_at"], str)
                or not isinstance(position["unit_id"], str)
            ):
                raise DurableControlError("durable_workload.invalid_cursor", 400)
            before = (position["updated_at"], position["unit_id"])
        records = self._read(lambda: self._store.list_units(
            owner_user_id, workload_id, state=normalized_state, before=before, limit=page_size + 1,
        ))
        visible = records[:page_size]
        next_cursor = None
        if len(records) > page_size and visible:
            last = visible[-1]
            next_cursor = self._encode_cursor(
                kind="units",
                position={"updated_at": last.updated_at, "unit_id": last.unit_id},
                state=normalized_state,
            )
        return {
            "schema_version": DTO_SCHEMA_VERSION,
            "items": [_unit_dto(record).to_dict() for record in visible],
            "next_cursor": next_cursor,
        }

    def _control(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        command: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        methods = {
            "pause": self._store.request_pause,
            "resume": self._store.request_resume,
            "cancel": self._store.request_cancel,
        }
        method = methods[command]
        def operation() -> dict[str, Any]:
            record = method(
                owner_user_id,
                workload_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            log.info(
                "durable_workload_control command=%s state=%s version=%d",
                command,
                record.state.value,
                record.version,
            )
            return {
                "schema_version": DTO_SCHEMA_VERSION,
                "command": command,
                "workload": _workload_dto(
                    record, self._store.unit_counters(owner_user_id, workload_id),
                ).to_dict(),
            }
        return self._read(operation)

    def pause(self, owner_user_id: str, workload_id: str, *, expected_version: int, idempotency_key: str) -> dict[str, Any]:
        return self._control(
            owner_user_id, workload_id, command="pause",
            expected_version=expected_version, idempotency_key=idempotency_key,
        )

    def resume(self, owner_user_id: str, workload_id: str, *, expected_version: int, idempotency_key: str) -> dict[str, Any]:
        return self._control(
            owner_user_id, workload_id, command="resume",
            expected_version=expected_version, idempotency_key=idempotency_key,
        )

    def cancel(self, owner_user_id: str, workload_id: str, *, expected_version: int, idempotency_key: str) -> dict[str, Any]:
        return self._control(
            owner_user_id, workload_id, command="cancel",
            expected_version=expected_version, idempotency_key=idempotency_key,
        )

    def resolve_attention(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        decision: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Resolve an owner-visible manual stop without accepting free text.

        The durable store already records the audit decision transactionally.
        Keeping the boundary to the two closed decisions avoids persisting a
        browser-supplied note in the workload audit trail.
        """

        if decision not in {"retry", "cancel"}:
            raise DurableControlError("durable_workload.invalid_request", 400)

        def operation() -> dict[str, Any]:
            record = self._store.record_attention_resolution(
                owner_user_id,
                workload_id,
                decision=decision,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            log.info(
                "durable_workload_control command=resolve_attention decision=%s state=%s version=%d",
                decision,
                record.state.value,
                record.version,
            )
            return {
                "schema_version": DTO_SCHEMA_VERSION,
                "command": "resolve_attention",
                "decision": decision,
                "workload": _workload_dto(
                    record, self._store.unit_counters(owner_user_id, workload_id),
                ).to_dict(),
            }

        return self._read(operation)


__all__ = [
    "DTO_SCHEMA_VERSION",
    "MAX_PAGE_SIZE",
    "MAX_STREAM_EVENTS",
    "DurableControlError",
    "DurableWorkloadControl",
    "EventDTO",
    "RevisionDTO",
    "UnitDTO",
    "WorkloadDTO",
]
