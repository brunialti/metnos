"""Read-only administrative custody primitives, without runtime bootstrap.

Shared by F4 and the optional F5 authority. Importing this module does not
resolve user configuration, create directories or load private key material.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


DEFAULT_OWNERSHIP_ROOT_V1 = Path("/var/lib/metnos/executor-birth")


class OwnershipAuthorityError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _managed_authority_platform_supported_v1() -> bool:
    """The managed administrative authority surface is Linux-only."""
    return sys.platform.startswith("linux")


def _directory_metadata(path: Path, *, root_owned: bool) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OwnershipAuthorityError("birth_ownership_authority_missing", path.name) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
        or (hasattr(path, "is_junction") and path.is_junction())
        or stat.S_IMODE(info.st_mode) != 0o755
        or info.st_mode & 0o022
        or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
    ):
        raise OwnershipAuthorityError("birth_ownership_authority_unsafe", path.name)


def _root_owned_chain(path: Path) -> None:
    if not path.is_absolute():
        raise OwnershipAuthorityError("birth_ownership_authority_unsafe", "non-absolute root")
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except OSError as exc:
            raise OwnershipAuthorityError("birth_ownership_authority_missing", component.name) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022
        ):
            raise OwnershipAuthorityError("birth_ownership_authority_unsafe", component.name)


def _read_regular(path: Path, *, maximum: int, mode: int, root_owned: bool) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OwnershipAuthorityError("birth_ownership_authority_missing", path.name) from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or (root_owned and (before.st_uid != 0 or before.st_gid != 0))
            or before.st_size > maximum
        ):
            raise OwnershipAuthorityError("birth_ownership_authority_unsafe", path.name)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(fd)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
            item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )
        if (len(payload) > maximum or len(payload) != before.st_size
                or identity(before) != identity(after)):
            raise OwnershipAuthorityError("birth_ownership_authority_unsafe", path.name)
        return payload
    finally:
        os.close(fd)
