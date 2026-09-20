from __future__ import annotations

import hashlib
import multiprocessing
import os
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
)


CASES = ("dispose-crash-before-native", "dispose-crash-after-native")


def _fixture_bindings(module):
    return (
        lock_role_binding(module),
        role_binding(
            module,
            ("victim.bin",),
            directory=False,
            role=private_role(module),
        ),
    )


def _expectation(module, identity):
    return module._DisposalExpectation(
        components=("victim.bin",),
        identity=identity,
        kind=module._ObjectKind("regular_file"),
        role=private_role(module),
        disposal_class=module._DisposalClass("complete_file"),
        links=1,
        expected_size=6,
        maximum_partial_size=None,
        content_sha256="sha256:" + hashlib.sha256(b"victim").hexdigest(),
        inventory=None,
    )


def _crashing_disposition(root: Path, case: str, channel) -> None:
    module = secure_fs()
    with open_session(root, role_bindings=_fixture_bindings(module)) as session:
        with session.global_lock(exclusive=True, create=True):
            identity = session.create_file_exclusive(
                ("victim.bin",), b"victim", role=private_role(module)
            )
            channel.send((identity, tree_snapshot(root)))
            channel.close()
            expectation = _expectation(module, identity)
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
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    receiver, sender = multiprocessing.Pipe(duplex=False)
    pid = os.fork()
    if pid == 0:
        receiver.close()
        _crashing_disposition(root, case, sender)
    sender.close()
    identity, baseline = receiver.recv()
    receiver.close()
    waitpid_killed(pid)
    if case == "dispose-crash-before-native":
        assert tree_snapshot(root) == baseline
        assert (root / "victim.bin").read_bytes() == b"victim"
        assert_posix_security(root / "victim.bin", directory=False, mode=0o600)
    else:
        after = tree_snapshot(root)
        baseline_by_name = {row[0]: row for row in baseline}
        after_by_name = {row[0]: row for row in after}
        assert set(after_by_name) == {".", "provisioning-v1.lock"}
        assert (
            after_by_name["provisioning-v1.lock"]
            == baseline_by_name["provisioning-v1.lock"]
        )
        assert after_by_name["."][1:8] == baseline_by_name["."][1:8]
    before_retry = tree_snapshot(root)
    with open_session(root, role_bindings=_fixture_bindings(module)) as retry:
        with retry.global_lock(exclusive=True, create=False):
            if case == "dispose-crash-before-native":
                result = retry.dispose_transaction_object(
                    _expectation(module, identity)
                )
                assert result.identity == identity
                assert result.kind == module._ObjectKind("regular_file")
                assert result.removed is True
            else:
                with pytest.raises(module.BirthSecureFSError) as caught:
                    retry.dispose_transaction_object(
                        _expectation(module, identity)
                    )
                assert caught.value.code == "birth_provisioning_recovery_ambiguous"
    if case == "dispose-crash-before-native":
        expected_after = tuple(
            row for row in before_retry if row[0] != "victim.bin"
        )
        after_retry = tree_snapshot(root)
        assert {row[0] for row in after_retry} == {
            ".",
            "provisioning-v1.lock",
        }
        assert after_retry[1:] == expected_after[1:]
    else:
        assert tree_snapshot(root) == before_retry
