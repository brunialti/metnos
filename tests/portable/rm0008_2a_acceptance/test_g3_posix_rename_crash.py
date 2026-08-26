from __future__ import annotations

import os
from pathlib import Path

import pytest

from ._support import (
    assert_posix_security,
    lock_role_binding,
    make_root,
    object_identity,
    open_session,
    private_role,
    role_binding,
    secure_fs,
    tree_snapshot,
    waitpid_killed,
)


CASES = ("rename-crash-before-native", "rename-crash-after-native")


def _fixture_bindings(module):
    return (
        lock_role_binding(module),
        role_binding(
            module, ("source",), directory=True, role=private_role(module)
        ),
        role_binding(
            module, ("target",), directory=True, role=private_role(module)
        ),
        role_binding(
            module,
            ("source", "payload.bin"),
            directory=False,
            role=private_role(module),
        ),
        role_binding(
            module,
            ("target", "payload.bin"),
            directory=False,
            role=private_role(module),
        ),
    )


def _kill_at_native_rename(root: Path, case: str) -> None:
    module = secure_fs()
    real_rename = module._renameat2_no_replace

    def intercepted_rename(source_fd, source_name, target_fd, target_name):
        assert source_name == "payload.bin"
        assert target_name == "payload.bin"
        if case == "rename-crash-before-native":
            os.kill(os.getpid(), 9)
        result = real_rename(source_fd, source_name, target_fd, target_name)
        os.kill(os.getpid(), 9)
        return result

    module._renameat2_no_replace = intercepted_rename
    with open_session(root, role_bindings=_fixture_bindings(module)) as session:
        with session.global_lock(exclusive=True, create=False):
            session.rename_no_replace(
                ("source", "payload.bin"),
                ("target", "payload.bin"),
                directory=False,
            )
    os._exit(72)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_rename_crash_boundary(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    source_directory = root / "source"
    target_directory = root / "target"
    source = source_directory / "payload.bin"
    target = target_directory / "payload.bin"
    with open_session(root, role_bindings=_fixture_bindings(module)) as session:
        with session.global_lock(exclusive=True, create=True):
            session.create_directory_exclusive(
                ("source",), role=private_role(module)
            )
            session.create_directory_exclusive(
                ("target",), role=private_role(module)
            )
            identity = session.create_file_exclusive(
                ("source", "payload.bin"), b"rename", role=private_role(module)
            )

    baseline = tree_snapshot(root)
    pid = os.fork()
    if pid == 0:
        _kill_at_native_rename(root, case)
    waitpid_killed(pid)

    assert_posix_security(source_directory, directory=True, mode=0o700)
    assert_posix_security(target_directory, directory=True, mode=0o700)
    if case == "rename-crash-before-native":
        assert tree_snapshot(root) == baseline
        assert object_identity(source, module) == identity
        assert source.read_bytes() == b"rename"
        assert not target.exists()
        with open_session(root, role_bindings=_fixture_bindings(module)) as retry:
            with retry.global_lock(exclusive=True, create=False):
                result = retry.rename_no_replace(
                    ("source", "payload.bin"),
                    ("target", "payload.bin"),
                    directory=False,
                )
        assert result == identity
        assert not source.exists()
        assert object_identity(target, module) == identity
        assert target.read_bytes() == b"rename"
        assert_posix_security(target, directory=False, mode=0o600)
        return

    assert not source.exists()
    assert target.read_bytes() == b"rename"
    assert object_identity(target, module) == identity
    assert_posix_security(target, directory=False, mode=0o600)
    after_crash = tree_snapshot(root)
    baseline_by_name = {row[0]: row for row in baseline}
    after_by_name = {row[0]: row for row in after_crash}
    assert set(after_by_name) == {
        ".",
        "provisioning-v1.lock",
        "source",
        "target",
        "target/payload.bin",
    }
    assert after_by_name["."] == baseline_by_name["."]
    assert (
        after_by_name["provisioning-v1.lock"]
        == baseline_by_name["provisioning-v1.lock"]
    )
    for directory_name in ("source", "target"):
        assert after_by_name[directory_name][1:8] == baseline_by_name[
            directory_name
        ][1:8]
    before_retry = tree_snapshot(root)
    with open_session(root, role_bindings=_fixture_bindings(module)) as retry:
        with retry.global_lock(exclusive=True, create=False):
            real_open = os.open
            target_parent_identity = (
                target_directory.stat().st_dev,
                target_directory.stat().st_ino,
            )
            observed_target = []

            def observe_target(path, flags, mode=0o777, *, dir_fd=None):
                result = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "payload.bin" and dir_fd is not None:
                    parent = os.fstat(dir_fd)
                    if (parent.st_dev, parent.st_ino) == target_parent_identity:
                        observed = module._posix_identity(result)
                        if observed != identity:
                            raise AssertionError(
                                "rename retry observed a different target identity"
                            )
                        observed_target.append(observed)
                return result

            monkeypatch.setattr(os, "open", observe_target)
            with pytest.raises(module.BirthSecureFSError) as caught:
                retry.rename_no_replace(
                    ("source", "payload.bin"),
                    ("target", "payload.bin"),
                    directory=False,
                )
    assert caught.value.code == "birth_provisioning_recovery_ambiguous"
    assert observed_target == [identity]
    assert tree_snapshot(root) == before_retry
