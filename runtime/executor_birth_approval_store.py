"""SQLite shadow store for approvals natively bound to an RM-0008 subject."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from executor_birth_approval import (
    ApprovalDecision,
    ApprovalEvidence,
    ApprovalSubject,
    BirthApprovalError,
    approval_subject_hash,
    validate_approval,
)
from executor_birth_approval_authority import ApprovalAuthority, verify_decision


_SCHEMA = """
CREATE TABLE IF NOT EXISTS birth_approvals (
    token TEXT PRIMARY KEY,
    subject_hash TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL,
    semantic_core_id TEXT NOT NULL,
    admission_context_id TEXT NOT NULL,
    approval_scope TEXT NOT NULL,
    requested_actor TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','expired')),
    created_at TEXT NOT NULL,
    decision_actor TEXT,
    decision_at TEXT,
    decision_key_id TEXT,
    decision_signature TEXT,
    CHECK (
      (status = 'pending' AND decision_actor IS NULL AND decision_at IS NULL) OR
      (status <> 'pending' AND decision_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_birth_approvals_status
ON birth_approvals(status, expires_at);
CREATE TABLE IF NOT EXISTS birth_approval_consumptions (
    token TEXT PRIMARY KEY REFERENCES birth_approvals(token),
    request_id TEXT NOT NULL UNIQUE,
    consumed_at TEXT NOT NULL
);
"""


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BirthApprovalError("approval_invalid", field)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    value = _utc(value, field="time")
    if value.microsecond:
        raise BirthApprovalError("approval_invalid", "time_precision")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _actor(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise BirthApprovalError("approval_invalid", "actor")
    return value


def _open(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    # Pre-resolution shadow databases did not retain the subject components.
    # Add the columns without blessing legacy rows: their NULL values make
    # productive resolution fail closed, while newly created records are exact.
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(birth_approvals)")}
    for name in ("candidate_id", "semantic_core_id", "admission_context_id", "approval_scope",
                 "decision_key_id", "decision_signature"):
        if name not in columns:
            connection.execute(f"ALTER TABLE birth_approvals ADD COLUMN {name} TEXT")
    return connection


def create_pending_approval(
    subject: ApprovalSubject,
    *,
    requested_actor: str,
    created_at: datetime,
    db_path: Path,
) -> str:
    """Create one unique pending subject; duplicates fail instead of aliasing."""
    actor = _actor(requested_actor)
    now = _utc(created_at, field="created_at")
    expires = datetime.strptime(subject.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    if now >= expires:
        raise BirthApprovalError("approval_expired")
    opaque = secrets.token_urlsafe(24)
    if len(opaque) < 16 or opaque != opaque.strip():  # pragma: no cover - secrets contract
        raise BirthApprovalError("approval_invalid", "token")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO birth_approvals "
                "(token,subject_hash,candidate_id,semantic_core_id,admission_context_id,"
                "approval_scope,requested_actor,expires_at,status,created_at,decision_actor,decision_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (opaque, approval_subject_hash(subject), subject.candidate_id,
                 subject.semantic_core_id, subject.admission_context_id,
                 subject.lifecycle, actor, subject.expires_at,
                 "pending", _iso(now), None, None),
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise BirthApprovalError("approval_invalid", "duplicate_subject_or_token") from exc
        connection.commit()
        return opaque
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def resolve_pending_approval(
    token: str,
    decision: ApprovalDecision,
    *,
    actor: str,
    key_id: str,
    signature: str,
    authority: ApprovalAuthority,
    decided_at: datetime,
    db_path: Path,
) -> ApprovalEvidence:
    """CAS pending -> decision, enforcing actor and expiry in one transaction."""
    if not isinstance(decision, ApprovalDecision):
        raise BirthApprovalError("approval_invalid", "decision")
    deciding_actor = _actor(actor)
    now = _utc(decided_at, field="decided_at")
    now_text = _iso(now)
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM birth_approvals WHERE token=?", (token,),
        ).fetchone()
        if row is None:
            raise BirthApprovalError("approval_invalid", "unknown_token")
        if row["status"] != "pending":
            raise BirthApprovalError("approval_invalid", "already_resolved")
        if row["requested_actor"] != deciding_actor:
            raise BirthApprovalError("approval_invalid", "actor_mismatch")
        verify_decision(
            authority, token=token, subject_hash=row["subject_hash"],
            scope=row["approval_scope"], actor=deciding_actor, decision=decision,
            decided_at=now_text, key_id=key_id, signature=signature,
        )
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if now >= expires:
            updated = connection.execute(
                "UPDATE birth_approvals SET status='expired', decision_at=? "
                "WHERE token=? AND status='pending'", (now_text, token),
            )
            if updated.rowcount != 1:
                raise BirthApprovalError("approval_invalid", "cas_conflict")
            connection.commit()
            raise BirthApprovalError("approval_expired")
        updated = connection.execute(
            "UPDATE birth_approvals SET status=?,decision_actor=?,decision_at=?,"
            "decision_key_id=?,decision_signature=? "
            "WHERE token=? AND status='pending'",
            (decision.value, deciding_actor, now_text, key_id, signature, token),
        )
        if updated.rowcount != 1:
            raise BirthApprovalError("approval_invalid", "cas_conflict")
        connection.commit()
        return ApprovalEvidence(
            approval_id=f"birth_approval.{token}", subject_hash=row["subject_hash"],
            actor=deciding_actor, decision=decision, decided_at=now_text,
            registry_token=token,
            key_id=key_id, signature=signature,
        )
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def verified_approval(
    subject: ApprovalSubject,
    *,
    token: str,
    now: datetime,
    db_path: Path,
    authority: ApprovalAuthority,
) -> ApprovalEvidence:
    """Read an exact native record; legacy approval rows are never consulted."""
    connection = _open(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM birth_approvals WHERE token=?", (token,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row["subject_hash"] != approval_subject_hash(subject):
        raise BirthApprovalError("approval_invalid", "subject_mismatch")
    try:
        decision = ApprovalDecision(row["status"])
    except ValueError as exc:
        raise BirthApprovalError("approval_required", str(row["status"])) from exc
    evidence = ApprovalEvidence(
        approval_id=f"birth_approval.{token}", subject_hash=row["subject_hash"],
        actor=row["decision_actor"], decision=decision, decided_at=row["decision_at"],
        registry_token=token,
        key_id=row["decision_key_id"], signature=row["decision_signature"],
    )
    verify_decision(
        authority, token=token, subject_hash=row["subject_hash"],
        scope=row["approval_scope"], actor=evidence.actor, decision=evidence.decision,
        decided_at=evidence.decided_at, key_id=evidence.key_id, signature=evidence.signature,
    )
    validate_approval(subject, evidence, now=now)
    return evidence


def consume_verified_approval(
    subject: ApprovalSubject,
    *,
    token: str,
    request_id: str,
    now: datetime,
    db_path: Path,
    authority: ApprovalAuthority,
) -> ApprovalEvidence:
    """Authenticate and consume one approval for exactly one Birth request.

    Re-entry by the same request is idempotent, which permits crash recovery.
    Any attempt to bind the token to a different request is a replay.
    """
    if (not isinstance(request_id, str) or len(request_id) != 71
            or not request_id.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in request_id[7:])):
        raise BirthApprovalError("approval_invalid", "request_id")
    current = _utc(now, field="now")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM birth_approvals WHERE token=?", (token,),
        ).fetchone()
        if row is None or row["subject_hash"] != approval_subject_hash(subject):
            raise BirthApprovalError("approval_invalid", "subject_mismatch")
        try:
            decision = ApprovalDecision(row["status"])
            evidence = ApprovalEvidence(
                approval_id=f"birth_approval.{token}",
                subject_hash=row["subject_hash"], actor=row["decision_actor"],
                decision=decision, decided_at=row["decision_at"], registry_token=token,
                key_id=row["decision_key_id"], signature=row["decision_signature"],
            )
        except (ValueError, TypeError, BirthApprovalError) as exc:
            raise BirthApprovalError("approval_required", str(row["status"])) from exc
        verify_decision(
            authority, token=token, subject_hash=row["subject_hash"],
            scope=row["approval_scope"], actor=evidence.actor, decision=evidence.decision,
            decided_at=evidence.decided_at, key_id=evidence.key_id, signature=evidence.signature,
        )
        validate_approval(subject, evidence, now=current)
        consumed = connection.execute(
            "SELECT request_id FROM birth_approval_consumptions WHERE token=?", (token,),
        ).fetchone()
        if consumed is not None:
            if consumed["request_id"] != request_id:
                raise BirthApprovalError("approval_invalid", "replayed_token")
        else:
            try:
                connection.execute(
                    "INSERT INTO birth_approval_consumptions VALUES (?,?,?)",
                    (token, request_id, _iso(current)),
                )
            except sqlite3.IntegrityError as exc:
                raise BirthApprovalError("approval_invalid", "request_already_approved") from exc
        connection.commit()
        return evidence
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def resolve_request_approval(
    *,
    approval_refs: tuple[str, ...],
    request_id: str,
    candidate_id: str,
    semantic_core_id: str,
    admission_context_id: str,
    scope: str | None,
    now: datetime,
    db_path: Path,
    authority: ApprovalAuthority,
) -> tuple[ApprovalSubject | None, ApprovalEvidence | None]:
    """Core-owned resolver; request references are lookup keys, never evidence."""
    if scope is None:
        # Approval is inapplicable. Supplying a reference is rejected so that
        # callers cannot smuggle unbound evidence into an unrelated revision.
        if approval_refs:
            raise BirthApprovalError("approval_invalid", "unexpected_reference")
        return None, None
    if len(approval_refs) != 1:
        raise BirthApprovalError("approval_required", "missing_or_ambiguous_reference")
    connection = _open(db_path)
    try:
        row = connection.execute(
            "SELECT candidate_id,semantic_core_id,admission_context_id,approval_scope,expires_at "
            "FROM birth_approvals WHERE token=?", (approval_refs[0],),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise BirthApprovalError("approval_invalid", "unknown_token")
    subject = ApprovalSubject(
        candidate_id, semantic_core_id, admission_context_id, scope, row["expires_at"],
    )
    if (row["candidate_id"] != candidate_id
            or row["semantic_core_id"] != semantic_core_id
            or row["admission_context_id"] != admission_context_id
            or row["approval_scope"] != scope):
        raise BirthApprovalError("approval_invalid", "subject_mismatch")
    evidence = consume_verified_approval(
        subject, token=approval_refs[0], request_id=request_id, now=now, db_path=db_path,
        authority=authority,
    )
    return subject, evidence
