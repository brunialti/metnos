"""Read-only Linux observer for reserved first-transition state."""
from __future__ import annotations

import errno
import os
from pathlib import Path, PurePosixPath
import stat
import sys

import executor_birth_host_path_policy as host_path_policy
from executor_birth_posix_metadata import snapshot_stat_v1

from executor_birth_legacy_state_policy import (
    LEGACY_STATE_RESERVED_TOP_LEVEL_V1,
    MAX_LEGACY_STATE_DEPTH_V1,
    MAX_LEGACY_STATE_ENTRIES_V1,
    MAX_LEGACY_STATE_FILE_BYTES_V1,
    MAX_LEGACY_STATE_TOTAL_BYTES_V1,
    LegacyNodeKindV1,
    LegacyPathObservationV1,
    LegacyStateObservationV1,
    legacy_state_file_sha256_v1,
)
from executor_birth_legacy_state_request import (
    LegacyStateError,
    LegacyStateRequestV1,
    require_canonical_legacy_state_request_v1,
)


_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_ACL_NAMES = ("system.posix_acl_access", "system.posix_acl_default")
# The shared directory capability does not expose its descriptor or bounded
# enumeration, while this observer must hash regular files in one closed walk.
# The canonical public host policy owns anchors; Path is an adapter-edge type.
_TRUST_ANCHORS_V1 = frozenset(
    Path(path.as_posix()) for path in host_path_policy.HOST_TRUST_ANCHORS_V1
)


class LegacyStatePosixError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.code = "birth_legacy_state_observation_failed"
        self.detail = detail
        super().__init__(self.code)


def _fail(detail: str, cause: BaseException | None = None):
    error = LegacyStatePosixError(detail)
    if cause is None:
        raise error
    raise error from cause


def _same_inode(first, second) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _same_snapshot(first, second) -> bool:
    return snapshot_stat_v1(first) == snapshot_stat_v1(second)


def _require_platform_v1() -> None:
    flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    supported = (
        os.name == "posix" and sys.platform.startswith("linux")
        and all(type(getattr(os, name, None)) is int for name in flags)
        and callable(getattr(os, "getxattr", None))
        and os.open in getattr(os, "supports_dir_fd", ())
        and os.stat in getattr(os, "supports_dir_fd", ())
        and os.stat in getattr(os, "supports_follow_symlinks", ())
        and os.scandir in getattr(os, "supports_fd", ())
    )
    if not supported:
        _fail("unsupported platform")


def _rebind(parent_fd: int, name: str, child_fd: int):
    try:
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(child_fd)
    except OSError as exc:
        _fail("path rebind", exc)
    if not _same_inode(opened, rebound):
        _fail("path replaced")
    return opened


def _open_chain(path: Path) -> list[tuple[int | None, str, int]]:
    if not path.is_absolute() or path == Path("/"):
        _fail("state root path")
    chain = [(None, "/", os.open("/", _DIRECTORY_FLAGS))]
    try:
        for name in path.parts[1:]:
            parent = chain[-1][2]
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                _rebind(parent, name, child)
            except BaseException:
                os.close(child)
                raise
            chain.append((parent, name, child))
        return chain
    except OSError as exc:
        for _parent, _name, descriptor in reversed(chain):
            os.close(descriptor)
        _fail("state root chain", exc)
    except BaseException:
        for _parent, _name, descriptor in reversed(chain):
            os.close(descriptor)
        raise


def _acl(descriptor: int) -> tuple[bool, bool]:
    # The shared directory ACL owner reports extended access ACLs.  This
    # inventory also covers files and deliberately rejects any ACL xattr,
    # including a base-only access ACL, so it keeps the stricter raw fact.
    result = []
    for name in _ACL_NAMES:
        try:
            os.getxattr(descriptor, name)
            result.append(True)
        except (TypeError, ValueError, NotImplementedError) as exc:
            _fail("ACL observation", exc)
        except OSError as exc:
            if exc.errno not in {errno.ENODATA, getattr(errno, "ENOATTR", -1)}:
                _fail("ACL observation", exc)
            result.append(False)
    return result[0], result[1]


def _expected_chain(request: LegacyStateRequestV1):
    state_root = Path(request.state_root.as_posix())
    expected = {}
    for item in host_path_policy.HOST_PATH_POLICY_V1:
        path = Path(item.path.as_posix())
        if path != state_root and path not in state_root.parents:
            continue
        owner = (
            (0, 0) if item.owner_kind is host_path_policy.HostOwnerKindV1.root
            else (request.service_uid, request.service_gid)
        )
        expected[path] = (*owner, item.mode)
    return expected


def _attest_chain(chain, request, *, test_anchor: Path | None = None) -> None:
    expected = _expected_chain(request) if test_anchor is None else {}
    current = Path("/")
    for parent, name, descriptor in chain:
        current = current if parent is None else current / name
        before = os.fstat(descriptor)
        acl = _acl(descriptor)
        after = os.fstat(descriptor)
        rebound = after if parent is None else _rebind(parent, name, descriptor)
        if not _same_snapshot(before, after) or not _same_snapshot(after, rebound):
            _fail("chain metadata changed")
        desired = expected.get(current)
        anchor = test_anchor is None and current in _TRUST_ANCHORS_V1
        isolated = test_anchor is not None and current == test_anchor
        if anchor and ((after.st_uid, after.st_gid) != (0, 0) or after.st_mode & 0o022):
            _fail("trust anchor metadata")
        if desired is not None and (
            (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) != desired
        ):
            _fail("canonical chain metadata")
        if isolated and (
            (after.st_uid, after.st_gid) != (request.service_uid, request.service_gid)
            or after.st_mode & 0o077
        ):
            _fail("test anchor metadata")
        if (anchor or desired is not None or isolated) and any(acl):
            _fail("chain ACL")
    if test_anchor is None and Path(request.state_root.as_posix()) not in expected:
        _fail("canonical state root")


def _read_file(parent_fd: int, name: str, before):
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        _fail("file open", exc)
    try:
        opened = _rebind(parent_fd, name, descriptor)
        if not _same_snapshot(before, opened) or opened.st_size > MAX_LEGACY_STATE_FILE_BYTES_V1:
            _fail("file metadata changed")
        chunks, remaining = [], opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                _fail("file shortened")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("file extended")
        after = os.fstat(descriptor)
        if not _same_snapshot(opened, after):
            _fail("file changed")
        acl = _acl(descriptor)
        final = os.fstat(descriptor)
        rebound = _rebind(parent_fd, name, descriptor)
        if not _same_snapshot(after, final) or not _same_snapshot(final, rebound):
            _fail("file ACL observation changed")
        return b"".join(chunks), final, acl
    finally:
        os.close(descriptor)


def _names(descriptor: int, remaining_entries: int) -> tuple[str, ...]:
    names = []
    try:
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                _name_bytes(entry.name)
                names.append(entry.name)
                if len(names) > remaining_entries:
                    _fail("inventory entries")
    except LegacyStatePosixError:
        raise
    except OSError as exc:
        _fail("directory inventory", exc)
    return tuple(sorted(names, key=_name_bytes))


def _name_bytes(name: object) -> bytes:
    if type(name) is not str or not name or name in {".", ".."} or "\0" in name:
        _fail("inventory name")
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        _fail("inventory name encoding", exc)
    if len(encoded) > 255 or "/" in name:
        _fail("inventory name")
    return encoded


class _Budget:
    def __init__(self) -> None:
        self.entries = 0
        self.bytes = 0

    def add_entry(self) -> None:
        self.entries += 1
        if self.entries > MAX_LEGACY_STATE_ENTRIES_V1:
            _fail("inventory entries")

    def add_bytes(self, size: int) -> None:
        self.bytes += size
        if self.bytes > MAX_LEGACY_STATE_TOTAL_BYTES_V1:
            _fail("inventory bytes")


def _other(relative: PurePosixPath, info) -> LegacyPathObservationV1:
    return LegacyPathObservationV1(
        relative, LegacyNodeKindV1.other, info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode), info.st_nlink, None, None,
        False, False, info.st_dev, info.st_ino,
    )


def _scan_name(parent_fd, name, relative, depth, budget, result) -> None:
    if depth > MAX_LEGACY_STATE_DEPTH_V1:
        _fail("inventory depth")
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        _fail("path disappeared")
    except OSError as exc:
        _fail("path metadata", exc)
    budget.add_entry()
    if stat.S_ISLNK(before.st_mode) or not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
        result.append(_other(relative, before))
        return
    if stat.S_ISREG(before.st_mode):
        payload, opened, acl = _read_file(parent_fd, name, before)
        budget.add_bytes(len(payload))
        result.append(LegacyPathObservationV1(
            relative, LegacyNodeKindV1.regular_file, opened.st_uid, opened.st_gid,
            stat.S_IMODE(opened.st_mode), opened.st_nlink, len(payload),
            legacy_state_file_sha256_v1(payload), acl[0], acl[1],
            opened.st_dev, opened.st_ino,
        ))
        return
    _scan_directory(parent_fd, name, relative, depth, before, budget, result)


def _scan_directory(parent_fd, name, relative, depth, before, budget, result) -> None:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        _fail("directory open", exc)
    try:
        opened = _rebind(parent_fd, name, descriptor)
        if not _same_snapshot(before, opened):
            _fail("directory changed")
        remaining = MAX_LEGACY_STATE_ENTRIES_V1 - budget.entries
        for child in _names(descriptor, remaining):
            _scan_name(
                descriptor, child, relative / child, depth + 1, budget, result,
            )
        after = os.fstat(descriptor)
        if not _same_snapshot(opened, after):
            _fail("directory changed")
        acl = _acl(descriptor)
        final = os.fstat(descriptor)
        rebound = _rebind(parent_fd, name, descriptor)
        if not _same_snapshot(after, final) or not _same_snapshot(final, rebound):
            _fail("directory ACL observation changed")
        result.append(LegacyPathObservationV1(
            relative, LegacyNodeKindV1.directory, final.st_uid, final.st_gid,
            stat.S_IMODE(final.st_mode), final.st_nlink, None, None,
            acl[0], acl[1], final.st_dev, final.st_ino,
        ))
    finally:
        os.close(descriptor)


def _require_state_root(descriptor: int, request: LegacyStateRequestV1):
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != (request.service_uid, request.service_gid)
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail("state root metadata")
    return info


def _observe_core(request, *, test_anchor: Path | None):
    _require_platform_v1()
    if type(request) is not LegacyStateRequestV1:
        _fail("request type")
    chain = _open_chain(Path(request.state_root.as_posix()))
    result, budget = [], _Budget()
    try:
        root_fd = chain[-1][2]
        _attest_chain(chain, request, test_anchor=test_anchor)
        initial_root = _require_state_root(root_fd, request)
        for relative in LEGACY_STATE_RESERVED_TOP_LEVEL_V1:
            try:
                os.stat(relative.name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as exc:
                _fail("reserved path metadata", exc)
            _scan_name(root_fd, relative.name, relative, 1, budget, result)
        final_root = _require_state_root(root_fd, request)
        if not _same_snapshot(initial_root, final_root):
            _fail("state root changed")
        _attest_chain(chain, request, test_anchor=test_anchor)
    finally:
        for _parent, _name, descriptor in reversed(chain):
            os.close(descriptor)
    ordered = tuple(sorted(result, key=lambda item: os.fsencode(item.relative_path.as_posix())))
    return LegacyStateObservationV1(ordered)


def observe_legacy_state_v1(request: LegacyStateRequestV1) -> LegacyStateObservationV1:
    """Observe reserved names under the canonical typed host state root."""
    try:
        require_canonical_legacy_state_request_v1(request)
    except LegacyStateError as exc:
        _fail("canonical request", exc)
    return _observe_core(request, test_anchor=None)


def _observe_legacy_state_for_test_v1(
    request: LegacyStateRequestV1,
) -> LegacyStateObservationV1:
    if type(request) is not LegacyStateRequestV1 or request._canonical is not False:
        _fail("test request")
    return _observe_core(request, test_anchor=Path(request.state_root.as_posix()))


__all__ = ["LegacyStatePosixError", "observe_legacy_state_v1"]
