"""Actual promoter archive move/copy and complete source projections."""
import os
from pathlib import Path

import pytest
import change_intents
from jobs import promoter as writer
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_promoter import _PromoterInventory
from install.birth_retention_inventory import _InventoryComposition
from install.birth_retention_synth_archive import _SynthArchiveOwner, _SynthSourcesInventory
from test_birth_retention_promoter import joined, sample, history
from test_birth_retention_synth import selected
from test_birth_retention_artifacts import collection


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX archive file custody')


@pytest.fixture
def archive(sample, tmp_path, monkeypatch):
    root = tmp_path / 'synth_archive'
    monkeypatch.setattr(writer, '_archive_dir', lambda: root)
    owner = _SynthArchiveOwner(root=root, intents=sample.intents)
    combined = _SynthSourcesInventory(synth=sample.owner, archive=owner)
    return owner, combined


@pytest.mark.parametrize('copy_crash', [False, True])
def test_native_move_or_copy_crash_keeps_all_physical_references(sample, archive, monkeypatch, copy_crash):
    owner, combined = archive
    source = sample.write()
    sample.intent()
    if copy_crash:
        replace, unlink = os.replace, Path.unlink
        def failed_replace(src, dst):
            if src == str(source):
                raise OSError('cross-device')
            return replace(src, dst)
        def failed_unlink(path, *args, **kwargs):
            if path == source:
                raise OSError('interrupted after copy')
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(os, 'replace', failed_replace)
        monkeypatch.setattr(Path, 'unlink', failed_unlink)
    result = writer._archive_proposal_json(source.stem, source)
    assert (result is None) == copy_crash
    objects = combined.inventory()
    files = [obj for obj in objects if obj.identity.owner in combined.proposal_owner_names]
    intent = next(obj for obj in objects if obj.identity.owner == sample.intents.name)
    assert len(files) == (2 if copy_crash else 1)
    assert set(intent.references) == {obj.identity for obj in files}
    assert all(intent.identity in obj.references for obj in files)
    if copy_crash:
        assert all(any(ref.owner in combined.proposal_owner_names for ref in obj.references) for obj in files)


def test_archive_only_retains_rolled_back_intent_witness_then_collects(sample, archive, tmp_path):
    owner, combined = archive
    source = sample.write(); sample.intent(change_intents.STATE_ROLLED_BACK)
    assert writer._archive_proposal_json(source.stem, source)
    sample.owner = combined
    objects = combined.inventory()
    intent = next(obj for obj in objects if obj.identity.owner == sample.intents.name)
    assert intent.state is NodeState.OPEN
    chosen = selected(sample, tmp_path)
    assert len(chosen) == 1 and chosen[0].identity.owner == owner.name
    _, public, factory = collection(tmp_path, chosen, combined.owners)
    factory().resume(combined.owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(combined.owners, public_keys=public, verify_recovery=lambda: None)
    assert len(owner.scan()) == 0  # Native empty archive directory is harmless.
    assert next(iter(combined.inventory())).state is NodeState.CLOSED


@pytest.mark.parametrize('state', ['synthesized', 'installed', 'in_progress'])
def test_native_archive_location_never_closes_live_synthesis(sample, archive, state):
    owner, combined = archive
    source = sample.write(state=state)
    assert writer._archive_proposal_json(source.stem, source)
    obj, = combined.inventory()
    assert obj.state is NodeState.OPEN and obj.roots
    with pytest.raises(RetentionError, match='retention_owner_state_invalid'):
        owner.delete(obj.identity, obj.version)



@pytest.mark.parametrize('archived_state,original_state', [('installed', 'abandoned'), ('abandoned', 'installed')])
def test_divergent_physical_copies_keep_closed_copy_when_other_remains_open(sample, archive, tmp_path, archived_state, original_state):
    owner, combined = archive
    source = sample.write(state=archived_state)
    assert writer._archive_proposal_json(source.stem, source)
    sample.write(state=original_state)
    sample.owner = combined
    objects = combined.inventory()
    assert len(objects) == 2
    assert {obj.state for obj in objects} == {NodeState.OPEN, NodeState.CLOSED}
    assert all(len(obj.references) == 1 for obj in objects)
    assert selected(sample, tmp_path) == ()


def test_promoter_exact_source_survives_native_archive_move(joined, monkeypatch, tmp_path):
    root = tmp_path / 'synth_archive'; monkeypatch.setattr(writer, '_archive_dir', lambda: root)
    archive = _SynthArchiveOwner(root=root, intents=joined.sample.intents)
    sources = _SynthSourcesInventory(synth=joined.sample.owner, archive=archive)
    assert writer._archive_proposal_json(joined.proposal_id, joined.source)
    inventory = _PromoterInventory(promoter=joined.promoter, blobs=joined.blobs, synth=sources,
        signed_store=joined.history.owner, reviews=joined.reviews)
    objects = inventory.inventory()
    promoter = next(obj for obj in objects if obj.identity.owner == joined.promoter.name)
    target = archive.identity(joined.proposal_id + '/' + joined.source.name)
    assert target in promoter.references and promoter.state is NodeState.OPEN
    assert target in {obj.identity for obj in objects}
    composed = _InventoryComposition(components=(sources, inventory),
        required_owners=frozenset(inventory.owners), require_exclusion=lambda: None)
    assert composed.inventory() == objects


@pytest.mark.parametrize('fault', ['wrong_parent', 'unknown_file', 'nested', 'symlink', 'partial'])
def test_unknown_archive_namespace_or_record_fails_closed(sample, archive, fault):
    owner, combined = archive
    source = sample.write()
    destination = Path(writer._archive_proposal_json(source.stem, source))
    if fault == 'wrong_parent':
        destination.parent.rename(destination.parent.with_name('different'))
    elif fault == 'unknown_file':
        (destination.parent / 'extra.txt').write_text('unknown')
    elif fault == 'nested':
        (destination.parent / 'nested').mkdir()
    elif fault == 'symlink':
        destination.unlink(); destination.symlink_to(source)
    else:
        destination.write_text('{')
    with pytest.raises(RetentionError):
        combined.inventory()


def test_changed_archive_during_projection_fails_closed(sample, archive, monkeypatch):
    owner, combined = archive
    source = sample.write(); destination = Path(writer._archive_proposal_json(source.stem, source))
    original = sample.owner._project
    def changed(files, rows):
        result = original(files, rows)
        destination.write_bytes(destination.read_bytes() + b' ')
        return result
    monkeypatch.setattr(sample.owner, '_project', changed)
    with pytest.raises(RetentionError, match='retention_owner_changed'):
        combined.inventory()
