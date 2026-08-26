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
import stat
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
        oracle._close_handle(token.value)


def privilege_attributes(snapshot: bytes, privilege_name: str) -> int:
    """Resolve one LUID independently and return its exact token attributes."""
    require_windows()
    import ctypes

    oracle = identity_oracle()
    expected = oracle._LUID()
    if not oracle._ADVAPI32.LookupPrivilegeValueW(
        None, privilege_name, ctypes.byref(expected)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    offset = oracle._TOKEN_PRIVILEGES.Privileges.offset
    item_type = oracle._LUID_AND_ATTRIBUTES
    if len(snapshot) < offset:
        raise AssertionError("TokenPrivileges snapshot is truncated")
    count = oracle.wintypes.DWORD.from_buffer_copy(snapshot).value
    width = ctypes.sizeof(item_type)
    if offset + count * width > len(snapshot):
        raise AssertionError("TokenPrivileges variable tail is truncated")
    for index in range(count):
        item = item_type.from_buffer_copy(snapshot, offset + index * width)
        if (
            item.Luid.LowPart == expected.LowPart
            and item.Luid.HighPart == expected.HighPart
        ):
            return int(item.Attributes)
    raise AssertionError(f"token does not contain {privilege_name}")


def apply_profile(path: Path, profile: str, *, directory: bool, sid: str) -> None:
    oracle = identity_oracle()
    oracle.apply_profile(path, profile, sid, directory=directory)
    oracle.assert_exact_profile(path, profile, sid, directory=directory)


def assert_security_descriptor_profile(
    descriptor_pointer,
    profile: str,
    *,
    directory: bool,
    sid: str,
) -> None:
    """Decode the pre-create descriptor without using product ACL helpers."""
    require_windows()
    import ctypes

    oracle = identity_oracle()
    descriptor = ctypes.c_void_p(
        int(getattr(descriptor_pointer, "value", descriptor_pointer) or 0)
    )
    if not descriptor.value:
        raise AssertionError("NtCreateFile security descriptor is absent")
    owner = ctypes.c_void_p()
    owner_defaulted = oracle.wintypes.BOOL()
    dacl_present = oracle.wintypes.BOOL()
    dacl = ctypes.c_void_p()
    dacl_defaulted = oracle.wintypes.BOOL()
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
    if (
        owner_defaulted
        or not owner.value
        or not dacl_present
        or dacl_defaulted
        or not dacl.value
    ):
        raise AssertionError("pre-create owner/DACL is absent or defaulted")
    control = oracle.wintypes.WORD()
    revision = oracle.wintypes.DWORD()
    if not oracle._ADVAPI32.GetSecurityDescriptorControl(
        descriptor, ctypes.byref(control), ctypes.byref(revision)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not control.value & oracle._SE_DACL_PROTECTED:
        raise AssertionError("pre-create DACL is not protected")
    acl_information = oracle._ACL_SIZE_INFORMATION()
    if not oracle._ADVAPI32.GetAclInformation(
        dacl,
        ctypes.byref(acl_information),
        ctypes.sizeof(acl_information),
        oracle._ACL_SIZE_INFORMATION_CLASS,
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    expected_sids = ["S-1-5-18", "S-1-5-32-544", sid]
    read_mask = oracle._DIRECTORY_READ_MASK if directory else oracle._FILE_READ_MASK
    expected_masks = [oracle._FILE_ALL_ACCESS, oracle._FILE_ALL_ACCESS, read_mask]
    if profile == "integrity_only":
        expected_sids.append("S-1-5-11")
        expected_masks.append(read_mask)
    elif profile != "confidential":
        raise AssertionError("unknown ACL profile")
    if acl_information.AceCount != len(expected_sids):
        raise AssertionError("pre-create DACL contains an unexpected ACE count")
    with contextlib.ExitStack() as stack:
        sid_values = [
            stack.enter_context(oracle._string_sid(value)) for value in expected_sids
        ]
        if not oracle._ADVAPI32.EqualSid(owner, sid_values[0]):
            raise AssertionError("pre-create owner is not SYSTEM")
        for index, (expected_sid, expected_mask) in enumerate(
            zip(sid_values, expected_masks, strict=True)
        ):
            ace_pointer = ctypes.c_void_p()
            if not oracle._ADVAPI32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise ctypes.WinError(ctypes.get_last_error())
            ace = ctypes.cast(
                ace_pointer, ctypes.POINTER(oracle._ACCESS_ALLOWED_ACE)
            ).contents
            if (
                ace.Header.AceType != oracle._ACCESS_ALLOWED_ACE_TYPE
                or ace.Header.AceFlags != 0
                or ace.Mask != expected_mask
            ):
                raise AssertionError(
                    "pre-create DACL ACE type, flags, order or mask differs"
                )
            ace_sid = ace_pointer.value + oracle._ACCESS_ALLOWED_ACE.SidStart.offset
            if not oracle._ADVAPI32.IsValidSid(ace_sid):
                raise AssertionError("pre-create DACL contains an invalid SID")
            if not oracle._ADVAPI32.EqualSid(ace_sid, expected_sid):
                raise AssertionError("pre-create DACL ACE SID/order differs")


def assert_set_security_info_call(arguments, *, expected_handle=None) -> int:
    """Validate the exact post-create hardening call with independent literals."""
    if len(arguments) != 7:
        raise AssertionError("SetSecurityInfo did not receive seven arguments")

    def scalar(value) -> int:
        return int(getattr(value, "value", value) or 0)

    handle = scalar(arguments[0])
    if not handle or (expected_handle is not None and handle != scalar(expected_handle)):
        raise AssertionError("SetSecurityInfo used the wrong creation handle")
    if scalar(arguments[1]) != 1:  # SE_FILE_OBJECT
        raise AssertionError("SetSecurityInfo used the wrong object type")
    if scalar(arguments[2]) != 0x80000005:  # OWNER | DACL | PROTECTED_DACL
        raise AssertionError("SetSecurityInfo used the wrong security-information mask")
    if not scalar(arguments[3]) or scalar(arguments[4]):
        raise AssertionError("SetSecurityInfo owner/group arguments are incorrect")
    if not scalar(arguments[5]) or scalar(arguments[6]):
        raise AssertionError("SetSecurityInfo DACL/SACL arguments are incorrect")
    return handle


def role_binding(sf, components, *, directory: bool, role_name: str):
    return required(sf, "_BirthRoleBindingV1")(
        components=tuple(components),
        kind=required(sf, "_ObjectKind")(
            "directory" if directory else "regular_file"
        ),
        role=role(sf, role_name),
    )


def explicit_role_bindings(sf, *specs):
    """Build only caller-declared test bindings; never inspect the filesystem."""
    return tuple(
        role_binding(
            sf,
            components,
            directory=directory,
            role_name=role_name,
        )
        for components, directory, role_name in specs
    )


def exact_role_catalog(sf, bindings=(), *, root: Path | None = None):
    values = [
        role_binding(
            sf,
            (),
            directory=True,
            role_name="birth_integrity_only",
        )
    ]
    candidates = tuple(
        set(
            (
                role_binding(
                    sf,
                    ("provisioning-v1.lock",),
                    directory=False,
                    role_name="birth_integrity_only",
                ),
                *bindings,
            )
        )
    )
    if root is None:
        # Without a root nothing is inspected: the declared bindings are taken
        # as they are, which is what a cell needs when it declares a name it is
        # about to create.
        values.extend(candidates)
    else:
        for binding in candidates:
            path = root.joinpath(*binding.components)
            try:
                observed = path.lstat()
            except FileNotFoundError:
                continue
            file_attributes = observed.st_file_attributes
            assert not (
                file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ), "preexisting exact binding is a reparse point"
            assert (
                stat.S_IFMT(observed.st_mode),
                binding.kind.value,
            ) in {
                (stat.S_IFDIR, "directory"),
                (stat.S_IFREG, "regular_file"),
            }, "preexisting exact binding kind mismatch"
            values.append(binding)
    ordered = sorted(
        values,
        key=lambda item: (
            tuple(os.fsencode(part) for part in item.components),
            item.kind.value,
            item.role.value,
        ),
    )
    keys = [(item.components, item.kind) for item in ordered]
    if len(keys) != len(set(keys)):
        raise AssertionError("duplicate exact role binding")
    return required(sf, "_BirthRoleCatalogV1")(
        schema_version=1,
        patterns=(),
        exact_bindings=tuple(ordered),
        generation=0,
    )


@contextlib.contextmanager
def session(
    root: Path,
    *,
    root_profile: str = "integrity_only",
    authenticated_sid: str | None = None,
    create_root: bool = True,
    role_bindings=(),
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
        # The declared bindings are the caller's, as the helper that builds
        # them states: filtering them by what already exists on disk would
        # silently drop the name a cell is about to create and then dispose.
        exact_role_catalog(sf, role_bindings),
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
    get_information = oracle._KERNEL32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL
    file_id = FILE_ID_INFO()
    standard = FILE_STANDARD_INFO()
    if not get_information(
        handle, 18, ctypes.byref(file_id), ctypes.sizeof(file_id)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not get_information(
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
        handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    try:
        return _identity_from_handle(handle)
    finally:
        oracle._close_handle(handle)


def volume_facts(path: Path, *, directory: bool = True) -> dict[str, object]:
    """Query filesystem capabilities and identity without product constants."""
    require_windows()
    import ctypes
    from ctypes import wintypes

    oracle = identity_oracle()
    query_volume = oracle._KERNEL32.GetVolumeInformationByHandleW
    query_volume.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    query_volume.restype = wintypes.BOOL
    handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    try:
        flags = wintypes.DWORD()
        filesystem = ctypes.create_unicode_buffer(64)
        if not query_volume(
            handle,
            None,
            0,
            None,
            None,
            ctypes.byref(flags),
            filesystem,
            len(filesystem),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        result = _identity_from_handle(handle)
        result.update(filesystem=filesystem.value, filesystem_flags=int(flags.value))
        return result
    finally:
        oracle._close_handle(handle)


def reparse_tag(path: Path, *, directory: bool) -> int:
    require_windows()
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    get_information = oracle._KERNEL32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL

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
        if not get_information(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(info.ReparseTag)
    finally:
        oracle._close_handle(handle)


def _security_descriptor_bytes(
    path: Path, *, directory: bool, open_reparse: bool = False
) -> bytes:
    """Return the independent self-relative owner/DACL representation."""
    require_windows()
    import ctypes
    from ctypes import wintypes

    oracle = identity_oracle()
    length_of = oracle._ADVAPI32.GetSecurityDescriptorLength
    length_of.argtypes = (ctypes.c_void_p,)
    length_of.restype = wintypes.DWORD
    if open_reparse:
        flags = (0x02000000 if directory else 0x00000080) | 0x00200000
        handle = oracle._KERNEL32.CreateFileW(
            str(path),
            oracle._READ_CONTROL,
            oracle._FILE_SHARE_READ
            | oracle._FILE_SHARE_WRITE
            | oracle._FILE_SHARE_DELETE,
            None,
            3,
            flags,
            None,
        )
        if handle == oracle._INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    descriptor = ctypes.c_void_p()
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
        length = int(length_of(descriptor))
        if length <= 0:
            raise AssertionError("GetSecurityDescriptorLength returned zero")
        return ctypes.string_at(descriptor, length)
    finally:
        if descriptor.value:
            oracle._KERNEL32.LocalFree(descriptor)
        oracle._close_handle(handle)


def acl_profile_facts(path: Path, *, directory: bool) -> dict[str, object]:
    """Read every closed-profile ACL dimension through the independent oracle."""
    require_windows()
    import ctypes

    oracle = identity_oracle()
    handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    try:
        result = oracle._ADVAPI32.GetSecurityInfo(
            handle,
            oracle._SE_FILE_OBJECT,
            oracle._OWNER_SECURITY_INFORMATION | oracle._DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise OSError(result, "GetSecurityInfo")
        if not owner.value or not dacl.value or not descriptor.value:
            raise AssertionError("owner or DACL is absent")
        control = oracle.wintypes.WORD()
        revision = oracle.wintypes.DWORD()
        if not oracle._ADVAPI32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        acl_information = oracle._ACL_SIZE_INFORMATION()
        if not oracle._ADVAPI32.GetAclInformation(
            dacl,
            ctypes.byref(acl_information),
            ctypes.sizeof(acl_information),
            oracle._ACL_SIZE_INFORMATION_CLASS,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        ace_types: list[int] = []
        ace_flags: list[int] = []
        ace_masks: list[int] = []
        ace_sids: list[str] = []
        for index in range(int(acl_information.AceCount)):
            ace_pointer = ctypes.c_void_p()
            if not oracle._ADVAPI32.GetAce(
                dacl, index, ctypes.byref(ace_pointer)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            ace = ctypes.cast(
                ace_pointer, ctypes.POINTER(oracle._ACCESS_ALLOWED_ACE)
            ).contents
            sid_pointer = ace_pointer.value + oracle._ACCESS_ALLOWED_ACE.SidStart.offset
            if not oracle._ADVAPI32.IsValidSid(sid_pointer):
                raise AssertionError("DACL ACE contains an invalid SID")
            ace_types.append(int(ace.Header.AceType))
            ace_flags.append(int(ace.Header.AceFlags))
            ace_masks.append(int(ace.Mask))
            ace_sids.append(oracle._sid_to_string(sid_pointer))
        return {
            "owner": oracle._sid_to_string(owner.value),
            "protected": bool(control.value & oracle._SE_DACL_PROTECTED),
            "ace_types": tuple(ace_types),
            "ace_flags": tuple(ace_flags),
            "ace_masks": tuple(ace_masks),
            "ace_sids": tuple(ace_sids),
        }
    finally:
        if descriptor.value:
            oracle._KERNEL32.LocalFree(descriptor)
        oracle._close_handle(handle)


def windows_tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    """Snapshot namespace, identity, metadata, ACL and bytes independently."""
    import ctypes

    oracle = identity_oracle()
    get_attributes = oracle._KERNEL32.GetFileAttributesW
    get_attributes.argtypes = (oracle.wintypes.LPCWSTR,)
    get_attributes.restype = oracle.wintypes.DWORD
    paths = [root, *root.rglob("*")]
    paths.sort(
        key=lambda path: os.fsencode(
            "." if path == root else path.relative_to(root).as_posix()
        )
    )
    rows = []
    for path in paths:
        relative = "." if path == root else path.relative_to(root).as_posix()
        directory = path.is_dir()
        attributes = int(get_attributes(str(path)))
        if attributes == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        facts = identity(path, directory=directory, open_reparse=True)
        payload_sha256 = None
        if not directory:
            # A byte-range lock is mandatory on this platform: the object the
            # product holds cannot be read while it holds it.  The refusal is
            # recorded as such, so the comparison still notices an object that
            # becomes readable or stops being so.
            try:
                payload_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            except PermissionError:
                payload_sha256 = "locked"
        rows.append(
            (
                relative,
                facts["volume"],
                facts["file_id"],
                facts["links"],
                facts["size"],
                facts["directory"],
                facts["delete_pending"],
                attributes,
                reparse_tag(path, directory=directory),
                hashlib.sha256(
                    _security_descriptor_bytes(
                        path, directory=directory, open_reparse=True
                    )
                ).hexdigest(),
                payload_sha256,
            )
        )
    return tuple(rows)


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
            oracle._close_handle(handle)
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


@contextlib.contextmanager
def provisioner_session(root: Path, **kwargs) -> Iterator[object]:
    """Open a test session with the required global exclusive lock held."""
    with session(root, **kwargs) as active:
        with exclusive(active):
            yield active


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


def _birth_authority_role_specs(key_id: str):
    """Return the sole explicit name/type/role table for the R8 fixture."""
    set_id = "0" * 64
    authority_set = ("authority-sets", set_id)
    admission = authority_set + ("admission",)
    approval = authority_set + ("approval",)
    semantic = authority_set + ("semantic",)
    return (
        (("authority-sets",), True, "birth_integrity_only"),
        (authority_set, True, "birth_integrity_only"),
        (admission, True, "birth_confidential"),
        (admission + ("birth-keystore.lock",), False, "birth_confidential"),
        (admission + ("keystore.json",), False, "birth_confidential"),
        (admission + ("private",), True, "birth_confidential"),
        (admission + ("private", f"{key_id}.key"), False, "birth_confidential"),
        (admission + ("public",), True, "birth_integrity_only"),
        (admission + ("public", f"{key_id}.pub"), False, "birth_integrity_only"),
        (approval, True, "birth_integrity_only"),
        (approval + ("authority.json",), False, "birth_integrity_only"),
        (semantic, True, "birth_integrity_only"),
        (semantic + ("authority.json",), False, "birth_integrity_only"),
        (semantic + ("public",), True, "birth_integrity_only"),
        (semantic + ("public", "review.pub"), False, "birth_integrity_only"),
        (semantic + ("evidence",), True, "birth_integrity_only"),
    )


def birth_authority_role_bindings(sf, key_id: str):
    """Build the R8 catalog only from the fixture's closed declaration."""
    return explicit_role_bindings(sf, *_birth_authority_role_specs(key_id))


def assert_historical_profile(
    path: Path, *, public: bool, directory: bool, sid: str
) -> None:
    """Inspect the legacy owner/DACL through GetAce, never SDDL text."""
    oracle = identity_oracle()
    import ctypes
    from ctypes import wintypes

    read = oracle._DIRECTORY_READ_MASK if directory else oracle._FILE_READ_MASK
    expected_sids = ["S-1-5-18", "S-1-5-32-544", sid]
    expected_masks = [oracle._FILE_ALL_ACCESS, oracle._FILE_ALL_ACCESS, oracle._FILE_ALL_ACCESS]
    if public:
        expected_sids.append("S-1-5-11")
        expected_masks.append(read)
    handle = oracle._open_path(path, oracle._READ_CONTROL, directory=directory)
    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    try:
        result = oracle._ADVAPI32.GetSecurityInfo(
            handle,
            oracle._SE_FILE_OBJECT,
            oracle._OWNER_SECURITY_INFORMATION | oracle._DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise OSError(result, "GetSecurityInfo")
        if not owner.value or not dacl.value:
            raise AssertionError("historical security descriptor lacks owner or DACL")
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not oracle._ADVAPI32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not control.value & oracle._SE_DACL_PROTECTED:
            raise AssertionError("historical DACL is not protected")
        acl_information = oracle._ACL_SIZE_INFORMATION()
        if not oracle._ADVAPI32.GetAclInformation(
            dacl,
            ctypes.byref(acl_information),
            ctypes.sizeof(acl_information),
            oracle._ACL_SIZE_INFORMATION_CLASS,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if acl_information.AceCount != len(expected_sids):
            raise AssertionError("historical DACL has an unexpected ACE count")
        with contextlib.ExitStack() as stack:
            resolved = [
                stack.enter_context(oracle._string_sid(value))
                for value in expected_sids
            ]
            if not oracle._ADVAPI32.EqualSid(owner, resolved[2]):
                raise AssertionError("historical owner is not the current token SID")
            for index, (expected_sid, expected_mask) in enumerate(
                zip(resolved, expected_masks, strict=True)
            ):
                ace_pointer = ctypes.c_void_p()
                if not oracle._ADVAPI32.GetAce(
                    dacl, index, ctypes.byref(ace_pointer)
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                ace = ctypes.cast(
                    ace_pointer, ctypes.POINTER(oracle._ACCESS_ALLOWED_ACE)
                ).contents
                if (
                    ace.Header.AceType != oracle._ACCESS_ALLOWED_ACE_TYPE
                    or ace.Header.AceFlags != 0
                    or ace.Mask != expected_mask
                ):
                    raise AssertionError(
                        "historical DACL ACE type, flags, order or mask differs"
                    )
                ace_sid = ace_pointer.value + oracle._ACCESS_ALLOWED_ACE.SidStart.offset
                if not oracle._ADVAPI32.IsValidSid(ace_sid) or not oracle._ADVAPI32.EqualSid(
                    ace_sid, expected_sid
                ):
                    raise AssertionError("historical DACL ACE SID/order differs")
    finally:
        if descriptor.value:
            oracle._KERNEL32.LocalFree(descriptor)
        oracle._close_handle(handle)


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
        "fixture_key_id": (key_id,),
        "keystore": ("authority-sets", set_id, "admission"),
        "approval": ("authority-sets", set_id, "approval", "authority.json"),
        "semantic_authority": ("authority-sets", set_id, "semantic", "authority.json"),
        "semantic_public": ("authority-sets", set_id, "semantic", "public"),
        "semantic_evidence": ("authority-sets", set_id, "semantic", "evidence"),
    }


def provision_birth_authorities(root: Path, sid: str) -> dict[str, tuple[str, ...]]:
    """Create the loader fixture with SYSTEM-owned Birth ACLs, not legacy ACLs."""
    paths = provision_authorities(root, sid)
    key_id = paths["fixture_key_id"][0]
    specs = _birth_authority_role_specs(key_id)
    expected = {components for components, _directory, _role in specs}
    observed = {
        path.relative_to(root).parts
        for path in root.rglob("*")
    }
    if observed != expected:
        raise AssertionError(
            "R8 authority fixture differs from its explicit role declaration"
        )
    for components, directory, role_name in specs:
        path = root.joinpath(*components)
        if path.is_dir() != directory:
            raise AssertionError(
                f"R8 authority fixture kind differs for {components!r}"
            )
        profile = (
            "confidential"
            if role_name == "birth_confidential"
            else "integrity_only"
        )
        apply_profile(path, profile, directory=directory, sid=sid)
    return paths


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
    owned_handle = process._handle
    process._handle = None
    close = getattr(owned_handle, "Close", None)
    if close is None:
        raise AssertionError("Popen process handle has no verified close operation")
    close()
