"""Shared descriptor-bound POSIX directory primitives for Birth adapters."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path, PurePosixPath
import stat
import sys

from executor_birth_posix_metadata import snapshot_stat_v1

DIRECTORY_FLAGS_V1 = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
FILE_FLAGS_V1 = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
ACL_NAMES_V1 = ("system.posix_acl_access", "system.posix_acl_default")


class PosixDirectoryError(RuntimeError):
    pass


def _fail(detail: str, cause: BaseException | None = None):
    error = PosixDirectoryError(detail)
    if cause is None:
        raise error
    raise error from cause


def require_posix_directory_platform_v1() -> None:
    """Reject platforms lacking the no-follow descriptor contract."""
    flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    dir_fd = getattr(os, "supports_dir_fd", ())
    no_follow = getattr(os, "supports_follow_symlinks", ())
    functions = ("open", "stat", "fstat", "close", "getxattr")
    supported = (
        os.name == "posix" and sys.platform.startswith("linux")
        and all(type(getattr(os, name, None)) is int for name in flags)
        and all(callable(getattr(os, name, None)) for name in functions)
        and os.open in dir_fd and os.stat in dir_fd
        and os.stat in no_follow
    )
    if not supported:
        _fail("POSIX directory platform unsupported")


def acl_presence_v1(descriptor: int) -> tuple[bool, bool]:
    require_posix_directory_platform_v1()
    found = []
    for name in ACL_NAMES_V1:
        try:
            os.getxattr(descriptor, name)
            found.append(True)
        except (TypeError, ValueError, NotImplementedError) as exc:
            _fail("POSIX ACL observation", exc)
        except OSError as exc:
            if exc.errno not in {errno.ENODATA, getattr(errno, "ENOATTR", -1)}:
                _fail("POSIX ACL observation", exc)
            found.append(False)
    return found[0], found[1]


def require_no_acl_v1(descriptor: int) -> None:
    if any(acl_presence_v1(descriptor)):
        _fail("POSIX ACL present")


def remove_acl_v1(descriptor: int) -> None:
    require_posix_directory_platform_v1()
    if not callable(getattr(os, "removexattr", None)):
        _fail("POSIX directory platform unsupported")
    for name in ACL_NAMES_V1:
        try:
            os.removexattr(descriptor, name)
        except OSError as exc:
            if exc.errno not in {errno.ENODATA, getattr(errno, "ENOATTR", -1)}:
                _fail("POSIX ACL removal", exc)


def require_rebound_v1(parent_fd: int, name: str, child_fd: int):
    try:
        opened = os.fstat(child_fd)
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        _fail("path rebind", exc)
    if not os.path.samestat(opened, rebound):
        _fail("path replaced")
    return opened


def run_cleanup_v1(actions, *, detail: str = "POSIX cleanup") -> None:
    """Attempt every teardown action without masking an active failure."""
    failures: list[Exception] = []
    for action in actions:
        try:
            action()
        except Exception as exc:
            failures.append(exc)
    if not failures:
        return
    active = sys.exception()
    if active is not None:
        for failure in failures:
            active.add_note(f"{detail}: {failure!r}")
        return
    _fail(detail, failures[0])


def close_descriptors_v1(descriptors) -> None:
    run_cleanup_v1(
        (lambda descriptor=descriptor: os.close(descriptor)
         for descriptor in reversed(tuple(descriptors))),
        detail="POSIX descriptor close",
    )


def require_lock_file_v1(
    root_fd: int, name: str, descriptor: int, owner: tuple[int, int],
) -> None:
    try:
        before = snapshot_stat_v1(os.fstat(descriptor))
    except OSError as exc:
        _fail("lock metadata", exc)
    if (
        not stat.S_ISREG(before.mode) or before.link_count != 1
        or before.size != 0 or (before.uid, before.gid) != owner
        or stat.S_IMODE(before.mode) != 0o600
    ):
        _fail("lock metadata")
    require_no_acl_v1(descriptor)
    try:
        rebound = snapshot_stat_v1(require_rebound_v1(
            root_fd, name, descriptor,
        ))
    except PosixDirectoryError as exc:
        _fail("lock replaced", exc)
    if before != rebound:
        _fail("lock replaced")


def open_lock_file_v1(
    root_fd: int, name: str, owner: tuple[int, int],
) -> int:
    flags = (
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=root_fd)
        require_lock_file_v1(root_fd, name, descriptor, owner)
        return descriptor
    except PosixDirectoryError:
        if descriptor >= 0:
            close_descriptors_v1((descriptor,))
        raise
    except OSError as exc:
        if descriptor >= 0:
            close_descriptors_v1((descriptor,))
        _fail("lock open", exc)


def _open_bound_v1(parent_fd: int | None, name: str, flags: int) -> int:
    descriptor = os.open(name, flags) if parent_fd is None else os.open(
        name, flags, dir_fd=parent_fd,
    )
    try:
        if parent_fd is not None:
            require_rebound_v1(parent_fd, name, descriptor)
        return descriptor
    except BaseException:
        close_descriptors_v1([descriptor])
        raise


class BoundDirectoryChainV1:
    """An absolute directory chain rebound through every live basename."""

    __slots__ = ("_descriptors", "_path", "_pid")

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute() or path == Path("/"):
            _fail("absolute directory")
        require_posix_directory_platform_v1()
        self._path, self._pid, self._descriptors = path, os.getpid(), []
        try:
            self._descriptors.append(_open_bound_v1(None, "/", DIRECTORY_FLAGS_V1))
            for part in path.parts[1:]:
                parent = self._descriptors[-1]
                self._descriptors.append(
                    _open_bound_v1(parent, part, DIRECTORY_FLAGS_V1),
                )
            self.assert_bound()
        except BaseException:
            close_descriptors_v1(self._descriptors)
            self._descriptors = []
            raise

    @property
    def root_fd(self) -> int:
        self.assert_bound()
        return self._descriptors[-1]

    def assert_bound(self) -> None:
        if os.getpid() != self._pid or not self._descriptors:
            _fail("directory capability inactive")
        root = self._descriptors[0]
        if not os.path.samestat(
            os.fstat(root), os.stat("/", follow_symlinks=False),
        ):
            _fail("path replaced")
        for index, name in enumerate(self._path.parts[1:], 1):
            info = require_rebound_v1(
                self._descriptors[index - 1], name,
                self._descriptors[index],
            )
            if not stat.S_ISDIR(info.st_mode):
                _fail("directory metadata")

    def attest_metadata(
        self, expected: dict[Path, tuple[int, int, int | None]],
    ) -> None:
        """Require owner, ACL absence and exact or anchor-safe mode."""
        if type(expected) is not dict:
            _fail("directory expectation")
        paths = [Path("/")]
        for part in self._path.parts[1:]:
            paths.append(paths[-1] / part)
        if set(expected) != set(paths):
            _fail("directory expectation")
        self.assert_bound()
        for path, descriptor in zip(paths, self._descriptors):
            desired = expected[path]
            if (
                type(desired) is not tuple or len(desired) != 3
                or any(type(value) is not int for value in desired[:2])
                or desired[2] is not None and type(desired[2]) is not int
            ):
                _fail("directory expectation")
            before = snapshot_stat_v1(os.fstat(descriptor))
            actual_mode = stat.S_IMODE(before.mode)
            mode_valid = (
                actual_mode & 0o022 == 0
                if desired[2] is None else actual_mode == desired[2]
            )
            if (
                (before.uid, before.gid) != desired[:2]
                or not mode_valid
            ):
                _fail("directory metadata")
            require_no_acl_v1(descriptor)
            after = snapshot_stat_v1(os.fstat(descriptor))
            if before != after:
                _fail("directory metadata changed")
        self.assert_bound()

    @contextmanager
    def open_relative(
        self, relative: PurePosixPath, *, directory: bool,
    ):
        if (
            type(relative) is not PurePosixPath or relative.is_absolute()
            or not relative.parts or ".." in relative.parts
        ):
            _fail("relative path")
        self.assert_bound()
        opened: list[int] = []
        try:
            parent = self._descriptors[-1]
            for index, name in enumerate(relative.parts):
                final = index == len(relative.parts) - 1
                flags = (
                    DIRECTORY_FLAGS_V1 if not final or directory
                    else FILE_FLAGS_V1
                )
                opened.append(_open_bound_v1(parent, name, flags))
                parent = opened[-1]
            yield opened[-1]
            for index, (name, descriptor) in enumerate(
                zip(relative.parts, opened),
            ):
                parent = (
                    self._descriptors[-1] if index == 0
                    else opened[index - 1]
                )
                require_rebound_v1(parent, name, descriptor)
            self.assert_bound()
        finally:
            close_descriptors_v1(opened)

    def close(self) -> None:
        descriptors, self._descriptors = self._descriptors, []
        close_descriptors_v1(descriptors)


__all__ = [
    "ACL_NAMES_V1", "BoundDirectoryChainV1", "DIRECTORY_FLAGS_V1",
    "FILE_FLAGS_V1", "PosixDirectoryError", "acl_presence_v1",
    "close_descriptors_v1", "open_lock_file_v1", "remove_acl_v1",
    "require_lock_file_v1", "require_no_acl_v1",
    "require_posix_directory_platform_v1", "require_rebound_v1", "run_cleanup_v1",
]
