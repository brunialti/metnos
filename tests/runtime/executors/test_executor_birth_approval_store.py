from datetime import datetime, timezone
from threading import Barrier, Thread

import pytest

from executor_birth_approval import ApprovalDecision, ApprovalSubject, BirthApprovalError
from executor_birth_approval_store import create_pending_approval, resolve_pending_approval, verified_approval


def subject(digit="1"):
    return ApprovalSubject(*(("sha256:" + digit * 64,) * 3), "synthesized", "2030-01-01T00:00:00Z")


CREATED = datetime(2029, 12, 31, 22, tzinfo=timezone.utc)
DECIDED = datetime(2029, 12, 31, 23, tzinfo=timezone.utc)


def test_native_store_binds_subject_and_resolves_once(tmp_path):
    db = tmp_path / "approval.sqlite"
    token = create_pending_approval(subject(), requested_actor="operator", created_at=CREATED, db_path=db)
    evidence = resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    assert evidence.subject_hash
    assert verified_approval(subject(), token=token, now=DECIDED, db_path=db) == evidence
    with pytest.raises(BirthApprovalError, match="already_resolved"):
        resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)


def test_unique_subject_and_exact_subject_lookup(tmp_path):
    db = tmp_path / "approval.sqlite"
    token = create_pending_approval(subject(), requested_actor="operator", created_at=CREATED, db_path=db)
    with pytest.raises(BirthApprovalError, match="duplicate_subject"):
        create_pending_approval(subject(), requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    with pytest.raises(BirthApprovalError, match="subject_mismatch"):
        verified_approval(subject("2"), token=token, now=DECIDED, db_path=db)


def test_actor_and_expiry_fail_closed(tmp_path):
    db = tmp_path / "approval.sqlite"
    token = create_pending_approval(subject(), requested_actor="operator", created_at=CREATED, db_path=db)
    with pytest.raises(BirthApprovalError, match="actor_mismatch"):
        resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="other", decided_at=DECIDED, db_path=db)
    with pytest.raises(BirthApprovalError, match="approval_expired"):
        resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=datetime(2030, 1, 1, tzinfo=timezone.utc), db_path=db)


def test_rejected_record_never_verifies_as_approval(tmp_path):
    db = tmp_path / "approval.sqlite"
    token = create_pending_approval(subject(), requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.REJECTED, actor="operator", decided_at=DECIDED, db_path=db)
    with pytest.raises(BirthApprovalError, match="approval_required"):
        verified_approval(subject(), token=token, now=DECIDED, db_path=db)


def test_concurrent_resolution_is_single_winner_cas(tmp_path):
    db = tmp_path / "approval.sqlite"
    token = create_pending_approval(
        subject(), requested_actor="operator", created_at=CREATED, db_path=db,
    )
    barrier = Barrier(2)
    outcomes = []

    def decide(decision):
        barrier.wait()
        try:
            outcomes.append(resolve_pending_approval(
                token, decision, actor="operator", decided_at=DECIDED, db_path=db,
            ).decision)
        except BirthApprovalError as exc:
            outcomes.append(exc.detail)

    threads = [
        Thread(target=decide, args=(ApprovalDecision.APPROVED,)),
        Thread(target=decide, args=(ApprovalDecision.REJECTED,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len([item for item in outcomes if isinstance(item, ApprovalDecision)]) == 1
    assert outcomes.count("already_resolved") == 1
