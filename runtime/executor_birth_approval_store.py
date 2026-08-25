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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS birth_approvals (
    token TEXT PRIMARY KEY,
    subject_hash TEXT NOT NULL UNIQUE,
    requested_actor TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','expired')),
    created_at TEXT NOT NULL,
    decision_actor TEXT,
    decision_at TEXT,
    CHECK (
      (status = 'pending' AND decision_actor IS NULL AND decision_at IS NULL) OR
      (status <> 'pending' AND decision_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_birth_approvals_status
ON birth_approvals(status, expires_at);
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
                "INSERT INTO birth_approvals VALUES (?,?,?,?,?,?,?,?)",
                (opaque, approval_subject_hash(subject), actor, subject.expires_at,
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
            "UPDATE birth_approvals SET status=?,decision_actor=?,decision_at=? "
            "WHERE token=? AND status='pending'",
            (decision.value, deciding_actor, now_text, token),
        )
        if updated.rowcount != 1:
            raise BirthApprovalError("approval_invalid", "cas_conflict")
        connection.commit()
        return ApprovalEvidence(
            approval_id=f"birth_approval.{token}", subject_hash=row["subject_hash"],
            actor=deciding_actor, decision=decision, decided_at=now_text,
            registry_token=token,
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
    )
    validate_approval(subject, evidence, now=now)
    return evidence
