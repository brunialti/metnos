from __future__ import annotations

import os
from pathlib import Path

import pytest

from ._support import make_root, open_session, waitpid_killed, write_public


CASES = ("empty-lock-fsync-order", "empty-lock-kill-and-recover")


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_empty_global_lock_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root = make_root(tmp_path / "birth")
    lock_path = root / "provisioning-v1.lock"
    write_public(lock_path, b"")
    if case == "empty-lock-fsync-order":
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
        with open_session(root) as session:
            with session.global_lock(exclusive=True, create=True):
                pass
        file_id = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        root_id = (root.stat().st_dev, root.stat().st_ino)
        assert events.index(("write", file_id)) < events.index(("fsync", file_id))
        assert events.index(("fsync", file_id)) < events.index(("fsync", root_id))
        assert lock_path.read_bytes() == b"0"
        return

    pid = os.fork()
    if pid == 0:
        real_write = os.write

        def kill_before_write(fd: int, payload: bytes) -> int:
            os.kill(os.getpid(), 9)
            return real_write(fd, payload)

        os.write = kill_before_write
        with open_session(root) as session:
            with session.global_lock(exclusive=True, create=True):
                pass
        os._exit(70)
    waitpid_killed(pid)
    assert lock_path.read_bytes() == b""
    with open_session(root) as session:
        with session.global_lock(exclusive=True, create=True):
            assert lock_path.read_bytes() == b"0"
    assert lock_path.read_bytes() == b"0"
