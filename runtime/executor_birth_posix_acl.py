"""Read-only Linux POSIX ACL xattr observation and validation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import os
import struct


class PosixAclFailureKindV1(str, Enum):
    platform_unsupported = "platform_unsupported"
    filesystem_unsupported = "filesystem_unsupported"
    malformed = "malformed"
    permission_denied = "permission_denied"
    io_unavailable = "io_unavailable"


class PosixAclError(RuntimeError):
    def __init__(
        self, kind: PosixAclFailureKindV1,
        cause: BaseException | None = None,
    ) -> None:
        self.kind = kind
        self.internal_cause = cause
        super().__init__(kind.value)


@dataclass(frozen=True, slots=True)
class PosixAclSnapshotV1:
    has_extended_access_acl: bool
    has_default_acl: bool


_ACCESS = "system.posix_acl_access"
_DEFAULT = "system.posix_acl_default"
_VERSION = 2
_BASE_TAGS = frozenset({0x01, 0x04, 0x20})
_NAMED_TAGS = frozenset({0x02, 0x08})
_MASK_TAG = 0x10
_ALL_TAGS = _BASE_TAGS | _NAMED_TAGS | {_MASK_TAG}
_UNDEFINED_ID = 0xFFFFFFFF


def _fail(
    kind: PosixAclFailureKindV1, cause: BaseException | None = None,
) -> PosixAclError:
    return PosixAclError(kind, cause)


def _read_xattr(descriptor: int, name: str) -> bytes | None:
    getxattr = getattr(os, "getxattr", None)
    if not callable(getxattr):
        raise _fail(PosixAclFailureKindV1.platform_unsupported)
    try:
        return getxattr(descriptor, name)
    except (TypeError, ValueError, NotImplementedError) as exc:
        raise _fail(PosixAclFailureKindV1.platform_unsupported, exc) from exc
    except OSError as exc:
        absent = {errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA)}
        unsupported = {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}
        if exc.errno in absent:
            return None
        if exc.errno in unsupported:
            raise _fail(PosixAclFailureKindV1.filesystem_unsupported, exc) from exc
        if exc.errno in {errno.EACCES, errno.EPERM}:
            raise _fail(PosixAclFailureKindV1.permission_denied, exc) from exc
        raise _fail(PosixAclFailureKindV1.io_unavailable, exc) from exc


def _decode_tags(payload: bytes) -> tuple[int, ...]:
    if type(payload) is not bytes or len(payload) < 4 or (len(payload) - 4) % 8:
        raise _fail(PosixAclFailureKindV1.malformed)
    if struct.unpack_from("<I", payload)[0] != _VERSION:
        raise _fail(PosixAclFailureKindV1.malformed)
    entries = tuple(
        struct.unpack_from("<HHI", payload, offset)
        for offset in range(4, len(payload), 8)
    )
    _validate_entries(entries)
    return tuple(tag for tag, _permission, _identifier in entries)


def _validate_entries(entries: tuple[tuple[int, int, int], ...]) -> None:
    tags = tuple(tag for tag, _permission, _identifier in entries)
    if not entries or any(tag not in _ALL_TAGS for tag in tags):
        raise _fail(PosixAclFailureKindV1.malformed)
    if any(permission & ~0o7 for _tag, permission, _identifier in entries):
        raise _fail(PosixAclFailureKindV1.malformed)
    if {tag for tag in tags if tag in _BASE_TAGS} != _BASE_TAGS:
        raise _fail(PosixAclFailureKindV1.malformed)
    if any(tags.count(tag) > 1 for tag in _BASE_TAGS | {_MASK_TAG}):
        raise _fail(PosixAclFailureKindV1.malformed)
    for tag, _permission, identifier in entries:
        if (tag in _NAMED_TAGS) == (identifier == _UNDEFINED_ID):
            raise _fail(PosixAclFailureKindV1.malformed)
    named = [(tag, identifier) for tag, _, identifier in entries if tag in _NAMED_TAGS]
    if len(named) != len(set(named)) or named and _MASK_TAG not in tags:
        raise _fail(PosixAclFailureKindV1.malformed)


def observe_posix_directory_acl_v1(descriptor: int) -> PosixAclSnapshotV1:
    """Return access/default ACL facts or a typed fail-closed error."""
    access = _read_xattr(descriptor, _ACCESS)
    default = _read_xattr(descriptor, _DEFAULT)
    access_tags = () if access is None else _decode_tags(access)
    if default is not None:
        _decode_tags(default)
    return PosixAclSnapshotV1(
        bool(access_tags and set(access_tags) != _BASE_TAGS),
        default is not None,
    )


__all__ = [
    "PosixAclError", "PosixAclFailureKindV1", "PosixAclSnapshotV1",
    "observe_posix_directory_acl_v1",
]
