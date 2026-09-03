"""Adversarial tests for the shared descriptor-bound directory kernel."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat

import pytest

from install import executor_birth_posix_directory as directory


def test_absolute_chain_rejects_live_root_replacement(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    bound = directory.BoundDirectoryChainV1(root)
    try:
        root.rename(tmp_path / "old-root")
        root.mkdir(mode=0o700)
        with pytest.raises(directory.PosixDirectoryError, match="path replaced"):
            bound.assert_bound()
    finally:
        bound.close()


def test_relative_open_rebinds_every_parent_after_use(tmp_path) -> None:
    root = tmp_path / "root"
    leaf = root / "parent" / "leaf"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"original")
    bound = directory.BoundDirectoryChainV1(root)
    try:
        with pytest.raises(directory.PosixDirectoryError, match="path replaced"):
            with bound.open_relative(
                PurePosixPath("parent/leaf"), directory=False,
            ):
                (root / "parent").rename(root / "old-parent")
                (root / "parent").mkdir()
                (root / "parent" / "leaf").write_bytes(b"replacement")
    finally:
        bound.close()


def test_acl_unsupported_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        directory.os, "getxattr",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "no ACL")),
    )
    with pytest.raises(directory.PosixDirectoryError, match="ACL observation"):
        directory.require_no_acl_v1(7)


def test_constructor_closes_child_that_fails_initial_rebind(monkeypatch) -> None:
    opened, closed = iter((10, 11)), []
    monkeypatch.setattr(directory, "require_posix_directory_platform_v1", lambda: None)
    monkeypatch.setattr(directory.os, "open", lambda *_args, **_kwargs: next(opened))
    monkeypatch.setattr(directory.os, "close", closed.append)
    monkeypatch.setattr(
        directory, "require_rebound_v1",
        lambda *_args: (_ for _ in ()).throw(directory.PosixDirectoryError("changed")),
    )
    with pytest.raises(directory.PosixDirectoryError):
        directory.BoundDirectoryChainV1(Path("/child"))
    assert closed == [11, 10]


def test_relative_open_closes_child_that_fails_initial_rebind(monkeypatch) -> None:
    chain = object.__new__(directory.BoundDirectoryChainV1)
    chain._path, chain._pid, chain._descriptors = Path("/root"), os.getpid(), [10]
    closed = []
    monkeypatch.setattr(directory.BoundDirectoryChainV1, "assert_bound", lambda _self: None)
    monkeypatch.setattr(directory.os, "open", lambda *_args, **_kwargs: 11)
    monkeypatch.setattr(directory.os, "close", closed.append)
    monkeypatch.setattr(
        directory, "require_rebound_v1",
        lambda *_args: (_ for _ in ()).throw(directory.PosixDirectoryError("changed")),
    )
    with pytest.raises(directory.PosixDirectoryError):
        with chain.open_relative(PurePosixPath("leaf"), directory=False):
            pass
    assert closed == [11]


def test_metadata_change_during_acl_observation_is_rejected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    chain = directory.BoundDirectoryChainV1(root)
    paths, current = [Path("/")], Path("/")
    for part in root.parts[1:]:
        current /= part
        paths.append(current)
    expected = {}
    for path, descriptor in zip(paths, chain._descriptors):
        info = os.fstat(descriptor)
        expected[path] = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
    target = chain._descriptors[-1]

    def change_mode(descriptor):
        if descriptor == target:
            os.fchmod(descriptor, 0o755)

    monkeypatch.setattr(directory, "require_no_acl_v1", change_mode)
    try:
        with pytest.raises(directory.PosixDirectoryError, match="metadata changed"):
            chain.attest_metadata(expected)
    finally:
        chain.close()


def test_platform_gate_rejects_missing_no_follow(monkeypatch) -> None:
    monkeypatch.delattr(directory.os, "O_NOFOLLOW")
    with pytest.raises(directory.PosixDirectoryError, match="platform unsupported"):
        directory.require_posix_directory_platform_v1()


def test_descriptor_cleanup_attempts_all_and_raises_typed_error(monkeypatch) -> None:
    attempted = []

    def close(descriptor):
        attempted.append(descriptor)
        if descriptor == 2:
            raise OSError("close injected")

    monkeypatch.setattr(directory.os, "close", close)
    with pytest.raises(directory.PosixDirectoryError) as denied:
        directory.close_descriptors_v1((1, 2, 3))
    assert isinstance(denied.value.__cause__, OSError)
    assert attempted == [3, 2, 1]


def test_descriptor_cleanup_does_not_mask_active_error(monkeypatch) -> None:
    attempted = []

    def close(descriptor):
        attempted.append(descriptor)
        raise OSError(f"close {descriptor}")

    monkeypatch.setattr(directory.os, "close", close)
    with pytest.raises(ValueError, match="primary") as denied:
        try:
            raise ValueError("primary")
        finally:
            directory.close_descriptors_v1((1, 2))
    assert attempted == [2, 1]
    assert len(denied.value.__notes__) == 2
