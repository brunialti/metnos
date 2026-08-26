from __future__ import annotations

from pathlib import Path

import pytest

from ._support import make_root, open_session, private_role, secure_fs, tree_snapshot


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
        content_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        inventory=None,
    )


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_mutation_lock_precondition(tmp_path: Path, case: str) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    session = open_session(root)
    try:
        _create_lock(session)
        if case == "exclusive-allows-mutations":
            with session.global_lock(exclusive=True, create=False):
                _create_directory(session, "staging")
                _create_file(session, "source.bin", b"source")
                session.rename_no_replace(
                    ("source.bin",), ("renamed.bin",), directory=False
                )
            assert (root / "staging").is_dir()
            assert (root / "renamed.bin").read_bytes() == b"source"
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
