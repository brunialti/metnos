"""Historical Windows D reproducers for the frozen RM-0008 2A prototype.

This is deliberately not an acceptance suite.  A successful execution means
that every listed defect was observed on the exact frozen prototype.  The file
is invoked only by the opt-in diagnostic workflow dispatch and is not collected
by the mandatory pytest suites.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch


if os.name != "nt":
    raise RuntimeError("the RM-0008 Windows diagnostics require Windows")

REPOSITORY = Path(__file__).resolve().parents[2]
FROZEN_INPUTS = {
    "runtime/executor_birth_secure_fs.py":
        "fd194b9c89a57ef94ddd2de0fb79717f488cffeafee3f26d1d0f4f4d3930a7d8",
    "tests/windows_identity/win32_identity_oracle.py":
        "56eceb56421faf92ef7989e6c1a644e60403cef6826dbf86a1a1b06dd7191352",
}


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _verify_frozen_inputs() -> dict[str, str]:
    observed = {
        relative: hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest()
        for relative in FROZEN_INPUTS
    }
    _require(
        observed == FROZEN_INPUTS,
        "Windows D inputs differ from the frozen prototype",
    )
    return observed


# Verify bytes before importing either the product or its independent oracle.
_FROZEN_OBSERVED = _verify_frozen_inputs()
sys.path.insert(0, str(REPOSITORY / "runtime"))

from ctypes import wintypes

import executor_birth_secure_fs as secure_fs
import win32_identity_oracle as oracle

_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_STANDARD_INFO_CLASS = 1
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("allocation_size", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("links", wintypes.DWORD),
        ("delete_pending", ctypes.c_ubyte),
        ("directory", ctypes.c_ubyte),
    ]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    ]


oracle._KERNEL32.GetFileInformationByHandleEx.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
)
oracle._KERNEL32.GetFileInformationByHandleEx.restype = wintypes.BOOL


def _close_unadopted(descriptor: secure_fs._AuthenticatedRootDescriptor) -> None:
    for handle in reversed(descriptor.handles):
        secure_fs._win_close(handle)
    descriptor.handles.clear()


def _descriptor(
    root: Path, service_sid: str
) -> secure_fs._AuthenticatedRootDescriptor:
    handles, absolute = secure_fs._open_win_root(root)
    return secure_fs._AuthenticatedRootDescriptor(
        secure_fs._DESCRIPTOR_TOKEN,
        handles,
        absolute,
        secure_fs._PlatformIdentity(None, service_sid),
    )


def _new_root(parent: Path, name: str, service_sid: str) -> Path:
    root = parent / name
    root.mkdir()
    oracle.apply_profile(
        root, "confidential", service_sid, directory=True
    )
    oracle.assert_exact_profile(
        root, "confidential", service_sid, directory=True
    )
    return root


def _independent_directory(
    root: Path,
    service_sid: str,
    name: str,
    *,
    profile: str,
    service_mask: int | None = None,
) -> Path:
    path = root / name
    path.mkdir()
    oracle.apply_profile(
        path,
        profile,
        service_sid,
        directory=True,
        service_mask=service_mask,
    )
    if service_mask is None:
        oracle.assert_exact_profile(
            path, profile, service_sid, directory=True
        )
    return path


def _independent_file(
    root: Path,
    service_sid: str,
    name: str,
    payload: bytes,
    *,
    profile: str,
) -> Path:
    path = root / name
    path.write_bytes(payload)
    oracle.apply_profile(
        path, profile, service_sid, directory=False
    )
    oracle.assert_exact_profile(
        path, profile, service_sid, directory=False
    )
    return path


def _closed_directory_sddl(
    service_sid: str,
    outsider_sid: str,
    mutation: str,
) -> tuple[str, bool]:
    owner = "BA" if mutation == "owner" else "SY"
    system = "(A;;FA;;;SY)"
    administrators = "(A;;FA;;;BA)"
    service_type = "D" if mutation == "ace_type" else "A"
    service_target = outsider_sid if mutation == "sid" else service_sid
    service_mask = 0x001F01FF if mutation == "mask" else 0x001200A9
    service = f"({service_type};;0x{service_mask:08x};;;{service_target})"
    aces = [system, administrators, service]
    if mutation == "ace_order":
        aces = [administrators, system, service]
    protected = mutation != "protection"
    return f"O:{owner}D:{'P' if protected else ''}{''.join(aces)}", protected


def _apply_independent_descriptor(
    path: Path, sddl: str, *, directory: bool, protected: bool
) -> None:
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    if not oracle._ADVAPI32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        oracle._SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        oracle._raise_last_error(
            "ConvertStringSecurityDescriptorToSecurityDescriptorW"
        )
    handle: int | None = None
    try:
        owner = ctypes.c_void_p()
        owner_defaulted = wintypes.BOOL()
        dacl_present = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        dacl_defaulted = wintypes.BOOL()
        if not oracle._ADVAPI32.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
        ):
            oracle._raise_last_error("GetSecurityDescriptorOwner")
        if not oracle._ADVAPI32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            oracle._raise_last_error("GetSecurityDescriptorDacl")
        _require(
            not owner_defaulted.value
            and bool(dacl_present.value)
            and bool(dacl.value)
            and not dacl_defaulted.value,
            "fixture descriptor is not explicit and complete",
        )
        handle = oracle._open_path(
            path,
            oracle._READ_CONTROL | oracle._WRITE_DAC | oracle._WRITE_OWNER,
            directory=directory,
        )
        protection_flag = (
            oracle._PROTECTED_DACL_SECURITY_INFORMATION
            if protected
            else _UNPROTECTED_DACL_SECURITY_INFORMATION
        )
        with oracle.enabled_restore_privilege():
            result = oracle._ADVAPI32.SetSecurityInfo(
                handle,
                oracle._SE_FILE_OBJECT,
                oracle._OWNER_SECURITY_INFORMATION
                | oracle._DACL_SECURITY_INFORMATION
                | protection_flag,
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


def _oracle_rejects_closed_profile(
    path: Path, service_sid: str, *, directory: bool
) -> bool:
    try:
        oracle.assert_exact_profile(
            path, "confidential", service_sid, directory=directory
        )
    except AssertionError:
        return True
    return False


def _independent_shape(path: Path, *, directory: bool) -> tuple[int, int]:
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    handle = oracle._KERNEL32.CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle in {None, _INVALID_HANDLE_VALUE}:
        oracle._raise_last_error("CreateFileW(independent shape)")
    standard = _FileStandardInfo()
    tagged = _FileAttributeTagInfo()
    try:
        if not oracle._KERNEL32.GetFileInformationByHandleEx(
            handle,
            _FILE_STANDARD_INFO_CLASS,
            ctypes.byref(standard),
            ctypes.sizeof(standard),
        ):
            oracle._raise_last_error("GetFileInformationByHandleEx(FileStandardInfo)")
        if not oracle._KERNEL32.GetFileInformationByHandleEx(
            handle,
            _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(tagged),
            ctypes.sizeof(tagged),
        ):
            oracle._raise_last_error(
                "GetFileInformationByHandleEx(FileAttributeTagInfo)"
            )
        return int(standard.links), int(tagged.reparse_tag)
    finally:
        oracle._close_handle(handle)


def _diagnose_r5_root_profiles(
    parent: Path, service_sid: str, outsider_sid: str
) -> dict[str, object]:
    accepted: list[str] = []
    for mutation in ("owner", "protection", "ace_order", "ace_type", "sid", "mask"):
        root = _new_root(parent, f"r5-{mutation}", service_sid)
        descriptor = _descriptor(root, service_sid)
        try:
            sddl, protected = _closed_directory_sddl(
                service_sid, outsider_sid, mutation
            )
            _apply_independent_descriptor(
                root, sddl, directory=True, protected=protected
            )
            _require(
                _oracle_rejects_closed_profile(
                    root, service_sid, directory=True
                ),
                f"independent oracle did not observe {mutation}",
            )
            with secure_fs._adopt_authenticated_root(descriptor) as session:
                _require(
                    session.inventory(()) == (),
                    f"prototype root use did not converge for {mutation}",
                )
            accepted.append(mutation)
        finally:
            _close_unadopted(descriptor)
    _require(
        accepted
        == ["owner", "protection", "ace_order", "ace_type", "sid", "mask"],
        "not every independent root-profile mutation was observed",
    )
    return {
        "criterion": "D-R5-root",
        "independent_mutations_rejected": accepted,
        "prototype_root_use_accepted": accepted,
    }


def _diagnose_r5_profile_catalog(
    parent: Path, service_sid: str
) -> dict[str, object]:
    root = _new_root(parent, "r5-catalog", service_sid)
    _independent_directory(
        root, service_sid, "public", profile="integrity_only"
    )
    _independent_file(
        root,
        service_sid,
        "private.bin",
        b"confidential",
        profile="confidential",
    )
    directory_mismatch = False
    file_mismatch = False
    with secure_fs._adopt_authenticated_root(_descriptor(root, service_sid)) as session:
        try:
            session.inventory(("public",))
        except secure_fs.BirthSecureFSError as exc:
            directory_mismatch = exc.code == "birth_provisioning_acl_unsafe"
        try:
            session.read_file(
                ("private.bin",), maximum=32, exact_private=False
            )
        except secure_fs.BirthSecureFSError as exc:
            file_mismatch = exc.code == "birth_provisioning_acl_unsafe"
    _require(
        directory_mismatch and file_mismatch,
        "the frozen profile-catalog mismatch was not observed",
    )
    return {
        "criterion": "D-R5-catalog",
        "created_integrity_directory_reopened_as_confidential": directory_mismatch,
        "created_confidential_file_reopened_as_integrity_only": file_mismatch,
    }


def _diagnose_r5_product_acl_self_check(
    parent: Path, service_sid: str
) -> dict[str, object]:
    root = _new_root(parent, "r5-product-self-check", service_sid)
    rejected = False
    with secure_fs._adopt_authenticated_root(_descriptor(root, service_sid)) as session:
        try:
            with session.global_lock(exclusive=True, create=True):
                pass
        except secure_fs.BirthSecureFSError as exc:
            rejected = (
                exc.code == "birth_provisioning_acl_unsafe"
                and exc.__cause__ is None
            )
    lock = root / "provisioning-v1.lock"
    _require(rejected, "the frozen product ACL self-check did not reject")
    _require(
        lock.is_file() and lock.stat().st_size == 0,
        "the rejected product lock did not leave the expected empty fixture",
    )
    oracle.assert_exact_profile(
        lock, "integrity_only", service_sid, directory=False
    )
    return {
        "criterion": "D-R5-product-self-check",
        "product_rejected_its_created_acl": True,
        "independent_oracle_accepts_created_acl": True,
        "empty_lock_remained_after_rejection": True,
    }


def _diagnose_r6_cached_handle(parent: Path, service_sid: str) -> dict[str, object]:
    root = _new_root(parent, "r6-cached", service_sid)
    _independent_directory(
        root, service_sid, "source", profile="confidential"
    )
    denied = False
    with secure_fs._adopt_authenticated_root(_descriptor(root, service_sid)) as session:
        session.open_directory(("source",), exact_private=True)
        try:
            session.rename_no_replace(
                ("source",), ("destination",), directory=True
            )
        except secure_fs.BirthSecureFSError as exc:
            denied = (
                exc.code == "birth_provisioning_io_unavailable"
                and isinstance(exc.__cause__, OSError)
                and exc.__cause__.errno == 5  # ERROR_ACCESS_DENIED
            )
    _require(denied, "the cached inspection handle unexpectedly renamed")
    _require(
        (root / "source").is_dir() and not (root / "destination").exists(),
        "cached-handle failure changed the source or destination",
    )
    return {
        "criterion": "D-R6-cached",
        "cached_inspection_handle_lacks_delete": True,
        "rename_failed_before_destination": True,
    }


def _diagnose_r6_fresh_handle(parent: Path, service_sid: str) -> dict[str, object]:
    root = _new_root(parent, "r6-fresh", service_sid)
    _independent_directory(
        root,
        service_sid,
        "source",
        profile="confidential",
        service_mask=0x001F01FF,
    )
    _require(
        _oracle_rejects_closed_profile(
            root / "source", service_sid, directory=True
        ),
        "independent oracle did not observe the source DACL mutation",
    )
    with secure_fs._adopt_authenticated_root(_descriptor(root, service_sid)) as session:
        session.rename_no_replace(
            ("source",), ("destination",), directory=True
        )
    _require(
        not (root / "source").exists() and (root / "destination").is_dir(),
        "fresh-handle rename did not reach the expected destination",
    )
    _require(
        _oracle_rejects_closed_profile(
            root / "destination", service_sid, directory=True
        ),
        "the renamed destination no longer exposes the DACL mutation",
    )
    return {
        "criterion": "D-R6-fresh",
        "corrupt_source_profile_rejected_by_oracle": True,
        "prototype_rename_accepted_corrupt_profile": True,
    }


def _diagnose_r7_inventory(parent: Path, service_sid: str) -> dict[str, object]:
    root = _new_root(parent, "r7", service_sid)
    item_parent = _independent_directory(
        root, service_sid, "parent", profile="confidential"
    )
    _independent_directory(
        root, service_sid, "target", profile="confidential"
    )
    _independent_file(
        item_parent,
        service_sid,
        "item.bin",
        b"payload",
        profile="confidential",
    )
    item = root / "parent" / "item.bin"
    alias = root / "alias.bin"
    junction = root / "parent" / "junction"
    os.link(item, alias)
    made = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(root / "target")],
        check=False,
        capture_output=True,
        text=True,
    )
    _require(made.returncode == 0, "junction fixture creation failed")
    links_before, _ = _independent_shape(item, directory=False)
    _, junction_tag = _independent_shape(junction, directory=True)
    _require(
        links_before == 2 and junction_tag != 0,
        "independent link or junction precondition was not established",
    )

    real_inventory = secure_fs._win_inventory
    scans = 0

    def mutate_between_scans(handle: int):
        nonlocal scans
        observed = real_inventory(handle)
        scans += 1
        if scans == 1:
            alias.unlink()
        return observed

    try:
        with secure_fs._adopt_authenticated_root(
            _descriptor(root, service_sid)
        ) as session:
            with patch.object(
                secure_fs, "_win_inventory", mutate_between_scans
            ):
                entries = session._inventory_state(("parent",))
        by_name = {entry.name: entry for entry in entries}
        links_after, _ = _independent_shape(item, directory=False)
        _require(
            scans == 2 and links_after == 1,
            "the synchronized link-count mutation did not occur",
        )
        _require(
            by_name["item.bin"].links == 1,
            "the prototype did not expose its forced link count",
        )
        _require(
            by_name["junction"].directory is True,
            "the prototype did not classify the junction as a directory",
        )
        _require(
            "reparse_tag" not in secure_fs._InventoryEntry.__dataclass_fields__,
            "the frozen inventory unexpectedly acquired a reparse tag",
        )
        return {
            "criterion": "D-R7-Windows",
            "independent_links_before": links_before,
            "independent_links_after": links_after,
            "prototype_links_before_and_after": by_name["item.bin"].links,
            "junction_has_nonzero_independent_tag": True,
            "record_has_reparse_tag": False,
            "mutation_between_scans_was_accepted": True,
        }
    finally:
        if junction.exists():
            os.rmdir(junction)


def main() -> int:
    _require(
        ctypes.sizeof(ctypes.c_void_p) == 8,
        "the diagnostics require Windows x64",
    )
    parent_token = oracle.current_token_facts()
    _require(
        parent_token.elevated and parent_token.administrator,
        "the diagnostic controller is not elevated administrator",
    )
    _require(
        parent_token.integrity_rid >= 0x3000,
        "the diagnostic controller integrity is below high",
    )
    frozen = _FROZEN_OBSERVED

    service: oracle.LocalAccount | None = None
    outsider: oracle.LocalAccount | None = None
    root: Path | None = None
    cleanup_errors: list[BaseException] = []
    try:
        service = oracle.create_standard_account("m8ds")
        outsider = oracle.create_standard_account("m8do")
        _require(
            service.sid.casefold() != outsider.sid.casefold(),
            "diagnostic identities are not distinct",
        )
        public = os.environ.get("PUBLIC")
        _require(public, "Windows did not expose the public profile directory")
        root = Path(public) / f"metnos-rm0008-d-{uuid.uuid4().hex}"
        root.mkdir()
        oracle.assert_supported_volume(root)
        oracle.apply_profile(
            root, "integrity_only", service.sid, directory=True
        )
        observations = [
            _diagnose_r5_root_profiles(root, service.sid, outsider.sid),
            _diagnose_r5_product_acl_self_check(root, service.sid),
            _diagnose_r5_profile_catalog(root, service.sid),
            _diagnose_r6_cached_handle(root, service.sid),
            _diagnose_r6_fresh_handle(root, service.sid),
            _diagnose_r7_inventory(root, service.sid),
        ]
        print(json.dumps({
            "schema_version": 1,
            "frozen_inputs": frozen,
            "observations": observations,
        }, sort_keys=True))
        return 0
    finally:
        if root is not None and root.exists():
            try:
                shutil.rmtree(root)
            except BaseException as exc:
                cleanup_errors.append(exc)
        for account in (outsider, service):
            if account is not None:
                try:
                    oracle.delete_account(account)
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "RM-0008 Windows diagnostic cleanup did not complete",
                cleanup_errors,
            )


if __name__ == "__main__":
    raise SystemExit(main())
