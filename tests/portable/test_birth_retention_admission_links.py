"""Native historical decision signatures, SQLite consumption and signed copies."""
import os
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

import contract_store as store
from executor_birth_approval import ApprovalDecision, approval_evidence_hash, approval_subject_hash
from executor_birth_approval_authority import decision_payload
from executor_birth_receipts import (AdmissionCheck, AdmissionKind, AdmittedCheckStatus,
    ApprovedLifecycle, RevisionClass, issue_admission_receipt)
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_admission_links import _ApprovalAdmissionInventory
from test_birth_retention_review_links import linked, review
from test_birth_retention_signed_store import history, D


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX signed-store and review custody')


@pytest.fixture
def joined(linked, history):
    subject = linked.subject
    evidence = next(iter(linked.inventory.scan()[1].values()))
    with sqlite3.connect(linked.path) as db:
        db.execute('INSERT INTO birth_approval_consumptions VALUES (?,?,?)',
                   (linked.review.record['token'], D, '2019-12-31T02:00:00Z'))
    history.receipts[1].unlink()
    history.receipts[1].parent.rmdir()
    paths = (history.receipts[0], store._birth_receipt_path_v2(history.directory, history.generation,
                                                          subject.admission_context_id))
    def publish(**changes):
        values = dict(policy_version='historical-policy/v1', contract_id=history.contract,
            generation_id=history.generation, candidate_id=subject.candidate_id,
            semantic_core_id=subject.semantic_core_id, admission_context_id=subject.admission_context_id,
            birth_request_id=D, authoring_journal_hash=D, predecessor_id=None, producer_receipt_hash=D,
            revision_class=RevisionClass.PROMOTION_REVISION,
            check_results={'manifest': AdmissionCheck('historical/v1', AdmittedCheckStatus.PASSED, D)},
            semantic_review_hash=D, approval_hash=approval_evidence_hash(evidence),
            approved_lifecycle=ApprovedLifecycle.ACTIVE, kind=AdmissionKind.ADMISSION,
            issued_at='2020-01-01T00:00:00Z', key_id='admission', private_key=history.admission)
        values.update(changes)
        encoded = issue_admission_receipt(**values)
        for path in paths:
            history.write(path, encoded)
    publish()
    inventory = _ApprovalAdmissionInventory(review_links=linked.inventory, signed_store=history.owner,
                                             require_exclusion=lambda: None)
    return SimpleNamespace(**locals())


def test_all_authenticated_physical_copies_link_and_consumption_remains_open(joined):
    objects = joined.inventory.inventory()
    approval = next(obj for obj in objects if obj.identity.owner == joined.linked.approvals.name)
    receipts = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    assert len(receipts) == 2
    assert approval.state is NodeState.OPEN and approval.eligible_after is None
    assert RootKind.OPEN_AUDIT in approval.roots
    for receipt in receipts:
        assert approval.identity in receipt.references
        assert receipt.identity in approval.references
    assert len(objects) == len(joined.linked.inventory.inventory()) + len(joined.history.owner.inventory())


def test_consumption_without_receipts_is_open_not_corruption(joined):
    # Empty signed history is valid while consumption survives failed admission.
    import shutil
    shutil.rmtree(joined.history.directory)
    objects = joined.inventory.inventory()
    approval = next(obj for obj in objects if obj.identity.owner == joined.linked.approvals.name)
    assert approval.state is NodeState.OPEN and RootKind.OPEN_AUDIT in approval.roots
    assert not any(obj.identity.node_type == 'admission_receipt' for obj in objects)


def test_null_approval_hash_does_not_invent_receipt_links(joined):
    joined.publish(approval_hash=None)
    objects = joined.inventory.inventory()
    approval = next(obj for obj in objects if obj.identity.owner == joined.linked.approvals.name)
    receipts = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    assert all(approval.identity not in obj.references for obj in receipts)
    assert approval.state is NodeState.OPEN


@pytest.mark.parametrize('fault', ['missing', 'request', 'hash', 'candidate', 'core', 'scope', 'signature', 'context', 'rejected'])
def test_authenticated_bindings_cannot_be_substituted(joined, fault):
    if fault == 'missing':
        with sqlite3.connect(joined.linked.path) as db:
            db.execute('DELETE FROM birth_approval_consumptions')
    elif fault == 'request':
        with sqlite3.connect(joined.linked.path) as db:
            db.execute('UPDATE birth_approval_consumptions SET request_id=?', ('sha256:'+'b'*64,))
    elif fault == 'rejected':
        joined.linked.update(status='rejected', decision_signature=joined.linked.sign('rejected'))
    elif fault == 'context':
        context = 'sha256:'+'f'*64
        joined.publish(admission_context_id=context)
        replacement = store._birth_receipt_path_v2(joined.history.directory, joined.history.generation, context)
        joined.history.write(replacement, joined.paths[1].read_bytes())
        joined.paths[1].unlink()
    elif fault == 'signature':
        joined.linked.update(decision_signature='00'*64)
    else:
        changes = {'hash': {'approval_hash': 'sha256:'+'b'*64},
                   'candidate': {'candidate_id': 'sha256:'+'b'*64},
                   'core': {'semantic_core_id': 'sha256:'+'f'*64},
                   'scope': {'revision_class': RevisionClass.AUTHORITY_REVISION}}
        joined.publish(**changes[fault])
    with pytest.raises(RetentionError):
        joined.inventory.inventory()


def test_changes_between_complete_observations_fail(joined, monkeypatch):
    original = joined.inventory._snapshot
    calls = 0
    def observe():
        nonlocal calls
        calls += 1
        if calls == 2:
            with sqlite3.connect(joined.linked.path) as db:
                db.execute('UPDATE birth_approval_consumptions SET consumed_at=?', ('2019-12-31T03:00:00Z',))
        return original()
    monkeypatch.setattr(joined.inventory, '_snapshot', observe)
    with pytest.raises(RetentionError, match='admission links changed'):
        joined.inventory.inventory()


def test_consumption_and_generation_without_receipts_remain_open(joined):
    for path in joined.paths:
        path.unlink()
    objects = joined.inventory.inventory()
    assert not any(obj.identity.node_type == 'admission_receipt' for obj in objects)
    assert any(obj.identity.node_type == 'generation' for obj in objects)
    approval = next(obj for obj in objects if obj.identity.owner == joined.linked.approvals.name)
    assert approval.state is NodeState.OPEN and RootKind.OPEN_AUDIT in approval.roots
    assert joined.history.gen.exists()


def test_distinct_authenticated_receipt_does_not_borrow_other_consumption(joined):
    original = joined.paths[0].read_bytes()
    context, request = 'sha256:' + 'e' * 64, 'sha256:' + 'f' * 64
    joined.publish(admission_context_id=context, birth_request_id=request, approval_hash=None)
    other = store._birth_receipt_path_v2(joined.history.directory, joined.history.generation, context)
    joined.history.write(other, joined.paths[1].read_bytes())
    joined.paths[1].unlink()
    joined.history.write(joined.paths[0], original)
    objects = joined.inventory.inventory()
    approval = next(obj for obj in objects if obj.identity.owner == joined.linked.approvals.name)
    receipts = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    assert len(receipts) == 2
    assert sum(approval.identity in obj.references for obj in receipts) == 1
    # Both signatures pass; only the distinct receipt's claimed consumption is absent.
    joined.publish(admission_context_id=context, birth_request_id=request)
    joined.history.write(other, joined.paths[1].read_bytes())
    joined.paths[1].unlink()
    joined.history.write(joined.paths[0], original)
    assert len(joined.history.owner.scan()[1]) == 2
    with pytest.raises(RetentionError, match='consumption absent'):
        joined.inventory.inventory()


@pytest.mark.parametrize('scope,revision', [('authority', RevisionClass.AUTHORITY_REVISION),
                                           ('reactivation', RevisionClass.REACTIVATION_REVISION)])
def test_native_revision_scopes_link_when_exactly_approved(joined, scope, revision):
    native = joined.linked
    subject = replace(native.subject, lifecycle=scope)
    signature = native.key.sign(decision_payload(token=native.review.record['token'],
        subject_hash=approval_subject_hash(subject), actor='operator', decision=ApprovalDecision.APPROVED,
        decided_at='2019-12-31T01:00:00Z', key_id='operator-key')).hex()
    native.update(approval_scope=scope, subject_hash=approval_subject_hash(subject), decision_signature=signature)
    for path in native.review.root.glob('*.json'):
        path.unlink()
    native.review.record['subject']['lifecycle'] = scope
    native.review.write()
    evidence = next(iter(native.inventory.scan()[1].values()))
    joined.publish(revision_class=revision, approval_hash=approval_evidence_hash(evidence))
    objects = joined.inventory.inventory()
    approval = next(obj for obj in objects if obj.identity.owner == native.approvals.name)
    receipts = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    assert len(receipts) == 2
    assert all(approval.identity in obj.references for obj in receipts)
    assert all(obj.identity in approval.references for obj in receipts)
