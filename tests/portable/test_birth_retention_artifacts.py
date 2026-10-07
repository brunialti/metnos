"""Native LRE metadata/blobs under maintenance; no installed F6 claim."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import signal
import sqlite3
import sys
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from tests.portable import durable_workload_fixtures as fixtures

from durable_workloads.artifacts import ArtifactRepository, ArtifactStore, _owner_key
from durable_workloads.coordinator import ValidatedResult, WorkerCapabilities
from durable_workloads.models import RunnerKind, WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.temporary_storage import TemporaryStorage
from durable_workloads.image_indexing import temporary_workspaces
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_artifacts import _ArtifactBlobOwner
from install.birth_retention_jobs_sqlite import _WorkloadOwner
from install.birth_retention_maintenance import Maintenance, plan


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX descriptors; separate Windows qualification required")
OLD = "2020-01-01T00:00:00Z"
OLD_TIME = datetime(2020, 1, 1, 0, 1, tzinfo=timezone.utc)
OBSERVED = "2026-10-05T12:00:00Z"
RUN = "sha256:" + "a" * 64
PAYLOAD = b"native archive content\n" * 4096


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setattr("durable_workloads.storage.utc_now", lambda: OLD)
    monkeypatch.setattr("durable_workloads.artifacts.utc_now", lambda: OLD)
    monkeypatch.setattr("durable_workloads.temporary_storage.utc_now", lambda: OLD)
    path, root = tmp_path / "workloads.sqlite", tmp_path / "artifacts"
    store = DurableWorkloadStore.open(path)
    repository = ArtifactRepository.open(path)
    artifacts = ArtifactStore(root, repository, max_blob_bytes=1024 * 1024)
    jobs = _WorkloadOwner(path=path, require_exclusion=lambda: None, owner=None,
                          resolve_workspaces=temporary_workspaces, artifact_workspace=artifacts.temporary_workspace)
    blobs = _ArtifactBlobOwner(root=root, workloads=jobs)

    def cleanup(user, workload_id):
        row = store._connection.execute("SELECT * FROM workloads WHERE owner_user_id=? AND id=?",
                                        (user, workload_id)).fetchone()
        TemporaryStorage(store, jobs.resolve_workspaces, artifact_workspace=artifacts.temporary_workspace).observe(row)

    def finish(user, workload_id, *, clean=True):
        current = store.get_workload(user, workload_id)
        if current.state is WorkloadState.RUNNING:
            assert store.evaluate_completion(user, workload_id, now=OLD_TIME).eligible
        else:
            store.transition_workload(user, workload_id, WorkloadState.CANCELLED,
                                      expected_version=current.version, now=OLD_TIME)
        for channel in ("owner_event", "telegram"):
            for row in store.claim_outbox(channel=channel, worker_id="retention-test", now=OLD_TIME):
                assert store.confirm_outbox(row, worker_id="retention-test", now=OLD_TIME)
        if clean:
            cleanup(user, workload_id)

    def create(user, *, closed=True, artifact=True, suffix="same-local-id", mapped=False):
        draft = store.create_draft(user, "request-" + suffix, redacted_request={}, workload_id=suffix)
        revision = store.admit_revision(user, draft.workload_id, fixtures.plan(with_map=mapped),
                                        fixtures.inventory([fixtures.source(0)] if mapped else []),
                                        expected_version=draft.version, usage_complete=mapped)
        if artifact:
            committed = artifacts.commit(user, draft.workload_id, revision.revision_id,
                                         "archive", "application/octet-stream", "metnos.test-artifact/1", PAYLOAD)
            blob_path = root / "owners" / _owner_key(user) / "blobs" / "sha256" / committed.digest[7:]
            os.utime(blob_path, (OLD_TIME.timestamp(),) * 2)
        else:
            blob_path = None
        if closed:
            finish(user, draft.workload_id)
        return SimpleNamespace(path=blob_path, draft=draft, revision=revision)

    def orphan(user="owner-a"):
        blob = artifacts.stage(user, PAYLOAD)
        blob_path = root / "owners" / _owner_key(user) / "blobs" / "sha256" / blob.digest[7:]
        os.utime(blob_path, (OLD_TIME.timestamp(),) * 2)
        return blob_path, blobs.identity(_owner_key(user), blob.digest[7:])

    yield SimpleNamespace(store=store, repository=repository, artifacts=artifacts, jobs=jobs,
                          blobs=blobs, root=root, create=create, finish=finish, cleanup=cleanup, orphan=orphan)
    repository.close()
    store.close()


def collection(tmp_path, candidates, owners):
    root = tmp_path / "maintenance"
    root.mkdir(mode=0o755)
    holds = root / "holds.json"
    holds.write_bytes(canonical({"schema_version": 1, "holds": []}))
    holds.chmod(0o600)
    private = Ed25519PrivateKey.generate()
    public = {"receipt-key": private.public_key()}
    def factory():
        return Maintenance(root, root_owned=False, require_exclusion=lambda: None)
    factory().begin(candidates, run_id=RUN, observed_at=OBSERVED, key_id="receipt-key",
                    private_key=private, public_keys=public, owners=owners)
    return root, public, factory


def test_native_job_then_blob_collection_preserves_same_digest_for_another_owner(native, tmp_path):
    first, other = native.create("owner-a"), native.create("owner-b")
    objects = native.blobs.inventory()
    jobs = tuple(obj for obj in objects if obj.identity.owner == native.jobs.name)
    blobs = tuple(obj for obj in objects if obj.identity.owner == native.blobs.name)
    assert len(jobs) == len(blobs) == 2
    assert all(obj.state is NodeState.CLOSED for obj in jobs)
    assert all(obj.state is NodeState.OPEN for obj in blobs)
    assert len({obj.identity for obj in blobs}) == 2
    assert {edge for obj in jobs for edge in obj.references} == {obj.identity for obj in blobs}
    # This is the explicit fixture subset, not the full installed owner/root census.
    owners = {native.jobs.name: native.jobs, native.blobs.name: native.blobs}
    candidates = plan(objects, observed_owners=frozenset(owners), required_owners=frozenset(owners),
                      observed_roots=frozenset(RootKind), holds=frozenset(),
                      graph_path=tmp_path / "projection.sqlite", run_id=RUN, observed_at=OBSERVED)
    assert {obj.identity for obj in candidates} == {obj.identity for obj in jobs}
    first_job = next(row for row in native.jobs.scan() if row.values["owner_user_id"] == "owner-a")
    native.jobs.delete(first_job.identity, first_job.version)
    assert first.path.read_bytes() == other.path.read_bytes() == PAYLOAD
    later = native.blobs.inventory()
    candidate, = (obj for obj in later if obj.identity.owner == native.blobs.name and obj.state is NodeState.CLOSED)
    assert candidate.identity.local_id != next(obj.identity.local_id for obj in blobs
                                              if obj.identity != candidate.identity)
    allocated_before = first.path.stat().st_blocks * 512
    root, public, factory = collection(tmp_path, (candidate,), owners)
    result = factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert result["completed"] == 1 and result["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert allocated_before > 0 and not first.path.exists()
    assert other.path.read_bytes() == PAYLOAD
    assert len(native.jobs.scan()) == 1
    assert not (root / "active.json").exists()


def test_active_work_protects_staged_unregistered_blob(native):
    native.create("owner-a", closed=False, artifact=False)
    path, identity = native.orphan()
    obj = next(obj for obj in native.blobs.inventory() if obj.identity == identity)
    assert obj.roots == (RootKind.IN_PROGRESS_JOB,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.blobs.delete(identity, obj.version)
    assert path.read_bytes() == PAYLOAD


def test_new_reference_blocks_deletion_even_for_terminal_job(native):
    path, identity = native.orphan()
    version = native.blobs.version(identity)
    draft = native.store.create_draft("owner-a", "later", redacted_request={})
    revision = native.store.admit_revision("owner-a", draft.workload_id, fixtures.plan(),
                                           fixtures.inventory(), expected_version=draft.version)
    native.artifacts.commit("owner-a", draft.workload_id, revision.revision_id, "later",
                            "application/octet-stream", "metnos.test-artifact/1", PAYLOAD)
    native.finish("owner-a", draft.workload_id)
    assert native.blobs.version(identity) == version
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.blobs.delete(identity, version)
    assert path.read_bytes() == PAYLOAD


def test_terminal_label_with_unfinished_native_delivery_retains_staged_blob(native):
    item = native.create("owner-a", closed=False, artifact=False)
    path, identity = native.orphan()
    current = native.store.get_workload("owner-a", item.draft.workload_id)
    native.store.transition_workload("owner-a", item.draft.workload_id, WorkloadState.CANCELLED,
                                     expected_version=current.version, now=OLD_TIME)
    obj = next(obj for obj in native.blobs.inventory() if obj.identity == identity)
    assert obj.roots == (RootKind.IN_PROGRESS_JOB,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.blobs.delete(identity, obj.version)
    assert path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("reference", ["native", "unknown", "missing"])
def test_native_result_blob_reference_is_not_lost_when_no_artifact_row_exists(native, reference):
    item = native.create("owner-a", artifact=False, closed=False, mapped=True)
    current = native.store.get_workload("owner-a", item.draft.workload_id)
    native.store.transition_workload("owner-a", item.draft.workload_id, WorkloadState.QUEUED,
                                     expected_version=current.version, now=OLD_TIME)
    lease = native.store.claim_next("retention-test", OLD_TIME, timedelta(minutes=1),
                                    WorkerCapabilities.create(((RunnerKind.EXECUTOR, "read_files_ocr"),),
                                    {key: 0 for key in ("cpu", "device", "llm", "local_io", "network_io", "vlm")}))
    assert lease is not None
    native.store.mark_running(lease, now=OLD_TIME)
    native.store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"ok": True}),
                               now=OLD_TIME)
    native.finish("owner-a", item.draft.workload_id)
    path, identity = native.orphan()
    blob_ref = "metnos-owner-blob/sha256:" + path.name
    if reference == "unknown":
        blob_ref = "unknown-backend/somewhere"
    elif reference == "missing":
        blob_ref = "metnos-owner-blob/sha256:" + "0" * 64
    # Native execution produces an actual result. Restore that same
    # row with the schema's blob field populated, exercising stored history
    # that current commit_result(payload) does not create. All schema/trigger
    # checks remain enabled; the transaction defers its existing FK references.
    connection = native.store._connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("PRAGMA defer_foreign_keys=ON")
        row = dict(connection.execute("SELECT * FROM results WHERE revision_id=?",
                                      (item.revision.revision_id,)).fetchone())
        connection.execute("DELETE FROM results WHERE owner_user_id=? AND id=?",
                           (row["owner_user_id"], row["id"]))
        row["blob_ref"] = blob_ref
        columns = ",".join(row)
        marks = ",".join("?" for _ in row)
        connection.execute(f"INSERT INTO results({columns}) VALUES({marks})", tuple(row.values()))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    if reference == "native":
        objects = native.blobs.inventory()
        job, = (obj for obj in objects if obj.identity.owner == native.jobs.name)
        blob, = (obj for obj in objects if obj.identity == identity)
        assert job.references == (identity,) and blob.state is NodeState.OPEN
    else:
        with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
            native.blobs.inventory()
    if reference != "missing":
        with pytest.raises(RetentionError):
            native.blobs.delete(identity, native.blobs.version(identity))
    assert path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("damage", ["missing", "content", "symlink", "hardlink", "mode", "unknown-file",
                                  "unknown-owner", "unknown-subtree", "unknown-root", "directory-link", "root-link"])
def test_damaged_or_unknown_native_archive_refuses_complete_inventory(native, tmp_path, damage):
    item = native.create("owner-a")
    outside = tmp_path / "outside"
    outside.write_bytes(b"must survive")
    outside.chmod(0o600)
    if damage == "missing":
        item.path.unlink()
    elif damage == "content":
        item.path.write_bytes(b"changed")
    elif damage == "symlink":
        item.path.unlink()
        item.path.symlink_to(outside)
    elif damage == "hardlink":
        os.link(item.path, tmp_path / "other-name")
    elif damage == "mode":
        item.path.chmod(0o644)
    elif damage == "unknown-file":
        (item.path.parent / "unexpected").write_bytes(b"not a native blob")
    elif damage == "unknown-owner":
        (native.root / "owners" / "unexpected").mkdir(mode=0o700)
    elif damage == "unknown-subtree":
        (item.path.parents[2] / "unexpected").mkdir(mode=0o700)
    elif damage == "unknown-root":
        (native.root / "unexpected").mkdir(mode=0o700)
    else:
        linked = native.root if damage == "root-link" else item.path.parent
        moved = linked.with_name(linked.name + "-original")
        linked.rename(moved)
        linked.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RetentionError):
        native.blobs.inventory()
    assert outside.read_bytes() == b"must survive"


def test_missing_native_metadata_is_not_an_empty_archive(native, tmp_path):
    native.orphan()
    absent = _WorkloadOwner(path=tmp_path / "absent.sqlite", require_exclusion=lambda: None, owner=None,
                            resolve_workspaces=temporary_workspaces)
    owner = _ArtifactBlobOwner(root=native.root, workloads=absent)
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        owner.inventory()
    assert not absent.path.exists()


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="requires native permission denial")
def test_unreadable_orphan_archive_is_not_reported_absent(native, tmp_path):
    native.orphan()
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    moved = parent / "artifacts"
    native.root.rename(moved)
    owner = _ArtifactBlobOwner(root=moved, workloads=native.jobs)
    parent.chmod(0o000)
    try:
        with pytest.raises(RetentionError, match="retention_owner_path_invalid"):
            owner.inventory()
    finally:
        parent.chmod(0o700)


@pytest.mark.parametrize("change", ["foreign-owner", "foreign-store", "identity-path", "version", "recent"])
def test_exact_identity_version_and_retention_window_are_required(native, change):
    path, identity = native.orphan()
    version = native.blobs.version(identity)
    if change == "foreign-owner":
        identity = replace(identity, owner="someone-else")
    elif change == "foreign-store":
        identity = replace(identity, store="file:///elsewhere")
    elif change == "identity-path":
        identity = replace(identity, local_id='["../escape","../escape"]')
    elif change == "version":
        version = "sha256:" + "0" * 64
    elif change == "recent":
        os.utime(path, None)
        version = native.blobs.version(identity)
    with pytest.raises(RetentionError):
        native.blobs.delete(identity, version)
    assert path.read_bytes() == PAYLOAD


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native SIGKILL and directory fsync")
@pytest.mark.parametrize("point", ["before_effect", "after_unlink", "after_effect", "after_outcome"])
def test_native_unlink_sigkill_resumes_original_signed_intent(native, tmp_path, monkeypatch, point):
    path, identity = native.orphan()
    candidate, = native.blobs.inventory()
    owners = {native.blobs.name: native.blobs}
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
    assert native.blobs.version(identity) is None
