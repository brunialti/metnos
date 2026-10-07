"""Native plan/receipt formats remain conservatively retained, never terminal."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from test_birth_retention_history import history, files
from test_birth_retention_undo import native

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX history backups and undo journal")


@pytest.fixture(scope="module")
def organize():
    spec = importlib.util.spec_from_file_location('retention_native_organize',
        Path(__file__).resolve().parents[2] / 'executors/organize_files/organize_files.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def documents(history, organize, status='prepared'):
    blob = history.create()
    plan = dict(schema=1, binding={}, policy={}, root_identities={}, actions=[])
    plan['token'] = organize._plan_digest(plan)
    path = blob.parent / (plan['token'] + '.frozen-plan.json')
    plan['plan_path'], plan['plan_sha256'] = str(path), 'excluded metadata'
    organize._write_json_atomic(path, plan)
    receipt = organize._prepare_journal(plan, [], blob.parent)
    receipt['status'] = status
    receipt_path = organize._receipt_path(plan['token'])
    organize._write_json_atomic(receipt_path, receipt)
    return blob, path, receipt_path, receipt


@pytest.mark.parametrize('status', ['prepared', 'applying', 'committed', 'rolling_back',
    'rolled_back', 'partial', 'undoing', 'undone'])
def test_native_documents_retain_entire_turn(history, organize, status):
    blob, plan, receipt, _ = documents(history, organize, status)
    other = history.create(turn='unrelated')
    objects = files(history)
    identities = {obj.identity for obj in objects.values() if obj.identity.local_id.startswith('first/')}
    for path in (blob, plan, receipt):
        obj = objects[str(path.relative_to(history.root))]
        assert obj.state is NodeState.OPEN and obj.roots == (RootKind.OPEN_AUDIT,)
        assert obj.eligible_after is None
        if path != blob:
            assert set(obj.references) == identities - {obj.identity}
        with pytest.raises(RetentionError):
            history.owner.delete(obj.identity, obj.version)
        assert path.exists()
    assert objects[str(other.relative_to(history.root))].state is NodeState.CLOSED


def test_native_journal_references_receipt(history, organize):
    _, _, receipt, _ = documents(history, organize)
    history.undo.log.append_pending('op', 'first', 'organize_files', {}, {}, actor='host')
    history.undo.log.append_done('op', {'results': [{'_undo': {'receipt_path': str(receipt),
        'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}}]})
    obj = next(item for item in history.owner.inventory() if item.identity == history.undo.owner.identity('op'))
    assert history.owner.identity(str(receipt.relative_to(history.root))) in obj.references


@pytest.mark.parametrize('mutation', ['token', 'status', 'schema', 'binding', 'backup', 'duplicate'])
def test_contradictory_documents_fail_closed(history, organize, mutation):
    blob, _, receipt_path, receipt = documents(history, organize)
    if mutation == 'token': receipt['token'] = 'f' * 64
    elif mutation == 'status': receipt['status'] = 'unknown'
    elif mutation == 'schema': receipt['schema'] = True
    elif mutation == 'binding': receipt['binding'] = {'changed': True}
    elif mutation == 'backup': receipt['actions'] = [{'backup_ready': True,
        'blob_path': str(blob), 'blob_sha256': 'f' * 64}]
    if mutation == 'duplicate':
        receipt_path.write_text('{"schema":1,"schema":1}')
    else:
        organize._write_json_atomic(receipt_path, receipt)
    with pytest.raises(RetentionError): history.owner.inventory()
    assert blob.exists()


def test_external_quarantine_never_traversed(history, organize, tmp_path):
    _, _, path, receipt = documents(history, organize, 'undone')
    external = tmp_path / '.metnos-organize-delete-private'
    external.write_bytes(b'user retained bytes')
    receipt['actions'] = [{'quarantine_name': str(external), 'path': str(external)}]
    organize._write_json_atomic(path, receipt)
    assert files(history)
    assert external.read_bytes() == b'user retained bytes'


def test_double_scan_detects_document_drift(history, organize, monkeypatch):
    _, _, path, receipt = documents(history, organize)
    original = history.owner._files
    calls = []
    def scan():
        result = original()
        if not calls:
            calls.append(True)
            receipt['failure'] = 'changed'
            organize._write_json_atomic(path, receipt)
        return result
    monkeypatch.setattr(history.owner, '_files', scan)
    with pytest.raises(RetentionError, match='history references changed'):
        history.owner.inventory()


def test_document_cross_turn_backup_reference(history, organize):
    _, _, path, receipt = documents(history, organize)
    blob = history.create(turn='previous')
    receipt['actions'] = [{'backup_ready': True, 'blob_path': str(blob), 'blob_sha256': blob.stem}]
    organize._write_json_atomic(path, receipt)
    objects = files(history)
    obj = objects[str(path.relative_to(history.root))]
    assert history.owner.identity(str(blob.relative_to(history.root))) in obj.references


def test_plan_content_digest_is_native(history, organize):
    blob, plan, _, _ = documents(history, organize)
    value = json.loads(plan.read_bytes())
    value['policy'] = {'changed': True}
    organize._write_json_atomic(plan, value)
    with pytest.raises(RetentionError): history.owner.inventory()
    assert blob.exists()


def test_new_document_blocks_previously_planned_cross_turn_delete(history, organize):
    blob = history.create(turn='previous')
    before = files(history)[str(blob.relative_to(history.root))]
    assert before.state is NodeState.CLOSED
    _, _, path, receipt = documents(history, organize)
    receipt['actions'] = [{'backup_ready': True, 'blob_path': str(blob), 'blob_sha256': blob.stem}]
    organize._write_json_atomic(path, receipt)
    after = files(history)[str(blob.relative_to(history.root))]
    assert after.state is NodeState.OPEN and after.roots == (RootKind.OPEN_AUDIT,)
    with pytest.raises(RetentionError, match='history backup still needed'):
        history.owner.delete(before.identity, before.version)
    assert blob.exists()


def test_aggregate_document_byte_budget(history, organize, monkeypatch):
    _, plan, receipt, _ = documents(history, organize)
    limit = max(plan.stat().st_size, receipt.stat().st_size)
    monkeypatch.setattr('install.birth_retention_history._MAX_DOCUMENT_BYTES', limit)
    with pytest.raises(RetentionError, match='history document byte budget'):
        history.owner.inventory()
