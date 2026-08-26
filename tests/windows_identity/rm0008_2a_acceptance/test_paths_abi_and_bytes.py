"""Windows path, native ABI/status, rename mapping and binary I/O cells."""
from __future__ import annotations

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


def _local_roundtrip(root: Path) -> None:
    with support.session(root) as active:
        payload = bytes(range(32))
        support.create_file(active, ("payload.bin",), payload, "birth_integrity_only")
        read = active.read_file(
            ("payload.bin",),
            maximum=len(payload),
            role=support.role(support.product(), "birth_integrity_only"),
        )
        if read != payload:
            raise AssertionError("path variant did not preserve payload")


def _net_share(directory: Path):
    name = "RM8" + secrets.token_hex(5)
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
        _assert_stable_error(
            lambda: sf._open_win_root(values[case]),
            "birth_provisioning_io_unavailable",
        )
        return
    if case == "unc-unreachable-rejected":
        path = Path(r"\\rm0008.invalid\never-published\birth")
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
        share = _net_share(backing)
        unc = Path(f"\\\\localhost\\{share}\\birth")
        try:
            if case == "unc-no-persistent-acls-rejected":
                original = sf._KERNEL32.GetVolumeInformationByHandleW
                import ctypes

                def no_acls(*args):
                    result = original(*args)
                    if result:
                        flags = ctypes.cast(args[5], ctypes.POINTER(sf.wintypes.DWORD))
                        flags.contents.value &= ~sf._FILE_PERSISTENT_ACLS
                    return result

                monkeypatch.setattr(sf._KERNEL32, "GetVolumeInformationByHandleW", no_acls)
                before = tuple(backing.iterdir())
                _assert_stable_error(
                    lambda: _local_roundtrip(unc),
                    "birth_provisioning_atomic_install_unsupported",
                )
                if tuple(backing.iterdir()) != before:
                    raise AssertionError("UNC volume rejection changed inventory")
            else:
                _local_roundtrip(unc)
                with support.session(unc, create_root=False) as active:
                    with support.exclusive(active):
                        active.rename_no_replace(
                            ("payload.bin",), ("renamed.bin",), directory=False
                        )
                        sf = support.product()
                        expectation = support.disposal_expectation(
                            sf,
                            unc / "renamed.bin",
                            ("renamed.bin",),
                            kind="regular_file",
                            role_name="birth_integrity_only",
                            disposal_class="complete_file",
                            payload=bytes(range(32)),
                        )
                        active.dispose_transaction_object(expectation)
                if (backing / "birth" / "renamed.bin").exists():
                    raise AssertionError("UNC disposition did not complete")
        finally:
            removed = subprocess.run(
                ["net.exe", "share", share, "/delete", "/y"], capture_output=True
            )
            if removed.returncode != 0:
                raise AssertionError("loopback share cleanup failed")
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
        _local_roundtrip(canonical)
        variant = Path(str(canonical).swapcase())
        with support.session(variant, create_root=False) as active:
            value = active.read_file(
                ("PAYLOAD.BIN",), maximum=32, role=support.role(sf, "birth_integrity_only")
            )
            if value != bytes(range(32)):
                raise AssertionError("case variant did not resolve the same handle identity")
        return
    _local_roundtrip(root)


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


def _expect_nt_error(sf, active, case: str, monkeypatch) -> None:
    ntdll = support.required(sf, "_NTDLL")
    native = ntdll.NtCreateFile
    convert = ntdll.RtlNtStatusToDosError
    dos = {
        "ntstatus-create-collision": 80,
        "ntstatus-lock-not-found": 2,
        "ntstatus-disposition-not-found": 3,
        "ntstatus-read-not-found": 2,
        "ntstatus-mutating-access-denied": 5,
        "ntstatus-read-access-denied": 1314,
        "ntstatus-lock-sharing": 32,
        "ntstatus-other-sharing": 32,
        "ntstatus-unsupported": 50,
        "ntstatus-residual": 1117,
    }[case]
    native_calls = 0
    convert_calls = 0

    def fail_native(*args):
        nonlocal native_calls
        native_calls += 1
        return -1073741823

    def convert_once(status):
        nonlocal convert_calls
        convert_calls += 1
        return dos

    monkeypatch.setattr(ntdll, "NtCreateFile", fail_native)
    monkeypatch.setattr(ntdll, "RtlNtStatusToDosError", convert_once)
    if case == "ntstatus-create-collision":
        call = lambda: support.create_file(active, ("new.bin",), b"x", "birth_confidential")
        expected = "birth_provisioning_transaction_conflict"
    elif case == "ntstatus-lock-not-found" or case == "ntstatus-lock-sharing":
        def call():
            with active.global_lock(exclusive=False, create=False):
                pass

        expected = "birth_provisioning_lock_unavailable"
    elif case == "ntstatus-disposition-not-found":
        sf._DisposalExpectation
        call = lambda: active.dispose_transaction_object(
            sf._DisposalExpectation(
                components=("absent",),
                identity=sf._ObjectIdentity("0" * 16, "0" * 32),
                kind=sf._ObjectKind("regular_file"),
                role=sf._BirthObjectRole("birth_confidential"),
                disposal_class=sf._DisposalClass("complete_file"),
                links=1,
                expected_size=0,
                maximum_partial_size=None,
                content_sha256=support.digest(b""),
                inventory=None,
            )
        )
        expected = "birth_provisioning_recovery_ambiguous"
    elif case in {"ntstatus-read-not-found", "ntstatus-read-access-denied", "ntstatus-other-sharing"}:
        call = lambda: active.read_file(
            ("absent.bin",), maximum=1, role=sf._BirthObjectRole("birth_confidential")
        )
        expected = "birth_provisioning_acl_unsafe" if case == "ntstatus-read-access-denied" else "birth_provisioning_io_unavailable"
    else:
        call = lambda: support.create_file(active, ("new.bin",), b"x", "birth_confidential")
        expected = (
            "birth_provisioning_elevation_required"
            if case == "ntstatus-mutating-access-denied"
            else "birth_provisioning_atomic_install_unsupported"
            if case == "ntstatus-unsupported"
            else "birth_provisioning_io_unavailable"
        )
    _assert_stable_error(call, expected)
    if native_calls != 1 or convert_calls != 1:
        raise AssertionError("NTSTATUS was not converted exactly once")
    monkeypatch.setattr(ntdll, "NtCreateFile", native)
    monkeypatch.setattr(ntdll, "RtlNtStatusToDosError", convert)


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
        return
    if case == "abi-ntdll-signatures":
        import ctypes

        ntdll = support.required(sf, "_NTDLL")
        ntcreate = ntdll.NtCreateFile
        rtl = ntdll.RtlNtStatusToDosError
        if ntcreate.restype is not ctypes.c_long or len(ntcreate.argtypes or ()) != 11:
            raise AssertionError("NtCreateFile ctypes ABI is not the closed 11-argument signature")
        if rtl.restype is not sf.wintypes.ULONG or tuple(rtl.argtypes or ()) != (ctypes.c_long,):
            raise AssertionError("RtlNtStatusToDosError ctypes ABI is incorrect")
        return
    if case == "volume-serial-high-bit":
        import ctypes

        path = tmp_path / "identity.bin"
        path.write_bytes(b"identity")
        oracle = support.identity_oracle()
        handle = oracle._open_path(path, directory=False)
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
        root = tmp_path / "birth"
        with support.session(root) as active:
            ntdll = support.required(sf, "_NTDLL")
            original_nt = ntdll.NtCreateFile
            calls = []

            def record(*args):
                object_attributes = args[2].contents
                calls.append((int(object_attributes.RootDirectory), object_attributes.ObjectName.contents.Length))
                return original_nt(*args)

            monkeypatch.setattr(ntdll, "NtCreateFile", record)
            if case == "ntcreate-no-createfilew-fallback":
                original_create = sf._KERNEL32.CreateFileW
                monkeypatch.setattr(
                    sf._KERNEL32,
                    "CreateFileW",
                    lambda *args: (_ for _ in ()).throw(AssertionError("absolute descendant fallback")),
                )
            support.create_file(active, ("relative.bin",), b"relative", "birth_confidential")
            if not calls or not calls[0][0] or calls[0][1] != len("relative.bin".encode("utf-16-le")):
                raise AssertionError("NtCreateFile did not receive parent handle and one UTF-16 component")
        return
    if case.startswith("ntstatus-"):
        root = tmp_path / "birth"
        with support.session(root) as active:
            if case == "ntstatus-disposition-not-found":
                with support.exclusive(active):
                    _expect_nt_error(sf, active, case, monkeypatch)
            else:
                _expect_nt_error(sf, active, case, monkeypatch)
        return
    root = tmp_path / "birth"
    with support.session(root) as active:
        support.create_file(active, ("source",), b"source", "birth_confidential")
        native = sf._KERNEL32.SetFileInformationByHandle
        dos = {
            "rename-error-existing": 80,
            "rename-error-access-denied": 5,
            "rename-error-unsupported": 50,
            "rename-error-residual": 1117,
        }[case]
        calls = 0

        def fail(*args):
            nonlocal calls
            calls += 1
            sf._KERNEL32.SetLastError(dos)
            return 0

        monkeypatch.setattr(sf._KERNEL32, "SetFileInformationByHandle", fail)
        expected = {
            "rename-error-existing": "birth_provisioning_transaction_conflict",
            "rename-error-access-denied": "birth_provisioning_elevation_required",
            "rename-error-unsupported": "birth_provisioning_atomic_install_unsupported",
            "rename-error-residual": "birth_provisioning_io_unavailable",
        }[case]
        _assert_stable_error(
            lambda: active.rename_no_replace(("source",), ("destination",), directory=False),
            expected,
        )
        if calls != 1 or not (root / "source").exists() or (root / "destination").exists():
            raise AssertionError("rename error did not preserve namespace")


@pytest.mark.parametrize("case", ("all-byte-values-roundtrip",), ids=("all-byte-values-roundtrip",))
def test_g11_windows_binary_roundtrip(case: str, tmp_path: Path) -> None:
    payload = bytes(range(256))
    root = tmp_path / "birth"
    with support.session(root) as active:
        support.create_file(active, ("all-bytes.bin",), payload, "birth_confidential")
        read = active.read_file(
            ("all-bytes.bin",),
            maximum=256,
            role=support.role(support.product(), "birth_confidential"),
        )
        if read != payload or (root / "all-bytes.bin").read_bytes() != payload:
            raise AssertionError("binary I/O changed a byte value")
