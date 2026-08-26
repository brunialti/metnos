from __future__ import annotations

import errno
import os
import select
import time
from pathlib import Path

import pytest

from ._support import make_root, mkdir_public, open_session, provision_keystore


CASES = (
    "shared-shared",
    "shared-exclusive",
    "exclusive-shared",
    "killed-holder-releases",
    "loader-blocks-provisioner-before-mutation",
)


def _child_lock(
    root: Path,
    exclusive: bool,
    ready: int,
    result: int,
    *,
    expect_contention: bool,
) -> None:
    try:
        import fcntl

        original_flock = fcntl.flock
        reported = False

        def observed_flock(fd: int, operation: int) -> object:
            nonlocal reported
            if not expect_contention and not reported and operation & fcntl.LOCK_NB:
                reported = True
                os.write(ready, b"R")
            try:
                return original_flock(fd, operation)
            except OSError as exc:
                if (
                    expect_contention
                    and not reported
                    and operation & fcntl.LOCK_NB
                    and exc.errno in {errno.EACCES, errno.EAGAIN}
                ):
                    reported = True
                    os.write(ready, b"R")
                raise

        fcntl.flock = observed_flock
        with open_session(root) as session:
            started = time.monotonic()
            with session.global_lock(exclusive=exclusive, create=False, timeout=2.0):
                elapsed = time.monotonic() - started
                os.write(result, f"{elapsed:.9f}".encode("ascii"))
    finally:
        os._exit(0)


def _spawn_lock(root: Path, exclusive: bool, *, expect_contention: bool):
    ready_r, ready_w = os.pipe()
    result_r, result_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        os.close(result_r)
        _child_lock(
            root,
            exclusive,
            ready_w,
            result_w,
            expect_contention=expect_contention,
        )
    os.close(ready_w)
    os.close(result_w)
    assert select.select([ready_r], [], [], 2.0)[0] == [ready_r]
    assert os.read(ready_r, 1) == b"R"
    os.close(ready_r)
    return pid, result_r


def _wait_success(pid: int, result_fd: int) -> float:
    value = os.read(result_fd, 128)
    os.close(result_fd)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    return float(value.decode("ascii"))


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_lock_interleaving(tmp_path: Path, case: str) -> None:
    root = make_root(tmp_path / "birth")
    with open_session(root) as initializer:
        with initializer.global_lock(exclusive=True, create=True):
            pass

    if case == "killed-holder-releases":
        ready_r, ready_w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(ready_r)
            with open_session(root) as holder:
                with holder.global_lock(exclusive=True, create=False):
                    os.write(ready_w, b"H")
                    select.select([], [], [], 30.0)
            os._exit(0)
        os.close(ready_w)
        assert os.read(ready_r, 1) == b"H"
        os.close(ready_r)
        os.kill(pid, 9)
        waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.WIFSIGNALED(status) and os.WTERMSIG(status) == 9
        with open_session(root) as recovered:
            with recovered.global_lock(exclusive=True, create=False, timeout=1.0):
                pass
        return

    if case == "loader-blocks-provisioner-before-mutation":
        set_id = "0" * 64
        authority_sets = root / "authority-sets"
        authority_set = authority_sets / set_id
        mkdir_public(authority_sets)
        mkdir_public(authority_set)
        store = authority_set / "admission"
        store_components = ("authority-sets", set_id, "admission")
        provision_keystore(store)
        before = tuple(sorted(path.name for path in root.iterdir()))
        with open_session(root) as blocker:
            with blocker.global_lock(exclusive=False, create=False):
                with blocker.local_lock(
                    store_components, exclusive=True, create=False
                ):
                    loader_ready_r, loader_ready_w = os.pipe()
                    loader_done_r, loader_done_w = os.pipe()
                    loader_pid = os.fork()
                    if loader_pid == 0:
                        os.close(loader_ready_r)
                        os.close(loader_done_r)
                        import fcntl

                        original_flock = fcntl.flock
                        reported = False

                        def observed_flock(fd: int, operation: int) -> object:
                            nonlocal reported
                            try:
                                return original_flock(fd, operation)
                            except OSError as exc:
                                if (
                                    not reported
                                    and operation & fcntl.LOCK_NB
                                    and exc.errno in {errno.EACCES, errno.EAGAIN}
                                ):
                                    reported = True
                                    os.write(loader_ready_w, b"G")
                                raise

                        fcntl.flock = observed_flock
                        with open_session(root) as reader:
                            with reader.global_lock(exclusive=False, create=False):
                                module = __import__("executor_birth_keystore")
                                module._load_birth_keystore_in_session(
                                    store_components, reader
                                )
                                os.write(loader_done_w, b"L")
                        os._exit(0)
                    os.close(loader_ready_w)
                    os.close(loader_done_w)
                    assert select.select([loader_ready_r], [], [], 2.0)[0] == [
                        loader_ready_r
                    ]
                    assert os.read(loader_ready_r, 1) == b"G"
                    os.close(loader_ready_r)
                    provisioner_pid, provisioner_result = _spawn_lock(
                        root, True, expect_contention=True
                    )
                    assert select.select([provisioner_result], [], [], 0.15)[0] == []
                    assert select.select([loader_done_r], [], [], 0.15)[0] == []
                    assert tuple(sorted(path.name for path in root.iterdir())) == before
                assert os.read(loader_done_r, 1) == b"L"
                os.close(loader_done_r)
                waited, status = os.waitpid(loader_pid, 0)
                assert waited == loader_pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        assert _wait_success(provisioner_pid, provisioner_result) >= 0.15
        assert tuple(sorted(path.name for path in root.iterdir())) == before
        return

    parent_exclusive, child_exclusive = {
        "shared-shared": (False, False),
        "shared-exclusive": (False, True),
        "exclusive-shared": (True, False),
    }[case]
    with open_session(root) as parent:
        with parent.global_lock(exclusive=parent_exclusive, create=False):
            pid, result_fd = _spawn_lock(
                root,
                child_exclusive,
                expect_contention=case != "shared-shared",
            )
            if case == "shared-shared":
                ready = select.select([result_fd], [], [], 2.0)[0]
                assert ready == [result_fd]
            else:
                ready = select.select([result_fd], [], [], 0.15)[0]
                assert ready == []
        elapsed = _wait_success(pid, result_fd)
    if case != "shared-shared":
        assert elapsed >= 0.15
