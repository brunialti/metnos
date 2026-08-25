from dataclasses import replace
from datetime import datetime, timezone

import pytest

from executor_birth_approval import (
    ApprovalDecision,
    ApprovalEvidence,
    ApprovalSubject,
    BirthApprovalError,
    approval_evidence_hash,
    approval_subject_hash,
    validate_approval,
)


def subject(**changes):
    base = ApprovalSubject(
        "sha256:" + "1" * 64, "sha256:" + "2" * 64, "sha256:" + "3" * 64,
        "synthesized", "2030-01-01T00:00:00Z",
    )
    return replace(base, **changes)


def evidence(value=None, **changes):
    value = value or subject()
    base = ApprovalEvidence(
        "approval.1", approval_subject_hash(value), "operator",
        ApprovalDecision.APPROVED, "2029-12-31T23:00:00Z",
    )
    return replace(base, **changes)


def test_subject_hash_golden_and_every_binding_changes_it():
    base = approval_subject_hash(subject())
    assert base == "sha256:ecebd885bb78ecca1a3df2a5fa62714c8ca0fa146e8a5b0aa99b9c40385e4144"
    for field, value in (
        ("candidate_id", "sha256:" + "4" * 64),
        ("semantic_core_id", "sha256:" + "4" * 64),
        ("admission_context_id", "sha256:" + "4" * 64),
        ("lifecycle", "preexercise"),
        ("expires_at", "2030-01-02T00:00:00Z"),
    ):
        assert approval_subject_hash(replace(subject(), **{field: value})) != base


def test_validation_is_exact_time_bound_and_approval_only():
    now = datetime(2029, 12, 31, 23, 30, tzinfo=timezone.utc)
    validate_approval(subject(), evidence(), now=now)
    with pytest.raises(BirthApprovalError, match="subject_mismatch"):
        validate_approval(subject(candidate_id="sha256:" + "4" * 64), evidence(), now=now)
    with pytest.raises(BirthApprovalError, match="approval_required"):
        validate_approval(subject(), evidence(decision=ApprovalDecision.REJECTED), now=now)
    with pytest.raises(BirthApprovalError, match="approval_expired"):
        validate_approval(subject(), evidence(), now=datetime(2030, 1, 1, 0, 0, 1, tzinfo=timezone.utc))


def test_strict_types_formats_and_timezone():
    with pytest.raises(BirthApprovalError):
        subject(expires_at="2030-01-01T00:00:00+00:00")
    with pytest.raises(BirthApprovalError):
        subject(candidate_id="SHA256:" + "1" * 64)
    with pytest.raises(BirthApprovalError, match="now"):
        validate_approval(subject(), evidence(), now=datetime(2029, 1, 1))


def test_evidence_hash_binds_actor_decision_time_and_subject():
    base = approval_evidence_hash(evidence())
    assert base == "sha256:6033f6d8d5437ece38f8da6d78c9f2e7dea20b98baf544d17f085af362dac740"
    assert approval_evidence_hash(evidence(actor="other")) != base
    assert approval_evidence_hash(evidence(decision=ApprovalDecision.REJECTED)) != base
    assert approval_evidence_hash(evidence(decided_at="2029-12-31T22:00:00Z")) != base
    assert approval_evidence_hash(evidence(subject_hash="sha256:" + "9" * 64)) != base
