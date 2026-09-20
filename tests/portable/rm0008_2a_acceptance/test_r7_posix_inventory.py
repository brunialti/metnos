from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ._support import (
    lock_role_binding,
    make_root,
    open_session,
    private_role,
    role_binding,
    secure_fs,
    tree_snapshot,
    write_private,
)


CASES = (
    "regular-record",
    "directory-record",
    "symlink-record-rejected",
    "hardlink-rejected",
    "mutation-between-scans-rejected",
)


def _entry_kind(entry) -> str:
    return entry.kind.value


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_inventory_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    bindings = []
    bindings.append(lock_role_binding(module))
    bindings.extend(
        (
            role_binding(
                module,
                ("entry.bin",),
                directory=False,
                role=private_role(module),
            ),
            role_binding(
                module,
                ("entry-directory",),
                directory=True,
                role=private_role(module),
            ),
        )
    )
    if case == "symlink-record-rejected":
        for name in ("target.bin", "link.bin"):
            bindings.append(
                role_binding(
                    module, (name,), directory=False, role=private_role(module)
                )
            )
    elif case == "hardlink-rejected":
        bindings.append(
            role_binding(
                module,
                ("second-link.bin",),
                directory=False,
                role=private_role(module),
            )
        )
    elif case == "mutation-between-scans-rejected":
        bindings.extend(
            role_binding(
                module, (name,), directory=False, role=private_role(module)
            )
            for name in ("victim.bin", "original.bin")
        )
    with open_session(root, role_bindings=tuple(bindings)) as session:
        with session.global_lock(exclusive=True, create=True):
            if case in {"regular-record", "hardlink-rejected"}:
                session.create_file_exclusive(
                    ("entry.bin",), b"record", role=private_role(module)
                )
            elif case == "directory-record":
                session.create_directory_exclusive(
                    ("entry-directory",), role=private_role(module)
                )
            elif case == "symlink-record-rejected":
                write_private(root / "target.bin", b"target")
                (root / "link.bin").symlink_to("target.bin")
            else:
                session.create_file_exclusive(
                    ("victim.bin",), b"original", role=private_role(module)
                )

        if case == "hardlink-rejected":
            os.link(root / "entry.bin", root / "second-link.bin")
        unchanged = tree_snapshot(root)
        if case == "mutation-between-scans-rejected":
            real_open = os.open
            fired = False
            injected_snapshot = None

            def replace_after_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal fired, injected_snapshot
                fd = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "victim.bin" and dir_fd is not None and not fired:
                    fired = True
                    (root / "victim.bin").rename(root / "original.bin")
                    (root / "victim.bin").write_bytes(b"replacement")
                    (root / "victim.bin").chmod(0o600)
                    injected_snapshot = tree_snapshot(root)
                return fd

            monkeypatch.setattr(os, "open", replace_after_open)

        rejected = case.endswith("rejected")

        def inventory():
            with session.global_lock(exclusive=False, create=False):
                return session._inventory_state(())

        if rejected:
            with pytest.raises(module.BirthSecureFSError) as caught:
                inventory()
            assert caught.value.code == "birth_provisioning_recovery_ambiguous"
            assert str(caught.value) == caught.value.code
            if case == "mutation-between-scans-rejected":
                assert fired
                assert (root / "victim.bin").read_bytes() == b"replacement"
                assert (root / "original.bin").read_bytes() == b"original"
                assert injected_snapshot is not None
                assert tree_snapshot(root) == injected_snapshot
            else:
                assert tree_snapshot(root) == unchanged
            return

        entries = inventory()
        target_name = "entry.bin" if case == "regular-record" else "entry-directory"
        entry = next(item for item in entries if item.name == target_name)
        observed = (root / target_name).lstat()
        expected_kind = "regular_file" if stat.S_ISREG(observed.st_mode) else "directory"
        assert _entry_kind(entry) == expected_kind
        assert entry.links == observed.st_nlink
        assert entry.identity.volume == f"{observed.st_dev:x}"
        assert entry.identity.object_id == f"{observed.st_ino:x}"
        assert entry.role == private_role(module)
        assert entry.size == (observed.st_size if expected_kind == "regular_file" else None)
