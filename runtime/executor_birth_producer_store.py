"""Native one-use store for authenticated RM-0008 producer receipts.

The store is deliberately ignorant of manifests and publication.  Authority
comes exclusively from the core-owned :class:`IssuerRegistry`; the expected
binding is supplied from the already acquired Birth snapshot.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_receipts import (
    IssuerRegistry,
    ProducerReceipt,
    ReceiptError,
    verify_producer_receipt,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS birth_producer_receipts (
    receipt_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL,
    encoded BLOB NOT NULL,
    issuer_id TEXT NOT NULL,
    objective_hash TEXT NOT NULL,
    candidate_source_id TEXT NOT NULL,
    executor_origin TEXT NOT NULL,
    revision_authorship TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('available','consumed','expired')),
    registered_at TEXT NOT NULL,
    consumed_at TEXT,
    request_id TEXT,
    CHECK (
      (state = 'available' AND consumed_at IS NULL AND request_id IS NULL) OR
      (state = 'consumed' AND consumed_at IS NOT NULL AND request_id IS NOT NULL) OR
      (state = 'expired' AND consumed_at IS NOT NULL AND request_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_birth_producer_receipt_hash
ON birth_producer_receipts(receipt_hash);
"""


@dataclass(frozen=True, slots=True)
class ProducerReceiptBinding:
    objective_hash: str
    candidate_source_id: str
    executor_origin: ExecutorOrigin
    revision_authorship: RevisionAuthor


def producer_receipt_hash(encoded: bytes) -> str:
    if not isinstance(encoded, bytes):
        raise ReceiptError("producer_receipt_invalid", "encoded bytes")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReceiptError("producer_receipt_invalid", "verification time")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    value = _utc(value)
    if value.microsecond:
        raise ReceiptError("producer_receipt_invalid", "time precision")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise ReceiptError("producer_receipt_invalid", "request_id")
    return value


def _open(path: Path) -> sqlite3.Connection:
    if not isinstance(path, Path):
        raise ReceiptError("producer_receipt_store_invalid", "db_path")
    connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    return connection


def _require_binding(receipt: ProducerReceipt, binding: ProducerReceiptBinding) -> None:
    if not isinstance(binding, ProducerReceiptBinding):
        raise ReceiptError("producer_receipt_binding_invalid", "binding")
    expected = {
        "objective_hash": binding.objective_hash,
        "candidate_source_id": binding.candidate_source_id,
        "executor_origin": binding.executor_origin,
        "revision_authorship": binding.revision_authorship,
    }
    for field, value in expected.items():
        if getattr(receipt, field) != value:
            raise ReceiptError("producer_receipt_binding_invalid", field)


def register_producer_receipt(
    encoded: bytes,
    *,
    registry: IssuerRegistry,
    now: datetime,
    db_path: Path,
) -> ProducerReceipt:
    """Authenticate and register one receipt before a Birth request uses it."""
    instant = _utc(now)
    receipt = verify_producer_receipt(encoded, registry=registry, now=instant)
    digest = producer_receipt_hash(encoded)
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO birth_producer_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt.receipt_id, digest, encoded, receipt.issuer_id,
                    receipt.objective_hash, receipt.candidate_source_id,
                    receipt.executor_origin.value, receipt.revision_authorship.value,
                    receipt.expires_at, "available", _iso(instant), None, None,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ReceiptError("producer_receipt_replay", "already_registered") from exc
        connection.commit()
        return receipt
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def consume_producer_receipt(
    encoded: bytes,
    *,
    registry: IssuerRegistry,
    binding: ProducerReceiptBinding,
    request_id: str,
    now: datetime,
    db_path: Path,
) -> ProducerReceipt:
    """Verify and atomically consume the exact registered receipt once."""
    instant = _utc(now)
    request = _request_id(request_id)
    # Authenticate and check temporal validity before consulting mutable state.
    receipt = verify_producer_receipt(encoded, registry=registry, now=instant)
    _require_binding(receipt, binding)
    digest = producer_receipt_hash(encoded)
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM birth_producer_receipts WHERE receipt_id=?",
            (receipt.receipt_id,),
        ).fetchone()
        if row is None:
            raise ReceiptError("producer_receipt_invalid", "not_registered")
        if (
            row["receipt_hash"] != digest
            or bytes(row["encoded"]) != encoded
            or row["issuer_id"] != receipt.issuer_id
            or row["objective_hash"] != receipt.objective_hash
            or row["candidate_source_id"] != receipt.candidate_source_id
            or row["executor_origin"] != receipt.executor_origin.value
            or row["revision_authorship"] != receipt.revision_authorship.value
            or row["expires_at"] != receipt.expires_at
        ):
            raise ReceiptError("producer_receipt_invalid", "store_binding")
        if row["state"] != "available":
            raise ReceiptError("producer_receipt_replay", str(row["state"]))
        updated = connection.execute(
            "UPDATE birth_producer_receipts "
            "SET state='consumed',consumed_at=?,request_id=? "
            "WHERE receipt_id=? AND receipt_hash=? AND state='available'",
            (_iso(instant), request, receipt.receipt_id, digest),
        )
        if updated.rowcount != 1:
            raise ReceiptError("producer_receipt_replay", "cas_conflict")
        connection.commit()
        return receipt
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
