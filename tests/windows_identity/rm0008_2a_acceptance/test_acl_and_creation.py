"""Independent ACL, privilege, volume and secure-creation acceptance cells."""
from __future__ import annotations

import builtins
import os
import ctypes
import io
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


def _historical_call_through_handles(
    sf,
    monkeypatch,
    call,
    *,
    fixture_root: Path,
    allowed_absolute_roots: tuple[Path, ...],
    required_relative_names: set[str],
):
    """Prove historical facades cross the frozen native-handle boundary."""
    observed_relative: list[str] = []
    native_ntcreate = sf._NTDLL.NtCreateFile
    native_createfile = sf._KERNEL32.CreateFileW

    def scalar(value) -> int:
        return int(getattr(value, "value", value) or 0)

    def normalized(value) -> str:
        text = os.fspath(value).replace("/", "\\")
        if text.casefold().startswith("\\\\?\\unc\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return os.path.normcase(os.path.abspath(os.path.normpath(text)))

    fixture = normalized(fixture_root)
    allowed = {normalized(path) for path in allowed_absolute_roots}

    def checked_createfile(path, *args):
        candidate = normalized(path)
        if (
            candidate == fixture or candidate.startswith(fixture + "\\")
        ) and candidate not in allowed:
            raise AssertionError(
                "historical loader reopened a descendant with absolute CreateFileW"
            )
        return native_createfile(path, *args)

    def observed_ntcreate(*args):
        attributes = ctypes.cast(
            args[2], ctypes.POINTER(sf._OBJECT_ATTRIBUTES)
        ).contents
        if not attributes.RootDirectory or not attributes.ObjectName:
            raise AssertionError("historical descendant NtCreateFile was not relative")
        name = attributes.ObjectName.contents
        observed_relative.append(
            ctypes.wstring_at(name.Buffer, scalar(name.Length) // 2)
        )
        return native_ntcreate(*args)

    def path_io_forbidden(*args, **kwargs):
        raise AssertionError("historical loader reopened authority through Python paths")

    with monkeypatch.context() as guard:
        guard.setattr(sf._KERNEL32, "CreateFileW", checked_createfile)
        guard.setattr(sf._NTDLL, "NtCreateFile", observed_ntcreate)
        guard.setattr(builtins, "open", path_io_forbidden)
        guard.setattr(io, "open", path_io_forbidden)
        for name in ("open", "read_bytes", "stat", "lstat", "iterdir"):
            guard.setattr(Path, name, path_io_forbidden)
        result = call()
    if not required_relative_names <= set(observed_relative):
        raise AssertionError(
            "historical facade did not open its authority components relatively: "
            f"{observed_relative!r}"
        )
    return result


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
    sf = support.product()
    oracle = support.identity_oracle()
    if case.startswith("reject-"):
        sid = support.service_sid()
        root = tmp_path / "birth"
        bindings = support.explicit_role_bindings(
            sf, (("target",), False, "birth_confidential")
        )
        with support.session(root, role_bindings=bindings) as active:
            with support.exclusive(active):
                target = root / "target"
                support.create_profiled(
                    target, "confidential", directory=False, sid=sid, payload=b"secret"
                )
                canonical_facts = support.acl_profile_facts(
                    target, directory=False
                )
                if canonical_facts != {
                    "owner": "S-1-5-18",
                    "protected": True,
                    "ace_types": (0, 0, 0),
                    "ace_flags": (0, 0, 0),
                    "ace_masks": (
                        oracle._FILE_ALL_ACCESS,
                        oracle._FILE_ALL_ACCESS,
                        oracle._FILE_READ_MASK,
                    ),
                    "ace_sids": ("S-1-5-18", "S-1-5-32-544", sid),
                }:
                    raise AssertionError("canonical ACL fixture is not independently valid")
                sddl = _invalid_sddl(case, sid, directory=False)
                if case == "reject-unprotected-dacl":
                    # Apply without the PROTECTED bit through the independent API.
                    support.apply_sddl(target, sddl, directory=False, protected=False)
                else:
                    support.apply_sddl(target, sddl, directory=False)
                invalid_facts = support.acl_profile_facts(target, directory=False)
                expected_changed_field = {
                    "reject-owner": "owner",
                    "reject-unprotected-dacl": "protected",
                    "reject-ace-order": "ace_sids",
                    "reject-ace-type-or-flags": "ace_flags",
                    "reject-ace-sid": "ace_sids",
                    "reject-ace-mask": "ace_masks",
                }[case]
                changed_fields = {
                    name
                    for name, value in canonical_facts.items()
                    if invalid_facts[name] != value
                }
                if changed_fields != {expected_changed_field}:
                    raise AssertionError(
                        "negative ACL fixture did not alter exactly its owned field: "
                        f"expected={expected_changed_field}, observed={sorted(changed_fields)}"
                    )
                support.require_code(
                    lambda: _read_with_role(active, "target", "birth_confidential"),
                    "birth_provisioning_acl_unsafe",
                )
                if case == "reject-ace-type-or-flags":
                    oracle.apply_profile(
                        target,
                        "confidential",
                        sid,
                        directory=False,
                    )
                    if support.acl_profile_facts(
                        target, directory=False
                    ) != canonical_facts:
                        raise AssertionError("ACL fixture did not restore canonically")
                    type_only_sddl = (
                        "O:SYD:P(D;;FA;;;SY)(A;;FA;;;BA)"
                        f"(A;;0x{oracle._FILE_READ_MASK:08x};;;{sid})"
                    )
                    support.apply_sddl(
                        target, type_only_sddl, directory=False
                    )
                    type_facts = support.acl_profile_facts(
                        target, directory=False
                    )
                    changed_fields = {
                        name
                        for name, value in canonical_facts.items()
                        if type_facts[name] != value
                    }
                    if changed_fields != {"ace_types"}:
                        raise AssertionError(
                            "ACE-type fixture did not alter only AceType: "
                            f"{sorted(changed_fields)}"
                        )
                    support.require_code(
                        lambda: _read_with_role(
                            active, "target", "birth_confidential"
                        ),
                        "birth_provisioning_acl_unsafe",
                    )
                if target.read_bytes() != b"secret":
                    raise AssertionError("ACL rejection changed payload")
        return
    if case == "catalog-role-identity-binding":
        sid = support.service_sid()
        root = tmp_path / "birth"
        bindings = support.explicit_role_bindings(
            sf, (("bound.bin",), False, "birth_confidential")
        )
        with support.session(root, role_bindings=bindings) as active:
            with support.exclusive(active):
                identity = support.create_file(
                    active, ("bound.bin",), b"bound", "birth_confidential"
                )
                before = support.identity(root / "bound.bin", directory=False)
                support.assert_profile(
                    root / "bound.bin", "confidential", directory=False, sid=sid
                )
                entry = support.get_named_entry(active._inventory_state(()), "bound.bin")
                independent_identity = support.object_identity(sf, before)
                if (
                    identity != independent_identity
                    or entry.identity != independent_identity
                    or entry.role is not sf._BirthObjectRole("birth_confidential")
                ):
                    raise AssertionError("inventory did not bind role to the observed identity")
                support.require_code(
                    lambda: active.read_file(
                        ("bound.bin",),
                        maximum=16,
                        role=sf._BirthObjectRole("birth_integrity_only"),
                    ),
                    "birth_provisioning_acl_unsafe",
                )
                displaced = tmp_path / "original-bound.bin"
                (root / "bound.bin").rename(displaced)
                support.create_profiled(
                    root / "bound.bin",
                    "confidential",
                    directory=False,
                    sid=sid,
                    payload=b"replacement",
                )
                replacement = support.identity(
                    root / "bound.bin", directory=False
                )
                if (
                    replacement["volume"] == before["volume"]
                    and replacement["file_id"] == before["file_id"]
                ):
                    raise AssertionError("identity-substitution fixture reused the original FileId")
                support.assert_profile(
                    root / "bound.bin",
                    "confidential",
                    directory=False,
                    sid=sid,
                )
                support.require_code(
                    lambda: active.read_file(
                        ("bound.bin",),
                        maximum=16,
                        role=sf._BirthObjectRole("birth_confidential"),
                    ),
                    "birth_provisioning_recovery_ambiguous",
                )
                if (root / "bound.bin").read_bytes() != b"replacement":
                    raise AssertionError("identity rejection changed replacement bytes")
                if displaced.read_bytes() != b"bound":
                    raise AssertionError("identity rejection changed original bytes")
                if support.identity(displaced, directory=False) != before:
                    raise AssertionError("identity rejection changed the displaced original")
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
            bindings = support.explicit_role_bindings(
                sf,
                (("never-log-this-secret.bin",), False, "birth_confidential"),
            )
            with support.session(
                root,
                authenticated_sid=account.sid,
                create_root=False,
                role_bindings=bindings,
            ) as provisioner:
                with support.exclusive(provisioner):
                    pass
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
        bindings = support.explicit_role_bindings(
            sf, (("target",), directory, f"birth_{profile}")
        )
        with support.session(
            root,
            authenticated_sid=service.sid,
            role_bindings=bindings,
        ) as active:
            target = root / "target"
            with support.exclusive(active):
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
    keystore_module = importlib.import_module("executor_birth_keystore")
    approval_module = importlib.import_module("executor_birth_approval_authority")
    semantic_module = importlib.import_module("executor_birth_semantic_authority")
    calls = {
        "keystore": lambda: keystore_module.load_birth_keystore(admission),
        "approval": lambda: approval_module.load_approval_authority(approval),
        "semantic": lambda: semantic_module.load_semantic_authority(
            semantic_value, semantic_root
        ),
    }
    loader_roots = {
        "keystore": (admission,),
        "approval": (approval.parent,),
        "semantic": (semantic_root,),
    }
    loader_components = {
        "keystore": {"keystore.json"},
        "approval": {approval.name},
        "semantic": {"review.pub"},
    }

    def invoke(loader_name: str):
        return _historical_call_through_handles(
            support.product(),
            monkeypatch,
            calls[loader_name],
            fixture_root=root,
            allowed_absolute_roots=loader_roots[loader_name],
            required_relative_names=loader_components[loader_name],
        )
    if case == "historical-inherited-rejected":
        sddl = f"O:{sid}D:(A;ID;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})(A;;0x00120089;;;AU)"
        support.apply_sddl(approval, sddl, directory=False, protected=False)
        approval_error = importlib.import_module(
            "executor_birth_approval"
        ).BirthApprovalError
        try:
            invoke("approval")
        except approval_error as exc:
            if exc.code != "approval_authority_unavailable":
                raise AssertionError(
                    "inherited ACL did not produce the loader's exact stable code"
                ) from exc
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
    before_snapshot = support.windows_tree_snapshot(root)
    selected_loaders = (
        ("keystore", "approval", "semantic")
        if case == "historical-acl-no-mutation"
        else (selected,)
    )
    loaded = {}
    for loader_name in selected_loaders:
        loaded[loader_name] = invoke(loader_name)
        if loaded[loader_name] is None:
            raise AssertionError("historical loader returned no authority")
    fixture_key_id = paths["fixture_key_id"][0]
    if "keystore" in loaded:
        value = loaded["keystore"]
        if (
            value.config_revision != 1
            or value.active_key_id != fixture_key_id
            or set(value.verifier_keys) != {fixture_key_id}
        ):
            raise AssertionError("historical keystore semantic value drifted")
    if "approval" in loaded:
        value = loaded["approval"]
        if (
            value.revision != 1
            or set(value.keys) != {"operator-key"}
            or set(value.actors) != {"operator"}
            or value.actors["operator"]["key_ids"] != frozenset({"operator-key"})
            or value.actors["operator"]["scopes"] != frozenset({"birth"})
        ):
            raise AssertionError("historical approval semantic value drifted")
    if "semantic" in loaded:
        value = loaded["semantic"]
        expected_evidence = root.joinpath(*paths["semantic_evidence"])
        if (
            value.evidence_dir != expected_evidence
            or set(value.verifier_keys) != {"review-key"}
        ):
            raise AssertionError("historical semantic authority value drifted")
    if case == "keystore-no-global" and any(
        path.name == "provisioning-v1.lock" for path in root.rglob("*")
    ):
        raise AssertionError("Path-based keystore loader created a global lock")
    if support.windows_tree_snapshot(root) != before_snapshot:
        raise AssertionError(
            "historical loader mutated namespace, bytes, ACL or metadata"
        )
    for path in sorted(root.rglob("*")):
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
        before_attributes = support.privilege_attributes(
            before, "SeRestorePrivilege"
        )
        if before_attributes & 0x2:
            raise AssertionError("SeRestorePrivilege was already enabled before the scope")
        during = None
        during_attributes = None
        try:
            with manager():
                during = support.token_privileges_snapshot()
                during_attributes = support.privilege_attributes(
                    during, "SeRestorePrivilege"
                )
                raise RuntimeError("privilege-body-sentinel")
        except RuntimeError as exc:
            if str(exc) != "privilege-body-sentinel":
                raise
        else:
            raise AssertionError("privilege body exception was swallowed")
        after = support.token_privileges_snapshot()
        if (
            during is None
            or during_attributes is None
            or not during_attributes & 0x2
            or before != after
        ):
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
    import ctypes

    original = sf._ADVAPI32.AdjustTokenPrivileges
    original_open = sf._ADVAPI32.OpenProcessToken
    original_close = sf._KERNEL32.CloseHandle
    calls = 0
    body_entered = False
    opened: list[int] = []
    closed: list[int] = []

    def record_open(*args):
        result = original_open(*args)
        if result:
            token = ctypes.cast(
                args[2], ctypes.POINTER(sf.wintypes.HANDLE)
            ).contents.value
            opened.append(int(token))
        return result

    def record_close(handle):
        value = int(getattr(handle, "value", handle) or 0)
        if value in opened:
            closed.append(value)
        return original_close(handle)

    def adjusted(*args):
        nonlocal calls
        calls += 1
        if calls == 2 and case in {
            "restore-not-all-assigned",
            "restore-false",
            "body-error-restore",
        }:
            sf._KERNEL32.SetLastError(1300 if case == "restore-not-all-assigned" else 5)
            return 1 if case == "restore-not-all-assigned" else 0
        return original(*args)

    monkeypatch.setattr(sf._ADVAPI32, "AdjustTokenPrivileges", adjusted)
    monkeypatch.setattr(sf._ADVAPI32, "OpenProcessToken", record_open)
    monkeypatch.setattr(sf._KERNEL32, "CloseHandle", record_close)
    if case == "body-error-restore":
        with pytest.raises(BaseExceptionGroup) as caught:
            with manager():
                body_entered = True
                raise RuntimeError("body-sentinel")
        rendered = repr(caught.value)
        if "body-sentinel" not in rendered or "elevation_required" not in rendered:
            raise AssertionError("body and restore failures were not both preserved")
    else:
        def scoped():
            nonlocal body_entered
            with manager():
                body_entered = True

        support.require_code(scoped, "birth_provisioning_elevation_required")
    if calls != 2 or not body_entered or len(opened) != 1 or closed != opened:
        raise AssertionError(
            "restore injection lifecycle drifted: "
            f"calls={calls}, body={body_entered}, opened={opened}, closed={closed}"
        )


@pytest.mark.parametrize("case", G7_CASES, ids=G7_CASES)
def test_g7_volume_gate_precedes_creation(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    import ctypes

    root = tmp_path / "birth"
    directory = "directory" in case
    bindings = support.explicit_role_bindings(
        sf, (("new",), directory, "birth_confidential")
    )
    with support.session(root, role_bindings=bindings) as active:
        with support.exclusive(active):
            before = support.windows_tree_snapshot(root)
            real_volume = support.volume_facts(root)
            if (
                str(real_volume["filesystem"]).casefold() != "ntfs"
                or not int(real_volume["filesystem_flags"]) & 0x00000008
            ):
                raise AssertionError(
                    "G7 fixture is not independently calibrated on supported NTFS"
                )
            original = sf._KERNEL32.GetVolumeInformationByHandleW
            injected_volume = dict(real_volume)
            if "no-persistent" in case:
                injected_volume["filesystem_flags"] = (
                    int(real_volume["filesystem_flags"]) & ~0x00000008
                )
                changed_field = "filesystem_flags"
            else:
                injected_volume["filesystem"] = "FAT32"
                changed_field = "filesystem"
            if {
                name
                for name in ("filesystem", "filesystem_flags")
                if injected_volume[name] != real_volume[name]
            } != {changed_field}:
                raise AssertionError("G7 injection did not alter exactly one volume field")
            volume_calls = 0

            def unsupported(*args):
                nonlocal volume_calls
                volume_calls += 1
                if not original(*args):
                    raise ctypes.WinError(ctypes.get_last_error())
                flags = ctypes.cast(args[5], ctypes.POINTER(sf.wintypes.DWORD))
                flags.contents.value = int(injected_volume["filesystem_flags"])
                filesystem = ctypes.cast(args[6], ctypes.POINTER(sf.wintypes.WCHAR))
                value = str(injected_volume["filesystem"])
                ctypes.memmove(filesystem, ctypes.create_unicode_buffer(value), (len(value) + 1) * ctypes.sizeof(sf.wintypes.WCHAR))
                return 1

            monkeypatch.setattr(sf._KERNEL32, "GetVolumeInformationByHandleW", unsupported)
            call = (
                (
                    lambda: support.create_directory(
                        active, ("new",), "birth_confidential"
                    )
                )
                if directory
                else (
                    lambda: support.create_file(
                        active, ("new",), b"secret", "birth_confidential"
                    )
                )
            )
            try:
                support.require_code(call, "birth_provisioning_atomic_install_unsupported")
            finally:
                monkeypatch.setattr(sf._KERNEL32, "GetVolumeInformationByHandleW", original)
            if volume_calls != 1:
                raise AssertionError(
                    f"G7 volume gate was called {volume_calls} times instead of once"
                )
            if support.windows_tree_snapshot(root) != before:
                raise AssertionError(
                    "unsupported volume changed namespace, bytes, ACL or metadata"
                )


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
            state_path = barrier.with_suffix(".state.json")
            if not state_path.is_file():
                raise AssertionError("creation worker did not publish its handle identity")
            import json

            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("killpoint") != case or not isinstance(state.get("identity"), dict):
                raise AssertionError("creation worker published the wrong killpoint state")
            pre_create_rows = state.get("pre_create_snapshot")
            if not isinstance(pre_create_rows, list):
                raise AssertionError("creation worker omitted its post-lock pre-create state")
            pre_create_snapshot = {
                row[0]: tuple(row)
                for row in pre_create_rows
                if isinstance(row, list) and row and isinstance(row[0], str)
            }
            if (
                len(pre_create_snapshot) != len(pre_create_rows)
                or set(pre_create_snapshot) != {".", "provisioning-v1.lock"}
            ):
                raise AssertionError("creation pre-state inventory is not exact")
            if state.get("setsecurityinfo") != {
                "object_type": 1,
                "security_information": "0x80000005",
                "same_handle": True,
            }:
                raise AssertionError(
                    "creation worker did not prove the exact SetSecurityInfo call"
                )
            crash_snapshot = support.windows_tree_snapshot(root)
            crash_by_name = {row[0]: row for row in crash_snapshot}
            snapshot_names = {row[0] for row in crash_snapshot}
            if snapshot_names != {".", "provisioning-v1.lock", "complete.bin"}:
                raise AssertionError(
                    f"crashed creation left an unexpected full inventory: {sorted(snapshot_names)}"
                )
            if {
                name: crash_by_name[name]
                for name in (".", "provisioning-v1.lock")
            } != pre_create_snapshot:
                raise AssertionError(
                    "creation crash changed preexisting root/lock metadata, ACL or bytes"
                )
            residues = [item.name for item in root.iterdir() if item.name != "provisioning-v1.lock"]
            if residues != ["complete.bin"]:
                raise AssertionError(f"crashed creation left an unexpected inventory: {residues}")
            target = root / "complete.bin"
            sid = support.service_sid()
            support.assert_profile(
                target, "confidential", directory=False, sid=sid
            )
            payload = b"creation-crash-payload"
            expected = {
                "crash-after-acl-before-write": b"",
                "crash-partial-write": payload[: max(1, len(payload) // 2)],
                "crash-complete-write": payload,
                "crash-flush": payload,
            }[case]
            before = support.identity(target, directory=False)
            if target.read_bytes() != expected:
                raise AssertionError("crash residue bytes differ from the selected killpoint")
            after = support.identity(target, directory=False)
            if (
                before != state["identity"]
                or after != state["identity"]
                or before["directory"]
                or before["links"] != 1
                or before["delete_pending"]
                or support.reparse_tag(target, directory=False) != 0
            ):
                raise AssertionError("crash residue identity/type is not stable and singular")
            bindings = support.explicit_role_bindings(
                sf, (("complete.bin",), False, "birth_confidential")
            )
            with support.session(
                root, create_root=False, role_bindings=bindings
            ) as active:
                with active.global_lock(exclusive=True, create=False):
                    support.require_code(
                        lambda: support.create_file(
                            active,
                            ("complete.bin",),
                            payload,
                            "birth_confidential",
                        ),
                        "birth_provisioning_transaction_conflict",
                    )
            if support.windows_tree_snapshot(root) != crash_snapshot:
                raise AssertionError(
                    "closed retry changed crash inventory, identity, ACL, metadata or bytes"
                )
        finally:
            if process.poll() is None:
                support.terminate_process(process)
        return
    root = tmp_path / "birth"
    sync_directory = case == "setsecurityinfo-access-denied-directory"
    sync_name = "created" if case.startswith("setsecurityinfo-") else "complete.bin"
    sync_bindings = support.explicit_role_bindings(
        sf, ((sync_name,), sync_directory, "birth_confidential")
    )
    with support.session(root, role_bindings=sync_bindings) as active:
        with support.exclusive(active):
            before = support.windows_tree_snapshot(root)
            if case.startswith("setsecurityinfo-"):
                result = 5 if "access-denied" in case else 1117
                calls = 0
                creation_handle: int | None = None
                native_create = sf._NTDLL.NtCreateFile
                native_close = sf._KERNEL32.CloseHandle
                native_dispose = sf._KERNEL32.SetFileInformationByHandle
                native_query = sf._KERNEL32.GetFileInformationByHandleEx
                creation_closed = False
                cleanup_calls = 0
                delete_pending_seen = False

                def record_create(*args):
                    nonlocal creation_handle
                    status = native_create(*args)
                    if int(status) >= 0:
                        if creation_handle is not None:
                            raise AssertionError(
                                "failure probe observed more than one NtCreateFile"
                            )
                        creation_handle = int(
                            ctypes.cast(
                                args[0], ctypes.POINTER(ctypes.c_void_p)
                            ).contents.value
                            or 0
                        )
                        if not creation_handle:
                            raise AssertionError("NtCreateFile returned a null handle")
                    return status

                def fail_security(*args):
                    nonlocal calls
                    calls += 1
                    if creation_handle is None:
                        raise AssertionError(
                            "SetSecurityInfo preceded successful NtCreateFile"
                        )
                    support.assert_set_security_info_call(
                        args, expected_handle=creation_handle
                    )
                    return result

                def record_close(handle):
                    nonlocal creation_closed
                    value = int(getattr(handle, "value", handle) or 0)
                    if value == creation_handle:
                        if calls == 0 or cleanup_calls != 1 or not delete_pending_seen:
                            raise AssertionError(
                                "creation handle closed before handle-bound cleanup"
                            )
                        if creation_closed:
                            raise AssertionError("creation handle was closed twice")
                        creation_closed = True
                    return native_close(handle)

                def record_cleanup(*args):
                    nonlocal cleanup_calls
                    if (
                        creation_handle is None
                        or int(getattr(args[0], "value", args[0]) or 0)
                        != creation_handle
                        or int(getattr(args[1], "value", args[1]) or 0) != 21
                    ):
                        raise AssertionError(
                            "SetSecurityInfo cleanup did not use FileDispositionInfoEx "
                            "on the creation handle"
                        )
                    cleanup_calls += 1
                    return native_dispose(*args)

                def record_cleanup_query(*args):
                    nonlocal delete_pending_seen
                    query_result = native_query(*args)
                    if (
                        query_result
                        and creation_handle is not None
                        and int(getattr(args[0], "value", args[0]) or 0)
                        == creation_handle
                        and int(getattr(args[1], "value", args[1]) or 0) == 1
                        and cleanup_calls == 1
                    ):
                        standard = ctypes.cast(
                            args[2], ctypes.POINTER(sf._FILE_STANDARD_INFO)
                        ).contents
                        if not bool(standard.DeletePending):
                            raise AssertionError("cleanup did not set DeletePending")
                        delete_pending_seen = True
                    return query_result

                monkeypatch.setattr(sf._NTDLL, "NtCreateFile", record_create)
                monkeypatch.setattr(sf._ADVAPI32, "SetSecurityInfo", fail_security)
                monkeypatch.setattr(
                    sf._KERNEL32, "SetFileInformationByHandle", record_cleanup
                )
                monkeypatch.setattr(
                    sf._KERNEL32,
                    "GetFileInformationByHandleEx",
                    record_cleanup_query,
                )
                monkeypatch.setattr(sf._KERNEL32, "CloseHandle", record_close)
                support.required(sf._KERNEL32, "DeleteFileW")
                monkeypatch.setattr(
                    sf._KERNEL32,
                    "DeleteFileW",
                    lambda *args: (_ for _ in ()).throw(
                        AssertionError("creation cleanup used DeleteFileW")
                    ),
                )
                directory = case.endswith("directory")
                call = (
                    (
                        lambda: support.create_directory(
                            active, ("created",), "birth_confidential"
                        )
                    )
                    if directory
                    else (
                        lambda: support.create_file(
                            active,
                            ("created",),
                            b"secret",
                            "birth_confidential",
                        )
                    )
                )
                expected = "birth_provisioning_elevation_required" if result == 5 else "birth_provisioning_acl_unsafe"
                support.require_code(call, expected)
                if (
                    creation_handle is None
                    or calls != 1
                    or cleanup_calls != 1
                    or not delete_pending_seen
                    or not creation_closed
                    or support.windows_tree_snapshot(root) != before
                ):
                    raise AssertionError(
                        "SetSecurityInfo failure did not reconcile full inventory and metadata"
                    )
                return
            native_create = sf._NTDLL.NtCreateFile
            native_close = sf._KERNEL32.CloseHandle
            native_dispose = sf._KERNEL32.SetFileInformationByHandle
            native_query = sf._KERNEL32.GetFileInformationByHandleEx
            creation_handle: int | None = None
            target_calls = 0
            cleanup_calls = 0
            delete_pending_seen = False
            creation_close_count = 0

            def record_create(*args):
                nonlocal creation_handle
                status = native_create(*args)
                if int(status) >= 0:
                    if creation_handle is not None:
                        raise AssertionError("failed create opened its target twice")
                    creation_handle = int(
                        ctypes.cast(
                            args[0], ctypes.POINTER(ctypes.c_void_p)
                        ).contents.value
                        or 0
                    )
                return status

            def record_cleanup(*args):
                nonlocal cleanup_calls
                if (
                    creation_handle is None
                    or int(getattr(args[0], "value", args[0]) or 0)
                    != creation_handle
                    or int(getattr(args[1], "value", args[1]) or 0) != 21
                ):
                    raise AssertionError(
                        "write/flush cleanup did not use FileDispositionInfoEx "
                        "on the creation handle"
                    )
                cleanup_calls += 1
                return native_dispose(*args)

            def record_query(*args):
                nonlocal delete_pending_seen
                query_result = native_query(*args)
                if (
                    query_result
                    and creation_handle is not None
                    and int(getattr(args[0], "value", args[0]) or 0)
                    == creation_handle
                    and int(getattr(args[1], "value", args[1]) or 0) == 1
                    and cleanup_calls == 1
                ):
                    standard = ctypes.cast(
                        args[2], ctypes.POINTER(sf._FILE_STANDARD_INFO)
                    ).contents
                    if not bool(standard.DeletePending):
                        raise AssertionError("failed-create cleanup did not set DeletePending")
                    delete_pending_seen = True
                return query_result

            def record_close(handle):
                nonlocal creation_close_count
                if (
                    creation_handle is not None
                    and int(getattr(handle, "value", handle) or 0)
                    == creation_handle
                ):
                    if cleanup_calls != 1 or not delete_pending_seen:
                        raise AssertionError(
                            "failed creation handle closed before cleanup reconciliation"
                        )
                    creation_close_count += 1
                    if creation_close_count > 1:
                        raise AssertionError("failed creation handle closed twice")
                return native_close(handle)

            monkeypatch.setattr(sf._NTDLL, "NtCreateFile", record_create)
            monkeypatch.setattr(
                sf._KERNEL32, "SetFileInformationByHandle", record_cleanup
            )
            monkeypatch.setattr(
                sf._KERNEL32, "GetFileInformationByHandleEx", record_query
            )
            monkeypatch.setattr(sf._KERNEL32, "CloseHandle", record_close)
            support.required(sf._KERNEL32, "DeleteFileW")
            monkeypatch.setattr(
                sf._KERNEL32,
                "DeleteFileW",
                lambda *args: (_ for _ in ()).throw(
                    AssertionError("failed creation cleanup used DeleteFileW")
                ),
            )

            if case == "no-complete-destination":
                def fail_flush(handle):
                    nonlocal target_calls
                    if (
                        creation_handle is None
                        or int(getattr(handle, "value", handle) or 0)
                        != creation_handle
                    ):
                        raise AssertionError("flush injection used the wrong handle")
                    target_calls += 1
                    sf._KERNEL32.SetLastError(1117)
                    return 0

                monkeypatch.setattr(
                    sf._KERNEL32, "FlushFileBuffers", fail_flush
                )
            else:
                original_write = support.required(sf, "_win_write_all")

                def fail_write(handle, payload):
                    nonlocal target_calls
                    if (
                        creation_handle is None
                        or int(getattr(handle, "value", handle) or 0)
                        != creation_handle
                    ):
                        raise AssertionError("write injection used the wrong handle")
                    target_calls += 1
                    original_write(handle, b"partial")
                    raise OSError(1117, "injected write failure")

                monkeypatch.setattr(sf, "_win_write_all", fail_write)
            support.require_code(
                lambda: support.create_file(active, ("complete.bin",), b"complete-payload", "birth_confidential"),
                "birth_provisioning_io_unavailable",
            )
            if (
                creation_handle is None
                or target_calls != 1
                or cleanup_calls != 1
                or not delete_pending_seen
                or creation_close_count != 1
            ):
                raise AssertionError(
                    "targeted write/flush failure or exact handle cleanup was not observed"
                )
            if support.windows_tree_snapshot(root) != before:
                raise AssertionError(
                    "failed creation changed inventory, identity, ACL, metadata or bytes"
                )
