"""Real extend backup writer and native SQLite lifecycle; no live publication."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

import change_applier_extend as extend
import change_intents
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_change_rollback import _ChangeRollbackBlobs, _ChangeRollbackInventory
from install.birth_retention_sqlite import _SQLiteOwner, _intent_state
from manifest_inventory import ContractId, ManifestOrigin
from test_birth_retention_llm_cost import selected


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX rollback custody')
OLD = '2020-01-01T00:00:00Z'
MANIFEST = b'name = "sample"\n[code]\nfiles = ["main.py"]\n[args.properties.existing]\ntype = "string"\n'


@pytest.fixture
def native(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir(mode=0o700)
    authoring = tmp_path / 'executors'; authoring.mkdir(mode=0o700)
    source = authoring / 'sample'; source.mkdir(mode=0o700)
    (source / 'manifest.toml').write_bytes(MANIFEST)
    (source / 'manifest.lang_state.json').write_text('{}')
    (source / 'main.py').write_text('pass\n')
    path = data / 'change_intents.sqlite'
    allowed, publications = [True], []

    def guard():
        if not allowed[0]:
            raise RetentionError('retention_invalid', 'lost exclusion')

    monkeypatch.setattr(extend.C, 'PATH_USER_DATA', data)
    monkeypatch.setattr(extend.C, 'PATH_EXECUTORS', authoring)
    monkeypatch.setattr(extend.C, 'PATH_SYNTH_EXECUTORS', tmp_path / 'absent-synth')
    monkeypatch.setattr(change_intents.C, 'DB_CHANGE_INTENTS', path)
    monkeypatch.setattr(change_intents, '_iso_utc_now', lambda: OLD)
    monkeypatch.setattr(extend, 'require_birth_intent_adapter', lambda: None)
    monkeypatch.setattr(extend, '_contract_id', lambda path: ContractId(ManifestOrigin.USER, 'sample/manifest.toml'))

    def publish(intent):
        # The producer writes and stages real files. Only the publication
        # boundary is replaced, so this cannot be a live Birth acceptance.
        assert intent.candidate_source_root != source
        assert b'[args.properties.added' in (intent.candidate_source_root / 'manifest.toml').read_bytes()
        publications.append(intent)
        return SimpleNamespace(request_id='sha256:' + 'a' * 64,
            publication=SimpleNamespace(current_generation_id='sha256:' + 'b' * 64, repeated=False))

    monkeypatch.setattr(extend, '_run_birth', publish)
    intents = _SQLiteOwner(name='change_intents', path=path, schema=change_intents._SCHEMA,
        table='change_intents', primary_key='id', node_type='revision', state=_intent_state,
        require_exclusion=guard, owner=None)
    blobs = _ChangeRollbackBlobs(root=data / 'rollback_blobs', intents=intents)
    inventory = _ChangeRollbackInventory(intents=intents, blobs=blobs)

    def new(arg='added', persist=True):
        ci = change_intents.ChangeIntent.new(origin_family='user', origin_module='test',
            intent_kind=change_intents.KIND_EXTEND_EXECUTOR, intent_target='sample',
            intent_summary='Extend sample', intent_body={'arg_name': arg, 'arg_type': 'string'})
        if persist:
            change_intents.upsert_intent(ci)
            ci = change_intents.apply_decision(ci.id, action='accept', by='operator')
        return ci

    def apply(ci=None):
        ci = ci or new()
        effect = extend.extend_executor_manifest(ci)
        ci = change_intents.mark_applied(ci.id, effect=effect)
        return ci, effect

    previous = os.umask(0o022)
    try:
        yield SimpleNamespace(**locals())
    finally:
        os.umask(previous)


def test_actual_backup_writer_links_only_the_exact_recorded_intent(native, tmp_path):
    ci, effect = native.apply()
    unrelated = native.new('added_unapplied')
    objects = native.inventory.inventory()
    blob, = [obj for obj in objects if obj.identity.owner == native.blobs.name]
    rows = {json.loads(obj.identity.local_id)[1]: obj for obj in objects
            if obj.identity.owner == native.intents.name}
    assert rows[ci.id].references == (blob.identity,)
    assert blob.references == (rows[ci.id].identity,)
    assert rows[unrelated.id].references == ()
    expected = hashlib.sha256(MANIFEST).hexdigest()[:8] + '-sample.toml'
    assert Path(effect['rollback_blob_path']) == native.blobs.root / expected
    assert Path(effect['rollback_blob_path']).read_bytes() == MANIFEST
    assert (native.source / 'manifest.toml').read_bytes() == MANIFEST
    assert len(native.publications) == 1
    assert blob.state is NodeState.OPEN and blob.roots == (RootKind.OPEN_REVISION,)
    assert selected(native.inventory, tmp_path) == ()
    assert native.blobs.version(blob.identity) == blob.version
    with pytest.raises(RetentionError, match='lacks native closure'):
        native.blobs.delete(blob.identity, blob.version)


def test_one_physical_backup_keeps_every_exact_native_intent_reference(native):
    first, _ = native.apply()
    second, _ = native.apply(native.new('added_again'))
    objects = native.inventory.inventory()
    blob, = [obj for obj in objects if obj.identity.owner == native.blobs.name]
    assert {json.loads(ref.local_id)[1] for ref in blob.references} == {first.id, second.id}
    assert all(obj.references == (blob.identity,) for obj in objects if obj.identity.owner == native.intents.name)


def test_rolled_back_intent_is_still_protected_by_its_open_backup(native, tmp_path):
    ci, _ = native.apply()
    change_intents.mark_rolled_back(ci.id, reason='native rollback complete')
    objects = native.inventory.inventory()
    row, = [obj for obj in objects if obj.identity.owner == native.intents.name]
    blob, = [obj for obj in objects if obj.identity.owner == native.blobs.name]
    assert row.state is NodeState.CLOSED and not row.roots
    assert blob.state is NodeState.OPEN and blob.references == (row.identity,)
    assert selected(native.inventory, tmp_path) == ()


def test_backup_written_before_failed_publication_is_retained_without_invented_intent_link(native, monkeypatch):
    def fail(intent):
        raise RuntimeError('test publication boundary failed')
    monkeypatch.setattr(extend, '_run_birth', fail)
    ci = native.new(persist=False)
    with pytest.raises(RuntimeError, match='requires retry'):
        extend.extend_executor_manifest(ci)
    blob, = native.inventory.inventory()
    assert blob.state is NodeState.OPEN and blob.references == ()
    assert blob.roots == (RootKind.OPEN_REVISION,)
    assert not native.path.exists()


@pytest.mark.parametrize('fault', ['relative', 'outside', 'traversal', 'executor', 'target', 'kind',
                                 'missing_executor', 'missing_blob', 'duplicate_effect', 'effect_null'])
def test_explicit_native_effect_must_resolve_exactly(native, fault):
    ci, effect = native.apply()
    blob = Path(effect['rollback_blob_path'])
    extra = {}
    if fault == 'relative': effect['rollback_blob_path'] = blob.name
    elif fault == 'outside': effect['rollback_blob_path'] = str(native.data / blob.name)
    elif fault == 'traversal': effect['rollback_blob_path'] = str(native.blobs.root) + '/../rollback_blobs/' + blob.name
    elif fault == 'executor': effect['executor_name'] = 'different'
    elif fault == 'target': extra['intent_target'] = 'different'
    elif fault == 'kind': extra['intent_kind'] = change_intents.KIND_CREATE_EXECUTOR
    elif fault == 'missing_executor': del effect['executor_name']
    elif fault == 'missing_blob': blob.unlink()
    payload = json.dumps(effect)
    if fault == 'duplicate_effect': payload = payload[:-1] + ',"rollback_blob_path":' + json.dumps(str(blob)) + '}'
    elif fault == 'effect_null': payload = 'null'
    extra['applied_effect'] = payload
    with sqlite3.connect(native.path) as db:
        db.execute('UPDATE change_intents SET ' + ','.join(key + '=?' for key in extra) + ' WHERE id=?',
                   (*extra.values(), ci.id))
    with pytest.raises(RetentionError):
        native.inventory.inventory()


@pytest.mark.parametrize('fault', ['content', 'toml', 'namespace', 'mode', 'symlink', 'hardlink', 'exclusion'])
def test_native_backup_content_custody_and_phase_are_required(native, fault):
    _, effect = native.apply()
    blob = Path(effect['rollback_blob_path'])
    if fault == 'content': blob.write_bytes(MANIFEST + b'\n')
    elif fault == 'toml':
        blob.unlink()
        payload = b'invalid toml bytes'
        name = hashlib.sha256(payload).hexdigest()[:8] + '-sample.toml'
        (native.blobs.root / name).write_bytes(payload)
    elif fault == 'namespace': (native.blobs.root / 'unknown.tmp').write_bytes(b'unknown')
    elif fault == 'mode': blob.chmod(0o664)
    elif fault == 'symlink':
        outside = native.data / 'outside.toml'
        blob.rename(outside)
        blob.symlink_to(outside)
    elif fault == 'hardlink': os.link(blob, native.data / 'outside.toml')
    elif fault == 'exclusion': native.allowed[0] = False
    with pytest.raises(RetentionError):
        native.inventory.inventory()


def test_join_detects_native_row_change_before_inventory_returns(native, monkeypatch):
    ci, _ = native.apply()
    original = native.intents.scan
    count = 0
    def scan():
        nonlocal count
        count += 1
        if count == 2:
            change_intents.mark_rolled_back(ci.id, reason='drift during inventory')
        return original()
    monkeypatch.setattr(native.intents, 'scan', scan)
    with pytest.raises(RetentionError, match='inventory changed'):
        native.inventory.inventory()


def test_absent_native_stores_are_not_created_by_inventory(native):
    assert native.inventory.inventory() == ()
    assert not native.path.exists() and not native.blobs.root.exists()
