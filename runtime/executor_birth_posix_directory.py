"""Read-only, descriptor-bound POSIX directory walking and observation."""
from __future__ import annotations

import errno
import os
from pathlib import PurePosixPath
import stat
import sys
import unicodedata

from executor_birth_posix_acl import (
    PosixAclError, PosixAclFailureKindV1, PosixAclSnapshotV1,
    observe_posix_directory_acl_v1,
)
from executor_birth_posix_directory_model import (
    PosixChildObservationV1, PosixDirectoryError,
    PosixDirectoryFailureKindV1, PosixDirectoryObservationV1, PosixNodeKindV1,
)
from executor_birth_posix_metadata import (
    PosixStatSnapshotV1, snapshot_fd_v1, snapshot_stat_v1,
)


def _fail(
    kind: PosixDirectoryFailureKindV1, cause: BaseException | None = None,
) -> PosixDirectoryError:
    return PosixDirectoryError(kind, cause)


def _flags_v1() -> int:
    names = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    dir_fd = getattr(os, "supports_dir_fd", ())
    no_follow = getattr(os, "supports_follow_symlinks", ())
    if (
        os.name != "posix" or not sys.platform.startswith("linux")
        or any(not hasattr(os, name) for name in names)
        or os.open not in dir_fd or os.stat not in dir_fd
        or os.stat not in no_follow
    ):
        raise _fail(PosixDirectoryFailureKindV1.platform_unsupported)
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _component_v1(value: object) -> str:
    invalid = (
        type(value) is not str or not value or value in {".", ".."}
        or "/" in value or "\\" in value or "\0" in value
        or unicodedata.normalize("NFC", value) != value
    )
    if invalid:
        raise _fail(PosixDirectoryFailureKindV1.invalid_path)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail(PosixDirectoryFailureKindV1.invalid_path, exc) from exc
    if len(encoded) > 255:
        raise _fail(PosixDirectoryFailureKindV1.invalid_path)
    return value


def _absolute_components_v1(path: object) -> tuple[str, ...]:
    if type(path) is not PurePosixPath:
        raise _fail(PosixDirectoryFailureKindV1.invalid_path)
    raw = path.as_posix()
    if not raw.startswith("/") or raw.startswith("//") or "\0" in raw:
        raise _fail(PosixDirectoryFailureKindV1.invalid_path)
    return tuple(_component_v1(item) for item in path.parts[1:])


def _translate_os_error_v1(exc: OSError) -> PosixDirectoryError:
    mapping = {
        errno.ENOENT: PosixDirectoryFailureKindV1.not_found,
        errno.ELOOP: PosixDirectoryFailureKindV1.symlink_refused,
        errno.ENOTDIR: PosixDirectoryFailureKindV1.not_directory,
        errno.EACCES: PosixDirectoryFailureKindV1.permission_denied,
        errno.EPERM: PosixDirectoryFailureKindV1.permission_denied,
    }
    return _fail(mapping.get(exc.errno, PosixDirectoryFailureKindV1.io_unavailable), exc)


def _translate_acl_error_v1(exc: PosixAclError) -> PosixDirectoryError:
    mapping = {
        PosixAclFailureKindV1.platform_unsupported: PosixDirectoryFailureKindV1.platform_unsupported,
        PosixAclFailureKindV1.filesystem_unsupported: PosixDirectoryFailureKindV1.acl_unsupported,
        PosixAclFailureKindV1.malformed: PosixDirectoryFailureKindV1.acl_malformed,
        PosixAclFailureKindV1.permission_denied: PosixDirectoryFailureKindV1.permission_denied,
        PosixAclFailureKindV1.io_unavailable: PosixDirectoryFailureKindV1.io_unavailable,
    }
    return _fail(mapping[exc.kind], exc)


def _close_all_v1(descriptors: tuple[int, ...] | list[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


_CAPABILITY_SEAL_V1 = object()


class PosixDirectoryCapabilityV1:
    """A non-transferable read capability bound to an open directory chain."""

    __slots__ = (
        "_closed", "_components", "_descriptor", "_handles", "_name", "_owner_pid",
        "_parent", "_snapshots",
    )

    def __init__(
        self, handles: tuple[int, ...], components: tuple[str, ...],
        snapshots: tuple[PosixStatSnapshotV1, ...], *,
        _seal: object,
        parent: "PosixDirectoryCapabilityV1 | None" = None, name: str = "",
    ) -> None:
        if _seal is not _CAPABILITY_SEAL_V1:
            raise TypeError("private directory capability constructor")
        self._closed = False
        self._components, self._handles, self._snapshots = components, handles, snapshots
        self._descriptor, self._owner_pid = handles[-1], os.getpid()
        self._parent, self._name = parent, name

    def __enter__(self) -> "PosixDirectoryCapabilityV1":
        self.assert_bound()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _deny_transfer(self, *_args: object) -> None:
        raise TypeError("directory capabilities cannot be transferred")

    __copy__ = __deepcopy__ = __reduce__ = __reduce_ex__ = _deny_transfer

    def _require_live_v1(self) -> None:
        if self._closed:
            raise _fail(PosixDirectoryFailureKindV1.capability_closed)
        if os.getpid() != self._owner_pid:
            raise _fail(PosixDirectoryFailureKindV1.process_changed)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _close_all_v1(self._handles)

    def assert_bound(self) -> None:
        self._require_live_v1()
        try:
            if self._parent is not None:
                self._assert_child_bound_v1()
                return
            flags = _flags_v1()
            for descriptor, expected in zip(self._handles, self._snapshots):
                if not expected.same_object_as(snapshot_fd_v1(descriptor)):
                    raise _fail(PosixDirectoryFailureKindV1.binding_changed)
            for index, name in enumerate(self._components, start=1):
                rebound = os.open(name, flags, dir_fd=self._handles[index - 1])
                try:
                    if not self._snapshots[index].same_object_as(snapshot_fd_v1(rebound)):
                        raise _fail(PosixDirectoryFailureKindV1.binding_changed)
                finally:
                    os.close(rebound)
        except PosixDirectoryError:
            raise
        except OSError as exc:
            raise _translate_os_error_v1(exc) from exc

    def _assert_child_bound_v1(self) -> None:
        assert self._parent is not None
        self._parent.assert_bound()
        expected = self._snapshots[-1]
        if not expected.same_object_as(snapshot_fd_v1(self._descriptor)):
            raise _fail(PosixDirectoryFailureKindV1.binding_changed)
        rebound = os.open(self._name, _flags_v1(), dir_fd=self._parent._descriptor)
        try:
            if not expected.same_object_as(snapshot_fd_v1(rebound)):
                raise _fail(PosixDirectoryFailureKindV1.binding_changed)
        finally:
            os.close(rebound)
        self._parent.assert_bound()

    def observe_self(self) -> PosixDirectoryObservationV1:
        self.assert_bound()
        try:
            before = snapshot_fd_v1(self._descriptor)
            acl = observe_posix_directory_acl_v1(self._descriptor)
            after = snapshot_fd_v1(self._descriptor)
        except PosixAclError as exc:
            raise _translate_acl_error_v1(exc) from exc
        except OSError as exc:
            raise _translate_os_error_v1(exc) from exc
        if not stat.S_ISDIR(after.mode):
            raise _fail(PosixDirectoryFailureKindV1.not_directory)
        if before != after:
            raise _fail(PosixDirectoryFailureKindV1.binding_changed)
        self.assert_bound()
        return PosixDirectoryObservationV1(after, acl)

    def _child_stat_v1(self, name: str) -> PosixStatSnapshotV1 | None:
        try:
            return snapshot_stat_v1(os.stat(
                name, dir_fd=self._descriptor, follow_symlinks=False,
            ))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _translate_os_error_v1(exc) from exc

    def observe_child(self, name: str) -> PosixChildObservationV1:
        name = _component_v1(name)
        self.assert_bound()
        before = self._child_stat_v1(name)
        if before is None:
            if self._child_stat_v1(name) is not None:
                raise _fail(PosixDirectoryFailureKindV1.binding_changed)
            self.assert_bound()
            return PosixChildObservationV1(name, PosixNodeKindV1.missing, None, None)
        if stat.S_ISDIR(before.mode):
            with self.open_child_directory(name) as child:
                observed = child.observe_self()
            return PosixChildObservationV1(
                name, PosixNodeKindV1.directory, observed.metadata, observed.acl,
            )
        if before != self._child_stat_v1(name):
            raise _fail(PosixDirectoryFailureKindV1.binding_changed)
        self.assert_bound()
        return PosixChildObservationV1(name, PosixNodeKindV1.other, before, None)

    def open_child_directory(self, name: str) -> "PosixDirectoryCapabilityV1":
        name = _component_v1(name)
        self.assert_bound()
        before = self._child_stat_v1(name)
        if before is None:
            raise _fail(PosixDirectoryFailureKindV1.not_found)
        if stat.S_ISLNK(before.mode):
            raise _fail(PosixDirectoryFailureKindV1.symlink_refused)
        if not stat.S_ISDIR(before.mode):
            raise _fail(PosixDirectoryFailureKindV1.not_directory)
        child = None
        try:
            child = os.open(name, _flags_v1(), dir_fd=self._descriptor)
            observed = snapshot_fd_v1(child)
            if not before.same_object_as(observed):
                raise _fail(PosixDirectoryFailureKindV1.binding_changed)
            result = PosixDirectoryCapabilityV1(
                (child,), self._components + (name,), (observed,),
                _seal=_CAPABILITY_SEAL_V1, parent=self, name=name,
            )
            result.assert_bound()
            child = None
            return result
        except PosixDirectoryError:
            raise
        except OSError as exc:
            raise _translate_os_error_v1(exc) from exc
        finally:
            if child is not None:
                os.close(child)


def open_posix_directory_v1(path: PurePosixPath) -> PosixDirectoryCapabilityV1:
    """Open a normalized absolute path from the root descriptor."""
    flags, components = _flags_v1(), _absolute_components_v1(path)
    handles: list[int] = []
    snapshots: list[PosixStatSnapshotV1] = []
    try:
        current = os.open("/", flags)
        handles.append(current)
        snapshots.append(snapshot_fd_v1(current))
        for name in components:
            before = os.stat(name, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise _fail(PosixDirectoryFailureKindV1.symlink_refused)
            current = os.open(name, flags, dir_fd=current)
            observed = snapshot_fd_v1(current)
            if not snapshot_stat_v1(before).same_object_as(observed):
                raise _fail(PosixDirectoryFailureKindV1.binding_changed)
            handles.append(current)
            snapshots.append(observed)
        result = PosixDirectoryCapabilityV1(
            tuple(handles), components, tuple(snapshots),
            _seal=_CAPABILITY_SEAL_V1,
        )
        result.assert_bound()
        return result
    except PosixDirectoryError:
        _close_all_v1(handles)
        raise
    except OSError as exc:
        _close_all_v1(handles)
        raise _translate_os_error_v1(exc) from exc


__all__ = [
    "PosixAclSnapshotV1", "PosixChildObservationV1",
    "PosixDirectoryCapabilityV1", "PosixDirectoryError",
    "PosixDirectoryFailureKindV1", "PosixDirectoryObservationV1",
    "PosixNodeKindV1", "open_posix_directory_v1",
]
