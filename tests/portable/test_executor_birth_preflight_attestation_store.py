from __future__ import annotations

import copy
import errno
import os
import pickle
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_dominant_startup as dominant
import executor_birth_preflight_attestation_store as store
import executor_birth_preflight_store_authority as authority_owner


LINUX_ONLY = pytest.mark.skipif(os.name != "posix", reason="real POSIX attestation store")


def D(character: str) -> str:
    return "sha256:" + character * 64


def _attestation() -> tuple[str, bytes]:
    request_id = D("1")
    value = {
        "schema_version": 1, "attestation_id": None,
        "request_id": request_id, "closed_build_id": D("2"),
        "release_sequence": 1, "head_id": D("3"),
        "required_head_frame_hash": D("4"),
        "deployment_descriptor_id": D("5"),
        "service_catalog_id": D("6"), "service_coverage_hash": D("7"),
        "candidate_units_hash": D("8"),
        "administrative_bundle_hash": D("9"),
        "python_binary_hash": D("a"), "openssl_binary_hash": D("b"),
        "openssl_tcb_hash": D("c"), "systemctl_binary_hash": D("d"),
        "systemd_analyze_binary_hash": D("e"),
        "effective_units_hash": D("f"), "checked_entry_ids": ["probe"],
    }
    value["attestation_id"] = preflight._deployment_document_id_v1(
        preflight.PREFLIGHT_ATTESTATION_DOMAIN_V1, value, "attestation_id",
    )
    return request_id, preflight._canonical_json(value)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "attestations"
    root.mkdir(mode=0o755)
    return root


def _names(request_id: str) -> tuple[str, str]:
    return (
        request_id + ".json",
        "." + request_id.removeprefix("sha256:") + ".tmp",
    )


def test_test_authority_is_local_nontransferable_and_not_product(tmp_path) -> None:
    authority = store._test_authority_v1(_root(tmp_path))
    for operation in (
        copy.copy, copy.deepcopy, lambda value: pickle.dumps(value),
    ):
        with pytest.raises(TypeError):
            operation(authority)
    authority.pid += 1
    with pytest.raises(preflight.PreflightError) as failure:
        authority_owner.authorize_store_effect_v1(
            authority, authority.root, authority.chain_stop,
        )
    assert failure.value.code == preflight.CODE_RECOVERY
    with pytest.raises(preflight.PreflightError):
        store._test_authority_v1(store._product_root_v1())
    product = store._product_root_v1()
    alias = product.parent / "alias" / ".." / product.name
    with pytest.raises(preflight.PreflightError):
        store._test_authority_v1(alias)


def test_product_sessions_are_checked_before_and_after_each_effect(monkeypatch) -> None:
    sessions = (object(), object(), object())
    authority = authority_owner.product_store_authority_v1(sessions)
    calls = []
    checks = 0

    def require(value):
        nonlocal checks
        checks += 1
        calls.append(value)
        if checks == 3:
            raise RuntimeError("session invalidated")

    monkeypatch.setattr(dominant, "_require_product_sessions_v1", require)
    monkeypatch.setattr(
        authority_owner, "_require_bound_directory_v1", lambda *_args: None,
    )
    monkeypatch.setattr(
        authority_owner.os, "fsync", lambda _descriptor: calls.append("effect"),
    )
    port = authority_owner.bind_store_mutation_port_v1(
        authority, store._product_root_v1(), None, 41,
    )
    with pytest.raises(preflight.PreflightError) as failure:
        port.sync_directory()
    assert failure.value.code == preflight.CODE_RECOVERY
    assert calls == [sessions, sessions, "effect", sessions]


@LINUX_ONLY
def test_store_is_no_replace_idempotent_bounded_and_detects_residue(
    tmp_path, monkeypatch,
) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    assert store._publish_preflight_attestation_for_test_v1(
        encoded, request_id, root,
    ) == encoded
    assert store._publish_preflight_attestation_for_test_v1(
        encoded, request_id, root,
    ) == encoded

    original_read = store.os.read
    monkeypatch.setattr(
        store.os, "read", lambda descriptor, maximum: original_read(
            descriptor, min(maximum, 7),
        ),
    )
    assert store._read_preflight_attestation_for_test_v1(
        request_id, root,
    ) == encoded

    destination = root / (request_id + ".json")
    destination.write_bytes(encoded + b" ")
    with pytest.raises(preflight.PreflightError) as conflict:
        store._publish_preflight_attestation_for_test_v1(
            encoded, request_id, root,
        )
    assert conflict.value.code == preflight.CODE_RECOVERY

    destination.unlink()
    partial = root / ("." + request_id.removeprefix("sha256:") + ".tmp")
    partial.write_bytes(b"partial")
    partial.chmod(0o644)
    with pytest.raises(preflight.PreflightError) as residue:
        store._publish_preflight_attestation_for_test_v1(
            encoded, request_id, root,
        )
    assert residue.value.code == preflight.CODE_RECOVERY


def test_import_has_no_filesystem_effect(tmp_path) -> None:
    runtime = Path(store.__file__).resolve().parent
    code = (
        "import pathlib,sys;sys.dont_write_bytecode=True;"
        f"sys.path.insert(0,{str(runtime)!r});"
        f"root=pathlib.Path({str(tmp_path)!r});before=tuple(root.iterdir());"
        "import executor_birth_preflight_attestation_store as module;"
        "assert module.__all__ == [] and tuple(root.iterdir()) == before"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@LINUX_ONLY
def test_inventory_is_bounded_before_publication(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    (root / "first.json").write_bytes(b"first")
    (root / "second.json").write_bytes(b"second")
    monkeypatch.setattr(authority_owner, "MAX_STORE_ENTRIES_V1", 1)
    with pytest.raises(preflight.PreflightError) as failure:
        store._publish_preflight_attestation_for_test_v1(
            encoded, request_id, root,
        )
    assert failure.value.code == preflight.CODE_RECOVERY


@LINUX_ONLY
def test_inventory_reserves_capacity_for_a_new_final_name(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    (root / "existing.json").write_bytes(b"existing")
    monkeypatch.setattr(authority_owner, "MAX_STORE_ENTRIES_V1", 1)

    with pytest.raises(preflight.PreflightError) as failure:
        store._publish_preflight_attestation_for_test_v1(
            encoded, request_id, root,
        )

    assert failure.value.code == preflight.CODE_RECOVERY
    assert {path.name for path in root.iterdir()} == {"existing.json"}


@LINUX_ONLY
def test_inventory_at_capacity_allows_exact_existing_attestation(
    tmp_path, monkeypatch,
) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    store._publish_preflight_attestation_for_test_v1(encoded, request_id, root)
    monkeypatch.setattr(authority_owner, "MAX_STORE_ENTRIES_V1", 1)

    assert store._publish_preflight_attestation_for_test_v1(
        encoded, request_id, root,
    ) == encoded


@LINUX_ONLY
def test_publication_releases_lock_only_by_closing_its_descriptor(
    tmp_path, monkeypatch,
) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    operations = []
    monkeypatch.setattr(
        authority_owner.fcntl, "flock",
        lambda _descriptor, operation: operations.append(operation),
    )
    store._publish_preflight_attestation_for_test_v1(encoded, request_id, root)
    assert operations == [authority_owner.fcntl.LOCK_EX]


@pytest.mark.parametrize("crash_state", ("incomplete", "complete", "linked"))
@LINUX_ONLY
def test_exact_crash_states_are_recovered(tmp_path, crash_state) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    final_name, temp_name = _names(request_id)
    temporary = root / temp_name
    temporary.write_bytes(b"partial" if crash_state == "incomplete" else encoded)
    temporary.chmod(0o600 if crash_state == "incomplete" else 0o644)
    if crash_state == "linked":
        os.link(temporary, root / final_name)

    observed = store._publish_preflight_attestation_for_test_v1(
        encoded, request_id, root,
    )

    final = root / final_name
    assert observed == encoded == final.read_bytes()
    assert not temporary.exists()
    assert stat.S_IMODE(final.stat().st_mode) == 0o644
    assert final.stat().st_nlink == 1


@pytest.mark.parametrize(
    "invalid_state", ("mismatch", "symlink", "mode", "distinct"),
)
@LINUX_ONLY
def test_untrusted_crash_states_fail_closed(tmp_path, invalid_state) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    final_name, temp_name = _names(request_id)
    temporary = root / temp_name
    if invalid_state == "symlink":
        temporary.symlink_to(root / "absent")
    else:
        temporary.write_bytes(b"wrong" if invalid_state == "mismatch" else encoded)
        temporary.chmod(0o640 if invalid_state == "mode" else 0o644)
    if invalid_state == "distinct":
        final = root / final_name
        final.write_bytes(encoded)
        final.chmod(0o644)

    with pytest.raises(preflight.PreflightError) as failure:
        store._publish_preflight_attestation_for_test_v1(
            encoded, request_id, root,
        )
    assert failure.value.code == preflight.CODE_RECOVERY


@pytest.mark.parametrize("acl_name", store._ACL_NAMES_V1)
@LINUX_ONLY
def test_acl_and_unsupported_acl_query_fail_closed(
    tmp_path, monkeypatch, acl_name,
) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    original = store.os.getxattr

    def file_acl(descriptor, name):
        if stat.S_ISREG(os.fstat(descriptor).st_mode) and name == acl_name:
            return b"extended-acl"
        return original(descriptor, name)

    final_name, _ = _names(request_id)
    final = root / final_name
    final.write_bytes(encoded)
    final.chmod(0o644)
    monkeypatch.setattr(store.os, "getxattr", file_acl)
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, root)

    monkeypatch.setattr(
        store.os, "getxattr",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "no ACL")),
    )
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, root)


@LINUX_ONLY
def test_basename_and_root_rebinding_are_rejected(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    store._publish_preflight_attestation_for_test_v1(encoded, request_id, root)
    final_name, _ = _names(request_id)
    final = root / final_name
    original_read = store.os.read
    replaced = False

    def replace_name(descriptor, maximum):
        nonlocal replaced
        chunk = original_read(descriptor, maximum)
        if chunk and not replaced:
            replaced = True
            final.rename(root / "old.json")
            final.write_bytes(encoded)
            final.chmod(0o644)
        return chunk

    monkeypatch.setattr(store.os, "read", replace_name)
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, root)
    monkeypatch.setattr(store.os, "read", original_read)

    original_read_at = store._read_at_v1
    def replace_root(*args, **kwargs):
        result = original_read_at(*args, **kwargs)
        root.rename(tmp_path / "old-root")
        root.mkdir(mode=0o755)
        return result

    monkeypatch.setattr(store, "_read_at_v1", replace_root)
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, root)


@LINUX_ONLY
def test_root_swap_during_final_chain_recheck_is_rejected(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    store._publish_preflight_attestation_for_test_v1(encoded, request_id, root)
    original = store.require_acl_free_directory_chain_v1
    calls = 0

    def swap(selected, stop, *, uid, gid):
        nonlocal calls
        calls += 1
        if calls == 2:
            selected.rename(tmp_path / "old-root")
            selected.mkdir(mode=0o755)
        return original(selected, stop, uid=uid, gid=gid)

    monkeypatch.setattr(store, "require_acl_free_directory_chain_v1", swap)
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, root)


@LINUX_ONLY
def test_child_swap_after_initial_read_is_rejected(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    store._publish_preflight_attestation_for_test_v1(encoded, request_id, root)
    final = root / (request_id + ".json")
    original = store._read_at_v1

    def swap(*args, **kwargs):
        result = original(*args, **kwargs)
        final.rename(root / "old.json")
        return result

    monkeypatch.setattr(store, "_read_at_v1", swap)
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, root)


@LINUX_ONLY
def test_final_binding_order_is_root_child_root(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    store._publish_preflight_attestation_for_test_v1(encoded, request_id, root)
    events = []
    children = []
    original_root = store._assert_root_bound_v1
    original_child = store._assert_child_bound_v1
    original_read = store._read_at_v1

    def observe_read(*args, **kwargs):
        result = original_read(*args, **kwargs)
        children.append(result[2])
        return result

    def observe_root(*args, **kwargs):
        os.fstat(children[0])
        events.append("root")
        return original_root(*args, **kwargs)

    def observe_child(*args, **kwargs):
        events.append("child")
        return original_child(*args, **kwargs)

    monkeypatch.setattr(store, "_read_at_v1", observe_read)
    monkeypatch.setattr(store, "_assert_root_bound_v1", observe_root)
    monkeypatch.setattr(store, "_assert_child_bound_v1", observe_child)
    assert store._read_preflight_attestation_for_test_v1(request_id, root) == encoded
    assert events == ["root", "child", "root"]
    with pytest.raises(OSError) as closed:
        os.fstat(children[0])
    assert closed.value.errno == errno.EBADF


@LINUX_ONLY
def test_post_authorization_failure_closes_store_descriptor(
    tmp_path, monkeypatch,
) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    test_authority = store._test_authority_v1(root)
    original = authority_owner.authorize_store_effect_v1
    calls = 0
    descriptors = []

    def expire(value, selected_root, chain_stop):
        nonlocal calls
        calls += 1
        original(value, selected_root, chain_stop)
        if calls == 2:
            raise preflight._recovery("expired")

    original_open = store._open_store_v1
    def capture_open(*args, **kwargs):
        result = original_open(*args, **kwargs)
        descriptors.append(result[0])
        return result

    monkeypatch.setattr(authority_owner, "authorize_store_effect_v1", expire)
    monkeypatch.setattr(store, "_open_store_v1", capture_open)
    with pytest.raises(preflight.PreflightError):
        store._publish_preflight_attestation_core_v1(
            encoded, request_id, root=root, uid=os.getuid(), gid=os.getgid(),
            chain_stop=root.parent, authority=test_authority,
        )
    assert len(descriptors) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF


@LINUX_ONLY
def test_staging_open_closes_fd_when_post_authorization_expires(
    tmp_path, monkeypatch,
) -> None:
    root = _root(tmp_path)
    selected = store._test_authority_v1(root)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    port = authority_owner.bind_store_mutation_port_v1(
        selected, root, root.parent, directory,
    )
    original = authority_owner.authorize_store_effect_v1
    calls = 0
    descriptors = []

    def expire(value, selected_root, chain_stop):
        nonlocal calls
        calls += 1
        original(value, selected_root, chain_stop)
        if calls == 2:
            raise preflight._recovery("expired")

    original_open = authority_owner.os.open
    def capture_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(authority_owner, "authorize_store_effect_v1", expire)
    monkeypatch.setattr(authority_owner.os, "open", capture_open)
    with pytest.raises(preflight.PreflightError):
        port.create_staging(_names(D("1"))[1])
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF
    os.close(directory)


@LINUX_ONLY
def test_mutation_port_is_narrow_nontransferable_and_name_bound(tmp_path) -> None:
    root = _root(tmp_path)
    selected = store._test_authority_v1(root)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        port = authority_owner.bind_store_mutation_port_v1(
            selected, root, root.parent, directory,
        )
        assert not hasattr(authority_owner, "perform_store_effect_v1")
        assert not hasattr(authority_owner, "perform_store_descriptor_effect_v1")
        for operation in (copy.copy, copy.deepcopy, lambda value: pickle.dumps(value)):
            with pytest.raises(TypeError):
                operation(port)
        with pytest.raises(preflight.PreflightError):
            port.unlink_temporary(1)
        with pytest.raises(preflight.PreflightError):
            port.link_staging(".not-a-digest.tmp", "outside")
    finally:
        os.close(directory)


@LINUX_ONLY
def test_mutation_port_rebinds_root_after_each_effect(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    selected = store._test_authority_v1(root)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    port = authority_owner.bind_store_mutation_port_v1(
        selected, root, root.parent, directory,
    )
    events = []
    original_check = authority_owner._require_bound_directory_v1

    def observe_check(*args):
        events.append("root")
        return original_check(*args)

    def replace_root(_descriptor):
        events.append("effect")
        root.rename(tmp_path / "old-root")
        root.mkdir(mode=0o755)

    monkeypatch.setattr(authority_owner, "_require_bound_directory_v1", observe_check)
    monkeypatch.setattr(authority_owner.os, "fsync", replace_root)
    try:
        with pytest.raises(preflight.PreflightError):
            port.sync_directory()
        assert events == ["root", "effect", "root"]
    finally:
        os.close(directory)


@LINUX_ONLY
def test_wrong_file_owner_and_symlink_root_fail_closed(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path)
    request_id, encoded = _attestation()
    final_name, _ = _names(request_id)
    final = root / final_name
    final.write_bytes(encoded)
    final.chmod(0o644)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    original_fstat = store.os.fstat

    def wrong_owner(descriptor):
        observed = original_fstat(descriptor)
        if stat.S_ISREG(observed.st_mode):
            fields = list(observed)
            fields[4] = observed.st_uid + 1
            return os.stat_result(fields)
        return observed

    monkeypatch.setattr(store.os, "fstat", wrong_owner)
    with pytest.raises(preflight.PreflightError):
        store._read_at_v1(
            directory, final_name, uid=os.getuid(), gid=os.getgid(),
            modes=store._MODE_FINAL_V1, links=store._LINK_ONE_V1,
        )
    os.close(directory)

    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(preflight.PreflightError):
        store._read_preflight_attestation_for_test_v1(request_id, alias)


def test_platform_gate_precedes_filesystem_io(monkeypatch) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(
        authority_owner.os, "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("I/O")),
    )
    with pytest.raises(preflight.PreflightError) as failure:
        # The product entry uses its fixed root and identities; the POSIX-only
        # test helper calls os.getuid() before entering this platform guard.
        store._read_preflight_attestation_v1(D("1"))
    assert failure.value.code == preflight.CODE_PLATFORM
