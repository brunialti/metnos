from __future__ import annotations

import errno
import os
import select
import time
from pathlib import Path

import pytest

from ._support import (
    birth_keystore_role_bindings,
    lock_role_binding,
    make_root,
    mkdir_public,
    open_session,
    provision_birth_keystore,
    public_role,
    role_binding,
    secure_fs,
    tree_snapshot,
)


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
        original_sleep = time.sleep
        reported = False
        delays: list[float] = []

        def observed_sleep(delay: float) -> None:
            delays.append(delay)
            original_sleep(delay)

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
        time.sleep = observed_sleep
        module = secure_fs()
        with open_session(
            root, role_bindings=(lock_role_binding(module),)
        ) as session:
            started = time.monotonic()
            with session.global_lock(exclusive=exclusive, create=False, timeout=2.0):
                elapsed = time.monotonic() - started
                encoded_delays = ",".join(f"{item:.3f}" for item in delays)
                os.write(
                    result, f"{elapsed:.9f}|{encoded_delays}".encode("ascii")
                )
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


def _wait_success(pid: int, result_fd: int) -> tuple[float, tuple[float, ...]]:
    value = os.read(result_fd, 128)
    os.close(result_fd)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    elapsed_text, delays_text = value.decode("ascii").split("|", 1)
    delays = tuple(float(item) for item in delays_text.split(",") if item)
    return float(elapsed_text), delays


def _assert_posix_retry_failure_contract(module, root: Path, lock_bindings) -> None:
    import fcntl

    with open_session(root, role_bindings=lock_bindings) as active:
        non_retry_attempts = 0

        def non_retryable(fd: int, operation: int) -> None:
            nonlocal non_retry_attempts
            if operation & fcntl.LOCK_UN:
                return
            non_retry_attempts += 1
            raise OSError(errno.EBADF, "forced non-retryable lock error")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(fcntl, "flock", non_retryable)
            patch.setattr(
                time,
                "sleep",
                lambda delay: (_ for _ in ()).throw(
                    AssertionError("non-retryable POSIX lock error slept")
                ),
            )
            with pytest.raises(module.BirthSecureFSError) as caught:
                with active.global_lock(
                    exclusive=False, create=False, timeout=1.0
                ):
                    pass
        assert caught.value.code == "birth_provisioning_lock_unsafe"
        assert non_retry_attempts == 1

        clock = [300.0]
        sleeps: list[float] = []
        attempts = 0

        def monotonic() -> float:
            return clock[0]

        def sleep(delay: float) -> None:
            sleeps.append(delay)
            clock[0] += delay

        def permanent_contention(fd: int, operation: int) -> None:
            nonlocal attempts
            if operation & fcntl.LOCK_UN:
                return
            attempts += 1
            raise OSError(errno.EAGAIN, "forced permanent contention")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(fcntl, "flock", permanent_contention)
            patch.setattr(time, "monotonic", monotonic)
            patch.setattr(time, "sleep", sleep)
            with pytest.raises(module.BirthSecureFSError) as caught:
                with active.global_lock(
                    exclusive=False, create=False, timeout=0.012
                ):
                    pass
        assert caught.value.code == "birth_provisioning_lock_unavailable"
        assert attempts == 3
        assert sleeps == [0.005, pytest.approx(0.007)]


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_lock_interleaving(tmp_path: Path, case: str) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    lock_bindings = (lock_role_binding(module),)
    with open_session(root, role_bindings=lock_bindings) as initializer:
        with initializer.global_lock(exclusive=True, create=True):
            pass
    baseline = tree_snapshot(root)

    if case == "killed-holder-releases":
        ready_r, ready_w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(ready_r)
            with open_session(root, role_bindings=lock_bindings) as holder:
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
        with open_session(root, role_bindings=lock_bindings) as recovered:
            with recovered.global_lock(exclusive=True, create=False, timeout=1.0):
                pass
        assert tree_snapshot(root) == baseline
        return

    if case == "loader-blocks-provisioner-before-mutation":
        set_id = "0" * 64
        authority_sets = root / "authority-sets"
        authority_set = authority_sets / set_id
        mkdir_public(authority_sets)
        mkdir_public(authority_set)
        store = authority_set / "admission"
        store_components = ("authority-sets", set_id, "admission")
        key_id = provision_birth_keystore(store)
        loader_bindings = (
            *lock_bindings,
            role_binding(
                module,
                ("authority-sets",),
                directory=True,
                role=public_role(module),
            ),
            role_binding(
                module,
                ("authority-sets", set_id),
                directory=True,
                role=public_role(module),
            ),
            *birth_keystore_role_bindings(module, store_components, key_id),
        )
        before = tree_snapshot(root)
        with open_session(root, role_bindings=loader_bindings) as blocker:
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
                        with open_session(
                            root, role_bindings=loader_bindings
                        ) as reader:
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
                    assert tree_snapshot(root) == before
                assert os.read(loader_done_r, 1) == b"L"
                os.close(loader_done_r)
                waited, status = os.waitpid(loader_pid, 0)
                assert waited == loader_pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        elapsed, delays = _wait_success(provisioner_pid, provisioner_result)
        assert elapsed >= 0.10
        canonical = (0.005, 0.010, 0.020, 0.040, 0.080, 0.100)
        assert delays
        assert delays[:6] == canonical[: min(len(delays), 6)]
        assert all(delay == 0.100 for delay in delays[6:])
        assert tree_snapshot(root) == before
        return

    parent_exclusive, child_exclusive = {
        "shared-shared": (False, False),
        "shared-exclusive": (False, True),
        "exclusive-shared": (True, False),
    }[case]
    with open_session(root, role_bindings=lock_bindings) as parent:
        before = tree_snapshot(root)
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
        elapsed, delays = _wait_success(pid, result_fd)
        assert tree_snapshot(root) == before
    if case == "shared-shared":
        assert delays == ()
        _assert_posix_retry_failure_contract(module, root, lock_bindings)
    else:
        assert elapsed >= 0.10
        canonical = (0.005, 0.010, 0.020, 0.040, 0.080, 0.100)
        assert delays
        assert delays[:6] == canonical[: min(len(delays), 6)]
        assert all(delay == 0.100 for delay in delays[6:])
