"""Crash and adversarial tests for fixed-root host journal storage."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from install import executor_birth_host_journal_posix as journal_posix
import executor_birth_account_identity as identity
import executor_birth_host_provisioning_journal as journal
from executor_birth_posix_metadata import snapshot_stat_v1


def _open_directory(path) -> int:
    return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def _store(path):
    descriptor = _open_directory(path)
    store = journal_posix.PosixJournalStoreV1((os.getuid(), os.getgid()))
    store._root_fd = descriptor
    return store, descriptor


def test_pending_bytes_conflict_is_preserved_fail_closed(tmp_path) -> None:
    store, descriptor = _store(tmp_path)
    pending = tmp_path / ".record-000.pending"
    pending.write_bytes(b"untrusted")
    pending.chmod(0o644)
    encoded = journal.encode_host_provisioning_record_v1(
        journal.plan_host_provisioning_v1(),
    )
    try:
        with pytest.raises(
            journal_posix.HostProvisioningPosixError,
            match="journal pending conflict",
        ):
            store.append_record(0, encoded)
        assert pending.read_bytes() == b"untrusted"
        assert not (tmp_path / "record-000.json").exists()
    finally:
        store._root_fd = None
        os.close(descriptor)


def test_partial_0600_stage_after_kill_is_rewritten_and_published(
    tmp_path, monkeypatch,
) -> None:
    store, descriptor = _store(tmp_path)
    encoded = journal.encode_host_provisioning_record_v1(
        journal.plan_host_provisioning_v1(),
    )
    original_write = journal_posix._write_all_v1

    def killed_write(target, payload):
        os.write(target, payload[:11])
        raise RuntimeError("simulated kill")

    monkeypatch.setattr(journal_posix.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(journal_posix, "_write_all_v1", killed_write)
    try:
        with pytest.raises(RuntimeError, match="simulated kill"):
            store.append_record(0, encoded)
        pending = tmp_path / ".record-000.pending"
        assert pending.read_bytes() == encoded[:11]
        assert pending.stat().st_mode & 0o777 == 0o600
        monkeypatch.setattr(journal_posix, "_write_all_v1", original_write)
        store.append_record(0, encoded)
        assert store.load_records() == (encoded,)
        assert not pending.exists()
        assert (tmp_path / "record-000.json").read_bytes() == encoded
    finally:
        store._root_fd = None
        os.close(descriptor)


def test_crash_after_link_before_unlink_resumes_without_rewrite(tmp_path) -> None:
    store, descriptor = _store(tmp_path)
    encoded = journal.encode_host_provisioning_record_v1(
        journal.plan_host_provisioning_v1(),
    )
    pending = tmp_path / ".record-000.pending"
    final = tmp_path / "record-000.json"
    pending.write_bytes(encoded)
    pending.chmod(0o644)
    os.link(pending, final)
    inode = final.stat().st_ino
    try:
        assert store.load_records() == (encoded,)
        assert not pending.exists()
        assert final.stat().st_ino == inode
        assert final.stat().st_nlink == 1
    finally:
        store._root_fd = None
        os.close(descriptor)


def test_lock_descriptor_is_closed_when_metadata_read_fails(monkeypatch) -> None:
    closed = []

    def fail_fstat(_descriptor):
        raise OSError("fstat failed")

    monkeypatch.setattr(journal_posix.os, "open", lambda *_args, **_kwargs: 47)
    monkeypatch.setattr(journal_posix.os, "fstat", fail_fstat)
    monkeypatch.setattr(journal_posix.os, "close", closed.append)
    with pytest.raises(OSError, match="fstat failed"):
        journal_posix.open_journal_lock_v1(12, (0, 0))
    assert closed == [47]


def test_lock_binding_is_rechecked_after_publication_name_changes(tmp_path) -> None:
    root = _open_directory(tmp_path)
    descriptor = journal_posix.open_journal_lock_v1(
        root, (os.getuid(), os.getgid()),
    )
    try:
        (tmp_path / "journal.lock").rename(tmp_path / "old.lock")
        (tmp_path / "journal.lock").write_bytes(b"")
        (tmp_path / "journal.lock").chmod(0o600)
        with pytest.raises(
            journal_posix.HostProvisioningPosixError,
            match="journal lock replaced",
        ):
            journal_posix.require_journal_lock_bound_v1(
                root, descriptor, (os.getuid(), os.getgid()),
            )
    finally:
        os.close(descriptor)
        os.close(root)


def test_append_rejects_invalid_existing_prefix_and_sequence_gap(tmp_path) -> None:
    store, descriptor = _store(tmp_path)
    planned = journal.plan_host_provisioning_v1()
    first = journal.encode_host_provisioning_record_v1(planned)
    record = identity.PosixAccountRecordV1(
        "metnos", 991, 992, "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    account = identity.PosixAccountSnapshotV1(record, (992,))
    second = journal.encode_host_provisioning_record_v1(
        journal.record_account_ready_v1(planned, account),
    )
    try:
        with pytest.raises(
            journal_posix.HostProvisioningPosixError,
            match="journal append sequence",
        ):
            store.append_record(1, second)
        (tmp_path / "record-000.json").write_bytes(b"{}")
        (tmp_path / "record-000.json").chmod(0o644)
        with pytest.raises(
            journal_posix.HostProvisioningPosixError,
            match="journal append chain",
        ):
            store.append_record(0, first)
    finally:
        store._root_fd = None
        os.close(descriptor)


def test_journal_inventory_stops_at_first_unadmitted_name(monkeypatch) -> None:
    visited = []

    class Entries:
        def __enter__(self):
            return iter(self)

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            visited.append("first")
            yield SimpleNamespace(name="unknown")
            visited.append("second")
            yield SimpleNamespace(name="record-000.json")

    store = journal_posix.PosixJournalStoreV1()
    store._root_fd = 7
    monkeypatch.setattr(journal_posix.os, "scandir", lambda _fd: Entries())
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="journal inventory",
    ):
        store.load_records()
    assert visited == ["first"]


@pytest.mark.parametrize(
    "field",
    ["st_mode", "st_nlink", "st_uid", "st_gid", "st_size",
     "st_mtime_ns", "st_ctime_ns"],
)
def test_stable_journal_snapshot_rejects_every_metadata_change(field) -> None:
    values = {
        "st_dev": 1, "st_ino": 2, "st_mode": 0o100644, "st_nlink": 1,
        "st_uid": 3, "st_gid": 4, "st_size": 5,
        "st_mtime_ns": 6, "st_ctime_ns": 7,
    }
    first = SimpleNamespace(**values)
    values[field] += 1
    second = SimpleNamespace(**values)
    assert snapshot_stat_v1(first) != snapshot_stat_v1(second)


def test_journal_read_rejects_a_stable_snapshot_change(tmp_path, monkeypatch) -> None:
    target = tmp_path / "record-000.json"
    target.write_bytes(b"record")
    target.chmod(0o644)
    root = _open_directory(tmp_path)
    real_fstat, calls = journal_posix.os.fstat, 0

    def changed_after_read(descriptor):
        nonlocal calls
        info = real_fstat(descriptor)
        calls += 1
        if calls != 2:
            return info
        values = {
            field: getattr(info, field)
            for field in (
                "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid",
                "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns",
            )
        }
        values["st_ctime_ns"] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(journal_posix.os, "fstat", changed_after_read)
    try:
        with pytest.raises(
            journal_posix.HostProvisioningPosixError,
            match="journal file changed",
        ):
            journal_posix._read_file_v1(
                root, target.name, (0o644,), (os.getuid(), os.getgid()),
            )
    finally:
        os.close(root)
