from __future__ import annotations

from dataclasses import replace
import errno
import os
import inspect
from pathlib import Path, PurePosixPath
import struct

import pytest

import executor_birth_legacy_state as legacy
import executor_birth_legacy_state_request as legacy_request
import executor_birth_account_identity as account_identity
import executor_birth_host_layout as host_layout
import executor_birth_host_path_policy as host_path_policy
import executor_birth_posix_acl as posix_acl
import install.executor_birth_legacy_state_posix as adapter
from executor_birth_authoring import authoring_paths


LINUX_ONLY = pytest.mark.skipif(os.name != "posix", reason="real POSIX state observation")


def _request(root: Path) -> legacy.LegacyStateRequestV1:
    root.chmod(0o700)
    return legacy_request._build_legacy_state_request_for_test_v1(
        PurePosixPath(root.as_posix()), os.getuid(), os.getgid(),
        "sha256:" + "a" * 64,
    )


def _observe(request):
    return adapter._observe_legacy_state_for_test_v1(request)


def _mkdir(path: Path) -> None:
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in missing:
        directory.chmod(0o700)


def _write(path: Path, payload: bytes) -> None:
    _mkdir(path.parent)
    path.write_bytes(payload)
    path.chmod(0o600)


def _materialize_real_authoring(root: Path) -> Path:
    canonical = root / "contract-authoring/v1/core/sample"
    _mkdir(canonical)
    _write(canonical / "manifest.toml", b"name='sample'\n")
    control = authoring_paths(canonical, "core:sample/manifest.toml").control
    _mkdir(control)
    _write(control / "authoring.lock", b"\0")
    _write(control / "version.json", b"{}")
    return canonical


@LINUX_ONLY
def test_fresh_observation_ignores_every_non_reserved_name(tmp_path: Path) -> None:
    root = tmp_path / "state"
    _mkdir(root)
    _write(root / "unrelated.db", b"payload")
    request = _request(root)
    observation = _observe(request)
    assert observation.entries == ()
    assert legacy._classify_legacy_state_for_test_v1(
        request, observation,
    ) is legacy.LegacyStateDispositionV1.fresh


@LINUX_ONLY
def test_real_authoring_and_store_replica_are_observed_exactly(tmp_path: Path) -> None:
    root = tmp_path / "state"
    _mkdir(root)
    _materialize_real_authoring(root)
    store = root / "contract-publications/v1" / ("b" * 64) / "generations"
    _mkdir(store)
    _write(root / "contract-publications.ACTIVE", b"v1\n")
    _write(root / ".contract-publications-v1.catalog-admission.lock", b"\0")
    request = _request(root)
    observation = _observe(request)
    assert legacy._classify_legacy_state_for_test_v1(
        request, observation,
    ) is legacy.LegacyStateDispositionV1.exact_service


@pytest.mark.parametrize("mutation", ("extra", "symlink", "hardlink", "staging"))
@LINUX_ONLY
def test_unsafe_authoring_residue_is_invalid(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / mutation
    _mkdir(root)
    canonical = _materialize_real_authoring(root)
    if mutation == "extra":
        _mkdir(root / "contract-authoring/unexpected")
    elif mutation == "symlink":
        (canonical.parent / "link").symlink_to(canonical)
    elif mutation == "hardlink":
        os.link(canonical / "manifest.toml", canonical / "linked.toml")
    else:
        _mkdir(canonical.parent / (".birth-stage-" + "c" * 64))
    request = _request(root)
    observation = _observe(request)
    assert legacy._classify_legacy_state_for_test_v1(
        request, observation,
    ) is legacy.LegacyStateDispositionV1.invalid


@LINUX_ONLY
def test_observer_has_no_mutating_calls(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "state"
    _mkdir(root)
    _materialize_real_authoring(root)
    request = _request(root)

    def forbidden(*_args, **_kwargs):
        pytest.fail("read-only observer attempted a mutation")

    for name in ("mkdir", "chmod", "chown", "fchmod", "fchown", "unlink", "rename", "replace", "link"):
        monkeypatch.setattr(adapter.os, name, forbidden)
    _observe(request)


@LINUX_ONLY
def test_concurrent_reserved_root_replacement_is_rejected(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "state"
    _mkdir(root)
    _mkdir(root / "contract-authoring")
    request = _request(root)
    real_rebind = adapter._rebind
    replaced = False

    def replace_once(parent_fd: int, name: str, child_fd: int):
        nonlocal replaced
        if name == "contract-authoring" and not replaced:
            replaced = True
            (root / name).rename(root / "old-authoring")
            _mkdir(root / name)
        return real_rebind(parent_fd, name, child_fd)

    monkeypatch.setattr(adapter, "_rebind", replace_once)
    with pytest.raises(adapter.LegacyStatePosixError):
        _observe(request)
    assert replaced


def test_adapter_is_small_and_functions_are_bounded() -> None:
    assert len(inspect.getsource(adapter).splitlines()) <= 400
    for value in vars(adapter).values():
        if inspect.isfunction(value) and value.__module__ == adapter.__name__:
            assert len(inspect.getsource(value).splitlines()) <= 40


@LINUX_ONLY
def test_raw_request_is_refused_by_product_observer(tmp_path: Path) -> None:
    root = tmp_path / "state"
    _mkdir(root)
    with pytest.raises(adapter.LegacyStatePosixError, match="birth_legacy"):
        adapter.observe_legacy_state_v1(_request(root))


@pytest.mark.parametrize("name", ["bad\0name", "bad\udcffname"])
def test_invalid_inventory_names_fail_with_typed_error(monkeypatch, name) -> None:
    class Entries:
        def __enter__(self):
            return iter((type("Entry", (), {"name": name})(),))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(adapter.os, "scandir", lambda _fd: Entries())
    with pytest.raises(adapter.LegacyStatePosixError):
        adapter._names(3, 1)


@LINUX_ONLY
def test_acl_mutation_after_file_snapshot_is_rejected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "state"
    _mkdir(root)
    target = root / "contract-publications.ACTIVE"
    _write(target, b"v1\n")
    request = _request(root)
    real_acl, changed = adapter._acl, False

    def changing_acl(descriptor):
        nonlocal changed
        result = real_acl(descriptor)
        if not changed and os.fstat(descriptor).st_size == 3:
            changed = True
            os.fchmod(descriptor, 0o640)
        return result

    monkeypatch.setattr(adapter, "_acl", changing_acl)
    with pytest.raises(adapter.LegacyStatePosixError):
        _observe(request)
    assert changed


@LINUX_ONLY
def test_acl_mutation_after_directory_snapshot_is_rejected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "state"
    _mkdir(root / "contract-publications/v1")
    request = _request(root)
    target_inode = (root / "contract-publications").stat().st_ino
    real_acl, changed = adapter._acl, False

    def changing_acl(descriptor):
        nonlocal changed
        result = real_acl(descriptor)
        if not changed and os.fstat(descriptor).st_ino == target_inode:
            changed = True
            os.fchmod(descriptor, 0o750)
        return result

    monkeypatch.setattr(adapter, "_acl", changing_acl)
    with pytest.raises(adapter.LegacyStatePosixError):
        _observe(request)
    assert changed


@LINUX_ONLY
def test_open_chain_closes_unbound_child_descriptor(monkeypatch) -> None:
    opened, closed = iter((10, 11)), []
    monkeypatch.setattr(adapter.os, "open", lambda *_args, **_kwargs: next(opened))
    monkeypatch.setattr(adapter.os, "close", closed.append)
    monkeypatch.setattr(
        adapter, "_rebind",
        lambda *_args: (_ for _ in ()).throw(adapter.LegacyStatePosixError("changed")),
    )
    with pytest.raises(adapter.LegacyStatePosixError):
        adapter._open_chain(Path("/child"))
    assert closed == [11, 10]


def test_trust_anchor_catalog_remains_in_parity_with_host_owner() -> None:
    assert adapter._TRUST_ANCHORS_V1 == frozenset(
        Path(path.as_posix()) for path in host_path_policy.HOST_TRUST_ANCHORS_V1
    )


def _canonical_request() -> legacy.LegacyStateRequestV1:
    record = account_identity.PosixAccountRecordV1(
        "metnos", 41, 42, "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    snapshot = account_identity.PosixAccountSnapshotV1(record, (42,))
    return legacy.build_legacy_state_request_v1(
        snapshot, "sha256:" + "a" * 64,
    )


def test_product_observer_revalidates_replaced_canonical_request(monkeypatch) -> None:
    request = _canonical_request()
    spec = host_layout.build_host_layout_spec_v1(request._account)
    data = next(
        item.path for item in spec.objects
        if item.role is host_layout.HostPathRoleV1.data
    )
    mutant = replace(request, state_root=data)
    monkeypatch.setattr(
        adapter, "_open_chain",
        lambda _path: pytest.fail("invalid request reached filesystem"),
    )
    with pytest.raises(adapter.LegacyStatePosixError):
        adapter.observe_legacy_state_v1(mutant)


@pytest.mark.parametrize("mutation", ("platform", "flag", "getxattr", "dir_fd"))
def test_platform_contract_fails_closed_with_typed_error(monkeypatch, mutation) -> None:
    if mutation == "platform":
        monkeypatch.setattr(adapter.sys, "platform", "darwin")
    elif mutation == "flag":
        monkeypatch.delattr(adapter.os, "O_NOFOLLOW", raising=False)
    elif mutation == "getxattr":
        monkeypatch.setattr(adapter.os, "getxattr", None, raising=False)
    else:
        monkeypatch.setattr(adapter.os, "supports_dir_fd", frozenset())
    with pytest.raises(adapter.LegacyStatePosixError):
        adapter._require_platform_v1()


def test_acl_adapter_is_intentionally_stricter_than_directory_owner(monkeypatch) -> None:
    monkeypatch.setattr(errno, "ENODATA", 61, raising=False)
    base = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, 0o7, 0xFFFFFFFF)
        for tag in (0x01, 0x04, 0x20)
    )

    def getxattr(_descriptor, name):
        if name == "system.posix_acl_access":
            return base
        raise OSError(getattr(os, "ENODATA", 61), "absent")

    monkeypatch.setattr(adapter.os, "getxattr", getxattr, raising=False)
    assert adapter._acl(3) == (True, False)
    assert posix_acl.observe_posix_directory_acl_v1(3) == (
        posix_acl.PosixAclSnapshotV1(False, False)
    )
