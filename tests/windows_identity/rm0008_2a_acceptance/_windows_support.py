"""Lazy Windows helpers shared by the RM-0008 2A acceptance tests.

Nothing in this module imports a product module or constructs a Win32 DLL at
collection time.  This is intentional: Linux is required to collect the exact
Windows node inventory, while execution belongs to the Windows activities.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import os
import sys
from pathlib import Path
from typing import Iterator


REPOSITORY = Path(__file__).resolve().parents[3]


def worker_environment() -> dict[str, str]:
    """Closed environment for subprocess probes; CI credentials never cross."""
    names = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PATH",
        "PATHEXT",
        "PYTHONNOUSERSITE",
    )
    result = {name: os.environ[name] for name in names if name in os.environ}
    result["PYTHONNOUSERSITE"] = "1"
    return result


def require_windows() -> None:
    if os.name != "nt":
        raise AssertionError("this acceptance call requires Windows")


def product():
    require_windows()
    runtime = str(REPOSITORY / "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    return importlib.import_module("executor_birth_secure_fs")


def identity_oracle():
    require_windows()
    helpers = str(REPOSITORY / "tests" / "windows_identity")
    if helpers not in sys.path:
        sys.path.insert(0, helpers)
    return importlib.import_module("win32_identity_oracle")


def required(value: object, name: str):
    try:
        return getattr(value, name)
    except AttributeError as exc:
        raise AssertionError(f"required product symbol is absent: {name}") from exc


def require_code(call, code: str):
    sf = product()
    try:
        call()
    except sf.BirthSecureFSError as exc:
        if exc.code != code:
            raise AssertionError(f"expected {code}, received {exc.code}") from exc
        return exc
    raise AssertionError(f"expected {code}")


def service_sid() -> str:
    return identity_oracle().current_token_facts().user_sid


def token_privileges_snapshot() -> bytes:
    """Read TokenPrivileges independently, including its variable-size tail."""
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    token = wintypes.HANDLE()
    if not oracle._ADVAPI32.OpenProcessToken(
        oracle._KERNEL32.GetCurrentProcess(), oracle._TOKEN_QUERY, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return bytes(oracle._token_information(token.value, 3))
    finally:
        oracle._close(token.value)


def apply_profile(path: Path, profile: str, *, directory: bool, sid: str) -> None:
    oracle = identity_oracle()
    oracle.apply_profile(path, profile, sid, directory=directory)
    oracle.assert_exact_profile(path, profile, sid, directory=directory)


@contextlib.contextmanager
def session(
    root: Path,
    *,
    root_profile: str = "integrity_only",
    authenticated_sid: str | None = None,
    create_root: bool = True,
) -> Iterator[object]:
    """Build the frozen descriptor shape and adopt it exactly once.

    This test-only fixture deliberately targets the post-2A constructor.  It
    has no compatibility path for the prototype token or mutable handle list.
    """
    sf = product()
    sid = service_sid() if authenticated_sid is None else authenticated_sid
    if create_root:
        root.mkdir()
        apply_profile(root, root_profile, directory=True, sid=sid)
    else:
        assert_profile(root, root_profile, directory=True, sid=sid)
    handles, absolute = required(sf, "_open_win_root")(root)
    descriptor_type = required(sf, "_AuthenticatedRootDescriptor")
    descriptor = descriptor_type(
        tuple(handles),
        absolute,
        sf._PlatformIdentity(None, sid),
    )
    try:
        adopted = required(sf, "_adopt_authenticated_root")(descriptor)
    except BaseException:
        for handle in reversed(handles):
            try:
                sf._win_close(handle)
            except BaseException:
                pass
        raise
    try:
        with adopted as value:
            yield value
    finally:
        # The context owns every descriptor handle after successful adoption.
        pass


def role(sf, value: str):
    return required(sf, "_BirthObjectRole")(value)


def create_file(active, components: tuple[str, ...], payload: bytes, role_name: str):
    sf = product()
    return active.create_file_exclusive(
        components,
        payload,
        role=role(sf, role_name),
    )


def create_directory(active, components: tuple[str, ...], role_name: str):
    sf = product()
    return active.create_directory_exclusive(
        components,
        role=role(sf, role_name),
    )


def _identity_from_handle(handle: int):
    """Return an independently queried (volume, FileId128, links, flags)."""
    import ctypes
    from ctypes import wintypes

    class FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", FILE_ID_128)]

    class FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [
            ("AllocationSize", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("NumberOfLinks", wintypes.DWORD),
            ("DeletePending", wintypes.BOOLEAN),
            ("Directory", wintypes.BOOLEAN),
        ]

    oracle = identity_oracle()
    file_id = FILE_ID_INFO()
    standard = FILE_STANDARD_INFO()
    if not oracle._KERNEL32.GetFileInformationByHandleEx(
        handle, 18, ctypes.byref(file_id), ctypes.sizeof(file_id)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not oracle._KERNEL32.GetFileInformationByHandleEx(
        handle, 1, ctypes.byref(standard), ctypes.sizeof(standard)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "volume": f"{file_id.VolumeSerialNumber:016x}",
        "file_id": bytes(file_id.FileId.Identifier).hex(),
        "links": int(standard.NumberOfLinks),
        "size": int(standard.EndOfFile),
        "directory": bool(standard.Directory),
        "delete_pending": bool(standard.DeletePending),
    }


def handle_identity(handle: int):
    return _identity_from_handle(handle)


def identity(path: Path, *, directory: bool, open_reparse: bool = False):
    oracle = identity_oracle()
    if open_reparse:
        flags = (0x02000000 if directory else 0x00000080) | 0x00200000
        handle = oracle._KERNEL32.CreateFileW(
            str(path),
            oracle._READ_CONTROL,
            oracle._FILE_SHARE_READ | oracle._FILE_SHARE_WRITE | oracle._FILE_SHARE_DELETE,
            None,
            3,
            flags,
            None,
        )
        if handle == oracle._INVALID_HANDLE_VALUE:
            import ctypes

            raise ctypes.WinError(ctypes.get_last_error())
    else:
        handle = oracle._open_path(path, directory=directory)
    try:
        return _identity_from_handle(handle)
    finally:
        oracle._close(handle)


def reparse_tag(path: Path, *, directory: bool) -> int:
    require_windows()
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    flags = (0x02000000 if directory else 0x00000080) | 0x00200000
    handle = oracle._KERNEL32.CreateFileW(
        str(path),
        oracle._READ_CONTROL,
        oracle._FILE_SHARE_READ | oracle._FILE_SHARE_WRITE | oracle._FILE_SHARE_DELETE,
        None,
        3,
        flags,
        None,
    )
    if handle == oracle._INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FILE_ATTRIBUTE_TAG_INFO()
        if not oracle._KERNEL32.GetFileInformationByHandleEx(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(info.ReparseTag)
    finally:
        oracle._close(handle)


def assert_profile(path: Path, profile: str, *, directory: bool, sid: str) -> None:
    identity_oracle().assert_exact_profile(path, profile, sid, directory=directory)


def create_profiled(
    path: Path,
    profile: str,
    *,
    directory: bool,
    sid: str,
    payload: bytes = b"payload",
) -> None:
    if directory:
        path.mkdir()
    else:
        path.write_bytes(payload)
    apply_profile(path, profile, directory=directory, sid=sid)


def apply_sddl(
    path: Path, sddl: str, *, directory: bool, protected: bool = True
) -> None:
    """Apply a deliberately supplied descriptor through the independent oracle."""
    require_windows()
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    descriptor = ctypes.c_void_p()
    length = wintypes.DWORD()
    if not oracle._ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(length)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    handle = None
    try:
        owner = ctypes.c_void_p()
        owner_defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        dacl_present = wintypes.BOOL()
        dacl_defaulted = wintypes.BOOL()
        if not oracle._ADVAPI32.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not oracle._ADVAPI32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = oracle._open_path(
            path,
            oracle._READ_CONTROL | oracle._WRITE_DAC | oracle._WRITE_OWNER,
            directory=directory,
        )
        information = (
            oracle._OWNER_SECURITY_INFORMATION | oracle._DACL_SECURITY_INFORMATION
        )
        if protected:
            information |= oracle._PROTECTED_DACL_SECURITY_INFORMATION
        else:
            information |= 0x20000000  # UNPROTECTED_DACL_SECURITY_INFORMATION
        with oracle.enabled_restore_privilege():
            result = oracle._ADVAPI32.SetSecurityInfo(
                handle,
                oracle._SE_FILE_OBJECT,
                information,
                owner,
                None,
                dacl,
                None,
            )
        if result:
            raise OSError(result, "SetSecurityInfo")
    finally:
        if handle is not None:
            oracle._close(handle)
        if descriptor.value:
            oracle._KERNEL32.LocalFree(descriptor)


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def object_identity(sf, facts: dict[str, object]):
    return sf._ObjectIdentity(facts["volume"], facts["file_id"])


def disposal_expectation(
    sf,
    path: Path,
    components: tuple[str, ...],
    *,
    kind: str,
    role_name: str,
    disposal_class: str,
    payload: bytes | None = None,
    maximum_partial_size: int | None = None,
    inventory: tuple[object, ...] | None = None,
):
    directory = kind == "directory"
    facts = identity(path, directory=directory)
    expected_size = len(payload) if disposal_class == "complete_file" and payload is not None else None
    content_sha256 = digest(payload) if expected_size is not None else None
    return required(sf, "_DisposalExpectation")(
        components=components,
        identity=object_identity(sf, facts),
        kind=required(sf, "_ObjectKind")(kind),
        role=role(sf, role_name),
        disposal_class=required(sf, "_DisposalClass")(disposal_class),
        links=facts["links"],
        expected_size=expected_size,
        maximum_partial_size=maximum_partial_size,
        content_sha256=content_sha256,
        inventory=inventory,
    )


@contextlib.contextmanager
def exclusive(active):
    with active.global_lock(exclusive=True, create=True):
        yield


def get_named_entry(entries, name: str):
    for entry in entries:
        if entry.name == name:
            return entry
    raise AssertionError(f"missing inventory record: {name}")


def canonical_json(value: object) -> bytes:
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def profile_tree(root: Path, sid: str, private_names: set[str]) -> None:
    """Apply the distinct historical owner/current-user ACL contract."""
    oracle = identity_oracle()
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        private = bool(set(path.relative_to(root).parts) & private_names)
        read = oracle._DIRECTORY_READ_MASK if path.is_dir() else oracle._FILE_READ_MASK
        sddl = (
            f"O:{sid}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})"
            + ("" if private else f"(A;;0x{read:08x};;;AU)")
        )
        apply_sddl(path, sddl, directory=path.is_dir())
        assert_historical_profile(
            path, public=not private, directory=path.is_dir(), sid=sid
        )


def _canonical_sddl(sddl: str) -> str:
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    descriptor = ctypes.c_void_p()
    length = wintypes.DWORD()
    encoded = wintypes.LPWSTR()
    encoded_length = wintypes.DWORD()
    if not oracle._ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(length)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not oracle._ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            oracle._OWNER_SECURITY_INFORMATION | oracle._DACL_SECURITY_INFORMATION,
            ctypes.byref(encoded),
            ctypes.byref(encoded_length),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return encoded.value.casefold()
        finally:
            oracle._KERNEL32.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))
    finally:
        oracle._KERNEL32.LocalFree(descriptor)


def assert_historical_profile(
    path: Path, *, public: bool, directory: bool, sid: str
) -> None:
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    read = oracle._DIRECTORY_READ_MASK if directory else oracle._FILE_READ_MASK
    expected = (
        f"O:{sid}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})"
        + (f"(A;;0x{read:08x};;;AU)" if public else "")
    )
    handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    descriptor = ctypes.c_void_p()
    encoded = wintypes.LPWSTR()
    encoded_length = wintypes.DWORD()
    try:
        result = oracle._ADVAPI32.GetSecurityInfo(
            handle,
            oracle._SE_FILE_OBJECT,
            oracle._OWNER_SECURITY_INFORMATION | oracle._DACL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise OSError(result, "GetSecurityInfo")
        if not oracle._ADVAPI32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            oracle._OWNER_SECURITY_INFORMATION | oracle._DACL_SECURITY_INFORMATION,
            ctypes.byref(encoded),
            ctypes.byref(encoded_length),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if encoded.value.casefold() != _canonical_sddl(expected):
            raise AssertionError("historical owner/DACL differs from the closed profile")
    finally:
        if encoded:
            oracle._KERNEL32.LocalFree(ctypes.cast(encoded, ctypes.c_void_p))
        if descriptor.value:
            oracle._KERNEL32.LocalFree(descriptor)
        oracle._close(handle)


def provision_authorities(root: Path, sid: str) -> dict[str, tuple[str, ...]]:
    """Create the closed, valid loader fixture then apply independent ACLs."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    runtime = str(REPOSITORY / "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    keystore = importlib.import_module("executor_birth_keystore")
    review = importlib.import_module("executor_birth_semantic_review")
    set_id = "0" * 64
    authority_set = root / "authority-sets" / set_id
    admission = authority_set / "admission"
    approval = authority_set / "approval"
    semantic = authority_set / "semantic"
    for directory in (
        root / "authority-sets",
        authority_set,
        admission,
        admission / "private",
        admission / "public",
        approval,
        semantic,
        semantic / "public",
        semantic / "evidence",
    ):
        directory.mkdir()
    private = Ed25519PrivateKey.generate()
    public = keystore.raw_public_key(private.public_key())
    key_id = keystore.birth_key_id(public)
    (admission / "birth-keystore.lock").write_bytes(b"0")
    (admission / "private" / f"{key_id}.key").write_bytes(
        private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )
    (admission / "public" / f"{key_id}.pub").write_bytes(public)
    (admission / "keystore.json").write_bytes(
        canonical_json(
            {
                "active_key_id": key_id,
                "config_revision": 1,
                "keys": [
                    {
                        "key_id": key_id,
                        "public_file": f"public/{key_id}.pub",
                        "status": "active",
                    }
                ],
                "private_file": f"private/{key_id}.key",
                "schema_version": 1,
            }
        )
    )
    approval_public = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    (approval / "authority.json").write_bytes(
        canonical_json(
            {
                "actors": {"operator": {"key_ids": ["operator-key"], "scopes": ["birth"]}},
                "keys": {"operator-key": base64.b64encode(approval_public).decode("ascii")},
                "revision": 1,
                "schema_version": 1,
            }
        )
    )
    semantic_public = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    (semantic / "public" / "review.pub").write_bytes(semantic_public)
    kinds = sorted(item.value for item in review.IndependentEvidenceKind)
    (semantic / "authority.json").write_bytes(
        canonical_json(
            {
                "evidence_dir": "evidence",
                "owners": {kind: ["independent-owner"] for kind in kinds},
                "verifiers": {"review-key": {"path": "public/review.pub", "status": "active"}},
                "versions": {kind: ["v1"] for kind in kinds},
            }
        )
    )
    profile_tree(root, sid, {"admission", "private"})
    return {
        "keystore": ("authority-sets", set_id, "admission"),
        "approval": ("authority-sets", set_id, "approval", "authority.json"),
        "semantic_authority": ("authority-sets", set_id, "semantic", "authority.json"),
        "semantic_public": ("authority-sets", set_id, "semantic", "public"),
        "semantic_evidence": ("authority-sets", set_id, "semantic", "evidence"),
    }


def terminate_process(process, *, expected_exit: int = 0xEE) -> None:
    """Terminate, wait, and verify the exact child exit code."""
    require_windows()
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    handle = wintypes.HANDLE(int(process._handle))
    if not kernel.TerminateProcess(handle, expected_exit):
        raise ctypes.WinError(ctypes.get_last_error())
    if kernel.WaitForSingleObject(handle, 30_000) != 0:
        raise AssertionError("terminated child did not become signaled")
    code = wintypes.DWORD()
    if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
        raise ctypes.WinError(ctypes.get_last_error())
    if code.value != expected_exit:
        raise AssertionError(f"unexpected child exit code {code.value}")
    process.returncode = expected_exit
