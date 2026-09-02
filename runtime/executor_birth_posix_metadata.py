"""Typed, read-only POSIX metadata snapshots for Executor Birth.

The module performs no observation at import time.  It names the two metadata
comparisons already used by the installer: object identity and stable metadata
excluding only timestamps.  Domain adapters retain their existing error
translation and filesystem policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class PosixObjectKeyV1:
    """Kernel identity of one filesystem object."""

    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class PosixStableMetadataV1:
    """Metadata whose equality deliberately excludes timestamps."""

    object_key: PosixObjectKeyV1
    mode: int
    link_count: int
    uid: int
    gid: int
    size: int


@dataclass(frozen=True, slots=True)
class PosixStatSnapshotV1:
    """Complete stat observation used by the current RM-0008 consumers."""

    device: int
    inode: int
    mode: int
    link_count: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @property
    def object_key(self) -> PosixObjectKeyV1:
        return PosixObjectKeyV1(self.device, self.inode)

    @property
    def stable_metadata(self) -> PosixStableMetadataV1:
        return PosixStableMetadataV1(
            self.object_key, self.mode, self.link_count,
            self.uid, self.gid, self.size,
        )

    def same_object_as(self, current: object) -> bool:
        return (
            type(current) is PosixStatSnapshotV1
            and self.object_key == current.object_key
        )

    def same_stable_metadata_as(self, current: object) -> bool:
        return (
            type(current) is PosixStatSnapshotV1
            and self.stable_metadata == current.stable_metadata
        )


def snapshot_stat_v1(info: os.stat_result) -> PosixStatSnapshotV1:
    """Translate one already-observed stat result without adding policy."""
    return PosixStatSnapshotV1(
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def snapshot_fd_v1(descriptor: int) -> PosixStatSnapshotV1:
    """Observe one open descriptor; raw platform errors remain caller-owned."""
    return snapshot_stat_v1(os.fstat(descriptor))


__all__ = [
    "PosixObjectKeyV1",
    "PosixStableMetadataV1",
    "PosixStatSnapshotV1",
    "snapshot_fd_v1",
    "snapshot_stat_v1",
]
