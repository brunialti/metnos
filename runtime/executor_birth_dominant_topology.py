#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Private core that installs a unit topology and re-reads what it wrote.

Point 1 of the group 7 wrapper: install and re-read the dominant topology. This
module owns the writing and the re-reading, and nothing else. It does not
decide which units belong to the topology, does not reload a manager, does not
start or stop anything, and does not hold a lock — the wrapper already holds
all three when it calls in.

The receipt is the RE-READ, never the write. A write that returned success is a
claim about a system call; a file read back from the directory it was written
into is a fact about the filesystem. On a boundary whose whole purpose is to
replace the running topology, only the second is worth having.

Everything happens under a root the caller injects, for the same reason as the
neutraliser: a module that could write into `/etc/systemd/system` by default
would be one typo away from replacing the live topology while proving something
about a fixture.
"""
from __future__ import annotations

import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


DOMINANT_TOPOLOGY_DOMAIN_V1 = b"metnos.executor-birth.dominant-topology/v1\0"
MAX_UNIT_FRAGMENT_BYTES_V1 = 256 * 1024
MAX_TOPOLOGY_UNITS_V1 = 256
UNIT_MODE_V1 = 0o644


class DominantTopologyError(RuntimeError):
    """One stable denial class; detail never reaches an operator stream."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(code: str, detail: str = "") -> DominantTopologyError:
    return DominantTopologyError(code, detail)


def _require_supported_platform_v1() -> None:
    """Windows denies first, before any path is resolved.

    A unit fragment is meaningless without a systemd manager to read it.
    Writing one on Windows would produce a green result that proves nothing,
    which §23.40 of the roadmap already cost this project once.
    """
    if sys.platform.startswith("win"):
        raise _invalid("topology_unsupported_platform", sys.platform)


@dataclass(frozen=True, slots=True)
class InstalledUnitV1:
    """One unit as the filesystem reported it AFTER the write."""

    unit_name: str
    content_hash: str
    size: int
    mode: int
    repeated: bool


def _require_unit_name_v1(name: object) -> str:
    if (
        type(name) is not str or not name or "/" in name or name in {".", ".."}
        or name.startswith(".") or len(name.encode("utf-8")) > 192
        or not name.endswith((".service", ".timer", ".target"))
    ):
        raise _invalid("topology_unit_name_invalid", str(name))
    return name


def _content_hash_v1(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _write_all_v1(descriptor: int, payload: bytes, position: int = 0) -> None:
    while position < len(payload):
        written = os.write(descriptor, payload[position:])
        if written <= 0:
            raise _invalid("topology_fragment_write_failed")
        position += written


def _read_regular_v1(path: Path, maximum: int) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _invalid("topology_unit_unconfirmed", path.name) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise _invalid("topology_unit_unconfirmed", path.name)
        observed = bytearray()
        while len(observed) <= maximum:
            block = os.read(descriptor, min(65536, maximum + 1 - len(observed)))
            if not block:
                break
            observed.extend(block)
        after = os.fstat(descriptor)
        if (
            len(observed) != before.st_size
            or (before.st_dev, before.st_ino, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns)
        ):
            raise _invalid("topology_unit_unconfirmed", path.name)
        return bytes(observed), after
    finally:
        os.close(descriptor)


def _publish_staged_v1(temporary: Path, final: Path) -> None:
    from executor_birth_secure_fs import (
        BirthSecureFSError, _renameat2_no_replace,
    )

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(final.parent, flags)
    try:
        _renameat2_no_replace(
            directory_fd, temporary.name, directory_fd, final.name,
        )
        os.fsync(directory_fd)
    except BirthSecureFSError as exc:
        raise _invalid("topology_unit_collision", final.name) from exc
    finally:
        os.close(directory_fd)


def _install_one_v1(
    root: Path, name: str, payload: bytes, *,
    _crash_seam: Callable[[str], None] | None,
) -> InstalledUnitV1:
    """Write, or agree that the same bytes are already there."""
    if type(payload) is not bytes or not payload:
        raise _invalid("topology_fragment_invalid", name)
    if len(payload) > MAX_UNIT_FRAGMENT_BYTES_V1:
        raise _invalid("topology_fragment_too_large", name)
    path = root / name
    if path.is_symlink():
        raise _invalid("topology_unit_link", name)
    temporary = root / f".{name}.installing"
    repeated = False
    if path.exists():
        if temporary.exists() or temporary.is_symlink():
            raise _invalid("topology_temporary_conflict", name)
        observed, status = _read_regular_v1(path, len(payload))
        if observed != payload or (status.st_mode & 0o7777) != UNIT_MODE_V1:
            # A name already holding DIFFERENT bytes is a collision, never an
            # overwrite: replacing it silently would discard a topology this
            # module was never told about.
            raise _invalid("topology_unit_collision", name)
        repeated = True
    else:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            observed = os.read(descriptor, len(payload) + 1)
            if not payload.startswith(observed):
                raise _invalid("topology_temporary_conflict", name)
            _write_all_v1(descriptor, payload, len(observed))
            os.fchmod(descriptor, UNIT_MODE_V1)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(root, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if _crash_seam is not None:
            _crash_seam("dominant_fragment_staged")
        _publish_staged_v1(temporary, path)
        if _crash_seam is not None:
            _crash_seam("dominant_fragment_published")
    # The re-read: what the directory holds now, not what we believe we wrote.
    observed, status = _read_regular_v1(path, len(payload))
    if observed != payload or (status.st_mode & 0o7777) != UNIT_MODE_V1:
        raise _invalid("topology_unit_unconfirmed", name)
    return InstalledUnitV1(
        name, _content_hash_v1(observed), status.st_size,
        status.st_mode & 0o7777, repeated,
    )


def _install_core_v1(
    root: Path, fragments: Mapping[str, bytes], *,
    _crash_seam: Callable[[str], None] | None = None,
) -> tuple[InstalledUnitV1, ...]:
    _require_supported_platform_v1()
    if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
        raise _invalid("topology_root_invalid", str(root))
    if (
        not isinstance(fragments, Mapping) or not fragments
        or _crash_seam is not None and not callable(_crash_seam)
    ):
        raise _invalid("topology_fragments_invalid", "shape")
    if len(fragments) > MAX_TOPOLOGY_UNITS_V1:
        raise _invalid("topology_too_many_units", str(len(fragments)))
    installed = [
        _install_one_v1(
            root, _require_unit_name_v1(name), fragments[name],
            _crash_seam=_crash_seam,
        )
        for name in sorted(fragments, key=lambda item: str(item).encode("utf-8"))
    ]
    return tuple(installed)


def topology_digest_v1(units: tuple[InstalledUnitV1, ...]) -> str:
    """Frame the observed topology so no field can slide into its neighbour."""
    digest = hashlib.sha256(DOMINANT_TOPOLOGY_DOMAIN_V1)
    digest.update(len(units).to_bytes(8, "big"))
    for unit in units:
        if type(unit) is not InstalledUnitV1:
            raise _invalid("topology_receipt_invalid", "unit")
        for field in (unit.unit_name, unit.content_hash):
            encoded = field.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        digest.update(unit.size.to_bytes(8, "big"))
        digest.update(unit.mode.to_bytes(4, "big"))
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True, slots=True)
class _TestOnlyTopologyCapabilityV1:
    """Nominally distinct capability; the productive graph never mints it."""

    root: Path


def install_for_test_v1(
    capability: _TestOnlyTopologyCapabilityV1, fragments: Mapping[str, bytes],
    *, _crash_seam: Callable[[str], None] | None = None,
) -> tuple[InstalledUnitV1, ...]:
    """Exercise the core through a capability no productive caller can hold."""
    if type(capability) is not _TestOnlyTopologyCapabilityV1:
        raise _invalid("topology_capability_invalid", type(capability).__name__)
    return _install_core_v1(
        capability.root, fragments, _crash_seam=_crash_seam,
    )


__all__ = [
    "DOMINANT_TOPOLOGY_DOMAIN_V1",
    "DominantTopologyError",
    "InstalledUnitV1",
    "MAX_TOPOLOGY_UNITS_V1",
    "MAX_UNIT_FRAGMENT_BYTES_V1",
    "UNIT_MODE_V1",
    "topology_digest_v1",
]
