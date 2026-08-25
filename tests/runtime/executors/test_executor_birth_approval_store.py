from datetime import datetime, timezone
from threading import Barrier, Thread
import sqlite3

import pytest

from executor_birth_approval import ApprovalDecision, ApprovalSubject, BirthApprovalError
from executor_birth_approval_authority import ApprovalAuthority, decision_payload
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from executor_birth_approval_store import (
    create_pending_approval, resolve_pending_approval as _resolve_pending,
    resolve_request_approval, verified_approval as _verified,
)

_PRIVATE = Ed25519PrivateKey.generate()
AUTHORITY = ApprovalAuthority(1, {"approver-v1": _PRIVATE.public_key()}, {
    "operator": {"key_ids": frozenset({"approver-v1"}),
                 "scopes": frozenset({"synthesized", "preexercise", "authority"})}
})


def resolve_pending_approval(token, decision, *, actor, decided_at, db_path,
                             authority=AUTHORITY, private=_PRIVATE, key_id="approver-v1"):
    with sqlite3.connect(db_path) as connection:
        subject_hash = connection.execute(
            "SELECT subject_hash FROM birth_approvals WHERE token=?", (token,)
        ).fetchone()[0]
    instant = decided_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature = private.sign(decision_payload(
        token=token, subject_hash=subject_hash, actor=actor, decision=decision,
        decided_at=instant, key_id=key_id,
    )).hex()
    return _resolve_pending(token, decision, actor=actor, key_id=key_id,
                            signature=signature, authority=authority,
                            decided_at=decided_at, db_path=db_path)


def verified_approval(subject, *, token, now, db_path):
    return _verified(subject, token=token, now=now, db_path=db_path, authority=AUTHORITY)


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


def _resolve(db, token, *, request_id="sha256:" + "9" * 64,
             candidate="sha256:" + "1" * 64, now=DECIDED):
    return resolve_request_approval(
        approval_refs=(token,), request_id=request_id,
        candidate_id=candidate, semantic_core_id="sha256:" + "1" * 64,
        admission_context_id="sha256:" + "1" * 64, scope="preexercise",
        now=now, db_path=db,
        authority=AUTHORITY,
    )


def test_request_refs_are_lookup_keys_not_evidence(tmp_path):
    db = tmp_path / "approval.sqlite"
    with pytest.raises(BirthApprovalError, match="missing_or_ambiguous"):
        resolve_request_approval(
            approval_refs=(), request_id="sha256:" + "9" * 64,
            candidate_id="sha256:" + "1" * 64,
            semantic_core_id="sha256:" + "1" * 64,
            admission_context_id="sha256:" + "1" * 64,
            scope="preexercise", now=DECIDED, db_path=db,
            authority=AUTHORITY,
        )
    with pytest.raises(BirthApprovalError, match="unknown_token"):
        _resolve(db, "forged-evidence")


def test_request_resolution_binds_observed_subject_scope_and_expiry(tmp_path):
    db = tmp_path / "approval.sqlite"
    scoped = ApprovalSubject(*(("sha256:" + "1" * 64,) * 3),
                             "preexercise", "2030-01-01T00:00:00Z")
    token = create_pending_approval(scoped, requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    resolved_subject, evidence = _resolve(db, token)
    assert resolved_subject == scoped
    assert evidence.registry_token == token
    with pytest.raises(BirthApprovalError, match="subject_mismatch"):
        _resolve(db, token, candidate="sha256:" + "2" * 64)
    with pytest.raises(BirthApprovalError, match="approval_expired"):
        _resolve(db, token, now=datetime(2030, 1, 1, tzinfo=timezone.utc))


def test_request_resolution_is_idempotent_for_retry_and_rejects_replay(tmp_path):
    db = tmp_path / "approval.sqlite"
    scoped = ApprovalSubject(*(("sha256:" + "1" * 64,) * 3),
                             "preexercise", "2030-01-01T00:00:00Z")
    token = create_pending_approval(scoped, requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    assert _resolve(db, token) == _resolve(db, token)
    with pytest.raises(BirthApprovalError, match="replayed_token"):
        _resolve(db, token, request_id="sha256:" + "8" * 64)


def test_concurrent_request_resolution_converges_for_same_request(tmp_path):
    db = tmp_path / "approval.sqlite"
    scoped = ApprovalSubject(*(("sha256:" + "1" * 64,) * 3),
                             "preexercise", "2030-01-01T00:00:00Z")
    token = create_pending_approval(scoped, requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    barrier = Barrier(2)
    outcomes = []
    def resolve():
        barrier.wait()
        outcomes.append(_resolve(db, token)[1].approval_id)
    threads = [Thread(target=resolve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert outcomes == [f"birth_approval.{token}"] * 2


def test_forged_actor_token_or_signature_cannot_resolve(tmp_path):
    db = tmp_path / "approval.sqlite"
    token = create_pending_approval(subject(), requested_actor="operator", created_at=CREATED, db_path=db)
    forged = Ed25519PrivateKey.generate()
    with pytest.raises(BirthApprovalError, match="signature"):
        resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator",
                                 decided_at=DECIDED, db_path=db, private=forged)
    with pytest.raises(BirthApprovalError, match="actor_mismatch"):
        resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="intruder",
                                 decided_at=DECIDED, db_path=db)
    with pytest.raises(BirthApprovalError, match="unknown_token"):
        _resolve_pending("not-a-token", ApprovalDecision.APPROVED, actor="operator",
                         key_id="approver-v1", signature="00" * 64,
                         authority=AUTHORITY, decided_at=DECIDED, db_path=db)


def test_database_decision_tamper_is_detected_before_consumption(tmp_path):
    db = tmp_path / "approval.sqlite"
    scoped = ApprovalSubject(*(("sha256:" + "1" * 64,) * 3),
                             "preexercise", "2030-01-01T00:00:00Z")
    token = create_pending_approval(scoped, requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE birth_approvals SET decision_at=? WHERE token=?",
                           ("2029-12-31T22:59:59Z", token))
    with pytest.raises(BirthApprovalError, match="signature"):
        _resolve(db, token)


def test_historical_rotation_verifies_but_scope_registry_remains_binding(tmp_path):
    db = tmp_path / "approval.sqlite"
    scoped = ApprovalSubject(*(("sha256:" + "1" * 64,) * 3),
                             "preexercise", "2030-01-01T00:00:00Z")
    token = create_pending_approval(scoped, requested_actor="operator", created_at=CREATED, db_path=db)
    resolve_pending_approval(token, ApprovalDecision.APPROVED, actor="operator", decided_at=DECIDED, db_path=db)
    new_private = Ed25519PrivateKey.generate()
    rotated = ApprovalAuthority(2, {
        "approver-v1": _PRIVATE.public_key(), "approver-v2": new_private.public_key(),
    }, {"operator": {"key_ids": frozenset({"approver-v1", "approver-v2"}),
                       "scopes": frozenset({"preexercise"})}})
    assert _verified(scoped, token=token, now=DECIDED, db_path=db,
                     authority=rotated).key_id == "approver-v1"
    denied = ApprovalAuthority(3, rotated.keys, {
        "operator": {"key_ids": frozenset({"approver-v1", "approver-v2"}),
                     "scopes": frozenset({"authority"})}})
    with pytest.raises(BirthApprovalError, match="actor_scope_unauthorized"):
        _verified(scoped, token=token, now=DECIDED, db_path=db, authority=denied)
