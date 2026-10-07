"""Native Birth operations create the bundles whose deletion is exercised here."""
from datetime import datetime, timedelta, timezone
import os
import signal
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_approval import ApprovalDecision, ApprovalSubject, approval_subject_hash
from executor_birth_approval_authority import ApprovalAuthority, decision_payload
from executor_birth_approval_store import (
    consume_verified_approval, create_pending_approval, resolve_pending_approval,
)
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_producer_store import (
    ProducerReceiptBinding, claim_producer_receipt, finalize_producer_receipt,
    get_or_issue_producer_receipt, producer_history_rows_for_binding_v1,
)
from executor_birth_receipts import IssuerKey, IssuerRegistry, issue_producer_receipt
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_birth_sqlite import _BirthBundleOwner
from install.birth_retention_maintenance import Maintenance, OwnerObject


OLD = "2020-01-01T00:00:00Z"
CREATED = datetime(2020, 1, 1, tzinfo=timezone.utc)
D1, D2, REQUEST = ("sha256:" + digit * 64 for digit in "123")


@pytest.fixture
def producer(tmp_path):
    owner = _BirthBundleOwner("producer_receipts", path=tmp_path / "producer.sqlite",
                              require_exclusion=lambda: None, owner=None)
    key = Ed25519PrivateKey.generate()
    registry = IssuerRegistry({"synt": (IssuerKey(
        "synt-1", key.public_key(), frozenset({ExecutorOrigin.SYNTHESIZED}),
        frozenset({RevisionAuthor.MODEL}),
    ),)})
    encoded = issue_producer_receipt(
        issuer_id="synt", executor_origin=ExecutorOrigin.SYNTHESIZED,
        revision_authorship=RevisionAuthor.MODEL, objective_hash=D1,
        candidate_source_id=D2, issued_at=OLD, expires_at="2020-01-01T01:00:00Z",
        nonce="0123456789abcdef0123456789abcdef", key_id="synt-1", private_key=key,
    )
    binding = ProducerReceiptBinding(D1, D2, ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL)

    def create(state="committed", *, terminal=True):
        get_or_issue_producer_receipt(
            request_id=REQUEST, issuer_id="synt", capability_id="synt_multistage:create_or_replay",
            contract_id="executor:test/example", objective_hash=D1, candidate_source_id=D2,
            registry=registry, now=CREATED, db_path=owner.path, issue=lambda: encoded,
        )
        if state == "available":
            return
        claim_producer_receipt(encoded, registry=registry, binding=binding, request_id=REQUEST,
                               now=CREATED, db_path=owner.path)
        if state != "in_progress":
            # Bundle tests bind native terminal bytes without claiming to
            # authenticate a historical BirthReport or produce an admission.
            finalize_producer_receipt(
                encoded, registry=registry, binding=binding, request_id=REQUEST,
                now=CREATED + timedelta(seconds=1), db_path=owner.path,
                result_binding=D2 if state == "committed" else None,
                rejection_code="native_rejection" if state == "rejected" else None,
                terminal_envelope=b"native terminal bytes" if terminal else None,
                terminal_auth=b"native authentication bytes" if terminal else None,
            )
    return owner, create


@pytest.fixture
def approval(tmp_path):
    owner = _BirthBundleOwner("approvals", path=tmp_path / "approval.sqlite",
                              require_exclusion=lambda: None, owner=None)
    key = Ed25519PrivateKey.generate()
    authority = ApprovalAuthority(1, {"approver": key.public_key()}, {
        "operator": {"key_ids": frozenset({"approver"}), "scopes": frozenset({"synthesized"})},
    })
    subject = ApprovalSubject(D1, D2, D1, "synthesized", "2020-01-01T01:00:00Z")

    def create(state="approved", *, consumed=True):
        token = create_pending_approval(subject, requested_actor="operator",
                                        created_at=CREATED, db_path=owner.path)
        if state == "pending":
            return token
        decision = ApprovalDecision(state)
        signature = key.sign(decision_payload(
            token=token, subject_hash=approval_subject_hash(subject), actor="operator",
            decision=decision, decided_at=OLD, key_id="approver",
        )).hex()
        resolve_pending_approval(token, decision, actor="operator", key_id="approver",
                                 signature=signature, authority=authority,
                                 decided_at=CREATED, db_path=owner.path)
        if consumed and state == "approved":
            consume_verified_approval(subject, token=token, request_id=REQUEST,
                                      now=CREATED, db_path=owner.path, authority=authority)
        return token
    return owner, create


def test_native_projection_has_no_fabricated_sql_identity(producer):
    owner, create = producer
    create()
    row = owner.scan()[0]
    receipt, issuance = producer_history_rows_for_binding_v1(row.values, row.related[0][1][0])
    assert receipt.row_id is None and issuance.row_id is None
    with pytest.raises(ValueError):
        producer_history_rows_for_binding_v1(dict(row.values, row_id=1), row.related[0][1][0])


@pytest.mark.parametrize("kind", ["producer", "approval"])
def test_native_bundle_deletes_parent_and_child_atomically(request, kind):
    owner, create = request.getfixturevalue(kind)
    create()
    row, = owner.scan()
    assert owner.state(row.values).state is NodeState.CLOSED
    assert len(row.related[0][1]) == 1
    owner.delete(row.identity, row.version)
    assert owner.version(row.identity) is None
    with sqlite3.connect(owner.path) as db:
        assert db.execute(f'SELECT count(*) FROM "{owner.child}"').fetchone()[0] == 0
    owner.delete(row.identity, row.version)


@pytest.mark.parametrize("state", ["available", "in_progress"])
def test_expired_producer_work_is_not_closed_by_collector(producer, state):
    owner, create = producer
    create(state)
    row, = owner.scan()
    assert owner.state(row.values).roots == (RootKind.IN_PROGRESS_JOB,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)


def test_legacy_producer_gap_preserves_both_records(producer):
    owner, create = producer
    create("rejected", terminal=False)
    row, = owner.scan()
    assert owner.state(row.values).roots == (RootKind.OPEN_AUDIT,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)
    assert len(owner.scan()) == 1


def test_pending_approval_is_preserved_even_after_expiry(approval):
    owner, create = approval
    create("pending")
    row, = owner.scan()
    assert owner.state(row.values).roots == (RootKind.OPEN_APPROVAL,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)


@pytest.mark.parametrize("kind", ["producer", "approval"])
def test_child_change_or_disappearance_invalidates_whole_bundle(request, kind):
    owner, create = request.getfixturevalue(kind)
    create()
    initial, = owner.scan()
    with sqlite3.connect(owner.path) as db:
        db.execute(f'UPDATE "{owner.child}" SET request_id=?', (D2,))
    assert owner.version(initial.identity) != initial.version
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(initial.identity, initial.version)
    changed, = owner.scan()
    with sqlite3.connect(owner.path) as db:
        db.execute(f'DELETE FROM "{owner.child}"')
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(changed.identity, changed.version)
    assert len(owner.scan()) == 1


@pytest.mark.parametrize("kind", ["producer", "approval"])
def test_orphan_child_is_damage_even_when_parent_is_absent(request, kind):
    owner, create = request.getfixturevalue(kind)
    create()
    row, = owner.scan()
    with sqlite3.connect(owner.path) as db:
        db.execute(f'DELETE FROM "{owner.table}"')  # corrupt with FK checks off
    with pytest.raises(RetentionError, match="retention_owner_invalid"):
        owner.scan()
    with pytest.raises(RetentionError, match="retention_owner_invalid"):
        owner.version(row.identity)


@pytest.mark.parametrize("kind", ["producer", "approval"])
def test_parent_effect_failure_rolls_back_child_deletion(request, kind, monkeypatch):
    owner, create = request.getfixturevalue(kind)
    create()
    row, = owner.scan()
    original = owner._delete_related

    def interrupted(connection, record):
        original(connection, record)
        raise RuntimeError("lost process before parent deletion")

    monkeypatch.setattr(owner, "_delete_related", interrupted)
    with pytest.raises(RuntimeError):
        owner.delete(row.identity, row.version)
    assert owner.version(row.identity) == row.version


@pytest.mark.parametrize("kind,column", [("producer", "objective_hash"),
                                         ("approval", "candidate_id")])
def test_row_cannot_claim_a_different_signed_subject(request, kind, column):
    owner, create = request.getfixturevalue(kind)
    create()
    with sqlite3.connect(owner.path) as db:
        db.execute(f'UPDATE "{owner.table}" SET "{column}"=?', (REQUEST,))
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.scan()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native process termination requires fork")
@pytest.mark.parametrize("point", ["before_effect", "after_effect"])
def test_birth_bundle_recovers_original_receipt_after_process_death(producer, tmp_path, point):
    owner, create = producer
    create()
    row, = owner.scan()
    state = owner.state(row.values)
    obj = OwnerObject(row.identity, row.version, state.state, state.created_at, state.eligible_after)
    root = tmp_path / "maintenance"
    root.mkdir(mode=0o755)
    (root / "holds.json").write_bytes(canonical({"schema_version": 1, "holds": []}))
    (root / "holds.json").chmod(0o600)
    key = Ed25519PrivateKey.generate()
    public, owners = {"receipt": key.public_key()}, {owner.name: owner}
    maintenance = lambda: Maintenance(root, root_owned=False, require_exclusion=lambda: None)
    maintenance().begin((obj,), run_id=D1, observed_at="2026-10-05T12:00:00Z",
                        key_id="receipt", private_key=key, public_keys=public, owners=owners)
    pid = os.fork()
    if pid == 0:
        def crash(stage):
            if stage == point:
                os.kill(os.getpid(), signal.SIGKILL)
        try:
            maintenance().resume(owners, public_keys=public, max_objects=10,
                                 max_seconds=20, crash=crash)
        finally:
            os._exit(91)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert maintenance().resume(owners, public_keys=public, max_objects=10,
                                max_seconds=20)["remaining"] == 0
    assert maintenance().finish(owners, public_keys=public, verify_recovery=lambda: None)["deleted"] == 1
    assert owner.scan() == ()
