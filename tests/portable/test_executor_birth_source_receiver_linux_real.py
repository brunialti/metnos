"""Focused Linux integration proof for the G6-B2 source receiver."""
from __future__ import annotations

import os
import inspect
import select
import signal
import stat
import sys
from pathlib import Path

import pytest

from install import executor_birth_source_receiver as receiver
from executor_birth_distribution_assembler import (
    DistributionAssemblerError, decode_received_source_v1,
)


linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the B2 filesystem transaction is Linux-only",
)


def _account(name: str = "metnos") -> receiver._ServiceAccountV1:
    return receiver._ServiceAccountV1(
        name, 12345, 12345, (12345,), "/var/lib/metnos", "/usr/sbin/nologin",
    )


def _source(root: Path) -> Path:
    source = root / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "README.md").write_bytes(b"received source\n")
    (source / "README.md").chmod(0o644)
    (source / "pkg" / "run.py").write_bytes(b"print('ok')\n")
    (source / "pkg" / "run.py").chmod(0o755)
    return source


@pytest.fixture(autouse=True)
def _stable_account(monkeypatch, request):
    if request.node.name == "test_productive_fixed_root_in_disposable_vm":
        return
    monkeypatch.setattr(
        receiver, "_service_account_snapshot_v1", lambda name: _account(name),
    )


@linux_only
def test_receive_is_content_addressed_idempotent_and_confined(tmp_path: Path) -> None:
    source = _source(tmp_path)
    ownership = tmp_path / "ownership"
    from executor_birth_ownership_coordinator import _deployment_lock_for_test_v1

    with _deployment_lock_for_test_v1(ownership):
        pass
    protected: list[Path] = []
    for relative in ("chain-v1", "releases-v1", "coordinator-v1"):
        sentinel = ownership / relative / "sentinel"
        sentinel.parent.mkdir()
        sentinel.write_bytes(relative.encode("ascii"))
        protected.append(sentinel)
    protected_before = {
        item: (item.read_bytes(), item.stat().st_dev, item.stat().st_ino, item.stat().st_mode)
        for item in protected
    }
    untouched = tmp_path / "other-root"
    untouched.mkdir()
    sentinel = untouched / "sentinel"
    sentinel.write_bytes(b"unchanged")
    before = (sentinel.stat(), sentinel.read_bytes())

    source_id = receiver._receive_source_for_test_v1(
        str(source), "metnos", ownership,
    )
    assert receiver._receive_source_for_test_v1(
        str(source), "metnos", ownership,
    ) == source_id
    account_id = receiver._receive_source_for_test_v1(
        str(source), "metnos-alt", ownership,
    )
    assert account_id != source_id
    final = ownership / "incoming-v1" / "sources-v1" / source_id
    record = decode_received_source_v1(
        (final / "received-source-v1.json").read_bytes(),
    )
    assert record.source_id == source_id
    assert [(item.path, item.mode) for item in record.files] == [
        ("README.md", 0o644), ("pkg/run.py", 0o755),
    ]
    assert stat.S_IMODE(final.stat().st_mode) == 0o755
    assert stat.S_IMODE((final / "pkg").stat().st_mode) == 0o755
    assert not any(
        name.name.startswith(".")
        for name in (ownership / "incoming-v1" / "sources-v1").iterdir()
    )
    after = sentinel.stat()
    assert sentinel.read_bytes() == before[1]
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_size) == (
        before[0].st_dev, before[0].st_ino, before[0].st_mode, before[0].st_size,
    )
    assert {
        item: (item.read_bytes(), item.stat().st_dev, item.stat().st_ino, item.stat().st_mode)
        for item in protected
    } == protected_before

    (source / "README.md").write_bytes(b"different\n")
    (source / "README.md").chmod(0o644)
    changed_id = receiver._receive_source_for_test_v1(
        str(source), "metnos", ownership,
    )
    assert changed_id != source_id
    assert {item.name for item in final.parent.iterdir()} == {
        source_id, account_id, changed_id,
    }


@pytest.mark.parametrize(
    "mutation", [
        "symlink", "hardlink", "empty", "reserved", "substitution",
        "ancestor-substitution",
    ],
)
@linux_only
def test_source_links_empty_directories_and_reserved_descriptor_are_rejected(
    tmp_path: Path, mutation: str, monkeypatch,
) -> None:
    source = _source(tmp_path / "input")
    if mutation == "symlink":
        (source / "link").symlink_to("README.md")
    elif mutation == "hardlink":
        os.link(source / "README.md", source / "alias")
    elif mutation == "empty":
        (source / "empty").mkdir()
    else:
        if mutation == "reserved":
            (source / "received-source-v1.json").write_bytes(b"reserved")
            (source / "received-source-v1.json").chmod(0o644)
        else:
            original = receiver.received_source_file_hash_v1
            replaced = False

            def replace_after_read(path, size, chunks):
                nonlocal replaced
                digest = original(path, size, chunks)
                if path == "README.md" and not replaced:
                    replaced = True
                    if mutation == "substitution":
                        replacement = source / ".replacement"
                        replacement.write_bytes(b"changed object\n")
                        replacement.chmod(0o644)
                        replacement.replace(source / "README.md")
                    else:
                        original_parent = source.parent
                        moved = original_parent.with_name(original_parent.name + "-old")
                        original_parent.rename(moved)
                        source.parent.mkdir()
                        replacement_source = source.parent / source.name
                        replacement_source.mkdir()
                        replacement = replacement_source / "README.md"
                        replacement.write_bytes(b"changed object\n")
                        replacement.chmod(0o644)
                return digest

            monkeypatch.setattr(
                receiver, "received_source_file_hash_v1", replace_after_read,
            )
    ownership = tmp_path / "ownership"
    descriptors_before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(DistributionAssemblerError) as failure:
        receiver._receive_source_for_test_v1(str(source), "metnos", ownership)
    assert len(os.listdir("/proc/self/fd")) == descriptors_before
    assert failure.value.code in {
        "birth_ownership_deployment_invalid", "birth_ownership_deployment_unsafe",
    }
    incoming = ownership / "incoming-v1"
    assert not incoming.exists() or {
        item.name for item in incoming.iterdir()
    } <= {"sources-v1"}


@linux_only
@pytest.mark.parametrize("stage", ["structured", "final"])
def test_rename_fsync_failures_converge_by_durable_state(
    tmp_path: Path, monkeypatch, stage: str,
) -> None:
    source = _source(tmp_path)
    ownership = tmp_path / "ownership"
    original_fsync = receiver.os.fsync
    failed = False

    def fail_parent_once(descriptor: int) -> None:
        nonlocal failed
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        structured = list((ownership / "incoming-v1" / "sources-v1").glob(".*.tmp"))
        finals = [
            item for item in (ownership / "incoming-v1" / "sources-v1").iterdir()
            if not item.name.startswith(".")
        ] if (ownership / "incoming-v1" / "sources-v1").exists() else []
        at_stage = (
            stage == "structured" and target.endswith("/incoming-v1") and structured
        ) or (
            stage == "final" and target.endswith("/sources-v1") and finals
        )
        if not failed and at_stage:
            failed = True
            raise OSError("injected parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(receiver.os, "fsync", fail_parent_once)
    with pytest.raises(DistributionAssemblerError) as failure:
        receiver._receive_source_for_test_v1(str(source), "metnos", ownership)
    assert failure.value.code == "birth_ownership_recovery_required"
    assert failed
    if stage == "structured":
        assert len(list((ownership / "incoming-v1" / "sources-v1").glob(".*.tmp"))) == 1
    else:
        assert len([
            item for item in (ownership / "incoming-v1" / "sources-v1").iterdir()
            if not item.name.startswith(".")
        ]) == 1
    monkeypatch.setattr(receiver.os, "fsync", original_fsync)
    source_id = receiver._receive_source_for_test_v1(
        str(source), "metnos", ownership,
    )
    assert (ownership / "incoming-v1" / "sources-v1" / source_id).is_dir()


@linux_only
def test_overlap_recovery_conflicts_and_failed_temp_setup_are_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _source(tmp_path)
    ownership = tmp_path / "ownership"
    source_id = receiver._receive_source_for_test_v1(str(source), "metnos", ownership)
    final = ownership / "incoming-v1" / "sources-v1" / source_id

    with pytest.raises(DistributionAssemblerError, match="deployment_unsafe"):
        receiver._receive_source_for_test_v1(
            str(final / "pkg"), "metnos", ownership,
        )
    ancestor_source = _source(tmp_path / "ancestor")
    nested_ownership = ancestor_source / "managed"
    with pytest.raises(DistributionAssemblerError, match="deployment_unsafe"):
        receiver._receive_source_for_test_v1(
            str(ancestor_source), "metnos", nested_ownership,
        )
    assert not nested_ownership.exists()

    foreign = final.parent / (".sha256:" + "f" * 64 + ".tmp")
    foreign.mkdir(mode=0o755)
    with pytest.raises(DistributionAssemblerError, match="recovery_required"):
        receiver._receive_source_for_test_v1(str(source), "metnos", ownership)
    foreign.rmdir()

    (final / "README.md").write_bytes(b"altered bytes\n")
    (final / "README.md").chmod(0o644)
    with pytest.raises(DistributionAssemblerError, match="recovery_required"):
        receiver._receive_source_for_test_v1(str(source), "metnos", ownership)

    clean_ownership = tmp_path / "clean-ownership"
    original_fchmod = receiver.os.fchmod
    failed = False

    def fail_receive_mode(descriptor: int, mode: int) -> None:
        nonlocal failed
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        if not failed and ".receive-" in target and mode == 0o700:
            failed = True
            raise OSError("injected temporary setup failure")
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(receiver.os, "fchmod", fail_receive_mode)
    with pytest.raises(DistributionAssemblerError, match="recovery_required"):
        receiver._receive_source_for_test_v1(
            str(source), "metnos", clean_ownership,
        )
    assert failed
    incoming = clean_ownership / "incoming-v1"
    assert {item.name for item in incoming.iterdir()} == {"sources-v1"}


def test_platform_and_euid_refuse_before_grammar_or_io(monkeypatch) -> None:
    monkeypatch.setattr(receiver.sys, "platform", "win32")
    monkeypatch.setattr(
        receiver.os, "geteuid", lambda: (_ for _ in ()).throw(AssertionError("euid")),
        raising=False,
    )
    with pytest.raises(DistributionAssemblerError) as failure:
        receiver._receive_source_v1("not absolute", "bad user")
    assert failure.value.code == "birth_ownership_platform_unsupported"

    monkeypatch.setattr(receiver.sys, "platform", "linux")
    monkeypatch.setattr(receiver.os, "geteuid", lambda: 12345)
    with pytest.raises(DistributionAssemblerError) as failure:
        receiver._receive_source_v1("not absolute", "bad user")
    assert failure.value.code == "birth_ownership_deployment_unsafe"

    assert receiver.__all__ == ["main"]
    assert tuple(inspect.signature(receiver._receive_source_v1).parameters) == (
        "source", "service_user",
    )


@linux_only
def test_account_authority_rejects_writable_units_linger_and_manager(
    tmp_path: Path, monkeypatch,
) -> None:
    assert tuple(map(str, receiver._GLOBAL_USER_UNIT_ROOTS_V1)) == (
        "/etc/xdg/systemd/user", "/etc/systemd/user", "/run/systemd/user",
        "/usr/local/share/systemd/user", "/usr/share/systemd/user",
        "/var/lib/snapd/desktop/systemd/user",
        "/usr/local/lib/systemd/user", "/usr/lib/systemd/user",
    )
    runtime = tmp_path / "run-user"
    linger = tmp_path / "linger"
    runtime.mkdir()
    linger.mkdir()
    account = receiver._ServiceAccountV1(
        "metnos", os.geteuid(), os.getegid(), (os.getegid(),),
        str(tmp_path / "writable-home"), "/usr/sbin/nologin",
    )
    Path(account.home).mkdir()
    with pytest.raises(DistributionAssemblerError, match="user unit root"):
        receiver._require_closed_user_authority_v1(
            account, linger_root=linger, runtime_root=runtime,
        )

    closed = receiver._ServiceAccountV1(
        "metnos", 12345, 12345, (12345,),
        "/nonexistent-metnos-home", "/usr/sbin/nologin",
    )
    (linger / closed.name).write_bytes(b"")
    with pytest.raises(DistributionAssemblerError, match="service account"):
        receiver._require_closed_user_authority_v1(
            closed, linger_root=linger, runtime_root=runtime,
        )
    (linger / closed.name).unlink()
    (runtime / str(closed.uid) / "systemd").mkdir(parents=True)
    with pytest.raises(DistributionAssemblerError, match="service account"):
        receiver._require_closed_user_authority_v1(
            closed, linger_root=linger, runtime_root=runtime,
        )

    routed: list[Path] = []
    missing_linger = tmp_path / "missing-linger"
    missing_runtime = tmp_path / "missing-runtime"
    with monkeypatch.context() as scoped:
        scoped.setattr(
            receiver, "_require_user_unit_root_closed_v1",
            lambda path, _account: routed.append(path),
        )
        receiver._require_closed_user_authority_v1(
            closed, linger_root=missing_linger,
            runtime_root=missing_runtime,
        )
    expected_routes = tuple(
        Path(closed.home).joinpath(*suffix)
        for suffix in receiver._USER_UNIT_HOME_SUFFIXES_V1
    ) + tuple(
        missing_runtime.joinpath(
            str(closed.uid), *suffix,
        )
        for suffix in receiver._USER_UNIT_RUNTIME_SUFFIXES_V1
    ) + receiver._GLOBAL_USER_UNIT_ROOTS_V1
    assert tuple(routed) == expected_routes

    artifact_root = tmp_path / "closed-global-root"
    artifact_root.mkdir()
    writable_unit = artifact_root / "metnos.service"
    writable_unit.write_bytes(b"[Service]\n")
    writable_unit.chmod(0o600)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            receiver, "_account_can_create_v1", lambda _info, _account: False,
        )
        with pytest.raises(DistributionAssemblerError, match="user unit root"):
            receiver._require_user_unit_root_closed_v1(artifact_root, account)
    assert writable_unit.read_bytes() == b"[Service]\n"

    assert tuple(map(str, receiver._GLOBAL_USER_UNIT_ROOTS_V1)) == (
        "/etc/xdg/systemd/user", "/etc/systemd/user", "/run/systemd/user",
        "/usr/local/share/systemd/user", "/usr/share/systemd/user",
        "/var/lib/snapd/desktop/systemd/user",
        "/usr/local/lib/systemd/user", "/usr/lib/systemd/user",
    )


@pytest.mark.parametrize("residue", ["incoming", "sources"])
@linux_only
def test_restrictive_empty_namespace_residue_is_repaired(
    tmp_path: Path, residue: str,
) -> None:
    ownership = tmp_path / residue / "ownership"
    from executor_birth_ownership_coordinator import _deployment_lock_for_test_v1

    with _deployment_lock_for_test_v1(ownership):
        pass
    incoming = ownership / "incoming-v1"
    if residue == "incoming":
        incoming.mkdir(mode=0o700)
    else:
        incoming.mkdir(mode=0o755)
        incoming.chmod(0o755)
        (incoming / "sources-v1").mkdir(mode=0o700)
    source_id = receiver._receive_source_for_test_v1(
        str(_source(tmp_path / residue / "input")), "metnos", ownership,
    )
    sources = incoming / "sources-v1"
    assert stat.S_IMODE(incoming.stat().st_mode) == 0o755
    assert stat.S_IMODE(sources.stat().st_mode) == 0o755
    assert (sources / source_id).is_dir()


@pytest.mark.parametrize("shape", ["wide", "too-deep"])
@linux_only
def test_source_shape_is_bounded_under_descriptor_limit(
    tmp_path: Path, shape: str,
) -> None:
    import resource

    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard < 64:
        pytest.skip("hard descriptor limit below the B2 proof threshold")
    source = tmp_path / (shape + "-source")
    if shape == "wide":
        for index in range(80):
            directory = source / f"d{index:03d}"
            directory.mkdir(parents=True)
            payload = directory / "f"
            payload.write_bytes(b"x")
            payload.chmod(0o644)
    else:
        directory = source
        for _index in range(receiver.MAX_SOURCE_PATH_DEPTH_V1):
            directory /= "d"
        directory.mkdir(parents=True)
        payload = directory / "f"
        payload.write_bytes(b"x")
        payload.chmod(0o644)
    ownership = tmp_path / (shape + "-ownership")
    result_read, result_write = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - isolated resource-limit worker
        os.close(result_read)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
        try:
            source_id = receiver._receive_source_for_test_v1(
                str(source), "metnos", ownership,
            )
            payload = ("ok:" + source_id).encode("ascii")
        except DistributionAssemblerError as exc:
            payload = ("closed:" + exc.code + ":" + exc.detail).encode("ascii")
        except BaseException as exc:
            payload = ("error:" + repr(exc)).encode("utf-8", "replace")
        os.write(result_write, payload[:4096])
        os.close(result_write)
        os._exit(0)
    os.close(result_write)
    payload = os.read(result_read, 4096).decode("utf-8", "replace")
    os.close(result_read)
    _pid, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    if shape == "wide":
        assert payload.startswith("ok:sha256:"), payload
    else:
        assert payload == (
            "closed:birth_ownership_deployment_invalid:source depth"
        )


@linux_only
def test_cleanup_accepts_the_maximum_bounded_depth(tmp_path: Path) -> None:
    parent = tmp_path / "cleanup-parent"
    parent.mkdir()
    name = ".receive-" + "0" * 32 + ".tmp"
    tree = parent / name
    tree.mkdir(mode=0o700)
    current = tree
    for _index in range(receiver.MAX_SOURCE_PATH_DEPTH_V1 - 1):
        current = current / "d"
        current.mkdir()
    leaf = current / "leaf"
    leaf.write_bytes(b"x")
    leaf.chmod(0o644)
    parent_fd = os.open(parent, receiver._DIRECTORY_FLAGS)
    try:
        identity = receiver._identity(tree.stat())
        receiver._remove_owned_tree_at_v1(
            parent_fd, name, expected_identity=identity,
            owner=(os.geteuid(), os.getegid()),
        )
    finally:
        os.close(parent_fd)
    assert not tree.exists()


@linux_only
def test_interrupted_cleanup_is_safe_to_repeat(tmp_path: Path, monkeypatch) -> None:
    parent = tmp_path / "cleanup-retry-parent"
    parent.mkdir()
    name = ".receive-" + "1" * 32 + ".tmp"
    tree = parent / name
    tree.mkdir(mode=0o700)
    for filename in ("a", "b"):
        payload = tree / filename
        payload.write_bytes(filename.encode("ascii"))
        payload.chmod(0o644)
    expected_identity = receiver._identity(tree.stat())
    parent_fd = os.open(parent, receiver._DIRECTORY_FLAGS)
    original_unlink = receiver.os.unlink

    def fail_second_file(path, *args, **kwargs):
        if path == "b":
            raise OSError("injected cleanup interruption")
        return original_unlink(path, *args, **kwargs)

    try:
        monkeypatch.setattr(receiver.os, "unlink", fail_second_file)
        with pytest.raises(DistributionAssemblerError, match="recovery_required"):
            receiver._remove_owned_tree_at_v1(
                parent_fd, name, expected_identity=expected_identity,
                owner=(os.geteuid(), os.getegid()),
            )
        assert not (tree / "a").exists()
        assert (tree / "b").read_bytes() == b"b"

        monkeypatch.setattr(receiver.os, "unlink", original_unlink)
        receiver._remove_owned_tree_at_v1(
            parent_fd, name, expected_identity=expected_identity,
            owner=(os.geteuid(), os.getegid()),
        )
    finally:
        os.close(parent_fd)
    assert not tree.exists()


@linux_only
def test_directory_binding_failure_does_not_leak_descriptor(
    tmp_path: Path, monkeypatch,
) -> None:
    parent = tmp_path / "binding"
    child = parent / "child"
    sibling = parent / "sibling"
    child.mkdir(parents=True)
    sibling.mkdir()
    parent_fd = os.open(parent, receiver._DIRECTORY_FLAGS)
    real_stat = receiver.os.stat

    def mismatched_stat(path, *args, **kwargs):
        if path == "child" and kwargs.get("dir_fd") == parent_fd:
            return real_stat("sibling", dir_fd=parent_fd, follow_symlinks=False)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(receiver.os, "stat", mismatched_stat)
    before = len(os.listdir("/proc/self/fd"))
    try:
        with pytest.raises(DistributionAssemblerError, match="directory binding"):
            receiver._open_child_directory_v1(parent_fd, "child")
        assert len(os.listdir("/proc/self/fd")) == before
    finally:
        os.close(parent_fd)


@linux_only
def test_copy_destination_open_failure_does_not_leak_source_descriptor(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _source(tmp_path / "copy-source")
    temporary = tmp_path / "copy-destination"
    temporary.mkdir()
    source_fd = os.open(source, receiver._DIRECTORY_FLAGS)
    temporary_fd = os.open(temporary, receiver._DIRECTORY_FLAGS)
    try:
        entries = receiver._scan_source_v1(source_fd)
        source_entries = receiver._entry_map(entries)
        item = source_entries["README.md"]

        def fail_destination_open(_root_fd, _parts):
            raise DistributionAssemblerError(
                "birth_ownership_deployment_unsafe", "injected destination",
            )

        monkeypatch.setattr(
            receiver, "_open_descendant_directory_v1", fail_destination_open,
        )
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(DistributionAssemblerError, match="injected destination"):
            receiver._copy_source_file_v1(
                source_fd, temporary_fd, item, source_entries,
                owner=(os.geteuid(), os.getegid()),
            )
        assert len(os.listdir("/proc/self/fd")) == before
    finally:
        os.close(temporary_fd)
        os.close(source_fd)


@linux_only
def test_forged_session_is_rejected_before_filesystem() -> None:
    from executor_birth_ownership_coordinator import OwnershipCoordinatorError

    with pytest.raises(OwnershipCoordinatorError) as failure:
        receiver._receive_source_locked_core_v1(
            "/does-not-exist", "metnos", object(),
        )
    assert failure.value.code == "birth_ownership_deployment_lock_invalid"


_RECEIVER_KILL_STAGES_V1 = (
    "before_first_write",
    "mid_file",
    "after_file",
    "after_subdirectory_fsync",
    "before_rename",
    "after_rename",
    "before_parent_fsync",
    "after_parent_fsync",
)


def _pause_for_sigkill_v1(ready_write: int) -> None:
    os.write(ready_write, b"1")
    signal.pause()
    raise AssertionError("unreachable")


def _kill_productive_receive_at_stage(source: Path, stage: str) -> None:
    ready_read, ready_write = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - child is deliberately killed
        os.close(ready_read)
        if stage == "before_first_write":
            original_write = receiver._write_all_v1
            stopped = False

            def stop_before_first_write(descriptor, payload):
                nonlocal stopped
                if not stopped:
                    stopped = True
                    _pause_for_sigkill_v1(ready_write)
                return original_write(descriptor, payload)

            receiver._write_all_v1 = stop_before_first_write
        elif stage == "mid_file":
            original_hash = receiver.received_source_file_hash_v1

            def stop_mid_file(path, size, chunks):
                if path != "large.bin":
                    return original_hash(path, size, chunks)
                iterator = iter(chunks)
                next(iterator)
                _pause_for_sigkill_v1(ready_write)

            receiver.received_source_file_hash_v1 = stop_mid_file
        elif stage == "after_file":
            original_copy = receiver._copy_source_file_v1

            def stop_after_copy(*args, **kwargs):
                result = original_copy(*args, **kwargs)
                item = args[2]
                if item.path == "README.md":
                    _pause_for_sigkill_v1(ready_write)
                return result

            receiver._copy_source_file_v1 = stop_after_copy
        elif stage == "after_subdirectory_fsync":
            original_fsync = receiver.os.fsync
            original_copy = receiver._copy_source_file_v1
            copying_subdirectory_file = False

            def mark_subdirectory_copy(*args, **kwargs):
                nonlocal copying_subdirectory_file
                item = args[2]
                if item.path != "pkg/run.py":
                    return original_copy(*args, **kwargs)
                copying_subdirectory_file = True
                try:
                    return original_copy(*args, **kwargs)
                finally:
                    copying_subdirectory_file = False

            def stop_after_subdirectory_fsync(descriptor):
                target = os.readlink(f"/proc/self/fd/{descriptor}")
                original_fsync(descriptor)
                if (
                    copying_subdirectory_file and target.endswith("/pkg")
                    and os.path.isfile(os.path.join(target, "run.py"))
                ):
                    assert not os.path.exists(
                        os.path.join(os.path.dirname(target), "z-last.txt")
                    )
                    _pause_for_sigkill_v1(ready_write)

            receiver._copy_source_file_v1 = mark_subdirectory_copy
            receiver.os.fsync = stop_after_subdirectory_fsync
        elif stage == "before_rename":
            original_rename = receiver._rename_no_replace_v1

            def stop_before_rename(*args, **kwargs):
                _pause_for_sigkill_v1(ready_write)
                return original_rename(*args, **kwargs)

            receiver._rename_no_replace_v1 = stop_before_rename
        elif stage == "after_rename":
            original_stat = receiver.os.stat

            def stop_after_raw_rename(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if (
                    isinstance(path, str) and path.startswith(".sha256:")
                    and path.endswith(".tmp")
                    and kwargs.get("dir_fd") is not None
                    and kwargs.get("follow_symlinks") is False
                ):
                    _pause_for_sigkill_v1(ready_write)
                return result

            receiver.os.stat = stop_after_raw_rename
        else:
            original_fsync = receiver.os.fsync
            original_stat = receiver.os.stat
            rename_observed = False

            def observe_structured_rename(path, *args, **kwargs):
                nonlocal rename_observed
                result = original_stat(path, *args, **kwargs)
                if (
                    isinstance(path, str) and path.startswith(".sha256:")
                    and path.endswith(".tmp")
                    and kwargs.get("dir_fd") is not None
                    and kwargs.get("follow_symlinks") is False
                ):
                    rename_observed = True
                return result

            def stop_at_parent_fsync(descriptor):
                if rename_observed:
                    if stage == "before_parent_fsync":
                        _pause_for_sigkill_v1(ready_write)
                    original_fsync(descriptor)
                    _pause_for_sigkill_v1(ready_write)
                return original_fsync(descriptor)

            receiver.os.stat = observe_structured_rename
            receiver.os.fsync = stop_at_parent_fsync
        try:
            receiver._receive_source_v1(str(source), "nobody")
        except BaseException as exc:
            detail = (
                f"E:{type(exc).__name__}:"
                f"{getattr(exc, 'code', '')}:{getattr(exc, 'detail', '')}"
            ).encode("utf-8", "replace")
            try:
                os.write(ready_write, detail[:4096])
            except OSError:
                pass
            os._exit(91)
        os._exit(92)

    os.close(ready_write)
    readable, _writable, _exceptional = select.select([ready_read], [], [], 10.0)
    if not readable:
        os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)
        os.close(ready_read)
        pytest.fail(f"receiver did not reach {stage} killpoint")
    marker = os.read(ready_read, 4096)
    os.close(ready_read)
    if marker != b"1":
        _pid, status = os.waitpid(child, 0)
        pytest.fail(
            f"receiver exited before {stage}: {status}: "
            f"{marker.decode('utf-8', 'replace')}"
        )
    os.kill(child, signal.SIGKILL)
    _pid, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL


def _remove_productive_receive_residue_v1(incoming: Path) -> None:
    from executor_birth_ownership_coordinator import _deployment_lock_v1

    with _deployment_lock_v1():
        incoming_fd = os.open(incoming, receiver._DIRECTORY_FLAGS)
        try:
            names = [
                item.name for item in incoming.iterdir()
                if item.name.startswith(".receive-") and item.name.endswith(".tmp")
            ]
            assert len(names) == 1
            info = os.stat(
                names[0], dir_fd=incoming_fd, follow_symlinks=False,
            )
            receiver._remove_owned_tree_at_v1(
                incoming_fd, names[0], expected_identity=receiver._identity(info),
                owner=(0, 0),
            )
        finally:
            os.close(incoming_fd)


@pytest.mark.skipif(
    os.environ.get("METNOS_REQUIRE_REAL_B2_RECEIVER_LINUX") != "1",
    reason="the fixed productive root is tested only in a disposable root VM",
)
@linux_only
def test_productive_fixed_root_in_disposable_vm(
    tmp_path: Path, capsys,
) -> None:
    from executor_birth_ownership_authorities import (
        DEFAULT_OWNERSHIP_ROOT_V1 as authoritative_root,
    )

    assert os.geteuid() == 0
    assert receiver.DEFAULT_OWNERSHIP_ROOT_V1 is authoritative_root
    ownership = authoritative_root
    assert not ownership.exists()
    previous_umask = os.umask(0o022)
    try:
        ownership.mkdir(parents=True, mode=0o755)
    finally:
        os.umask(previous_umask)
    incoming = ownership / "incoming-v1"
    incoming.mkdir(mode=0o700)
    incoming.chmod(0o700)
    source = _source(tmp_path)
    assert receiver.main([
        "receive", "--source", str(source), "--service-user", "nobody",
    ]) == 0
    output = capsys.readouterr()
    source_id = output.out.strip()
    assert output.err == ""
    assert source_id.startswith("sha256:") and len(source_id) == 71
    final = ownership / "incoming-v1" / "sources-v1" / source_id
    record = decode_received_source_v1(
        (final / "received-source-v1.json").read_bytes(),
    )
    assert record.service_user == "nobody"
    assert stat.S_IMODE(incoming.stat().st_mode) == 0o755

    sources = incoming / "sources-v1"
    structured_stages = {
        "after_rename", "before_parent_fsync", "after_parent_fsync",
    }
    for stage in _RECEIVER_KILL_STAGES_V1:
        crash_source = _source(tmp_path / ("crash-" + stage))
        large = crash_source / "large.bin"
        large.write_bytes(b"x" * (2 * 1024 * 1024))
        large.chmod(0o644)
        sibling = crash_source / "z-last.txt"
        sibling.write_bytes(b"last\n")
        sibling.chmod(0o644)
        stage_marker = crash_source / "stage.txt"
        stage_marker.write_bytes(stage.encode("ascii"))
        stage_marker.chmod(0o644)

        _kill_productive_receive_at_stage(crash_source, stage)
        if stage in structured_stages:
            assert not list(incoming.glob(".receive-*.tmp"))
            assert len(list(sources.glob(".sha256:*.tmp"))) == 1
            recovered_id = receiver._receive_source_v1(
                str(crash_source), "nobody",
            )
            recovered = sources / recovered_id
            assert recovered.is_dir()
            assert (recovered / "large.bin").stat().st_size == 2 * 1024 * 1024
            assert (recovered / "z-last.txt").read_bytes() == b"last\n"
        else:
            assert len(list(incoming.glob(".receive-*.tmp"))) == 1
            assert not list(sources.glob(".sha256:*.tmp"))
            with pytest.raises(DistributionAssemblerError) as failure:
                receiver._receive_source_v1(str(crash_source), "nobody")
            assert failure.value.code == "birth_ownership_recovery_required"
            _remove_productive_receive_residue_v1(incoming)
