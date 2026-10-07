"""Native completed observations and authenticated retention collection."""
import json
import os
import time

import pytest
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_evaluation_audit import (
    _ProposalEvaluationAuditOwner, _SemanticVerificationAuditOwner,
)
from test_birth_retention_artifacts import collection
from test_birth_retention_llm_cost import selected


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX journal custody')


@pytest.fixture(params=['evaluation', 'semantic'])
def native(request, tmp_path, monkeypatch):
    import proposal_evaluator as evaluator
    import synt_stage6_verify as semantic
    root = tmp_path / 'audit'; root.mkdir(mode=0o700)
    old = time.gmtime(1577836800)
    monkeypatch.setattr(evaluator.time, 'time', lambda: 1577836800.0)
    monkeypatch.setattr(semantic.time, 'gmtime', lambda: old)
    previous = os.umask(0o077)
    try:
        if request.param == 'evaluation':
            monkeypatch.setattr(evaluator, '_AUDIT_DIR', root)
            proposal = tmp_path / 'proposal.json'
            proposal.write_text(json.dumps({'id': 'sample', 'name': 'sample'}))
            evaluator.evaluate_proposal(proposal, catalog={}, path_queries=[], audit=True)
            path = root / 'proposal_evaluator.jsonl'; cls = _ProposalEvaluationAuditOwner
        else:
            monkeypatch.setattr(semantic, 'VERIFY_AUDIT_DIR', root)
            verdict = semantic.validate_stage6_verdict({'aligned': True, 'mismatch': ''})
            semantic._write_audit('sample', 'prompt', 'response', verdict, ['native-model'])
            path = next(root.glob('verify_*.jsonl')); cls = _SemanticVerificationAuditOwner
    finally:
        os.umask(previous)
    assert path.exists()
    return path, cls(path=path, require_exclusion=lambda: None, owner=None)


def test_native_signed_collection(native, tmp_path):
    path, owner = native
    objects = owner.inventory()
    assert len(objects) == 1 and objects[0].state is NodeState.CLOSED
    _, public, factory = collection(tmp_path, selected(owner, tmp_path), owner.owners)
    factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == b''


def test_rotated_duplicate_hold(native, tmp_path):
    path, owner = native
    copy = path.with_name(path.name + '.1'); copy.write_bytes(path.read_bytes()); copy.chmod(0o600)
    objects = owner.inventory()
    assert len(objects) == 2 and all(len(obj.references) == 1 for obj in objects)
    assert selected(owner, tmp_path, frozenset({objects[0].identity.key.node_id})) == ()


@pytest.mark.parametrize('mutation', ['unknown', 'timestamp', 'custody'])
def test_invalid_preserved(native, mutation):
    path, owner = native
    row = json.loads(path.read_text())
    if mutation == 'unknown': row['pending'] = True
    elif mutation == 'timestamp': row['ts'] = 'tomorrow'
    else: path.chmod(0o664)
    path.write_text(json.dumps(row) + '\n')
    before = path.read_bytes()
    with pytest.raises(RetentionError): owner.inventory()
    assert path.read_bytes() == before


@pytest.mark.parametrize('native', ['semantic'], indirect=True)
def test_semantic_family_and_independent_timestamps(native):
    path, owner = native
    other = path.with_name(path.name.replace('20200101T000000Z', '20200102T000000Z'))
    other.write_bytes(path.read_bytes()); other.chmod(0o600)
    assert len(owner.inventory()) == 2
    unknown = path.with_name('verify_unknown.jsonl'); unknown.write_bytes(b''); unknown.chmod(0o600)
    with pytest.raises(RetentionError): owner.inventory()


@pytest.mark.parametrize('native', ['semantic'], indirect=True)
@pytest.mark.parametrize('change', [{'verdict': {'aligned': True, 'mismatch': 'contradiction'}},
    {'verdict': {'aligned': True, 'mismatch': '', 'model': 'extra'}}, {'name_hint': 'other'}])
def test_semantic_contradictions(native, change):
    path, owner = native
    row = json.loads(path.read_text()); row.update(change)
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(RetentionError): owner.inventory()


def test_timestamp_outside_representable_retention(native):
    path, owner = native
    row = json.loads(path.read_text())
    row['ts'] = 1e100 if isinstance(owner, _ProposalEvaluationAuditOwner) else '9999-12-31T23:59:59Z'
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(RetentionError): owner.inventory()
