from __future__ import annotations

import io
import hashlib
import multiprocessing
import os
import signal
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty

import pytest

from durable_workloads.artifacts import (
    ArtifactConflictError,
    ArtifactContractError,
    ArtifactDownloadRegistry,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRepository,
    ArtifactSecurityError,
    ArtifactStore,
    Blob,
    RecoveryStatus,
    _owner_key,
    _target_key,
)
from durable_workloads.models import ArtifactState, PublicationState
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan


@dataclass
class ArtifactEnvironment:
    db_path: Path
    root: Path
    workload_store: DurableWorkloadStore
    repository: ArtifactRepository
    artifacts: ArtifactStore
    revisions: dict[str, tuple[str, str]]


def _create_revision(
    store: DurableWorkloadStore,
    owner: str,
    suffix: str,
) -> tuple[str, str]:
    draft = store.create_draft(
        owner,
        f"artifact-request-{suffix}",
        redacted_request={"summary": "synthetic artifact fixture"},
    )
    revision = store.admit_revision(
        owner,
        draft.workload_id,
        plan(),
        inventory(),
        expected_version=draft.version,
    )
    return draft.workload_id, revision.revision_id


@pytest.fixture
def artifact_environment(tmp_path: Path):
    db_path = tmp_path / "state" / "durable.sqlite3"
    workload_store = DurableWorkloadStore.open(db_path)
    revisions = {
        "owner-a": _create_revision(workload_store, "owner-a", "owner-a"),
        "owner-b": _create_revision(workload_store, "owner-b", "owner-b"),
    }
    repository = ArtifactRepository.open(db_path)
    root = tmp_path / "artifacts"
    artifacts = ArtifactStore(root, repository, max_blob_bytes=1024 * 1024)
    environment = ArtifactEnvironment(
        db_path,
        root,
        workload_store,
        repository,
        artifacts,
        revisions,
    )
    try:
        yield environment
    finally:
        repository.close()
        workload_store.close()


def _commit(
    environment: ArtifactEnvironment,
    owner: str,
    name: str,
    payload: bytes,
    *,
    artifact_id: str | None = None,
):
    workload_id, revision_id = environment.revisions[owner]
    return environment.artifacts.commit(
        owner,
        workload_id,
        revision_id,
        name,
        "application/octet-stream",
        "metnos.test-artifact/1",
        payload,
        artifact_id=artifact_id,
    )


def _blob_directory(root: Path, owner: str) -> Path:
    return root / "owners" / _owner_key(owner) / "blobs" / "sha256"


def _blob_path(root: Path, owner: str, digest: str) -> Path:
    return _blob_directory(root, owner) / digest[7:]


def _publication_directory(root: Path, owner: str, target: str) -> Path:
    return root / "owners" / _owner_key(owner) / "publications" / _target_key(owner, target)


def _crash_commit(
    db_path: str,
    root: str,
    workload_id: str,
    revision_id: str,
    checkpoint: str,
) -> None:
    repository = ArtifactRepository.open(db_path)

    def stop(name: str) -> None:
        if name == checkpoint:
            os.kill(os.getpid(), signal.SIGKILL)

    artifacts = ArtifactStore(root, repository, checkpoint=stop)
    artifacts.commit(
        "owner-a",
        workload_id,
        revision_id,
        "crash-report",
        "application/octet-stream",
        "metnos.test-artifact/1",
        b"complete-crash-payload",
        artifact_id="artifact_crash_01",
    )


def _recover_commit(
    db_path: str,
    root: str,
    workload_id: str,
    revision_id: str,
    output,
) -> None:
    try:
        repository = ArtifactRepository.open(db_path)
        artifacts = ArtifactStore(root, repository)
        artifact = artifacts.commit(
            "owner-a",
            workload_id,
            revision_id,
            "crash-report",
            "application/octet-stream",
            "metnos.test-artifact/1",
            b"complete-crash-payload",
            artifact_id="artifact_crash_01",
        )
        output.put((RecoveryStatus.COMMITTED.value, artifact.state.value))
        repository.close()
    except BaseException as exc:  # pragma: no cover - reported to the parent
        output.put(("error", type(exc).__name__))


def _crash_publication(
    db_path: str,
    root: str,
    artifact_id: str,
) -> None:
    repository = ArtifactRepository.open(db_path)

    def stop(name: str) -> None:
        if name == "publication_after_fsync":
            os.kill(os.getpid(), signal.SIGKILL)

    artifacts = ArtifactStore(root, repository, checkpoint=stop)
    artifacts.publish(
        "owner-a",
        artifact_id,
        "internal.crash-target",
        publication_id="publication_crash_01",
    )


def _recover_publication(
    db_path: str,
    root: str,
    output,
) -> None:
    try:
        repository = ArtifactRepository.open(db_path)
        artifacts = ArtifactStore(root, repository)
        recovery = artifacts.reconcile("owner-a", "publication_crash_01")
        output.put((recovery.status.value, recovery.publication.state.value))
        repository.close()
    except BaseException as exc:  # pragma: no cover - reported to the parent
        output.put(("error", type(exc).__name__))


def _run_process(context, target, args, expected_exit: int) -> None:
    process = context.Process(target=target, args=args)
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("artifact child process did not terminate")
    assert process.exitcode == expected_exit


def _queue_result(queue):
    try:
        return queue.get(timeout=10)
    except Empty:
        pytest.fail("artifact recovery process returned no result")


def test_download_capability_is_owner_bound_expiring_and_revocable(
    artifact_environment: ArtifactEnvironment,
):
    artifact = _commit(
        artifact_environment,
        "owner-a",
        "download-report",
        b"registered bytes",
        artifact_id="artifact_download_01",
    )
    registry = ArtifactDownloadRegistry()
    now = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    capability = registry.issue(
        "owner-a", artifact.artifact_id, lifetime=timedelta(seconds=30), now=now,
    )
    assert registry.resolve(
        capability.token, owner_user_id="owner-b", now=now,
    ) is None
    assert registry.resolve(
        capability.token, owner_user_id="owner-a", now=now,
    ) == capability
    assert registry.resolve(
        capability.token, owner_user_id="owner-a", now=now + timedelta(seconds=31),
    ) is None

    replacement = registry.issue("owner-a", artifact.artifact_id, now=now)
    assert registry.revoke(replacement.token)
    assert registry.resolve(replacement.token, owner_user_id="owner-a", now=now) is None


def test_download_opens_only_the_registered_owner_blob(
    artifact_environment: ArtifactEnvironment,
):
    artifact = _commit(
        artifact_environment,
        "owner-a",
        "download-open",
        b"registered bytes",
        artifact_id="artifact_download_02",
    )
    opened, stream = artifact_environment.artifacts.open_registered_download(
        "owner-a", artifact.artifact_id,
    )
    try:
        assert opened.artifact_id == artifact.artifact_id
        assert stream.read() == b"registered bytes"
    finally:
        stream.close()
    with pytest.raises(ArtifactNotFoundError):
        artifact_environment.artifacts.open_registered_download(
            "owner-b", artifact.artifact_id,
        )


def test_same_owner_deduplicates_content_and_replays_logical_commit(
    artifact_environment: ArtifactEnvironment,
):
    first = _commit(
        artifact_environment,
        "owner-a",
        "report-a",
        b"same bytes",
        artifact_id="artifact_same_01",
    )
    second = _commit(
        artifact_environment,
        "owner-a",
        "report-b",
        b"same bytes",
        artifact_id="artifact_same_02",
    )
    replay = _commit(
        artifact_environment,
        "owner-a",
        "report-a",
        b"same bytes",
        artifact_id="artifact_ignored_03",
    )

    assert first.digest == second.digest
    assert replay.artifact_id == first.artifact_id
    assert len(list(_blob_directory(
        artifact_environment.root,
        "owner-a",
    ).iterdir())) == 1
    assert artifact_environment.repository._connection.execute(
        "SELECT COUNT(*) FROM artifacts WHERE owner_user_id='owner-a'"
    ).fetchone()[0] == 2


def test_same_digest_is_physically_isolated_between_owners(
    artifact_environment: ArtifactEnvironment,
):
    first = _commit(artifact_environment, "owner-a", "report", b"shared")
    second = _commit(artifact_environment, "owner-b", "report", b"shared")

    assert first.digest == second.digest
    first_path = _blob_path(artifact_environment.root, "owner-a", first.digest)
    second_path = _blob_path(artifact_environment.root, "owner-b", second.digest)
    assert first_path != second_path
    assert first_path.read_bytes() == second_path.read_bytes() == b"shared"
    assert first_path.stat().st_ino != second_path.stat().st_ino
    with pytest.raises(ArtifactNotFoundError):
        artifact_environment.repository.get_artifact("owner-b", first.artifact_id)


def test_private_permissions_stream_input_and_no_path_input(
    artifact_environment: ArtifactEnvironment,
    tmp_path: Path,
):
    artifact = artifact_environment.artifacts.commit(
        "owner-a",
        *artifact_environment.revisions["owner-a"],
        "stream-report",
        "application/octet-stream",
        "metnos.test-artifact/1",
        io.BytesIO(b"from stream"),
    )
    owner_root = artifact_environment.root / "owners" / _owner_key("owner-a")
    assert stat.S_IMODE(owner_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(_blob_path(
        artifact_environment.root,
        "owner-a",
        artifact.digest,
    ).stat().st_mode) == 0o600

    source = tmp_path / "not-authorized-by-path.bin"
    source.write_bytes(b"path bytes")
    with pytest.raises(ArtifactContractError) as raised:
        artifact_environment.artifacts.stage("owner-a", source)
    assert raised.value.code == "artifact_payload_must_be_bytes_or_stream"


def test_invalid_metadata_is_rejected_before_any_blob_is_written(
    artifact_environment: ArtifactEnvironment,
):
    workload_id, revision_id = artifact_environment.revisions["owner-a"]
    with pytest.raises(ArtifactContractError):
        artifact_environment.artifacts.commit(
            "owner-a",
            workload_id,
            revision_id,
            "Report with spaces",
            "application/octet-stream",
            "metnos.test-artifact/1",
            b"must not be staged",
        )
    owner_path = artifact_environment.root / "owners" / _owner_key("owner-a")
    assert not owner_path.exists()


def test_existing_blob_is_reused_only_after_digest_and_size_verification(
    artifact_environment: ArtifactEnvironment,
):
    blob = artifact_environment.artifacts.stage("owner-a", b"original")
    path = _blob_path(artifact_environment.root, "owner-a", blob.digest)
    path.write_bytes(b"tampered")
    path.chmod(0o600)

    with pytest.raises(ArtifactIntegrityError):
        artifact_environment.artifacts.stage("owner-a", b"original")


@pytest.mark.parametrize("checkpoint", ["blob_after_fsync", "blob_after_install"])
@pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"),
    reason="controlled durable-crash tests require SIGKILL",
)
def test_real_process_recovers_blob_crashes_without_partial_final(
    artifact_environment: ArtifactEnvironment,
    checkpoint: str,
):
    artifact_environment.repository.close()
    artifact_environment.workload_store.close()
    workload_id, revision_id = artifact_environment.revisions["owner-a"]
    context = multiprocessing.get_context("spawn")
    _run_process(
        context,
        _crash_commit,
        (
            str(artifact_environment.db_path),
            str(artifact_environment.root),
            workload_id,
            revision_id,
            checkpoint,
        ),
        -signal.SIGKILL,
    )

    output = context.Queue()
    _run_process(
        context,
        _recover_commit,
        (
            str(artifact_environment.db_path),
            str(artifact_environment.root),
            workload_id,
            revision_id,
            output,
        ),
        0,
    )
    assert _queue_result(output) == ("committed", "committed")

    repository = ArtifactRepository.open(artifact_environment.db_path)
    try:
        artifact = repository.get_artifact("owner-a", "artifact_crash_01")
        final = _blob_path(artifact_environment.root, "owner-a", artifact.digest)
        assert final.read_bytes() == b"complete-crash-payload"
        assert stat.S_IMODE(final.stat().st_mode) == 0o600
    finally:
        repository.close()


@pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"),
    reason="controlled durable-crash tests require SIGKILL",
)
def test_real_process_reconciles_crash_during_publication(
    artifact_environment: ArtifactEnvironment,
):
    artifact = _commit(
        artifact_environment,
        "owner-a",
        "crash-publication",
        b"publication payload",
        artifact_id="artifact_publish_crash_01",
    )
    artifact_environment.repository.close()
    artifact_environment.workload_store.close()
    context = multiprocessing.get_context("spawn")
    _run_process(
        context,
        _crash_publication,
        (
            str(artifact_environment.db_path),
            str(artifact_environment.root),
            artifact.artifact_id,
        ),
        -signal.SIGKILL,
    )

    output = context.Queue()
    _run_process(
        context,
        _recover_publication,
        (
            str(artifact_environment.db_path),
            str(artifact_environment.root),
            output,
        ),
        0,
    )
    assert _queue_result(output) == ("committed", "published")
    final = _publication_directory(
        artifact_environment.root,
        "owner-a",
        "internal.crash-target",
    ) / "artifact"
    assert final.read_bytes() == b"publication payload"


def test_prepared_publication_without_files_is_retryable(
    artifact_environment: ArtifactEnvironment,
):
    artifact = _commit(artifact_environment, "owner-a", "retry", b"retry")
    publication = artifact_environment.repository.prepare_publication(
        "owner-a",
        artifact.artifact_id,
        "internal.retry",
        "metnos-internal://artifact/test",
        publication_id="publication_retry_01",
    )

    recovery = artifact_environment.artifacts.reconcile(
        "owner-a",
        publication.publication_id,
    )
    assert recovery.status is RecoveryStatus.RETRYABLE
    assert recovery.publication.state is PublicationState.PREPARED
    assert not _publication_directory(
        artifact_environment.root,
        "owner-a",
        "internal.retry",
    ).exists()


def test_final_digest_mismatch_never_overwrites_and_needs_attention(
    artifact_environment: ArtifactEnvironment,
):
    first = _commit(artifact_environment, "owner-a", "first", b"first final")
    second = _commit(artifact_environment, "owner-a", "second", b"second final")
    target = "internal.shared-target"
    published = artifact_environment.artifacts.publish(
        "owner-a",
        first.artifact_id,
        target,
        publication_id="publication_first_01",
    )
    collision = artifact_environment.artifacts.publish(
        "owner-a",
        second.artifact_id,
        target,
        publication_id="publication_second_01",
    )

    assert published.status is RecoveryStatus.COMMITTED
    assert collision.status is RecoveryStatus.NEEDS_ATTENTION
    assert collision.publication.state is PublicationState.NEEDS_ATTENTION
    assert collision.publication.observed_digest == first.digest
    final = _publication_directory(
        artifact_environment.root,
        "owner-a",
        target,
    ) / "artifact"
    assert final.read_bytes() == b"first final"
    assert artifact_environment.repository.get_artifact(
        "owner-a",
        second.artifact_id,
    ).state is ArtifactState.NEEDS_ATTENTION


def test_blob_symlink_is_rejected_without_following_it(
    artifact_environment: ArtifactEnvironment,
    tmp_path: Path,
):
    artifact_environment.artifacts.stage("owner-a", b"seed")
    digest = "sha256:" + hashlib.sha256(b"blocked").hexdigest()
    outside = tmp_path / "outside"
    outside.write_bytes(b"blocked")
    outside.chmod(0o600)
    os.symlink(outside, _blob_path(
        artifact_environment.root,
        "owner-a",
        digest,
    ))

    with pytest.raises(ArtifactSecurityError):
        artifact_environment.artifacts.stage("owner-a", b"blocked")
    assert outside.read_bytes() == b"blocked"


def test_missing_registered_blob_moves_publication_to_needs_attention(
    artifact_environment: ArtifactEnvironment,
):
    artifact = _commit(artifact_environment, "owner-a", "missing", b"gone")
    _blob_path(artifact_environment.root, "owner-a", artifact.digest).unlink()

    recovery = artifact_environment.artifacts.publish(
        "owner-a",
        artifact.artifact_id,
        "internal.missing",
        publication_id="publication_missing_01",
    )
    assert recovery.status is RecoveryStatus.NEEDS_ATTENTION
    assert recovery.reason_code == "artifact_integrity_failed"


def test_gc_preserves_active_and_recent_blobs_and_removes_old_orphans(
    artifact_environment: ArtifactEnvironment,
):
    active = _commit(artifact_environment, "owner-a", "active", b"active")
    old_orphan = artifact_environment.artifacts.stage("owner-a", b"old orphan")
    recent_orphan = artifact_environment.artifacts.stage("owner-a", b"recent orphan")
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    old_timestamp = (now - timedelta(hours=2)).timestamp()
    for digest in (active.digest, old_orphan.digest):
        os.utime(
            _blob_path(artifact_environment.root, "owner-a", digest),
            (old_timestamp, old_timestamp),
        )
    recent_timestamp = (now - timedelta(minutes=5)).timestamp()
    os.utime(
        _blob_path(artifact_environment.root, "owner-a", recent_orphan.digest),
        (recent_timestamp, recent_timestamp),
    )

    report = artifact_environment.artifacts.collect_garbage(
        "owner-a",
        grace_period=timedelta(hours=1),
        now=now,
    )
    assert report.deleted == 1
    assert report.referenced == 1
    assert report.recent == 1
    assert report.events == ("orphan_blob_deleted",)
    assert _blob_path(artifact_environment.root, "owner-a", active.digest).exists()
    assert not _blob_path(
        artifact_environment.root,
        "owner-a",
        old_orphan.digest,
    ).exists()
    assert _blob_path(
        artifact_environment.root,
        "owner-a",
        recent_orphan.digest,
    ).exists()


def test_gc_is_bounded_and_skips_symlinks(
    artifact_environment: ArtifactEnvironment,
    tmp_path: Path,
):
    blob = artifact_environment.artifacts.stage("owner-a", b"orphan")
    blob_dir = _blob_directory(artifact_environment.root, "owner-a")
    outside = tmp_path / "outside-gc"
    outside.write_bytes(b"outside")
    unsafe = blob_dir / ("f" * 64)
    os.symlink(outside, unsafe)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    os.utime(_blob_path(
        artifact_environment.root,
        "owner-a",
        blob.digest,
    ), (old.timestamp(), old.timestamp()))

    first = artifact_environment.artifacts.collect_garbage(
        "owner-a",
        grace_period=timedelta(days=1),
        batch_limit=1,
        log_limit=1,
    )
    assert first.scanned == 1
    assert first.more is True
    assert len(first.events) <= 1
    second = artifact_environment.artifacts.collect_garbage(
        "owner-a",
        grace_period=timedelta(days=1),
        batch_limit=10,
        log_limit=1,
    )
    assert second.unsafe == 1
    assert unsafe.is_symlink()
    assert outside.read_bytes() == b"outside"


def test_owner_deletion_is_explicit_idempotent_and_selective(
    artifact_environment: ArtifactEnvironment,
):
    first = _commit(artifact_environment, "owner-a", "report", b"owner a")
    second = _commit(artifact_environment, "owner-b", "report", b"owner b")
    artifact_environment.artifacts.publish(
        "owner-a",
        first.artifact_id,
        "internal.owner-a",
    )
    artifact_environment.artifacts.publish(
        "owner-b",
        second.artifact_id,
        "internal.owner-b",
    )

    deleted = artifact_environment.artifacts.delete_owner("owner-a")
    repeated = artifact_environment.artifacts.delete_owner("owner-a")
    assert deleted.database_rows == 1
    assert deleted.files >= 2
    assert repeated.database_rows == repeated.files == repeated.directories == 0
    assert not (artifact_environment.root / "owners" / _owner_key("owner-a")).exists()
    assert (artifact_environment.root / "owners" / _owner_key("owner-b")).is_dir()
    artifact_environment.artifacts.verify_blob(
        "owner-b",
        Blob(
            second.digest,
            second.size_bytes,
            second.blob_ref,
        ),
    )
    with pytest.raises(ArtifactNotFoundError):
        artifact_environment.repository.get_artifact("owner-a", first.artifact_id)
    assert artifact_environment.repository.get_artifact(
        "owner-b",
        second.artifact_id,
    ).artifact_id == second.artifact_id


def test_delete_refuses_unexpected_owner_entries(
    artifact_environment: ArtifactEnvironment,
):
    artifact = _commit(artifact_environment, "owner-a", "report", b"owner a")
    owner_root = artifact_environment.root / "owners" / _owner_key("owner-a")
    unexpected = owner_root / "unexpected"
    unexpected.write_text("do not delete", encoding="utf-8")
    unexpected.chmod(0o600)

    with pytest.raises(ArtifactSecurityError):
        artifact_environment.artifacts.delete_owner("owner-a")
    assert unexpected.read_text(encoding="utf-8") == "do not delete"
    with pytest.raises(ArtifactNotFoundError):
        artifact_environment.repository.get_artifact("owner-a", artifact.artifact_id)


def test_fsync_error_leaves_no_database_row_or_partial_blob(
    artifact_environment: ArtifactEnvironment,
    tmp_path: Path,
):
    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    artifacts = ArtifactStore(
        tmp_path / "fsync-artifacts",
        artifact_environment.repository,
        fsync=fail_fsync,
    )
    workload_id, revision_id = artifact_environment.revisions["owner-a"]
    with pytest.raises(OSError, match="synthetic fsync failure"):
        artifacts.commit(
            "owner-a",
            workload_id,
            revision_id,
            "fsync-report",
            "application/octet-stream",
            "metnos.test-artifact/1",
            b"not committed",
        )
    assert artifact_environment.repository._connection.execute(
        """
        SELECT COUNT(*) FROM artifacts
        WHERE owner_user_id='owner-a' AND logical_name='fsync-report'
        """
    ).fetchone()[0] == 0
    blob_dir = _blob_directory(tmp_path / "fsync-artifacts", "owner-a")
    assert list(blob_dir.iterdir()) == []


def test_logical_name_conflict_never_uses_last_writer_wins(
    artifact_environment: ArtifactEnvironment,
):
    first = _commit(artifact_environment, "owner-a", "stable", b"first")
    with pytest.raises(ArtifactConflictError):
        _commit(artifact_environment, "owner-a", "stable", b"second")
    stored = artifact_environment.repository.get_artifact("owner-a", first.artifact_id)
    assert stored.digest == first.digest
