from __future__ import annotations

import builtins
import importlib
import io
import os
from pathlib import Path

import pytest

from ._support import (
    chown_other_uid,
    provision_approval,
    provision_keystore,
    provision_semantic,
    restore_owner,
    tree_snapshot,
)


CASES = (
    "keystore-external-local-only",
    "approval-public-other-uid",
    "semantic-public-other-uid",
    "keystore-legacy-no-mutation",
    "approval-legacy-no-mutation",
    "semantic-legacy-no-mutation",
)


def _call_through_handles(
    monkeypatch: pytest.MonkeyPatch,
    call,
    *,
    allowed_absolute_roots: set[Path],
    required_relative_names: set[str],
):
    opened: list[tuple[str, int, int | None]] = []
    descendant_parents: list[tuple[int, int]] = []
    owned_identities: set[tuple[int, int]] = set()
    real_open = os.open
    real_chdir = os.chdir
    real_fchdir = os.fchdir

    def traced_open(path, flags, mode=0o777, *, dir_fd=None):
        # A nominal reopen against the process directory passes ``AT_FDCWD``,
        # which is an int like any other descriptor but names no object: it
        # cannot be inspected.  Only a descriptor whose identity descends from
        # the authenticated root counts as handle-bound.
        if dir_fd is not None:
            try:
                parent = os.fstat(dir_fd)
            except OSError as exc:
                raise AssertionError(
                    "legacy loader opened relative to a descriptor that names "
                    "no directory"
                ) from exc
            descendant_parents.append((parent.st_dev, parent.st_ino))
        result = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append((os.fspath(path), flags, dir_fd))
        try:
            observed = os.fstat(result)
        except OSError:  # pragma: no cover - descriptor already invalid
            return result
        owned_identities.add((observed.st_dev, observed.st_ino))
        return result

    def chdir_forbidden(*args, **kwargs):
        raise AssertionError("legacy loader changed the process directory")

    def path_io_forbidden(*args, **kwargs):
        raise AssertionError("legacy loader reopened authority through pathlib")

    def stream_io_forbidden(*args, **kwargs):
        raise AssertionError("legacy loader reopened authority through a path stream")

    real_stat, real_lstat = os.stat, os.lstat
    real_listdir, real_scandir, real_readlink = os.listdir, os.scandir, os.readlink

    def checked_stat(path, *args, dir_fd=None, **kwargs):
        if isinstance(path, int) or (
            dir_fd is not None and not os.path.isabs(os.fsdecode(path))
        ):
            return real_stat(path, *args, dir_fd=dir_fd, **kwargs)
        raise AssertionError("legacy loader performed path-based stat")

    def checked_lstat(path, *args, dir_fd=None, **kwargs):
        if isinstance(path, int) or (
            dir_fd is not None and not os.path.isabs(os.fsdecode(path))
        ):
            return real_lstat(path, *args, dir_fd=dir_fd, **kwargs)
        raise AssertionError("legacy loader performed path-based lstat")

    def checked_directory_read(path="."):
        if isinstance(path, int):
            return real_listdir(path)
        raise AssertionError("legacy loader enumerated a directory by path")

    def checked_scandir(path="."):
        if isinstance(path, int):
            return real_scandir(path)
        raise AssertionError("legacy loader enumerated a directory by path")

    def checked_readlink(path, *args, dir_fd=None, **kwargs):
        if dir_fd is not None and not os.path.isabs(os.fsdecode(path)):
            return real_readlink(path, *args, dir_fd=dir_fd, **kwargs)
        raise AssertionError("legacy loader resolved a link by path")

    with monkeypatch.context() as guard:
        guard.setattr(os, "open", traced_open)
        guard.setattr(os, "chdir", chdir_forbidden)
        guard.setattr(os, "fchdir", chdir_forbidden)
        guard.setattr(builtins, "open", stream_io_forbidden)
        guard.setattr(io, "open", stream_io_forbidden)
        guard.setattr(os, "stat", checked_stat)
        guard.setattr(os, "lstat", checked_lstat)
        guard.setattr(os, "listdir", checked_directory_read)
        guard.setattr(os, "scandir", checked_scandir)
        guard.setattr(os, "readlink", checked_readlink)
        for name in ("open", "read_bytes", "stat", "lstat", "iterdir"):
            guard.setattr(Path, name, path_io_forbidden)
        result = call()
    descendants = [item for item in opened if item[2] is not None]
    assert descendants
    assert all(flags & os.O_NOFOLLOW for _, flags, _ in descendants)
    assert all(not os.path.isabs(path) for path, _, _ in descendants)
    # Each relative open must hang from a descriptor this call actually opened,
    # or from the authenticated root itself.  An unrelated descriptor would
    # otherwise satisfy the shape of a handle-bound traversal.
    root_identities = set()
    for path in allowed_absolute_roots:
        observed = os.stat(path)
        root_identities.add((observed.st_dev, observed.st_ino))
    assert descendant_parents
    assert all(
        parent in owned_identities | root_identities
        for parent in descendant_parents
    )
    allowed_absolute = {
        os.path.abspath(os.fspath(path)) for path in allowed_absolute_roots
    } | {os.path.abspath(os.sep)}
    assert all(
        not os.path.isabs(path)
        or os.path.abspath(path) in allowed_absolute
        for path, _, dir_fd in opened
        if dir_fd is None
    )
    observed_names = {os.fsdecode(path) for path, _, _ in descendants}
    assert required_relative_names <= observed_names
    return result


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_legacy_loader_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    keystore_root = tmp_path / "external-keystore"
    approval_file = tmp_path / "approval-authority.json"
    semantic_root = tmp_path / "semantic"
    provision_keystore(keystore_root)
    provision_approval(approval_file)
    semantic_value = provision_semantic(semantic_root)
    # Imports precede the guard so import machinery cannot be mistaken for an
    # authority read.  Calls below must then cross only the authenticated
    # descriptor boundary.
    keystore_module = importlib.import_module("executor_birth_keystore")
    approval_module = importlib.import_module("executor_birth_approval_authority")
    semantic_module = importlib.import_module("executor_birth_semantic_authority")

    if case == "keystore-external-local-only":
        loaded = _call_through_handles(
            monkeypatch,
            lambda: keystore_module.load_birth_keystore(keystore_root),
            allowed_absolute_roots={keystore_root},
            required_relative_names={"keystore.json"},
        )
        assert loaded.config_revision == 1
        # The authoritative global lock would be created inside the keystore
        # root, which is the only root the legacy facade knows.
        assert not (keystore_root / "provisioning-v1.lock").exists()
        assert not (tmp_path / "provisioning-v1.lock").exists()
        (keystore_root / "keystore.json").chmod(0o640)
        with pytest.raises(
            importlib.import_module("executor_birth_keystore").BirthKeyStoreError
        ):
            _call_through_handles(
                monkeypatch,
                lambda: keystore_module.load_birth_keystore(keystore_root),
                allowed_absolute_roots={keystore_root},
                required_relative_names={"keystore.json"},
            )
        return

    if case == "approval-public-other-uid":
        chown_other_uid(iter((approval_file,)))
        try:
            loaded = _call_through_handles(
                monkeypatch,
                lambda: approval_module.load_approval_authority(approval_file),
                allowed_absolute_roots={approval_file.parent},
                required_relative_names={approval_file.name},
            )
            assert loaded.revision == 1
        finally:
            restore_owner(iter((approval_file,)))
        approval_file.chmod(0o664)
        with pytest.raises(approval_module.BirthApprovalError):
            _call_through_handles(
                monkeypatch,
                lambda: approval_module.load_approval_authority(approval_file),
                allowed_absolute_roots={approval_file.parent},
                required_relative_names={approval_file.name},
            )
        return

    if case == "semantic-public-other-uid":
        owned_elsewhere = (
            semantic_root / "public" / "review.pub",
            semantic_root / "evidence",
        )
        chown_other_uid(iter(owned_elsewhere))
        try:
            loaded = _call_through_handles(
                monkeypatch,
                lambda: semantic_module.load_semantic_authority(
                    semantic_value, semantic_root
                ),
                allowed_absolute_roots={semantic_root},
                required_relative_names={"review.pub"},
            )
            assert set(loaded.verifier_keys) == {"review-key"}
        finally:
            restore_owner(iter(owned_elsewhere))
        (semantic_root / "public" / "review.pub").chmod(0o664)
        review_module = importlib.import_module("executor_birth_semantic_review")
        with pytest.raises(review_module.SemanticReviewError):
            _call_through_handles(
                monkeypatch,
                lambda: semantic_module.load_semantic_authority(
                    semantic_value, semantic_root
                ),
                allowed_absolute_roots={semantic_root},
                required_relative_names={"review.pub"},
            )
        return

    if case == "keystore-legacy-no-mutation":
        target = keystore_root
        call = lambda: keystore_module.load_birth_keystore(keystore_root)
        allowed_roots = {keystore_root}
        required_names = {"keystore.json"}
    elif case == "approval-legacy-no-mutation":
        target = tmp_path
        call = lambda: approval_module.load_approval_authority(approval_file)
        allowed_roots = {approval_file.parent}
        required_names = {approval_file.name}
    else:
        target = semantic_root
        call = lambda: semantic_module.load_semantic_authority(
            semantic_value, semantic_root
        )
        allowed_roots = {semantic_root}
        required_names = {"review.pub"}
    before = tree_snapshot(target)
    result = _call_through_handles(
        monkeypatch,
        call,
        allowed_absolute_roots=allowed_roots,
        required_relative_names=required_names,
    )
    assert result is not None
    assert tree_snapshot(target) == before
