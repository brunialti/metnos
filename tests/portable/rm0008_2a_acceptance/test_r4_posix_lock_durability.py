from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ._support import (
    assert_posix_security,
    lock_role_binding,
    make_root,
    open_session,
    private_role,
    role_binding,
    secure_fs,
    tree_snapshot,
    waitpid_killed,
    write_public,
)


CASES = ("empty-lock-fsync-order", "empty-lock-kill-and-recover")


def _fixture_bindings(module):
    return (
        lock_role_binding(module),
        role_binding(
            module, ("store",), directory=True, role=private_role(module)
        ),
        role_binding(
            module,
            ("store", "birth-keystore.lock"),
            directory=False,
            role=private_role(module),
        ),
    )


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_empty_global_lock_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root = make_root(tmp_path / "birth")
    lock_path = root / "provisioning-v1.lock"
    if case == "empty-lock-fsync-order":
        write_public(lock_path, b"")
        before = tree_snapshot(root)
        module = secure_fs()
        with monkeypatch.context() as immediate:
            immediate.setattr(
                module.time,
                "sleep",
                lambda *_: (_ for _ in ()).throw(
                    AssertionError("invalid lock state was retried")
                ),
            )
            with open_session(
                root, role_bindings=_fixture_bindings(module)
            ) as reader:
                with pytest.raises(module.BirthSecureFSError) as caught:
                    with reader.global_lock(exclusive=False, create=False):
                        pass
            assert caught.value.code == "birth_provisioning_lock_unsafe"
        assert tree_snapshot(root) == before
        events: list[tuple[str, tuple[int, int]]] = []
        real_write, real_fsync = os.write, os.fsync

        def traced_write(fd: int, payload: bytes) -> int:
            events.append(("write", (os.fstat(fd).st_dev, os.fstat(fd).st_ino)))
            return real_write(fd, payload)

        def traced_fsync(fd: int) -> None:
            events.append(("fsync", (os.fstat(fd).st_dev, os.fstat(fd).st_ino)))
            return real_fsync(fd)

        monkeypatch.setattr(os, "write", traced_write)
        monkeypatch.setattr(os, "fsync", traced_fsync)
        with open_session(
            root, role_bindings=_fixture_bindings(module)
        ) as session:
            with session.global_lock(exclusive=True, create=True):
                pass
        file_id = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        root_id = (root.stat().st_dev, root.stat().st_ino)
        assert events.index(("write", file_id)) < events.index(("fsync", file_id))
        assert events.index(("fsync", file_id)) < events.index(("fsync", root_id))
        assert lock_path.read_bytes() == b"0"
        assert_posix_security(lock_path, directory=False, mode=0o644)
        with open_session(
            root, role_bindings=_fixture_bindings(module)
        ) as ordered:
            with ordered.global_lock(exclusive=True, create=False):
                ordered.create_directory_exclusive(
                    ("store",), role=private_role(module)
                )
                ordered.create_file_exclusive(
                    ("store", "birth-keystore.lock"),
                    b"0",
                    role=private_role(module),
                )
            before_rejections = tree_snapshot(root)
            with pytest.raises(module.BirthSecureFSError) as caught:
                with ordered.local_lock(("store",), exclusive=False, create=False):
                    pass
            assert caught.value.code == "birth_provisioning_lock_unsafe"
            with ordered.global_lock(exclusive=False, create=False):
                with ordered.local_lock(
                    ("store",), exclusive=False, create=False
                ):
                    with pytest.raises(module.BirthSecureFSError) as caught:
                        with ordered.global_lock(exclusive=False, create=False):
                            pass
                    assert caught.value.code == "birth_provisioning_lock_unsafe"
            assert tree_snapshot(root) == before_rejections
        return

    assert not lock_path.exists()
    module = secure_fs()
    with monkeypatch.context() as immediate:
        immediate.setattr(
            module.time,
            "sleep",
            lambda *_: (_ for _ in ()).throw(
                AssertionError("missing lock was retried")
            ),
        )
        with open_session(
            root, role_bindings=_fixture_bindings(module)
        ) as reader:
            with pytest.raises(module.BirthSecureFSError) as caught:
                with reader.global_lock(exclusive=False, create=False):
                    pass
        assert caught.value.code == "birth_provisioning_lock_unavailable"
    assert not lock_path.exists()

    pid = os.fork()
    if pid == 0:
        real_open = os.open

        def kill_after_exclusive_create(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == "provisioning-v1.lock" and dir_fd is not None:
                assert flags & os.O_CREAT and flags & os.O_EXCL
                assert flags & os.O_NOFOLLOW
                assert stat.S_IMODE(mode) == 0o644
                os.kill(os.getpid(), 9)
            return fd

        os.open = kill_after_exclusive_create
        with open_session(
            root, role_bindings=_fixture_bindings(module)
        ) as session:
            with session.global_lock(exclusive=True, create=True):
                pass
        os._exit(70)
    waitpid_killed(pid)
    assert lock_path.read_bytes() == b""
    assert_posix_security(lock_path, directory=False, mode=0o644)
    with open_session(
        root, role_bindings=_fixture_bindings(module)
    ) as session:
        with session.global_lock(exclusive=True, create=True):
            assert lock_path.read_bytes() == b"0"
    assert lock_path.read_bytes() == b"0"
    assert_posix_security(lock_path, directory=False, mode=0o644)
