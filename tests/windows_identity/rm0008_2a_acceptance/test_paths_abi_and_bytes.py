"""Windows path, native ABI/status, rename mapping and binary I/O cells."""
from __future__ import annotations

import importlib
import inspect
import os
import secrets
import subprocess
from pathlib import Path

import pytest

import _windows_support as support


G9_CASES = (
    "local-canonical",
    "local-long",
    "local-verbatim",
    "local-case-variant",
    "unc-loopback-positive",
    "unc-unreachable-rejected",
    "unc-no-persistent-acls-rejected",
    "malformed-prefix-rejected",
    "relative-rejected",
    "parent-traversal-rejected",
)

G10_CASES = (
    "abi-file-rename-info",
    "abi-file-id-info",
    "abi-file-disposition-info-ex",
    "abi-overlapped",
    "abi-unicode-string",
    "abi-object-attributes",
    "abi-io-status-block",
    "abi-ntdll-signatures",
    "volume-serial-high-bit",
    "ntcreate-relative-rootdirectory",
    "ntcreate-no-createfilew-fallback",
    "ntstatus-create-collision",
    "ntstatus-lock-not-found",
    "ntstatus-disposition-not-found",
    "ntstatus-read-not-found",
    "ntstatus-mutating-access-denied",
    "ntstatus-read-access-denied",
    "ntstatus-lock-sharing",
    "ntstatus-other-sharing",
    "ntstatus-unsupported",
    "ntstatus-residual",
    "rename-error-existing",
    "rename-error-access-denied",
    "rename-error-unsupported",
    "rename-error-residual",
)


# Independent, literal transcription of the closed table in section 7.3.  The
# test must not obtain either the Win32 aliases or their Birth result from the
# product under test.
_NT_OPEN_PURPOSES = (
    "read_required",
    "lock_reader",
    "create_exclusive",
    "mutating_open",
    "disposition",
)
_NT_OPEN_EXPECTED = {
    "read_required": {
        80: "birth_provisioning_io_unavailable",
        183: "birth_provisioning_io_unavailable",
        2: "birth_provisioning_io_unavailable",
        3: "birth_provisioning_io_unavailable",
        5: "birth_provisioning_acl_unsafe",
        1314: "birth_provisioning_acl_unsafe",
        32: "birth_provisioning_io_unavailable",
        87: "birth_provisioning_atomic_install_unsupported",
        50: "birth_provisioning_atomic_install_unsupported",
        120: "birth_provisioning_atomic_install_unsupported",
        1117: "birth_provisioning_io_unavailable",
    },
    "lock_reader": {
        80: "birth_provisioning_io_unavailable",
        183: "birth_provisioning_io_unavailable",
        2: "birth_provisioning_lock_unavailable",
        3: "birth_provisioning_lock_unavailable",
        5: "birth_provisioning_acl_unsafe",
        1314: "birth_provisioning_acl_unsafe",
        32: "birth_provisioning_lock_unavailable",
        87: "birth_provisioning_atomic_install_unsupported",
        50: "birth_provisioning_atomic_install_unsupported",
        120: "birth_provisioning_atomic_install_unsupported",
        1117: "birth_provisioning_io_unavailable",
    },
    "create_exclusive": {
        80: "birth_provisioning_transaction_conflict",
        183: "birth_provisioning_transaction_conflict",
        2: "birth_provisioning_io_unavailable",
        3: "birth_provisioning_io_unavailable",
        5: "birth_provisioning_elevation_required",
        1314: "birth_provisioning_elevation_required",
        32: "birth_provisioning_io_unavailable",
        87: "birth_provisioning_atomic_install_unsupported",
        50: "birth_provisioning_atomic_install_unsupported",
        120: "birth_provisioning_atomic_install_unsupported",
        1117: "birth_provisioning_io_unavailable",
    },
    "mutating_open": {
        80: "birth_provisioning_io_unavailable",
        183: "birth_provisioning_io_unavailable",
        2: "birth_provisioning_io_unavailable",
        3: "birth_provisioning_io_unavailable",
        5: "birth_provisioning_elevation_required",
        1314: "birth_provisioning_elevation_required",
        32: "birth_provisioning_io_unavailable",
        87: "birth_provisioning_atomic_install_unsupported",
        50: "birth_provisioning_atomic_install_unsupported",
        120: "birth_provisioning_atomic_install_unsupported",
        1117: "birth_provisioning_io_unavailable",
    },
    "disposition": {
        80: "birth_provisioning_io_unavailable",
        183: "birth_provisioning_io_unavailable",
        2: "birth_provisioning_recovery_ambiguous",
        3: "birth_provisioning_recovery_ambiguous",
        5: "birth_provisioning_elevation_required",
        1314: "birth_provisioning_elevation_required",
        32: "birth_provisioning_io_unavailable",
        87: "birth_provisioning_atomic_install_unsupported",
        50: "birth_provisioning_atomic_install_unsupported",
        120: "birth_provisioning_atomic_install_unsupported",
        1117: "birth_provisioning_io_unavailable",
    },
}


# The fixed ten ntstatus node IDs partition every purpose/error pair.  Keeping
# this partition separate from _NT_OPEN_EXPECTED makes omission of a synonym a
# test-baseline error rather than silently inheriting a product mapping.
_NT_OPEN_CASE_PAIRS = {
    "ntstatus-create-collision": tuple(
        (purpose, error)
        for error in (80, 183)
        for purpose in _NT_OPEN_PURPOSES
    ),
    "ntstatus-lock-not-found": tuple(
        ("lock_reader", error) for error in (2, 3)
    ),
    "ntstatus-disposition-not-found": tuple(
        ("disposition", error) for error in (2, 3)
    ),
    "ntstatus-read-not-found": tuple(
        (purpose, error)
        for error in (2, 3)
        for purpose in ("read_required", "create_exclusive", "mutating_open")
    ),
    "ntstatus-mutating-access-denied": tuple(
        (purpose, error)
        for error in (5, 1314)
        for purpose in ("create_exclusive", "mutating_open", "disposition")
    ),
    "ntstatus-read-access-denied": tuple(
        (purpose, error)
        for error in (5, 1314)
        for purpose in ("read_required", "lock_reader")
    ),
    "ntstatus-lock-sharing": (("lock_reader", 32),),
    "ntstatus-other-sharing": tuple(
        (purpose, 32)
        for purpose in (
            "read_required",
            "create_exclusive",
            "mutating_open",
            "disposition",
        )
    ),
    "ntstatus-unsupported": tuple(
        (purpose, error)
        for error in (87, 50, 120)
        for purpose in _NT_OPEN_PURPOSES
    ),
    "ntstatus-residual": tuple(
        (purpose, 1117) for purpose in _NT_OPEN_PURPOSES
    ),
}


@pytest.fixture(scope="session")
def compiled_windows_abi(tmp_path_factory):
    """Compile the SDK declarations; product ctypes are not the ABI oracle."""
    support.require_windows()
    import json

    work = tmp_path_factory.mktemp("rm0008-win32-abi")
    source = work / "probe.c"
    executable = work / "probe.exe"
    source.write_text(
        r'''
#include <windows.h>
#include <winternl.h>
#include <stddef.h>
#include <stdio.h>
int main(void) {
  printf("{");
  printf("\"pointer\":%zu,", sizeof(void*));
  printf("\"rename.size\":%zu,\"rename.RootDirectory\":%zu,\"rename.FileNameLength\":%zu,\"rename.FileName\":%zu,", sizeof(FILE_RENAME_INFO), offsetof(FILE_RENAME_INFO, RootDirectory), offsetof(FILE_RENAME_INFO, FileNameLength), offsetof(FILE_RENAME_INFO, FileName));
  printf("\"id.size\":%zu,\"id.FileId\":%zu,", sizeof(FILE_ID_INFO), offsetof(FILE_ID_INFO, FileId));
  printf("\"disposition.size\":%zu,", sizeof(FILE_DISPOSITION_INFO_EX));
  printf("\"overlapped.size\":%zu,\"overlapped.hEvent\":%zu,", sizeof(OVERLAPPED), offsetof(OVERLAPPED, hEvent));
  printf("\"unicode.size\":%zu,\"unicode.Buffer\":%zu,", sizeof(UNICODE_STRING), offsetof(UNICODE_STRING, Buffer));
  printf("\"object.size\":%zu,\"object.RootDirectory\":%zu,\"object.ObjectName\":%zu,\"object.Attributes\":%zu,\"object.SecurityDescriptor\":%zu,\"object.SecurityQualityOfService\":%zu,", sizeof(OBJECT_ATTRIBUTES), offsetof(OBJECT_ATTRIBUTES, RootDirectory), offsetof(OBJECT_ATTRIBUTES, ObjectName), offsetof(OBJECT_ATTRIBUTES, Attributes), offsetof(OBJECT_ATTRIBUTES, SecurityDescriptor), offsetof(OBJECT_ATTRIBUTES, SecurityQualityOfService));
  printf("\"iosb.size\":%zu,\"iosb.Information\":%zu,\"ntstatus.size\":%zu}", sizeof(IO_STATUS_BLOCK), offsetof(IO_STATUS_BLOCK, Information), sizeof(NTSTATUS));
  return 0;
}
''',
        encoding="utf-8",
    )
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if not program_files_x86:
        raise AssertionError("ProgramFiles(x86) is absent on windows-2022")
    vswhere = Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    located = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
        capture_output=True,
        text=True,
    )
    installation = located.stdout.strip()
    if located.returncode != 0 or not installation:
        raise AssertionError("Visual C++ build tools are unavailable on windows-2022")
    vcvars = Path(installation) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.is_file():
        raise AssertionError("vcvars64.bat is absent from the selected Visual Studio installation")
    compile_script = work / "compile.cmd"
    compile_script.write_text(
        (
            "@echo off\n"
            f'@call "{vcvars}" >nul\n'
            "@if errorlevel 1 exit /b %errorlevel%\n"
            f'@cl.exe /nologo /W4 /WX /TC "{source.name}" '
            f'/Fo:"{source.with_suffix(".obj").name}" /Fe:"{executable.name}"\n'
            "@exit /b %errorlevel%\n"
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    # A command file avoids cmd.exe /s quote stripping around the Program Files
    # path while preserving the environment changes made by vcvars64.bat.
    compiled = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", compile_script.name],
        cwd=work,
        capture_output=True,
        text=True,
    )
    if compiled.returncode != 0:
        raise AssertionError(f"Win32 ABI probe compilation failed: {compiled.stdout} {compiled.stderr}")
    measured = subprocess.run([str(executable)], capture_output=True, text=True)
    if measured.returncode != 0:
        raise AssertionError(f"Win32 ABI probe failed: {measured.stderr}")
    values = json.loads(measured.stdout)
    if values["pointer"] != 8 or values["ntstatus.size"] != 4:
        raise AssertionError("runner is not the required Windows x64 ABI")
    return values


def _assert_stable_error(call, code: str) -> None:
    exc = support.require_code(call, code)
    if str(exc) != code:
        raise AssertionError("public error leaked native diagnostics")


def _normalized_windows_path(value: str | Path) -> str:
    """Normalize Win32 final-path text for comparison only, never reopening."""
    text = str(value).replace("/", "\\")
    if text.casefold().startswith("\\\\?\\unc\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.abspath(os.path.normpath(text)))


def _independent_handle_facts(handle: int) -> dict[str, object]:
    """Bind an already-open handle to its independent final path and FileID."""
    support.require_windows()
    import ctypes
    from ctypes import wintypes

    oracle = support.identity_oracle()
    final_name = oracle._KERNEL32.GetFinalPathNameByHandleW
    final_name.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    final_name.restype = wintypes.DWORD
    required = int(final_name(handle, None, 0, 0))
    if required <= 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = int(final_name(handle, buffer, len(buffer), 0))
    if written <= 0:
        raise ctypes.WinError(ctypes.get_last_error())
    if written >= len(buffer):
        raise AssertionError("GetFinalPathNameByHandleW result was truncated")
    facts = dict(support.handle_identity(handle))
    facts["final_path"] = _normalized_windows_path(buffer.value)
    return facts


def _independent_path_facts(path: Path, *, directory: bool) -> dict[str, object]:
    """Open the requested name independently and bind it to final path/FileID."""
    oracle = support.identity_oracle()
    handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    try:
        return _independent_handle_facts(handle)
    finally:
        oracle._close(handle)


def _local_roundtrip(
    root: Path,
    *,
    create_root: bool = True,
    require_exact_final_text: bool = True,
) -> tuple[dict[str, object], dict[str, object]]:
    sf = support.product()
    bindings = support.explicit_role_bindings(
        sf, (("payload.bin",), False, "birth_integrity_only")
    )
    payload = bytes(range(32))
    created = None
    with support.provisioner_session(
        root, create_root=create_root, role_bindings=bindings
    ) as active:
        created = support.create_file(
            active, ("payload.bin",), payload, "birth_integrity_only"
        )
        read = active.read_file(
            ("payload.bin",),
            maximum=len(payload),
            role=support.role(support.product(), "birth_integrity_only"),
        )
        if read != payload:
            raise AssertionError("path variant did not preserve payload")

    payload_path = root / "payload.bin"
    root_facts = _independent_path_facts(root, directory=True)
    payload_facts = _independent_path_facts(payload_path, directory=False)
    if payload_path.read_bytes() != payload:
        raise AssertionError("product roundtrip did not create the requested payload path")
    if (
        created is None
        or created.volume != payload_facts["volume"]
        or created.object_id != payload_facts["file_id"]
        or payload_facts["volume"] != root_facts["volume"]
        or payload_facts["directory"]
        or payload_facts["links"] != 1
        or payload_facts["delete_pending"]
    ):
        raise AssertionError("product roundtrip is not bound to the requested FileId128")
    if require_exact_final_text and (
        root_facts["final_path"] != _normalized_windows_path(root)
        or payload_facts["final_path"] != _normalized_windows_path(payload_path)
    ):
        raise AssertionError("product roundtrip resolved to a different final path")
    return root_facts, payload_facts


def _assert_net_share_absent(name: str) -> None:
    """Require NERR_NetNameNotFound from the local server before UNC use."""
    support.require_windows()
    import ctypes
    from ctypes import wintypes

    netapi = ctypes.WinDLL("netapi32", use_last_error=True)
    get_info = netapi.NetShareGetInfo
    get_info.argtypes = (
        wintypes.LPWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_info.restype = wintypes.DWORD
    free_buffer = netapi.NetApiBufferFree
    free_buffer.argtypes = (ctypes.c_void_p,)
    free_buffer.restype = wintypes.DWORD
    buffer = ctypes.c_void_p()
    status = int(get_info(None, name, 0, ctypes.byref(buffer)))
    try:
        if status != 2310:  # NERR_NetNameNotFound
            raise AssertionError(
                f"loopback share name was not independently absent: status={status}"
            )
    finally:
        if buffer.value and free_buffer(buffer):
            raise AssertionError("NetApiBufferFree failed after NetShareGetInfo")


def _exclusive_delete_probe(path: Path, *, directory: bool) -> None:
    """Prove no competing handle blocks DELETE before tearing down the share."""
    support.require_windows()
    import ctypes

    oracle = support.identity_oracle()
    flags = (0x02000000 if directory else 0x00000080) | 0x00200000
    handle = oracle._KERNEL32.CreateFileW(
        str(path),
        0x00010000 | 0x00000080,  # DELETE | FILE_READ_ATTRIBUTES
        0,
        None,
        3,
        flags,
        None,
    )
    if handle == oracle._INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        facts = support.handle_identity(handle)
        if facts["delete_pending"] or facts["directory"] is not directory:
            raise AssertionError("exclusive delete probe observed unsafe object state")
    finally:
        oracle._close(handle)


def _remove_unc_fixture(backing: Path, birth: Path, share: str) -> None:
    """Verify handle quiescence, remove the share, and remove its backing tree."""
    errors: list[BaseException] = []
    try:
        children = [(child, child.is_dir()) for child in birth.iterdir()]
    except BaseException as exc:
        children = []
        errors.append(exc)
    for child, is_directory in children:
        try:
            _exclusive_delete_probe(child, directory=is_directory)
        except BaseException as exc:
            errors.append(exc)
    for directory in (birth, backing):
        try:
            _exclusive_delete_probe(directory, directory=True)
        except BaseException as exc:
            errors.append(exc)

    removed = subprocess.run(
        ["net.exe", "share", share, "/delete", "/y"], capture_output=True
    )
    if removed.returncode != 0:
        errors.append(
            AssertionError(
                f"loopback share cleanup failed: returncode={removed.returncode}"
            )
        )
    try:
        _assert_net_share_absent(share)
    except BaseException as exc:
        errors.append(exc)

    for child, is_directory in children:
        try:
            if is_directory:
                child.rmdir()
            else:
                child.unlink()
        except BaseException as exc:
            errors.append(exc)
    for directory in (birth, backing):
        try:
            directory.rmdir()
        except BaseException as exc:
            errors.append(exc)
    if backing.exists():
        errors.append(
            AssertionError("loopback share backing directory survived cleanup")
        )
    if errors:
        raise AssertionError(
            "UNC cleanup did not prove quiescence and complete teardown: "
            + "; ".join(f"{type(exc).__name__}: {exc}" for exc in errors)
        ) from errors[0]


def _net_share(directory: Path):
    name = "RM8" + secrets.token_hex(5)
    _assert_net_share_absent(name)
    created = subprocess.run(
        ["net.exe", "share", f"{name}={directory}", "/GRANT:Everyone,FULL"],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        raise AssertionError(f"loopback share creation failed: {created.stderr}")
    return name


@pytest.mark.parametrize("case", G9_CASES, ids=G9_CASES)
def test_g9_windows_path_forms(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    if case in {"malformed-prefix-rejected", "relative-rejected", "parent-traversal-rejected"}:
        values = {
            "malformed-prefix-rejected": Path(r"\\?\UNC\server"),
            "relative-rejected": Path("relative-birth"),
            "parent-traversal-rejected": tmp_path / "safe" / ".." / "birth",
        }
        open_calls: list[str] = []

        def forbid_path_open(*args, **kwargs):
            open_calls.append("CreateFileW")
            raise AssertionError("invalid path reached CreateFileW")

        def forbid_relative_open(*args, **kwargs):
            open_calls.append("NtCreateFile")
            raise AssertionError("invalid path reached NtCreateFile")

        monkeypatch.setattr(sf._KERNEL32, "CreateFileW", forbid_path_open)
        monkeypatch.setattr(sf._NTDLL, "NtCreateFile", forbid_relative_open)
        before = support.windows_tree_snapshot(tmp_path)
        _assert_stable_error(
            lambda: sf._open_win_root(values[case]),
            "birth_provisioning_io_unavailable",
        )
        if open_calls:
            raise AssertionError("invalid root path reached a native open")
        if support.windows_tree_snapshot(tmp_path) != before:
            raise AssertionError("invalid root path mutated the filesystem")
        return
    if case == "unc-unreachable-rejected":
        # NetShareGetInfo proves the random name is absent on the local server;
        # loopback therefore cannot depend on DNS or outbound connectivity.
        share = f"RM8MISSING{secrets.token_hex(5)}"
        _assert_net_share_absent(share)
        path = Path(f"\\\\127.0.0.1\\{share}\\birth")
        _assert_stable_error(
            lambda: sf._open_win_root(path),
            "birth_provisioning_atomic_install_unsupported",
        )
        return
    if case in {"unc-loopback-positive", "unc-no-persistent-acls-rejected"}:
        backing = tmp_path / "share"
        backing.mkdir()
        sid = support.service_sid()
        support.apply_profile(backing, "integrity_only", directory=True, sid=sid)
        birth = backing / "birth"
        birth.mkdir()
        support.apply_profile(birth, "integrity_only", directory=True, sid=sid)
        share = _net_share(backing)
        unc = Path(f"\\\\127.0.0.1\\{share}\\birth")
        try:
            before_volume = support.volume_facts(unc)
            if (
                str(before_volume["filesystem"]).casefold() != "ntfs"
                or not int(before_volume["filesystem_flags"]) & 0x00000008
            ):
                raise AssertionError("loopback share is not backed by NTFS with persistent ACLs")
            if case == "unc-no-persistent-acls-rejected":
                original = sf._KERNEL32.GetVolumeInformationByHandleW
                import ctypes

                def no_acls(*args):
                    result = original(*args)
                    if result:
                        flags = ctypes.cast(args[5], ctypes.POINTER(sf.wintypes.DWORD))
                        flags.contents.value &= ~sf._FILE_PERSISTENT_ACLS
                    return result

                bindings = support.explicit_role_bindings(
                    sf, (("payload.bin",), False, "birth_integrity_only")
                )
                with support.provisioner_session(
                    unc, create_root=False, role_bindings=bindings
                ) as active:
                    before = support.windows_tree_snapshot(birth)
                    monkeypatch.setattr(
                        sf._KERNEL32, "GetVolumeInformationByHandleW", no_acls
                    )
                    _assert_stable_error(
                        lambda: support.create_file(
                            active,
                            ("payload.bin",),
                            bytes(range(32)),
                            "birth_integrity_only",
                        ),
                        "birth_provisioning_atomic_install_unsupported",
                    )
                    after = support.windows_tree_snapshot(birth)
                    if after != before:
                        raise AssertionError(
                            "UNC volume rejection changed namespace, bytes, ACL or metadata"
                        )
            else:
                _local_roundtrip(
                    unc,
                    create_root=False,
                    require_exact_final_text=False,
                )
                bindings = support.explicit_role_bindings(
                    sf,
                    (("payload.bin",), False, "birth_integrity_only"),
                    (("renamed.bin",), False, "birth_integrity_only"),
                )
                with support.session(
                    unc, create_root=False, role_bindings=bindings
                ) as active:
                    with support.exclusive(active):
                        def scalar(value) -> int:
                            return int(getattr(value, "value", value) or 0)

                        native_information = (
                            sf._KERNEL32.SetFileInformationByHandle
                        )
                        native_close = sf._KERNEL32.CloseHandle
                        native_calls = {"rename": 0, "disposition": 0}
                        disposition_handle: int | None = None
                        disposition_pending = False
                        disposition_closed = 0
                        payload_path = birth / "payload.bin"
                        payload_identity = support.identity(
                            payload_path, directory=False
                        )
                        before_rename = {
                            row[0]: row
                            for row in support.windows_tree_snapshot(birth)
                        }

                        def observe_information(*args):
                            nonlocal disposition_handle, disposition_pending
                            information_class = scalar(args[1])
                            handle = scalar(args[0])
                            if information_class == 3:
                                native_calls["rename"] += 1
                                if support.handle_identity(handle) != payload_identity:
                                    raise AssertionError(
                                        "UNC rename native handle lost FileId128"
                                    )
                            elif information_class == 21:
                                native_calls["disposition"] += 1
                                if disposition_handle is not None:
                                    raise AssertionError(
                                        "UNC disposition used more than one native handle"
                                    )
                                disposition_handle = handle
                                if support.handle_identity(handle) != payload_identity:
                                    raise AssertionError(
                                        "UNC disposition handle lost renamed identity"
                                    )
                            result = native_information(*args)
                            if result and information_class == 21:
                                if not support.handle_identity(handle)["delete_pending"]:
                                    raise AssertionError(
                                        "UNC disposition did not set DeletePending"
                                    )
                                disposition_pending = True
                            return result

                        def observe_close(handle):
                            nonlocal disposition_closed
                            if (
                                disposition_handle is not None
                                and scalar(handle) == disposition_handle
                            ):
                                if not disposition_pending:
                                    raise AssertionError(
                                        "UNC disposition handle closed before DeletePending"
                                    )
                                disposition_closed += 1
                            return native_close(handle)

                        monkeypatch.setattr(
                            sf._KERNEL32,
                            "SetFileInformationByHandle",
                            observe_information,
                        )
                        monkeypatch.setattr(
                            sf._KERNEL32, "CloseHandle", observe_close
                        )
                        renamed = active.rename_no_replace(
                            ("payload.bin",), ("renamed.bin",), directory=False
                        )
                        if (
                            renamed.volume != payload_identity["volume"]
                            or renamed.object_id != payload_identity["file_id"]
                        ):
                            raise AssertionError(
                                "UNC rename returned a different identity"
                            )
                        after_rename = {
                            row[0]: row
                            for row in support.windows_tree_snapshot(birth)
                        }
                        if set(after_rename) != {
                            ".",
                            "provisioning-v1.lock",
                            "renamed.bin",
                        }:
                            raise AssertionError(
                                "UNC rename produced an unexpected inventory"
                            )
                        if (
                            after_rename["provisioning-v1.lock"]
                            != before_rename["provisioning-v1.lock"]
                            or after_rename["renamed.bin"][1:]
                            != before_rename["payload.bin"][1:]
                        ):
                            raise AssertionError(
                                "UNC rename changed identity, ACL, bytes or metadata"
                            )
                        support.assert_profile(
                            birth / "renamed.bin",
                            "integrity_only",
                            directory=False,
                            sid=sid,
                        )
                        expectation = support.disposal_expectation(
                            sf,
                            unc / "renamed.bin",
                            ("renamed.bin",),
                            kind="regular_file",
                            role_name="birth_integrity_only",
                            disposal_class="complete_file",
                            payload=bytes(range(32)),
                        )
                        disposed = active.dispose_transaction_object(expectation)
                        if (
                            not disposed.removed
                            or disposed.identity.volume
                            != payload_identity["volume"]
                            or disposed.identity.object_id
                            != payload_identity["file_id"]
                            or native_calls != {"rename": 1, "disposition": 1}
                            or disposition_closed != 1
                        ):
                            raise AssertionError(
                                "UNC native rename/disposition lifecycle is incomplete"
                            )
                        final_snapshot = {
                            row[0]: row
                            for row in support.windows_tree_snapshot(birth)
                        }
                        if set(final_snapshot) != {
                            ".",
                            "provisioning-v1.lock",
                        } or (
                            final_snapshot["provisioning-v1.lock"]
                            != after_rename["provisioning-v1.lock"]
                        ):
                            raise AssertionError(
                                "UNC disposition changed unrelated inventory"
                            )
                if (backing / "birth" / "renamed.bin").exists():
                    raise AssertionError("UNC disposition did not complete")
            after_volume = support.volume_facts(unc)
            if (
                after_volume["volume"] != before_volume["volume"]
                or after_volume["file_id"] != before_volume["file_id"]
            ):
                raise AssertionError("UNC activity escaped the original volume/root identity")
        finally:
            _remove_unc_fixture(backing, birth, share)
        return
    root = tmp_path / "birth"
    if case == "local-long":
        root = tmp_path
        for index in range(12):
            root = root / (f"long-component-{index:02d}-" + "x" * 16)
        if len(str(root.resolve())) <= 260:
            raise AssertionError("long-path fixture is not longer than MAX_PATH")
        root.parent.mkdir(parents=True)
    elif case == "local-verbatim":
        root = Path("\\\\?\\" + str(root.resolve()))
    elif case == "local-case-variant":
        canonical = root
        canonical_root, canonical_payload = _local_roundtrip(canonical)
        variant = Path(str(canonical).swapcase())
        bindings = support.explicit_role_bindings(
            sf, (("payload.bin",), False, "birth_integrity_only")
        )
        with support.session(
            variant, create_root=False, role_bindings=bindings
        ) as active:
            with active.global_lock(exclusive=False, create=False):
                value = active.read_file(
                    ("PAYLOAD.BIN",), maximum=32, role=support.role(sf, "birth_integrity_only")
                )
            if value != bytes(range(32)):
                raise AssertionError("case variant did not resolve the same handle identity")
        variant_root = _independent_path_facts(variant, directory=True)
        variant_payload = _independent_path_facts(
            variant / "PAYLOAD.BIN", directory=False
        )
        if (
            variant_root["volume"] != canonical_root["volume"]
            or variant_root["file_id"] != canonical_root["file_id"]
            or variant_root["final_path"] != canonical_root["final_path"]
            or variant_payload["volume"] != canonical_payload["volume"]
            or variant_payload["file_id"] != canonical_payload["file_id"]
            or variant_payload["final_path"] != canonical_payload["final_path"]
            or (variant / "PAYLOAD.BIN").read_bytes() != bytes(range(32))
        ):
            raise AssertionError(
                "case variant did not resolve to the canonical root/payload FileID"
            )
        return
    root_facts, _payload_facts = _local_roundtrip(root)
    if case == "local-canonical":
        installer = importlib.import_module("install.birth_authority_provisioning")
        identity_resolver = installer._resolve_birth_service_identity_v1
        root_resolver = installer._resolve_birth_root_v1
        if tuple(inspect.signature(identity_resolver).parameters) != ():
            raise AssertionError("Windows installer identity resolver signature drifted")
        if tuple(inspect.signature(root_resolver).parameters) != ("root", "identity"):
            raise AssertionError("Windows installer root resolver signature drifted")
        identity = identity_resolver()
        sid = support.service_sid()
        if identity.posix_uid is not None or identity.windows_service_sid != sid:
            raise AssertionError("Windows installer did not resolve the real token SID")
        handles, canonical_root = root_resolver(root, identity)
        try:
            if (
                type(handles) is not tuple
                or not handles
                or not all(type(handle) is int and handle for handle in handles)
                or len(handles) != len(set(handles))
                or type(canonical_root) is not str
            ):
                raise AssertionError("Windows installer root resolver returned bad handles")
            handle_facts = _independent_handle_facts(handles[-1])
            if (
                handle_facts["volume"] != root_facts["volume"]
                or handle_facts["file_id"] != root_facts["file_id"]
                or handle_facts["final_path"] != root_facts["final_path"]
            ):
                raise AssertionError(
                    "Windows installer resolver opened another root path/FileId"
                )
            canonical_facts = _independent_path_facts(
                Path(canonical_root), directory=True
            )
            if (
                canonical_facts["volume"] != root_facts["volume"]
                or canonical_facts["file_id"] != root_facts["file_id"]
                or canonical_facts["final_path"] != root_facts["final_path"]
            ):
                raise AssertionError(
                    "Windows installer canonical path changed final path or identity"
                )
            support.assert_profile(
                root, "integrity_only", directory=True, sid=sid
            )
        finally:
            for handle in reversed(handles):
                sf._win_close(handle)


def _independent_abi():
    support.require_windows()
    import ctypes
    from ctypes import wintypes

    class FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", FILE_ID_128)]

    class FILE_DISPOSITION_INFO_EX(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD)]

    class OVERLAPPED_OFFSET(ctypes.Structure):
        _fields_ = [("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD)]

    class OVERLAPPED_UNION(ctypes.Union):
        _fields_ = [("offset", OVERLAPPED_OFFSET), ("Pointer", ctypes.c_void_p)]

    class OVERLAPPED(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("union", OVERLAPPED_UNION),
            ("hEvent", wintypes.HANDLE),
        ]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IOSB_UNION(ctypes.Union):
        _fields_ = [("Status", ctypes.c_long), ("Pointer", ctypes.c_void_p)]

    class IO_STATUS_BLOCK(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [("value", IOSB_UNION), ("Information", ctypes.c_size_t)]

    return {
        "rename": FILE_RENAME_INFO,
        "id": FILE_ID_INFO,
        "disposition": FILE_DISPOSITION_INFO_EX,
        "overlapped": OVERLAPPED,
        "unicode": UNICODE_STRING,
        "object": OBJECT_ATTRIBUTES,
        "iosb": IO_STATUS_BLOCK,
    }


def _assert_layout(product_type, independent_type, offsets: tuple[str, ...], compiled, prefix: str) -> None:
    import ctypes

    if ctypes.sizeof(product_type) != ctypes.sizeof(independent_type):
        raise AssertionError("ctypes structure size differs from independent ABI")
    if ctypes.sizeof(product_type) != compiled[f"{prefix}.size"]:
        raise AssertionError("ctypes structure size differs from compiled Windows SDK")
    for name in offsets:
        if getattr(product_type, name).offset != getattr(independent_type, name).offset:
            raise AssertionError(f"ctypes offset differs for {name}")
        key = f"{prefix}.{name}"
        if key in compiled and getattr(product_type, name).offset != compiled[key]:
            raise AssertionError(f"ctypes offset differs from compiled SDK for {name}")


def _assert_io_status_block_union(product_type) -> None:
    """Check the frozen anonymous Status|Pointer union, not only its size."""
    import ctypes

    union_fields = [
        (name, field_type)
        for name, field_type, *_ in product_type._fields_
        if isinstance(field_type, type) and issubclass(field_type, ctypes.Union)
    ]
    if len(union_fields) != 1:
        raise AssertionError("IO_STATUS_BLOCK does not contain one native union")
    union_name, union_type = union_fields[0]
    if union_name not in tuple(product_type._anonymous_):
        raise AssertionError("IO_STATUS_BLOCK Status|Pointer union is not anonymous")
    if tuple(union_type._fields_) != (
        ("Status", ctypes.c_long),
        ("Pointer", ctypes.c_void_p),
    ):
        raise AssertionError("IO_STATUS_BLOCK union fields or ctypes are incorrect")
    if (
        not hasattr(product_type, "Status")
        or not hasattr(product_type, "Pointer")
        or product_type.Status.offset != 0
        or product_type.Pointer.offset != 0
    ):
        raise AssertionError("IO_STATUS_BLOCK union aliases do not overlap at zero")


def _assert_nt_open_matrix_complete() -> None:
    expected = {
        (purpose, error)
        for purpose in _NT_OPEN_PURPOSES
        for error in (80, 183, 2, 3, 5, 1314, 32, 87, 50, 120, 1117)
    }
    flattened = [
        pair for pairs in _NT_OPEN_CASE_PAIRS.values() for pair in pairs
    ]
    if len(flattened) != len(set(flattened)) or set(flattened) != expected:
        raise AssertionError(
            "G10 NTSTATUS nodes do not cover every normative purpose/error pair once"
        )
    if any(
        set(per_purpose) != {error for _, error in expected}
        for per_purpose in _NT_OPEN_EXPECTED.values()
    ):
        raise AssertionError("independent NTSTATUS oracle omits a normative synonym")


def _expect_nt_error(
    sf,
    active,
    *,
    purpose: str,
    dos_error: int,
    expected: str,
    disposition_expectation,
    monkeypatch,
) -> None:
    ntdll = support.required(sf, "_NTDLL")
    native = ntdll.NtCreateFile
    convert = ntdll.RtlNtStatusToDosError
    native_status = -1073741823  # STATUS_UNSUCCESSFUL, signed 32-bit.
    native_calls = 0
    convert_calls = 0
    converted_statuses: list[int] = []

    def fail_native(*args):
        nonlocal native_calls
        native_calls += 1
        return native_status

    def convert_once(status):
        nonlocal convert_calls
        convert_calls += 1
        converted_statuses.append(int(getattr(status, "value", status)))
        return dos_error

    if purpose == "create_exclusive":
        call = lambda: support.create_file(
            active, ("new.bin",), b"x", "birth_confidential"
        )
    elif purpose == "lock_reader":
        def call():
            with active.global_lock(exclusive=False, create=False):
                pass
    elif purpose == "read_required":
        call = lambda: active.read_file(
            ("read.bin",),
            maximum=len(b"read"),
            role=support.role(sf, "birth_confidential"),
        )
    elif purpose == "mutating_open":
        call = lambda: active.rename_no_replace(
            ("source.bin",), ("renamed.bin",), directory=False
        )
    elif purpose == "disposition":
        call = lambda: active.dispose_transaction_object(
            disposition_expectation
        )
    else:
        raise AssertionError(f"unknown independent NT open purpose: {purpose}")

    with monkeypatch.context() as patch:
        patch.setattr(ntdll, "NtCreateFile", fail_native)
        patch.setattr(ntdll, "RtlNtStatusToDosError", convert_once)
        _assert_stable_error(call, expected)
    if (
        native_calls != 1
        or convert_calls != 1
        or converted_statuses != [native_status]
        or ntdll.NtCreateFile is not native
        or ntdll.RtlNtStatusToDosError is not convert
    ):
        raise AssertionError(
            "NTSTATUS was not passed through the native converter exactly once"
        )


_RENAME_ERROR_SUBCASES = {
    # FILE_EXISTS/ALREADY_EXISTS are exercised with both namespace states.
    # ACCESS_DENIED/SHARING_VIOLATION with a racing destination prove the
    # conflict branch is selected by reconciliation, not by the raw errno.
    "rename-error-existing": (
        (80, False, "birth_provisioning_transaction_conflict"),
        (183, False, "birth_provisioning_transaction_conflict"),
        (80, True, "birth_provisioning_transaction_conflict"),
        (183, True, "birth_provisioning_transaction_conflict"),
        (5, True, "birth_provisioning_transaction_conflict"),
        (32, True, "birth_provisioning_transaction_conflict"),
    ),
    # The same two ambiguous native failures have distinct absent-destination
    # results, forcing an actual reconciliation decision.
    "rename-error-access-denied": (
        (5, False, "birth_provisioning_elevation_required"),
        (32, False, "birth_provisioning_io_unavailable"),
    ),
    "rename-error-unsupported": (
        (50, False, "birth_provisioning_atomic_install_unsupported"),
        (17, False, "birth_provisioning_atomic_install_unsupported"),
        (87, False, "birth_provisioning_atomic_install_unsupported"),
    ),
    "rename-error-residual": (
        (2, False, "birth_provisioning_recovery_ambiguous"),
        (3, False, "birth_provisioning_recovery_ambiguous"),
        (1117, False, "birth_provisioning_io_unavailable"),
    ),
}


def _assert_rename_error_matrix_complete() -> None:
    absent = {
        (error, expected)
        for subcases in _RENAME_ERROR_SUBCASES.values()
        for error, install_destination, expected in subcases
        if not install_destination
    }
    if absent != {
        (80, "birth_provisioning_transaction_conflict"),
        (183, "birth_provisioning_transaction_conflict"),
        (5, "birth_provisioning_elevation_required"),
        (32, "birth_provisioning_io_unavailable"),
        (50, "birth_provisioning_atomic_install_unsupported"),
        (17, "birth_provisioning_atomic_install_unsupported"),
        (87, "birth_provisioning_atomic_install_unsupported"),
        (2, "birth_provisioning_recovery_ambiguous"),
        (3, "birth_provisioning_recovery_ambiguous"),
        (1117, "birth_provisioning_io_unavailable"),
    }:
        raise AssertionError("G10 rename matrix omits a normative native-error branch")
    installed = {
        (error, expected)
        for subcases in _RENAME_ERROR_SUBCASES.values()
        for error, install_destination, expected in subcases
        if install_destination
    }
    if installed != {
        (80, "birth_provisioning_transaction_conflict"),
        (183, "birth_provisioning_transaction_conflict"),
        (5, "birth_provisioning_transaction_conflict"),
        (32, "birth_provisioning_transaction_conflict"),
    }:
        raise AssertionError("G10 rename matrix omits a destination reconciliation branch")


@pytest.mark.parametrize("case", G10_CASES, ids=G10_CASES)
def test_g10_windows_native_contract(
    case: str, tmp_path: Path, monkeypatch, compiled_windows_abi
) -> None:
    sf = support.product()
    abi = _independent_abi()
    layout_cases = {
        "abi-file-rename-info": ("_FILE_RENAME_INFO", "rename", ("ReplaceIfExists", "RootDirectory", "FileNameLength", "FileName")),
        "abi-file-id-info": ("_FILE_ID_INFO", "id", ("VolumeSerialNumber", "FileId")),
        "abi-file-disposition-info-ex": ("_FILE_DISPOSITION_INFO_EX", "disposition", ("Flags",)),
        "abi-overlapped": ("_OVERLAPPED", "overlapped", ("Internal", "InternalHigh", "hEvent")),
        "abi-unicode-string": ("_UNICODE_STRING", "unicode", ("Length", "MaximumLength", "Buffer")),
        "abi-object-attributes": ("_OBJECT_ATTRIBUTES", "object", ("Length", "RootDirectory", "ObjectName", "Attributes", "SecurityDescriptor", "SecurityQualityOfService")),
        "abi-io-status-block": ("_IO_STATUS_BLOCK", "iosb", ("Information",)),
    }
    if case in layout_cases:
        product_name, independent_name, fields = layout_cases[case]
        _assert_layout(
            support.required(sf, product_name),
            abi[independent_name],
            fields,
            compiled_windows_abi,
            independent_name,
        )
        if case == "abi-io-status-block":
            _assert_io_status_block_union(support.required(sf, product_name))
        return
    if case == "abi-ntdll-signatures":
        import ctypes

        ntdll = support.required(sf, "_NTDLL")
        ntcreate = ntdll.NtCreateFile
        rtl = ntdll.RtlNtStatusToDosError
        expected_ntcreate_arguments = (
            ctypes.POINTER(sf.wintypes.HANDLE),
            sf.wintypes.ULONG,
            ctypes.POINTER(sf._OBJECT_ATTRIBUTES),
            ctypes.POINTER(sf._IO_STATUS_BLOCK),
            ctypes.POINTER(ctypes.c_longlong),
            sf.wintypes.ULONG,
            sf.wintypes.ULONG,
            sf.wintypes.ULONG,
            sf.wintypes.ULONG,
            ctypes.c_void_p,
            sf.wintypes.ULONG,
        )
        if (
            ntcreate.restype is not ctypes.c_long
            or tuple(ntcreate.argtypes or ()) != expected_ntcreate_arguments
        ):
            raise AssertionError("NtCreateFile ctypes ABI is not the closed 11-argument signature")
        if rtl.restype is not sf.wintypes.ULONG or tuple(rtl.argtypes or ()) != (ctypes.c_long,):
            raise AssertionError("RtlNtStatusToDosError ctypes ABI is incorrect")
        return
    if case == "volume-serial-high-bit":
        import ctypes

        root = tmp_path / "birth"
        bindings = support.explicit_role_bindings(
            sf, (("identity.bin",), False, "birth_confidential")
        )
        with support.provisioner_session(root, role_bindings=bindings) as active:
            support.create_file(
                active, ("identity.bin",), b"identity", "birth_confidential"
            )
            oracle = support.identity_oracle()
            handle = oracle._open_path(root / "identity.bin", directory=False)
            original = sf._KERNEL32.GetFileInformationByHandleEx

            def high_bit(handle_value, information_class, buffer, size):
                if information_class == 18:
                    value = ctypes.cast(
                        buffer, ctypes.POINTER(sf._FILE_ID_INFO)
                    ).contents
                    value.VolumeSerialNumber = 0x8000000000000001
                    for index in range(16):
                        value.FileId.Identifier[index] = index
                    return 1
                return original(handle_value, information_class, buffer, size)

            monkeypatch.setattr(
                sf._KERNEL32, "GetFileInformationByHandleEx", high_bit
            )
            try:
                identity = sf._win_info(handle)[0]
            finally:
                oracle._close(handle)
        if identity.volume != "8000000000000001" or identity.object_id != bytes(range(16)).hex():
            raise AssertionError("product signed or truncated volume/FileId information")
        return
    if case in {"ntcreate-relative-rootdirectory", "ntcreate-no-createfilew-fallback"}:
        import ctypes

        def scalar(value) -> int:
            return int(getattr(value, "value", value) or 0)

        root = tmp_path / "birth"
        bindings = support.explicit_role_bindings(
            sf,
            (("relative-confidential.bin",), False, "birth_confidential"),
            (("relative-confidential-dir",), True, "birth_confidential"),
            (("relative-integrity.bin",), False, "birth_integrity_only"),
            (("relative-integrity-dir",), True, "birth_integrity_only"),
        )
        calls: list[dict[str, object]] = []
        active_generations: dict[int, dict[str, object]] = {}
        retired_generations: dict[int, list[int]] = {}
        with support.provisioner_session(root, role_bindings=bindings) as active:
            ntdll = support.required(sf, "_NTDLL")
            original_nt = ntdll.NtCreateFile
            original_security = sf._ADVAPI32.SetSecurityInfo
            original_write = sf._win_write_all
            original_flush = sf._KERNEL32.FlushFileBuffers
            original_read = sf._KERNEL32.ReadFile
            original_close = sf._KERNEL32.CloseHandle
            original_status_to_error = ntdll.RtlNtStatusToDosError
            positive_status_injected = False

            def generation(handle) -> dict[str, object]:
                value = scalar(handle)
                if value not in active_generations:
                    raise AssertionError("creation operation used a closed or reopened handle")
                return active_generations[value]

            def record(*args):
                nonlocal positive_status_injected
                object_attributes = ctypes.cast(
                    args[2], ctypes.POINTER(sf._OBJECT_ATTRIBUTES)
                ).contents
                if not object_attributes.ObjectName:
                    raise AssertionError("NtCreateFile received no relative ObjectName")
                object_name = object_attributes.ObjectName.contents
                name = ctypes.wstring_at(
                    object_name.Buffer, int(object_name.Length) // 2
                )
                create_options = scalar(args[8])
                support.assert_security_descriptor_profile(
                    object_attributes.SecurityDescriptor,
                    (
                        "integrity_only"
                        if name.startswith("relative-integrity")
                        else "confidential"
                    ),
                    directory=bool(create_options & 0x00000001),
                    sid=support.service_sid(),
                )
                call = (
                    {
                        "output_handle": bool(args[0]),
                        "desired_access": scalar(args[1]),
                        "object_length": scalar(object_attributes.Length),
                        "root_handle": scalar(object_attributes.RootDirectory),
                        "name": name,
                        "name_length": scalar(object_name.Length),
                        "name_maximum": scalar(object_name.MaximumLength),
                        "attributes": scalar(object_attributes.Attributes),
                        "security_descriptor": bool(
                            object_attributes.SecurityDescriptor
                        ),
                        "security_qos": bool(
                            object_attributes.SecurityQualityOfService
                        ),
                        "iosb": bool(args[3]),
                        "allocation_size": bool(args[4]),
                        "file_attributes": scalar(args[5]),
                        "share_access": scalar(args[6]),
                        "create_disposition": scalar(args[7]),
                        "create_options": create_options,
                        "ea_buffer": bool(args[9]),
                        "ea_length": scalar(args[10]),
                    }
                )
                calls.append(call)
                result = original_nt(*args)
                if int(result) >= 0:
                    created_handle = int(
                        ctypes.cast(
                            args[0], ctypes.POINTER(ctypes.c_void_p)
                        ).contents.value
                        or 0
                    )
                    if not created_handle or created_handle in active_generations:
                        raise AssertionError("NtCreateFile output handle is invalid or still active")
                    call["handle"] = created_handle
                    call["generation"] = len(calls)
                    call["events"] = ["create"]
                    call["close_count"] = 0
                    active_generations[created_handle] = call
                    if (
                        case == "ntcreate-relative-rootdirectory"
                        and not positive_status_injected
                    ):
                        # Informational NTSTATUS (severity 01) is NT_SUCCESS even
                        # though it is nonzero.  The real syscall and its output
                        # handle have already completed, so the product must
                        # continue the genuine creation lifecycle.
                        result = 0x40000000
                        call["reported_status"] = result
                        positive_status_injected = True
                return result

            def convert_failure_only(status):
                if scalar(status) >= 0:
                    raise AssertionError(
                        "RtlNtStatusToDosError received successful NTSTATUS"
                    )
                return original_status_to_error(status)

            def security(*args):
                handle = scalar(args[0])
                active = generation(handle)
                support.assert_set_security_info_call(
                    args, expected_handle=handle
                )
                active["events"].append("acl")
                return original_security(*args)

            def write(handle, payload):
                active = generation(handle)
                active["events"].append("write")
                return original_write(handle, payload)

            def flush(handle):
                active = generation(handle)
                active["events"].append("flush")
                return original_flush(handle)

            def read(handle, *args):
                active = generation(handle)
                active["events"].append("read")
                return original_read(handle, *args)

            def close(handle):
                value = scalar(handle)
                active = active_generations.get(value)
                if active is None:
                    if value in retired_generations:
                        raise AssertionError(
                            "CloseHandle repeated a retired creation-handle generation"
                        )
                    return original_close(handle)
                events = active["events"]
                name = active["name"]
                if name.endswith(".bin"):
                    if events[:4] != ["create", "acl", "write", "flush"] or not all(
                        event == "read" for event in events[4:]
                    ) or len(events) < 5:
                        raise AssertionError(
                            f"file creation handle closed too early: {events!r}"
                        )
                elif events != ["create", "acl"]:
                    raise AssertionError(
                        f"directory creation handle closed too early: {events!r}"
                    )
                result = original_close(handle)
                if not result:
                    raise ctypes.WinError(ctypes.get_last_error())
                active["close_count"] = int(active["close_count"]) + 1
                active["closed"] = True
                retired_generations.setdefault(value, []).append(
                    int(active["generation"])
                )
                del active_generations[value]
                return result

            monkeypatch.setattr(ntdll, "NtCreateFile", record)
            if case == "ntcreate-relative-rootdirectory":
                monkeypatch.setattr(
                    ntdll, "RtlNtStatusToDosError", convert_failure_only
                )
            monkeypatch.setattr(sf._ADVAPI32, "SetSecurityInfo", security)
            monkeypatch.setattr(sf, "_win_write_all", write)
            monkeypatch.setattr(sf._KERNEL32, "FlushFileBuffers", flush)
            monkeypatch.setattr(sf._KERNEL32, "ReadFile", read)
            monkeypatch.setattr(sf._KERNEL32, "CloseHandle", close)
            if case == "ntcreate-no-createfilew-fallback":
                original_create = sf._KERNEL32.CreateFileW
                monkeypatch.setattr(
                    sf._KERNEL32,
                    "CreateFileW",
                    lambda *args: (_ for _ in ()).throw(AssertionError("absolute descendant fallback")),
                )
            support.create_file(
                active,
                ("relative-confidential.bin",),
                b"relative",
                "birth_confidential",
            )
            support.create_directory(
                active, ("relative-confidential-dir",), "birth_confidential"
            )
            support.create_file(
                active,
                ("relative-integrity.bin",),
                b"relative",
                "birth_integrity_only",
            )
            support.create_directory(
                active, ("relative-integrity-dir",), "birth_integrity_only"
            )
            if len(calls) != 4 or len({call["name"] for call in calls}) != 4:
                raise AssertionError(
                    "both ACL profiles and object kinds did not each use NtCreateFile once"
                )
            if sum("handle" in call for call in calls) != 4:
                raise AssertionError("not every NtCreateFile returned a tracked handle")
            if case == "ntcreate-relative-rootdirectory" and (
                not positive_status_injected
                or sum("reported_status" in call for call in calls) != 1
            ):
                raise AssertionError(
                    "positive informational NTSTATUS was not exercised exactly once"
                )
            for call in calls:
                name = call["name"]
                observed_events = call["events"]
                valid = (
                    observed_events[:4] == ["create", "acl", "write", "flush"]
                    and len(observed_events) >= 5
                    and all(event == "read" for event in observed_events[4:])
                    if name.endswith(".bin")
                    else observed_events == ["create", "acl"]
                )
                if not valid:
                    raise AssertionError(
                        f"creation handle sequence for {name} was {observed_events!r}"
                    )
            calls_by_name = {call["name"]: call for call in calls}
            expected_per_name = {
                "relative-confidential.bin": {
                    "desired_access": 0x001F0083,
                    "create_options": (
                        0x00200000 | 0x00000020 | 0x00000040 | 0x00000002
                    ),
                },
                "relative-integrity.bin": {
                    "desired_access": 0x001F0083,
                    "create_options": (
                        0x00200000 | 0x00000020 | 0x00000040 | 0x00000002
                    ),
                },
                "relative-confidential-dir": {
                    "desired_access": 0x001F00A1,
                    "create_options": (
                        0x00200000 | 0x00000020 | 0x00000001 | 0x00000002
                    ),
                },
                "relative-integrity-dir": {
                    "desired_access": 0x001F00A1,
                    "create_options": (
                        0x00200000 | 0x00000020 | 0x00000001 | 0x00000002
                    ),
                },
            }
            for name, per_name in expected_per_name.items():
                call = calls_by_name.get(name)
                if call is None:
                    raise AssertionError(f"NtCreateFile did not receive relative component {name}")
                encoded_length = len(name.encode("utf-16-le"))
                expected = {
                    "output_handle": True,
                    "object_length": ctypes.sizeof(sf._OBJECT_ATTRIBUTES),
                    "root_handle": scalar(active._root_handle),
                    "name": name,
                    "name_length": encoded_length,
                    "name_maximum": encoded_length,
                    "attributes": 0x00000040,
                    "security_descriptor": True,
                    "security_qos": False,
                    "iosb": True,
                    "allocation_size": False,
                    "file_attributes": 0x00000080,
                    "share_access": 0x00000001 | 0x00000002,
                    "create_disposition": 0x00000002,
                    "ea_buffer": False,
                    "ea_length": 0,
                    **per_name,
                }
                for field, value in expected.items():
                    if call[field] != value:
                        raise AssertionError(
                            f"NtCreateFile {name} field {field} was {call[field]!r}, expected {value!r}"
                        )
                if call["share_access"] & 0x00000004:
                    raise AssertionError("exclusive create granted FILE_SHARE_DELETE")
                if call["create_options"] & 0x00001000:
                    raise AssertionError("exclusive create used FILE_DELETE_ON_CLOSE")
        if active_generations:
            raise AssertionError(
                f"creation handles survived session close: {sorted(active_generations)}"
            )
        if any(
            call.get("handle")
            and (call.get("closed") is not True or call.get("close_count") != 1)
            for call in calls
        ):
            raise AssertionError(
                "not every NtCreateFile creation generation was closed exactly once"
            )
        return
    if case.startswith("ntstatus-"):
        _assert_nt_open_matrix_complete()
        root = tmp_path / "birth"
        sentinel_bindings = support.explicit_role_bindings(
            sf,
            (("new.bin",), False, "birth_confidential"),
            (("read.bin",), False, "birth_confidential"),
            (("source.bin",), False, "birth_confidential"),
            (("renamed.bin",), False, "birth_confidential"),
            (("disposable.bin",), False, "birth_confidential"),
        )
        disposable_payload = b"dispose"
        with support.provisioner_session(
            root, role_bindings=sentinel_bindings
        ) as setup:
            support.create_file(
                setup, ("read.bin",), b"read", "birth_confidential"
            )
            support.create_file(
                setup, ("source.bin",), b"source", "birth_confidential"
            )
            support.create_file(
                setup,
                ("disposable.bin",),
                disposable_payload,
                "birth_confidential",
            )
        disposition_expectation = support.disposal_expectation(
            sf,
            root / "disposable.bin",
            ("disposable.bin",),
            kind="regular_file",
            role_name="birth_confidential",
            disposal_class="complete_file",
            payload=disposable_payload,
        )
        with support.session(
            root, create_root=False, role_bindings=sentinel_bindings
        ) as active:
            for purpose, dos_error in _NT_OPEN_CASE_PAIRS[case]:
                before = support.windows_tree_snapshot(root)
                expected = _NT_OPEN_EXPECTED[purpose][dos_error]
                if purpose == "lock_reader":
                    # This purpose deliberately intercepts the lock open itself.
                    _expect_nt_error(
                        sf,
                        active,
                        purpose=purpose,
                        dos_error=dos_error,
                        expected=expected,
                        disposition_expectation=disposition_expectation,
                        monkeypatch=monkeypatch,
                    )
                else:
                    with support.exclusive(active):
                        _expect_nt_error(
                            sf,
                            active,
                            purpose=purpose,
                            dos_error=dos_error,
                            expected=expected,
                            disposition_expectation=disposition_expectation,
                            monkeypatch=monkeypatch,
                        )
                if support.windows_tree_snapshot(root) != before:
                    raise AssertionError(
                        f"NTSTATUS {dos_error} changed state for purpose {purpose}"
                    )
        return
    _assert_rename_error_matrix_complete()
    root = tmp_path / "birth"
    subcases = _RENAME_ERROR_SUBCASES[case]
    names = tuple(
        (f"rename-source-{index}.bin", f"rename-destination-{index}.bin")
        for index in range(len(subcases))
    )
    rename_bindings = support.explicit_role_bindings(
        sf,
        *tuple(
            binding
            for source_name, destination_name in names
            for binding in (
                ((source_name,), False, "birth_confidential"),
                ((destination_name,), False, "birth_confidential"),
            )
        ),
    )
    sid = support.service_sid()
    with support.provisioner_session(
        root, role_bindings=rename_bindings
    ) as active:
        for source_name, _ in names:
            support.create_file(
                active, (source_name,), b"source", "birth_confidential"
            )
        native = sf._KERNEL32.SetFileInformationByHandle
        for (
            index,
            (dos_error, install_destination, expected),
        ) in enumerate(subcases):
            source_name, destination_name = names[index]
            source_path = root / source_name
            destination_path = root / destination_name
            source_identity = support.identity(source_path, directory=False)
            before = {
                row[0]: row for row in support.windows_tree_snapshot(root)
            }
            calls = 0
            raced_destination_identity: dict[str, object] | None = None
            raced_destination_row: tuple[object, ...] | None = None

            def fail(*args):
                nonlocal calls, raced_destination_identity, raced_destination_row
                calls += 1
                if int(getattr(args[1], "value", args[1])) != 3:
                    raise AssertionError("rename error probe intercepted the wrong class")
                if support.handle_identity(args[0]) != source_identity:
                    raise AssertionError("rename error probe received the wrong source handle")
                if install_destination:
                    if destination_path.exists():
                        raise AssertionError("racing destination existed before the barrier")
                    support.create_profiled(
                        destination_path,
                        "confidential",
                        directory=False,
                        sid=sid,
                        payload=b"racing-destination",
                    )
                    raced_destination_identity = support.identity(
                        destination_path, directory=False
                    )
                    raced_destination_row = next(
                        row
                        for row in support.windows_tree_snapshot(root)
                        if row[0] == destination_name
                    )
                sf._KERNEL32.SetLastError(dos_error)
                return 0

            with monkeypatch.context() as patch:
                patch.setattr(
                    sf._KERNEL32, "SetFileInformationByHandle", fail
                )
                _assert_stable_error(
                    lambda: active.rename_no_replace(
                        (source_name,), (destination_name,), directory=False
                    ),
                    expected,
                )
            if calls != 1 or sf._KERNEL32.SetFileInformationByHandle is not native:
                raise AssertionError("rename native error was not injected exactly once")
            after = {
                row[0]: row for row in support.windows_tree_snapshot(root)
            }
            if install_destination:
                if raced_destination_identity is None or raced_destination_row is None:
                    raise AssertionError("destination race was not installed")
                expected_names = set(before) | {destination_name}
                unchanged_entries = all(
                    (
                        after[name][:4] == row[:4]
                        and after[name][5:] == row[5:]
                    )
                    if name == "."
                    else after[name] == row
                    for name, row in before.items()
                )
                if set(after) != expected_names or not unchanged_entries:
                    raise AssertionError(
                        "rename reconciliation changed source or unrelated inventory"
                    )
                destination_after = support.identity(
                    destination_path, directory=False
                )
                if (
                    destination_after != raced_destination_identity
                    or after[destination_name] != raced_destination_row
                    or destination_path.read_bytes() != b"racing-destination"
                ):
                    raise AssertionError(
                        "rename reconciliation did not preserve the racing destination"
                    )
                support.assert_profile(
                    destination_path,
                    "confidential",
                    directory=False,
                    sid=sid,
                )
            elif after != before:
                raise AssertionError(
                    "rename native failure changed an absent-destination namespace"
                )
            if (
                support.identity(source_path, directory=False) != source_identity
                or source_path.read_bytes() != b"source"
            ):
                raise AssertionError("rename native failure changed source identity or bytes")


@pytest.mark.parametrize("case", ("all-byte-values-roundtrip",), ids=("all-byte-values-roundtrip",))
def test_g11_windows_binary_roundtrip(case: str, tmp_path: Path) -> None:
    payload = bytes(range(256))
    root = tmp_path / "birth"
    sf = support.product()
    bindings = support.explicit_role_bindings(
        sf, (("all-bytes.bin",), False, "birth_confidential")
    )
    with support.provisioner_session(root, role_bindings=bindings) as active:
        support.create_file(active, ("all-bytes.bin",), payload, "birth_confidential")
        read = active.read_file(
            ("all-bytes.bin",),
            maximum=256,
            role=support.role(support.product(), "birth_confidential"),
        )
        if read != payload or (root / "all-bytes.bin").read_bytes() != payload:
            raise AssertionError("binary I/O changed a byte value")
