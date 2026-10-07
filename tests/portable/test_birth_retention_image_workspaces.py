"""Orphan census uses native image paths without assigning lifecycle closure."""
import os

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from test_birth_retention_artifacts import native

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX workspace custody')


def setup(native, tmp_path, monkeypatch):
    from image_index_build import ImageIndexBuild
    from install.birth_retention_image_workspaces import _ImageWorkspaceCensus
    from install.birth_retention_workspaces import _WorkspaceOwner

    root = tmp_path / 'indexes' / 'image'
    monkeypatch.setenv('METNOS_INDEX_ROOT', str(root.parent))
    workspace = ImageIndexBuild.workspace(str(tmp_path / 'images'), 'native-build')
    census = _ImageWorkspaceCensus(image_root=root, require_exclusion=native.jobs.require_exclusion,
                                  owner=native.jobs.owner)
    owner = _WorkspaceOwner(workloads=native.jobs, resolve_workspaces=lambda _: (),
                            discover_workspaces=census)
    return root, workspace, census, owner


@pytest.mark.parametrize('state', ['precontext', 'complete', 'removing', 'fence'])
def test_native_orphan_without_any_revision_stays_open(native, tmp_path, monkeypatch, state):
    from image_index_build import ImageIndexBuild

    root, workspace, census, owner = setup(native, tmp_path, monkeypatch)
    with workspace.use():
        if state != 'fence':
            if state == 'complete':
                ImageIndexBuild(str(tmp_path / 'images'), workspace.name)
            elif state == 'precontext':
                import image_index_build
                def crash(*args, **kwargs):
                    raise RuntimeError('before native context write')
                with monkeypatch.context() as patch:
                    patch.setattr(image_index_build, '_write_bytes', crash)
                    with pytest.raises(RuntimeError, match='native context'):
                        ImageIndexBuild(str(tmp_path / 'images'), workspace.name)
                assert not (workspace.parent / workspace.name / 'context.json').exists()
            else:
                # The native builder creates parts before context.json.
                (workspace.parent / workspace.name / 'parts').mkdir(parents=True, mode=0o700)
    if state == 'removing':
        with workspace.use(exclusive=True) as fd:
            workspace.retire(fd)
            workspace.detach()
    snapshot = census()
    assert snapshot.workspaces == frozenset({workspace})
    objects = owner.inventory()
    assert objects
    assert all(obj.state is NodeState.OPEN and obj.roots == (RootKind.OPEN_AUDIT,)
               and obj.eligible_after is None and not obj.references for obj in objects)
    for obj in objects:
        with pytest.raises(RetentionError):
            owner.delete(obj.identity, obj.version)
    assert census() == snapshot


def test_absent_root_is_read_only(native, tmp_path, monkeypatch):
    root, _, census, owner = setup(native, tmp_path, monkeypatch)
    assert not census().workspaces and owner.inventory() == ()
    assert not root.exists()


@pytest.mark.parametrize('fault', ['unknown_corpus', 'unknown_workspace', 'symlink', 'writable'])
def test_namespace_and_custody_refuse(native, tmp_path, monkeypatch, fault):
    root, workspace, census, owner = setup(native, tmp_path, monkeypatch)
    with workspace.use():
        (workspace.parent / workspace.name).mkdir(mode=0o700)
    if fault == 'unknown_corpus':
        (root / 'unknown').mkdir()
    elif fault == 'unknown_workspace':
        (workspace.parent / '.unexpected').mkdir()
    elif fault == 'symlink':
        (workspace.parent / workspace.name).rmdir()
        (workspace.parent / workspace.name).symlink_to(tmp_path, target_is_directory=True)
    else:
        workspace.parent.chmod(0o777)
    with pytest.raises(RetentionError):
        owner.inventory()


def test_new_orphan_between_discovery_reads_refuses(native, tmp_path, monkeypatch):
    root, workspace, census, owner = setup(native, tmp_path, monkeypatch)
    with workspace.use():
        (workspace.parent / workspace.name).mkdir(mode=0o700)
    calls = 0
    def changed():
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace.parent / 'another-native-build').mkdir(mode=0o700)
        return census()
    owner.discover_workspaces = changed
    with pytest.raises(RetentionError, match='workspace discovery drift'):
        owner.inventory()


def test_bound_workspace_is_not_duplicated(native, tmp_path, monkeypatch):
    _, workspace, census, owner = setup(native, tmp_path, monkeypatch)
    native.create('owner-a', artifact=False, closed=False)
    native.jobs.resolve_workspaces = lambda _: (workspace,)
    owner.resolve_workspaces = native.jobs.resolve_workspaces
    with workspace.use():
        (workspace.parent / workspace.name).mkdir(mode=0o700)
    objects = owner.inventory()
    assert len({obj.identity for obj in objects}) == len(objects)
    assert any(obj.roots == (RootKind.IN_PROGRESS_JOB,) for obj in objects)
