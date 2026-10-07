"""Native cost writer, signed F6 collection and physical segment boundaries."""
from datetime import datetime, timezone
import json
import os
from uuid import uuid4

import pytest

import llm_cost_sink as sink
from audit_jsonl import append_bounded_jsonl
from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_llm_cost import _LlmCostOwner
from install.birth_retention_maintenance import plan
from test_birth_retention_artifacts import RUN, OBSERVED, collection

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX journal custody")
OLD = 1577836800.0


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / 'telemetry'
    root.mkdir(mode=0o700)
    path = root / 'llm_usage.jsonl'
    monkeypatch.setenv('METNOS_LLM_COST_LOG_PATH', str(path))
    monkeypatch.setattr(sink.time, 'time', lambda: OLD)
    owner = _LlmCostOwner(path=path, require_exclusion=lambda: None, owner=None)
    sink._sink(dict(provider='local', model='test', in_tokens=2, out_tokens=3))
    assert path.exists()
    return path, owner


def selected(owner, tmp_path, holds=frozenset()):
    return plan(owner.inventory(), observed_owners=frozenset(owner.owners),
                required_owners=frozenset(owner.owners), observed_roots=frozenset(RootKind),
                holds=holds, graph_path=tmp_path / (uuid4().hex + '.sqlite'), run_id=RUN, observed_at=OBSERVED)


def test_signed_compaction_preserves_recent_bytes_and_native_summary(native, tmp_path):
    path, owner = native
    row = json.loads(path.read_text())
    row['ts'] = datetime.now(timezone.utc).timestamp()
    row['ts_iso'] = datetime.fromtimestamp(row['ts'], timezone.utc).isoformat()
    recent = (json.dumps(row) + '\n').encode()
    with path.open('ab') as stream:
        stream.write(recent)
    chosen = selected(owner, tmp_path)
    assert len(chosen) == 1
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    assert factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == recent
    assert sink.summarize(path)['rows'] == 1


def test_rotated_and_legacy_copies_follow_hold(native, tmp_path):
    path, owner = native
    row = json.loads(path.read_text())
    path.with_name(path.name + '.1').write_bytes(path.read_bytes())
    path.with_name(path.name + '.1').chmod(0o600)
    append_bounded_jsonl(path, row, max_bytes=1, backup_count=None)
    objects = owner.inventory()
    assert len(objects) == 3
    assert all(len(obj.references) == 2 for obj in objects)
    assert selected(owner, tmp_path, frozenset({objects[0].identity.key.node_id})) == ()
    chosen = selected(owner, tmp_path)
    assert len(chosen) == 3
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert owner.inventory() == ()
    assert all(p.stat().st_size == 0 for p in path.parent.glob(path.name + '*'))


@pytest.mark.parametrize('damage', ['partial', 'extension', 'timestamp', 'symlink', 'unknown_segment'])
def test_incomplete_or_unknown_input_blocks_inventory(native, damage):
    path, owner = native
    if damage == 'partial':
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
    elif damage == 'symlink':
        path.with_name(path.name + '.1').symlink_to(path)
    elif damage == 'unknown_segment':
        path.with_name(path.name + '.unknown').write_bytes(path.read_bytes())
    else:
        row = json.loads(path.read_text())
        if damage == 'extension':
            row['job_id'] = 'still-open'
        else:
            row['ts_iso'] = '2025-01-01T00:00:00+00:00'
        path.write_text(json.dumps(row) + '\n')
    with pytest.raises(RetentionError):
        owner.inventory()


def test_changed_record_and_retention_window_refuse_direct_delete(native):
    path, owner = native
    obj = owner.inventory()[0]
    row = json.loads(path.read_text())
    row['ts'] = datetime.now(timezone.utc).timestamp()
    row['ts_iso'] = datetime.fromtimestamp(row['ts'], timezone.utc).isoformat()
    path.write_text(json.dumps(row) + '\n')
    recent = owner.inventory()[0]
    assert owner.version(obj.identity) is None
    with pytest.raises(RetentionError):
        owner.delete(recent.identity, recent.version)
    assert sink.summarize(path)['rows'] == 1


def test_interrupted_replace_resumes_signed_receipt(native, tmp_path, monkeypatch):
    path, owner = native
    chosen = selected(owner, tmp_path)
    _, public, factory = collection(tmp_path, chosen, owner.owners)
    import install.birth_retention_jsonl as journals
    original = journals.os.replace
    def interrupt(*args, **kwargs):
        raise OSError('injected before journal replacement')
    monkeypatch.setattr(journals.os, 'replace', interrupt)
    with pytest.raises(RetentionError, match="journal read"):
        factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)
    monkeypatch.setattr(journals.os, 'replace', original)
    assert factory().resume(owner.owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(owner.owners, public_keys=public, verify_recovery=lambda: None)
    assert path.read_bytes() == b''
