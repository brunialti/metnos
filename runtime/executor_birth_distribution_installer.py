#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Private filesystem core that publishes one prepared distribution.

This module deliberately exposes NO productive installer. It holds only the
no-replace publication, its synchronisation and its complete re-read, plus a
nominally distinct seam that exercises the core with a test-only capability.

The core does not decide whether a publication is authorised: type, owner,
minter and validator of that authority belong to G6-D, which will wrap the
core after verifying authority, live session and prepared capability bound by
identity. Keeping the decision out of here is what makes the core reusable
without becoming an authority of its own: a caller cannot obtain publication
by holding a path, and the productive graph does not reach this module.

Neither the seam nor the future wrapper accepts a destination, a conflict
behaviour or a callback from the caller. The destination is derived, the
conflict behaviour is fixed (no-replace), and there is nothing to call back.
"""
from __future__ import annotations

import errno
import os
import sys
from dataclasses import dataclass
from pathlib import Path


PUBLICATION_DOMAIN_V1 = b"metnos.executor-birth.distribution-publication/v1\0"
MAX_PUBLISHED_FILE_BYTES_V1 = 64 * 1024 * 1024
MAX_PUBLISHED_FILES_V1 = 4096


class DistributionInstallerError(RuntimeError):
    """One stable denial class; detail never reaches an operator stream."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(code: str, detail: str = "") -> DistributionInstallerError:
    return DistributionInstallerError(code, detail)


@dataclass(frozen=True, slots=True)
class _StagedFileV1:
    """One file observed under the staging root, by relative path."""

    relative_path: str
    size: int
    mode: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class _PublishedDistributionV1:
    """The result of one completed publication, re-read from the final name."""

    final_path: str
    files: tuple[_StagedFileV1, ...]
    bundle_hash: str
    repeated: bool


def _require_supported_platform_v1() -> None:
    """Windows denies before session, filesystem or authority are consulted.

    The publication relies on POSIX rename semantics for its no-replace
    guarantee and on POSIX ownership for the re-read. Emulating either on
    Windows would make the denial depend on how well the emulation held,
    which is not a property this boundary can prove.
    """
    if sys.platform.startswith("win"):
        raise _invalid("unsupported_platform", sys.platform)


def _relative_staged_path_v1(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _invalid("staging_escape", str(path)) from exc
    text = relative.as_posix()
    if not text or text.startswith("/") or ".." in relative.parts:
        raise _invalid("staging_escape", text)
    return text


def _hash_regular_file_v1(path: Path) -> tuple[int, int, str]:
    import hashlib

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        status = os.fstat(descriptor)
        if not os.path.stat.S_ISREG(status.st_mode):
            raise _invalid("staging_not_regular", str(path))
        if status.st_size > MAX_PUBLISHED_FILE_BYTES_V1:
            raise _invalid("staging_too_large", str(path))
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1 << 16)
            if not chunk:
                break
            digest.update(chunk)
        return (
            status.st_size,
            os.path.stat.S_IMODE(status.st_mode),
            f"sha256:{digest.hexdigest()}",
        )
    finally:
        os.close(descriptor)


def observe_staging_v1(staging_root: Path) -> tuple[_StagedFileV1, ...]:
    """Read every staged file exactly once, in a deterministic order."""
    _require_supported_platform_v1()
    if not isinstance(staging_root, Path) or not staging_root.is_absolute():
        raise _invalid("staging_root_invalid", str(staging_root))
    if staging_root.is_symlink() or not staging_root.is_dir():
        raise _invalid("staging_root_invalid", str(staging_root))
    observed: list[_StagedFileV1] = []
    for path in sorted(staging_root.rglob("*")):
        if path.is_symlink():
            raise _invalid("staging_link", str(path))
        if path.is_dir():
            continue
        if len(observed) >= MAX_PUBLISHED_FILES_V1:
            raise _invalid("staging_too_many", str(staging_root))
        relative = _relative_staged_path_v1(staging_root, path)
        size, mode, content_hash = _hash_regular_file_v1(path)
        observed.append(_StagedFileV1(relative, size, mode, content_hash))
    if not observed:
        raise _invalid("staging_empty", str(staging_root))
    return tuple(observed)


def bundle_hash_v1(files: tuple[_StagedFileV1, ...]) -> str:
    """Frame the observation so no field can slide into its neighbour."""
    import hashlib

    digest = hashlib.sha256(PUBLICATION_DOMAIN_V1)
    digest.update(len(files).to_bytes(8, "big"))
    for item in sorted(files, key=lambda entry: entry.relative_path.encode()):
        encoded = item.relative_path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(item.mode.to_bytes(4, "big"))
        digest.update(item.size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(item.content_hash.split(":", 1)[1]))
    return f"sha256:{digest.hexdigest()}"


def _sync_directory_v1(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_core_v1(
    staging_root: Path, final_path: Path, expected_bundle_hash: str,
) -> _PublishedDistributionV1:
    """Move one verified staging tree to its final name, or refuse.

    An exact repetition is idempotent: when the final name already holds the
    same bundle the core reports it as repeated instead of failing, because a
    publication interrupted after the rename and before its receipt must be
    resumable. A final name holding anything else is a collision, never an
    overwrite.
    """
    _require_supported_platform_v1()
    staged = observe_staging_v1(staging_root)
    observed_hash = bundle_hash_v1(staged)
    if observed_hash != expected_bundle_hash:
        raise _invalid("staging_bundle_mismatch", observed_hash)
    if final_path.is_symlink():
        raise _invalid("final_name_link", str(final_path))
    if final_path.exists():
        published = observe_staging_v1(final_path)
        if bundle_hash_v1(published) != expected_bundle_hash:
            raise _invalid("final_name_collision", str(final_path))
        return _PublishedDistributionV1(
            str(final_path), published, expected_bundle_hash, True,
        )
    _sync_directory_v1(staging_root)
    try:
        os.rename(staging_root, final_path)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise _invalid("final_name_collision", str(final_path)) from exc
        raise _invalid("publication_failed", str(exc)) from exc
    _sync_directory_v1(final_path.parent)
    # The receipt is the RE-READ, not the rename: a publication is complete
    # only once the final name has been observed again from scratch and still
    # frames to the same bundle.
    republished = observe_staging_v1(final_path)
    if bundle_hash_v1(republished) != expected_bundle_hash:
        raise _invalid("published_bundle_mismatch", str(final_path))
    return _PublishedDistributionV1(
        str(final_path), republished, expected_bundle_hash, False,
    )


@dataclass(frozen=True, slots=True)
class _TestOnlyPublicationCapabilityV1:
    """Nominally distinct capability; the productive graph never mints it."""

    staging_root: Path
    final_path: Path
    expected_bundle_hash: str


def publish_for_test_v1(
    capability: _TestOnlyPublicationCapabilityV1,
) -> _PublishedDistributionV1:
    """Exercise the core through a capability no productive caller can hold."""
    if type(capability) is not _TestOnlyPublicationCapabilityV1:
        raise _invalid("test_capability_invalid", type(capability).__name__)
    return _publish_core_v1(
        capability.staging_root,
        capability.final_path,
        capability.expected_bundle_hash,
    )


__all__ = [
    "DistributionInstallerError",
    "MAX_PUBLISHED_FILES_V1",
    "MAX_PUBLISHED_FILE_BYTES_V1",
    "PUBLICATION_DOMAIN_V1",
    "bundle_hash_v1",
    "observe_staging_v1",
]
