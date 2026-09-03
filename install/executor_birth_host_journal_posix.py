"""Linux append-only storage for the Executor Birth host journal."""
from __future__ import annotations

import os
import stat

import executor_birth_host_provisioning_journal as journal
from executor_birth_posix_metadata import snapshot_stat_v1


_LOCK_NAME = "journal.lock"
_RECORD_NAMES = tuple(f"record-{index:03d}.json" for index in range(4))
_PENDING_NAMES = tuple(f".record-{index:03d}.pending" for index in range(4))
_FILE_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)


class HostProvisioningPosixError(RuntimeError):
    pass


def raise_posix_v1(detail: str, cause: BaseException | None = None):
    error = HostProvisioningPosixError(detail)
    if cause is None:
        raise error
    raise error from cause


def _write_all_v1(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise_posix_v1("journal write")
        offset += written


def _read_file_v1(
    root_fd: int, name: str, modes: tuple[int, ...], owner: tuple[int, int],
):
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=root_fd)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink not in {1, 2}
            or (info.st_uid, info.st_gid) != owner
            or stat.S_IMODE(info.st_mode) not in modes
            or info.st_size > journal.MAX_HOST_PROVISIONING_RECORD_BYTES_V1
        ):
            raise_posix_v1("journal file metadata")
        chunks, remaining = [], info.st_size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != info.st_size:
            raise_posix_v1("journal file size")
        after = os.fstat(descriptor)
        if snapshot_stat_v1(info) != snapshot_stat_v1(after):
            raise_posix_v1("journal file changed")
        rebound = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if not os.path.samestat(after, rebound):
            raise_posix_v1("journal file replaced")
        return payload, info
    finally:
        os.close(descriptor)


class PosixJournalStoreV1:
    """Fixed-root, no-replace journal storage used only while locked."""

    def __init__(self, owner: tuple[int, int] = (0, 0)) -> None:
        if (
            type(owner) is not tuple or len(owner) != 2
            or any(type(item) is not int or item < 0 for item in owner)
        ):
            raise_posix_v1("journal owner")
        self._owner = owner
        self._root_fd: int | None = None

    def _fd(self) -> int:
        if self._root_fd is None:
            raise_posix_v1("journal lock absent")
        return self._root_fd

    def _inventory(self) -> set[str]:
        admitted = {_LOCK_NAME, *_RECORD_NAMES, *_PENDING_NAMES}
        names: set[str] = set()
        with os.scandir(self._fd()) as entries:
            for entry in entries:
                if entry.name not in admitted or len(names) >= len(admitted):
                    raise_posix_v1("journal inventory")
                names.add(entry.name)
        return names

    def _recover_linked_pending(self, sequence: int, names: set[str]) -> None:
        final, pending = _RECORD_NAMES[sequence], _PENDING_NAMES[sequence]
        if final not in names or pending not in names:
            return
        final_raw, final_info = _read_file_v1(
            self._fd(), final, (0o644,), self._owner,
        )
        pending_raw, pending_info = _read_file_v1(
            self._fd(), pending, (0o644,), self._owner,
        )
        if final_raw != pending_raw or not os.path.samestat(final_info, pending_info):
            raise_posix_v1("journal publication collision")
        os.unlink(pending, dir_fd=self._fd())
        os.fsync(self._fd())

    def _rewrite_staging(self, name: str, previous, encoded: bytes) -> None:
        flags = (
            os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(name, flags, dir_fd=self._fd())
        try:
            opened = os.fstat(descriptor)
            rebound = os.stat(name, dir_fd=self._fd(), follow_symlinks=False)
            if (
                not os.path.samestat(previous, opened)
                or not os.path.samestat(opened, rebound)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_uid, opened.st_gid) != self._owner
                or stat.S_IMODE(opened.st_mode) != 0o600
            ):
                raise_posix_v1("journal staging replaced")
            os.ftruncate(descriptor, 0)
            _write_all_v1(descriptor, encoded)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
            rebound = os.stat(name, dir_fd=self._fd(), follow_symlinks=False)
            if not os.path.samestat(opened, rebound):
                raise_posix_v1("journal staging replaced")
        finally:
            os.close(descriptor)

    def load_records(self) -> tuple[bytes, ...]:
        names = self._inventory()
        for sequence in range(4):
            self._recover_linked_pending(sequence, names)
        names = self._inventory()
        records = []
        for sequence, name in enumerate(_RECORD_NAMES):
            if name not in names:
                if any(item in names for item in _RECORD_NAMES[sequence + 1:]):
                    raise_posix_v1("journal gap")
                break
            raw, info = _read_file_v1(
                self._fd(), name, (0o644,), self._owner,
            )
            if info.st_nlink != 1:
                raise_posix_v1("journal hardlink")
            records.append(raw)
        pending = {name for name in names if name in _PENDING_NAMES}
        expected = {_PENDING_NAMES[len(records)]} if len(records) < 4 else set()
        if not pending <= expected:
            raise_posix_v1("journal pending state")
        return tuple(records)

    def _stage_record(self, sequence: int, encoded: bytes) -> None:
        pending = _PENDING_NAMES[sequence]
        if pending in self._inventory():
            raw, info = _read_file_v1(
                self._fd(), pending, (0o600, 0o644), self._owner,
            )
            if stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1:
                self._rewrite_staging(pending, info, encoded)
                return
            if info.st_nlink != 1 or raw != encoded:
                raise_posix_v1("journal pending conflict")
            return
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(pending, flags, 0o600, dir_fd=self._fd())
        try:
            os.fchown(descriptor, *self._owner)
            _write_all_v1(descriptor, encoded)
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
            rebound = os.stat(pending, dir_fd=self._fd(), follow_symlinks=False)
            if not os.path.samestat(os.fstat(descriptor), rebound):
                raise_posix_v1("journal staging replaced")
        finally:
            os.close(descriptor)

    def append_record(self, sequence: int, encoded: bytes) -> None:
        prefix, already_present = self._validated_append_v1(sequence, encoded)
        if already_present:
            return
        final, pending = _RECORD_NAMES[sequence], _PENDING_NAMES[sequence]
        self._stage_record(sequence, encoded)
        try:
            os.link(
                pending, final, src_dir_fd=self._fd(), dst_dir_fd=self._fd(),
                follow_symlinks=False,
            )
            os.fsync(self._fd())
            os.unlink(pending, dir_fd=self._fd())
            os.fsync(self._fd())
        except FileExistsError as exc:
            raise_posix_v1("journal publication collision", exc)
        if self.load_records() != prefix + (encoded,):
            raise_posix_v1("journal publication verification")

    def _validated_append_v1(
        self, sequence: int, encoded: bytes,
    ) -> tuple[tuple[bytes, ...], bool]:
        if type(sequence) is not int or not 0 <= sequence < len(_RECORD_NAMES):
            raise_posix_v1("journal append binding")
        prefix = self.load_records()
        try:
            if prefix:
                journal.decode_host_provisioning_chain_v1(prefix)
            record = journal.decode_host_provisioning_record_v1(encoded)
        except journal.HostProvisioningJournalError as exc:
            raise_posix_v1("journal append chain", exc)
        if record.sequence != sequence:
            raise_posix_v1("journal append binding")
        if sequence < len(prefix):
            if prefix[sequence] != encoded:
                raise_posix_v1("journal record conflict")
            return prefix, True
        if sequence != len(prefix):
            raise_posix_v1("journal append sequence")
        try:
            decoded = journal.decode_host_provisioning_chain_v1(prefix + (encoded,))
        except journal.HostProvisioningJournalError as exc:
            raise_posix_v1("journal append chain", exc)
        if decoded[-1].sequence != sequence:
            raise_posix_v1("journal append binding")
        return prefix, False


def require_journal_lock_bound_v1(
    root_fd: int, descriptor: int, owner: tuple[int, int],
) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or info.st_size != 0 or (info.st_uid, info.st_gid) != owner
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise_posix_v1("journal lock metadata")
    rebound = os.stat(_LOCK_NAME, dir_fd=root_fd, follow_symlinks=False)
    if not os.path.samestat(info, rebound):
        raise_posix_v1("journal lock replaced")


def open_journal_lock_v1(root_fd: int, owner: tuple[int, int]) -> int:
    flags = (
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(_LOCK_NAME, flags, 0o600, dir_fd=root_fd)
    try:
        require_journal_lock_bound_v1(root_fd, descriptor, owner)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


__all__ = [
    "HostProvisioningPosixError", "PosixJournalStoreV1",
    "open_journal_lock_v1", "raise_posix_v1",
    "require_journal_lock_bound_v1",
]
