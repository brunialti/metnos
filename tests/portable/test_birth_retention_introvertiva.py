"""Whole native snapshots: stable diff, physical copies and signed recovery."""
from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import introvertiva as native
from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_introvertiva import _IntrovertivaOwner
from install.birth_retention_maintenance import plan
from test_birth_retention_artifacts import RUN, collection

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX snapshot custody')
FUTURE = '2035-01-01T00:00:00Z'


@pytest.fixture
def sample(tmp_path, monkeypatch):
    root = tmp_path / 'introvertiva'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(native, 'AUDIT_DIR', root)
    monkeypatch.setattr('test_birth_retention_artifacts.OBSERVED', FUTURE)
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2035, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr('install.birth_retention_introvertiva.datetime', Future)
    owner = _IntrovertivaOwner(root=root, require_exclusion=lambda: None, owner=None)
    def write(stamp, kind='dedupe', archived=False, records=None):
        parent = root / ('_archived/2020/01' if archived else '')
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / f'candidates_{kind}_{stamp}.jsonl'
        if records is None:
            records = [dict(kind='legacy_orphan', mnest_id='m', uses=stamp,
                            src_executor='old', dst_executor='write_files')]
        path.write_text(''.join(json.dumps(row) + '\n' for row in records))
        path.chmod(0o600)
        return path
    return SimpleNamespace(**locals())


def selected(sample, tmp_path, holds=frozenset()):
    owners = sample.owner.owners
    return plan(sample.owner.inventory(), observed_owners=frozenset(owners),
        required_owners=frozenset(owners), observed_roots=frozenset(RootKind), holds=holds,
        graph_path=tmp_path / (uuid4().hex + '.sqlite'), run_id=RUN, observed_at=FUTURE)


def test_signed_collection_preserves_native_diff_and_current_snapshots(sample, tmp_path, monkeypatch):
    old = sample.write(1577836800)
    sample.write(1580515200)
    monkeypatch.setattr('runtime.audit_jsonl.time.time_ns', lambda: 1700000000000000000)
    latest = native._audit_write('candidates_dedupe', [dict(kind='legacy_orphan', mnest_id='m', uses=7)])
    before = native.diff_audit('dedupe')
    latest_bytes = latest.read_bytes()
    chosen = selected(sample, tmp_path)
    assert len(chosen) == 1 and chosen[0].identity.local_id == old.name
    _, public, factory = collection(tmp_path, chosen, sample.owner.owners)
    result = factory().resume(sample.owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(sample.owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert result['completed'] == 1 and not old.exists()
    assert native.diff_audit('dedupe') == before and latest.read_bytes() == latest_bytes


def test_window_is_per_kind_and_uses_native_lexical_order(sample, tmp_path):
    for kind in ('dedupe', 'generalize', 'specialize'):
        sample.write('900000000', kind)
        sample.write('1000000000', kind)
        sample.write('1700000000000000000_' + 'a' * 32, kind)
    chosen = selected(sample, tmp_path)
    assert len(chosen) == 3
    assert all(obj.identity.local_id.endswith('_1000000000.jsonl') for obj in chosen)


def test_copy_and_hold_retain_all_physical_copies(sample, tmp_path):
    old = sample.write(1577836800)
    archived = sample.write(1577836800, archived=True)
    sample.write(1580515200)
    sample.write(1583020800)
    assert len(selected(sample, tmp_path)) == 2
    identity = sample.owner.identity(old.name)
    assert selected(sample, tmp_path, frozenset({identity.key.node_id})) == ()
    latest = sample.write(1583020800, archived=True)
    assert all(obj.identity.local_id != str(latest.relative_to(sample.root)) for obj in selected(sample, tmp_path))
    assert archived.exists()


@pytest.mark.parametrize('fault', ['partial', 'duplicate', 'nan', 'nondict', 'symlink', 'hardlink', 'namespace', 'month'])
def test_incomplete_or_unsafe_inventory_is_rejected(sample, fault):
    path = sample.write(1577836800)
    if fault in {'partial', 'duplicate', 'nan', 'nondict'}:
        path.write_bytes({'partial': b'{"x":1}', 'duplicate': b'{"x":1,"x":2}\n',
                          'nan': b'{"x":NaN}\n', 'nondict': b'[]\n'}[fault])
    elif fault == 'symlink':
        target = path.with_name('candidates_dedupe_1580515200.jsonl')
        target.symlink_to(path)
    elif fault == 'hardlink':
        os.link(path, sample.root.parent / 'alias')
    elif fault == 'namespace':
        (sample.root / 'unknown').mkdir()
    else:
        (sample.root / '_archived/2020/13').mkdir(parents=True)
    with pytest.raises((RetentionError, OSError)):
        sample.owner.inventory()


def test_delete_rechecks_reader_window_and_version(sample, tmp_path):
    old = sample.write(1577836800)
    middle = sample.write(1580515200)
    sample.write(1583020800)
    chosen, = selected(sample, tmp_path)
    middle.unlink()
    with pytest.raises(RetentionError, match='retained snapshot'):
        sample.owner.delete(chosen.identity, chosen.version)
    sample.write(1580515200)
    old.write_text('{"changed":true}\n')
    with pytest.raises(RetentionError, match='version'):
        sample.owner.delete(chosen.identity, chosen.version)


def test_recent_copy_and_empty_native_snapshot(sample, tmp_path, monkeypatch):
    sample.write(1577836800, records=[])
    sample.write(1580515200)
    sample.write(1583020800)
    chosen, = selected(sample, tmp_path)
    monkeypatch.setattr('install.birth_retention_introvertiva.datetime', datetime)
    with pytest.raises(RetentionError, match='retained snapshot'):
        sample.owner.delete(chosen.identity, chosen.version)


def test_signed_resume_finishes_directory_sync_after_unlink(sample, tmp_path, monkeypatch):
    old = sample.write(1577836800)
    sample.write(1580515200)
    sample.write(1583020800)
    owners = sample.owner.owners
    _, public, factory = collection(tmp_path, selected(sample, tmp_path), owners)
    sync, inode = os.fsync, sample.root.stat().st_ino
    def fail(fd):
        if os.fstat(fd).st_ino == inode:
            raise OSError('injected snapshot directory sync failure')
        sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fail)
        with pytest.raises(RetentionError, match='private-file directory') as failure:
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert isinstance(failure.value.__cause__, OSError)
    assert 'injected snapshot' in str(failure.value.__cause__)
    assert not old.exists()
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)['completed'] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
