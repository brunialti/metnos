"""Linux adapter tests that never mutate the productive host layout."""
from __future__ import annotations

import ast
import errno
import os
from pathlib import Path, PurePosixPath
import stat
from types import SimpleNamespace

import pytest

from install import executor_birth_host_journal_posix as journal_posix
from install import executor_birth_host_posix as posix
from install import executor_birth_host_provisioning as provisioning
import executor_birth_host_layout as layout
import executor_birth_account_identity as identity


def _open_directory(path) -> int:
    return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def test_fd_acl_observation_uses_supported_fd_signature(monkeypatch) -> None:
    calls = []

    def getxattr(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError(errno.ENODATA, "absent")

    monkeypatch.setattr(posix.os, "getxattr", getxattr)
    assert posix._acl_present_v1(17) == (False, False)
    assert [call[0][0] for call in calls] == [17, 17]
    assert all(not kwargs for _, kwargs in calls)


def test_real_fd_acl_observation_is_supported_or_fails_closed(tmp_path) -> None:
    descriptor = _open_directory(tmp_path)
    try:
        try:
            observed = posix._acl_present_v1(descriptor)
        except journal_posix.HostProvisioningPosixError as exc:
            assert isinstance(exc.__cause__, OSError)
            assert exc.__cause__.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}
        else:
            assert observed == (False, False)
    finally:
        os.close(descriptor)


def test_acl_enotsup_is_not_misreported_as_absent(monkeypatch) -> None:
    def unsupported(*_args, **_kwargs):
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr(posix.os, "getxattr", unsupported)
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="POSIX ACL observation",
    ):
        posix._acl_present_v1(17)


def test_acl_removal_uses_the_open_descriptor_and_fails_closed(monkeypatch) -> None:
    calls = []

    def remove(target, name):
        calls.append((target, name))

    monkeypatch.setattr(posix.os, "removexattr", remove)
    posix._remove_acl_v1(23)
    assert calls == [
        (23, "system.posix_acl_access"),
        (23, "system.posix_acl_default"),
    ]

    def unsupported(*_args):
        raise OSError(errno.ENOTSUP, "no ACL API")

    monkeypatch.setattr(posix.os, "removexattr", unsupported)
    with pytest.raises(journal_posix.HostProvisioningPosixError):
        posix._remove_acl_v1(23)


def test_observation_rejects_concurrent_child_replacement(tmp_path, monkeypatch) -> None:
    child = tmp_path / "child"
    child.mkdir()
    target = SimpleNamespace(path=PurePosixPath(child.as_posix()))
    real_stat, calls = posix.os.stat, []

    def changing_stat(*args, **kwargs):
        info = real_stat(*args, **kwargs)
        if args[0] == "child":
            calls.append(info)
        if len(calls) == 3 and args[0] == "child":
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
        return info

    monkeypatch.setattr(posix.os, "stat", changing_stat)
    monkeypatch.setattr(posix, "_TRUST_ANCHORS_V1", frozenset())
    monkeypatch.setattr(posix, "_acl_present_v1", lambda _fd: (False, False))
    effects = posix._PosixHostEffectsV1()
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="layout path replaced",
    ):
        effects._observe_target(target)
    assert len(calls) == 3


@pytest.mark.parametrize("field", ["st_mode", "st_ctime_ns"])
def test_observation_rejects_mode_or_acl_metadata_race(
    tmp_path, monkeypatch, field,
) -> None:
    child = tmp_path / "child"
    child.mkdir()
    target = SimpleNamespace(path=PurePosixPath(child.as_posix()))
    real_require, calls = posix._require_bound_v1, 0

    def changed_final(parent, name, descriptor):
        nonlocal calls
        info = real_require(parent, name, descriptor)
        if name != "child":
            return info
        calls += 1
        if calls != 2:
            return info
        values = {
            item: getattr(info, item)
            for item in (
                "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid",
                "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns",
            )
        }
        values[field] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(posix, "_TRUST_ANCHORS_V1", frozenset())
    monkeypatch.setattr(posix, "_acl_present_v1", lambda _fd: (False, False))
    monkeypatch.setattr(posix, "_require_bound_v1", changed_final)
    with pytest.raises(journal_posix.HostProvisioningPosixError):
        posix._PosixHostEffectsV1()._observe_target(target)


def test_symlink_is_observed_as_conflict_without_following(
    tmp_path, monkeypatch,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)
    target = SimpleNamespace(path=PurePosixPath(link.as_posix()))
    effects = posix._PosixHostEffectsV1()
    monkeypatch.setattr(posix, "_TRUST_ANCHORS_V1", frozenset())
    observed = effects._observe_target(target)
    assert observed.node_kind is layout.HostNodeKindV1.other


def test_fixed_account_commands_never_request_a_shell(monkeypatch) -> None:
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(posix.subprocess, "run", run)
    effects = posix._PosixHostEffectsV1()
    record = identity.PosixAccountRecordV1(
        "metnos", 991, 992, "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    snapshot = identity.PosixAccountSnapshotV1(record, (992,))
    monkeypatch.setattr(effects, "observe_primary_group", lambda: 992)
    monkeypatch.setattr(effects, "observe_account", lambda: snapshot)
    effects.create_primary_group()
    effects.create_account()
    assert calls[0][0] == [
        "/usr/sbin/groupadd", "--system", "--", "metnos",
    ]
    assert calls[1][0] == [
        "/usr/sbin/useradd", "--system", "--gid", "metnos",
        "--home-dir", "/var/lib/metnos-service",
        "--shell", "/usr/sbin/nologin", "--no-create-home", "--", "metnos",
    ]
    assert all("shell" not in kwargs for _, kwargs in calls)
    assert all(kwargs["cwd"] == "/" and kwargs["close_fds"] for _, kwargs in calls)
    assert all(
        kwargs["stdout"] is posix.subprocess.DEVNULL
        and kwargs["stderr"] is posix.subprocess.DEVNULL
        for _, kwargs in calls
    )


def test_nonzero_account_commands_accept_only_an_exact_committed_race(
    monkeypatch,
) -> None:
    effects = posix._PosixHostEffectsV1()
    record = identity.PosixAccountRecordV1(
        "metnos", 991, 992, "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    snapshot = identity.PosixAccountSnapshotV1(record, (992,))
    monkeypatch.setattr(effects, "_run", lambda _arguments: 9)
    monkeypatch.setattr(effects, "observe_primary_group", lambda: 992)
    monkeypatch.setattr(effects, "observe_account", lambda: snapshot)
    effects.create_primary_group()
    effects.create_account()
    monkeypatch.setattr(effects, "observe_primary_group", lambda: None)
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="group creation race",
    ):
        effects.create_primary_group()
    monkeypatch.setattr(effects, "observe_primary_group", lambda: 992)
    monkeypatch.setattr(effects, "observe_account", lambda: None)
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="account creation race",
    ):
        effects.create_account()


def test_useradd_is_not_attempted_with_a_nonpositive_gid(monkeypatch) -> None:
    effects = posix._PosixHostEffectsV1()
    calls = []
    monkeypatch.setattr(effects, "observe_primary_group", lambda: 0)
    monkeypatch.setattr(effects, "_run", lambda arguments: calls.append(arguments))
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="primary group identity",
    ):
        effects.create_account()
    assert calls == []


def test_chain_rebinds_every_opened_component(tmp_path, monkeypatch) -> None:
    calls = []
    real_require = posix._require_bound_v1

    def observing_require(parent, name, child):
        calls.append(name)
        return real_require(parent, name, child)

    monkeypatch.setattr(posix, "_TRUST_ANCHORS_V1", frozenset())
    monkeypatch.setattr(posix, "_require_bound_v1", observing_require)
    descriptors = posix._open_chain_v1(tmp_path)
    try:
        assert calls == [part for part in tmp_path.parts[1:] for _ in range(2)]
    finally:
        posix._close_all_v1(descriptors)


@pytest.mark.parametrize(
    "uid,mode,acl",
    [(1, 0o755, (False, False)), (0, 0o775, (False, False)),
     (0, 0o755, (True, False))],
)
def test_trust_anchor_policy_fails_closed(uid, mode, acl, monkeypatch) -> None:
    info = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=uid, st_gid=0)
    monkeypatch.setattr(posix.os, "fstat", lambda _descriptor: info)
    monkeypatch.setattr(posix, "_acl_present_v1", lambda _descriptor: acl)
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="trust anchor metadata",
    ):
        posix._require_opened_metadata_v1(3, Path("/var"), {})


def test_preexisting_bootstrap_drift_is_never_repaired_before_lock(
    monkeypatch,
) -> None:
    mutations = []
    monkeypatch.setattr(posix, "_open_chain_v1", lambda _path: [11])
    monkeypatch.setattr(posix, "_bootstrap_expected_v1", lambda: {posix._ROOT: (0, 0, 0o700)})
    monkeypatch.setattr(posix.os, "mkdir", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()))
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: 12)
    monkeypatch.setattr(posix, "_require_bound_v1", lambda *_args: None)
    monkeypatch.setattr(posix, "_require_directory_v1", lambda *_args, **_kwargs: posix._raise("directory metadata"))
    monkeypatch.setattr(posix.os, "fchown", lambda *_args: mutations.append("chown"))
    monkeypatch.setattr(posix.os, "fchmod", lambda *_args: mutations.append("chmod"))
    monkeypatch.setattr(posix, "_remove_acl_v1", lambda *_args: mutations.append("acl"))
    monkeypatch.setattr(posix.os, "close", lambda _descriptor: None)
    with pytest.raises(journal_posix.HostProvisioningPosixError):
        posix._ensure_bootstrap_root_v1()
    assert mutations == []


def test_new_bootstrap_is_created_with_the_exact_policy(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(posix, "_open_chain_v1", lambda _path: [11])
    monkeypatch.setattr(posix, "_bootstrap_expected_v1", lambda: {posix._ROOT: (0, 0, 0o700)})
    monkeypatch.setattr(posix.os, "mkdir", lambda name, mode, **_kwargs: events.append(("mkdir", name, mode)))
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: 12)
    monkeypatch.setattr(posix, "_require_bound_v1", lambda *_args: None)
    monkeypatch.setattr(posix, "_require_directory_v1", lambda _fd, mode: events.append(("verify", mode)))
    monkeypatch.setattr(posix.os, "fchown", lambda _fd, uid, gid: events.append(("chown", uid, gid)))
    monkeypatch.setattr(posix, "_remove_acl_v1", lambda _fd: events.append(("acl",)))
    monkeypatch.setattr(posix.os, "fchmod", lambda _fd, mode: events.append(("chmod", mode)))
    monkeypatch.setattr(posix.os, "fsync", lambda fd: events.append(("fsync", fd)))
    monkeypatch.setattr(posix.os, "close", lambda _descriptor: None)
    posix._ensure_bootstrap_root_v1()
    assert ("mkdir", posix._ROOT.name, 0o700) in events
    assert ("chown", 0, 0) in events
    assert ("acl",) in events
    assert ("chmod", 0o700) in events
    assert ("verify", 0o700) in events


@pytest.mark.parametrize("module", [provisioning, posix, journal_posix])
def test_product_modules_are_small_and_functions_are_bounded(module) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    spans = [
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert len(source.splitlines()) <= 400
    assert max(spans) <= 40
