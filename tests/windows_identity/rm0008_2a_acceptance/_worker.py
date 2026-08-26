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


def dispose(case: str, root: Path, barrier: Path) -> None:
    sf = support.product()
    with support.session(root) as active:
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
    with support.session(root) as active:
        with support.exclusive(active):
            support.create_file(active, ("source",), b"crash-rename", "birth_confidential")
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
                active.rename_no_replace(("source",), ("destination",), directory=False)


def create(case: str, root: Path, barrier: Path) -> None:
    sf = support.product()
    with support.session(root) as active:
        native_write = sf._win_write_all
        native_flush = sf._KERNEL32.FlushFileBuffers

        def write_intercepted(handle, payload):
            if case == "crash-after-acl-before-write":
                _barrier(barrier)
            if case == "crash-partial-write":
                native_write(handle, payload[: max(1, len(payload) // 2)])
                _barrier(barrier)
            native_write(handle, payload)
            if case == "crash-complete-write":
                _barrier(barrier)

        def flush_intercepted(handle):
            result = native_flush(handle)
            if result and case == "crash-flush":
                _barrier(barrier)
            return result

        with mock.patch.object(sf, "_win_write_all", write_intercepted):
            with mock.patch.object(sf._KERNEL32, "FlushFileBuffers", flush_intercepted):
                support.create_file(
                    active,
                    ("complete.bin",),
                    b"creation-crash-payload",
                    "birth_confidential",
                )


def product_create_as_standard_user(root: Path) -> int:
    sf = support.product()
    sid = support.identity_oracle().current_token_facts().user_sid
    try:
        with support.session(root, authenticated_sid=sid, create_root=False) as active:
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


def loader(kind: str, root: Path, marker: Path) -> None:
    sf = support.product()
    native_lock = sf._KERNEL32.LockFileEx

    def attempted(*args):
        result = native_lock(*args)
        if not result:
            import ctypes

            code = ctypes.get_last_error()
            if code == sf._ERROR_LOCK_VIOLATION:
                marker.with_suffix(".attempt").write_bytes(b"attempt")
            ctypes.set_last_error(code)
            sf._KERNEL32.SetLastError(code)
        return result

    with mock.patch.object(sf._KERNEL32, "LockFileEx", attempted):
        with support.session(root, create_root=False) as active:
            with active.global_lock(exclusive=False, create=False, timeout=20):
                _call_loader(kind, active)
    marker.with_suffix(".result").write_bytes(b"ok")


def lock(case: str, root: Path, marker: Path) -> None:
    exclusive = case in {"exclusive", "empty"}
    create = case == "empty"
    sf = support.product()
    native_lock = sf._KERNEL32.LockFileEx

    def attempted(*args):
        result = native_lock(*args)
        if not result:
            import ctypes

            code = ctypes.get_last_error()
            if code == sf._ERROR_LOCK_VIOLATION:
                marker.with_suffix(".attempt").write_bytes(b"attempt")
            ctypes.set_last_error(code)
            sf._KERNEL32.SetLastError(code)
        return result

    with mock.patch.object(sf._KERNEL32, "LockFileEx", attempted):
        with support.session(root, create_root=False) as active:
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
        loader(case, Path(root), Path(barrier))
    elif operation == "lock":
        lock(case, Path(root), Path(barrier))
    elif operation == "swap":
        swap(case, Path(root), Path(barrier))
    else:
        raise SystemExit(64)


if __name__ == "__main__":
    main(sys.argv)
