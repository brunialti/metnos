from __future__ import annotations

import ctypes
import errno
import os
import stat
from pathlib import Path

import pytest

from ._support import make_root, open_session, private_role, secure_fs, waitpid_killed


CASES = (
    "short-write",
    "eintr-write",
    "file-fsync-error-state",
    "parent-fsync-error-state",
    "rename-two-parents-fsync",
    "rename-exdev",
    "rename-enosys",
    "renameat2-unavailable",
    "crash-created",
    "crash-partial",
    "crash-complete",
    "crash-file-fsync",
    "crash-parent-fsync",
)


def _create_lock(root: Path) -> None:
    with open_session(root) as session:
        with session.global_lock(exclusive=True, create=True):
            pass


class _FailingRenameAt2:
    def __init__(self) -> None:
        self.argtypes = None
        self.restype = None

    def __call__(self, *args) -> int:
        return -1


class _LibC:
    def __init__(self, *, expose: bool) -> None:
        if expose:
            self.renameat2 = _FailingRenameAt2()


def _prepare_rename(session, module) -> None:
    session.create_directory_exclusive(("source",), role=private_role(module))
    session.create_directory_exclusive(("target",), role=private_role(module))
    session.create_file_exclusive(
        ("source", "payload.bin"), b"rename", role=private_role(module)
    )


def _crashing_create(root: Path, case: str) -> None:
    module = secure_fs()
    with open_session(root) as session:
        with session.global_lock(exclusive=True, create=True):
            real_open, real_write, real_fsync = os.open, os.write, os.fsync
            target_fd: int | None = None
            root_identity = (root.stat().st_dev, root.stat().st_ino)

            def intercepted_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal target_fd
                result = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "crash.bin" and dir_fd is not None:
                    target_fd = result
                    if case == "crash-created":
                        os.kill(os.getpid(), 9)
                return result

            def intercepted_write(fd: int, payload: bytes) -> int:
                if fd == target_fd and case == "crash-partial":
                    real_write(fd, payload[: max(1, len(payload) // 2)])
                    os.kill(os.getpid(), 9)
                if fd == target_fd and case == "crash-complete":
                    result = real_write(fd, payload)
                    os.kill(os.getpid(), 9)
                    return result
                return real_write(fd, payload)

            def intercepted_fsync(fd: int) -> None:
                value = os.fstat(fd)
                identity = (value.st_dev, value.st_ino)
                if fd == target_fd and case == "crash-file-fsync":
                    real_fsync(fd)
                    os.kill(os.getpid(), 9)
                if identity == root_identity and target_fd is not None and case == "crash-parent-fsync":
                    real_fsync(fd)
                    os.kill(os.getpid(), 9)
                return real_fsync(fd)

            os.open = intercepted_open
            os.write = intercepted_write
            os.fsync = intercepted_fsync
            session.create_file_exclusive(
                ("crash.bin",), b"complete-payload", role=private_role(module)
            )
    os._exit(72)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_mutation_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    _create_lock(root)

    if case.startswith("crash-"):
        pid = os.fork()
        if pid == 0:
            _crashing_create(root, case)
        waitpid_killed(pid)
        path = root / "crash.bin"
        assert path.exists()
        payload = path.read_bytes()
        expected = b"complete-payload"
        if case == "crash-created":
            assert payload == b""
        elif case == "crash-partial":
            assert 0 < len(payload) < len(expected) and expected.startswith(payload)
        else:
            assert payload == expected
        assert sorted(item.name for item in root.iterdir()) == [
            "crash.bin",
            "provisioning-v1.lock",
        ]
        return

    with open_session(root) as session:
        if case in {"short-write", "eintr-write"}:
            real_write = os.write
            interrupted = False

            def altered_write(fd: int, payload: bytes) -> int:
                nonlocal interrupted
                if case == "eintr-write" and not interrupted:
                    interrupted = True
                    raise InterruptedError(errno.EINTR, "interrupted")
                if case == "short-write" and len(payload) > 1:
                    return real_write(fd, payload[: max(1, len(payload) // 3)])
                return real_write(fd, payload)

            monkeypatch.setattr(os, "write", altered_write)
            with session.global_lock(exclusive=True, create=False):
                session.create_file_exclusive(
                    ("payload.bin",), b"0123456789abcdef", role=private_role(module)
                )
            assert (root / "payload.bin").read_bytes() == b"0123456789abcdef"
            if case == "eintr-write":
                assert interrupted
            return

        if case in {"file-fsync-error-state", "parent-fsync-error-state"}:
            real_fsync = os.fsync
            root_identity = (root.stat().st_dev, root.stat().st_ino)
            raised = False

            def failing_fsync(fd: int) -> None:
                nonlocal raised
                value = os.fstat(fd)
                is_directory = stat.S_ISDIR(value.st_mode)
                should_fail = (
                    case == "file-fsync-error-state" and not is_directory
                ) or (
                    case == "parent-fsync-error-state"
                    and (value.st_dev, value.st_ino) == root_identity
                )
                if should_fail and not raised:
                    raised = True
                    raise OSError(errno.EIO, "injected fsync failure")
                return real_fsync(fd)

            monkeypatch.setattr(os, "fsync", failing_fsync)
            with pytest.raises(module.BirthSecureFSError) as caught:
                with session.global_lock(exclusive=True, create=False):
                    session.create_file_exclusive(
                        ("payload.bin",), b"durable", role=private_role(module)
                    )
            assert caught.value.code == "birth_provisioning_io_unavailable"
            assert raised
            assert (root / "payload.bin").read_bytes() == b"durable"
            return

        with session.global_lock(exclusive=True, create=False):
            _prepare_rename(session, module)
            if case == "rename-two-parents-fsync":
                real_fsync = os.fsync
                fsynced: list[tuple[int, int]] = []

                def traced_fsync(fd: int) -> None:
                    value = os.fstat(fd)
                    if stat.S_ISDIR(value.st_mode):
                        fsynced.append((value.st_dev, value.st_ino))
                    return real_fsync(fd)

                monkeypatch.setattr(os, "fsync", traced_fsync)
                session.rename_no_replace(
                    ("source", "payload.bin"),
                    ("target", "payload.bin"),
                    directory=False,
                )
                source_id = (root.joinpath("source").stat().st_dev, root.joinpath("source").stat().st_ino)
                target_id = (root.joinpath("target").stat().st_dev, root.joinpath("target").stat().st_ino)
                assert source_id in fsynced and target_id in fsynced
                assert (root / "target" / "payload.bin").read_bytes() == b"rename"
                return

            expose = case != "renameat2-unavailable"
            error = errno.EXDEV if case == "rename-exdev" else errno.ENOSYS
            monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: _LibC(expose=expose))
            monkeypatch.setattr(ctypes, "get_errno", lambda: error)
            with pytest.raises(module.BirthSecureFSError) as caught:
                session.rename_no_replace(
                    ("source", "payload.bin"),
                    ("target", "payload.bin"),
                    directory=False,
                )
            assert caught.value.code == "birth_provisioning_atomic_install_unsupported"
            assert (root / "source" / "payload.bin").read_bytes() == b"rename"
            assert not (root / "target" / "payload.bin").exists()
