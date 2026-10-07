"""Real historical public-set reads, decision signatures and native SQLite rows."""
import base64
import json
import os
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from executor_birth_approval import ApprovalDecision, ApprovalSubject, approval_subject_hash
from executor_birth_approval_authority import decision_payload
from executor_birth_approval_store import _SCHEMA
from executor_birth_retention import NodeState, RetentionError
import executor_birth_prepared_root as prepared
from install.birth_retention_birth_sqlite import _BirthBundleOwner
from install.birth_retention_review_links import _ReviewApprovalInventory
from test_birth_retention_reviews import review
from rm0008_2b import support
from rm0008_2b.test_group8_public_history import _chain_boundary_fixture


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX administrative review custody')


@pytest.fixture
def linked(review, tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    base = support.make_config(tmp_path / 'native', author=Ed25519PrivateKey.generate(), operator=True)
    registry = json.loads(support.approval_document())
    registry['keys']['operator-key'] = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    registry['actors']['operator']['scopes'] = ['promotion', 'authority', 'reactivation']
    support.install_operator_input(base, approval=support.canonical_json(registry))
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    chain = _chain_boundary_fixture(base, monkeypatch)
    review.record['subject']['admission_context_id'] = chain.context_transitions[0].prepared_admission_context_id
    review.write()
    subject = ApprovalSubject(**review.record['subject'])
    path = tmp_path / 'approvals.sqlite'
    def update(**changes):
        with sqlite3.connect(path) as db:
            db.execute('UPDATE birth_approvals SET ' + ','.join(f'{k}=?' for k in changes), tuple(changes.values()))
    def sign(status='approved'):
        return key.sign(decision_payload(token=review.record['token'], subject_hash=approval_subject_hash(subject),
            actor='operator', decision=ApprovalDecision(status), decided_at='2019-12-31T01:00:00Z', key_id='operator-key')).hex()
    values = dict(token=review.record['token'], subject_hash=approval_subject_hash(subject), candidate_id=subject.candidate_id,
        semantic_core_id=subject.semantic_core_id, admission_context_id=subject.admission_context_id,
        approval_scope=subject.lifecycle, requested_actor='operator', expires_at=subject.expires_at,
        status='approved', created_at=review.record['created_at'], decision_actor='operator',
        decision_at='2019-12-31T01:00:00Z', decision_key_id='operator-key', decision_signature=sign())
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA)
        db.execute('INSERT INTO birth_approvals (' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', tuple(values.values()))
    path.chmod(0o600)
    approvals = _BirthBundleOwner('approvals', path=path, require_exclusion=lambda: None, owner=None)
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            inventory = _ReviewApprovalInventory(reviews=review.owner, approvals=approvals, session=session, require_exclusion=lambda: None)
            yield SimpleNamespace(**locals())


def test_expired_historical_signature_and_exact_bidirectional_links(linked):
    objects, decisions = linked.inventory.scan()
    assert len(objects) == 2 and len(decisions) == 1
    assert all(len(obj.references) == 1 for obj in objects)
    assert {obj.state for obj in objects} == {NodeState.OPEN, NodeState.CLOSED}
    assert next(iter(decisions.values())).decision is ApprovalDecision.APPROVED
    with pytest.raises(TypeError):
        decisions[next(iter(decisions))] = None


@pytest.mark.parametrize('status', ['pending', 'expired', 'rejected'])
def test_other_native_states_do_not_invent_consent(linked, status):
    linked.update(status=status, decision_actor='operator' if status == 'rejected' else None,
        decision_key_id='operator-key' if status == 'rejected' else None,
        decision_signature=linked.sign('rejected') if status == 'rejected' else None,
        decision_at=None if status == 'pending' else '2019-12-31T01:00:00Z')
    objects, decisions = linked.inventory.scan()
    assert len(decisions) == (1 if status == 'rejected' else 0)
    if status != 'rejected':
        assert all(obj.state is NodeState.OPEN for obj in objects)


@pytest.mark.parametrize('fault', ['signature', 'context', 'token', 'subject'])
def test_tampered_native_link_fails_closed(linked, fault):
    if fault == 'signature':
        linked.update(decision_signature='00' * 64)
    elif fault == 'context':
        subject = replace(linked.subject, admission_context_id='sha256:' + 'd' * 64)
        linked.update(admission_context_id=subject.admission_context_id, subject_hash=approval_subject_hash(subject))
    elif fault == 'token':
        signature = linked.key.sign(decision_payload(token='other-token', subject_hash=approval_subject_hash(linked.subject),
            actor='operator', decision=ApprovalDecision.APPROVED, decided_at='2019-12-31T01:00:00Z', key_id='operator-key')).hex()
        linked.update(token='other-token', decision_signature=signature)
    else:
        linked.update(subject_hash='sha256:' + 'd' * 64)
    with pytest.raises(RetentionError):
        linked.inventory.scan()


def test_second_read_detects_native_drift(linked, monkeypatch):
    original = linked.approvals.scan
    calls = 0
    def scan():
        nonlocal calls
        calls += 1
        if calls == 2:
            linked.update(requested_actor='different')
        return original()
    monkeypatch.setattr(linked.approvals, 'scan', scan)
    with pytest.raises(RetentionError, match='inventory changed'):
        linked.inventory.scan()
