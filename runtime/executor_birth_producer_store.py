"""Crash-safe ownership store for authenticated RM-0008 producer receipts."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_receipts import IssuerRegistry, ProducerReceipt, ReceiptError, verify_producer_receipt

_VERSION = 3
_SCHEMA = """
CREATE TABLE IF NOT EXISTS birth_producer_receipts (
 receipt_id TEXT PRIMARY KEY, receipt_hash TEXT NOT NULL UNIQUE, encoded BLOB NOT NULL,
 issuer_id TEXT NOT NULL, objective_hash TEXT NOT NULL, candidate_source_id TEXT NOT NULL,
 executor_origin TEXT NOT NULL, revision_authorship TEXT NOT NULL, expires_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('available','in_progress','committed','rejected')),
 registered_at TEXT NOT NULL, request_id TEXT, claimed_at TEXT, lease_expires_at TEXT,
 finalized_at TEXT, result_binding TEXT, rejection_code TEXT,
 terminal_envelope BLOB, terminal_auth BLOB,
 CHECK ((state='available' AND request_id IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL AND finalized_at IS NULL AND result_binding IS NULL AND rejection_code IS NULL)
 OR (state='in_progress' AND request_id IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND finalized_at IS NULL AND result_binding IS NULL AND rejection_code IS NULL)
 OR (state='committed' AND request_id IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NULL AND finalized_at IS NOT NULL AND result_binding IS NOT NULL AND rejection_code IS NULL)
 OR (state='rejected' AND request_id IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NULL AND finalized_at IS NOT NULL AND result_binding IS NULL AND rejection_code IS NOT NULL))
);
"""

@dataclass(frozen=True, slots=True)
class ProducerReceiptBinding:
    objective_hash: str
    candidate_source_id: str
    executor_origin: ExecutorOrigin
    revision_authorship: RevisionAuthor

@dataclass(frozen=True, slots=True)
class ProducerReceiptClaim:
    receipt: ProducerReceipt
    request_id: str
    state: str
    lease_expires_at: str | None
    result_binding: str | None
    rejection_code: str | None
    terminal_envelope: bytes | None = None
    terminal_auth: bytes | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"committed", "rejected"}

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

def _digest(value: object, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in value[7:])):
        raise ReceiptError("producer_receipt_invalid", field)
    return value

def _migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > _VERSION:
        raise ReceiptError("producer_receipt_store_invalid", "schema_too_new")
    db.execute("BEGIN IMMEDIATE")
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='birth_producer_receipts'").fetchone()
        if exists and version < 2:
            db.execute("ALTER TABLE birth_producer_receipts RENAME TO birth_producer_receipts_v1")
            db.execute(_SCHEMA)
            db.execute("""INSERT INTO birth_producer_receipts
             SELECT receipt_id,receipt_hash,encoded,issuer_id,objective_hash,candidate_source_id,
              executor_origin,revision_authorship,expires_at,
              CASE state WHEN 'available' THEN 'available' ELSE 'rejected' END,registered_at,
              CASE WHEN state='available' THEN NULL ELSE COALESCE(request_id,'sha256:0000000000000000000000000000000000000000000000000000000000000000') END,
              CASE WHEN state='available' THEN NULL ELSE consumed_at END,NULL,
              CASE WHEN state='available' THEN NULL ELSE consumed_at END,NULL,
              CASE WHEN state='available' THEN NULL ELSE 'legacy_terminal' END,NULL,NULL
             FROM birth_producer_receipts_v1""")
            db.execute("DROP TABLE birth_producer_receipts_v1")
        else:
            db.execute(_SCHEMA)
        if exists and version == 2:
            db.execute("ALTER TABLE birth_producer_receipts ADD COLUMN terminal_envelope BLOB")
            db.execute("ALTER TABLE birth_producer_receipts ADD COLUMN terminal_auth BLOB")
        db.execute(f"PRAGMA user_version={_VERSION}")
        db.commit()
    except Exception:
        db.rollback(); raise

def _open(path: Path) -> sqlite3.Connection:
    if not isinstance(path, Path):
        raise ReceiptError("producer_receipt_store_invalid", "db_path")
    db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL")
    _migrate(db)
    return db

def _verify_claimed(encoded: bytes, *, registry: IssuerRegistry, now: datetime,
                    db_path: Path) -> ProducerReceipt:
    """Authenticate a previously claimed receipt even after its issue expiry.

    Expiry prevents acquiring new authority, not recovery or deterministic
    replay of authority durably acquired while valid.
    """
    try:
        return verify_producer_receipt(encoded, registry=registry, now=now)
    except ReceiptError as exc:
        if exc.code != "producer_receipt_expired":
            raise
    db = _open(db_path)
    try:
        row = db.execute(
            "SELECT encoded,expires_at,state FROM birth_producer_receipts WHERE receipt_hash=?",
            (producer_receipt_hash(encoded),),
        ).fetchone()
        if row is None or bytes(row["encoded"]) != encoded or row["state"] == "available":
            raise ReceiptError("producer_receipt_expired")
        expiry = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return verify_producer_receipt(
            encoded, registry=registry, now=expiry - timedelta(seconds=1),
        )
    finally:
        db.close()

def _require_binding(receipt: ProducerReceipt, binding: ProducerReceiptBinding) -> None:
    if not isinstance(binding, ProducerReceiptBinding):
        raise ReceiptError("producer_receipt_binding_invalid", "binding")
    for field, value in {"objective_hash": binding.objective_hash,
                         "candidate_source_id": binding.candidate_source_id,
                         "executor_origin": binding.executor_origin,
                         "revision_authorship": binding.revision_authorship}.items():
        if getattr(receipt, field) != value:
            raise ReceiptError("producer_receipt_binding_invalid", field)

def _row(db: sqlite3.Connection, receipt: ProducerReceipt, encoded: bytes):
    row = db.execute("SELECT * FROM birth_producer_receipts WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    if row is None:
        raise ReceiptError("producer_receipt_invalid", "not_registered")
    actual = (row["receipt_hash"], bytes(row["encoded"]), row["issuer_id"], row["objective_hash"], row["candidate_source_id"], row["executor_origin"], row["revision_authorship"], row["expires_at"])
    expected = (producer_receipt_hash(encoded), encoded, receipt.issuer_id, receipt.objective_hash, receipt.candidate_source_id, receipt.executor_origin.value, receipt.revision_authorship.value, receipt.expires_at)
    if actual != expected:
        raise ReceiptError("producer_receipt_invalid", "store_binding")
    return row

def register_producer_receipt(encoded: bytes, *, registry: IssuerRegistry, now: datetime, db_path: Path) -> ProducerReceipt:
    instant = _utc(now); receipt = verify_producer_receipt(encoded, registry=registry, now=instant)
    db = _open(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO birth_producer_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (receipt.receipt_id, producer_receipt_hash(encoded), encoded, receipt.issuer_id,
                        receipt.objective_hash, receipt.candidate_source_id, receipt.executor_origin.value,
                        receipt.revision_authorship.value, receipt.expires_at, "available", _iso(instant),
                        None, None, None, None, None, None, None, None))
        except sqlite3.IntegrityError as exc:
            raise ReceiptError("producer_receipt_replay", "already_registered") from exc
        db.commit(); return receipt
    finally:
        if db.in_transaction: db.rollback()
        db.close()

def claim_producer_receipt(encoded: bytes, *, registry: IssuerRegistry, binding: ProducerReceiptBinding,
                           request_id: str, now: datetime, db_path: Path,
                           lease_seconds: int = 300) -> ProducerReceiptClaim:
    """Claim once; retries by the same request replay live or terminal state."""
    instant = _utc(now); request = _digest(request_id, "request_id")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
        raise ReceiptError("producer_receipt_invalid", "lease_seconds")
    receipt = _verify_claimed(encoded, registry=registry, now=instant, db_path=db_path); _require_binding(receipt, binding)
    db = _open(db_path)
    try:
        db.execute("BEGIN IMMEDIATE"); row = _row(db, receipt, encoded)
        if row["state"] == "available":
            lease = _iso(instant + timedelta(seconds=lease_seconds))
            db.execute("UPDATE birth_producer_receipts SET state='in_progress',request_id=?,claimed_at=?,lease_expires_at=? WHERE receipt_id=? AND state='available'",
                       (request, _iso(instant), lease, receipt.receipt_id))
            db.commit(); return ProducerReceiptClaim(receipt, request, "in_progress", lease, None, None)
        if row["request_id"] != request:
            raise ReceiptError("producer_receipt_replay", "owned_by_other_request")
        if row["state"] == "in_progress" and row["lease_expires_at"] <= _iso(instant):
            raise ReceiptError("producer_receipt_lease_expired", "explicit_recovery_required")
        db.commit()
        return ProducerReceiptClaim(receipt, request, row["state"], row["lease_expires_at"], row["result_binding"], row["rejection_code"],
                                    bytes(row["terminal_envelope"]) if row["terminal_envelope"] is not None else None,
                                    bytes(row["terminal_auth"]) if row["terminal_auth"] is not None else None)
    finally:
        if db.in_transaction: db.rollback()
        db.close()

def recover_producer_receipt_claim(encoded: bytes, *, registry: IssuerRegistry,
                                   binding: ProducerReceiptBinding, request_id: str,
                                   now: datetime, db_path: Path,
                                   lease_seconds: int = 300) -> ProducerReceiptClaim:
    """Explicitly renew an expired lease without transferring ownership."""
    instant = _utc(now); request = _digest(request_id, "request_id")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
        raise ReceiptError("producer_receipt_invalid", "lease_seconds")
    receipt = _verify_claimed(encoded, registry=registry, now=instant, db_path=db_path); _require_binding(receipt, binding)
    db = _open(db_path)
    try:
        db.execute("BEGIN IMMEDIATE"); row = _row(db, receipt, encoded)
        if row["request_id"] != request:
            raise ReceiptError("producer_receipt_replay", "owned_by_other_request")
        if row["state"] != "in_progress":
            raise ReceiptError("producer_receipt_recovery_invalid", str(row["state"]))
        if row["lease_expires_at"] > _iso(instant):
            raise ReceiptError("producer_receipt_recovery_invalid", "lease_active")
        lease = _iso(instant + timedelta(seconds=lease_seconds))
        db.execute("UPDATE birth_producer_receipts SET lease_expires_at=? WHERE receipt_id=? AND state='in_progress' AND request_id=?",
                   (lease, receipt.receipt_id, request))
        db.commit(); return ProducerReceiptClaim(receipt, request, "in_progress", lease, None, None)
    finally:
        if db.in_transaction: db.rollback()
        db.close()

def record_producer_receipt_terminal_hint(
    encoded: bytes, *, registry: IssuerRegistry, binding: ProducerReceiptBinding,
    request_id: str, now: datetime, db_path: Path,
    terminal_envelope: bytes, terminal_auth: bytes,
) -> ProducerReceiptClaim:
    """Durably retain signed recovery context while authority remains in progress."""
    instant = _utc(now); request = _digest(request_id, "request_id")
    if not isinstance(terminal_envelope, bytes) or not terminal_envelope or not isinstance(terminal_auth, bytes) or not terminal_auth:
        raise ReceiptError("producer_receipt_invalid", "terminal_envelope")
    receipt = _verify_claimed(encoded, registry=registry, now=instant, db_path=db_path); _require_binding(receipt, binding)
    db = _open(db_path)
    try:
        db.execute("BEGIN IMMEDIATE"); row = _row(db, receipt, encoded)
        if row["request_id"] != request:
            raise ReceiptError("producer_receipt_replay", "owned_by_other_request")
        if row["state"] != "in_progress":
            raise ReceiptError("producer_receipt_final_invalid", str(row["state"]))
        if row["lease_expires_at"] <= _iso(instant):
            raise ReceiptError("producer_receipt_lease_expired", "explicit_recovery_required")
        existing = (bytes(row["terminal_envelope"]) if row["terminal_envelope"] is not None else None,
                    bytes(row["terminal_auth"]) if row["terminal_auth"] is not None else None)
        if existing != (None, None) and existing != (terminal_envelope, terminal_auth):
            raise ReceiptError("producer_receipt_final_conflict", "terminal_hint")
        db.execute("UPDATE birth_producer_receipts SET terminal_envelope=?,terminal_auth=? WHERE receipt_id=? AND state='in_progress' AND request_id=?",
                   (terminal_envelope, terminal_auth, receipt.receipt_id, request))
        db.commit()
        return ProducerReceiptClaim(receipt, request, "in_progress", row["lease_expires_at"], None, None,
                                    terminal_envelope, terminal_auth)
    finally:
        if db.in_transaction: db.rollback()
        db.close()

def finalize_producer_receipt(encoded: bytes, *, registry: IssuerRegistry,
                              binding: ProducerReceiptBinding, request_id: str,
                              now: datetime, db_path: Path, result_binding: str | None = None,
                              rejection_code: str | None = None,
                              terminal_envelope: bytes | None = None,
                              terminal_auth: bytes | None = None) -> ProducerReceiptClaim:
    """Finalize a claim; an identical retry is idempotent, a conflict fails."""
    instant = _utc(now); request = _digest(request_id, "request_id")
    if (result_binding is None) == (rejection_code is None):
        raise ReceiptError("producer_receipt_invalid", "final_result")
    if result_binding is not None: _digest(result_binding, "result_binding")
    if rejection_code is not None and (not isinstance(rejection_code, str) or not rejection_code or "\x00" in rejection_code):
        raise ReceiptError("producer_receipt_invalid", "rejection_code")
    if (terminal_envelope is None) != (terminal_auth is None):
        raise ReceiptError("producer_receipt_invalid", "terminal_envelope")
    if terminal_envelope is not None and (not isinstance(terminal_envelope, bytes) or not terminal_envelope
                                           or not isinstance(terminal_auth, bytes) or not terminal_auth):
        raise ReceiptError("producer_receipt_invalid", "terminal_envelope")
    receipt = _verify_claimed(encoded, registry=registry, now=instant, db_path=db_path); _require_binding(receipt, binding)
    state = "committed" if result_binding is not None else "rejected"
    db = _open(db_path)
    try:
        db.execute("BEGIN IMMEDIATE"); row = _row(db, receipt, encoded)
        if row["request_id"] != request:
            raise ReceiptError("producer_receipt_replay", "owned_by_other_request")
        if row["state"] in {"committed", "rejected"}:
            stored_envelope = bytes(row["terminal_envelope"]) if row["terminal_envelope"] is not None else None
            stored_auth = bytes(row["terminal_auth"]) if row["terminal_auth"] is not None else None
            if (row["state"], row["result_binding"], row["rejection_code"], stored_envelope, stored_auth) != (state, result_binding, rejection_code, terminal_envelope, terminal_auth):
                raise ReceiptError("producer_receipt_final_conflict", str(row["state"]))
            db.commit(); return ProducerReceiptClaim(receipt, request, state, None, result_binding, rejection_code, terminal_envelope, terminal_auth)
        if row["state"] != "in_progress":
            raise ReceiptError("producer_receipt_final_invalid", str(row["state"]))
        if row["lease_expires_at"] <= _iso(instant):
            raise ReceiptError("producer_receipt_lease_expired", "explicit_recovery_required")
        db.execute("UPDATE birth_producer_receipts SET state=?,lease_expires_at=NULL,finalized_at=?,result_binding=?,rejection_code=?,terminal_envelope=?,terminal_auth=? WHERE receipt_id=? AND state='in_progress' AND request_id=?",
                   (state, _iso(instant), result_binding, rejection_code, terminal_envelope, terminal_auth, receipt.receipt_id, request))
        db.commit(); return ProducerReceiptClaim(receipt, request, state, None, result_binding, rejection_code, terminal_envelope, terminal_auth)
    finally:
        if db.in_transaction: db.rollback()
        db.close()

def consume_producer_receipt(encoded: bytes, *, registry: IssuerRegistry, binding: ProducerReceiptBinding,
                             request_id: str, now: datetime, db_path: Path) -> ProducerReceipt:
    """Legacy one-shot adapter. New publication code must claim then finalize."""
    claim = claim_producer_receipt(encoded, registry=registry, binding=binding, request_id=request_id, now=now, db_path=db_path)
    if claim.state != "in_progress":
        raise ReceiptError("producer_receipt_replay", claim.state)
    finalize_producer_receipt(encoded, registry=registry, binding=binding, request_id=request_id,
                              now=now, db_path=db_path, rejection_code="legacy_consume_without_result_binding")
    return claim.receipt
