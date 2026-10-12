"""Actual SourceAuthority grants/copies, observed without a runtime opener."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from durable_workloads.inventory import InventoryLimits
from durable_workloads.models import ExecutionContext
from durable_workloads import source_authority as native
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_source_authority import (
    _SourceAuthorityInventory, _SourceGrantOwner, _SourceSnapshots,
)
from test_birth_retention_llm_cost import selected


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX source custody')
NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _owners(path, guard):
    grants = _SourceGrantOwner(path=path, require_exclusion=guard, owner=None)
    snapshots = _SourceSnapshots(root=path.parent / 'source_snapshots',
        require_exclusion=guard, owner=None)
    return _SourceAuthorityInventory(grants=grants, snapshots=snapshots)


@pytest.fixture
def actual(tmp_path):
    source = tmp_path / 'input.PNG'
    source.write_bytes(b'actual sealed source')
    path = tmp_path / 'private' / 'source_authority.sqlite3'
    current, exclusion = [NOW], [True]

    def guard():
        if not exclusion[0]:
            raise RetentionError('retention_invalid', 'lost exclusion')

    context = ExecutionContext(owner_user_id='owner-a', workload_id='workload-a',
        revision_id='revision-a', stage_id='stage-a', unit_key='unit-a',
        attempt_id='attempt-a', priority='normal', resource_claims=(), deadline_at=None)
    with native.SourceAuthority.open(path, clock=lambda: current[0]) as authority:
        sealed = authority.seal_and_register([source], owner_user_id=context.owner_user_id,
            workload_id=context.workload_id, device_id='server',
            limits=InventoryLimits(max_sources=4, max_total_bytes=1024, max_depth=1),
            valid_until=NOW + timedelta(days=1))
        facts = sealed['sources'][0]
        snapshot = Path(authority.resolve(facts, context).value)
        inventory = _owners(path, guard)
        yield SimpleNamespace(**locals())


def _file_state(paths):
    return {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode),
                  path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in paths}


def test_native_grant_and_lane_files_stay_open_without_runtime_open_or_source_reopen(actual, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('retention must not invoke the runtime opener, prune or resolver')
    monkeypatch.setattr(native.SourceAuthority, 'open', forbidden)
    monkeypatch.setattr(native.SourceAuthority, 'prune', forbidden)
    monkeypatch.setattr(native.SourceAuthority, 'resolve', forbidden)
    observed_paths = (actual.path, Path(str(actual.path) + '-wal'), actual.snapshot,
                      actual.snapshot.parent / 'lease.lock')
    before = _file_state(observed_paths)
    # Concrete locators remain private facts, never paths opened by retention.
    actual.source.unlink()
    objects = actual.inventory.inventory()
    assert len(objects) == 3
    grant, = [obj for obj in objects if obj.identity.owner == 'lre_source_grants']
    files = [obj for obj in objects if obj.identity.owner == 'lre_source_snapshots']
    assert grant.roots == (RootKind.OPEN_AUDIT,)
    assert len(files) == 2 and all(obj.roots == (RootKind.OPEN_REVISION,) for obj in files)
    assert actual.snapshot.suffix == '.png'
    assert all(obj.state is NodeState.OPEN and obj.eligible_after is None
               and obj.references == () for obj in objects)
    assert selected(actual.inventory, tmp_path) == ()
    assert _file_state(observed_paths) == before
    for obj in objects:
        owner = actual.inventory.owners[obj.identity.owner]
        assert owner.version(obj.identity) == obj.version
        with pytest.raises(RetentionError, match='lacks native closure'):
            owner.delete(obj.identity, obj.version)


@pytest.mark.parametrize('closed', ['expired', 'revoked'])
def test_age_or_revocation_does_not_invent_workload_closure(actual, closed, tmp_path):
    if closed == 'revoked':
        actual.authority.revoke_workload('owner-a', 'workload-a')
    else:
        actual.current[0] += timedelta(days=1000)
    objects = actual.inventory.inventory()
    assert len(objects) == 3 and all(obj.state is NodeState.OPEN for obj in objects)
    assert selected(actual.inventory, tmp_path) == ()


def test_native_grant_pruning_leaves_unclosed_snapshot_copies_protected(actual):
    actual.authority.revoke_workload('owner-a', 'workload-a')
    assert actual.authority.prune() == 1
    objects = actual.inventory.inventory()
    assert len(objects) == 2 and all(obj.identity.owner == 'lre_source_snapshots' for obj in objects)
    assert all(obj.state is NodeState.OPEN and not obj.references for obj in objects)
    actual.authority.close()
    assert actual.inventory.inventory() == ()


def test_absent_optional_stores_are_observed_without_creation_and_empty_metadata_is_checked(tmp_path):
    path = tmp_path / 'absent' / 'source_authority.sqlite3'
    inventory = _owners(path, lambda: None)
    assert inventory.inventory() == ()
    assert not path.parent.exists()
    with native.SourceAuthority.open(path) as authority:
        assert inventory.inventory() == ()
        authority._connection.execute('UPDATE source_authority_schema SET version=2')
        with pytest.raises(RetentionError, match='source authority version'):
            inventory.inventory()


@pytest.mark.parametrize('damage', ['scope', 'device', 'source_id', 'locator_type', 'digest',
    'granted_at', 'expires_at', 'checked_at', 'revoked_at', 'generation', 'extra_table', 'mode'])
def test_malformed_native_grants_or_schema_block_inventory(actual, damage):
    changes = {'scope': ('owner_user_id', 'bad scope'), 'device': ('device_id', 'bad device'),
        'source_id': ('source_id', 'source_invalid'), 'locator_type': ('locator_bytes', 'not bytes'),
        'digest': ('content_digest', 'sha256:short'), 'granted_at': ('granted_at', '2020-01-01'),
        'expires_at': ('expires_at', '2019-01-01T00:00:00Z'),
        'checked_at': ('checked_at', 'bad'), 'revoked_at': ('revoked_at', 'bad'),
        'generation': ('check_generation', 'invalid')}
    if damage == 'extra_table':
        actual.authority._connection.execute('CREATE TABLE unexpected(value TEXT)')
    elif damage == 'mode':
        actual.path.chmod(0o644)
    else:
        column, value = changes[damage]
        actual.authority._connection.execute(f'UPDATE source_grants SET {column}=?', (value,))
    with pytest.raises(RetentionError):
        actual.inventory.inventory()


@pytest.mark.parametrize('damage', ['unknown_session', 'unknown_file', 'uppercase_suffix',
    'snapshot_mode', 'lease_mode', 'lease_content', 'symlink', 'hardlink', 'session_symlink'])
def test_unowned_or_unsafe_snapshot_files_block_complete_inventory(actual, damage):
    if damage == 'unknown_session':
        (actual.inventory.snapshots.root / 'unknown').mkdir(mode=0o700)
    elif damage == 'unknown_file':
        (actual.snapshot.parent / 'unexpected.bin').write_bytes(b'unknown')
    elif damage == 'uppercase_suffix':
        other = actual.snapshot.with_suffix('.PNG')
        other.write_bytes(actual.snapshot.read_bytes()); other.chmod(0o400)
    elif damage == 'snapshot_mode':
        actual.snapshot.chmod(0o600)
    elif damage == 'lease_mode':
        (actual.snapshot.parent / 'lease.lock').chmod(0o400)
    elif damage == 'lease_content':
        (actual.snapshot.parent / 'lease.lock').write_bytes(b'invalid native lease')
    elif damage == 'symlink':
        actual.snapshot.unlink(); actual.snapshot.symlink_to(actual.source)
    elif damage == 'hardlink':
        os.link(actual.snapshot, actual.path.parent / 'linked-copy')
    else:
        (actual.inventory.snapshots.root / '.session-symlink123').symlink_to(
            actual.snapshot.parent, target_is_directory=True)
    with pytest.raises(RetentionError):
        actual.inventory.inventory()


def test_crash_session_without_lease_stays_open_and_inventory_drift_or_exclusion_blocks(actual, monkeypatch):
    (actual.snapshot.parent / 'lease.lock').unlink()
    objects = actual.inventory.inventory()
    assert len(objects) == 2 and all(obj.state is NodeState.OPEN for obj in objects)
    original, reads = actual.inventory.snapshots.scan, [0]
    def changed():
        reads[0] += 1
        if reads[0] == 2:
            actual.snapshot.chmod(0o600)
            actual.snapshot.write_bytes(b'changed copy')
            actual.snapshot.chmod(0o400)
        return original()
    monkeypatch.setattr(actual.inventory.snapshots, 'scan', changed)
    with pytest.raises(RetentionError, match='inventory changed'):
        actual.inventory.inventory()
    actual.exclusion[0] = False
    with pytest.raises(RetentionError, match='lost exclusion'):
        actual.inventory.inventory()
