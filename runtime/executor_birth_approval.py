"""Pure, strictly bound human-approval envelope for RM-0008 Birth."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from executor_birth_identity import encode_framed_v1


APPROVAL_DOMAIN_V1 = b"metnos.executor-birth.approval/v1\0"
APPROVAL_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_LIFECYCLES = frozenset({
    "proposed", "synthesized", "preexercise", "active", "quarantined",
    # Productive approval scopes for changes to an existing executor.  These
    # are deliberately distinct from lifecycle values: an approval for an
    # authority change must not authorize promotion or reactivation.
    "authority", "promotion", "reactivation",
})


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class BirthApprovalError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise BirthApprovalError("approval_invalid", field)
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise BirthApprovalError("approval_invalid", field)
    return value


def _instant(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        raise BirthApprovalError("approval_invalid", field)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise BirthApprovalError("approval_invalid", field) from exc
    return parsed


@dataclass(frozen=True, slots=True)
class ApprovalSubject:
    candidate_id: str
    semantic_core_id: str
    admission_context_id: str
    lifecycle: str
    expires_at: str

    def __post_init__(self) -> None:
        _digest(self.candidate_id, "candidate_id")
        _digest(self.semantic_core_id, "semantic_core_id")
        _digest(self.admission_context_id, "admission_context_id")
        if self.lifecycle not in _LIFECYCLES:
            raise BirthApprovalError("approval_invalid", "lifecycle")
        _instant(self.expires_at, "expires_at")


def approval_subject_hash(subject: ApprovalSubject) -> str:
    if not isinstance(subject, ApprovalSubject):
        raise BirthApprovalError("approval_invalid", "subject")
    payload = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "candidate_id": subject.candidate_id,
        "semantic_core_id": subject.semantic_core_id,
        "admission_context_id": subject.admission_context_id,
        "lifecycle": subject.lifecycle,
        "expires_at": subject.expires_at,
    }
    return "sha256:" + hashlib.sha256(
        APPROVAL_DOMAIN_V1 + encode_framed_v1(payload)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    approval_id: str
    subject_hash: str
    actor: str
    decision: ApprovalDecision
    decided_at: str
    registry_token: str | None = None
    key_id: str | None = None
    signature: str | None = None

    def __post_init__(self) -> None:
        _text(self.approval_id, "approval_id")
        _digest(self.subject_hash, "subject_hash")
        _text(self.actor, "actor")
        if not isinstance(self.decision, ApprovalDecision):
            raise BirthApprovalError("approval_invalid", "decision")
        _instant(self.decided_at, "decided_at")
        if self.registry_token is not None:
            _text(self.registry_token, "registry_token")
        if (self.key_id is None) != (self.signature is None):
            raise BirthApprovalError("approval_invalid", "signature_envelope")
        if self.key_id is not None:
            _text(self.key_id, "key_id")
            if not isinstance(self.signature, str) or not re.fullmatch(r"[0-9a-f]{128}", self.signature):
                raise BirthApprovalError("approval_invalid", "signature")


def validate_approval(
    subject: ApprovalSubject,
    evidence: ApprovalEvidence,
    *,
    now: datetime,
) -> None:
    """Validate an exact approval without converting rejection into alignment."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise BirthApprovalError("approval_invalid", "now")
    if not isinstance(evidence, ApprovalEvidence):
        raise BirthApprovalError("approval_invalid", "evidence")
    if evidence.subject_hash != approval_subject_hash(subject):
        raise BirthApprovalError("approval_invalid", "subject_mismatch")
    expires = _instant(subject.expires_at, "expires_at")
    decided = _instant(evidence.decided_at, "decided_at")
    current = now.astimezone(timezone.utc)
    if decided >= expires or current >= expires:
        raise BirthApprovalError("approval_expired")
    if decided > current:
        raise BirthApprovalError("approval_invalid", "decision_in_future")
    if evidence.decision is not ApprovalDecision.APPROVED:
        raise BirthApprovalError("approval_required", "rejected")


def approval_evidence_hash(evidence: ApprovalEvidence) -> str:
    """Bind the override record without treating it as a semantic verdict."""
    if not isinstance(evidence, ApprovalEvidence):
        raise BirthApprovalError("approval_invalid", "evidence")
    payload = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "kind": "approval_evidence",
        "approval_id": evidence.approval_id,
        "subject_hash": evidence.subject_hash,
        "actor": evidence.actor,
        "decision": evidence.decision.value,
        "decided_at": evidence.decided_at,
        "registry_token": evidence.registry_token,
    }
    # Preserve the V1 digest of non-registry/unit-test evidence. Productive
    # store evidence always carries this signed extension.
    if evidence.key_id is not None:
        payload["key_id"] = evidence.key_id
        payload["signature"] = evidence.signature
    return "sha256:" + hashlib.sha256(
        APPROVAL_DOMAIN_V1 + encode_framed_v1(payload)
    ).hexdigest()
