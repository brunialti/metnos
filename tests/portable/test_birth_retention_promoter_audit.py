"""Native promoter observations, private custody and signed compaction."""
import json
import os
from types import SimpleNamespace

import pytest
from jobs import promoter, promoter_state, promoter_digest
import proposal_evaluator
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_promoter_audit import _PromoterDailyAuditOwner
from test_birth_retention_artifacts import collection
from test_birth_retention_llm_cost import selected


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / 'audit'; root.mkdir(mode=0o700)
    proposals = tmp_path / 'proposals'; proposals.mkdir()
    (proposals / 'sample.json').write_text('{}')
    monkeypatch.setattr(promoter_state, '_audit_dir', lambda: root)
    monkeypatch.setattr(promoter_state, '_today_iso_date', lambda: '2020-01-01')
    monkeypatch.setattr(promoter, '_now_iso', lambda: '2020-01-01T00:00:00Z')
    monkeypatch.setattr(promoter_digest, '_now_iso', lambda: '2020-01-01T00:00:00Z')
    monkeypatch.setattr(promoter, '_proposals_dir', lambda: proposals)
    monkeypatch.setattr(promoter, 'render_practical_example', lambda *args: 'example')
    monkeypatch.setattr(promoter, 'insert_pending', lambda *args: None)
    monkeypatch.setattr(promoter, 'upsert_review_needed', lambda **kwargs: None)
    monkeypatch.setattr(promoter, 'upsert_archived', lambda **kwargs: None)
    monkeypatch.setattr(promoter, '_archive_proposal_json', lambda *args: None)
    path = root / 'promoter_2020-01-01.jsonl'
    owner = _PromoterDailyAuditOwner(path=path, require_exclusion=lambda: None, owner=None)
    previous = os.umask(0o022)
    try:
        yield SimpleNamespace(**locals())
    finally:
        os.umask(previous)


def event(native, monkeypatch, verdict='accept', dry_run=True):
    result = SimpleNamespace(verdict=verdict, score=0.75, killers_triggered=[], to_dict=lambda: {})
    monkeypatch.setattr(proposal_evaluator, 'evaluate_proposal', lambda *args, **kwargs: result)
    value = promoter._process_one({'id': 'sample', 'name': 'sample'}, dry_run, 24)
    promoter_state.audit_append(value)
    return value


@pytest.mark.parametrize('verdict,dry_run', [('accept', True), ('gray', True), ('reject', True), ('gray', False), ('reject', False)])
def test_native_process_observation_collects_without_source_deletion(native, monkeypatch, tmp_path, verdict, dry_run):
    event(native, monkeypatch, verdict, dry_run)
    obj, = native.owner.inventory()
    assert obj.state is NodeState.CLOSED and not obj.roots and not obj.references
    _, public, factory = collection(tmp_path, selected(native.owner, tmp_path), native.owner.owners)
    factory().resume(native.owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(native.owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert native.path.read_bytes() == b'' and (native.proposals / 'sample.json').exists()


@pytest.mark.parametrize('outcome', ['finalized', 'grace', 'admission', 'transient', 'crash', 'missing'])
def test_other_native_process_shapes(native, monkeypatch, outcome):
    if outcome in {'finalized', 'grace'}:
        monkeypatch.setattr(promoter, 'promote_to_catalog', lambda p: {'ok': True, 'blob_path': '/native/backup', 'path': '/native/catalog'})
        monkeypatch.setattr(promoter, 'upsert_promoted_grace', lambda **kw: '' if outcome == 'finalized' else '2020-01-02T00:00:00Z')
    elif outcome in {'admission', 'transient'}:
        monkeypatch.setattr(promoter, 'promote_to_catalog', lambda p: {'ok': False, 'error': 'admission_failed_test' if outcome == 'admission' else 'retry', 'reason': 'failure'})
    elif outcome == 'missing':
        (native.proposals / 'sample.json').unlink()
    result = SimpleNamespace(verdict='accept', score=.5, killers_triggered=[], to_dict=lambda: {})
    def evaluate(*args, **kwargs):
        if outcome == 'crash': raise RuntimeError('failure')
        return result
    monkeypatch.setattr(proposal_evaluator, 'evaluate_proposal', evaluate)
    row = promoter._process_one({'id': 'sample', 'name': 'sample'}, False, 0 if outcome == 'finalized' else 24)
    promoter_state.audit_append(row)
    assert len(native.owner.inventory()) == 1


@pytest.mark.parametrize('outcome', ['success', 'failed', 'skipped'])
def test_native_aggregated_notification_shapes(native, monkeypatch, outcome):
    monkeypatch.setattr(promoter_digest, '_resolve_admin_recipient', lambda: (None, 'missing') if outcome == 'skipped' else ('operator', None))
    monkeypatch.setattr(promoter_digest, '_format_aggregated_body', lambda *args: 'body')
    monkeypatch.setattr(promoter_digest, '_build_aggregated_keyboard', lambda: [])
    monkeypatch.setattr(promoter_digest, '_send_to_admin', lambda *args: (outcome == 'success', 'error' if outcome == 'failed' else None))
    monkeypatch.setattr(promoter_digest, 'mark_notified', lambda *args: None)
    promoter_digest._task_aggregated([{'proposal_id': 'sample'}], n_grace=1, n_review=0, n_archived=0, n_total=1)
    assert len(native.owner.inventory()) == 1


def test_daily_rotations_link_copies_and_exclude_review(native, monkeypatch, tmp_path):
    event(native, monkeypatch)
    data = native.path.read_bytes()
    for name in ['promoter_2020-01-02.jsonl', native.path.name + '.1']:
        (native.root / name).write_bytes(data)
        (native.root / name).chmod(0o600)
    (native.root / 'promoter_review_2020-01-01_session.jsonl').write_text('foreign format')
    objects = native.owner.inventory()
    assert len(objects) == 3 and all(len(obj.references) == 2 for obj in objects)
    assert selected(native.owner, tmp_path, frozenset({objects[0].identity.key.node_id})) == ()


@pytest.mark.parametrize('fault', ['unknown_action', 'extra', 'timestamp', 'bool_score', 'partial', 'mode', 'namespace'])
def test_unknown_or_unsafe_audit_fails_closed(native, monkeypatch, fault):
    row = event(native, monkeypatch)
    if fault == 'unknown_action': row['action'] = 'unknown'
    elif fault == 'extra': row['new'] = True
    elif fault == 'timestamp': row['ts'] = 'today'
    elif fault == 'bool_score': row['evaluator_score'] = True
    elif fault == 'mode': native.path.chmod(0o664)
    elif fault == 'namespace': (native.root / 'promoter_unknown.jsonl').write_text('')
    native.path.write_text('{' if fault == 'partial' else json.dumps(row) + '\n')
    with pytest.raises(RetentionError): native.owner.inventory()


@pytest.mark.parametrize('fields', [
    {'action': 'promoted_finalized_via_grace_expiry'},
    {'action': 'skipped', 'reason': 'json_decode_failed'},
    {'action': 'notify_skipped', 'reason': 'no_host_user'},
    {'action': 'notify_failed', 'error': 'send_failed'},
    {'action': 'notified', 'recipient': 'operator', 'chunks': 1},
    {'action': 'rolled_back', 'name': 'sample', 'restored_path': '/native/restored',
     'rolled_back_blob': '/native/rolled', 'prev_state': 'promoted_grace'},
])
def test_native_daily_append_remaining_observation_schemas(native, fields):
    promoter_state.audit_append({'ts': '2020-01-01T00:00:00Z', 'proposal_id': 'sample', **fields})
    obj, = native.owner.inventory()
    assert obj.state is NodeState.CLOSED and not obj.references
