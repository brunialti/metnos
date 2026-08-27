"""What the running Windows token actually holds.

The provisioner stops on Windows at the rename that publishes a final, and the
refusal names a privilege.  Whether the token holds that privilege is a fact
about the machine, and on a runner the only channel that prints a fact from a
quiet run is a warning.  This cell never fails: it observes and reports.
"""
from __future__ import annotations

import os
import warnings


def _token_privileges() -> dict[str, object]:
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    # Without these the pseudo handle is truncated to a 32-bit int and the
    # open fails for a reason that has nothing to do with the token.
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = ()
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    advapi.OpenProcessToken.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    )
    advapi.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi.LookupPrivilegeNameW.argtypes = (
        wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(
        kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        return {"error": "OpenProcessToken", "code": ctypes.get_last_error()}
    try:
        size = wintypes.DWORD(0)
        advapi.GetTokenInformation(token, 3, None, 0, ctypes.byref(size))
        buffer = (ctypes.c_byte * max(size.value, 4))()
        if not advapi.GetTokenInformation(
            token, 3, ctypes.byref(buffer), size, ctypes.byref(size)
        ):
            return {
                "error": "GetTokenInformation", "code": ctypes.get_last_error(),
            }
        count = int.from_bytes(bytes(buffer[:4]), "little")
        found: dict[str, int] = {}
        for index in range(count):
            offset = 4 + index * 12
            luid = bytes(buffer[offset:offset + 8])
            attributes = int.from_bytes(
                bytes(buffer[offset + 8:offset + 12]), "little"
            )
            name = ctypes.create_unicode_buffer(256)
            length = wintypes.DWORD(256)
            if advapi.LookupPrivilegeNameW(
                None, ctypes.byref(ctypes.create_string_buffer(luid, 8)),
                name, ctypes.byref(length),
            ):
                found[name.value] = attributes
        return {"count": count, "privileges": found}
    finally:
        kernel.CloseHandle(token)


def test_the_windows_token_is_reported_not_assumed():
    if os.name != "nt":
        return
    observed = _token_privileges()
    privileges = observed.get("privileges", {})
    restore = privileges.get("SeRestorePrivilege")
    warnings.warn(
        "RM-0008 Windows token: SeRestorePrivilege="
        + ("absent" if restore is None else f"attributes={restore:#x}")
        + f"; privileges={observed.get('count')}"
        + f"; takeownership={'SeTakeOwnershipPrivilege' in privileges}"
        + f"; error={observed.get('error')}",
        stacklevel=1,
    )
