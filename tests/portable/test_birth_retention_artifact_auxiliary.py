"""Native artifact copies and scratch; no installed F6 qualification."""
from dataclasses import replace
from datetime import timedelta
import os
import signal
import sqlite3
import sys

import pytest

from durable_workloads.artifacts import (
    ArtifactStore, RecoveryStatus, _owner_key, _publication_temp_name, _target_key,
)
from durable_workloads.coordinator import WorkerCapabilities
from durable_workloads.models import RunnerKind, WorkloadState
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_artifact_auxiliary import _ArtifactAuxiliaryOwner
from test_birth_retention_artifacts import OLD_TIME, PAYLOAD, RUN, collection, native


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX files and fences")


class Interrupted(Exception):
    pass


def interrupt_at(point):
    def checkpoint(name):
        if name == point:
            raise Interrupted(name)
    return checkpoint


def staging(native, *, user="owner-a", state="cancelled", detached=False):
    item = native.create(user, artifact=False, closed=False, mapped=state == "leased")
    workspace = native.artifacts.temporary_workspace(user, item.draft.workload_id)
    interrupted = ArtifactStore(native.root, native.repository,
                                checkpoint=interrupt_at("blob_before_install"))
    with pytest.raises(Interrupted):
        interrupted.stage(user, PAYLOAD, workspace=workspace)
    directory = workspace.parent / workspace.name
    path, = directory.iterdir()
    os.utime(path, (OLD_TIME.timestamp(),) * 2)
    current = native.store.get_workload(user, item.draft.workload_id)
    if state == "cancelled":
        # Pending notification intentionally remains; scratch is disposable
        # after the native terminal state, independently of delivery records.
        native.store.transition_workload(user, current.workload_id, WorkloadState.CANCELLED,
                                         expected_version=current.version, now=OLD_TIME)
    elif state in {"paused", "leased"}:
        current = native.store.transition_workload(user, current.workload_id, WorkloadState.QUEUED,
                                                  expected_version=current.version, now=OLD_TIME)
        if state == "paused":
            native.store.request_pause(user, current.workload_id, expected_version=current.version,
                                        idempotency_key="pause")
            native.store.settle_workload(user, current.workload_id)
        else:
            lease = native.store.claim_next("retention-test", OLD_TIME, timedelta(minutes=1),
                WorkerCapabilities.create(((RunnerKind.EXECUTOR, "read_files_ocr"),),
                    {key: 0 for key in ("cpu", "device", "llm", "local_io", "network_io", "vlm")}))
            assert lease is not None
    if detached:
        with workspace.use(exclusive=True) as fd:
            workspace.retire(fd)
            workspace.detach()
        path = workspace.parent / workspace.removing / path.name
    owner = _ArtifactAuxiliaryOwner(blobs=native.blobs, kind="temporary")
    identity = owner.identity(_owner_key(user), path.parent.name, path.name)
    return owner, path, identity, workspace


def published(native, *, user="owner-a", prepared=False):
    item = native.create(user)
    artifact, = native.repository.list_workload_artifacts(user, item.draft.workload_id)
    if prepared:
        interrupted = ArtifactStore(native.root, native.repository,
                                     checkpoint=interrupt_at("publication_before_install"))
        with pytest.raises(Interrupted):
            interrupted.publish(user, artifact.artifact_id, "target", publication_id="pub_test")
        name = _publication_temp_name("pub_test")
    else:
        result = native.artifacts.publish(user, artifact.artifact_id, "target", publication_id="pub_test")
        assert result.status is RecoveryStatus.COMMITTED
        name = "artifact"
    owner = _ArtifactAuxiliaryOwner(blobs=native.blobs, kind="publications")
    target = _target_key(user, "target")
    path = native.root / "owners" / _owner_key(user) / "publications" / target / name
    os.utime(path, (OLD_TIME.timestamp(),) * 2)
    return owner, path, owner.identity(_owner_key(user), target, name)


def orphan_copy(native, user="owner-a"):
    # A copy left without metadata uses the native directory layout and bytes.
    with native.artifacts._publication_directory(user, "unused", create=True) as directory:
        fd = os.open("artifact", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
        try:
            os.write(fd, PAYLOAD)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
    owner = _ArtifactAuxiliaryOwner(blobs=native.blobs, kind="publications")
    target = _target_key(user, "unused")
    path = native.root / "owners" / _owner_key(user) / "publications" / target / "artifact"
    os.utime(path, (OLD_TIME.timestamp(),) * 2)
    return owner, path, owner.identity(_owner_key(user), target, "artifact")


def observed(owner, identity):
    return next(obj for obj in owner.inventory() if obj.identity == identity)


@pytest.mark.parametrize("prepared", [False, True])
def test_native_publication_and_retry_copy_remain_referenced(native, prepared):
    owner, path, identity = published(native, prepared=prepared)
    objects = owner.inventory()
    obj, = (obj for obj in objects if obj.identity == identity)
    job, = (obj for obj in objects if obj.identity.owner == native.jobs.name)
    assert obj.roots == (RootKind.OPEN_AUDIT,) and job.references == (identity,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(identity, obj.version)
    assert path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("state", ["active", "paused", "leased"])
def test_unfinished_native_work_retains_staging(native, state):
    owner, path, identity, workspace = staging(native, state=state)
    obj = observed(owner, identity)
    assert obj.roots == (RootKind.IN_PROGRESS_JOB,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(identity, obj.version)
    with workspace.use():
        assert path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("detached", [False, True])
def test_terminal_scratch_cleanup_fences_stale_writers_and_preserves_other_owner(native, tmp_path, detached):
    owner, path, identity, workspace = staging(native, detached=detached)
    other, kept, _, _ = staging(native, user="owner-b", state="paused")
    candidate = observed(owner, identity)
    assert candidate.state is NodeState.CLOSED
    # Pending deliveries still retain their job, independently of its scratch.
    job = next(row for row in native.jobs.scan() if row.values["owner_user_id"] == "owner-a")
    assert native.jobs.state_of(job).state is NodeState.OPEN
    assert path.stat().st_blocks * 512 > 0
    owners = {owner.name: owner}
    root, public, factory = collection(tmp_path, (candidate,), owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not path.exists() and kept.read_bytes() == PAYLOAD
    assert native.jobs.version(job.identity) == job.version
    with pytest.raises(OSError, match="retired"):
        with workspace.use():
            pytest.fail("a stale writer may not recreate retired scratch")
    lock = owner.identity(_owner_key("owner-a"), "", ".lre-lock-" + workspace.name)
    fence = observed(owner, lock)
    assert fence.state is NodeState.OPEN and owner.version(lock) == fence.version
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(lock, fence.version)
    assert not (root / "active.json").exists()


def test_live_native_writer_prevents_retirement_even_for_terminal_job(native):
    owner, path, identity, workspace = staging(native)
    candidate = observed(owner, identity)
    with workspace.use():
        with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
            owner.delete(identity, candidate.version)
        assert path.read_bytes() == PAYLOAD
    owner.delete(identity, candidate.version)
    assert not path.exists()


def test_orphan_workspace_receives_native_retirement_fence(native):
    owner, path, identity, workspace = staging(native)
    lock = workspace.parent / (".lre-lock-" + workspace.name)
    lock.unlink()  # Historical workspace without a fence, no running writer.
    candidate = observed(owner, identity)
    owner.delete(identity, candidate.version)
    assert lock.read_bytes() == b"closed\n" and not path.exists()
    with pytest.raises(OSError, match="retired"):
        with workspace.use():
            pytest.fail("the missing fence must be installed before cleanup")


def test_new_publication_reference_blocks_original_candidate(native):
    owner, path, identity = orphan_copy(native)
    candidate = observed(owner, identity)
    item = native.create("owner-a")
    artifact, = native.repository.list_workload_artifacts("owner-a", item.draft.workload_id)
    result = native.artifacts.publish("owner-a", artifact.artifact_id, "unused")
    assert result.status is RecoveryStatus.COMMITTED
    assert owner.version(identity) == candidate.version  # File bytes did not change.
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(identity, candidate.version)
    assert path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("damage", ["missing", "content", "symlink", "hardlink", "mode", "unknown-file", "directory-link"])
def test_damaged_publication_refuses_complete_inventory(native, tmp_path, damage):
    owner, path, identity = published(native)
    outside = tmp_path / "original"
    outside.write_bytes(b"must survive")
    outside.chmod(0o600)
    if damage == "missing":
        path.unlink()
    elif damage == "content":
        path.write_bytes(b"altered")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(outside)
    elif damage == "hardlink":
        os.link(path, tmp_path / "alias")
    elif damage == "mode":
        path.chmod(0o644)
    elif damage == "unknown-file":
        (path.parent / "unknown").write_bytes(b"unknown")
    else:
        directory = path.parent
        moved = directory.with_name(directory.name + "-original")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RetentionError):
        owner.inventory()
    assert outside.read_bytes() == b"must survive"


@pytest.mark.parametrize("change", ["corrupt-fence", "foreign-identity", "replaced-file", "recent"])
def test_scratch_fence_identity_and_retention_window_are_rechecked(native, change):
    owner, path, identity, workspace = staging(native)
    candidate = observed(owner, identity)
    if change == "corrupt-fence":
        (workspace.parent / (".lre-lock-" + workspace.name)).write_bytes(b"unknown")
    elif change == "foreign-identity":
        identity = replace(identity, store="file:///elsewhere")
    elif change == "replaced-file":
        path.unlink()
        path.write_bytes(PAYLOAD)
        path.chmod(0o600)
        os.utime(path, (OLD_TIME.timestamp(),) * 2)
    else:
        os.utime(path, None)
        candidate = observed(owner, identity)
    with pytest.raises(RetentionError):
        owner.delete(identity, candidate.version)
    assert path.read_bytes() == PAYLOAD


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native SIGKILL and directory fsync")
@pytest.mark.parametrize("kind", ["publications", "temporary"])
@pytest.mark.parametrize("point", ["before_effect", "after_unlink", "after_effect", "after_outcome"])
def test_native_auxiliary_sigkill_resumes_original_receipt(native, tmp_path, monkeypatch, kind, point):
    if kind == "temporary":
        owner, path, identity, _ = staging(native)
    else:
        owner, path, identity = orphan_copy(native)
    candidate = observed(owner, identity)
    owners = {owner.name: owner}
    root, public, factory = collection(tmp_path, (candidate,), owners)
    journal = root / (RUN[7:] + ".sqlite")
    with sqlite3.connect(journal) as db:
        original = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    parent_identity = (path.parent.stat().st_dev, path.parent.stat().st_ino)
    fsync = os.fsync
    child = os.fork()
    if child == 0:
        try:
            if point == "after_unlink":
                def interrupt_sync(fd):
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) == parent_identity and not path.exists():
                        os.kill(os.getpid(), signal.SIGKILL)
                    return fsync(fd)
                os.fsync = interrupt_sync
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20,
                             crash=lambda stage: os.kill(os.getpid(), signal.SIGKILL)
                             if stage == point else None)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert path.exists() == (point == "before_effect")
    assert (root / "active.json").exists()
    synchronized = []
    def observe_sync(fd):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == parent_identity:
            synchronized.append(True)
        return fsync(fd)
    monkeypatch.setattr(os, "fsync", observe_sync)
    factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not path.exists() and synchronized
    assert not (root / "active.json").exists()
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
        assert db.execute("SELECT status FROM intents").fetchall() == [("deleted",)]
    assert owner.version(identity) is None
