"""Actual loader observations collected via signed F6 journal receipts."""
import json
import os
import time
from datetime import datetime, timezone

import pytest
import loader
from audit_jsonl import append_bounded_jsonl
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_affinity_audit import _AffinityAuditOwner
from test_birth_retention_artifacts import collection
from test_birth_retention_llm_cost import selected
from tests.portable.learning_fixtures import _make_catalog_with

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX journal custody')


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / 'synth_audit'; root.mkdir(mode=0o700)
    monkeypatch.setattr(loader, '_AFFINITY_AUDIT_DIR', root)
    original = time.gmtime
    monkeypatch.setattr(time, 'gmtime', lambda *args: original(1577836800))
    catalog = _make_catalog_with({
        'native_sample': dict(affinity=['file', 'read'], manifest_path=tmp_path / 'native.toml'),
        'synth_sample': dict(affinity=['file', 'read'], manifest_path=tmp_path / 'synth.toml', source='synthesized'),
    })
    previous_umask = os.umask(0o077)
    try:
        assert loader._check_affinity_overlap(catalog)
    finally:
        os.umask(previous_umask)
    assert 'synth_sample' not in catalog.executors
    path = root / 'affinity_rejected.jsonl'
    owner = _AffinityAuditOwner(path=path, require_exclusion=lambda: None, owner=None)
    return path, owner


def test_completed_native_observation_signed_collection_preserves_recent(native, tmp_path):
    path, owner = native
    old = owner.inventory()[0]
    assert old.state is NodeState.CLOSED and old.roots == ()
    row = json.loads(path.read_text()); row['ts'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    recent = (json.dumps(row) + '\n').encode()
    with path.open('ab') as stream:
        stream.write(recent)
    chosen = selected(owner, tmp_path)
    assert len(chosen) == 1
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    assert factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == recent
    remaining = owner.inventory()[0]
    with pytest.raises(RetentionError):
        owner.delete(remaining.identity, remaining.version)


def test_all_physical_copies_share_hold_and_can_be_collected(native, tmp_path):
    path, owner = native
    path.with_name(path.name + '.1').write_bytes(path.read_bytes())
    path.with_name(path.name + '.1').chmod(0o600)
    append_bounded_jsonl(path, json.loads(path.read_text()), max_bytes=1, backup_count=None)
    objects = owner.inventory()
    assert len(objects) == 3 and all(len(obj.references) == 2 for obj in objects)
    assert selected(owner, tmp_path, frozenset({objects[0].identity.key.node_id})) == ()
    chosen = selected(owner, tmp_path)
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert owner.inventory() == ()


@pytest.mark.parametrize('fault', ['partial', 'extension', 'reason', 'time', 'score', 'terms', 'symlink', 'segment', 'writable'])
def test_unknown_or_incomplete_records_fail_closed(native, fault):
    path, owner = native
    if fault == 'partial':
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
    elif fault == 'writable':
        path.chmod(0o664)
    elif fault == 'symlink':
        path.with_name(path.name + '.1').symlink_to(path)
    elif fault == 'segment':
        path.with_name(path.name + '.gz').write_bytes(path.read_bytes())
    else:
        row = json.loads(path.read_text())
        field, value = {'extension': ('request_id', 'open'), 'reason': ('reason', 'pending'),
                        'time': ('ts', '2020-1-1T00:00:00Z'), 'score': ('jaccard', 2),
                        'terms': ('shared_terms', ['duplicate', 'duplicate'])}[fault]
        row[field] = value
        path.write_text(json.dumps(row) + '\n')
    with pytest.raises(RetentionError):
        owner.inventory()


def test_signed_interrupted_compaction_recovery(native, tmp_path, monkeypatch):
    path, owner = native
    chosen = selected(owner, tmp_path)
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    import install.birth_retention_jsonl as journals
    original = journals.os.replace
    def interrupt(*args, **kwargs):
        raise OSError('interrupted replacement')
    monkeypatch.setattr(journals.os, 'replace', interrupt)
    with pytest.raises(RetentionError):
        factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    monkeypatch.setattr(journals.os, 'replace', original)
    assert factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == b''
