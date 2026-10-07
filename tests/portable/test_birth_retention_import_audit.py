import json
import os
from types import SimpleNamespace

import pytest
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_import_audit import _ImportAuditOwner
from test_birth_retention_artifacts import collection
from test_birth_retention_llm_cost import selected


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX journal custody')


@pytest.fixture
def native(tmp_path, monkeypatch):
    import skill_admission as admission
    from cli import skills_cli as cli
    root = tmp_path / 'audit'; root.mkdir(mode=0o700)
    monkeypatch.setenv('METNOS_AUDIT_DIR', str(root))
    parsed = SimpleNamespace(name='fixture', version='1', source_sha256='a' * 64)
    accepted = admission.AdmissionVerdict('read_files', True,
        smoke_battery_case=admission._smoke_case_for_plan(SimpleNamespace(verb='read', obj='files', qualifier='', args=[])))
    report = admission.AdmissionReport('fixture', accepted=[accepted],
        rejected=[admission.AdmissionVerdict('bad', False, reasons=['rejected'])])
    previous = os.umask(0o077)
    try:
        with monkeypatch.context() as clock:
            clock.setattr(admission.time, 'strftime', lambda *args: '2020-01-01T00:00:00Z')
            admission._write_audit(report, parsed)
            cli._append_audit(parsed, [object(), object()], report,
                              translator_rejected=[('files', 'bad', 'unknown')])
    finally:
        os.umask(previous)
    path = root / 'imports.jsonl'
    return path, _ImportAuditOwner(path=path, require_exclusion=lambda: None, owner=None), cli


def test_signed_collection_preserves_native_status(native, tmp_path, monkeypatch, capsys):
    path, owner, cli = native
    skill = tmp_path / 'skill'; skill.mkdir()
    monkeypatch.setattr(cli, '_resolve_existing_skill_dir', lambda value: skill)
    cli._cmd_status(SimpleNamespace(skill='fixture')); before = capsys.readouterr().out
    original_last = path.read_bytes().splitlines(keepends=True)[-1]
    objects = owner.inventory()
    assert sorted(obj.state.value for obj in objects) == sorted([NodeState.OPEN.value, NodeState.CLOSED.value])
    chosen = selected(owner, tmp_path); assert len(chosen) == 1
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == original_last
    cli._cmd_status(SimpleNamespace(skill='fixture')); assert capsys.readouterr().out == before


def test_physical_last_not_largest_timestamp_and_each_skill(native):
    path, owner, _ = native
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['ts'] = '2022-01-01T00:00:00Z'
    other = dict(rows[0], skill='other', binding='other')
    path.write_text('\n'.join(json.dumps(row) for row in [*rows, other]) + '\n')
    entries = owner.scan()
    retained = [entry.records[0] for entry in entries if entry.object.state is NodeState.OPEN]
    assert retained == [rows[1], other]


@pytest.mark.parametrize('change', [
    {'receipt_id': 'pending'}, {'binding': 'different'}, {'skill_source_sha256': 'invalid'},
    {'ts': '2020-01-01'}, {'ts': '9999-12-31T00:00:00Z'}, {'ts': None},
    {'accepted': 'name'}, {'smoke_cases': [{'pending': True}]},
    {'rejected': [{'name': 'x', 'reasons': 'not-list'}]},
])
def test_unknown_or_inconsistent_observation_preserved(native, change):
    path, owner, _ = native
    record = json.loads(path.read_text().splitlines()[0]); record.update(change)
    path.write_text(json.dumps(record) + '\n')
    with pytest.raises(RetentionError): owner.inventory()


def test_holds_duplicates_and_direct_delete_latest(native, tmp_path):
    path, owner, _ = native
    copy = path.with_name(path.name + '.1'); copy.write_bytes(path.read_bytes()); copy.chmod(0o600)
    objects = owner.inventory(); assert len(objects) == 4
    closed = next(obj for obj in objects if obj.state is NodeState.CLOSED)
    assert len(selected(owner, tmp_path, frozenset({closed.identity.key.node_id}))) == 0
    retained = next(obj for obj in objects if obj.state is NodeState.OPEN)
    with pytest.raises(RetentionError): owner.delete(retained.identity, retained.version)
    path.chmod(0o664)
    with pytest.raises(RetentionError): owner.inventory()
