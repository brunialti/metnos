"""Real-fd contracts for the read-only POSIX directory capability."""
from __future__ import annotations

import ast
import copy
import errno
import os
from pathlib import Path, PurePosixPath
import pickle
import runpy
import stat
import struct

import pytest

import executor_birth_posix_acl as acl_owner
import executor_birth_posix_directory as directory
from executor_birth_posix_metadata import PosixStatSnapshotV1


LINUX_ONLY = pytest.mark.skipif(
    not os.name == "posix" or not __import__("sys").platform.startswith("linux"),
    reason="descriptor-bound POSIX directory walking requires Linux",
)
_UNDEFINED_ID = 0xFFFFFFFF


def _pure(path: Path) -> PurePosixPath:
    return PurePosixPath(path.as_posix())


def _acl(entries: tuple[tuple[int, int, int], ...]) -> bytes:
    return struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", *entry) for entry in entries
    )


def _minimal_acl() -> bytes:
    return _acl((
        (0x01, 0o7, _UNDEFINED_ID),
        (0x04, 0o5, _UNDEFINED_ID),
        (0x20, 0o0, _UNDEFINED_ID),
    ))


def _extended_acl() -> bytes:
    return _acl((
        (0x01, 0o7, _UNDEFINED_ID),
        (0x02, 0o7, os.getuid()),
        (0x04, 0o5, _UNDEFINED_ID),
        (0x10, 0o7, _UNDEFINED_ID),
        (0x20, 0o0, _UNDEFINED_ID),
    ))


def _fd_count() -> int:
    descriptor_root = Path("/proc/self/fd")
    if not descriptor_root.is_dir():
        pytest.skip("Linux descriptor inventory is unavailable")
    return len(tuple(descriptor_root.iterdir()))


@LINUX_ONLY
def test_real_tree_observation_is_typed_and_no_follow(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "child").mkdir()
    (root / "file.txt").write_text("x", encoding="utf-8")
    with directory.open_posix_directory_v1(_pure(root)) as capability:
        try:
            observed = capability.observe_self()
            child = capability.observe_child("child")
        except directory.PosixDirectoryError as exc:
            if exc.kind is directory.PosixDirectoryFailureKindV1.acl_unsupported:
                pytest.skip("temporary filesystem does not support POSIX ACL xattrs")
            raise
        regular = capability.observe_child("file.txt")
        missing = capability.observe_child("missing")
        assert stat.S_ISDIR(observed.metadata.mode)
        assert child.node_kind is directory.PosixNodeKindV1.directory
        assert child.acl is not None
        assert regular.node_kind is directory.PosixNodeKindV1.other
        assert stat.S_ISREG(regular.metadata.mode)  # type: ignore[union-attr]
        assert missing == directory.PosixChildObservationV1(
            "missing", directory.PosixNodeKindV1.missing, None, None,
        )


@LINUX_ONLY
def test_symlinks_are_never_followed(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(directory.PosixDirectoryError) as captured:
        directory.open_posix_directory_v1(_pure(link))
    assert captured.value.kind is directory.PosixDirectoryFailureKindV1.symlink_refused
    with directory.open_posix_directory_v1(_pure(tmp_path)) as capability:
        observed = capability.observe_child("link")
        assert observed.node_kind is directory.PosixNodeKindV1.other
        assert stat.S_ISLNK(observed.metadata.mode)  # type: ignore[union-attr]
        with pytest.raises(directory.PosixDirectoryError) as child_error:
            capability.open_child_directory("link")
        assert child_error.value.kind is directory.PosixDirectoryFailureKindV1.symlink_refused


@LINUX_ONLY
def test_root_and_child_inode_replacements_are_detected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    root_capability = directory.open_posix_directory_v1(_pure(root))
    child_capability = root_capability.open_child_directory("child")
    child.rename(root / "old-child")
    child.mkdir()
    with pytest.raises(directory.PosixDirectoryError) as child_error:
        child_capability.assert_bound()
    assert child_error.value.kind is directory.PosixDirectoryFailureKindV1.binding_changed
    child_capability.close()
    root.rename(tmp_path / "old-root")
    root.mkdir()
    with pytest.raises(directory.PosixDirectoryError) as root_error:
        root_capability.assert_bound()
    assert root_error.value.kind is directory.PosixDirectoryFailureKindV1.binding_changed
    root_capability.close()


@LINUX_ONLY
def test_child_rechecks_parent_after_rebound(tmp_path: Path, monkeypatch) -> None:
    child_path = tmp_path / "child"
    child_path.mkdir()
    parent = directory.open_posix_directory_v1(_pure(tmp_path))
    child = parent.open_child_directory("child")
    original = directory.PosixDirectoryCapabilityV1.assert_bound
    parent_checks = 0

    def assert_bound(capability) -> None:
        nonlocal parent_checks
        if capability is parent:
            parent_checks += 1
            if parent_checks == 2:
                raise directory.PosixDirectoryError(
                    directory.PosixDirectoryFailureKindV1.binding_changed,
                )
        original(capability)

    monkeypatch.setattr(
        directory.PosixDirectoryCapabilityV1, "assert_bound", assert_bound,
    )
    try:
        with pytest.raises(directory.PosixDirectoryError) as captured:
            child.assert_bound()
        assert captured.value.kind is directory.PosixDirectoryFailureKindV1.binding_changed
        assert parent_checks == 2
    finally:
        child.close()
        parent.close()


@LINUX_ONLY
def test_capability_is_pid_bound_closed_and_nontransferable(
    tmp_path: Path, monkeypatch,
) -> None:
    capability = directory.open_posix_directory_v1(_pure(tmp_path))
    with pytest.raises(TypeError):
        copy.copy(capability)
    with pytest.raises(TypeError):
        copy.deepcopy(capability)
    with pytest.raises(TypeError):
        pickle.dumps(capability)
    original_getpid = os.getpid
    owner_pid = original_getpid()
    monkeypatch.setattr(directory.os, "getpid", lambda: owner_pid + 1)
    with pytest.raises(directory.PosixDirectoryError) as captured:
        capability.assert_bound()
    assert captured.value.kind is directory.PosixDirectoryFailureKindV1.process_changed
    capability.close()
    monkeypatch.setattr(directory.os, "getpid", original_getpid)
    with pytest.raises(directory.PosixDirectoryError) as closed:
        capability.assert_bound()
    assert closed.value.kind is directory.PosixDirectoryFailureKindV1.capability_closed


def test_child_observation_rejects_impossible_public_states() -> None:
    snapshot = directory.PosixAclSnapshotV1(False, False)
    with pytest.raises(ValueError, match="missing child metadata"):
        directory.PosixChildObservationV1(
            "missing", directory.PosixNodeKindV1.missing,
            PosixStatSnapshotV1(1, 2, 3, 1, 4, 5, 0, 6, 7), None,
        )
    with pytest.raises(ValueError, match="missing child metadata"):
        directory.PosixChildObservationV1(
            "file", directory.PosixNodeKindV1.other, None, None,
        )
    with pytest.raises(ValueError, match="child ACL metadata"):
        directory.PosixChildObservationV1(
            "directory", directory.PosixNodeKindV1.directory,
            PosixStatSnapshotV1(1, 2, 3, 1, 4, 5, 0, 6, 7), None,
        )
    with pytest.raises(ValueError, match="child ACL metadata"):
        directory.PosixChildObservationV1(
            "file", directory.PosixNodeKindV1.other,
            PosixStatSnapshotV1(1, 2, 3, 1, 4, 5, 0, 6, 7), snapshot,
        )


@LINUX_ONLY
def test_descriptors_are_closed_on_success_and_failure(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    baseline = _fd_count()
    with directory.open_posix_directory_v1(_pure(root)) as capability:
        with capability.open_child_directory("child") as child:
            child.assert_bound()
    assert _fd_count() == baseline
    missing = _pure(root / "missing" / "descendant")
    with pytest.raises(directory.PosixDirectoryError):
        directory.open_posix_directory_v1(missing)
    assert _fd_count() == baseline


@LINUX_ONLY
def test_real_extended_access_and_default_acl_are_detected(tmp_path: Path) -> None:
    if not callable(getattr(os, "setxattr", None)):
        pytest.skip("POSIX ACL xattr setup is unavailable")
    target = tmp_path / "acl-root"
    target.mkdir()
    try:
        os.setxattr(target, "system.posix_acl_access", _extended_acl(), follow_symlinks=False)
        os.setxattr(target, "system.posix_acl_default", _extended_acl(), follow_symlinks=False)
    except OSError as exc:
        unsupported = {
            errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            errno.EACCES, errno.EPERM,
        }
        if exc.errno in unsupported:
            pytest.skip("temporary filesystem cannot install POSIX ACL xattrs")
        raise
    with directory.open_posix_directory_v1(_pure(target)) as capability:
        observed = capability.observe_self()
    assert observed.acl == directory.PosixAclSnapshotV1(True, True)


@LINUX_ONLY
@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (OSError(errno.ENOTSUP, "unsupported"), "acl_unsupported"),
        (b"malformed", "acl_malformed"),
    ),
)
def test_acl_failures_are_closed_and_typed(
    tmp_path: Path, monkeypatch, failure, expected: str,
) -> None:
    capability = directory.open_posix_directory_v1(_pure(tmp_path))

    def fail_or_return(_descriptor, _name):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(acl_owner.os, "getxattr", fail_or_return)
    try:
        with pytest.raises(directory.PosixDirectoryError) as captured:
            capability.observe_self()
        assert captured.value.kind.value == expected
    finally:
        capability.close()


@LINUX_ONLY
def test_acl_codec_distinguishes_minimal_access_from_default(
    tmp_path: Path, monkeypatch,
) -> None:
    values = {
        "system.posix_acl_access": _minimal_acl(),
        "system.posix_acl_default": _minimal_acl(),
    }
    monkeypatch.setattr(
        acl_owner.os, "getxattr", lambda _descriptor, name: values[name],
    )
    with directory.open_posix_directory_v1(_pure(tmp_path)) as capability:
        observed = capability.observe_self()
    assert observed.acl == directory.PosixAclSnapshotV1(False, True)


@LINUX_ONLY
@pytest.mark.parametrize(
    "value",
    (Path("/tmp"), PurePosixPath("relative"), PurePosixPath("//server/root"),
     PurePosixPath("/tmp/../unsafe")),
)
def test_absolute_path_grammar_is_closed(value) -> None:
    with pytest.raises(directory.PosixDirectoryError) as captured:
        directory.open_posix_directory_v1(value)  # type: ignore[arg-type]
    assert captured.value.kind is directory.PosixDirectoryFailureKindV1.invalid_path


def test_platform_capability_absence_is_typed(monkeypatch) -> None:
    monkeypatch.setattr(directory.sys, "platform", "win32")
    with pytest.raises(directory.PosixDirectoryError) as captured:
        directory.open_posix_directory_v1(PurePosixPath("/tmp"))
    assert captured.value.kind is directory.PosixDirectoryFailureKindV1.platform_unsupported


def test_missing_required_syscall_flag_is_typed(monkeypatch) -> None:
    monkeypatch.delattr(directory.os, "O_NOFOLLOW")
    with pytest.raises(directory.PosixDirectoryError) as captured:
        directory.open_posix_directory_v1(PurePosixPath("/tmp"))
    assert captured.value.kind is directory.PosixDirectoryFailureKindV1.platform_unsupported


def test_imports_are_pure_small_and_read_only(tmp_path: Path) -> None:
    before_environment = dict(os.environ)
    before_entries = tuple(tmp_path.iterdir())
    before_fds = _fd_count() if Path("/proc/self/fd").is_dir() else None
    roots = (
        Path(directory.__file__), Path(acl_owner.__file__),
        Path(__import__("executor_birth_posix_directory_model").__file__),
    )
    forbidden_calls = {
        "mkdir", "chown", "fchown", "chmod", "fchmod", "setxattr",
        "removexattr", "rename", "replace", "unlink", "write", "flock",
    }
    for source_path in roots:
        namespace = runpy.run_path(source_path)
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        sizes = [
            node.end_lineno - node.lineno + 1 for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        calls = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert namespace
        assert calls.isdisjoint(forbidden_calls)
        assert len(source.splitlines()) <= (300 if source_path == roots[0] else 200)
        assert max(sizes, default=0) <= 40
        assert ".resolve(" not in source
    assert dict(os.environ) == before_environment
    assert tuple(tmp_path.iterdir()) == before_entries
    if before_fds is not None:
        assert _fd_count() == before_fds
