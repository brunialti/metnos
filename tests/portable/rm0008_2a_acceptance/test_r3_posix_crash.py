from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ._support import make_root, open_session, private_role, secure_fs, waitpid_killed


CASES = ("dispose-crash-before-native", "dispose-crash-after-native")


def _crashing_disposition(root: Path, case: str) -> None:
    module = secure_fs()
    with open_session(root) as session:
        with session.global_lock(exclusive=True, create=True):
            identity = session.create_file_exclusive(
                ("victim.bin",), b"victim", role=private_role(module)
            )
            expectation = module._DisposalExpectation(
                components=("victim.bin",),
                identity=identity,
                kind=module._ObjectKind("regular_file"),
                role=private_role(module),
                disposal_class=module._DisposalClass("complete_file"),
                links=1,
                expected_size=6,
                maximum_partial_size=None,
                content_sha256=hashlib.sha256(b"victim").hexdigest(),
                inventory=None,
            )
            real_unlink = os.unlink

            def intercepted_unlink(path, *, dir_fd=None):
                if path == "victim.bin" and dir_fd is not None:
                    if case == "dispose-crash-before-native":
                        os.kill(os.getpid(), 9)
                    result = real_unlink(path, dir_fd=dir_fd)
                    os.kill(os.getpid(), 9)
                    return result
                return real_unlink(path, dir_fd=dir_fd)

            os.unlink = intercepted_unlink
            session.dispose_transaction_object(expectation)
    os._exit(71)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_disposition_crash_boundary(tmp_path: Path, case: str) -> None:
    root = make_root(tmp_path / "birth")
    pid = os.fork()
    if pid == 0:
        _crashing_disposition(root, case)
    waitpid_killed(pid)
    names = sorted(path.name for path in root.iterdir())
    if case == "dispose-crash-before-native":
        assert names == ["provisioning-v1.lock", "victim.bin"]
        assert (root / "victim.bin").read_bytes() == b"victim"
    else:
        assert names == ["provisioning-v1.lock"]
