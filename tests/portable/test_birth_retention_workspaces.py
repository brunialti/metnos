"""Native LRE scratch fixtures and signed maintenance; no runtime activation."""
from pathlib import Path
import json
import os

import pytest

from durable_workloads.models import WorkloadState
from durable_workloads.temporary_storage import TemporaryStorage, TemporaryWorkspace, reports
from executor_birth_retention import NodeState, RetentionError
from test_birth_retention_artifacts import OLD_TIME, collection, native

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX custody and fences")


def setup(native, tmp_path, *, active=False, detached=False):
    from install.birth_retention_workspaces import _WorkspaceOwner

    item = native.create("owner-a", artifact=False, closed=False)
    if not active:
        current = native.store.get_workload("owner-a", item.draft.workload_id)
        native.store.transition_workload("owner-a", current.workload_id, WorkloadState.CANCELLED,
                                         expected_version=current.version, now=OLD_TIME)
    workspace = TemporaryWorkspace(tmp_path / "builds", "generation")
    with workspace.use():
        directory = workspace.parent / workspace.name
        directory.mkdir(mode=0o700)
        (directory / "parts").mkdir(mode=0o700)
        path = directory / "parts" / "checkpoint"
        path.write_bytes(b"checkpoint native content")
        path.chmod(0o600)
    if detached:
        with workspace.use(exclusive=True) as fd:
            workspace.retire(fd)
            workspace.detach()
        path = workspace.parent / workspace.removing / "parts" / "checkpoint"
    os.utime(path, (OLD_TIME.timestamp(),) * 2)
    native.jobs.resolve_workspaces = lambda plan: (workspace,)
    owner = _WorkspaceOwner(workloads=native.jobs, resolve_workspaces=native.jobs.resolve_workspaces)
    return owner, workspace, path, item


def leaf(owner):
    return next(obj for obj in owner.inventory() if obj.identity.owner == owner.name
                and json.loads(obj.identity.local_id)[-1] == "file")


@pytest.mark.parametrize("detached", [False, True])
def test_native_terminal_leaf_then_empty_directories_and_permanent_fence(native, tmp_path, detached):
    owner, workspace, path, item = setup(native, tmp_path, detached=detached)
    obj = leaf(owner)
    assert obj.state is NodeState.CLOSED and obj.references
    job = next(obj for obj in owner.inventory() if obj.identity.owner == native.jobs.name)
    # Pending native notification preserves job, but not terminal scratch.
    assert job.state is NodeState.OPEN and not job.references
    owner.delete(obj.identity, obj.version)
    assert not path.exists() and owner.version(obj.identity) is None
    owner.delete(obj.identity, obj.version)
    # Existing policy is 90 days even for now-empty directory metadata.
    for directory in (path.parent, path.parent.parent):
        os.utime(directory, (OLD_TIME.timestamp(),) * 2)
        candidates = [obj for obj in owner.inventory() if obj.identity.owner == owner.name
                      and obj.state is NodeState.CLOSED]
        assert len(candidates) == 1
        owner.delete(candidates[0].identity, candidates[0].version)
    assert not path.parent.parent.exists()
    fence, = [obj for obj in owner.inventory() if obj.identity.owner == owner.name]
    assert fence.state is NodeState.OPEN and not fence.references
    with pytest.raises(RetentionError):
        owner.delete(fence.identity, fence.version)
    with pytest.raises(OSError, match="retired"):
        with workspace.use():
            pytest.fail("stale writer reopened retired workspace")


def test_pause_checkpoint_and_shared_cross_owner_are_preserved(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    second = native.create("owner-b", artifact=False, closed=False)
    current = native.store.get_workload("owner-b", second.draft.workload_id)
    queued = native.store.transition_workload("owner-b", current.workload_id, WorkloadState.QUEUED,
                                              expected_version=current.version, now=OLD_TIME)
    native.store.request_pause("owner-b", current.workload_id, expected_version=queued.version,
                              idempotency_key="pause")
    native.store.settle_workload("owner-b", current.workload_id)
    obj = leaf(owner)
    assert obj.state is NodeState.OPEN and len(obj.references) == 2
    jobs = [obj for obj in owner.inventory() if obj.identity.owner == native.jobs.name]
    assert sum(bool(obj.references) for obj in jobs) == 1
    with pytest.raises(RetentionError, match="active"):
        owner.delete(obj.identity, obj.version)
    with workspace.use():
        assert path.read_bytes() == b"checkpoint native content"


def test_writer_fence_blocks_terminal_cleanup(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    obj = leaf(owner)
    with workspace.use():
        with pytest.raises(RetentionError, match="writer"):
            owner.delete(obj.identity, obj.version)
    assert path.exists()


def test_runtime_handoff_keeps_dismissed_job_metadata_for_physical_owner(native, tmp_path):
    owner, workspace, path, item = setup(native, tmp_path)
    accepted = []

    def accept(owner_id, workload_id, version, paths):
        accepted.append((owner_id, workload_id, version, paths))

    manager = TemporaryStorage(
        native.store, lambda _plan: (workspace,), handoff_detached=accept,
    )
    manager.maintain()
    current = native.store.get_workload('owner-a', item.draft.workload_id)
    scratch = reports(native.store, 'owner-a', (current.workload_id,))[current.workload_id]
    assert scratch['status'] == 'clean' and scratch['can_dismiss']
    native.store.dismiss_terminal_workload(
        'owner-a', current.workload_id, expected_version=current.version,
        idempotency_key='dismiss-after-f6-handoff',
    )

    assert native.store.list_workloads('owner-a') == ()
    assert accepted == [('owner-a', current.workload_id, current.version, (workspace,))]
    detached = workspace.parent / workspace.removing / 'parts' / 'checkpoint'
    assert not path.exists() and detached.read_bytes() == b'checkpoint native content'
    inventory = owner.inventory()
    physical = [obj for obj in inventory if obj.identity.owner == owner.name
                and json.loads(obj.identity.local_id)[-1] == 'file']
    retained_job = next(obj for obj in inventory if obj.identity.owner == native.jobs.name)
    assert len(physical) == 1 and physical[0].state is NodeState.CLOSED
    assert retained_job.state is NodeState.OPEN


@pytest.mark.parametrize("change", ["same-content-replace", "content", "symlink", "hardlink"])
def test_replacement_and_unsafe_files_never_use_old_authorization(native, tmp_path, change):
    owner, _, path, _ = setup(native, tmp_path)
    obj = leaf(owner)
    content = path.read_bytes()
    if change == "same-content-replace":
        alternate = path.with_name("new")
        alternate.write_bytes(content); alternate.chmod(0o600)
        os.utime(alternate, (OLD_TIME.timestamp(),) * 2)
        alternate.replace(path)
    elif change == "content":
        path.write_bytes(b"replacement")
    elif change == "symlink":
        path.unlink(); path.symlink_to(tmp_path / "outside")
    else:
        os.link(path, path.with_name("another"))
    with pytest.raises(RetentionError):
        owner.delete(obj.identity, obj.version)
    assert path.exists() or path.is_symlink()


def test_signed_resume_after_unlink_and_before_receipt_commit(native, tmp_path, monkeypatch):
    owner, _, path, _ = setup(native, tmp_path)
    obj = leaf(owner)
    owners = {owner.name: owner, native.jobs.name: native.jobs}
    _, public, factory = collection(tmp_path, (obj,), owners)
    original = owner.delete
    def interrupted(identity, version):
        original(identity, version)
        raise RuntimeError("crash after durable unlink")
    monkeypatch.setattr(owner, "delete", interrupted)
    with pytest.raises(RuntimeError, match="crash"):
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert not path.exists()
    monkeypatch.setattr(owner, "delete", original)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0


def test_signed_resume_rejects_recreated_leaf(native, tmp_path, monkeypatch):
    owner, _, path, _ = setup(native, tmp_path)
    obj = leaf(owner)
    owners = {owner.name: owner, native.jobs.name: native.jobs}
    _, public, factory = collection(tmp_path, (obj,), owners)
    original = owner.delete
    def interrupted(identity, version):
        original(identity, version)
        path.write_bytes(b"new unauthorized payload"); path.chmod(0o600)
        raise RuntimeError("crash")
    monkeypatch.setattr(owner, "delete", interrupted)
    with pytest.raises(RuntimeError):
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    monkeypatch.setattr(owner, "delete", original)
    with pytest.raises(RetentionError, match="changed"):
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert path.read_bytes() == b"new unauthorized payload"


def test_unknown_neighbor_is_not_claimed_and_unbound_delete_denied(native, tmp_path):
    owner, workspace, _, _ = setup(native, tmp_path)
    unknown = TemporaryWorkspace(workspace.parent, "unknown")
    other = unknown.parent / unknown.name
    other.mkdir(mode=0o700)
    path = other / "payload"; path.write_bytes(b"unbound"); path.chmod(0o600)
    os.utime(path, (OLD_TIME.timestamp(),) * 2)
    identity = owner.identity(unknown, "unknown/payload", "file")
    assert identity not in {obj.identity for obj in owner.inventory()}
    with pytest.raises(RetentionError, match="unbound"):
        owner.delete(identity, owner.version(identity))
    assert path.exists()


def test_explicit_artifact_workspace_exclusion(native, tmp_path):
    from install.birth_retention_workspaces import _WorkspaceOwner

    owner, workspace, path, _ = setup(native, tmp_path)
    excluded = _WorkspaceOwner(workloads=native.jobs, resolve_workspaces=owner.resolve_workspaces,
                               excluded_workspaces=(workspace,))
    assert all(obj.identity.owner == native.jobs.name for obj in excluded.inventory())
    obj = leaf(owner)
    with pytest.raises(RetentionError, match="unbound"):
        excluded.delete(obj.identity, obj.version)
    assert path.exists()


def test_empty_directory_replacement_and_new_child_invalidate_cas(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    obj = leaf(owner); owner.delete(obj.identity, obj.version)
    os.utime(path.parent, (OLD_TIME.timestamp(),) * 2)
    obj = next(obj for obj in owner.inventory() if obj.identity.owner == owner.name
               and obj.state is NodeState.CLOSED)
    path.write_bytes(b"new child"); path.chmod(0o600)
    with pytest.raises(RetentionError):
        owner.delete(obj.identity, obj.version)
    assert path.exists()
    path.unlink(); path.parent.rmdir(); path.parent.mkdir(mode=0o700)
    os.utime(path.parent, (OLD_TIME.timestamp(),) * 2)
    with pytest.raises(RetentionError, match="version"):
        owner.delete(obj.identity, obj.version)
    assert path.parent.exists()


def test_fence_replacement_symlink_is_rejected(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    obj = leaf(owner)
    fence = workspace.parent / (".lre-lock-" + workspace.name)
    outside = tmp_path / "outside"; outside.write_bytes(b"must remain")
    fence.unlink(); fence.symlink_to(outside)
    with pytest.raises((RetentionError, OSError)):
        owner.delete(obj.identity, obj.version)
    assert path.exists() and outside.read_bytes() == b"must remain"


def test_inventory_rejects_metadata_drift(native, tmp_path, monkeypatch):
    owner, _, path, item = setup(native, tmp_path)
    original = owner._observe
    changed = False
    def observe(identity):
        nonlocal changed
        result = original(identity)
        if not changed:
            changed = True
            with native.store._transaction() as connection:
                connection.execute("UPDATE workloads SET version=version+1 WHERE id=?", (item.draft.workload_id,))
        return result
    monkeypatch.setattr(owner, "_observe", observe)
    with pytest.raises(RetentionError, match="metadata drift"):
        owner.inventory()
    assert path.exists()


def test_missing_parent_is_not_reported_as_durable_absence(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    obj = leaf(owner)
    owner.delete(obj.identity, obj.version)
    workspace.parent.rename(tmp_path / "moved-parent")
    with pytest.raises(RetentionError, match="parent missing"):
        owner.version(obj.identity)


def test_native_fence_mode_is_checked_during_inventory(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    (workspace.parent / (".lre-lock-" + workspace.name)).chmod(0o644)
    with pytest.raises(RetentionError, match="fence mode"):
        owner.inventory()
    assert path.exists()


def test_overlapping_capabilities_cannot_duplicate_physical_ownership(native, tmp_path):
    owner, workspace, path, _ = setup(native, tmp_path)
    nested = TemporaryWorkspace(workspace.parent / workspace.name, "parts")
    owner.resolve_workspaces = lambda plan: (workspace, nested)
    with pytest.raises(RetentionError, match="overlapping"):
        owner.inventory()
    assert path.exists()
