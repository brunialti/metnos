"""Native cross-owner LRE composition; execution requires preregistration."""
import json
import os

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from durable_workloads.artifacts import ArtifactWorkspacePaths
from install.birth_retention_lre_inventory import _LreInventory
from test_birth_retention_artifacts import native
from test_birth_retention_workspaces import setup


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX LRE owners')


def test_readonly_resolver_observes_native_paths_without_creating_store(native, tmp_path):
    missing = tmp_path / 'missing-artifacts'
    resolver = ArtifactWorkspacePaths(missing)
    workspace = resolver.temporary_workspace('owner-a', 'workload-123')
    assert not missing.exists()
    assert workspace == ArtifactWorkspacePaths(missing).temporary_workspace('owner-a', 'workload-123')
    assert not missing.exists()
    native_resolver = ArtifactWorkspacePaths(native.root)
    native.jobs.artifact_workspace = native_resolver.temporary_workspace
    inventory = _LreInventory(workloads=native.jobs, artifact_root=native.root,
                              image_root=tmp_path / 'images')
    before = {p: p.read_bytes() for p in native.root.rglob('*') if p.is_file()}
    inventory.inventory()
    assert before == {p: p.read_bytes() for p in native.root.rglob('*') if p.is_file()}
    assert not missing.exists()


def test_readonly_resolver_selects_same_workspace_as_productive_store(native):
    readonly = ArtifactWorkspacePaths(native.root)
    assert readonly.temporary_workspace('owner-a', 'workload-123') == \
        native.artifacts.temporary_workspace('owner-a', 'workload-123')


def test_readonly_resolver_wrong_root_is_refused(native, tmp_path):
    native.jobs.artifact_workspace = ArtifactWorkspacePaths(tmp_path / 'wrong').temporary_workspace
    with pytest.raises(RetentionError, match='LRE artifact workspace root binding'):
        _LreInventory(workloads=native.jobs, artifact_root=native.root,
                      image_root=tmp_path / 'images')


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('detached', [False, True])
def test_native_scratch_and_metadata_share_owner_and_references(native, tmp_path, active, detached):
    _, _, path, _ = setup(native, tmp_path, active=active, detached=detached)
    inventory = _LreInventory(workloads=native.jobs, artifact_root=native.root,
                              image_root=tmp_path / 'images')
    objects = inventory.inventory()
    assert inventory.owners[native.jobs.name] is native.jobs
    assert set(inventory.owners) == {
        'durable_workloads', 'durable_artifact_blobs', 'durable_artifact_publications',
        'durable_artifact_temporary', 'durable_workspaces'}
    physical = next(obj for obj in objects if obj.identity.owner == 'durable_workspaces'
                    and json.loads(obj.identity.local_id)[-1] == 'file')
    job = next(obj for obj in objects if obj.identity.owner == native.jobs.name)
    assert physical.references == (job.identity,)
    assert (physical.state is NodeState.OPEN) == active
    assert (physical.identity in job.references) == active
    assert path.is_file()
    assert inventory.inventory() == objects


def test_unbound_image_workspace_is_preserved_in_composition(native, tmp_path):
    image_root = tmp_path / 'images'
    build = image_root / ('a' * 16) / 'unified' / '.builds' / ('b' * 64)
    build.mkdir(parents=True, mode=0o700)
    for directory in (image_root, *build.relative_to(image_root).parents):
        target = directory if directory == image_root else image_root / directory
        target.chmod(0o700)
    payload = build / 'checkpoint'
    payload.write_bytes(b'pre-context crash')
    payload.chmod(0o600)
    inventory = _LreInventory(workloads=native.jobs, artifact_root=native.root,
                              image_root=image_root)
    objects = inventory.inventory()
    leaf = next(obj for obj in objects if obj.identity.owner == 'durable_workspaces'
                and json.loads(obj.identity.local_id)[-1] == 'file')
    assert leaf.state is NodeState.OPEN
    assert leaf.roots == (RootKind.OPEN_AUDIT,)
    assert not leaf.references
    assert payload.read_bytes() == b'pre-context crash'


@pytest.mark.parametrize('resolver', ['resolve_workspaces', 'artifact_workspace'])
def test_resolver_change_rejected_even_when_both_inventories_are_empty(native, tmp_path, resolver):
    inventory = _LreInventory(workloads=native.jobs, artifact_root=native.root,
                              image_root=tmp_path / 'images')
    setattr(native.jobs, resolver, lambda *args: ())
    with pytest.raises(RetentionError, match='LRE workspace resolver changed'):
        inventory.inventory()


@pytest.mark.parametrize('detached', [False, True])
@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_composed_plan_recovers_signed_workspace_deletion(native, tmp_path, monkeypatch,
                                                         detached, boundary):
    from install.birth_retention_maintenance import plan
    from test_birth_retention_artifacts import OBSERVED, RUN, collection

    _, _, path, item = setup(native, tmp_path, detached=detached)
    inventory = _LreInventory(workloads=native.jobs, artifact_root=native.root,
                              image_root=tmp_path / 'images')
    objects = inventory.inventory()
    owners = inventory.owners
    candidates = plan(objects, observed_owners=frozenset(owners),
                      required_owners=frozenset(owners),
                      observed_roots=frozenset(RootKind), holds=frozenset(),
                      graph_path=tmp_path / 'projection.sqlite', run_id=RUN,
                      observed_at=OBSERVED)
    # This fixture explicitly supplies empty holds for its five-owner subset;
    # it makes no claim about installed F6 roots or owner coverage.
    assert len(candidates) == 1
    assert candidates[0].identity.owner == 'durable_workspaces'
    owner = owners['durable_workspaces']
    _, public, factory = collection(tmp_path, candidates, owners)
    original = owner.delete

    def interrupted(identity, version):
        if boundary == 'after':
            original(identity, version)
        raise RuntimeError('simulated interruption ' + boundary)

    monkeypatch.setattr(owner, 'delete', interrupted)
    with pytest.raises(RuntimeError, match='simulated interruption'):
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert path.exists() == (boundary == 'before')
    monkeypatch.setattr(owner, 'delete', original)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    assert not path.exists()
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)['remaining'] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert native.store.get_workload('owner-a', item.draft.workload_id) is not None
    assert inventory.inventory()


def test_native_artifact_root_mismatch_refused_with_empty_inventory(native, tmp_path):
    with pytest.raises(RetentionError, match='LRE artifact workspace root binding'):
        _LreInventory(workloads=native.jobs, artifact_root=tmp_path / 'other-artifacts',
                      image_root=tmp_path / 'images')


def test_foreign_artifact_resolver_refused_with_empty_inventory(native, tmp_path):
    native.jobs.artifact_workspace = lambda *args: ()
    with pytest.raises(RetentionError, match='LRE artifact workspace root binding'):
        _LreInventory(workloads=native.jobs, artifact_root=native.root,
                      image_root=tmp_path / 'images')


def test_native_artifact_root_change_refused_with_empty_inventory(native, tmp_path):
    inventory = _LreInventory(workloads=native.jobs, artifact_root=native.root,
                              image_root=tmp_path / 'images')
    native.jobs.artifact_workspace.__self__._root = tmp_path / 'other-artifacts'
    with pytest.raises(RetentionError, match='LRE artifact workspace root binding'):
        inventory.inventory()
