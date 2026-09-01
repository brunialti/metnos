#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Exclusive owner of the fixed executor-startup gate.

Ordinary launches already take a shared lock in the installed preflight.  The
transition takes the exclusive side and carries a live, non-transferable
session into the dominant-startup wrapper.  This module never creates the gate:
installation owns that durable/runtime object and this owner only opens it.
"""
from __future__ import annotations

import os
import stat
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from executor_birth_admin_preflight import STARTUP_GATE_PATH_V1

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is denied before I/O
    fcntl = None  # type: ignore[assignment]


class StartupGateError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(detail: str = "") -> StartupGateError:
    return StartupGateError("birth_ownership_startup_gate_invalid", detail)


_PRODUCT_SESSION_SEAL_V1 = object()
_TEST_SESSION_SEAL_V1 = object()
_SESSION_GUARD_V1 = threading.Lock()
_ACTIVE_SESSIONS_V1: dict[object, object] = {}


class _ExclusiveStartupGateSessionV1:
    __slots__ = (
        "_token", "_descriptor", "_owner_process", "_path", "_owner",
        "_seal", "_active",
    )

    def __init__(
        self, token: object, descriptor: int, path: Path,
        owner: tuple[int, int], seal: object,
    ) -> None:
        if seal not in {_PRODUCT_SESSION_SEAL_V1, _TEST_SESSION_SEAL_V1}:
            raise _invalid("session seal")
        self._token = token
        self._descriptor = descriptor
        self._owner_process = os.getpid()
        self._path = path
        self._owner = owner
        self._seal = seal
        self._active = True

    def __copy__(self):
        raise TypeError("startup gate sessions cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("startup gate sessions cannot be copied")

    def __reduce__(self):
        raise TypeError("startup gate sessions cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("startup gate sessions cannot be serialized")


class _ExclusiveStartupGateSessionForTestV1(_ExclusiveStartupGateSessionV1):
    __slots__ = ()


def _metadata_identity_v1(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_uid, info.st_gid, info.st_size,
    )


def _require_gate_metadata_v1(
    path: Path, owner: tuple[int, int], *, product: bool,
) -> os.stat_result:
    if not path.is_absolute() or (product and path != STARTUP_GATE_PATH_V1):
        raise _invalid("path")
    try:
        parent = path.parent.lstat()
        gate = path.lstat()
    except OSError as exc:
        raise _invalid("missing") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or (parent.st_uid, parent.st_gid) != owner
        or stat.S_IMODE(parent.st_mode) != 0o700
        or not stat.S_ISREG(gate.st_mode)
        or stat.S_ISLNK(gate.st_mode)
        or (gate.st_uid, gate.st_gid) != owner
        or gate.st_nlink != 1
        or stat.S_IMODE(gate.st_mode) != 0o600
    ):
        raise _invalid("metadata")
    return gate


def _open_exclusive_v1(
    path: Path, owner: tuple[int, int], *, product: bool,
) -> tuple[int, os.stat_result]:
    if not sys.platform.startswith("linux") or fcntl is None:
        raise StartupGateError("birth_ownership_platform_unsupported")
    before = _require_gate_metadata_v1(path, owner, product=product)
    descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _metadata_identity_v1(opened) != _metadata_identity_v1(before):
            raise _invalid("replaced")
        deadline = time.monotonic() + 30.0
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StartupGateError(
                        "birth_ownership_startup_gate_busy",
                    )
                time.sleep(0.01)
        after = _require_gate_metadata_v1(path, owner, product=product)
        if _metadata_identity_v1(after) != _metadata_identity_v1(before):
            raise _invalid("changed")
        return descriptor, opened
    except BaseException:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        raise


def _require_session_core_v1(
    session: object, *, seal: object, path: Path,
    owner: tuple[int, int], exact_type: type,
) -> None:
    if type(session) is not exact_type:
        raise _invalid("session type")
    with _SESSION_GUARD_V1:
        registered = _ACTIVE_SESSIONS_V1.get(session._token)
    if (
        session._seal is not seal
        or registered is not session
        or not session._active
        or session._owner_process != os.getpid()
        or session._path != path
        or session._owner != owner
    ):
        raise _invalid("session authority")
    try:
        opened = os.fstat(session._descriptor)
        observed = _require_gate_metadata_v1(
            path, owner, product=seal is _PRODUCT_SESSION_SEAL_V1,
        )
    except OSError as exc:
        raise _invalid("session descriptor") from exc
    if _metadata_identity_v1(opened) != _metadata_identity_v1(observed):
        raise _invalid("session changed")


def _require_exclusive_startup_gate_session_v1(
    session: _ExclusiveStartupGateSessionV1,
) -> None:
    _require_session_core_v1(
        session,
        seal=_PRODUCT_SESSION_SEAL_V1,
        path=STARTUP_GATE_PATH_V1,
        owner=(0, 0),
        exact_type=_ExclusiveStartupGateSessionV1,
    )


def _require_exclusive_startup_gate_session_for_test_v1(
    session: _ExclusiveStartupGateSessionForTestV1, path: Path,
) -> None:
    _require_session_core_v1(
        session,
        seal=_TEST_SESSION_SEAL_V1,
        path=Path(path),
        owner=(os.geteuid(), os.getegid()),
        exact_type=_ExclusiveStartupGateSessionForTestV1,
    )


@contextmanager
def _exclusive_startup_gate_at_v1(
    path: Path, owner: tuple[int, int], *, product: bool,
) -> Iterator[_ExclusiveStartupGateSessionV1]:
    descriptor, _opened = _open_exclusive_v1(
        path, owner, product=product,
    )
    token = object()
    session_type = (
        _ExclusiveStartupGateSessionV1
        if product else _ExclusiveStartupGateSessionForTestV1
    )
    seal = _PRODUCT_SESSION_SEAL_V1 if product else _TEST_SESSION_SEAL_V1
    session = session_type(token, descriptor, path, owner, seal)
    with _SESSION_GUARD_V1:
        _ACTIVE_SESSIONS_V1[token] = session
    try:
        yield session
    finally:
        with _SESSION_GUARD_V1:
            session._active = False
            _ACTIVE_SESSIONS_V1.pop(token, None)
        failed = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            failed = True
        try:
            os.close(descriptor)
        except OSError:
            failed = True
        if failed:
            raise StartupGateError(
                "birth_ownership_startup_gate_release_failed",
            )


@contextmanager
def _exclusive_startup_gate_v1(
) -> Iterator[_ExclusiveStartupGateSessionV1]:
    with _exclusive_startup_gate_at_v1(
        STARTUP_GATE_PATH_V1, (0, 0), product=True,
    ) as session:
        yield session


@contextmanager
def _exclusive_startup_gate_for_test_v1(
    path: Path,
) -> Iterator[_ExclusiveStartupGateSessionForTestV1]:
    with _exclusive_startup_gate_at_v1(
        Path(path), (os.geteuid(), os.getegid()), product=False,
    ) as session:
        assert type(session) is _ExclusiveStartupGateSessionForTestV1
        yield session


__all__ = ["StartupGateError"]
