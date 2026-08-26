from __future__ import annotations

import os
from pathlib import Path

import pytest

from ._support import (
    make_root,
    mkdir_private,
    open_session,
    private_role,
    secure_fs,
    write_private,
)


CASES = (
    "swap-after-root",
    "swap-after-first",
    "swap-after-middle",
    "swap-after-last",
    "swap-final-object",
)


def _read_exact(fd: int, length: int) -> bytes:
    value = bytearray()
    while len(value) < length:
        block = os.read(fd, length - len(value))
        if not block:
            raise AssertionError("barrier pipe closed")
        value.extend(block)
    return bytes(value)


def _worker(root: Path, case: str, ready_fd: int, resume_fd: int) -> None:
    try:
        module = secure_fs()
        session = open_session(root)
        original_open = os.open
        barrier_component = {
            "swap-after-first": "first",
            "swap-after-middle": "middle",
            "swap-after-last": "last",
            "swap-final-object": "payload.bin",
        }.get(case)
        fired = False

        def barrier_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal fired
            result = original_open(path, flags, mode, dir_fd=dir_fd)
            if path == barrier_component and dir_fd is not None and not fired:
                fired = True
                os.write(ready_fd, b"R")
                _read_exact(resume_fd, 1)
            return result

        os.open = barrier_open
        if case == "swap-after-root":
            os.write(ready_fd, b"R")
            _read_exact(resume_fd, 1)
        try:
            with session.global_lock(exclusive=False, create=False):
                payload = session.read_file(
                    ("first", "middle", "last", "payload.bin"),
                    maximum=64,
                    role=private_role(),
                )
            os.write(ready_fd, b"B" + payload.hex().encode("ascii") + b"\n")
        except module.BirthSecureFSError as exc:
            os.write(ready_fd, b"E" + exc.code.encode("ascii") + b"\n")
        except BaseException as exc:
            os.write(ready_fd, b"X" + type(exc).__name__.encode("ascii") + b"\n")
        finally:
            session.close()
    finally:
        os._exit(0)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_component_substitution(tmp_path: Path, case: str) -> None:
    root = make_root(tmp_path / "birth")
    first, middle, last = root / "first", root / "first/middle", root / "first/middle/last"
    mkdir_private(first)
    mkdir_private(middle)
    mkdir_private(last)
    write_private(last / "payload.bin", b"trusted")
    attacker = tmp_path / "attacker"
    mkdir_private(attacker)
    write_private(attacker / "payload.bin", b"malicious")
    with open_session(root) as initializer:
        with initializer.global_lock(exclusive=True, create=True):
            pass

    child_to_parent_r, child_to_parent_w = os.pipe()
    parent_to_child_r, parent_to_child_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(child_to_parent_r)
        os.close(parent_to_child_w)
        _worker(root, case, child_to_parent_w, parent_to_child_r)
    os.close(child_to_parent_w)
    os.close(parent_to_child_r)
    assert _read_exact(child_to_parent_r, 1) == b"R"

    next_path = {
        "swap-after-root": first,
        "swap-after-first": middle,
        "swap-after-middle": last,
        "swap-after-last": last / "payload.bin",
        "swap-final-object": last / "payload.bin",
    }[case]
    saved = next_path.with_name(next_path.name + ".original")
    next_path.rename(saved)
    if next_path.suffix == ".bin":
        write_private(next_path, b"malicious")
    else:
        next_path.symlink_to(attacker, target_is_directory=True)
    os.write(parent_to_child_w, b"C")
    os.close(parent_to_child_w)
    result = bytearray()
    while not result.endswith(b"\n"):
        block = os.read(child_to_parent_r, 256)
        if not block:
            break
        result.extend(block)
    os.close(child_to_parent_r)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert result.startswith((b"B", b"E"))
    assert result != b"B" + b"malicious".hex().encode("ascii") + b"\n"
    if result.startswith(b"B"):
        assert bytes.fromhex(result[1:-1].decode("ascii")) == b"trusted"
