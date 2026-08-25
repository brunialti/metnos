from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from executor_birth_authoring import (
    AuthoringInstallError,
    AuthoringInstallJournalV1,
    AuthoringVersionV1,
    authoring_token,
    authoring_paths,
    authoring_tree_id,
    advance_version,
    cleanup_transaction,
    decode_journal,
    decode_version,
    materialize_staging,
    persist_prepared_journal,
    read_authoring_versioned,
    read_manifest_ref_versioned,
    read_version,
    read_tree,
    replace_with_staging,
    rollback_prepared,
)


D = "sha256:" + "a" * 64
R = "sha256:" + "b" * 64


def _journal() -> AuthoringInstallJournalV1:
    suffix = R.removeprefix("sha256:")
    return AuthoringInstallJournalV1(
        request_id=R,
        contract_id="active:sample",
        source_origin="active",
        canonical_tree_id=D,
        old_tree_id=D,
        new_tree_id=D,
        candidate_id=D,
        semantic_core_id=D,
        admission_context_id=D,
        predecessor_generation_id=D,
        new_generation_id=D,
        staging_basename=f".birth-stage-{suffix}",
        backup_basename=f".birth-backup-{suffix}",
    )


def test_tree_identity_is_order_independent_and_binds_size_and_path() -> None:
    first = authoring_tree_id({"manifest.toml": b"x", "code/a.py": b"yy"})
    reordered = authoring_tree_id({"code/a.py": b"yy", "manifest.toml": b"x"})
    assert first == reordered
    assert first != authoring_tree_id({"manifest.toml": b"x", "code/b.py": b"yy"})
    assert first != authoring_tree_id({"manifest.toml": b"x", "code/a.py": b"y"})


def test_journal_and_version_require_exact_canonical_wire() -> None:
    journal = _journal()
    assert decode_journal(journal.encode()) == journal
    assert decode_journal(journal.encode()).journal_hash == journal.journal_hash
    with pytest.raises(AuthoringInstallError, match="authoring_journal_invalid"):
        decode_journal(journal.encode() + b"\n")

    version = AuthoringVersionV1("active:sample", 0, D)
    assert decode_version(version.encode()) == version
    with pytest.raises(AuthoringInstallError, match="authoring_version_invalid"):
        decode_version(b'{"contract_id":"active:sample","schema_version":1,"tree_id":"' + D.encode() + b'","version":false}')


def test_closed_tree_rejects_extra_and_hardlink(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "manifest.toml").write_bytes(b"m")
    assert dict(read_tree(root, ("manifest.toml",))) == {"manifest.toml": b"m"}
    (root / "extra").write_bytes(b"x")
    with pytest.raises(AuthoringInstallError, match="authoring_tree_invalid"):
        read_tree(root, ("manifest.toml",))


def test_writer_waits_for_readers_and_blocks_new_readers(tmp_path: Path) -> None:
    lock = tmp_path / "control" / "authoring.lock"
    reader_entered = threading.Event()
    release_reader = threading.Event()
    writer_entered = threading.Event()
    order: list[str] = []

    def reader() -> None:
        with authoring_token(lock, exclusive=False, timeout=2):
            order.append("reader")
            reader_entered.set()
            assert release_reader.wait(2)

    def writer() -> None:
        assert reader_entered.wait(2)
        with authoring_token(lock, exclusive=True, timeout=2):
            order.append("writer")
            writer_entered.set()

    first = threading.Thread(target=reader)
    second = threading.Thread(target=writer)
    first.start()
    second.start()
    assert reader_entered.wait(2)
    time.sleep(0.05)
    assert not writer_entered.is_set()
    release_reader.set()
    first.join(2)
    second.join(2)
    assert order == ["reader", "writer"]


def test_token_timeout_is_fail_closed(tmp_path: Path) -> None:
    lock = tmp_path / "control" / "authoring.lock"
    with authoring_token(lock, exclusive=True, timeout=1):
        with pytest.raises(AuthoringInstallError) as caught:
            with authoring_token(lock, exclusive=False, timeout=0.02):
                pass
    assert caught.value.code == "authoring_token_timeout"


def _transaction(tmp_path: Path, *, first: bool = False):
    canonical = tmp_path / "sample"
    old = {"manifest.toml": b"old", "manifest.toml.sig": b"old-sig"}
    if not first:
        canonical.mkdir()
        for name, payload in old.items():
            (canonical / name).write_bytes(payload)
    new = {"manifest.toml": b"new", "manifest.toml.sig": b"new-sig"}
    paths = authoring_paths(canonical, "active:sample")
    base = _journal()
    journal = AuthoringInstallJournalV1(
        request_id=base.request_id,
        contract_id=base.contract_id,
        source_origin=base.source_origin,
        canonical_tree_id=authoring_tree_id(old) if not first else authoring_tree_id(new),
        old_tree_id=None if first else authoring_tree_id(old),
        new_tree_id=authoring_tree_id(new),
        candidate_id=base.candidate_id,
        semantic_core_id=base.semantic_core_id,
        admission_context_id=base.admission_context_id,
        predecessor_generation_id=None if first else base.predecessor_generation_id,
        new_generation_id=base.new_generation_id,
        staging_basename=base.staging_basename,
        backup_basename=base.backup_basename,
    )
    return paths, journal, old, new


@pytest.mark.parametrize("crash_point", ["prepared", "old_renamed", "new_canonical"])
def test_old_pointer_recovery_restores_exact_old_tree(
    tmp_path: Path, crash_point: str,
) -> None:
    paths, journal, old, new = _transaction(tmp_path)
    materialize_staging(paths, journal, new)
    persist_prepared_journal(paths, journal)
    staging, backup = paths.transaction_paths(journal)
    if crash_point == "old_renamed":
        paths.canonical.replace(backup)
    elif crash_point == "new_canonical":
        replace_with_staging(paths, journal)
    rollback_prepared(paths, journal)
    assert dict(read_tree(paths.canonical, tuple(old))) == old
    assert not staging.exists()
    assert not backup.exists()
    assert not paths.journal.exists()


def test_first_birth_old_pointer_recovery_returns_to_absence(tmp_path: Path) -> None:
    paths, journal, _old, new = _transaction(tmp_path, first=True)
    materialize_staging(paths, journal, new)
    persist_prepared_journal(paths, journal)
    replace_with_staging(paths, journal)
    rollback_prepared(paths, journal)
    assert not paths.canonical.exists()


def test_new_pointer_completion_advances_version_and_cleans_backup(tmp_path: Path) -> None:
    paths, journal, _old, new = _transaction(tmp_path)
    materialize_staging(paths, journal, new)
    persist_prepared_journal(paths, journal)
    replace_with_staging(paths, journal)
    version = advance_version(paths, journal.contract_id, journal.new_tree_id)
    assert version.version == 0
    cleanup_transaction(paths, journal)
    observed = read_authoring_versioned(
        paths, journal.contract_id, tuple(new), timeout=1,
    )
    assert dict(observed) == new


def test_manifest_ref_reader_derives_and_checks_the_versioned_view(tmp_path: Path) -> None:
    paths, journal, _old, new = _transaction(tmp_path)
    materialize_staging(paths, journal, new)
    persist_prepared_journal(paths, journal)
    replace_with_staging(paths, journal)
    advance_version(paths, journal.contract_id, journal.new_tree_id)
    cleanup_transaction(paths, journal)
    ref = SimpleNamespace(
        manifest_dir=paths.canonical,
        contract_id=SimpleNamespace(value=journal.contract_id),
    )

    observed = read_manifest_ref_versioned(
        ref, tuple(new), timeout=1,
    )

    assert dict(observed) == new


def test_recovery_does_not_advance_an_already_durable_tree_version(tmp_path: Path) -> None:
    paths, journal, _old, new = _transaction(tmp_path)
    materialize_staging(paths, journal, new)
    persist_prepared_journal(paths, journal)
    replace_with_staging(paths, journal)

    first = advance_version(paths, journal.contract_id, journal.new_tree_id)
    replay = advance_version(paths, journal.contract_id, journal.new_tree_id)

    assert replay == first
    assert read_version(paths, journal.contract_id) == first


def test_recovery_rejects_foreign_canonical_tree(tmp_path: Path) -> None:
    paths, journal, _old, new = _transaction(tmp_path)
    materialize_staging(paths, journal, new)
    persist_prepared_journal(paths, journal)
    (paths.canonical / "manifest.toml").write_bytes(b"tampered")
    with pytest.raises(AuthoringInstallError) as caught:
        rollback_prepared(paths, journal)
    assert caught.value.code == "authoring_recovery_ambiguous"
