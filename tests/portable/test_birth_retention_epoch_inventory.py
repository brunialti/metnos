"""Real epoch transitions, migrations and authenticated physical history."""
import os

import pytest
import executor_birth_epoch_store as native
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_epoch_sqlite import _EpochOwner
from install.birth_retention_epoch_inventory import _EpochInventory
from install.birth_retention_maintenance import plan
from test_birth_retention_signed_store import history, D

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX signed-store custody')
OLD = '2020-01-01T00:00:00Z'


@pytest.fixture
def joined(history, tmp_path):
    epochs = _EpochOwner(path=tmp_path / 'epochs.sqlite', require_exclusion=lambda: None, owner=None)
    return _EpochInventory(epochs=epochs, signed_store=history.owner)


def open_epoch(joined, history, archived=False, generation=None):
    generation = generation or history.generation
    native.open_epoch(contract_id=history.contract, generation_id=generation,
        name='historical', source='synt', lifecycle=native.BirthLifecycle.ACTIVE,
        observed_at=OLD, db_path=joined.epochs.path)
    if archived:
        for version, before, after in ((1, 'active', 'deprecated'), (2, 'deprecated', 'archived')):
            native.transition_epoch(native.EpochCacheKey(history.contract, generation,
                native.BirthLifecycle(before)), expected_version=version,
                new_state=native.EpochState(after), new_lifecycle=native.BirthLifecycle(after),
                event_kind=after, occurred_at=OLD, db_path=joined.epochs.path)


def selected(joined, tmp_path):
    return plan(joined.inventory(), required_owners=frozenset(joined.owners),
        observed_owners=frozenset(joined.owners), observed_roots=frozenset(RootKind),
        holds=frozenset(), graph_path=tmp_path / 'graph.sqlite', run_id=D,
        observed_at='2035-01-01T00:00:00Z')


def test_historical_generation_does_not_invent_epoch(joined, history):
    objects = joined.inventory()
    assert len(objects) == 8 and not joined.epochs.path.exists()
    assert {obj.identity for obj in objects} == {obj.identity for obj in history.owner.inventory()}


def test_current_pointer_preserves_even_archived_epoch(joined, history, tmp_path):
    open_epoch(joined, history, archived=True)
    objects = joined.inventory()
    epoch = next(obj for obj in objects if obj.identity.owner == joined.epochs.name)
    generation = next(obj for obj in objects if obj.identity == history.owner.generation_identity(
        history.contract, history.generation))
    assert epoch.state is NodeState.CLOSED
    assert generation.identity in epoch.references and epoch.identity in generation.references
    assert selected(joined, tmp_path) == ()


def test_unreferenced_archived_epoch_keeps_native_collectibility(joined, history, tmp_path):
    open_epoch(joined, history, archived=True)
    (history.directory / 'current').unlink()
    candidates = selected(joined, tmp_path)
    assert len(candidates) == 1
    row, = joined.epochs.scan()
    joined.epochs.delete(row.identity, row.version)
    assert len(joined.inventory()) == 7
    assert len(history.owner.inventory()) == 7


def test_epoch_generation_gap_blocks_before_collection(joined, history):
    open_epoch(joined, history, archived=True, generation='sha256:' + 'f' * 64)
    with pytest.raises(RetentionError, match='epoch generation absent'):
        joined.inventory()


def migration(joined, history, *, generation=None):
    rows = ({'name': 'historical', 'calls': 7},)
    native.migrate_legacy_rows(source_id=D, source_schema_id=D, legacy_table='executor_usage',
        rows=rows, expected_count=1, expected_digest=native.legacy_rows_digest(rows),
        migrated_at=OLD, db_path=joined.epochs.path)
    migration_id = native.verify_preserved_migrations(db_path=joined.epochs.path)[0]['migration_id']
    native.record_legacy_resolutions(migration_id=migration_id,
        resolutions=((0, 'attested', history.contract.value, generation or history.generation, D),),
        recorded_at=OLD, db_path=joined.epochs.path)


def test_legacy_proof_keeps_exact_generation_and_epoch(joined, history, tmp_path):
    open_epoch(joined, history, archived=True)
    migration(joined, history)
    (history.directory / 'current').unlink()
    objects = joined.inventory()
    ids = {obj.identity for obj in objects}
    assert all(set(obj.references) <= ids for obj in objects)
    state = next(obj for obj in objects if obj.identity.owner == joined.legacy[0].name)
    proof = next(obj for obj in objects if obj.identity.owner == joined.legacy[1].name)
    epoch = next(obj for obj in objects if obj.identity.owner == joined.epochs.name)
    assert proof.identity in state.references and state.identity in proof.references
    assert epoch.identity in state.references and state.identity in epoch.references
    assert history.owner.generation_identity(history.contract, history.generation) in state.references
    assert selected(joined, tmp_path) == ()
    assert native.verify_preserved_migrations(db_path=joined.epochs.path)


def test_attestation_can_name_history_without_fabricated_epoch(joined, history):
    migration(joined, history)
    objects = joined.inventory()
    assert len(objects) == 10
    assert not any(obj.identity.owner == joined.epochs.name for obj in objects)


def test_legacy_generation_gap_is_not_lost_as_empty_epoch(joined, history):
    migration(joined, history, generation='sha256:' + 'f' * 64)
    with pytest.raises(RetentionError, match='epoch generation absent'):
        joined.inventory()


def test_late_epoch_change_invalidates_join(joined, history, monkeypatch):
    open_epoch(joined, history)
    original, reads = joined._rows, []
    def read():
        reads.append(1)
        if len(reads) == 2:
            native.put_cache(native.EpochCacheKey(history.contract, history.generation,
                native.BirthLifecycle.ACTIVE), b'late write', created_at=OLD, db_path=joined.epochs.path)
        return original()
    monkeypatch.setattr(joined, '_rows', read)
    with pytest.raises(RetentionError, match='epoch inventory changed'):
        joined.inventory()


def test_cross_contract_same_digest_is_not_a_reference(joined, history):
    from manifest_inventory import ContractId, ManifestOrigin
    foreign = ContractId(ManifestOrigin.USER, 'historical/manifest.toml')
    native.open_epoch(contract_id=foreign, generation_id=history.generation,
        name='historical', source='synt', lifecycle=native.BirthLifecycle.ACTIVE,
        observed_at=OLD, db_path=joined.epochs.path)
    with pytest.raises(RetentionError, match='epoch generation absent'):
        joined.inventory()


def test_retirement_preserves_predecessor_epoch(joined, history, tmp_path):
    import contract_store as store
    open_epoch(joined, history, archived=True)
    payload = store.encode_retirement(history.contract, previous_generation_id=history.generation,
        actor='administrator', reason='native retirement')
    payloads = {'retirement.json': payload,
        'retirement.json.sig': history.author.sign(store.RETIREMENT_SIGNATURE_DOMAIN + payload)}
    retired = store.retirement_id(payloads)
    for name, data in payloads.items():
        history.write(history.directory / 'generations' / retired[7:] / name, data)
    history.write(history.directory / 'current', (retired + '\n').encode())
    assert selected(joined, tmp_path) == ()
    epoch, = joined.epochs.scan()
    prior = next(obj for obj in joined.inventory() if RootKind.RETIREMENT_PREDECESSOR in obj.roots)
    assert epoch.identity in prior.references


def test_changed_signed_snapshot_invalidates_complete_join(joined, history, monkeypatch):
    open_epoch(joined, history)
    original, reads = history.owner.inventory, []
    def read():
        reads.append(1)
        if len(reads) == 2:
            (history.directory / 'current').unlink()
        return original()
    monkeypatch.setattr(history.owner, 'inventory', read)
    with pytest.raises(RetentionError, match='epoch inventory changed'):
        joined.inventory()
