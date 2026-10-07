"""Native promoter transitions retain physical source and recovery material."""
import io
import gzip
import json
import os
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest
import contract_store as store
from jobs import promoter_state as native
from jobs.promoter_promote import _write_rollback_blob
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import plan
from install.birth_retention_reviews import _ReviewOwner
from install.birth_retention_promoter import _PromoterOwner, _PromoterBlobs, _PromoterInventory
from manifest_inventory import ContractId, ManifestOrigin
from test_birth_retention_synth import sample, FUTURE, RUN
from test_birth_retention_signed_store import history
from test_birth_retention_reviews import review

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX promoter custody')


@pytest.fixture
def joined(sample, history, tmp_path, monkeypatch):
    path = tmp_path / 'promoter.sqlite'
    monkeypatch.setenv('METNOS_PROMOTER_DB', str(path))
    monkeypatch.setattr(native, '_now_iso', lambda: '2020-01-01T00:00:00Z')
    source = sample.write(name='historical', expected_name='historical')
    proposal_id = source.stem
    native.insert_pending(proposal_id, 'historical')
    path.chmod(0o600)
    promoter = _PromoterOwner(path=path, require_exclusion=lambda: None, owner=None)
    blobs = _PromoterBlobs(root=tmp_path / 'promoter_blobs', promoter=promoter)
    reviews = _ReviewOwner(root=tmp_path / 'reviews', require_exclusion=lambda: None, owner=None)
    inventory = _PromoterInventory(promoter=promoter, blobs=blobs,
                                  synth=sample.owner, signed_store=history.owner, reviews=reviews)
    blob = blobs.root / (proposal_id + '.tar.gz')
    def promote(ids=False, bound=True):
        if bound:
            user_generation()
        _write_rollback_blob(history.gen, blob)
        native.upsert_promoted_grace(proposal_id=proposal_id, name='historical',
            blob_path=str(blob), verdict={}, practical_example='', grace_hours=0,
            prepromotion_generation_id=history.generation if ids else None,
            active_generation_id=history.generation if ids else None)
    def user_generation():
        contract = ContractId(ManifestOrigin.USER, 'historical/manifest.toml')
        directory = history.root / store.contract_storage_key(contract)
        history.write(directory / 'binding.json', store.encode_binding(contract))
        history.write(directory / 'writer.lock', b'\0')
        for name, data in history.payloads.items():
            history.write(directory / 'generations' / history.generation[7:] / name, data)
        history.write(directory / 'current', (history.generation + '\n').encode())
        return history.owner.generation_identity(contract, history.generation)
    return SimpleNamespace(**locals())


def row_object(joined):
    return next(obj for obj in joined.inventory.inventory() if obj.identity.owner == joined.promoter.name)


@pytest.mark.parametrize('state', ['pending', 'promoted_grace', 'promoted_finalized', 'rolled_back', 'archived', 'review_needed'])
def test_every_native_state_remains_reopenable_and_retains_failed_source(joined, state):
    with sqlite3.connect(joined.path) as conn:
        conn.execute('UPDATE proposal_promote SET state=?', (state,))
    obj = row_object(joined)
    assert obj.state is NodeState.OPEN
    assert joined.sample.owner.identity(joined.source.name) in obj.references
    with pytest.raises(RetentionError):
        joined.promoter.delete(obj.identity, obj.version)


def test_finalized_and_moved_rollback_preserve_native_rights(joined):
    joined.promote()
    obj = row_object(joined)
    assert joined.blobs.identity(joined.blob.name) in obj.references
    moved = joined.blobs.root / '_rolled_back' / joined.blob.name
    moved.parent.mkdir()
    joined.blob.rename(moved)
    # Native rollback has moved the tar but has not updated SQLite yet.
    assert joined.blobs.identity('_rolled_back/' + joined.blob.name) in row_object(joined).references
    native.mark_rolled_back(joined.proposal_id)
    assert native.resurrect_from_archive(joined.proposal_id)
    assert row_object(joined).state is NodeState.OPEN and moved.exists()


def test_exact_user_contract_generation_and_payload_are_required(joined):
    joined.promote(ids=True, bound=False)
    with pytest.raises(RetentionError, match='exact generation absent'):
        joined.inventory.inventory()  # Same digest under BUILTIN is insufficient.
    target = joined.user_generation()
    assert target in row_object(joined).references


def test_archive_generation_mismatch_blocks_inventory(joined):
    joined.promote(ids=True)
    joined.user_generation()
    with tarfile.open(joined.blob, 'w:gz') as archive:
        for name, data in joined.history.payloads.items():
            content = data + b'\n# changed\n' if name == 'manifest.toml' else data
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    with pytest.raises(RetentionError, match='exact generation absent'):
        joined.inventory.inventory()


@pytest.mark.parametrize('fault', ['missing_source', 'missing_blob', 'path', 'state', 'unknown_staging', 'duplicate_member', 'traversal'])
def test_gaps_or_unsafe_recovery_block_inventory(joined, fault):
    joined.promote()
    if fault == 'missing_source':
        joined.source.unlink()
    elif fault == 'missing_blob':
        joined.blob.unlink()
    elif fault in {'path', 'state'}:
        with sqlite3.connect(joined.path) as conn:
            if fault == 'path':
                conn.execute("UPDATE proposal_promote SET rollback_blob_path='/outside/rollback.tar.gz'")
            else:
                conn.execute("UPDATE proposal_promote SET state='unknown'")
    elif fault == 'unknown_staging':
        (joined.blobs.root / '.unfinished.tmp').write_bytes(b'partial')
    else:
        with tarfile.open(joined.blob, 'w:gz') as archive:
            for name in (['manifest.toml', 'manifest.toml'] if fault == 'duplicate_member' else ['../manifest.toml']):
                member = tarfile.TarInfo(name)
                member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(RetentionError):
        joined.inventory.inventory()


def test_orphan_blob_is_uncommitted_recovery_not_expired_garbage(joined, tmp_path):
    joined.promote()
    with sqlite3.connect(joined.path) as conn:
        conn.execute('DELETE FROM proposal_promote')
    objects = joined.inventory.inventory()
    blob = next(obj for obj in objects if obj.identity.owner == joined.blobs.name)
    assert blob.state is NodeState.OPEN and blob.roots
    source = joined.sample.owner.identity(joined.source.name)
    assert source in blob.references
    target = joined.history.owner.generation_identity(
        ContractId(ManifestOrigin.USER, 'historical/manifest.toml'), joined.history.generation)
    assert target in blob.references
    owners = frozenset(joined.inventory.owners)
    candidates = plan(objects, observed_owners=owners, required_owners=owners,
        observed_roots=frozenset(RootKind), holds=frozenset(), graph_path=tmp_path / 'graph.sqlite',
        run_id=RUN, observed_at=FUTURE)
    assert source not in {obj.identity for obj in candidates}
    with pytest.raises(RetentionError, match='lacks native closure'):
        joined.blobs.delete(blob.identity, blob.version)


def test_changed_row_invalidates_complete_join(joined, monkeypatch):
    original, reads = joined.promoter.scan, []
    def scan():
        reads.append(1)
        if len(reads) == 2:
            with sqlite3.connect(joined.path) as conn:
                conn.execute("UPDATE proposal_promote SET state='review_needed'")
        return original()
    monkeypatch.setattr(joined.promoter, 'scan', scan)
    with pytest.raises(RetentionError, match='inventory changed'):
        joined.inventory.inventory()


@pytest.mark.parametrize('fault', ['expanded_size', 'members'])
def test_archive_resource_limits_block_inventory(joined, fault):
    joined.promote()
    if fault == 'expanded_size':
        with gzip.open(joined.blob, 'wb') as stream:
            for _ in range(65):
                stream.write(b'\0' * (1 << 20))
    else:
        with tarfile.open(joined.blob, 'w:gz') as archive:
            for index in range(1001):
                archive.addfile(tarfile.TarInfo('manifest.toml' if index == 0 else str(index)))
    with pytest.raises(RetentionError, match='rollback archive'):
        joined.inventory.inventory()


def test_resurrected_promotion_retains_distinct_historical_blob(joined):
    joined.promote(ids=True)
    moved = joined.blobs.root / '_rolled_back' / joined.blob.name
    moved.parent.mkdir()
    joined.blob.rename(moved)
    native.mark_rolled_back(joined.proposal_id)
    assert native.resurrect_from_archive(joined.proposal_id)
    payloads = dict(joined.history.payloads)
    payloads['manifest.toml'] += b'\n# next revision\n'
    payloads['manifest.toml.sig'] = joined.history.author.sign(payloads['manifest.toml'])
    generation = store.generation_id(payloads)
    contract = ContractId(ManifestOrigin.USER, 'historical/manifest.toml')
    directory = joined.history.root / store.contract_storage_key(contract) / 'generations' / generation[7:]
    for name, payload in payloads.items():
        joined.history.write(directory / name, payload)
    _write_rollback_blob(directory, joined.blob)
    native.upsert_promoted_grace(proposal_id=joined.proposal_id, name='historical',
        blob_path=str(joined.blob), verdict={}, practical_example='', grace_hours=0,
        prepromotion_generation_id=generation, active_generation_id=joined.history.generation)
    objects = {obj.identity: obj for obj in joined.inventory.inventory()}
    old = joined.blobs.identity('_rolled_back/' + joined.blob.name)
    current = joined.blobs.identity(joined.blob.name)
    assert joined.history.owner.generation_identity(contract, generation) in objects[current].references
    assert joined.history.owner.generation_identity(contract, joined.history.generation) in objects[old].references
    # The current row must still bind the current blob, even if the archive
    # happens to match the row's claimed predecessor.
    with sqlite3.connect(joined.path) as conn:
        conn.execute('UPDATE proposal_promote SET prepromotion_generation_id=?',
                     (joined.history.generation,))
    with pytest.raises(RetentionError, match='generation mismatch'):
        joined.inventory.inventory()


def test_native_operator_promotion_uses_review_without_synthesis_log(joined, review, monkeypatch):
    from jobs import promoter_promote
    review.record['proposal'].update(contract_id='user:historical/manifest.toml', producer='promoter')
    review.record['name'] = 'historical'
    path = review.write()
    review_id = 'sha256:' + path.stem
    joined.source.unlink()
    with sqlite3.connect(joined.path) as conn:
        conn.execute('DELETE FROM proposal_promote')
    joined.user_generation()
    blob = joined.blobs.root / (path.stem + '.tar.gz')
    _write_rollback_blob(joined.history.gen, blob)
    def published(name, proposal_id, **kwargs):
        assert name == 'historical' and proposal_id == path.stem
        return dict(ok=True, blob_path=str(blob), prepromotion_generation_id=joined.history.generation,
                    active_generation_id=joined.history.generation)
    monkeypatch.setattr(promoter_promote, '_promote_candidate', published)
    contract = ContractId(ManifestOrigin.USER, 'historical/manifest.toml')
    result = promoter_promote.promote_reviewed_candidate(contract, review_id=review_id,
        reason='native retained review', approval_refs=(review.record['token'],), candidate=object())
    assert result['ok']
    obj = row_object(joined)
    assert review.owner.identity(path.name) in obj.references
    assert not any(ref.owner == joined.sample.owner.name for ref in obj.references)
    # A verdict is not a substitute for physical review custody.
    path.unlink()
    with pytest.raises(RetentionError, match='operator review absent or mismatched'):
        joined.inventory.inventory()


def test_legacy_unsigned_rollback_stays_open_without_invented_generation(joined):
    joined.promote()
    with tarfile.open(joined.blob, 'w:gz') as archive:
        payload = b'name="historical"\n'
        member = tarfile.TarInfo('manifest.toml')
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    objects = joined.inventory.inventory()
    blob = next(obj for obj in objects if obj.identity.owner == joined.blobs.name)
    assert blob.state is NodeState.OPEN
    assert all(target.node_type != 'generation' for target in blob.references)
