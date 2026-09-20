"""Codec-free crash-recoverable POSIX append journal kernel."""
from __future__ import annotations

from dataclasses import dataclass
import os
import stat

from executor_birth_posix_metadata import snapshot_stat_v1
from install import executor_birth_posix_directory as posix_directory

_LOCK_NAME_V1 = "journal.lock"

class PosixAppendJournalError(RuntimeError):
    pass

def _fail(detail: str, cause: BaseException | None = None):
    error = PosixAppendJournalError(detail)
    if cause is None:
        raise error
    raise error from cause

@dataclass(frozen=True, slots=True)
class PosixAppendJournalLayoutV1:
    record_count: int
    maximum_record_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.record_count) is not int or not 1 <= self.record_count <= 999
            or type(self.maximum_record_bytes) is not int
            or self.maximum_record_bytes <= 0
        ):
            raise ValueError("append journal layout")

    @property
    def record_names(self) -> tuple[str, ...]:
        return tuple(f"record-{index:03d}.json" for index in range(self.record_count))

    @property
    def pending_names(self) -> tuple[str, ...]:
        return tuple(f".record-{index:03d}.pending" for index in range(self.record_count))

def _require_no_acl_v1(descriptor: int) -> None:
    try:
        posix_directory.require_no_acl_v1(descriptor)
    except posix_directory.PosixDirectoryError as exc:
        _fail("append journal ACL", exc)

def _require_platform_v1() -> None:
    try:
        posix_directory.require_posix_directory_platform_v1()
    except posix_directory.PosixDirectoryError as exc:
        _fail("append journal platform unsupported", exc)
    names = ("read", "write", "fsync", "fchmod", "fchown", "ftruncate")
    dir_fd = getattr(os, "supports_dir_fd", ())
    no_follow = getattr(os, "supports_follow_symlinks", ())
    link, unlink, scandir = (
        getattr(os, name, None) for name in ("link", "unlink", "scandir")
    )
    if (
        any(not callable(getattr(os, name, None)) for name in names)
        or not all(callable(item) for item in (link, unlink, scandir))
        or link not in dir_fd or unlink not in dir_fd or link not in no_follow
        or scandir not in getattr(os, "supports_fd", ())
    ):
        _fail("append journal platform unsupported")
def _rebound_v1(parent_fd, name, child_fd, detail="append journal file replaced"):
    try:
        return posix_directory.require_rebound_v1(parent_fd, name, child_fd)
    except posix_directory.PosixDirectoryError as exc:
        _fail(detail, exc)

def _snapshot_fd_v1(descriptor: int, detail: str):
    try:
        return snapshot_stat_v1(os.fstat(descriptor))
    except OSError as exc:
        _fail(detail, exc)
def _close_v1(*descriptors: int) -> None:
    try:
        posix_directory.close_descriptors_v1(descriptors)
    except posix_directory.PosixDirectoryError as exc:
        _fail("append journal close", exc)
def _write_all_v1(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as exc:
            _fail("append journal write", exc)
        if written <= 0:
            _fail("append journal write")
        offset += written
def _seal_stage_v1(descriptor: int, encoded: bytes) -> None:
    _write_all_v1(descriptor, encoded)
    os.fsync(descriptor)
    os.fchmod(descriptor, 0o644)
    os.fsync(descriptor)
def _require_unchanged_after_acl_v1(
    root_fd: int, name: str, descriptor: int, expected,
) -> None:
    _require_no_acl_v1(descriptor)
    after = _snapshot_fd_v1(descriptor, "append journal staging metadata")
    rebound = snapshot_stat_v1(_rebound_v1(
        root_fd, name, descriptor, "append journal staging replaced",
    ))
    if expected != after or after != rebound:
        _fail("append journal staging replaced")

def _read_file_v1(
    root_fd: int, name: str, modes: tuple[int, ...], owner: tuple[int, int],
    maximum: int,
) -> tuple[bytes, os.stat_result]:
    _require_platform_v1()
    try:
        before = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        descriptor = os.open(
            name, posix_directory.FILE_FLAGS_V1, dir_fd=root_fd,
        )
    except OSError as exc:
        _fail("append journal file open", exc)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(before.st_mode)
            or opened.st_nlink not in {1, 2}
            or (opened.st_uid, opened.st_gid) != owner
            or stat.S_IMODE(opened.st_mode) not in modes
            or opened.st_size > maximum
            or snapshot_stat_v1(before) != snapshot_stat_v1(opened)
        ):
            _fail("append journal file metadata")
        _require_no_acl_v1(descriptor)
        payload = _read_bounded_v1(descriptor, maximum)
        if len(payload) != opened.st_size:
            _fail("append journal file size")
        after = os.fstat(descriptor)
        rebound = _rebound_v1(root_fd, name, descriptor)
        if (
            snapshot_stat_v1(opened) != snapshot_stat_v1(after)
            or snapshot_stat_v1(after) != snapshot_stat_v1(rebound)
        ):
            _fail("append journal file replaced")
        return payload, after
    finally:
        _close_v1(descriptor)

def _read_bounded_v1(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        try:
            chunk = os.read(descriptor, remaining)
        except OSError as exc:
            _fail("append journal read", exc)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > maximum:
        _fail("append journal file size")
    return payload

class BoundPosixAppendJournalV1:
    """Descriptor-bound exact-byte journal; codecs remain in typed facades."""

    __slots__ = ("_bound", "_layout", "_owner", "_pid", "_root_fd")

    def __init__(
        self, root_fd: int, layout: PosixAppendJournalLayoutV1,
        owner: tuple[int, int],
    ) -> None:
        if (
            type(root_fd) is not int or root_fd < 0
            or type(layout) is not PosixAppendJournalLayoutV1
            or type(owner) is not tuple or len(owner) != 2
            or any(type(value) is not int or value < 0 for value in owner)
        ):
            _fail("append journal binding")
        self._root_fd, self._layout, self._owner = root_fd, layout, owner
        self._pid = os.getpid()
        _require_platform_v1()
        self._bound = _snapshot_fd_v1(root_fd, "append journal root").object_key
        self.assert_bound()
    def assert_bound(self) -> None:
        if os.getpid() != self._pid:
            _fail("append journal process changed")
        before = _snapshot_fd_v1(self._root_fd, "append journal root")
        if (
            before.object_key != self._bound
            or not stat.S_ISDIR(before.mode)
            or (before.uid, before.gid) != self._owner
            or stat.S_IMODE(before.mode) != 0o700
        ):
            _fail("append journal root")
        _require_no_acl_v1(self._root_fd)
        after = _snapshot_fd_v1(self._root_fd, "append journal root")
        if before != after:
            _fail("append journal root changed")
    def _inventory_v1(self) -> set[str]:
        self.assert_bound()
        admitted = {
            _LOCK_NAME_V1, *self._layout.record_names,
            *self._layout.pending_names,
        }
        names: set[str] = set()
        try:
            with os.scandir(self._root_fd) as entries:
                for entry in entries:
                    if entry.name not in admitted or len(names) >= len(admitted):
                        _fail("append journal inventory")
                    names.add(entry.name)
        except OSError as exc:
            _fail("append journal inventory", exc)
        self.assert_bound()
        return names
    def _recover_linked_v1(self, sequence: int, names: set[str]) -> None:
        final = self._layout.record_names[sequence]
        pending = self._layout.pending_names[sequence]
        if final not in names or pending not in names:
            return
        final_raw, final_info = self._read_v1(final, (0o644,))
        pending_raw, pending_info = self._read_v1(pending, (0o644,))
        if final_raw != pending_raw or not os.path.samestat(final_info, pending_info):
            _fail("append journal publication collision")
        self._unlink_pending_v1(pending)
    def _read_v1(self, name: str, modes: tuple[int, ...]):
        return _read_file_v1(
            self._root_fd, name, modes, self._owner,
            self._layout.maximum_record_bytes,
        )
    def read_prefix(self, *, recover: bool) -> tuple[bytes, ...]:
        names = self._inventory_v1()
        if recover:
            for sequence in range(self._layout.record_count):
                self._recover_linked_v1(sequence, names)
            names = self._inventory_v1()
        records: list[bytes] = []
        for sequence, name in enumerate(self._layout.record_names):
            if name not in names:
                if any(item in names for item in self._layout.record_names[sequence + 1:]):
                    _fail("append journal gap")
                break
            raw, info = self._read_v1(name, (0o644,))
            if info.st_nlink != 1:
                _fail("append journal hardlink")
            records.append(raw)
        self._require_pending_shape_v1(names, len(records), recover=recover)
        return tuple(records)
    def _require_pending_shape_v1(
        self, names: set[str], prefix_size: int, *, recover: bool,
    ) -> None:
        pending = {name for name in names if name in self._layout.pending_names}
        expected = (
            {self._layout.pending_names[prefix_size]}
            if recover and prefix_size < self._layout.record_count else set()
        )
        if not pending <= expected:
            _fail("append journal pending state")
    def append_exact(self, sequence: int, encoded: bytes) -> None:
        if (
            type(sequence) is not int
            or not 0 <= sequence < self._layout.record_count
            or type(encoded) is not bytes or not encoded
            or len(encoded) > self._layout.maximum_record_bytes
        ):
            _fail("append journal append binding")
        prefix = self.read_prefix(recover=True)
        if sequence < len(prefix):
            if prefix[sequence] != encoded:
                _fail("append journal record conflict")
            return
        if sequence != len(prefix):
            _fail("append journal append sequence")
        self._stage_v1(sequence, encoded)
        self._promote_v1(sequence)
        if self.read_prefix(recover=True) != prefix + (encoded,):
            _fail("append journal publication verification")
    def _stage_v1(self, sequence: int, encoded: bytes) -> None:
        self.assert_bound()
        name = self._layout.pending_names[sequence]
        if name in self._inventory_v1():
            raw, info = self._read_v1(name, (0o600, 0o644))
            if stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1:
                self._rewrite_v1(name, info, encoded)
                return
            if info.st_nlink != 1 or raw != encoded:
                _fail("append journal pending conflict")
            return
        self._create_stage_v1(name, encoded)
    def _create_stage_v1(self, name: str, encoded: bytes) -> None:
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=self._root_fd)
        except OSError as exc:
            _fail("append journal staging", exc)
        try:
            os.fchown(descriptor, *self._owner)
            _require_no_acl_v1(descriptor)
            _seal_stage_v1(descriptor, encoded)
            opened = os.fstat(descriptor)
            rebound = _rebound_v1(self._root_fd, name, descriptor)
            if (
                not os.path.samestat(opened, rebound)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_uid, opened.st_gid) != self._owner
                or stat.S_IMODE(opened.st_mode) != 0o644
                or opened.st_size != len(encoded)
            ):
                _fail("append journal staging replaced")
            _require_unchanged_after_acl_v1(
                self._root_fd, name, descriptor, snapshot_stat_v1(opened),
            )
        finally:
            _close_v1(descriptor)
        self.assert_bound()
    def _rewrite_v1(self, name: str, previous, encoded: bytes) -> None:
        flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=self._root_fd)
        except OSError as exc:
            _fail("append journal staging", exc)
        try:
            opened = os.fstat(descriptor)
            rebound = _rebound_v1(self._root_fd, name, descriptor)
            if (
                not os.path.samestat(previous, opened)
                or not os.path.samestat(opened, rebound)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_uid, opened.st_gid) != self._owner
                or stat.S_IMODE(opened.st_mode) != 0o600
            ):
                _fail("append journal staging replaced")
            os.ftruncate(descriptor, 0)
            _seal_stage_v1(descriptor, encoded)
            final = os.fstat(descriptor)
            rebound = _rebound_v1(self._root_fd, name, descriptor)
            if (
                not os.path.samestat(opened, final)
                or not os.path.samestat(final, rebound)
                or stat.S_IMODE(final.st_mode) != 0o644
                or final.st_size != len(encoded)
            ):
                _fail("append journal staging replaced")
            _require_unchanged_after_acl_v1(
                self._root_fd, name, descriptor, snapshot_stat_v1(final),
            )
        finally:
            _close_v1(descriptor)
        self.assert_bound()
    def _promote_v1(self, sequence: int) -> None:
        self.assert_bound()
        pending = self._layout.pending_names[sequence]
        final = self._layout.record_names[sequence]
        try:
            os.link(
                pending, final, src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd, follow_symlinks=False,
            )
            os.fsync(self._root_fd)
        except OSError as exc:
            _fail("append journal publication collision", exc)
        self._unlink_pending_v1(pending)
        self.assert_bound()
    def _unlink_pending_v1(self, pending: str) -> None:
        try:
            os.unlink(pending, dir_fd=self._root_fd)
            os.fsync(self._root_fd)
        except OSError as exc:
            _fail("append journal recovery", exc)

def require_journal_lock_bound_v1(
    root_fd: int, descriptor: int, owner: tuple[int, int],
) -> None:
    _require_platform_v1()
    try:
        posix_directory.require_lock_file_v1(
            root_fd, _LOCK_NAME_V1, descriptor, owner,
        )
    except posix_directory.PosixDirectoryError as exc:
        _fail("append journal " + str(exc), exc)

def open_journal_lock_v1(root_fd: int, owner: tuple[int, int]) -> int:
    _require_platform_v1()
    try:
        return posix_directory.open_lock_file_v1(
            root_fd, _LOCK_NAME_V1, owner,
        )
    except posix_directory.PosixDirectoryError as exc:
        _fail("append journal " + str(exc), exc)

__all__ = [
    "BoundPosixAppendJournalV1", "PosixAppendJournalError",
    "PosixAppendJournalLayoutV1", "open_journal_lock_v1",
    "require_journal_lock_bound_v1",
]
