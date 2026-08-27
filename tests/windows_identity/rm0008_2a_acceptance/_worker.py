"""Subprocess crash/lock worker for the Windows RM-0008 acceptance cells."""
from __future__ import annotations

import sys
import threading
import time
import importlib
import json
import os
import subprocess
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import _windows_support as support


def _barrier(path: Path) -> None:
    path.write_bytes(b"ready")
    threading.Event().wait()


def _write_state(barrier: Path, **state) -> None:
    barrier.with_suffix(".state.json").write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _scalar(value) -> int:
    return int(getattr(value, "value", value) or 0)


def _validate_lockfileex_call(sf, args, *, exclusive: bool) -> tuple[int, int]:
    """Validate the complete one-byte, nonblocking LockFileEx contract."""
    import ctypes

    if len(args) != 6:
        raise AssertionError("LockFileEx did not receive its six ABI arguments")
    expected_flags = 0x00000001 | (0x00000002 if exclusive else 0)
    if _scalar(args[0]) == 0:
        raise AssertionError("LockFileEx received a null lock handle")
    if _scalar(args[1]) != expected_flags or _scalar(args[2]) != 0:
        raise AssertionError("LockFileEx flags/reserved field violate the frozen contract")
    if _scalar(args[3]) != 1 or _scalar(args[4]) != 0:
        raise AssertionError("LockFileEx did not address exactly byte range [0, 1)")
    if not args[5]:
        raise AssertionError("LockFileEx received a null OVERLAPPED pointer")
    overlapped = ctypes.cast(
        args[5], ctypes.POINTER(sf._OVERLAPPED)
    ).contents
    for field in ("Internal", "InternalHigh", "hEvent"):
        if _scalar(getattr(overlapped, field)) != 0:
            raise AssertionError(f"LockFileEx OVERLAPPED.{field} was not zero")
    offset = getattr(overlapped, "offset", None)
    if offset is None:
        raise AssertionError("LockFileEx OVERLAPPED lacks the offset union member")
    for field in ("Offset", "OffsetHigh"):
        if _scalar(getattr(offset, field)) != 0:
            raise AssertionError(f"LockFileEx OVERLAPPED.{field} was not zero")
    return _scalar(args[0]), ctypes.cast(args[5], ctypes.c_void_p).value


def _validate_unlockfileex_call(
    sf,
    args,
    *,
    expected_handle: int,
    expected_overlapped_address: int,
) -> None:
    """Validate exact release of the same one-byte lock and OVERLAPPED."""
    import ctypes

    if len(args) != 5:
        raise AssertionError("UnlockFileEx did not receive its five ABI arguments")
    if _scalar(args[0]) != expected_handle:
        raise AssertionError("UnlockFileEx used a handle different from LockFileEx")
    if _scalar(args[1]) != 0 or _scalar(args[2]) != 1 or _scalar(args[3]) != 0:
        raise AssertionError("UnlockFileEx did not release exactly byte range [0, 1)")
    if not args[4]:
        raise AssertionError("UnlockFileEx received a null OVERLAPPED pointer")
    address = ctypes.cast(args[4], ctypes.c_void_p).value
    if address != expected_overlapped_address:
        raise AssertionError("UnlockFileEx did not reuse the LockFileEx OVERLAPPED")


def dispose(case: str, root: Path, barrier: Path) -> None:
    sf = support.product()
    bindings = support.explicit_role_bindings(
        sf, (("victim",), False, "birth_confidential")
    )
    with support.session(root, role_bindings=bindings) as active:
        with support.exclusive(active):
            payload = b"crash-disposition"
            support.create_file(active, ("victim",), payload, "birth_confidential")
            expectation = support.disposal_expectation(
                sf,
                root / "victim",
                ("victim",),
                kind="regular_file",
                role_name="birth_confidential",
                disposal_class="complete_file",
                payload=payload,
            )
            _write_state(
                barrier,
                identity=support.identity(root / "victim", directory=False),
                payload_sha256=support.digest(payload),
                snapshot=support.windows_tree_snapshot(root),
            )
            native = sf._KERNEL32.SetFileInformationByHandle

            def intercepted(*args):
                if case.endswith("before-native"):
                    _barrier(barrier)
                result = native(*args)
                if not result:
                    return result
                _barrier(barrier)
                return result

            with mock.patch.object(sf._KERNEL32, "SetFileInformationByHandle", intercepted):
                active.dispose_transaction_object(expectation)


def rename(case: str, root: Path, barrier: Path) -> None:
    sf = support.product()
    bindings = support.explicit_role_bindings(
        sf,
        (("source",), False, "birth_confidential"),
        (("destination",), False, "birth_confidential"),
    )
    with support.session(root, role_bindings=bindings) as active:
        with support.exclusive(active):
            support.create_file(active, ("source",), b"crash-rename", "birth_confidential")
            _write_state(
                barrier,
                identity=support.identity(root / "source", directory=False),
                payload_sha256=support.digest(b"crash-rename"),
                snapshot=support.windows_tree_snapshot(root),
            )
            # The move is asked of the native entry point, which reports a
            # refusal with a negative status and not with the truth value of
            # the Win32 wrapper it replaced.
            native = sf._NTDLL.NtSetInformationFile

            def intercepted(*args):
                if case.endswith("before-native"):
                    _barrier(barrier)
                result = native(*args)
                if int(result) < 0:
                    return result
                _barrier(barrier)
                return result

            with mock.patch.object(sf._NTDLL, "NtSetInformationFile", intercepted):
                active.rename_no_replace(("source",), ("destination",), directory=False)


def create(case: str, root: Path, barrier: Path) -> None:
    import ctypes

    sf = support.product()
    bindings = support.explicit_role_bindings(
        sf, (("complete.bin",), False, "birth_confidential")
    )
    with support.session(root, role_bindings=bindings) as active:
        # Acquire and, if needed, initialize the global lock before installing
        # the killpoint interceptions: those must observe the payload create,
        # never lock-file initialization.
        with support.exclusive(active):
            pre_create_snapshot = support.windows_tree_snapshot(root)
            if {row[0] for row in pre_create_snapshot} != {
                ".",
                "provisioning-v1.lock",
            }:
                raise AssertionError("creation worker pre-state is not root plus lock")
            native_write = sf._win_write_all
            native_flush = sf._KERNEL32.FlushFileBuffers
            native_read = sf._KERNEL32.ReadFile
            native_security = sf._ADVAPI32.SetSecurityInfo
            native_create = sf._NTDLL.NtCreateFile
            native_close = sf._KERNEL32.CloseHandle
            creation_handle: int | None = None
            security_handle: int | None = None
            read_count = 0
            creation_closed = False

            def create_intercepted(*args):
                nonlocal creation_handle
                result = native_create(*args)
                if int(result) >= 0:
                    if creation_handle is not None:
                        raise AssertionError("payload creation used NtCreateFile more than once")
                    creation_handle = int(
                        ctypes.cast(
                            args[0], ctypes.POINTER(ctypes.c_void_p)
                        ).contents.value
                        or 0
                    )
                    if not creation_handle:
                        raise AssertionError("NtCreateFile returned a null payload handle")
                return result

            def security_intercepted(*args):
                nonlocal security_handle
                if creation_handle is None:
                    raise AssertionError("SetSecurityInfo preceded successful NtCreateFile")
                security_handle = support.assert_set_security_info_call(
                    args, expected_handle=creation_handle
                )
                return native_security(*args)

            def close_intercepted(handle):
                nonlocal creation_closed
                if _scalar(handle) == creation_handle:
                    if creation_closed:
                        raise AssertionError("creation handle was closed twice")
                    if security_handle != creation_handle:
                        raise AssertionError("creation handle closed before ACL application")
                    creation_closed = True
                return native_close(handle)

            def crash_state(handle):
                payload_handle = int(getattr(handle, "value", handle) or 0)
                if (
                    creation_handle is None
                    or security_handle is None
                    or creation_handle != security_handle
                    or security_handle != payload_handle
                ):
                    raise AssertionError(
                        "payload create, ACL and write did not use the same handle"
                    )
                return {
                    "identity": support.handle_identity(handle),
                    "killpoint": case,
                    "pre_create_snapshot": pre_create_snapshot,
                    "setsecurityinfo": {
                        "object_type": 1,
                        "security_information": "0x80000005",
                        "same_handle": True,
                    },
                }

            def write_intercepted(handle, payload):
                if creation_closed:
                    raise AssertionError("payload write used a closed/reused handle generation")
                if case == "crash-after-acl-before-write":
                    _write_state(barrier, **crash_state(handle))
                    _barrier(barrier)
                if case == "crash-partial-write":
                    native_write(handle, payload[: max(1, len(payload) // 2)])
                    _write_state(barrier, **crash_state(handle))
                    _barrier(barrier)
                native_write(handle, payload)
                if case == "crash-complete-write":
                    _write_state(barrier, **crash_state(handle))
                    _barrier(barrier)

            def flush_intercepted(handle):
                if creation_closed or _scalar(handle) != creation_handle:
                    raise AssertionError("FlushFileBuffers used a reopened payload handle")
                result = native_flush(handle)
                if result and case == "crash-flush":
                    _write_state(barrier, **crash_state(handle))
                    _barrier(barrier)
                return result

            def read_intercepted(handle, *args):
                nonlocal read_count
                if creation_closed or _scalar(handle) != creation_handle:
                    raise AssertionError("ReadFile used a reopened payload handle")
                read_count += 1
                return native_read(handle, *args)

            with mock.patch.object(sf._NTDLL, "NtCreateFile", create_intercepted):
                with mock.patch.object(sf._ADVAPI32, "SetSecurityInfo", security_intercepted):
                    with mock.patch.object(sf._KERNEL32, "ReadFile", read_intercepted):
                        with mock.patch.object(sf, "_win_write_all", write_intercepted):
                            with mock.patch.object(sf._KERNEL32, "FlushFileBuffers", flush_intercepted):
                                with mock.patch.object(sf._KERNEL32, "CloseHandle", close_intercepted):
                                    support.create_file(
                                        active,
                                        ("complete.bin",),
                                        b"creation-crash-payload",
                                        "birth_confidential",
                                    )
            if read_count < 1 or not creation_closed:
                raise AssertionError("payload creation did not reread from its creation handle")


def product_create_as_standard_user(root: Path) -> int:
    sf = support.product()
    sid = support.identity_oracle().current_token_facts().user_sid
    bindings = support.explicit_role_bindings(
        sf, (("never-log-this-secret.bin",), False, "birth_confidential")
    )
    try:
        with support.session(
            root,
            authenticated_sid=sid,
            create_root=False,
            role_bindings=bindings,
        ) as active:
            with active.global_lock(exclusive=True, create=False):
                support.create_file(
                    active,
                    ("never-log-this-secret.bin",),
                    b"never-log-this-secret",
                    "birth_confidential",
                )
    except sf.BirthSecureFSError as exc:
        rendered = str(exc)
        if (
            exc.code == "birth_provisioning_elevation_required"
            and rendered == exc.code
            and "never-log-this-secret" not in rendered
        ):
            return 40
        return 20
    return 20


def _fixed_authority_paths():
    set_id = "0" * 64
    return {
        "keystore": ("authority-sets", set_id, "admission"),
        "approval": ("authority-sets", set_id, "approval", "authority.json"),
        "semantic_authority": ("authority-sets", set_id, "semantic", "authority.json"),
        "semantic_public": ("authority-sets", set_id, "semantic", "public"),
        "semantic_evidence": ("authority-sets", set_id, "semantic", "evidence"),
    }


def _call_loader(kind: str, active):
    paths = _fixed_authority_paths()
    if kind == "keystore":
        module = importlib.import_module("executor_birth_keystore")
        return module._load_birth_keystore_in_session(paths["keystore"], active)
    if kind == "approval":
        module = importlib.import_module("executor_birth_approval_authority")
        return module._load_approval_authority_in_session(paths["approval"], active)
    module = importlib.import_module("executor_birth_semantic_authority")
    return module._load_semantic_authority_in_session(
        paths["semantic_authority"],
        paths["semantic_public"],
        paths["semantic_evidence"],
        active,
    )


def loader(kind: str, root: Path, marker: Path, key_id: str) -> None:
    sf = support.product()
    bindings = support.birth_authority_role_bindings(sf, key_id)
    native_lock = sf._KERNEL32.LockFileEx
    native_unlock = sf._KERNEL32.UnlockFileEx
    acquired: list[tuple[int, int]] = []
    unlock_count = 0

    def attempted(*args):
        handle, overlapped_address = _validate_lockfileex_call(
            sf, args, exclusive=False
        )
        result = native_lock(*args)
        if result:
            acquired.append((handle, overlapped_address))
        if not result:
            import ctypes

            code = ctypes.get_last_error()
            if code == sf._ERROR_LOCK_VIOLATION:
                marker.with_suffix(".attempt").write_bytes(b"attempt")
            ctypes.set_last_error(code)
            sf._KERNEL32.SetLastError(code)
        return result

    def unlocked(*args):
        nonlocal unlock_count
        if not acquired:
            raise AssertionError("UnlockFileEx has no matching successful acquisition")
        expected_handle, expected_overlapped_address = acquired.pop()
        _validate_unlockfileex_call(
            sf,
            args,
            expected_handle=expected_handle,
            expected_overlapped_address=expected_overlapped_address,
        )
        unlock_count += 1
        return native_unlock(*args)

    with mock.patch.object(sf._KERNEL32, "LockFileEx", attempted), mock.patch.object(
        sf._KERNEL32, "UnlockFileEx", unlocked
    ):
        with support.session(
            root, create_root=False, role_bindings=bindings
        ) as active:
            with active.global_lock(exclusive=False, create=False, timeout=20):
                _call_loader(kind, active)
    expected_unlocks = 2 if kind == "keystore" else 1
    if acquired or unlock_count != expected_unlocks:
        raise AssertionError(
            "loader did not release its Windows byte locks exactly once in LIFO order"
        )
    marker.with_suffix(".result").write_bytes(b"ok")


def lock(case: str, root: Path, marker: Path) -> None:
    exclusive = case in {"exclusive", "empty"}
    create = case == "empty"
    sf = support.product()
    bindings = support.explicit_role_bindings(sf)
    native_lock = sf._KERNEL32.LockFileEx
    native_unlock = sf._KERNEL32.UnlockFileEx
    acquired: dict[str, int] = {}
    unlock_count = 0

    def attempted(*args):
        handle, overlapped_address = _validate_lockfileex_call(
            sf, args, exclusive=exclusive
        )
        result = native_lock(*args)
        if result:
            acquired.update(
                handle=handle, overlapped_address=overlapped_address
            )
        if not result:
            import ctypes

            code = ctypes.get_last_error()
            if code == sf._ERROR_LOCK_VIOLATION:
                marker.with_suffix(".attempt").write_bytes(b"attempt")
            ctypes.set_last_error(code)
            sf._KERNEL32.SetLastError(code)
        return result

    def unlocked(*args):
        nonlocal unlock_count
        _validate_unlockfileex_call(
            sf,
            args,
            expected_handle=acquired["handle"],
            expected_overlapped_address=acquired["overlapped_address"],
        )
        unlock_count += 1
        return native_unlock(*args)

    with mock.patch.object(sf._KERNEL32, "LockFileEx", attempted), mock.patch.object(
        sf._KERNEL32, "UnlockFileEx", unlocked
    ):
        with support.session(
            root, create_root=False, role_bindings=bindings
        ) as active:
            manager = active.global_lock(
                exclusive=exclusive, create=create, timeout=20
            )
            if case == "empty":
                native_write = sf._win_write_all

                def stop_before_write(handle, payload):
                    marker.with_suffix(".ready").write_bytes(b"ready")
                    threading.Event().wait()

                with mock.patch.object(sf, "_win_write_all", stop_before_write):
                    with manager:
                        pass
                return
            with manager:
                marker.with_suffix(".ready").write_bytes(b"ready")
                release = marker.with_suffix(".release")
                deadline = time.monotonic() + 30
                while not release.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not release.exists():
                    raise TimeoutError("lock worker release barrier timed out")
    if case != "empty" and unlock_count != 1:
        raise AssertionError("worker did not release its Windows byte lock exactly once")
    marker.with_suffix(".result").write_bytes(b"ok")


def swap(case: str, root: Path, marker: Path) -> None:
    outside = root.parent / "outside"
    target = (
        root
        if case == "swap-after-root"
        else root / "first"
        if case == "swap-after-first"
        else root / "first" / "middle"
        if case == "swap-after-middle"
        else root / "first" / "middle" / "last"
        if case == "swap-after-last"
        else root / "first" / "middle" / "last" / "payload.bin"
    )
    directory = case != "swap-final-object"
    deadline = time.monotonic() + 30
    go = marker.with_suffix(".go")
    while not go.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not go.exists():
        raise TimeoutError("substitution worker did not receive the barrier")
    original = support.identity(target, directory=directory)
    saved = target.with_name(target.name + ".saved")
    renamed = False
    response: dict[str, object]
    try:
        target.rename(saved)
        renamed = True
        if directory:
            made = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(outside)],
                capture_output=True,
            )
            if made.returncode != 0:
                raise OSError(made.returncode, "mklink /J")
        else:
            os.symlink(outside / "payload.bin", target)
        saved_facts = support.identity(saved, directory=directory)
        replacement = support.identity(
            target, directory=directory, open_reparse=True
        )
        tag = support.reparse_tag(target, directory=directory)
        if (
            saved_facts["volume"] != original["volume"]
            or saved_facts["file_id"] != original["file_id"]
            or tag == 0
        ):
            raise AssertionError("substitution oracle did not preserve the original or install reparse")
        response = {
            "outcome": "installed",
            "original_volume": original["volume"],
            "original_file_id": original["file_id"],
            "replacement_volume": replacement["volume"],
            "replacement_file_id": replacement["file_id"],
            "reparse_tag": tag,
        }
    except OSError as exc:
        if renamed:
            raise
        response = {
            "outcome": "denied",
            "winerror": getattr(exc, "winerror", None),
            "original_volume": original["volume"],
            "original_file_id": original["file_id"],
        }
    marker.with_suffix(".result").write_text(
        json.dumps(response, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def main(argv: list[str]) -> None:
    if len(argv) == 5 and argv[1:3] == ["--child", "product-create"]:
        raise SystemExit(product_create_as_standard_user(Path(argv[3])))
    if len(argv) == 6 and argv[1] == "loader":
        _, operation, case, root, barrier, key_id = argv
        loader(case, Path(root), Path(barrier), key_id)
        return
    if len(argv) != 5:
        raise SystemExit(64)
    operation, case, root, barrier = argv[1:]
    if operation == "dispose":
        dispose(case, Path(root), Path(barrier))
    elif operation == "rename":
        rename(case, Path(root), Path(barrier))
    elif operation == "create":
        create(case, Path(root), Path(barrier))
    elif operation == "loader":
        raise SystemExit("loader requires the explicit fixture key id")
    elif operation == "lock":
        lock(case, Path(root), Path(barrier))
    elif operation == "swap":
        swap(case, Path(root), Path(barrier))
    else:
        raise SystemExit(64)


if __name__ == "__main__":
    main(sys.argv)
