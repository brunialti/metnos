"""Real native demotions joined to signed F6 journal collection."""
import json
import os

import pytest
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_efficacy_audit import _EfficacyAuditOwner
from test_birth_retention_artifacts import collection
from test_birth_retention_llm_cost import selected
from tests.portable.learning_fixtures import isolated_aging_db, _write_turn_log


@pytest.fixture
def native(isolated_aging_db):
    ea = isolated_aging_db['module']
    turns = isolated_aging_db['turns_dir']
    ea.register('audit_synth', source='synth:reactive')
    ea.register('audit_feedback', source='synth:reactive')
    _write_turn_log(turns, '2020-01-01.jsonl', [
        {'chosen_tool': 'audit_synth', 'result': {'ok': False}, 'error': 'failed'}
        for _ in range(140)])
    previous = os.umask(0o077)
    try:
        ea.apply_efficacy_ager(turns_dir=turns, now_iso='2020-01-01T00:00:00Z')
        ea.apply_efficacy_ager(turns_dir=turns, now_iso='2020-01-02T00:00:00Z')
        ea.apply_feedback_ager('audit_feedback', consecutive_errors=3, now_iso='2020-01-03T00:00:00Z')
    finally:
        os.umask(previous)
    path = isolated_aging_db['audit_dir'] / 'efficacy_demotions.jsonl'
    assert ea.lookup('audit_synth').archived_at and ea.lookup('audit_feedback').deprecated_at
    return path, _EfficacyAuditOwner(path=path, require_exclusion=lambda: None, owner=None), ea


def test_all_native_terminal_schemas_collect_without_changing_lifecycle(native, tmp_path):
    path, owner, ea = native
    objects = owner.inventory()
    assert len(objects) == 3 and all(obj.state is NodeState.CLOSED and not obj.roots for obj in objects)
    _, public, factory = collection(tmp_path, selected(owner, tmp_path), owner.owners)
    factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == b''
    assert ea.lookup('audit_synth').archived_at and ea.lookup('audit_feedback').deprecated_at


@pytest.mark.parametrize('change', [
    {'proposal_id': 'pending'}, {'event': 'pending'}, {'success_rate': True},
    {'success_rate': .1}, {'ok_invocations': 141}, {'total_invocations': 0},
    {'name': ''}, {'ts': '2020-01-01'},
])
def test_unknown_or_inconsistent_schema_preserved(native, change):
    path, owner, _ = native
    row = json.loads(path.read_text().splitlines()[0]); row.update(change)
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(RetentionError):
        owner.inventory()


def test_feedback_schema_and_custody_preserved(native):
    path, owner, _ = native
    row = json.loads(path.read_text().splitlines()[-1]); row['consecutive_errors'] = True
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(RetentionError):
        owner.inventory()
    row['consecutive_errors'] = 3
    path.write_text(json.dumps(row) + '\n'); path.chmod(0o664)
    with pytest.raises(RetentionError):
        owner.inventory()


def test_rotated_copies_propagate_holds(native, tmp_path):
    path, owner, _ = native
    copy = path.with_name(path.name + '.1'); copy.write_bytes(path.read_bytes()); copy.chmod(0o600)
    objects = owner.inventory()
    assert len(objects) == 6 and all(len(obj.references) == 1 for obj in objects)
    hold = frozenset({objects[0].identity.key.node_id})
    assert len(selected(owner, tmp_path, hold)) == 4
