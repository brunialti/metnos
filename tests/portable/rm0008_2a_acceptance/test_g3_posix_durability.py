from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
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


def _fixture_bindings(module):
    return (
        lock_role_binding(module),
        role_binding(
            module,
            ("payload.bin",),
            directory=False,
            role=private_role(module),
        ),
        role_binding(
            module,
            ("crash.bin",),
            directory=False,
            role=private_role(module),
        ),
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


def _create_lock(root: Path) -> None:
    module = secure_fs()
    with open_session(root, role_bindings=_fixture_bindings(module)) as session:
        with session.global_lock(exclusive=True, create=True):
            pass


class _FailingRenameAt2:
    def __init__(
        self,
        *,
        source_parent: tuple[int, int],
        target_parent: tuple[int, int],
    ) -> None:
        self.argtypes = None
        self.restype = None
        self.source_parent = source_parent
        self.target_parent = target_parent
        self.calls = 0

    def __call__(self, *args) -> int:
        if len(args) != 5:
            raise AssertionError("renameat2 did not receive its five native arguments")
        source_fd, source_name, target_fd, target_name, flags = args
        scalar = lambda value: int(getattr(value, "value", value))
        source_value = os.fstat(scalar(source_fd))
        target_value = os.fstat(scalar(target_fd))
        if (
            (source_value.st_dev, source_value.st_ino) != self.source_parent
            or (target_value.st_dev, target_value.st_ino) != self.target_parent
            or source_name != b"payload.bin"
            or target_name != b"payload.bin"
            or scalar(flags) != 1  # Linux RENAME_NOREPLACE, independent literal.
        ):
            raise AssertionError("renameat2 did not use exact parent-relative NOREPLACE ABI")
        self.calls += 1
        return -1


class _LibC:
    def __init__(self, *, expose: bool, renameat2=None) -> None:
        if expose:
            if renameat2 is None:
                raise AssertionError("exposed renameat2 probe is absent")
            self.renameat2 = renameat2


def _prepare_rename(session, module) -> None:
    session.create_directory_exclusive(("source",), role=private_role(module))
    session.create_directory_exclusive(("target",), role=private_role(module))
    session.create_file_exclusive(
        ("source", "payload.bin"), b"rename", role=private_role(module)
    )


def _crashing_create(root: Path, case: str) -> None:
    module = secure_fs()
    with open_session(
        root, role_bindings=_fixture_bindings(module)
    ) as session:
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
        assert_posix_security(path, directory=False, mode=0o600)
        assert_posix_security(
            root / "provisioning-v1.lock", directory=False, mode=0o644
        )
        assert_posix_security(root, directory=True, mode=0o755)
        return

    with open_session(
        root, role_bindings=_fixture_bindings(module)
    ) as session:
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
            before_state = {
                row[0]: row for row in tree_snapshot(root)
            }
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
            # Section 16.13.1: any exception raised after the creation or the
            # write removes the new object, releases the reservation and leaves
            # the logical inventory unchanged.  A surviving payload would be a
            # half-committed object that no journal can classify.
            assert not (root / "payload.bin").exists()
            after_state = {
                row[0]: row for row in tree_snapshot(root)
            }
            assert set(after_state) == {".", "provisioning-v1.lock"}
            assert (
                after_state["provisioning-v1.lock"]
                == before_state["provisioning-v1.lock"]
            )
            assert after_state["."][1:8] == before_state["."][1:8]
            # The reservation must be released, so the same name is creatable
            # again through the session without a stale committed binding.
            with session.global_lock(exclusive=True, create=False):
                session.create_file_exclusive(
                    ("payload.bin",), b"durable", role=private_role(module)
                )
            assert (root / "payload.bin").read_bytes() == b"durable"
            assert_posix_security(root / "payload.bin", directory=False, mode=0o600)
            return

        with session.global_lock(exclusive=True, create=False):
            _prepare_rename(session, module)
            if case == "rename-two-parents-fsync":
                source_identity = object_identity(
                    root / "source" / "payload.bin", module
                )
                source_parent_before = session._inventory_state(("source",))
                target_parent_before = session._inventory_state(("target",))
                moved_entry = next(
                    item
                    for item in source_parent_before
                    if item.name == "payload.bin"
                )
                expected_source_inventory = tuple(
                    item
                    for item in source_parent_before
                    if item.name != "payload.bin"
                )
                expected_target_inventory = tuple(
                    sorted(
                        (*target_parent_before, moved_entry),
                        key=lambda item: os.fsencode(item.name),
                    )
                )
                real_fsync = os.fsync
                real_open = os.open
                real_rename = module._renameat2_no_replace
                real_verify = module._verify_posix_file
                real_inventory = module._posix_inventory
                fsynced: list[tuple[int, int]] = []
                source_parent_identity = (
                    (root / "source").stat().st_dev,
                    (root / "source").stat().st_ino,
                )
                target_parent_identity = (
                    (root / "target").stat().st_dev,
                    (root / "target").stat().st_ino,
                )
                post_state = {
                    "native": False,
                    "opened": False,
                    "profile": False,
                    "source_inventory": [],
                    "target_inventory": [],
                }
                post_fd: int | None = None

                events: list[tuple[str, object]] = []

                def traced_fsync(fd: int) -> None:
                    value = os.fstat(fd)
                    if stat.S_ISDIR(value.st_mode):
                        identity = (value.st_dev, value.st_ino)
                        fsynced.append(identity)
                        events.append(("fsync", identity))
                    return real_fsync(fd)

                def traced_rename(*args):
                    events.append(("rename", None))
                    result = real_rename(*args)
                    post_state["native"] = True
                    events.append(("rename-returned", None))
                    return result

                def traced_open(path, flags, mode=0o777, *, dir_fd=None):
                    nonlocal post_fd
                    result = real_open(path, flags, mode, dir_fd=dir_fd)
                    if (
                        post_state["native"]
                        and path == "payload.bin"
                        and dir_fd is not None
                    ):
                        parent = os.fstat(dir_fd)
                        if (parent.st_dev, parent.st_ino) == target_parent_identity:
                            if module._posix_identity(result) != source_identity:
                                raise AssertionError(
                                    "rename post-open observed a different identity"
                                )
                            if post_state["opened"]:
                                raise AssertionError(
                                    "rename post-validation reopened the target twice"
                                )
                            post_fd = result
                            post_state["opened"] = True
                            events.append(("reopen", None))
                    return result

                def traced_verify(fd, *args, **kwargs):
                    if post_fd is not None and fd == post_fd:
                        post_state["profile"] = True
                    return real_verify(fd, *args, **kwargs)

                def traced_inventory(fd, *args, **kwargs):
                    result = real_inventory(fd, *args, **kwargs)
                    value = os.fstat(fd)
                    identity = (value.st_dev, value.st_ino)
                    if post_state["native"]:
                        if identity == source_parent_identity:
                            post_state["source_inventory"].append(result)
                        elif identity == target_parent_identity:
                            post_state["target_inventory"].append(result)
                    return result

                monkeypatch.setattr(os, "fsync", traced_fsync)
                monkeypatch.setattr(os, "open", traced_open)
                monkeypatch.setattr(module, "_renameat2_no_replace", traced_rename)
                monkeypatch.setattr(module, "_verify_posix_file", traced_verify)
                monkeypatch.setattr(module, "_posix_inventory", traced_inventory)
                returned = session.rename_no_replace(
                    ("source", "payload.bin"),
                    ("target", "payload.bin"),
                    directory=False,
                )
                source_id = (root.joinpath("source").stat().st_dev, root.joinpath("source").stat().st_ino)
                target_id = (root.joinpath("target").stat().st_dev, root.joinpath("target").stat().st_ino)
                # Presence alone would accept a mutant that synchronises both
                # parents before the native rename.  The durable order is
                # rename -> fsync(source) and fsync(target) -> final re-read.
                assert source_id in fsynced and target_id in fsynced
                kinds = [name for name, _ in events]
                assert kinds.count("rename") == 1
                rename_at = kinds.index("rename-returned")
                reopen_at = kinds.index("reopen")
                for parent in (source_id, target_id):
                    positions = [
                        index
                        for index, (name, identity) in enumerate(events)
                        if name == "fsync" and identity == parent
                    ]
                    assert positions, "parent directory was never synchronised"
                    assert min(positions) > rename_at, (
                        "parent directory synchronised before the native rename"
                    )
                    assert max(positions) < reopen_at, (
                        "final re-read preceded the parent synchronisation"
                    )
                assert not (root / "source" / "payload.bin").exists()
                assert (root / "target" / "payload.bin").read_bytes() == b"rename"
                assert object_identity(
                    root / "target" / "payload.bin", module
                ) == source_identity
                assert returned == source_identity
                assert post_state == {
                    "native": True,
                    "opened": True,
                    "profile": True,
                    "source_inventory": [
                        expected_source_inventory,
                        expected_source_inventory,
                    ],
                    "target_inventory": [
                        expected_target_inventory,
                        expected_target_inventory,
                    ],
                }
                assert_posix_security(
                    root / "target" / "payload.bin", directory=False, mode=0o600
                )
                monkeypatch.setattr(os, "fsync", real_fsync)
                monkeypatch.setattr(os, "open", real_open)
                monkeypatch.setattr(module, "_renameat2_no_replace", real_rename)
                monkeypatch.setattr(module, "_verify_posix_file", real_verify)
                monkeypatch.setattr(module, "_posix_inventory", real_inventory)
                # A destination that already exists is the functional oracle for
                # the literal RENAME_NOREPLACE flag.  A flags=0 mutant otherwise
                # succeeds in every positive fixture above because its target is
                # initially absent.
                session.create_file_exclusive(
                    ("source", "payload.bin"),
                    b"second-source",
                    role=private_role(module),
                )
                conflict_before = tree_snapshot(root)
                source_conflict_identity = object_identity(
                    root / "source" / "payload.bin", module
                )
                target_conflict_identity = object_identity(
                    root / "target" / "payload.bin", module
                )
                with pytest.raises(module.BirthSecureFSError) as conflict:
                    session.rename_no_replace(
                        ("source", "payload.bin"),
                        ("target", "payload.bin"),
                        directory=False,
                    )
                assert conflict.value.code == "birth_provisioning_transaction_conflict"
                assert tree_snapshot(root) == conflict_before
                assert object_identity(
                    root / "source" / "payload.bin", module
                ) == source_conflict_identity
                assert object_identity(
                    root / "target" / "payload.bin", module
                ) == target_conflict_identity
                assert (root / "source" / "payload.bin").read_bytes() == b"second-source"
                assert (root / "target" / "payload.bin").read_bytes() == b"rename"
                return

            expose = case != "renameat2-unavailable"
            before = tree_snapshot(root)
            errors = (
                (errno.EXDEV,)
                if case == "rename-exdev"
                else (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP)
                if case == "rename-enosys"
                else (errno.ENOSYS,)
            )
            source_parent = (
                (root / "source").stat().st_dev,
                (root / "source").stat().st_ino,
            )
            target_parent = (
                (root / "target").stat().st_dev,
                (root / "target").stat().st_ino,
            )
            for error in errors:
                probe = (
                    _FailingRenameAt2(
                        source_parent=source_parent,
                        target_parent=target_parent,
                    )
                    if expose
                    else None
                )
                monkeypatch.setattr(
                    ctypes,
                    "CDLL",
                    lambda *args, _probe=probe, **kwargs: _LibC(
                        expose=expose, renameat2=_probe
                    ),
                )
                monkeypatch.setattr(ctypes, "get_errno", lambda _error=error: _error)
                with pytest.raises(module.BirthSecureFSError) as caught:
                    session.rename_no_replace(
                        ("source", "payload.bin"),
                        ("target", "payload.bin"),
                        directory=False,
                    )
                assert caught.value.code == "birth_provisioning_atomic_install_unsupported"
                assert probe is None or probe.calls == 1
                assert (root / "source" / "payload.bin").read_bytes() == b"rename"
                assert not (root / "target" / "payload.bin").exists()
                assert tree_snapshot(root) == before
