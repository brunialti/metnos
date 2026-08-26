"""Windows disposition and no-replace rename acceptance cells."""
from __future__ import annotations

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
    if len(expectation_type.__dataclass_fields__) != 11:
        raise AssertionError("unexpected disposition expectation schema")
    directory = case == "disposition-directory-access-mask"
    root = tmp_path / "birth"
    with support.session(root) as active:
        with support.exclusive(active):
            path, expectation = _prepare_disposition(active, root, sf, directory=directory)
            relative = support.required(sf, "_win_open_relative_v1")
            relative_calls = []

            def record_relative(*args, **kwargs):
                relative_calls.append(_bound_call(relative, args, kwargs))
                return relative(*args, **kwargs)

            if case == "disposition-relative-open":
                monkeypatch.setattr(sf, "_win_open_relative_v1", record_relative)
                create_file_w = sf._KERNEL32.CreateFileW

                def reject_absolute(*args):
                    raise AssertionError("disposition reopened a descendant by absolute path")

                monkeypatch.setattr(sf._KERNEL32, "CreateFileW", reject_absolute)
                try:
                    result = active.dispose_transaction_object(expectation)
                finally:
                    monkeypatch.setattr(sf._KERNEL32, "CreateFileW", create_file_w)
                if not relative_calls or "victim" not in relative_calls[0].values():
                    raise AssertionError("victim was not opened as a relative component")
                if not result.removed or path.exists():
                    raise AssertionError("relative disposition did not remove the object")
                return
            if case in {"disposition-file-access-mask", "disposition-directory-access-mask"}:
                monkeypatch.setattr(sf, "_win_open_relative_v1", record_relative)
                active.dispose_transaction_object(expectation)
                disposition_calls = [
                    call for call in relative_calls
                    if call.get("purpose") == sf._NtOpenPurposeV1.disposition
                ]
                if len(disposition_calls) != 1:
                    raise AssertionError("expected exactly one disposition relative open")
                observed = disposition_calls[0].get("desired_access")
                expected = (
                    sf._DELETE | sf._SYNCHRONIZE | sf._READ_CONTROL | sf._FILE_READ_ATTRIBUTES
                )
                expected |= sf._FILE_LIST_DIRECTORY | sf._FILE_TRAVERSE if directory else sf._FILE_READ_DATA
                if observed != expected:
                    raise AssertionError(f"wrong disposition access mask {observed!r}")
                return
            native = sf._KERNEL32.SetFileInformationByHandle
            native_calls = 0

            def native_result(*args):
                nonlocal native_calls
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
                if not sf._KERNEL32.SetFileAttributesW(str(path), 0x1):
                    raise sf.ctypes.WinError(sf.ctypes.get_last_error())
                monkeypatch.setattr(sf._KERNEL32, "SetFileInformationByHandle", native_result)
                support.require_code(
                    lambda: active.dispose_transaction_object(expectation),
                    "birth_provisioning_recovery_ambiguous",
                )
                if native_calls:
                    raise AssertionError("readonly object reached native disposition")
                return
            monkeypatch.setattr(sf._KERNEL32, "SetFileInformationByHandle", native_result)
            codes = {
                "disposition-ex-invalid-parameter-no-fallback": "birth_provisioning_atomic_install_unsupported",
                "disposition-ex-not-supported-no-fallback": "birth_provisioning_atomic_install_unsupported",
                "disposition-deletepending-false": "birth_provisioning_recovery_ambiguous",
                "disposition-access-denied-mapping": "birth_provisioning_elevation_required",
                "disposition-residual-error-mapping": "birth_provisioning_io_unavailable",
            }
            support.require_code(
                lambda: active.dispose_transaction_object(expectation), codes[case]
            )
            if native_calls != 1 or not path.exists():
                raise AssertionError("failed disposition did not preserve one native attempt and object")


@pytest.mark.parametrize("case", R3_CRASH, ids=R3_CRASH)
def test_r3_windows_disposition_crash_matrix(case: str, tmp_path: Path) -> None:
    support.require_windows()
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
        target = root / "victim"
        if case.endswith("before-native") and not target.exists():
            raise AssertionError("pre-native crash removed the object")
        if case.endswith("after-native") and target.exists():
            raise AssertionError("post-native crash did not leave the atomic absent state")
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


@pytest.mark.parametrize("case", R6_PORTABLE_WINDOWS, ids=R6_PORTABLE_WINDOWS)
def test_r6_windows_rename_contract(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    with support.session(tmp_path / "birth") as active:
        root = tmp_path / "birth"
        if case == "cached-source-renames":
            _successful_rename(active, root, directory=True)
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
                service_mask=oracle._FILE_READ_MASK,
            )
            try:
                oracle.assert_exact_profile(source, "confidential", sid, directory=False)
            except AssertionError:
                pass
            else:
                raise AssertionError("independent ACL corruption did not take effect")
            support.require_code(
                lambda: active.rename_no_replace(
                    ("source",), ("destination",), directory=False
                ),
                "birth_provisioning_acl_unsafe",
            )
            if not source.exists() or (root / "destination").exists():
                raise AssertionError("rejected source changed namespace")
            return
        if case == "destination-existing-conflict":
            source, source_before = _rename_fixture(active, root)
            support.create_file(
                active, ("destination",), b"destination-payload", "birth_confidential"
            )
            destination = root / "destination"
            destination_before = support.identity(destination, directory=False)
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
            return
        if case == "native-error-destination-absent":
            source, before = _rename_fixture(active, root)
            kernel = sf._KERNEL32
            original = kernel.SetFileInformationByHandle
            monkeypatch.setattr(kernel, "SetFileInformationByHandle", lambda *args: 0)
            monkeypatch.setattr(sf.ctypes, "get_last_error", lambda: 5)
            try:
                support.require_code(
                    lambda: active.rename_no_replace(
                        ("source",), ("destination",), directory=False
                    ),
                    "birth_provisioning_elevation_required",
                )
            finally:
                monkeypatch.setattr(kernel, "SetFileInformationByHandle", original)
            if not source.exists() or (root / "destination").exists():
                raise AssertionError("native error changed namespace")
            if support.identity(source, directory=False) != before:
                raise AssertionError("native error changed source identity")
            return
        if case in {"success-postvalidation", "source-fileid128-preserved"}:
            before, after, returned = _successful_rename(active, root)
            if case == "source-fileid128-preserved":
                if len(before["file_id"]) != 32 or returned.object_id != before["file_id"]:
                    raise AssertionError("product did not preserve full FileId128")
            elif returned.volume != after["volume"] or returned.object_id != after["file_id"]:
                raise AssertionError("returned identity is not the postvalidated destination")
            return
        # A real cross-volume destination is not needed: the independent
        # volume identities are injected at the last pre-native comparison.
        source, before = _rename_fixture(active, root)
        original_info = sf._win_info
        calls = 0

        def different_volume(handle):
            nonlocal calls
            value = original_info(handle)
            calls += 1
            if calls == 2:
                fake = sf._ObjectIdentity("ffffffffffffffff", value[0].object_id)
                return (fake,) + value[1:]
            return value

        monkeypatch.setattr(sf, "_win_info", different_volume)
        support.require_code(
            lambda: active.rename_no_replace(("source",), ("destination",), directory=False),
            "birth_provisioning_atomic_install_unsupported",
        )
        if not source.exists() or (root / "destination").exists():
            raise AssertionError("different-volume refusal changed namespace")
        if support.identity(source, directory=False) != before:
            raise AssertionError("different-volume refusal changed source")


@pytest.mark.parametrize("case", R6_CONCURRENCY_WINDOWS, ids=R6_CONCURRENCY_WINDOWS)
def test_r6_windows_rename_races(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    if case == "destination-race-conflict":
        import threading

        root = tmp_path / "birth"
        with support.session(root) as active:
            with support.exclusive(active):
                source_path, source_before = _rename_fixture(active, root)
                start = threading.Event()
                finished = threading.Event()
                failures = []
                sid = support.service_sid()

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
                    except BaseException as exc:
                        failures.append(exc)
                    finally:
                        finished.set()

                thread = threading.Thread(target=competitor)
                thread.start()
                native = sf._KERNEL32.SetFileInformationByHandle
                native_calls = 0

                def raced(*args):
                    nonlocal native_calls
                    native_calls += 1
                    start.set()
                    if not finished.wait(30):
                        raise AssertionError("competitor did not finish")
                    if failures:
                        raise failures[0]
                    return native(*args)

                monkeypatch.setattr(sf._KERNEL32, "SetFileInformationByHandle", raced)
                support.require_code(
                    lambda: active.rename_no_replace(
                        ("source",), ("destination",), directory=False
                    ),
                    "birth_provisioning_transaction_conflict",
                )
                thread.join(30)
                if thread.is_alive() or failures or native_calls != 1:
                    raise AssertionError("destination race was not deterministic")
                if source_path.read_bytes() != b"source-payload":
                    raise AssertionError("race changed source bytes")
                if (root / "destination").read_bytes() != b"competitor":
                    raise AssertionError("race replaced destination")
                if support.identity(source_path, directory=False) != source_before:
                    raise AssertionError("race changed source identity")
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
        source_path = root / "source"
        destination_path = root / "destination"
        if case.endswith("before-native"):
            if not source_path.exists() or destination_path.exists():
                raise AssertionError("pre-native crash left a non-atomic namespace")
        elif source_path.exists() or not destination_path.exists():
            raise AssertionError("post-native crash left a non-atomic namespace")
        surviving = source_path if source_path.exists() else destination_path
        if surviving.read_bytes() != b"crash-rename":
            raise AssertionError("crash changed payload bytes")
    finally:
        if process.poll() is None:
            support.terminate_process(process)
