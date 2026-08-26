"""Independent ACL, privilege, volume and secure-creation acceptance cells."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import _windows_support as support


R5_CASES = (
    "reject-owner",
    "reject-unprotected-dacl",
    "reject-ace-order",
    "reject-ace-type-or-flags",
    "reject-ace-sid",
    "reject-ace-mask",
    "birth-confidential-file-access",
    "birth-confidential-directory-access",
    "birth-integrity-file-access",
    "birth-integrity-directory-access",
    "nonelevated-stable-error-no-secret",
    "catalog-role-identity-binding",
)

G2_CASES = (
    "keystore-historical-private",
    "approval-historical-public",
    "semantic-historical-public",
    "keystore-no-global",
    "historical-acl-no-mutation",
    "historical-inherited-rejected",
)

G5_CASES = (
    "restore-false",
    "restore-not-all-assigned",
    "body-error-restore",
    "real-token-roundtrip",
    "token-handle-close-once",
)

G7_CASES = (
    "reject-non-ntfs-file-create",
    "reject-non-ntfs-directory-create",
    "reject-no-persistent-acl-file-create",
    "reject-no-persistent-acl-directory-create",
)

G12_CASES = (
    "setsecurityinfo-access-denied-file",
    "setsecurityinfo-access-denied-directory",
    "setsecurityinfo-injected-dword-error",
    "no-complete-destination",
    "residues-reconciled",
    "crash-after-acl-before-write",
    "crash-partial-write",
    "crash-complete-write",
    "crash-flush",
)


def _read_with_role(active, name: str, role_name: str) -> bytes:
    sf = support.product()
    return active.read_file(
        (name,), maximum=64, role=support.role(sf, role_name)
    )


def _open_with_role(active, name: str, role_name: str):
    sf = support.product()
    return active.open_directory((name,), role=support.role(sf, role_name))


def _invalid_sddl(case: str, sid: str, *, directory: bool) -> str:
    oracle = support.identity_oracle()
    read = oracle._DIRECTORY_READ_MASK if directory else oracle._FILE_READ_MASK
    canonical = [
        "(A;;FA;;;SY)",
        "(A;;FA;;;BA)",
        f"(A;;0x{read:08x};;;{sid})",
    ]
    if case == "reject-owner":
        return "O:BAD:P" + "".join(canonical)
    if case == "reject-unprotected-dacl":
        return "O:SYD:" + "".join(canonical)
    if case == "reject-ace-order":
        return "O:SYD:P" + "".join((canonical[1], canonical[0], canonical[2]))
    if case == "reject-ace-type-or-flags":
        return "O:SYD:P(A;CI;FA;;;SY)" + "".join(canonical[1:])
    if case == "reject-ace-sid":
        return "O:SYD:P" + "".join(canonical[:2]) + f"(A;;0x{read:08x};;;AU)"
    return "O:SYD:P" + "".join(canonical[:2]) + f"(A;;0x{read ^ 1:08x};;;{sid})"


@pytest.mark.parametrize("case", R5_CASES, ids=R5_CASES)
def test_r5_exact_acl_and_real_access(case: str, tmp_path: Path) -> None:
    support.require_windows()
    oracle = support.identity_oracle()
    if case.startswith("reject-"):
        sid = support.service_sid()
        root = tmp_path / "birth"
        with support.session(root) as active:
            target = root / "target"
            support.create_profiled(
                target, "confidential", directory=False, sid=sid, payload=b"secret"
            )
            sddl = _invalid_sddl(case, sid, directory=False)
            if case == "reject-unprotected-dacl":
                # Apply without the PROTECTED bit through the independent API.
                support.apply_sddl(target, sddl, directory=False, protected=False)
            else:
                support.apply_sddl(target, sddl, directory=False)
            support.require_code(
                lambda: _read_with_role(active, "target", "birth_confidential"),
                "birth_provisioning_acl_unsafe",
            )
            if target.read_bytes() != b"secret":
                raise AssertionError("ACL rejection changed payload")
        return
    if case == "catalog-role-identity-binding":
        sf = support.product()
        sid = support.service_sid()
        root = tmp_path / "birth"
        with support.session(root) as active:
            identity = support.create_file(
                active, ("bound.bin",), b"bound", "birth_confidential"
            )
            before = support.identity(root / "bound.bin", directory=False)
            support.assert_profile(
                root / "bound.bin", "confidential", directory=False, sid=sid
            )
            entry = support.get_named_entry(active._inventory_state(()), "bound.bin")
            if entry.identity != identity or entry.role is not sf._BirthObjectRole("birth_confidential"):
                raise AssertionError("inventory did not bind role to the observed identity")
            support.require_code(
                lambda: active.read_file(
                    ("bound.bin",),
                    maximum=16,
                    role=sf._BirthObjectRole("birth_integrity_only"),
                ),
                "birth_provisioning_acl_unsafe",
            )
            if support.identity(root / "bound.bin", directory=False) != before:
                raise AssertionError("role rejection replaced the object")
        return
    if case == "nonelevated-stable-error-no-secret":
        from unittest import mock

        account = oracle.create_standard_account("rm8svc")
        try:
            root = tmp_path / "birth"
            root.mkdir()
            oracle.apply_profile(
                root, "integrity_only", account.sid, directory=True
            )
            worker = support.REPOSITORY / "tests" / "windows_identity" / "rm0008_2a_acceptance" / "_worker.py"
            with mock.patch.dict(
                os.environ, support.worker_environment(), clear=True
            ):
                result = oracle.run_probe_as(
                    account, worker, "product-create", root, directory=True
                )
            if result != 40 or (root / "never-log-this-secret.bin").exists():
                raise AssertionError("non-elevated product call was not stably redacted")
        finally:
            oracle.delete_account(account)
        return
    profile = "confidential" if "confidential" in case else "integrity_only"
    directory = "directory" in case
    service = oracle.create_standard_account("rm8svc")
    outsider = None
    try:
        outsider = oracle.create_standard_account("rm8usr")
        root = tmp_path / "birth"
        with support.session(root, authenticated_sid=service.sid) as active:
            target = root / "target"
            if directory:
                support.create_directory(active, ("target",), f"birth_{profile}")
            else:
                support.create_file(active, ("target",), b"secret", f"birth_{profile}")
            oracle.assert_exact_profile(
                target, profile, service.sid, directory=directory
            )
            probe = support.REPOSITORY / "tests" / "windows_identity" / "win32_identity_oracle.py"
            service_read = oracle.run_probe_as(service, probe, "read", target, directory=directory)
            outsider_read = oracle.run_probe_as(outsider, probe, "read", target, directory=directory)
            expected_outsider = (
                oracle.ACCESS_RESULT_DENIED
                if profile == "confidential"
                else oracle.ACCESS_RESULT_ALLOWED
            )
            if service_read != oracle.ACCESS_RESULT_ALLOWED or outsider_read != expected_outsider:
                raise AssertionError("effective read access differs from the profile")
            for account in (service, outsider):
                for operation in ("write", "delete", "write_dac"):
                    if oracle.run_probe_as(account, probe, operation, target, directory=directory) != oracle.ACCESS_RESULT_DENIED:
                        raise AssertionError(f"{operation} was granted to a standard identity")
    finally:
        if outsider is not None:
            oracle.delete_account(outsider)
        oracle.delete_account(service)


@pytest.mark.parametrize("case", G2_CASES, ids=G2_CASES)
def test_g2_historical_role_boundary(case: str, tmp_path: Path, monkeypatch) -> None:
    support.product()
    import importlib
    import json

    root = tmp_path / "birth"
    sid = support.service_sid()
    root.mkdir()
    support.apply_profile(root, "integrity_only", directory=True, sid=sid)
    paths = support.provision_authorities(root, sid)
    admission = root.joinpath(*paths["keystore"])
    approval = root.joinpath(*paths["approval"])
    semantic_root = root.joinpath(*paths["semantic_authority"][:-1])
    semantic_value = json.loads(root.joinpath(*paths["semantic_authority"]).read_bytes())
    calls = {
        "keystore": lambda: importlib.import_module(
            "executor_birth_keystore"
        ).load_birth_keystore(admission),
        "approval": lambda: importlib.import_module(
            "executor_birth_approval_authority"
        ).load_approval_authority(approval),
        "semantic": lambda: importlib.import_module(
            "executor_birth_semantic_authority"
        ).load_semantic_authority(semantic_value, semantic_root),
    }
    if case == "historical-inherited-rejected":
        sddl = f"O:{sid}D:(A;ID;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})(A;;0x00120089;;;AU)"
        support.apply_sddl(approval, sddl, directory=False, protected=False)
        try:
            calls["approval"]()
        except BaseException as exc:
            if "unsafe" not in str(exc).casefold():
                raise AssertionError("inherited ACL did not produce the loader's stable unsafe error") from exc
        else:
            raise AssertionError("historical loader accepted an inherited ACL")
        return
    selected = (
        "keystore"
        if case.startswith("keystore")
        else "approval"
        if case.startswith("approval")
        else "semantic"
    )
    observed = [
        (path, support.identity(path, directory=path.is_dir()))
        for path in sorted(root.rglob("*"))
    ]
    selected_loaders = (
        ("keystore", "approval", "semantic")
        if case == "historical-acl-no-mutation"
        else (selected,)
    )
    for loader_name in selected_loaders:
        if calls[loader_name]() is None:
            raise AssertionError("historical loader returned no authority")
    if case == "keystore-no-global" and any(
        path.name == "provisioning-v1.lock" for path in root.rglob("*")
    ):
        raise AssertionError("Path-based keystore loader created a global lock")
    for path, before in observed:
        if support.identity(path, directory=path.is_dir()) != before:
            raise AssertionError("historical loader mutated filesystem identity")
        if case == "historical-acl-no-mutation":
            private = bool(
                set(path.relative_to(root).parts) & {"admission", "private"}
            )
            support.assert_historical_profile(
                path, public=not private, directory=path.is_dir(), sid=sid
            )
    if selected == "keystore":
        support.assert_historical_profile(
            admission, public=False, directory=True, sid=sid
        )
    elif selected == "approval":
        support.assert_historical_profile(
            approval, public=True, directory=False, sid=sid
        )
    else:
        support.assert_historical_profile(
            semantic_root, public=True, directory=True, sid=sid
        )


@pytest.mark.parametrize("case", G5_CASES, ids=G5_CASES)
def test_g5_restore_privilege_lifecycle(case: str, monkeypatch) -> None:
    sf = support.product()
    manager = support.required(sf, "_win_restore_privilege")
    if case == "real-token-roundtrip":
        before = support.token_privileges_snapshot()
        during = None
        try:
            with manager():
                during = support.token_privileges_snapshot()
                raise RuntimeError("privilege-body-sentinel")
        except RuntimeError as exc:
            if str(exc) != "privilege-body-sentinel":
                raise
        else:
            raise AssertionError("privilege body exception was swallowed")
        after = support.token_privileges_snapshot()
        if during is None or before != after:
            raise AssertionError("privilege scope did not restore TokenPrivileges exactly")
        return
    if case == "token-handle-close-once":
        import ctypes

        original_open = sf._ADVAPI32.OpenProcessToken
        original_close = sf._KERNEL32.CloseHandle
        opened = []
        closed = []

        def record_open(*args):
            result = original_open(*args)
            if result:
                token = ctypes.cast(
                    args[2], ctypes.POINTER(sf.wintypes.HANDLE)
                ).contents.value
                opened.append(int(token))
            return result

        def record(handle):
            closed.append(int(handle))
            return original_close(handle)

        monkeypatch.setattr(sf._ADVAPI32, "OpenProcessToken", record_open)
        monkeypatch.setattr(sf._KERNEL32, "CloseHandle", record)
        with manager():
            pass
        if len(opened) != 1 or closed != [opened[0]]:
            raise AssertionError(
                f"privilege token lifecycle differs from one exact close: opened={opened}, closed={closed}"
            )
        return
    original = sf._ADVAPI32.AdjustTokenPrivileges
    calls = 0

    def adjusted(*args):
        nonlocal calls
        calls += 1
        if calls > 1 and case in {
            "restore-not-all-assigned",
            "restore-false",
            "body-error-restore",
        }:
            sf._KERNEL32.SetLastError(1300 if case == "restore-not-all-assigned" else 5)
            return 1 if case == "restore-not-all-assigned" else 0
        return original(*args)

    monkeypatch.setattr(sf._ADVAPI32, "AdjustTokenPrivileges", adjusted)
    if case == "body-error-restore":
        with pytest.raises(BaseExceptionGroup) as caught:
            with manager():
                raise RuntimeError("body-sentinel")
        rendered = repr(caught.value)
        if "body-sentinel" not in rendered or "elevation_required" not in rendered:
            raise AssertionError("body and restore failures were not both preserved")
    else:
        def scoped():
            with manager():
                pass

        support.require_code(scoped, "birth_provisioning_elevation_required")


@pytest.mark.parametrize("case", G7_CASES, ids=G7_CASES)
def test_g7_volume_gate_precedes_creation(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    import ctypes

    root = tmp_path / "birth"
    with support.session(root) as active:
        before = tuple(root.iterdir())
        original = sf._KERNEL32.GetVolumeInformationByHandleW

        def unsupported(*args):
            flags = ctypes.cast(args[5], ctypes.POINTER(sf.wintypes.DWORD))
            flags.contents.value = 0 if "no-persistent" in case else sf._FILE_PERSISTENT_ACLS
            filesystem = ctypes.cast(args[6], ctypes.POINTER(sf.wintypes.WCHAR))
            value = "FAT32" if "non-ntfs" in case else "NTFS"
            ctypes.memmove(filesystem, ctypes.create_unicode_buffer(value), (len(value) + 1) * ctypes.sizeof(sf.wintypes.WCHAR))
            return 1

        monkeypatch.setattr(sf._KERNEL32, "GetVolumeInformationByHandleW", unsupported)
        directory = "directory" in case
        call = (
            lambda: support.create_directory(active, ("new",), "birth_confidential")
            if directory
            else lambda: support.create_file(active, ("new",), b"secret", "birth_confidential")
        )
        try:
            support.require_code(call, "birth_provisioning_atomic_install_unsupported")
        finally:
            monkeypatch.setattr(sf._KERNEL32, "GetVolumeInformationByHandleW", original)
        if tuple(root.iterdir()) != before:
            raise AssertionError("unsupported volume changed parent inventory")


@pytest.mark.parametrize("case", G12_CASES, ids=G12_CASES)
def test_g12_secure_creation_failure_states(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    if case.startswith("crash-"):
        support.require_windows()
        import subprocess
        import time

        root = tmp_path / "birth"
        barrier = tmp_path / "create.barrier"
        worker = support.REPOSITORY / "tests" / "windows_identity" / "rm0008_2a_acceptance" / "_worker.py"
        process = subprocess.Popen(
            [support.sys.executable, str(worker), "create", case, str(root), str(barrier)],
            close_fds=True,
            env=support.worker_environment(),
        )
        try:
            deadline = time.monotonic() + 30
            while not barrier.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            if not barrier.exists():
                raise AssertionError(f"child did not reach creation barrier, exit={process.poll()}")
            support.terminate_process(process)
            if (root / "complete.bin").exists():
                raise AssertionError("crashed creation exposed a complete destination")
            residues = [item.name for item in root.iterdir() if item.name != "provisioning-v1.lock"]
            if residues:
                raise AssertionError(f"crashed creation left residues: {residues}")
        finally:
            if process.poll() is None:
                support.terminate_process(process)
        return
    root = tmp_path / "birth"
    with support.session(root) as active:
        set_security = sf._ADVAPI32.SetSecurityInfo
        if case.startswith("setsecurityinfo-"):
            result = 5 if "access-denied" in case else 1117
            calls = 0

            def fail_security(*args):
                nonlocal calls
                calls += 1
                return result

            monkeypatch.setattr(sf._ADVAPI32, "SetSecurityInfo", fail_security)
            directory = case.endswith("directory")
            call = (
                lambda: support.create_directory(active, ("created",), "birth_confidential")
                if directory
                else lambda: support.create_file(active, ("created",), b"secret", "birth_confidential")
            )
            expected = "birth_provisioning_elevation_required" if result == 5 else "birth_provisioning_acl_unsafe"
            support.require_code(call, expected)
            if calls != 1 or (root / "created").exists():
                raise AssertionError("SetSecurityInfo failure was not checked and reconciled")
            return
        original_write = support.required(sf, "_win_write_all")

        def fail_write(*args):
            if case == "residues-reconciled":
                original_write(args[0], b"partial")
            raise OSError(1117, "injected write failure")

        monkeypatch.setattr(sf, "_win_write_all", fail_write)
        support.require_code(
            lambda: support.create_file(active, ("complete.bin",), b"complete-payload", "birth_confidential"),
            "birth_provisioning_io_unavailable",
        )
        if (root / "complete.bin").exists():
            raise AssertionError("failed creation exposed a complete destination or residue")
