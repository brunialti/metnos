"""Real historical public-set reads, decision signatures and native SQLite rows."""
import base64
import json
import os
import shutil
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
import executor_birth_ownership_chain as chains
from install.birth_retention_birth_sqlite import _BirthBundleOwner
import install.birth_retention_review_links as review_links
from install.birth_retention_review_links import _ReviewApprovalInventory
from test_birth_retention_reviews import review
from rm0008_2b import support
from rm0008_2b.test_group8_public_history import _chain_boundary_fixture, _read_public


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


def test_distinct_signed_contexts_share_one_native_frontier_per_scan(linked, monkeypatch, tmp_path):
    initial_base = support.make_config(tmp_path / 'initial', author=Ed25519PrivateKey.generate(), operator=True)
    support.install_operator_input(initial_base, approval=support.canonical_json(linked.registry))
    support.provision(monkeypatch, initial_base)
    support.use_config(monkeypatch, initial_base)
    initial = _read_public()
    assert initial.set_id != support.installed_set(linked.base).name
    shutil.copytree(support.installed_set(initial_base),
                    support.installed_set(linked.base).parent / initial.set_id)
    for public_key in (support.installed_author_store(initial_base) / 'public').iterdir():
        shutil.copy2(public_key, support.installed_author_store(linked.base) / 'public' / public_key.name)
    support.use_config(monkeypatch, linked.base)
    _chain_boundary_fixture(linked.base, monkeypatch,
        previous_set_id=initial.set_id,
        previous_admission_context_id=initial.material.pin.admission_context_id,
        previous_context_epoch=initial.material.pin.context_epoch)
    shutil.copyfile(support.installed_marker(initial_base), support.installed_marker(linked.base))

    # Three real signed rows span two native public sets; one context repeats.
    for token, subject in (
            ('another-native-token', replace(linked.subject, candidate_id='sha256:' + 'e' * 64)),
            ('initial-native-token', replace(linked.subject,
                admission_context_id=initial.material.pin.admission_context_id))):
        values = dict(linked.values, token=token, candidate_id=subject.candidate_id,
                      admission_context_id=subject.admission_context_id,
                      subject_hash=approval_subject_hash(subject))
        values['decision_signature'] = linked.key.sign(decision_payload(
            token=token, subject_hash=values['subject_hash'], actor='operator',
            decision=ApprovalDecision.APPROVED, decided_at=values['decision_at'], key_id='operator-key')).hex()
        with sqlite3.connect(linked.path) as db:
            db.execute('INSERT INTO birth_approvals (' + ','.join(values) + ') VALUES ('
                       + ','.join('?' for _ in values) + ')', tuple(values.values()))

    batch_calls, frontier_reads = [], []
    native_batch = prepared.load_historical_context_verifiers_for_contexts_in_session_v1
    native_inspect = chains.inspect_ownership_chain_state_v1
    def batch(contexts, session):
        batch_calls.append(contexts)
        return native_batch(contexts, session)
    def inspect():
        frontier_reads.append(1)
        return native_inspect()
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1', batch)
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_in_session_v1',
                        lambda *args: pytest.fail('individual historical authentication'))
    monkeypatch.setattr(chains, 'inspect_ownership_chain_state_v1', inspect)
    for _ in range(2):
        objects, decisions = linked.inventory.scan()
        assert len(objects) == 4 and len(decisions) == 3
    contexts = tuple(sorted((linked.subject.admission_context_id, initial.material.pin.admission_context_id)))
    assert batch_calls == [contexts, contexts]
    assert len(frontier_reads) == 4


def test_unsigned_rows_do_not_load_historical_public_sets(linked, monkeypatch):
    linked.update(status='pending', decision_actor=None, decision_key_id=None,
                  decision_signature=None, decision_at=None)
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1',
                        lambda *args: pytest.fail('unsigned row selected historical authority'))
    objects, decisions = linked.inventory.scan()
    assert not decisions and all(obj.state is NodeState.OPEN for obj in objects)


def test_historical_frontier_drift_in_batch_is_rejected(linked, monkeypatch):
    readings = iter((linked.chain, replace(linked.chain, context_transitions=())))
    monkeypatch.setattr(chains, 'inspect_ownership_chain_state_v1', lambda: next(readings))
    with pytest.raises(RetentionError, match='historical approval authentication'):
        linked.inventory.scan()


def test_shared_native_census_preserves_results_and_physical_reads_without_batch(linked, monkeypatch):
    expected = linked.inventory.scan()
    context = linked.subject.admission_context_id
    bindings = dict(zip((context,),
        prepared.load_historical_context_verifiers_for_contexts_in_session_v1((context,), linked.session)))
    guards, reads, authorities = [], [], []
    native_authority = review_links._load_approval_authority_in_session
    def authority(*args):
        authorities.append(args[0])
        return native_authority(*args)
    def no_batch(*args):
        pytest.fail('shared census repeated historical authentication')
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1', no_batch)
    monkeypatch.setattr(review_links, '_load_approval_authority_in_session', authority)
    for owner in (linked.review.owner, linked.approvals):
        original = owner.scan
        def scan(original=original, name=owner.name):
            reads.append(name)
            return original()
        monkeypatch.setattr(owner, 'scan', scan)
    inventory = _ReviewApprovalInventory(reviews=linked.review.owner, approvals=linked.approvals,
        session=linked.session, require_exclusion=lambda: guards.append(True), context_bindings=bindings)
    bindings.clear()
    for _ in range(2):
        assert inventory.scan() == expected
    assert len(authorities) == 2
    assert reads.count(linked.review.owner.name) == reads.count(linked.approvals.name) == 4
    assert guards
    # Census contexts may be unused when a decision is still pending.
    linked.update(status='pending', decision_actor=None, decision_key_id=None,
                  decision_signature=None, decision_at=None)
    objects, decisions = inventory.scan()
    assert not decisions and all(obj.state is NodeState.OPEN for obj in objects)


@pytest.mark.parametrize('fault', ['mapping', 'key', 'type', 'context', 'head', 'set', 'mixed_head'])
def test_shared_native_census_rejects_inconsistent_bindings(linked, fault):
    context = linked.subject.admission_context_id
    binding, = prepared.load_historical_context_verifiers_for_contexts_in_session_v1((context,), linked.session)
    bindings = {context: binding}
    other_context = 'sha256:' + ('d' * 64 if context != 'sha256:' + 'd' * 64 else 'e' * 64)
    if fault == 'mapping':
        bindings = tuple(bindings.items())
    elif fault == 'key':
        bindings = {'invalid-context': binding}
    elif fault == 'type':
        bindings[context] = SimpleNamespace(required_head_id=binding.required_head_id,
            transition_id=binding.transition_id, public_set=binding.public_set)
    elif fault == 'context':
        pin = replace(binding.public_set.material.pin, admission_context_id=other_context)
        public = replace(binding.public_set, material=replace(binding.public_set.material, pin=pin))
        bindings[context] = replace(binding, public_set=public)
    elif fault == 'head':
        bindings[context] = replace(binding, required_head_id='sha256:invalid')
    elif fault == 'set':
        bindings[context] = replace(binding, public_set=replace(binding.public_set, set_id='../foreign'))
    else:
        pin = replace(binding.public_set.material.pin, admission_context_id=other_context)
        public = replace(binding.public_set, material=replace(binding.public_set.material, pin=pin))
        other_head = 'sha256:' + ('a' * 64 if binding.required_head_id != 'sha256:' + 'a' * 64 else 'b' * 64)
        bindings[other_context] = replace(binding, public_set=public, required_head_id=other_head)
    with pytest.raises(RetentionError, match='context bindings'):
        _ReviewApprovalInventory(reviews=linked.review.owner, approvals=linked.approvals,
            session=linked.session, require_exclusion=lambda: None, context_bindings=bindings)


def test_shared_native_census_rejects_missing_signed_context(linked, monkeypatch):
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1',
                        lambda *args: pytest.fail('supplied census fell back to native authentication'))
    inventory = _ReviewApprovalInventory(reviews=linked.review.owner, approvals=linked.approvals,
        session=linked.session, require_exclusion=lambda: None, context_bindings={})
    with pytest.raises(RetentionError, match='historical approval authentication'):
        inventory.scan()


@pytest.mark.parametrize('fault', ['signature', 'physical_drift', 'root', 'exclusion'])
def test_shared_native_census_preserves_native_guards(linked, monkeypatch, tmp_path, fault):
    context = linked.subject.admission_context_id
    binding, = prepared.load_historical_context_verifiers_for_contexts_in_session_v1((context,), linked.session)
    active = True
    def guard():
        if not active:
            raise RetentionError('retention_owner_changed', 'held exclusion ended')
    inventory = _ReviewApprovalInventory(reviews=linked.review.owner, approvals=linked.approvals,
        session=linked.session, require_exclusion=guard, context_bindings={context: binding})
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1',
                        lambda *args: pytest.fail('shared census repeated historical authentication'))
    if fault == 'signature':
        linked.update(decision_signature='00' * 64)
    elif fault == 'physical_drift':
        original, calls = linked.approvals.scan, 0
        def scan():
            nonlocal calls
            calls += 1
            if calls == 2:
                linked.update(requested_actor='different')
            return original()
        monkeypatch.setattr(linked.approvals, 'scan', scan)
    else:
        def no_read(*args):
            pytest.fail('lost root or held exclusion permitted a physical read')
        monkeypatch.setattr(linked.review.owner, 'scan', no_read)
        if fault == 'root':
            monkeypatch.setattr(linked.session, '_root_path', str(tmp_path / 'foreign-root'))
        else:
            active = False
    with pytest.raises(RetentionError):
        inventory.scan()
