from __future__ import annotations

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
    public_role,
    role_binding,
    secure_fs,
    tree_snapshot,
)


CASES = (
    "create-file-no-global",
    "create-file-shared-global",
    "create-directory-no-global",
    "create-directory-shared-global",
    "rename-no-global",
    "rename-shared-global",
    "dispose-no-global",
    "dispose-shared-global",
    "exclusive-allows-mutations",
    "shared-allows-readers",
)


def _create_lock(session) -> None:
    with session.global_lock(exclusive=True, create=True):
        pass


def _fixture_bindings(module):
    private_files = (
        "source.bin",
        "renamed.bin",
        "conflict-source.bin",
        "conflict-target.bin",
        "discard.bin",
        "readable.bin",
        "new.bin",
        "destination.bin",
    )
    return (
        lock_role_binding(module),
        *(
            role_binding(
                module, (name,), directory=False, role=private_role(module)
            )
            for name in private_files
        ),
        role_binding(
            module, ("staging",), directory=True, role=private_role(module)
        ),
        role_binding(
            module,
            ("new-directory",),
            directory=True,
            role=private_role(module),
        ),
        role_binding(
            module, ("public",), directory=True, role=public_role(module)
        ),
        role_binding(
            module,
            ("public", "record.json"),
            directory=False,
            role=public_role(module),
        ),
    )


def _create_file(session, name: str, payload: bytes = b"payload"):
    return session.create_file_exclusive((name,), payload, role=private_role())


def _create_directory(session, name: str):
    return session.create_directory_exclusive((name,), role=private_role())


def _dispose_expectation(module, name: str, identity, payload: bytes):
    return module._DisposalExpectation(
        components=(name,),
        identity=identity,
        kind=module._ObjectKind("regular_file"),
        role=private_role(module),
        disposal_class=module._DisposalClass("complete_file"),
        links=1,
        expected_size=len(payload),
        maximum_partial_size=None,
        content_sha256="sha256:" + __import__("hashlib").sha256(payload).hexdigest(),
        inventory=None,
    )


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_mutation_lock_precondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    session = open_session(root, role_bindings=_fixture_bindings(module))
    try:
        _create_lock(session)
        if case == "exclusive-allows-mutations":
            create_opens: list[tuple[object, int, int | None]] = []
            mkdirs: list[tuple[object, int | None]] = []
            renames: list[tuple[int, object, int, object]] = []
            fsynced_directories: list[tuple[int, int]] = []
            if os.name == "posix":
                real_open, real_mkdir, real_fsync = os.open, os.mkdir, os.fsync
                real_rename = module._renameat2_no_replace
                create_flag = os.O_CREAT

                def traced_open(path, flags, mode=0o777, *, dir_fd=None):
                    if flags & create_flag:
                        create_opens.append((path, flags, dir_fd))
                    return real_open(path, flags, mode, dir_fd=dir_fd)

                def traced_mkdir(path, mode=0o777, *, dir_fd=None):
                    mkdirs.append((path, dir_fd))
                    return real_mkdir(path, mode, dir_fd=dir_fd)

                def traced_rename(source_fd, source_name, target_fd, target_name):
                    renames.append(
                        (source_fd, source_name, target_fd, target_name)
                    )
                    return real_rename(
                        source_fd, source_name, target_fd, target_name
                    )

                def traced_fsync(fd: int) -> None:
                    value = os.fstat(fd)
                    if stat.S_ISDIR(value.st_mode):
                        fsynced_directories.append((value.st_dev, value.st_ino))
                    return real_fsync(fd)

                monkeypatch.setattr(os, "open", traced_open)
                monkeypatch.setattr(os, "mkdir", traced_mkdir)
                monkeypatch.setattr(os, "fsync", traced_fsync)
                monkeypatch.setattr(
                    module, "_renameat2_no_replace", traced_rename
                )
            with session.global_lock(exclusive=True, create=False):
                _create_directory(session, "staging")
                source_identity = _create_file(session, "source.bin", b"source")
                renamed_identity = session.rename_no_replace(
                    ("source.bin",), ("renamed.bin",), directory=False
                )
                session.create_directory_exclusive(
                    ("public",), role=public_role(module)
                )
                session.create_file_exclusive(
                    ("public", "record.json"),
                    b"public",
                    role=public_role(module),
                )
                _create_file(session, "conflict-source.bin", b"source-two")
                _create_file(session, "conflict-target.bin", b"target-two")
                conflict_before = tree_snapshot(root)
                with pytest.raises(module.BirthSecureFSError) as caught:
                    session.rename_no_replace(
                        ("conflict-source.bin",),
                        ("conflict-target.bin",),
                        directory=False,
                    )
                assert caught.value.code == "birth_provisioning_transaction_conflict"
                assert tree_snapshot(root) == conflict_before
            assert (root / "staging").is_dir()
            assert not (root / "source.bin").exists()
            assert (root / "renamed.bin").read_bytes() == b"source"
            assert renamed_identity == source_identity
            assert object_identity(root / "renamed.bin", module) == source_identity
            if os.name == "posix":
                assert_posix_security(
                    root / "staging", directory=True, mode=0o700
                )
                assert_posix_security(
                    root / "renamed.bin", directory=False, mode=0o600
                )
                assert_posix_security(
                    root / "public", directory=True, mode=0o755
                )
                assert_posix_security(
                    root / "public" / "record.json",
                    directory=False,
                    mode=0o644,
                )
                assert create_opens
                assert all(
                    path != os.fspath(root) and isinstance(dir_fd, int)
                    for path, _, dir_fd in create_opens
                )
                assert all(
                    flags & os.O_EXCL
                    and flags & os.O_NOFOLLOW
                    for _, flags, _ in create_opens
                )
                assert mkdirs and all(
                    isinstance(dir_fd, int) for _, dir_fd in mkdirs
                )
                assert renames
                assert renames[0] == (
                    renames[0][0],
                    "source.bin",
                    renames[0][2],
                    "renamed.bin",
                )
                if len(renames) == 2:
                    assert renames[1] == (
                        renames[1][0],
                        "conflict-source.bin",
                        renames[1][2],
                        "conflict-target.bin",
                    )
                else:
                    assert len(renames) == 1
                directory_ids = {
                    (path.stat().st_dev, path.stat().st_ino)
                    for path in (root, root / "staging", root / "public")
                }
                assert directory_ids <= set(fsynced_directories)
            return
        if case == "shared-allows-readers":
            with session.global_lock(exclusive=True, create=False):
                _create_file(session, "readable.bin", b"readable")
            with session.global_lock(exclusive=False, create=False):
                assert session.read_file(
                    ("readable.bin",), maximum=8, role=private_role(module)
                ) == b"readable"
                assert "readable.bin" in session.inventory(())
            return

        operation, lock_mode = case.rsplit("-", 2)[0], case.rsplit("-", 2)[1:]
        shared = lock_mode == ["shared", "global"]
        if operation == "rename":
            with session.global_lock(exclusive=True, create=False):
                _create_file(session, "source.bin", b"source")
        if operation == "dispose":
            with session.global_lock(exclusive=True, create=False):
                identity = _create_file(session, "discard.bin", b"discard")
            expectation = _dispose_expectation(
                module, "discard.bin", identity, b"discard"
            )
        before = tree_snapshot(root)

        def mutate() -> None:
            if operation == "create-file":
                _create_file(session, "new.bin")
            elif operation == "create-directory":
                _create_directory(session, "new-directory")
            elif operation == "rename":
                session.rename_no_replace(
                    ("source.bin",), ("destination.bin",), directory=False
                )
            else:
                session.dispose_transaction_object(expectation)

        with pytest.raises(module.BirthSecureFSError) as caught:
            if shared:
                with session.global_lock(exclusive=False, create=False):
                    mutate()
            else:
                mutate()
        assert caught.value.code == "birth_provisioning_lock_unsafe"
        assert tree_snapshot(root) == before
    finally:
        session.close()
