"""Inactive durable integration substrate for RM-0008 F6 retention.

No productive owner adapter is registered here.  The only supported adapter
contract is deliberately narrow: an owner whose authoritative rows live in the
same SQLite transaction as the retention graph.  External stores require a
later, separately proved protocol; they must not be approximated with a
best-effort callback.

Every pending or applying outbox event is a conservative global root in
``executor_birth_retention``.  A crash, an absent adapter, or a failed adapter
therefore stops collection rather than manufacturing absence of a reference.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol, runtime_checkable

from executor_birth_retention import (
    NodeKey, NodeType, RetentionError, _open, _text, _timestamp,
)


_EVENT_DOMAIN = b"metnos.executor-birth.retention-outbox/v1\0"
_MAX_PAYLOAD_BYTES = 64 * 1024


class RetentionIntegrationError(RetentionError):
    pass


class OutboxEventKind(str, Enum):
    OWNER_RECONCILE = "owner_reconcile"


class OutboxState(str, Enum):
    PENDING = "pending"
    APPLYING = "applying"
    APPLIED = "applied"


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    event_version: int
    key: NodeKey
    event_kind: OutboxEventKind
    payload: Mapping[str, object]
    state: OutboxState
    created_at: str
    applied_at: str | None
    attempts: int
    last_error: str | None


@runtime_checkable
class CoLocatedOwnerAdapter(Protocol):
    """Adapter allowed to mutate owner and graph rows in one SQLite tx."""

    node_type: NodeType

    def reconcile(self, connection: sqlite3.Connection,
                  event: OutboxEvent) -> None:
        """Apply exactly one idempotent event using ``connection`` only."""


class OwnerAdapterRegistry:
    """Closed exact NodeType registry with no fallback or name resolution."""

    __slots__ = ("_adapters",)

    def __init__(self, adapters: Iterable[CoLocatedOwnerAdapter]) -> None:
        exact: dict[NodeType, CoLocatedOwnerAdapter] = {}
        for adapter in adapters:
            node_type = getattr(adapter, "node_type", None)
            if (not isinstance(node_type, NodeType)
                    or not callable(getattr(adapter, "reconcile", None))):
                raise RetentionIntegrationError(
                    "retention_owner_adapter_invalid")
            if node_type in exact:
                raise RetentionIntegrationError(
                    "retention_owner_adapter_duplicate", node_type.value)
            exact[node_type] = adapter
        self._adapters = MappingProxyType(exact)

    @property
    def node_types(self) -> tuple[NodeType, ...]:
        return tuple(sorted(self._adapters, key=lambda item: item.value))

    def resolve(self, node_type: NodeType) -> CoLocatedOwnerAdapter:
        if not isinstance(node_type, NodeType):
            raise RetentionIntegrationError("retention_owner_adapter_invalid")
        try:
            return self._adapters[node_type]
        except KeyError as exc:
            raise RetentionIntegrationError(
                "retention_owner_adapter_missing", node_type.value) from exc


# Intentionally empty until an owner can prove same-transaction authority.
PRODUCTIVE_OWNER_ADAPTERS = OwnerAdapterRegistry(())


def _canonical_payload(payload: Mapping[str, object]) -> str:
    if not isinstance(payload, Mapping):
        raise RetentionIntegrationError("retention_outbox_invalid", "payload")
    try:
        encoded = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RetentionIntegrationError(
            "retention_outbox_invalid", "payload") from exc
    if (not isinstance(decoded, dict) or decoded != dict(payload)
            or len(encoded.encode("ascii")) > _MAX_PAYLOAD_BYTES):
        raise RetentionIntegrationError("retention_outbox_invalid", "payload")
    return encoded


def _event_id(key: NodeKey, kind: OutboxEventKind, payload_json: str,
              created_at: str) -> str:
    body = json.dumps({
        "created_at": created_at,
        "event_kind": kind.value,
        "event_version": 1,
        "node_id": key.node_id,
        "node_type": key.node_type.value,
        "payload": json.loads(payload_json),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return "sha256:" + hashlib.sha256(_EVENT_DOMAIN + body).hexdigest()


def enqueue_owner_event(*, key: NodeKey, payload: Mapping[str, object],
                        created_at: str, db_path: Path) -> str:
    """Durably enqueue one deterministic event; an exact replay is a no-op."""
    if not isinstance(key, NodeKey):
        raise RetentionIntegrationError("retention_outbox_invalid", "node key")
    created = _timestamp(created_at)
    payload_json = _canonical_payload(payload)
    kind = OutboxEventKind.OWNER_RECONCILE
    identifier = _event_id(key, kind, payload_json, created)
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT event_version,node_type,node_id,event_kind,payload_json,created_at "
            "FROM retention_outbox WHERE event_id=?", (identifier,),
        ).fetchone()
        expected = (1, key.node_type.value, key.node_id, kind.value,
                    payload_json, created)
        if existing is None:
            connection.execute(
                "INSERT INTO retention_outbox(event_id,event_version,node_type,node_id,"
                "event_kind,payload_json,state,created_at,attempts) "
                "VALUES(?,?,?,?,?,?,'pending',?,0)",
                (identifier, *expected),
            )
        elif tuple(existing) != expected:
            raise RetentionIntegrationError(
                "retention_outbox_conflict", identifier)
        connection.commit()
        return identifier
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def _decode_event(row: sqlite3.Row) -> OutboxEvent:
    try:
        if int(row["event_version"]) != 1:
            raise ValueError("event version")
        key = NodeKey(NodeType(row["node_type"]), str(row["node_id"]))
        kind = OutboxEventKind(row["event_kind"])
        state = OutboxState(row["state"])
        payload_json = str(row["payload_json"])
        payload = json.loads(payload_json)
        if _canonical_payload(payload) != payload_json:
            raise ValueError("payload encoding")
        created = _timestamp(row["created_at"])
        applied = (None if row["applied_at"] is None
                   else _timestamp(row["applied_at"]))
        attempts = int(row["attempts"])
        if attempts < 0 or isinstance(row["attempts"], bool):
            raise ValueError("attempts")
        expected_id = _event_id(key, kind, payload_json, created)
        if row["event_id"] != expected_id:
            raise ValueError("event id")
        last_error = row["last_error"]
        if last_error is not None:
            _text(last_error, "last_error")
        return OutboxEvent(expected_id, 1, key, kind,
                           MappingProxyType(payload), state, created, applied,
                           attempts, last_error)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError,
            RetentionError) as exc:
        raise RetentionIntegrationError(
            "retention_outbox_invalid", "stored event") from exc


def pending_events(*, db_path: Path) -> tuple[OutboxEvent, ...]:
    connection = _open(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM retention_outbox WHERE state IN ('pending','applying') "
            "ORDER BY created_at,event_id"
        ).fetchall()
        return tuple(_decode_event(row) for row in rows)
    finally:
        connection.close()


def _record_failure(db_path: Path, event_id: str, code: str) -> None:
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE retention_outbox SET state='pending',attempts=attempts+1,"
            "last_error=? WHERE event_id=? AND state IN ('pending','applying')",
            (_text(code, "outbox error"), event_id),
        )
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def drain_outbox(*, registry: OwnerAdapterRegistry, applied_at: str,
                 db_path: Path, limit: int = 100,
                 after_adapter=None) -> tuple[str, ...]:
    """Reconcile events atomically; never skips an absent or failed owner."""
    if not isinstance(registry, OwnerAdapterRegistry):
        raise RetentionIntegrationError("retention_owner_registry_invalid")
    applied = _timestamp(applied_at)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise RetentionIntegrationError("retention_outbox_invalid", "limit")
    if after_adapter is not None and not callable(after_adapter):
        raise RetentionIntegrationError("retention_outbox_invalid", "crash seam")
    identifiers = tuple(event.event_id for event in pending_events(db_path=db_path)[:limit])
    completed: list[str] = []
    for identifier in identifiers:
        connection = _open(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM retention_outbox WHERE event_id=?", (identifier,),
            ).fetchone()
            if row is None:
                raise RetentionIntegrationError(
                    "retention_outbox_invalid", "event disappeared")
            event = _decode_event(row)
            if event.state is OutboxState.APPLIED:
                connection.commit()
                continue
            adapter = registry.resolve(event.key.node_type)
            connection.execute(
                "UPDATE retention_outbox SET state='applying' WHERE event_id=?",
                (identifier,),
            )
            adapter.reconcile(connection, event)
            if after_adapter is not None:
                after_adapter(event)
            changed = connection.execute(
                "UPDATE retention_outbox SET state='applied',applied_at=?,"
                "attempts=attempts+1,last_error=NULL "
                "WHERE event_id=? AND state='applying'",
                (applied, identifier),
            )
            if changed.rowcount != 1:
                raise RetentionIntegrationError(
                    "retention_outbox_conflict", identifier)
            connection.commit()
            completed.append(identifier)
        except RetentionIntegrationError as exc:
            if connection.in_transaction:
                connection.rollback()
            _record_failure(db_path, identifier, exc.code)
            raise
        except Exception as exc:
            if connection.in_transaction:
                connection.rollback()
            _record_failure(db_path, identifier, "retention_owner_adapter_failed")
            raise RetentionIntegrationError(
                "retention_owner_adapter_failed", identifier) from exc
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    return tuple(completed)
