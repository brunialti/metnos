"""Windows disposition and no-replace rename acceptance cells."""
from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

import _windows_support as support


R3_WINDOWS = (
    "disposition-relative-open",
    "disposition-file-access-mask",
    "disposition-directory-access-mask",
    "disposition-ex-invalid-parameter-no-fallback",
    "disposition-ex-not-supported-no-fallback",
    "disposition-deletepending-false",
    "disposition-readonly-rejected",
    "disposition-access-denied-mapping",
    "disposition-residual-error-mapping",
)

R3_CRASH = ("dispose-crash-before-native", "dispose-crash-after-native")

R6_PORTABLE_WINDOWS = (
    "cached-source-renames",
    "fresh-source-profile-rejected",
    "destination-existing-conflict",
    "native-error-destination-absent",
    "success-postvalidation",
    "different-volume-rejected",
    "source-fileid128-preserved",
)

R6_CONCURRENCY_WINDOWS = (
    "destination-race-conflict",
    "rename-crash-before-native",
    "rename-crash-after-native",
)


def _disposition_symbols():
    sf = support.product()
    method = support.required(sf._SecureRootSession, "dispose_transaction_object")
    expectation = support.required(sf, "_DisposalExpectation")
    support.required(sf, "_DispositionResult")
    support.required(sf, "_ObjectKind")
    support.required(sf, "_DisposalClass")
    support.required(sf, "_BirthObjectRole")
    return sf, method, expectation


def _bound_call(function, args, kwargs):
    return inspect.signature(function).bind_partial(*args, **kwargs).arguments


def _assert_disposition_open(call, sf, active, *, directory: bool) -> None:
    del active, directory
    if call.get("purpose") != sf._NtOpenPurposeV1.disposition:
        raise AssertionError("relative open did not use the disposition error domain")
    if "victim" not in call.values():
        raise AssertionError("victim was not passed as one relative component")


def _nt_scalar(value) -> int:
    return int(getattr(value, "value", value) or 0)


def _assert_native_disposition_open(calls, sf, active, *, directory: bool) -> None:
    import ctypes

    # Independent Win32 literals: DELETE | SYNCHRONIZE | READ_CONTROL |
    # FILE_READ_ATTRIBUTES, plus LIST_DIRECTORY/TRAVERSE for directories or
    # READ_DATA for regular files.
    expected_access = 0x001300A1 if directory else 0x00130081
    candidates = [call for call in calls if call["desired_access"] == expected_access]
    if len(candidates) != 1:
        raise AssertionError("expected exactly one native disposition open")
    call = candidates[0]
    expected_options = 0x00200000 | 0x00000020
    expected_options |= 0x00000001 if directory else 0x00000040
    expected = {
        "output_handle": True,
        "object_length": ctypes.sizeof(sf._OBJECT_ATTRIBUTES),
        "root_handle": _nt_scalar(active._root_handle),
        "name": "victim",
        "name_length": len("victim".encode("utf-16-le")),
        "name_maximum": len("victim".encode("utf-16-le")),
        "attributes": 0x00000040,
        "security_descriptor": False,
        "security_qos": False,
        "iosb": True,
        "allocation_size": False,
        "share_access": 0x00000001 | 0x00000002,
        "create_disposition": 0x00000001,
        "create_options": expected_options,
        "ea_buffer": False,
        "ea_length": 0,
    }
    for name, value in expected.items():
        if call[name] != value:
            raise AssertionError(
                f"wrong native disposition-open field {name}: {call[name]!r}"
            )
    if call["share_access"] & 0x00000004:
        raise AssertionError("native disposition open granted FILE_SHARE_DELETE")
    if call["create_options"] & 0x00001000:
        raise AssertionError("native disposition open used FILE_DELETE_ON_CLOSE")


def _assert_native_disposition_call(sf, args) -> None:
    import ctypes

    if len(args) != 4 or _nt_scalar(args[0]) == 0:
        raise AssertionError("SetFileInformationByHandle disposition ABI is incomplete")
    if _nt_scalar(args[1]) != 21:
        raise AssertionError("disposition did not use FileDispositionInfoEx")
    if not args[2] or _nt_scalar(args[3]) != ctypes.sizeof(
        sf._FILE_DISPOSITION_INFO_EX
    ):
        raise AssertionError("disposition buffer pointer/size is incorrect")
    information = ctypes.cast(
        args[2], ctypes.POINTER(sf._FILE_DISPOSITION_INFO_EX)
    ).contents
    if _nt_scalar(information.Flags) != (0x00000001 | 0x00000002):
        raise AssertionError("disposition flags are not exactly DELETE|POSIX_SEMANTICS")


def _worker_state(barrier: Path) -> dict[str, object]:
    import json

    path = barrier.with_suffix(".state.json")
    if not path.is_file():
        raise AssertionError("crash worker did not publish its independent state")
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_by_name(root: Path) -> dict[str, tuple[object, ...]]:
    return {row[0]: row for row in support.windows_tree_snapshot(root)}


def _published_snapshot(state: dict[str, object]) -> dict[str, tuple[object, ...]]:
    rows = state.get("snapshot")
    if not isinstance(rows, list):
        raise AssertionError("crash worker did not publish its pre-native snapshot")
    result = {}
    for row in rows:
        if not isinstance(row, list) or not row or not isinstance(row[0], str):
            raise AssertionError("crash worker snapshot has an invalid row")
        result[row[0]] = tuple(row)
    if len(result) != len(rows):
        raise AssertionError("crash worker snapshot contains duplicate names")
    return result


def _assert_crash_file(path: Path, state, payload: bytes) -> None:
    facts = support.identity(path, directory=False)
    if facts != state["identity"]:
        raise AssertionError("crash object differs from the worker's FileId metadata")
    if path.read_bytes() != payload or support.digest(payload) != state["payload_sha256"]:
        raise AssertionError("crash object payload differs from the worker state")
    if facts["directory"] or facts["links"] != 1 or facts["delete_pending"]:
        raise AssertionError("crash object type/link/delete metadata is unsafe")
    if support.reparse_tag(path, directory=False):
        raise AssertionError("crash object became a reparse point")
    support.assert_profile(
        path,
        "confidential",
        directory=False,
        sid=support.service_sid(),
    )


def _prepare_disposition(active, root: Path, sf, *, directory: bool):
    name = "victim"
    path = root / name
    if directory:
        support.create_directory(active, (name,), "birth_confidential")
        expectation = support.disposal_expectation(
            sf,
            path,
            (name,),
            kind="directory",
            role_name="birth_confidential",
            disposal_class="empty_directory",
            inventory=(),
        )
    else:
        payload = b"disposition-payload"
        support.create_file(active, (name,), payload, "birth_confidential")
        expectation = support.disposal_expectation(
            sf,
            path,
            (name,),
            kind="regular_file",
            role_name="birth_confidential",
            disposal_class="complete_file",
            payload=payload,
        )
    return path, expectation


@pytest.mark.parametrize("case", R3_WINDOWS, ids=R3_WINDOWS)
def test_r3_windows_disposition_contract(case: str, tmp_path: Path, monkeypatch) -> None:
    sf, method, expectation_type = _disposition_symbols()
    if len(expectation_type.__dataclass_fields__) != 10:
        raise AssertionError("unexpected disposition expectation schema")
    directory = case == "disposition-directory-access-mask"
    root = tmp_path / "birth"
    binding_specs = [(('victim',), directory, "birth_confidential")]
    if case == "disposition-readonly-rejected":
        binding_specs.append((("acl-victim",), False, "birth_confidential"))
    bindings = support.explicit_role_bindings(sf, *binding_specs)
    with support.session(root, role_bindings=bindings) as active:
        with support.exclusive(active):
            path, expectation = _prepare_disposition(active, root, sf, directory=directory)
            relative = support.required(sf, "_win_open_relative_v1")
            relative_calls = []

            def record_relative(*args, **kwargs):
                relative_calls.append(_bound_call(relative, args, kwargs))
                return relative(*args, **kwargs)

            native_ntcreate = sf._NTDLL.NtCreateFile
            ntcreate_calls = []

            def record_ntcreate(*args):
                import ctypes

                attributes = ctypes.cast(
                    args[2], ctypes.POINTER(sf._OBJECT_ATTRIBUTES)
                ).contents
                if not attributes.ObjectName:
                    raise AssertionError("disposition NtCreateFile had no ObjectName")
                object_name = attributes.ObjectName.contents
                call = {
                        "output_handle": bool(args[0]),
                        "desired_access": _nt_scalar(args[1]),
                        "object_length": _nt_scalar(attributes.Length),
                        "root_handle": _nt_scalar(attributes.RootDirectory),
                        "name": ctypes.wstring_at(
                            object_name.Buffer, _nt_scalar(object_name.Length) // 2
                        ),
                        "name_length": _nt_scalar(object_name.Length),
                        "name_maximum": _nt_scalar(object_name.MaximumLength),
                        "attributes": _nt_scalar(attributes.Attributes),
                        "security_descriptor": bool(attributes.SecurityDescriptor),
                        "security_qos": bool(attributes.SecurityQualityOfService),
                        "iosb": bool(args[3]),
                        "allocation_size": bool(args[4]),
                        "share_access": _nt_scalar(args[6]),
                        "create_disposition": _nt_scalar(args[7]),
                        "create_options": _nt_scalar(args[8]),
                        "ea_buffer": bool(args[9]),
                        "ea_length": _nt_scalar(args[10]),
                    }
                ntcreate_calls.append(call)
                result = native_ntcreate(*args)
                if int(result) >= 0:
                    call["returned_handle"] = int(
                        ctypes.cast(
                            args[0], ctypes.POINTER(ctypes.c_void_p)
                        ).contents.value
                        or 0
                    )
                    if not call["returned_handle"]:
                        raise AssertionError(
                            "successful disposition NtCreateFile returned no handle"
                        )
                return result

            success_cases = {
                "disposition-relative-open",
                "disposition-file-access-mask",
                "disposition-directory-access-mask",
            }
            disposition_state = {
                "native": False,
                "delete_pending": False,
                "close_count": 0,
            }
            post_parent_inventories = []
            expected_parent_inventory = None
            if case in success_cases:
                import ctypes

                parent_before = active._inventory_state(())
                expected_parent_inventory = tuple(
                    item for item in parent_before if item.name != "victim"
                )
                native_set = sf._KERNEL32.SetFileInformationByHandle
                native_query = sf._KERNEL32.GetFileInformationByHandleEx
                native_close = sf._KERNEL32.CloseHandle
                native_inventory = sf._win_inventory

                def disposition_handle() -> int:
                    candidates = [
                        call
                        for call in ntcreate_calls
                        if call.get("name") == "victim"
                        and call.get("returned_handle")
                    ]
                    if len(candidates) != 1:
                        raise AssertionError(
                            "disposition did not create exactly one handle-bound victim open"
                        )
                    return int(candidates[0]["returned_handle"])

                def checked_disposition(*args):
                    _assert_native_disposition_call(sf, args)
                    if _nt_scalar(args[0]) != disposition_handle():
                        raise AssertionError(
                            "disposition did not use the NtCreateFile validation handle"
                        )
                    result = native_set(*args)
                    if result:
                        disposition_state["native"] = True
                    return result

                def checked_query(*args):
                    result = native_query(*args)
                    if (
                        result
                        and disposition_state["native"]
                        and _nt_scalar(args[0]) == disposition_handle()
                        and _nt_scalar(args[1]) == 1  # FileStandardInfo
                    ):
                        standard = ctypes.cast(
                            args[2], ctypes.POINTER(sf._FILE_STANDARD_INFO)
                        ).contents
                        if not bool(standard.DeletePending):
                            raise AssertionError(
                                "successful disposition did not observe DeletePending"
                            )
                        disposition_state["delete_pending"] = True
                    return result

                def checked_close(handle):
                    value = _nt_scalar(handle)
                    candidates = [
                        int(call["returned_handle"])
                        for call in ntcreate_calls
                        if call.get("name") == "victim"
                        and call.get("returned_handle")
                    ]
                    if value in candidates:
                        if value != disposition_handle():
                            raise AssertionError("disposition closed a second victim handle")
                        if not (
                            disposition_state["native"]
                            and disposition_state["delete_pending"]
                        ):
                            raise AssertionError(
                                "disposition handle closed before native reconciliation"
                            )
                        disposition_state["close_count"] += 1
                    return native_close(handle)

                def checked_inventory(handle, *args, **kwargs):
                    result = native_inventory(handle, *args, **kwargs)
                    if (
                        disposition_state["close_count"] == 1
                        and _nt_scalar(handle) == _nt_scalar(active._root_handle)
                    ):
                        post_parent_inventories.append(result)
                    return result

                monkeypatch.setattr(
                    sf._KERNEL32,
                    "SetFileInformationByHandle",
                    checked_disposition,
                )
                monkeypatch.setattr(
                    sf._KERNEL32,
                    "GetFileInformationByHandleEx",
                    checked_query,
                )
                monkeypatch.setattr(sf._KERNEL32, "CloseHandle", checked_close)
                monkeypatch.setattr(sf, "_win_inventory", checked_inventory)
                support.required(sf._KERNEL32, "DeleteFileW")
                monkeypatch.setattr(
                    sf._KERNEL32,
                    "DeleteFileW",
                    lambda *args: (_ for _ in ()).throw(
                        AssertionError("disposition used a path-based delete fallback")
                    ),
                )

            if case == "disposition-relative-open":
                monkeypatch.setattr(sf, "_win_open_relative_v1", record_relative)
                monkeypatch.setattr(sf._NTDLL, "NtCreateFile", record_ntcreate)
                create_file_w = sf._KERNEL32.CreateFileW

                def reject_absolute(*args):
                    raise AssertionError("disposition reopened a descendant by absolute path")

                monkeypatch.setattr(sf._KERNEL32, "CreateFileW", reject_absolute)
                try:
                    result = active.dispose_transaction_object(expectation)
                finally:
                    monkeypatch.setattr(sf._KERNEL32, "CreateFileW", create_file_w)
                disposition_calls = [
                    call for call in relative_calls
                    if call.get("purpose") == sf._NtOpenPurposeV1.disposition
                ]
                if len(disposition_calls) != 1:
                    raise AssertionError("expected exactly one disposition relative open")
                _assert_disposition_open(
                    disposition_calls[0], sf, active, directory=directory
                )
                _assert_native_disposition_open(
                    ntcreate_calls, sf, active, directory=directory
                )
                if disposition_state != {
                    "native": True,
                    "delete_pending": True,
                    "close_count": 1,
                }:
                    raise AssertionError(
                        f"disposition handle lifecycle is incomplete: {disposition_state!r}"
                    )
                if post_parent_inventories != [
                    expected_parent_inventory,
                    expected_parent_inventory,
                ]:
                    raise AssertionError(
                        "disposition did not reconcile the parent inventory after close"
                    )
                if not result.removed or path.exists():
                    raise AssertionError("relative disposition did not remove the object")
                return
            if case in {"disposition-file-access-mask", "disposition-directory-access-mask"}:
                monkeypatch.setattr(sf, "_win_open_relative_v1", record_relative)
                monkeypatch.setattr(sf._NTDLL, "NtCreateFile", record_ntcreate)
                active.dispose_transaction_object(expectation)
                disposition_calls = [
                    call for call in relative_calls
                    if call.get("purpose") == sf._NtOpenPurposeV1.disposition
                ]
                if len(disposition_calls) != 1:
                    raise AssertionError("expected exactly one disposition relative open")
                _assert_disposition_open(
                    disposition_calls[0], sf, active, directory=directory
                )
                _assert_native_disposition_open(
                    ntcreate_calls, sf, active, directory=directory
                )
                if disposition_state != {
                    "native": True,
                    "delete_pending": True,
                    "close_count": 1,
                }:
                    raise AssertionError(
                        f"disposition handle lifecycle is incomplete: {disposition_state!r}"
                    )
                if post_parent_inventories != [
                    expected_parent_inventory,
                    expected_parent_inventory,
                ]:
                    raise AssertionError(
                        "disposition did not reconcile the parent inventory after close"
                    )
                return
            native = sf._KERNEL32.SetFileInformationByHandle
            native_calls = 0

            def native_result(*args):
                nonlocal native_calls
                _assert_native_disposition_call(sf, args)
                candidates = [
                    int(call["returned_handle"])
                    for call in ntcreate_calls
                    if call.get("name") == "victim"
                    and call.get("returned_handle")
                ]
                if len(candidates) != 1 or _nt_scalar(args[0]) != candidates[0]:
                    raise AssertionError(
                        "failed disposition did not use its relative validation handle"
                    )
                native_calls += 1
                if case == "disposition-deletepending-false":
                    return 1
                errors = {
                    "disposition-ex-invalid-parameter-no-fallback": 87,
                    "disposition-ex-not-supported-no-fallback": 50,
                    "disposition-access-denied-mapping": 5,
                    "disposition-residual-error-mapping": 1117,
                }
                sf._KERNEL32.SetLastError(errors[case])
                return 0

            if case == "disposition-readonly-rejected":
                acl_path = root / "acl-victim"
                acl_payload = b"acl-disposition-payload"
                support.create_file(
                    active,
                    ("acl-victim",),
                    acl_payload,
                    "birth_confidential",
                )
                acl_expectation = support.disposal_expectation(
                    sf,
                    acl_path,
                    ("acl-victim",),
                    kind="regular_file",
                    role_name="birth_confidential",
                    disposal_class="complete_file",
                    payload=acl_payload,
                )
                acl_before = support.identity(acl_path, directory=False)
                oracle = support.identity_oracle()
                sid = support.service_sid()
                oracle.apply_profile(
                    acl_path,
                    "confidential",
                    sid,
                    directory=False,
                    service_mask=oracle._FILE_READ_MASK ^ 0x00000001,
                )
                try:
                    oracle.assert_exact_profile(
                        acl_path, "confidential", sid, directory=False
                    )
                except AssertionError:
                    pass
                else:
                    raise AssertionError("disposition ACL corruption did not take effect")
                acl_corrupt_snapshot = support.windows_tree_snapshot(root)
                monkeypatch.setattr(
                    sf._KERNEL32, "SetFileInformationByHandle", native_result
                )
                support.require_code(
                    lambda: active.dispose_transaction_object(acl_expectation),
                    "birth_provisioning_acl_unsafe",
                )
                if native_calls:
                    raise AssertionError("unsafe ACL reached native disposition")
                if (
                    support.identity(acl_path, directory=False) != acl_before
                    or acl_path.read_bytes() != acl_payload
                    or support.windows_tree_snapshot(root) != acl_corrupt_snapshot
                ):
                    raise AssertionError("ACL rejection changed disposition inventory or object")
                if not sf._KERNEL32.SetFileAttributesW(str(path), 0x1):
                    raise sf.ctypes.WinError(sf.ctypes.get_last_error())
                readonly_snapshot = support.windows_tree_snapshot(root)
                support.require_code(
                    lambda: active.dispose_transaction_object(expectation),
                    "birth_provisioning_recovery_ambiguous",
                )
                if native_calls:
                    raise AssertionError("readonly object reached native disposition")
                if support.windows_tree_snapshot(root) != readonly_snapshot:
                    raise AssertionError("readonly rejection mutated the object or inventory")
                return
            native_close = sf._KERNEL32.CloseHandle
            native_query = sf._KERNEL32.GetFileInformationByHandleEx
            native_create_path = sf._KERNEL32.CreateFileW
            close_count = 0
            standard_queries = 0
            lifecycle_active = False

            def checked_failure_close(handle):
                nonlocal close_count
                candidates = [
                    int(call["returned_handle"])
                    for call in ntcreate_calls
                    if call.get("name") == "victim"
                    and call.get("returned_handle")
                ]
                if (
                    lifecycle_active
                    and len(candidates) == 1
                    and _nt_scalar(handle) == candidates[0]
                ):
                    close_count += 1
                    if close_count > 1:
                        raise AssertionError("failed disposition closed its handle twice")
                return native_close(handle)

            def checked_failure_query(*args):
                nonlocal standard_queries
                result = native_query(*args)
                candidates = [
                    int(call["returned_handle"])
                    for call in ntcreate_calls
                    if call.get("name") == "victim"
                    and call.get("returned_handle")
                ]
                if (
                    lifecycle_active
                    and len(candidates) == 1
                    and _nt_scalar(args[0]) == candidates[0]
                    and _nt_scalar(args[1]) == 1
                ):
                    standard_queries += 1
                return result

            def reject_absolute_reopen(*args):
                if lifecycle_active:
                    raise AssertionError(
                        "failed disposition attempted an absolute path reopen"
                    )
                return native_create_path(*args)

            monkeypatch.setattr(sf, "_win_open_relative_v1", record_relative)
            monkeypatch.setattr(sf._NTDLL, "NtCreateFile", record_ntcreate)
            monkeypatch.setattr(sf._KERNEL32, "SetFileInformationByHandle", native_result)
            monkeypatch.setattr(sf._KERNEL32, "CloseHandle", checked_failure_close)
            monkeypatch.setattr(
                sf._KERNEL32,
                "GetFileInformationByHandleEx",
                checked_failure_query,
            )
            monkeypatch.setattr(sf._KERNEL32, "CreateFileW", reject_absolute_reopen)
            support.required(sf._KERNEL32, "DeleteFileW")
            monkeypatch.setattr(
                sf._KERNEL32,
                "DeleteFileW",
                lambda *args: (_ for _ in ()).throw(
                    AssertionError("failed disposition attempted path-based deletion")
                ),
            )
            codes = {
                "disposition-ex-invalid-parameter-no-fallback": "birth_provisioning_atomic_install_unsupported",
                "disposition-ex-not-supported-no-fallback": "birth_provisioning_atomic_install_unsupported",
                "disposition-deletepending-false": "birth_provisioning_io_unavailable",
                "disposition-access-denied-mapping": "birth_provisioning_elevation_required",
                "disposition-residual-error-mapping": "birth_provisioning_io_unavailable",
            }
            failure_snapshot = support.windows_tree_snapshot(root)
            lifecycle_active = True
            try:
                support.require_code(
                    lambda: active.dispose_transaction_object(expectation), codes[case]
                )
            finally:
                lifecycle_active = False
            disposition_calls = [
                call
                for call in relative_calls
                if call.get("purpose") == sf._NtOpenPurposeV1.disposition
            ]
            if len(disposition_calls) != 1:
                raise AssertionError("failed disposition did not use one relative open")
            _assert_disposition_open(
                disposition_calls[0], sf, active, directory=False
            )
            _assert_native_disposition_open(
                ntcreate_calls, sf, active, directory=False
            )
            if (
                native_calls != 1
                or close_count != 1
                or standard_queries
                != (1 if case == "disposition-deletepending-false" else 0)
                or not path.exists()
                or support.windows_tree_snapshot(root) != failure_snapshot
            ):
                raise AssertionError("failed disposition did not preserve one native attempt and object")


@pytest.mark.parametrize("case", R3_CRASH, ids=R3_CRASH)
def test_r3_windows_disposition_crash_matrix(case: str, tmp_path: Path) -> None:
    support.require_windows()
    sf = support.product()
    worker = support.REPOSITORY / "tests" / "windows_identity" / "rm0008_2a_acceptance" / "_worker.py"
    if not worker.is_file():
        raise AssertionError("crash worker is absent")
    import subprocess
    import time

    root = tmp_path / "birth"
    barrier = tmp_path / "dispose.barrier"
    process = subprocess.Popen(
        [support.sys.executable, str(worker), "dispose", case, str(root), str(barrier)],
        close_fds=True,
        env=support.worker_environment(),
    )
    try:
        deadline = time.monotonic() + 30
        while not barrier.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not barrier.exists():
            raise AssertionError(f"child did not reach crash barrier, exit={process.poll()}")
        support.terminate_process(process)
        state = _worker_state(barrier)
        target = root / "victim"
        pre_native = _published_snapshot(state)
        if set(pre_native) != {".", "provisioning-v1.lock", "victim"}:
            raise AssertionError("disposition worker published an unexpected inventory")
        before_retry = _snapshot_by_name(root)
        expected_names = {".", "provisioning-v1.lock"}
        if case.endswith("before-native"):
            expected_names.add("victim")
        if set(before_retry) != expected_names:
            raise AssertionError(
                f"disposition crash left an unexpected inventory: {sorted(before_retry)}"
            )
        expected_crash = dict(pre_native)
        if case.endswith("after-native"):
            expected_crash.pop("victim")
        if before_retry != expected_crash:
            raise AssertionError(
                "disposition crash changed identity, ACL, metadata, bytes or unrelated inventory"
            )
        bindings = support.explicit_role_bindings(
            sf, (("victim",), False, "birth_confidential")
        )
        payload = b"crash-disposition"
        with support.session(
            root, create_root=False, role_bindings=bindings
        ) as active:
            with support.exclusive(active):
                if case.endswith("before-native"):
                    _assert_crash_file(target, state, payload)
                    expectation = support.disposal_expectation(
                        sf,
                        target,
                        ("victim",),
                        kind="regular_file",
                        role_name="birth_confidential",
                        disposal_class="complete_file",
                        payload=payload,
                    )
                    result = active.dispose_transaction_object(expectation)
                    if not result.removed:
                        raise AssertionError(
                            "pre-native complete disposition retry did not succeed"
                        )
                else:
                    if target.exists():
                        raise AssertionError(
                            "post-native crash did not leave the atomic absent state"
                        )
                    facts = state["identity"]
                    expectation = sf._DisposalExpectation(
                        components=("victim",),
                        identity=support.object_identity(sf, facts),
                        kind=sf._ObjectKind("regular_file"),
                        role=sf._BirthObjectRole("birth_confidential"),
                        disposal_class=sf._DisposalClass("complete_file"),
                        links=facts["links"],
                        expected_size=len(payload),
                        maximum_partial_size=None,
                        content_sha256=support.digest(payload),
                        inventory=None,
                    )
                    support.require_code(
                        lambda: active.dispose_transaction_object(expectation),
                        "birth_provisioning_recovery_ambiguous",
                    )
        after_retry = _snapshot_by_name(root)
        expected_after = dict(before_retry)
        if case.endswith("before-native"):
            expected_after.pop("victim")
        if after_retry != expected_after:
            raise AssertionError("disposition retry changed unrelated inventory or metadata")
    finally:
        if process.poll() is None:
            support.terminate_process(process)


def _rename_fixture(active, root: Path, *, directory: bool = False):
    source = root / "source"
    if directory:
        support.create_directory(active, ("source",), "birth_confidential")
    else:
        support.create_file(active, ("source",), b"source-payload", "birth_confidential")
    return source, support.identity(source, directory=directory)


def _successful_rename(active, root: Path, *, directory: bool = False):
    source, before = _rename_fixture(active, root, directory=directory)
    returned = active.rename_no_replace(("source",), ("destination",), directory=directory)
    destination = root / "destination"
    if source.exists() or not destination.exists():
        raise AssertionError("rename did not produce the only allowed final namespace")
    after = support.identity(destination, directory=directory)
    if after["volume"] != before["volume"] or after["file_id"] != before["file_id"]:
        raise AssertionError("rename changed the independent 128-bit identity")
    return before, after, returned


def _assert_native_rename_call(
    arguments,
    sf,
    active,
    *,
    expected_parent_handle=None,
    expected_name: str = "destination",
    expected_source_handle=None,
) -> dict[str, object]:
    import ctypes

    # Section 17.26: the move goes through the native call, which alone
    # honours the containing directory of the request.  Its shape is handle,
    # status block, buffer, length, class.
    if len(arguments) != 5:
        raise AssertionError("rename did not use the five-argument native ABI")
    arguments = (arguments[0], arguments[4], arguments[2], arguments[3])

    def scalar(value) -> int:
        return int(getattr(value, "value", value) or 0)

    source_handle = scalar(arguments[0])
    if not source_handle or source_handle == scalar(active._root_handle):
        raise AssertionError("rename did not use the opened source handle")
    if (
        expected_source_handle is not None
        and source_handle != scalar(expected_source_handle)
    ):
        raise AssertionError(
            "rename did not use the exact validated source-handle generation"
        )
    if scalar(arguments[1]) != 10:  # FileRenameInformation
        raise AssertionError("rename used the wrong file-information class")
    if not arguments[2]:
        raise AssertionError("rename information buffer is absent")
    information = ctypes.cast(
        arguments[2], ctypes.POINTER(sf._FILE_RENAME_INFO)
    ).contents
    encoded = expected_name.encode("utf-16-le")
    expected_size = sf._FILE_RENAME_INFO.FileName.offset + len(encoded)
    if scalar(arguments[3]) != expected_size:
        raise AssertionError("rename information buffer has the wrong exact size")
    if scalar(information.ReplaceIfExists) != 0:
        raise AssertionError("rename enabled destination replacement")
    parent_handle = (
        active._root_handle
        if expected_parent_handle is None
        else expected_parent_handle
    )
    if scalar(information.RootDirectory) != scalar(parent_handle):
        raise AssertionError("rename RootDirectory is not the destination parent")
    if scalar(information.FileNameLength) != len(encoded):
        raise AssertionError("rename FileNameLength is not exact UTF-16 bytes")
    name_address = ctypes.addressof(information) + sf._FILE_RENAME_INFO.FileName.offset
    name = ctypes.wstring_at(name_address, len(encoded) // 2)
    if name != expected_name or any(separator in name for separator in ("/", "\\")):
        raise AssertionError("rename did not use one exact relative component")
    return {
        "source_handle": source_handle,
        "source_identity": support.handle_identity(source_handle),
        "name": name,
    }


def _bind_probe(probe, function):
    """Adapt one probe method to a call that passes the session first."""

    def call(session, *args, **kwargs):
        return function(probe, session, *args, **kwargs)

    return call


class _RenameCausalityProbe:
    """Observe the complete handle-bound R6 protocol without product callbacks."""

    def __init__(
        self,
        sf,
        active,
        monkeypatch,
        *,
        source_identity,
        native_impl,
        expect_native: bool = True,
    ) -> None:
        self.sf = sf
        self.active = active
        self.source_identity = source_identity
        self.destination_identity = None
        self.expect_native = expect_native
        self.native_impl = native_impl
        self.relative = support.required(sf, "_win_open_relative_v1")
        self.real_info = sf._win_info
        self.real_profile = sf._SecureRootSession._verify_windows_profile
        self.real_close = sf._KERNEL32.CloseHandle
        self.real_inventory = sf._win_inventory
        self.source_handle = None
        self.destination_handle = None
        self.source_open_attempts = 0
        self.destination_open_attempts = 0
        self.source_open_failed = False
        self.destination_open_failed = False
        self.source_identity_before = 0
        self.source_profile_before = 0
        self.source_identity_after = 0
        self.source_profile_after = 0
        self.destination_identity_seen = 0
        self.destination_profile_seen = 0
        self.source_close_count = 0
        self.destination_close_count = 0
        self.native_calls = []
        self.native_started = False
        self.native_finished = False
        self.post_inventories = []

        monkeypatch.setattr(sf, "_win_open_relative_v1", self._checked_relative)
        monkeypatch.setattr(sf, "_win_info", self._checked_info)
        monkeypatch.setattr(
            sf._SecureRootSession,
            "_verify_windows_profile",
            # A bound method is not a descriptor: installed on the class it
            # would receive the handle in place of the session. The function of
            # the class is installed instead, so the session arrives first.
            _bind_probe(self, type(self)._checked_profile),
        )
        monkeypatch.setattr(sf._KERNEL32, "CloseHandle", self._checked_close)
        monkeypatch.setattr(
            sf._NTDLL,
            "NtSetInformationFile",
            self._checked_native,
        )
        monkeypatch.setattr(sf, "_win_inventory", self._checked_inventory)

        def forbidden_fallback(*args, **kwargs):
            del args, kwargs
            raise AssertionError("R6 used an absolute/path-based rename fallback")

        monkeypatch.setattr(sf, "_win_open_path", forbidden_fallback)
        monkeypatch.setattr(sf, "_win_destination_exists", forbidden_fallback)
        monkeypatch.setattr(sf.os, "rename", forbidden_fallback)
        monkeypatch.setattr(sf.os, "replace", forbidden_fallback)
        monkeypatch.setattr(sf._KERNEL32, "MoveFileW", forbidden_fallback)
        monkeypatch.setattr(sf._KERNEL32, "MoveFileExW", forbidden_fallback)
        monkeypatch.setattr(sf._KERNEL32, "ReplaceFileW", forbidden_fallback)

    @staticmethod
    def _contains_component(call, name: str) -> bool:
        return any(value == name or value == (name,) for value in call.values())

    def _contains_parent(self, call) -> bool:
        for value in call.values():
            try:
                if _nt_scalar(value) == _nt_scalar(self.active._root_handle):
                    return True
            except (TypeError, ValueError):
                pass
        return False

    def _checked_relative(self, *args, **kwargs):
        call = _bound_call(self.relative, args, kwargs)
        source = self._contains_component(call, "source")
        destination = self._contains_component(call, "destination")
        if source == destination:
            return self.relative(*args, **kwargs)
        if call.get("purpose") != self.sf._NtOpenPurposeV1.mutating_open:
            raise AssertionError("R6 reconciliation used the wrong relative-open domain")
        if not self._contains_parent(call):
            raise AssertionError("R6 object open was not relative to its parent handle")
        if source:
            self.source_open_attempts += 1
            if self.source_open_attempts != 1:
                raise AssertionError("R6 opened the source more than once")
        else:
            self.destination_open_attempts += 1
            if self.destination_open_attempts != 1:
                raise AssertionError("R6 opened the destination more than once")
        try:
            result = self.relative(*args, **kwargs)
        except BaseException:
            if source:
                self.source_open_failed = True
            else:
                self.destination_open_failed = True
            raise
        handle = _nt_scalar(result)
        if not handle:
            raise AssertionError("successful R6 relative open returned no handle")
        if source:
            if self.native_started:
                raise AssertionError("R6 opened the source only after native rename")
            self.source_handle = handle
            expected = self.source_identity
        else:
            if self.expect_native and not self.native_finished:
                raise AssertionError("R6 opened the destination before native rename")
            self.destination_handle = handle
            expected = self.destination_identity
        independently_observed = support.handle_identity(handle)
        if expected is not None and independently_observed != expected:
            raise AssertionError("R6 relative handle has the wrong independent FileId")
        return result

    @staticmethod
    def _matches_identity(observed, expected) -> bool:
        return (
            expected is not None
            and observed.volume == expected["volume"]
            and observed.object_id == expected["file_id"]
        )

    def _checked_info(self, handle):
        value = self.real_info(handle)
        scalar = _nt_scalar(handle)
        if self.source_handle is not None and scalar == self.source_handle:
            if not self._matches_identity(value[0], self.source_identity):
                raise AssertionError("R6 product FileId check observed the wrong source")
            if self.native_finished:
                self.source_identity_after += 1
            else:
                self.source_identity_before += 1
        if self.destination_handle is not None and scalar == self.destination_handle:
            if not self._matches_identity(value[0], self.destination_identity):
                raise AssertionError("R6 product FileId check observed the wrong destination")
            self.destination_identity_seen += 1
        return value

    def _checked_profile(self, session, handle, *, directory, profile):
        scalar = _nt_scalar(handle)
        expected_role = self.sf._BirthObjectRole("birth_confidential")
        if scalar in {self.source_handle, self.destination_handle}:
            if directory or profile is not expected_role:
                raise AssertionError("R6 product checked the wrong type or role profile")
            if scalar == self.source_handle:
                if self.native_finished:
                    self.source_profile_after += 1
                else:
                    self.source_profile_before += 1
            else:
                self.destination_profile_seen += 1
        return self.real_profile(
            session,
            handle,
            directory=directory,
            profile=profile,
        )

    def _checked_native(self, *args):
        if not self.expect_native:
            raise AssertionError("ambiguous retry repeated the native rename")
        if self.native_calls:
            raise AssertionError("R6 attempted the native rename more than once")
        if (
            self.source_handle is None
            or self.source_identity_before < 1
            or self.source_profile_before < 1
        ):
            raise AssertionError("R6 native rename preceded source FileId/profile validation")
        self.native_calls.append(
            _assert_native_rename_call(
                args,
                self.sf,
                self.active,
                expected_source_handle=self.source_handle,
            )
        )
        self.native_started = True
        try:
            return self.native_impl(*args)
        finally:
            self.native_finished = True

    def _checked_close(self, handle):
        scalar = _nt_scalar(handle)
        if self.source_handle is not None and scalar == self.source_handle:
            if self.expect_native and not self.native_finished:
                raise AssertionError("R6 closed the source before its native rename")
            self.source_close_count += 1
        if self.destination_handle is not None and scalar == self.destination_handle:
            if self.destination_identity_seen < 1 or self.destination_profile_seen < 1:
                raise AssertionError("R6 closed destination before FileId/profile validation")
            self.destination_close_count += 1
        return self.real_close(handle)

    def _checked_inventory(self, handle, *args, **kwargs):
        result = self.real_inventory(handle, *args, **kwargs)
        destination_validated = (
            self.destination_identity_seen >= 1 and self.destination_profile_seen >= 1
        )
        if (
            _nt_scalar(handle) == _nt_scalar(self.active._root_handle)
            and (self.native_finished or (not self.expect_native and destination_validated))
        ):
            self.post_inventories.append(result)
        return result

    def assert_source_native(self) -> None:
        if (
            self.source_open_attempts != 1
            or self.source_open_failed
            or len(self.native_calls) != 1
            or self.source_identity_before < 1
            or self.source_profile_before < 1
            or self.source_close_count != 1
        ):
            raise AssertionError("R6 source handle causality is incomplete")

    def assert_error_reconciled(
        self,
        expected_parent,
        *,
        destination_identity=None,
    ) -> None:
        self.assert_source_native()
        if self.source_identity_after < 1 or self.source_profile_after < 1:
            raise AssertionError("R6 did not revalidate the source after native error")
        if destination_identity is None:
            if (
                self.destination_open_attempts != 1
                or not self.destination_open_failed
                or self.destination_handle is not None
            ):
                raise AssertionError("R6 did not reconcile the absent destination relatively")
        else:
            if (
                self.destination_identity != destination_identity
                or self.destination_open_attempts != 1
                or self.destination_open_failed
                or self.destination_identity_seen < 1
                or self.destination_profile_seen < 1
                or self.destination_close_count != 1
            ):
                raise AssertionError("R6 destination reconciliation is incomplete")
        if self.post_inventories != [expected_parent, expected_parent]:
            raise AssertionError("R6 error reconciliation did not prove one stable parent state")

    def assert_success_reconciled(self, expected_parent, destination_identity) -> None:
        self.assert_source_native()
        if (
            self.destination_identity != destination_identity
            or self.destination_open_attempts != 1
            or self.destination_open_failed
            or self.destination_identity_seen < 1
            or self.destination_profile_seen < 1
            or self.destination_close_count != 1
            or self.post_inventories != [expected_parent, expected_parent]
        ):
            raise AssertionError("R6 successful retry reconciliation is incomplete")

    def assert_ambiguous_reconciled(self, expected_parent, destination_identity) -> None:
        if (
            self.native_calls
            or self.source_open_attempts != 1
            or not self.source_open_failed
            or self.source_handle is not None
            or self.destination_identity != destination_identity
            or self.destination_open_attempts != 1
            or self.destination_open_failed
            or self.destination_identity_seen < 1
            or self.destination_profile_seen < 1
            or self.destination_close_count != 1
            or self.post_inventories != [expected_parent, expected_parent]
        ):
            raise AssertionError("R6 ambiguous retry reconciliation is incomplete")


@pytest.mark.parametrize("case", R6_PORTABLE_WINDOWS, ids=R6_PORTABLE_WINDOWS)
def test_r6_windows_rename_contract(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    directory_fixture = case == "cached-source-renames"
    binding_specs = [
        (("source",), directory_fixture, "birth_confidential"),
    ]
    if directory_fixture:
        binding_specs.extend(
            [
                (("target-parent",), True, "birth_confidential"),
                (
                    ("target-parent", "destination"),
                    True,
                    "birth_confidential",
                ),
            ]
        )
    else:
        binding_specs.append(
            (("destination",), False, "birth_confidential")
        )
    bindings = support.explicit_role_bindings(sf, *binding_specs)
    root = tmp_path / "birth"
    if case == "cached-source-renames":
        with support.provisioner_session(
            root, role_bindings=bindings
        ) as creator:
            support.create_directory(
                creator, ("target-parent",), "birth_confidential"
            )
            _rename_fixture(creator, root, directory=True)
    # Every R6 mutation is performed under the provisioner's global lock; any
    # per-cell interception below is therefore installed only after the lock
    # itself has been opened and acquired.
    with support.provisioner_session(
        root,
        create_root=case != "cached-source-renames",
        role_bindings=bindings,
    ) as active:
        if case == "cached-source-renames":
            source = root / "source"
            before = support.identity(source, directory=True)
            active._inventory_state(("source",))
            active._inventory_state(("target-parent",))
            parent_handle = active._directories[("target-parent",)]
            cached_handle = active._directories[("source",)]
            root_before = active._inventory_state(())
            target_before = active._inventory_state(("target-parent",))
            source_entry = next(item for item in root_before if item.name == "source")
            expected_root = tuple(item for item in root_before if item.name != "source")
            expected_target = tuple(
                sorted(
                    (
                        *target_before,
                        sf._InventoryEntry(
                            name="destination",
                            identity=source_entry.identity,
                            kind=source_entry.kind,
                            role=source_entry.role,
                            links=source_entry.links,
                            size=source_entry.size,
                        ),
                    ),
                    key=lambda item: item.name.encode("utf-8"),
                )
            )
            native = sf._NTDLL.NtSetInformationFile
            native_close = sf._KERNEL32.CloseHandle
            relative = support.required(sf, "_win_open_relative_v1")
            real_info = sf._win_info
            real_profile = sf._SecureRootSession._verify_windows_profile
            real_inventory = sf._win_inventory
            native_calls: list[dict[str, object]] = []
            mutating_handle: int | None = None
            mutating_active = False
            mutating_identity_seen = False
            mutating_profile_seen = False
            mutating_close_count = 0
            post_handle: int | None = None
            post_identity_seen = False
            post_profile_seen = False
            post_close_count = 0
            post_inventories: dict[str, list[tuple[object, ...]]] = {
                "source-parent": [],
                "target-parent": [],
            }
            native_started = False
            operation_complete = False

            def contains_handle(call, expected) -> bool:
                for value in call.values():
                    try:
                        if _nt_scalar(value) == _nt_scalar(expected):
                            return True
                    except (TypeError, ValueError):
                        continue
                return False

            def checked_relative(*args, **kwargs):
                nonlocal mutating_handle, mutating_active, post_handle
                call = _bound_call(relative, args, kwargs)
                source_open = "source" in call.values() and not native_started
                destination_open = "destination" in call.values() and native_started
                if source_open:
                    if call.get("purpose") != sf._NtOpenPurposeV1.mutating_open:
                        raise AssertionError("cached rename did not request a mutating source")
                    if not contains_handle(call, active._root_handle):
                        raise AssertionError("cached rename source was not parent-relative")
                if destination_open:
                    if call.get("purpose") != sf._NtOpenPurposeV1.mutating_open:
                        raise AssertionError("cached rename post-open used the wrong purpose")
                    if not contains_handle(call, parent_handle):
                        raise AssertionError("cached rename destination was not parent-relative")
                result = relative(*args, **kwargs)
                value = _nt_scalar(result)
                if source_open:
                    if mutating_handle is not None or value == _nt_scalar(cached_handle):
                        raise AssertionError(
                            "cached rename reused its read/cache handle for mutation"
                        )
                    if support.handle_identity(value) != before:
                        raise AssertionError("mutating source handle has a different FileId")
                    mutating_handle = value
                    mutating_active = True
                if destination_open:
                    if post_handle is not None:
                        raise AssertionError("cached rename reopened destination twice")
                    post_handle = value
                return result

            def checked_info(handle):
                nonlocal mutating_identity_seen, post_identity_seen
                value = real_info(handle)
                if (
                    mutating_active
                    and mutating_handle is not None
                    and _nt_scalar(handle) == mutating_handle
                    and not native_started
                ):
                    mutating_identity_seen = True
                if post_handle is not None and _nt_scalar(handle) == post_handle:
                    post_identity_seen = True
                return value

            def checked_profile(session, handle, *, directory, profile):
                nonlocal mutating_profile_seen, post_profile_seen
                if (
                    mutating_active
                    and mutating_handle is not None
                    and _nt_scalar(handle) == mutating_handle
                    and not native_started
                ):
                    mutating_profile_seen = True
                if post_handle is not None and _nt_scalar(handle) == post_handle:
                    post_profile_seen = True
                return real_profile(
                    session, handle, directory=directory, profile=profile
                )

            def protect_cached_source(handle):
                nonlocal mutating_active, mutating_close_count, post_close_count
                value = _nt_scalar(handle)
                if value == _nt_scalar(cached_handle) and not operation_complete:
                    raise AssertionError(
                        "cached source handle was closed instead of being remapped"
                    )
                if mutating_active and value == mutating_handle:
                    if not native_started:
                        raise AssertionError("mutating source closed before native rename")
                    mutating_active = False
                    mutating_close_count += 1
                elif post_handle is not None and value == post_handle:
                    post_close_count += 1
                return native_close(handle)

            def checked_nested_directory_rename(*args):
                nonlocal native_started
                if (
                    mutating_handle is None
                    or not mutating_active
                    or not mutating_identity_seen
                    or not mutating_profile_seen
                ):
                    raise AssertionError("cached source was not fully validated before rename")
                native_calls.append(
                    _assert_native_rename_call(
                        args,
                        sf,
                        active,
                        expected_parent_handle=parent_handle,
                        expected_source_handle=mutating_handle,
                    )
                )
                native_started = True
                return native(*args)

            def checked_inventory(handle, *args, **kwargs):
                result = real_inventory(handle, *args, **kwargs)
                if native_started:
                    if _nt_scalar(handle) == _nt_scalar(active._root_handle):
                        post_inventories["source-parent"].append(result)
                    elif _nt_scalar(handle) == _nt_scalar(parent_handle):
                        post_inventories["target-parent"].append(result)
                return result

            monkeypatch.setattr(sf, "_win_open_relative_v1", checked_relative)
            monkeypatch.setattr(
                sf._NTDLL,
                "NtSetInformationFile",
                checked_nested_directory_rename,
            )
            monkeypatch.setattr(
                sf._KERNEL32, "CloseHandle", protect_cached_source
            )
            monkeypatch.setattr(sf, "_win_info", checked_info)
            monkeypatch.setattr(
                sf._SecureRootSession, "_verify_windows_profile", checked_profile
            )
            monkeypatch.setattr(sf, "_win_inventory", checked_inventory)
            returned = active.rename_no_replace(
                ("source",),
                ("target-parent", "destination"),
                directory=True,
            )
            destination = root / "target-parent" / "destination"
            after = support.identity(destination, directory=True)
            if source.exists() or not destination.is_dir():
                raise AssertionError("nested directory rename changed the wrong namespace")
            if len(native_calls) != 1 or native_calls[0]["source_identity"] != before:
                raise AssertionError("nested directory rename did not bind the native source")
            if (
                mutating_close_count != 1
                or not post_identity_seen
                or not post_profile_seen
                or post_close_count != 1
                or post_inventories["source-parent"] != [expected_root, expected_root]
                or post_inventories["target-parent"] != [expected_target, expected_target]
            ):
                raise AssertionError(
                    "cached rename lacks exact post-validation or parent reconciliation"
                )
            if (
                returned.volume != after["volume"]
                or returned.object_id != after["file_id"]
                or ("source",) in active._directories
                or ("target-parent", "destination") not in active._directories
                or _nt_scalar(
                    active._directories[("target-parent", "destination")]
                )
                != _nt_scalar(cached_handle)
            ):
                raise AssertionError("directory cache was not remapped to the nested target")
            cached = support.handle_identity(
                active._directories[("target-parent", "destination")]
            )
            if cached != after:
                raise AssertionError("remapped directory handle lost its identity")
            operation_complete = True
            return
        if case == "fresh-source-profile-rejected":
            sid = support.service_sid()
            source = root / "source"
            oracle = support.identity_oracle()
            support.create_profiled(
                source,
                "confidential",
                directory=False,
                sid=sid,
                payload=b"secret",
            )
            oracle.apply_profile(
                source,
                "confidential",
                sid,
                directory=False,
                service_mask=oracle._FILE_READ_MASK ^ 0x00000001,
            )
            try:
                oracle.assert_exact_profile(source, "confidential", sid, directory=False)
            except AssertionError:
                pass
            else:
                raise AssertionError("independent ACL corruption did not take effect")
            before = support.windows_tree_snapshot(root)
            support.require_code(
                lambda: active.rename_no_replace(
                    ("source",), ("destination",), directory=False
                ),
                "birth_provisioning_acl_unsafe",
            )
            if not source.exists() or (root / "destination").exists():
                raise AssertionError("rejected source changed namespace")
            if support.windows_tree_snapshot(root) != before:
                raise AssertionError("ACL rejection changed source metadata or inventory")
            return
        if case == "destination-existing-conflict":
            source, source_before = _rename_fixture(active, root)
            support.create_file(
                active, ("destination",), b"destination-payload", "birth_confidential"
            )
            destination = root / "destination"
            destination_before = support.identity(destination, directory=False)
            before = support.windows_tree_snapshot(root)
            support.require_code(
                lambda: active.rename_no_replace(
                    ("source",), ("destination",), directory=False
                ),
                "birth_provisioning_transaction_conflict",
            )
            if source.read_bytes() != b"source-payload" or destination.read_bytes() != b"destination-payload":
                raise AssertionError("conflict modified bytes")
            if support.identity(source, directory=False) != source_before:
                raise AssertionError("conflict replaced source")
            if support.identity(destination, directory=False) != destination_before:
                raise AssertionError("conflict replaced destination")
            if support.windows_tree_snapshot(root) != before:
                raise AssertionError("conflict changed metadata or unrelated inventory")
            return
        if case == "native-error-destination-absent":
            source, before = _rename_fixture(active, root)
            expected_parent = active._inventory_state(())
            snapshot = support.windows_tree_snapshot(root)
            probe = _RenameCausalityProbe(
                sf,
                active,
                monkeypatch,
                source_identity=before,
                native_impl=lambda *args: 0,
            )
            monkeypatch.setattr(sf.ctypes, "get_last_error", lambda: 5)
            support.require_code(
                lambda: active.rename_no_replace(
                    ("source",), ("destination",), directory=False
                ),
                "birth_provisioning_elevation_required",
            )
            probe.assert_error_reconciled(expected_parent)
            if not source.exists() or (root / "destination").exists():
                raise AssertionError("native error changed namespace")
            if support.identity(source, directory=False) != before:
                raise AssertionError("native error changed source identity")
            if support.windows_tree_snapshot(root) != snapshot:
                raise AssertionError("native error changed metadata, bytes or inventory")
            return
        if case in {"success-postvalidation", "source-fileid128-preserved"}:
            native = sf._NTDLL.NtSetInformationFile
            native_close = sf._KERNEL32.CloseHandle
            relative = support.required(sf, "_win_open_relative_v1")
            real_info = sf._win_info
            real_profile = sf._SecureRootSession._verify_windows_profile
            real_inventory = sf._win_inventory
            native_calls: list[dict[str, object]] = []
            events: list[str] = []
            post_inventories: list[tuple[object, ...]] = []
            source_handle: int | None = None
            source_active = False
            source_identity_seen = False
            source_profile_seen = False
            source_close_count = 0
            native_started = False
            post_handle: int | None = None

            def contains_handle(call, expected) -> bool:
                for value in call.values():
                    try:
                        if _nt_scalar(value) == _nt_scalar(expected):
                            return True
                    except (TypeError, ValueError):
                        continue
                return False

            def checked_relative(*args, **kwargs):
                nonlocal source_handle, source_active, post_handle
                call = _bound_call(relative, args, kwargs)
                is_source = "source" in call.values()
                is_destination = "destination" in call.values()
                if is_source and not native_started:
                    if call.get("purpose") != sf._NtOpenPurposeV1.mutating_open:
                        return relative(*args, **kwargs)
                    if source_handle is not None:
                        raise AssertionError(
                            "rename opened the mutating source more than once"
                        )
                    if not contains_handle(call, active._root_handle):
                        raise AssertionError(
                            "rename source open was not relative to its parent"
                        )
                if (
                    case == "success-postvalidation"
                    and is_destination
                    and events == ["native-rename"]
                ):
                    if call.get("purpose") != sf._NtOpenPurposeV1.mutating_open:
                        raise AssertionError(
                            "rename post-validation used the wrong relative-open domain"
                        )
                    if not contains_handle(call, active._root_handle):
                        raise AssertionError(
                            "rename post-validation was not relative to the target parent"
                        )
                result = relative(*args, **kwargs)
                if (
                    is_source
                    and not native_started
                    and call.get("purpose") == sf._NtOpenPurposeV1.mutating_open
                ):
                    source_handle = _nt_scalar(result)
                    if not source_handle:
                        raise AssertionError("rename source open returned no handle")
                    source_active = True
                if (
                    case == "success-postvalidation"
                    and is_destination
                    and events == ["native-rename"]
                ):
                    post_handle = _nt_scalar(result)
                    if not post_handle:
                        raise AssertionError(
                            "rename post-validation returned no destination handle"
                        )
                    events.append("relative-open")
                return result

            def checked_rename(*args):
                nonlocal native_started
                if (
                    source_handle is None
                    or not source_active
                    or not source_identity_seen
                    or not source_profile_seen
                ):
                    raise AssertionError(
                        "rename native call preceded source handle-bound validation"
                    )
                native_calls.append(
                    _assert_native_rename_call(
                        args,
                        sf,
                        active,
                        expected_source_handle=source_handle,
                    )
                )
                native_started = True
                result = native(*args)
                if result:
                    events.append("native-rename")
                return result

            def checked_info(handle):
                nonlocal source_identity_seen
                value = real_info(handle)
                if (
                    source_handle is not None
                    and source_active
                    and not native_started
                    and _nt_scalar(handle) == source_handle
                ):
                    source_identity_seen = True
                if (
                    case == "success-postvalidation"
                    and post_handle is not None
                    and _nt_scalar(handle) == post_handle
                ):
                    if events != ["native-rename", "relative-open"]:
                        raise AssertionError(
                            "rename destination identity was observed out of order"
                        )
                    events.append("identity")
                return value

            def checked_profile(session, handle, *, directory, profile):
                nonlocal source_profile_seen
                if (
                    source_handle is not None
                    and source_active
                    and not native_started
                    and _nt_scalar(handle) == source_handle
                ):
                    source_profile_seen = True
                if (
                    case == "success-postvalidation"
                    and post_handle is not None
                    and _nt_scalar(handle) == post_handle
                ):
                    if events != [
                        "native-rename",
                        "relative-open",
                        "identity",
                    ]:
                        raise AssertionError(
                            "rename destination ACL was observed out of order"
                        )
                    events.append("profile")
                return real_profile(
                    session,
                    handle,
                    directory=directory,
                    profile=profile,
                )

            def checked_inventory(handle, *args, **kwargs):
                result = real_inventory(handle, *args, **kwargs)
                if (
                    case == "success-postvalidation"
                    and _nt_scalar(handle) == _nt_scalar(active._root_handle)
                    and events
                    and events[-1] in {"profile", "inventory"}
                ):
                    post_inventories.append(result)
                    events.append("inventory")
                return result

            def checked_close(handle):
                nonlocal source_active, source_close_count
                if (
                    source_active
                    and source_handle is not None
                    and _nt_scalar(handle) == source_handle
                ):
                    if not native_started:
                        raise AssertionError(
                            "rename source handle closed before native use"
                        )
                    source_active = False
                    source_close_count += 1
                return native_close(handle)

            monkeypatch.setattr(sf, "_win_open_relative_v1", checked_relative)
            monkeypatch.setattr(
                sf._NTDLL, "NtSetInformationFile", checked_rename
            )
            monkeypatch.setattr(sf._KERNEL32, "CloseHandle", checked_close)
            monkeypatch.setattr(sf, "_win_info", checked_info)
            monkeypatch.setattr(
                sf._SecureRootSession,
                "_verify_windows_profile",
                checked_profile,
            )
            monkeypatch.setattr(sf, "_win_inventory", checked_inventory)
            source, before = _rename_fixture(active, root)
            parent_before = active._inventory_state(())
            expected_parent = tuple(
                sf._InventoryEntry(
                    name="destination" if item.name == "source" else item.name,
                    identity=item.identity,
                    kind=item.kind,
                    role=item.role,
                    links=item.links,
                    size=item.size,
                )
                for item in parent_before
            )
            returned = active.rename_no_replace(
                ("source",), ("destination",), directory=False
            )
            destination = root / "destination"
            if source.exists() or not destination.exists():
                raise AssertionError("rename did not produce the final namespace")
            after = support.identity(destination, directory=False)
            if len(native_calls) != 1:
                raise AssertionError("rename did not use exactly one native call")
            if (
                native_calls[0]["source_identity"] != before
                or source_close_count != 1
            ):
                raise AssertionError("native rename handle is not the source identity")
            if case == "source-fileid128-preserved":
                if len(before["file_id"]) != 32 or returned.object_id != before["file_id"]:
                    raise AssertionError("product did not preserve full FileId128")
            elif returned.volume != after["volume"] or returned.object_id != after["file_id"]:
                raise AssertionError("returned identity is not the postvalidated destination")
            if case == "success-postvalidation" and post_inventories != [
                expected_parent,
                expected_parent,
            ]:
                raise AssertionError(
                    "rename post-validation inventories differ from exact final state"
                )
            if case == "success-postvalidation" and events != [
                "native-rename",
                "relative-open",
                "identity",
                "profile",
                "inventory",
                "inventory",
            ]:
                raise AssertionError(
                    f"rename post-validation sequence is incomplete: {events!r}"
                )
            return
        # A real cross-volume destination is not needed: the independent
        # volume identities are injected at the last pre-native comparison.
        source, before = _rename_fixture(active, root)
        snapshot = support.windows_tree_snapshot(root)
        original_info = sf._win_info
        original_native = sf._NTDLL.NtSetInformationFile
        source_seen = False
        native_calls = 0

        def different_volume(handle):
            nonlocal source_seen
            value = original_info(handle)
            observed = value[0]
            if (
                observed.volume == before["volume"]
                and observed.object_id == before["file_id"]
            ):
                source_seen = True
                return value
            if source_seen and _nt_scalar(handle) == _nt_scalar(active._root_handle):
                fake = sf._ObjectIdentity("ffffffffffffffff", value[0].object_id)
                return (fake,) + value[1:]
            return value

        def record_native(*args):
            nonlocal native_calls
            native_calls += 1
            return original_native(*args)

        monkeypatch.setattr(sf, "_win_info", different_volume)
        monkeypatch.setattr(
            sf._NTDLL, "NtSetInformationFile", record_native
        )
        support.require_code(
            lambda: active.rename_no_replace(("source",), ("destination",), directory=False),
            "birth_provisioning_atomic_install_unsupported",
        )
        if not source_seen or native_calls:
            raise AssertionError(
                "different-volume gate did not reject the observed source/root identities before native rename"
            )
        if not source.exists() or (root / "destination").exists():
            raise AssertionError("different-volume refusal changed namespace")
        if support.identity(source, directory=False) != before:
            raise AssertionError("different-volume refusal changed source")
        if support.windows_tree_snapshot(root) != snapshot:
            raise AssertionError("different-volume refusal changed metadata or inventory")


@pytest.mark.parametrize("case", R6_CONCURRENCY_WINDOWS, ids=R6_CONCURRENCY_WINDOWS)
def test_r6_windows_rename_races(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    if case == "destination-race-conflict":
        import threading

        root = tmp_path / "birth"
        bindings = support.explicit_role_bindings(
            sf,
            (("source",), False, "birth_confidential"),
            (("destination",), False, "birth_confidential"),
        )
        with support.session(root, role_bindings=bindings) as active:
            with support.exclusive(active):
                source_path, source_before = _rename_fixture(active, root)
                parent_before = active._inventory_state(())
                source_entry = next(
                    item for item in parent_before if item.name == "source"
                )
                pre_race = _snapshot_by_name(root)
                start = threading.Event()
                finished = threading.Event()
                failures = []
                sid = support.service_sid()
                competitor_state: dict[str, object] = {}

                def competitor():
                    try:
                        if not start.wait(30):
                            raise AssertionError("race barrier was not released")
                        support.create_profiled(
                            root / "destination",
                            "confidential",
                            directory=False,
                            sid=sid,
                            payload=b"competitor",
                        )
                        competitor_state["identity"] = support.identity(
                            root / "destination", directory=False
                        )
                        competitor_state["snapshot"] = _snapshot_by_name(root)
                    except BaseException as exc:
                        failures.append(exc)
                    finally:
                        finished.set()

                thread = threading.Thread(target=competitor)
                thread.start()
                native = sf._NTDLL.NtSetInformationFile
                expected_parent = None

                def raced_native(*args):
                    nonlocal expected_parent
                    start.set()
                    if not finished.wait(30):
                        raise AssertionError("competitor did not finish")
                    if failures:
                        raise failures[0]
                    destination_identity = competitor_state.get("identity")
                    if not isinstance(destination_identity, dict):
                        raise AssertionError("competitor did not publish destination FileId")
                    probe.destination_identity = destination_identity
                    destination_entry = dataclasses.replace(
                        source_entry,
                        name="destination",
                        identity=sf._ObjectIdentity(
                            destination_identity["volume"],
                            destination_identity["file_id"],
                        ),
                        links=destination_identity["links"],
                        size=destination_identity["size"],
                    )
                    expected_parent = tuple(
                        sorted(
                            (*parent_before, destination_entry),
                            key=lambda item: item.name.encode("utf-8"),
                        )
                    )
                    return native(*args)

                probe = _RenameCausalityProbe(
                    sf,
                    active,
                    monkeypatch,
                    source_identity=source_before,
                    native_impl=raced_native,
                )
                support.require_code(
                    lambda: active.rename_no_replace(
                        ("source",), ("destination",), directory=False
                    ),
                    "birth_provisioning_transaction_conflict",
                )
                thread.join(30)
                if thread.is_alive() or failures or len(probe.native_calls) != 1:
                    raise AssertionError("destination race was not deterministic")
                destination_before = competitor_state.get("identity")
                conflict_snapshot = competitor_state.get("snapshot")
                if not isinstance(conflict_snapshot, dict) or expected_parent is None:
                    raise AssertionError(
                        "competitor did not publish its completed filesystem state"
                    )
                probe.assert_error_reconciled(
                    expected_parent,
                    destination_identity=destination_before,
                )
                if source_path.read_bytes() != b"source-payload":
                    raise AssertionError("race changed source bytes")
                if (root / "destination").read_bytes() != b"competitor":
                    raise AssertionError("race replaced destination")
                if support.identity(source_path, directory=False) != source_before:
                    raise AssertionError("race changed source identity")
                if (
                    support.identity(root / "destination", directory=False)
                    != destination_before
                ):
                    raise AssertionError("race changed destination identity")
                support.assert_profile(
                    root / "destination",
                    "confidential",
                    directory=False,
                    sid=sid,
                )
                after = _snapshot_by_name(root)
                if after != conflict_snapshot:
                    raise AssertionError(
                        "failed rename changed the competitor state, ACL or inventory"
                    )
                if set(conflict_snapshot) != set(pre_race) | {"destination"}:
                    raise AssertionError(
                        "destination race changed more than the competing name"
                    )
                for name in ("provisioning-v1.lock", "source"):
                    if conflict_snapshot[name] != pre_race[name]:
                        raise AssertionError(
                            f"destination race changed unrelated {name}"
                        )
        return
    support.require_windows()
    worker = support.REPOSITORY / "tests" / "windows_identity" / "rm0008_2a_acceptance" / "_worker.py"
    import subprocess
    import time

    root = tmp_path / "birth"
    barrier = tmp_path / "rename.barrier"
    process = subprocess.Popen(
        [support.sys.executable, str(worker), "rename", case, str(root), str(barrier)],
        close_fds=True,
        env=support.worker_environment(),
    )
    try:
        deadline = time.monotonic() + 30
        while not barrier.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not barrier.exists():
            raise AssertionError(f"child did not reach rename barrier, exit={process.poll()}")
        support.terminate_process(process)
        state = _worker_state(barrier)
        source_path = root / "source"
        destination_path = root / "destination"
        pre_native = _published_snapshot(state)
        if set(pre_native) != {".", "provisioning-v1.lock", "source"}:
            raise AssertionError("rename worker published an unexpected inventory")
        before_retry = _snapshot_by_name(root)
        expected_names = {".", "provisioning-v1.lock"}
        if case.endswith("before-native"):
            expected_names.add("source")
        else:
            expected_names.add("destination")
        if set(before_retry) != expected_names:
            raise AssertionError(
                f"rename crash left an unexpected inventory: {sorted(before_retry)}"
            )
        expected_crash = dict(pre_native)
        if case.endswith("after-native"):
            source_row = expected_crash.pop("source")
            expected_crash["destination"] = ("destination",) + source_row[1:]
        if before_retry != expected_crash:
            raise AssertionError(
                "rename crash changed identity, ACL, metadata, bytes or unrelated inventory"
            )
        surviving = source_path if source_path.exists() else destination_path
        _assert_crash_file(surviving, state, b"crash-rename")
        bindings = support.explicit_role_bindings(
            sf,
            (("source",), False, "birth_confidential"),
            (("destination",), False, "birth_confidential"),
        )
        with support.session(
            root, create_root=False, role_bindings=bindings
        ) as active:
            with support.exclusive(active):
                retry_parent = active._inventory_state(())
                native_rename = sf._NTDLL.NtSetInformationFile
                if case.endswith("before-native"):
                    source_entry = next(
                        item for item in retry_parent if item.name == "source"
                    )
                    expected_parent = tuple(
                        sorted(
                            (
                                *(
                                    item
                                    for item in retry_parent
                                    if item.name != "source"
                                ),
                                dataclasses.replace(
                                    source_entry,
                                    name="destination",
                                ),
                            ),
                            key=lambda item: item.name.encode("utf-8"),
                        )
                    )
                    probe = _RenameCausalityProbe(
                        sf,
                        active,
                        monkeypatch,
                        source_identity=state["identity"],
                        native_impl=native_rename,
                    )
                    probe.destination_identity = state["identity"]
                    returned = active.rename_no_replace(
                        ("source",), ("destination",), directory=False
                    )
                    if (
                        returned.volume != state["identity"]["volume"]
                        or returned.object_id != state["identity"]["file_id"]
                    ):
                        raise AssertionError("rename retry returned a different identity")
                    probe.assert_success_reconciled(
                        expected_parent,
                        state["identity"],
                    )
                else:
                    probe = _RenameCausalityProbe(
                        sf,
                        active,
                        monkeypatch,
                        source_identity=state["identity"],
                        native_impl=native_rename,
                        expect_native=False,
                    )
                    probe.destination_identity = state["identity"]
                    support.require_code(
                        lambda: active.rename_no_replace(
                            ("source",), ("destination",), directory=False
                        ),
                        "birth_provisioning_recovery_ambiguous",
                    )
                    probe.assert_ambiguous_reconciled(
                        retry_parent,
                        state["identity"],
                    )
        after_retry = _snapshot_by_name(root)
        if case.endswith("before-native"):
            _assert_crash_file(destination_path, state, b"crash-rename")
            common_before = {
                name: row
                for name, row in before_retry.items()
                if name != "source"
            }
            common_after = {
                name: row
                for name, row in after_retry.items()
                if name != "destination"
            }
            source_row = before_retry["source"]
            destination_row = after_retry.get("destination")
            if (
                common_after != common_before
                or destination_row is None
                or destination_row[1:] != source_row[1:]
            ):
                raise AssertionError(
                    "rename retry changed identity, ACL, metadata or unrelated inventory"
                )
        elif after_retry != before_retry:
            raise AssertionError("ambiguous rename retry changed the crash state")
    finally:
        if process.poll() is None:
            support.terminate_process(process)
