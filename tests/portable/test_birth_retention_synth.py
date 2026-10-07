"""Native failed-run files, references, holds and signed interrupted cleanup."""
from datetime import datetime, timezone
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest
import change_intents as native
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import plan
from install.birth_retention_sqlite import _SQLiteOwner, _intent_state
from install.birth_retention_synth import _SynthProposalOwner
from test_birth_retention_artifacts import RUN, collection

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX file custody')
OLD = '2020-01-01T00:00:00Z'
FUTURE = '2035-01-01T00:00:00Z'


@pytest.fixture
def sample(tmp_path, monkeypatch):
    root, path = tmp_path / 'synt_proposals', tmp_path / 'intents.sqlite'
    root.mkdir(mode=0o700)
    with sqlite3.connect(path) as connection:
        connection.executescript(native._SCHEMA)
    path.chmod(0o600)
    monkeypatch.setattr(native.C, 'DB_CHANGE_INTENTS', path)
    monkeypatch.setattr(native, '_iso_utc_now', lambda: OLD)
    monkeypatch.setattr('test_birth_retention_artifacts.OBSERVED', FUTURE)
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2035, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr('install.birth_retention_synth.datetime', Future)
    intents = _SQLiteOwner(name='change_intents', path=path, schema=native._SCHEMA,
        table='change_intents', primary_key='id', node_type='revision', state=_intent_state,
        require_exclusion=lambda: None, owner=None)
    owner = _SynthProposalOwner(root=root, intents=intents)
    def write(state='abandoned', archived=False, **changes):
        data = dict(id='1577836800_compute_sample', expected_name='compute_sample',
            intent='sample', user_query='sample', ts_start=1577836800, elapsed_s=2,
            final_state=state, name='compute_sample', abandon_reason='stage failure', stages=[])
        data.update(changes)
        target = root / ('_archived' if archived else '') / (data['id'] + '.json')
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(data))
        target.chmod(0o600)
        return target
    def intent(state=native.STATE_PROPOSED):
        ci = native.ChangeIntent.new(origin_family='synt', origin_module='request_new_executor',
            origin_source_id='1577836800_compute_sample', intent_kind=native.KIND_CREATE_EXECUTOR,
            intent_target='compute_sample', intent_summary='sample', intent_body={})
        ci.state = state
        if state == native.STATE_ROLLED_BACK:
            ci.rolled_back_at = OLD
        native.upsert_intent(ci)
        return ci
    return SimpleNamespace(**locals())


def selected(sample, tmp_path, holds=frozenset()):
    owners = sample.owner.owners
    return plan(sample.owner.inventory(), observed_owners=frozenset(owners),
        required_owners=frozenset(owners), observed_roots=frozenset(RootKind), holds=holds,
        graph_path=tmp_path / f'graph-{len(tuple(tmp_path.glob("graph-*.sqlite")))}.sqlite',
        run_id=RUN, observed_at=FUTURE)


def test_closed_source_is_collected_before_its_native_discovery_witness(sample, tmp_path):
    source = sample.write()
    sample.intent(native.STATE_ROLLED_BACK)
    candidates = selected(sample, tmp_path)
    assert len(candidates) == 1 and candidates[0].identity.owner == sample.owner.name
    owners = sample.owner.owners
    _, public, factory = collection(tmp_path, candidates, owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)['completed'] == 1
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not source.exists() and len(sample.intents.scan()) == 1
    later, = selected(sample, tmp_path)
    assert later.identity.owner == sample.intents.name
    sample.intents.delete(later.identity, later.version)
    assert sample.owner.inventory() == ()


def test_open_intent_and_copy_hold_preserve_all_source_copies(sample, tmp_path):
    a, b = sample.write(), sample.write(archived=True)
    before = a.read_bytes(), b.read_bytes()
    objects = sample.owner.inventory()
    assert len(selected(sample, tmp_path)) == 2
    assert selected(sample, tmp_path, frozenset({objects[0].identity.key.node_id})) == ()
    sample.intent()
    assert selected(sample, tmp_path) == ()
    assert (a.read_bytes(), b.read_bytes()) == before


@pytest.mark.parametrize('state', ['synthesized', 'installed', 'in_progress', ''])
def test_operative_state_is_not_closed_by_file_age(sample, tmp_path, state):
    source = sample.write(state)
    os.utime(source, (1, 1))
    assert selected(sample, tmp_path) == ()


@pytest.mark.parametrize('fault', ['extra_reference', 'identity', 'duration', 'state', 'partial', 'link', 'namespace'])
def test_incomplete_or_unsafe_sources_block_inventory(sample, fault):
    source = sample.write()
    if fault in {'extra_reference', 'identity', 'duration', 'state'}:
        data = json.loads(source.read_bytes())
        data.update({'extra_reference': {'job_id': 'pending'}, 'identity': {'id': 'different'},
                     'duration': {'elapsed_s': -1}, 'state': {'final_state': 'unknown'}}[fault])
        source.write_text(json.dumps(data))
    elif fault == 'partial':
        source.write_bytes(b'{')
    elif fault == 'link':
        os.link(source, sample.root.parent / 'alias')
    else:
        (sample.root / 'unknown').mkdir()
    with pytest.raises(RetentionError):
        sample.owner.inventory()


def test_changed_source_and_recent_copy_cannot_be_deleted(sample, tmp_path, monkeypatch):
    source = sample.write()
    obj, = selected(sample, tmp_path)
    sample.write(abandon_reason='changed')
    with pytest.raises(RetentionError, match='version'):
        sample.owner.delete(obj.identity, obj.version)
    monkeypatch.setattr('install.birth_retention_synth.datetime', datetime)
    current, = sample.owner.inventory()
    with pytest.raises(RetentionError, match='not closed or expired'):
        sample.owner.delete(current.identity, current.version)
    assert source.exists()


def test_signed_resume_finishes_unlink_before_parent_sync(sample, tmp_path, monkeypatch):
    source = sample.write()
    owners = sample.owner.owners
    _, public, factory = collection(tmp_path, selected(sample, tmp_path), owners)
    real_sync, inode = os.fsync, sample.root.stat().st_ino
    def fail_once(fd):
        if os.fstat(fd).st_ino == inode:
            raise OSError('injected parent sync failure')
        real_sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fail_once)
        with pytest.raises(RetentionError):
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert not source.exists()
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
