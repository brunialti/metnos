"""Windows inventory, loader locking, byte locking and substitution cells."""
from __future__ import annotations

import os
import subprocess
import time
import contextlib
from pathlib import Path

import pytest

import _windows_support as support


R7_CASES = (
    "regular-record",
    "directory-record",
    "hardlink-rejected",
    "junction-reparse-record-rejected",
    "mutation-between-scans-rejected",
)

R8_CASES = (
    "keystore-global-exclusive",
    "approval-global-exclusive",
    "semantic-global-exclusive",
    "keystore-local-exclusive",
    "semantic-use-after-close",
)

C1_CASES = (
    "shared-shared",
    "shared-exclusive",
    "exclusive-shared",
    "killed-holder-releases",
    "empty-lock-crash-recovery",
    "reader-never-creates",
)

C2_CASES = (
    "swap-after-root",
    "swap-after-first",
    "swap-after-middle",
    "swap-after-last",
    "swap-final-object",
)


def _wait_for(path: Path, process: subprocess.Popen, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if not path.exists():
        exit_code = process.poll()
        if exit_code is None:
            support.terminate_process(process)
            exit_code = 0xEE
        raise AssertionError(f"worker did not produce {path.name}; exit={exit_code}")


def _worker(*arguments: str) -> subprocess.Popen:
    support.require_windows()
    path = support.REPOSITORY / "tests" / "windows_identity" / "rm0008_2a_acceptance" / "_worker.py"
    return subprocess.Popen(
        [support.sys.executable, str(path), *arguments],
        close_fds=True,
        env=support.worker_environment(),
    )


@pytest.mark.parametrize("case", R7_CASES, ids=R7_CASES)
def test_r7_windows_inventory_records(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    root = tmp_path / "birth"
    with support.session(root) as active:
        if case == "junction-reparse-record-rejected":
            target = root / "record-dir"
            outside = tmp_path / "outside"
            outside.mkdir()
            made = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(outside)],
                capture_output=True,
                text=True,
            )
            if made.returncode != 0:
                raise AssertionError(f"junction creation failed: {made.stderr}")
            if support.reparse_tag(target, directory=True) == 0:
                raise AssertionError("junction fixture has no reparse tag")
            support.require_code(
                lambda: active._inventory_state(()),
                "birth_provisioning_recovery_ambiguous",
            )
            return
        if case in {"regular-record", "hardlink-rejected", "mutation-between-scans-rejected"}:
            identity = support.create_file(
                active, ("record.bin",), b"record", "birth_confidential"
            )
            target = root / "record.bin"
        else:
            identity = support.create_directory(
                active, ("record-dir",), "birth_integrity_only"
            )
            target = root / "record-dir"
        if case == "hardlink-rejected":
            os.link(target, root / "second-link.bin")
            support.require_code(
                lambda: active._inventory_state(()),
                "birth_provisioning_recovery_ambiguous",
            )
            if support.identity(target, directory=False)["links"] != 2:
                raise AssertionError("hardlink fixture was not established")
            return
        if case == "mutation-between-scans-rejected":
            native = sf._win_inventory
            calls = 0

            def mutate(handle):
                nonlocal calls
                calls += 1
                result = native(handle)
                if calls == 1:
                    target.write_bytes(b"mutated-between-scans")
                return result

            monkeypatch.setattr(sf, "_win_inventory", mutate)
            support.require_code(
                lambda: active._inventory_state(()),
                "birth_provisioning_recovery_ambiguous",
            )
            if calls != 2:
                raise AssertionError("inventory did not perform its two scans")
            return
        entries = active._inventory_state(())
        entry = support.get_named_entry(entries, target.name)
        facts = support.identity(target, directory=case == "directory-record")
        expected_kind = sf._ObjectKind(
            "directory" if case == "directory-record" else "regular_file"
        )
        expected_role = sf._BirthObjectRole(
            "birth_integrity_only" if case == "directory-record" else "birth_confidential"
        )
        if (
            entry.identity.volume != facts["volume"]
            or entry.identity.object_id != facts["file_id"]
            or entry.kind is not expected_kind
            or entry.role is not expected_role
            or entry.links != facts["links"]
            or entry.size != (None if case == "directory-record" else facts["size"])
        ):
            raise AssertionError("inventory record differs from independent FileIdInfo")


def _loader_in_parent(kind: str, active, paths):
    import importlib

    if kind == "keystore":
        return importlib.import_module("executor_birth_keystore")._load_birth_keystore_in_session(
            paths["keystore"], active
        )
    if kind == "approval":
        return importlib.import_module("executor_birth_approval_authority")._load_approval_authority_in_session(
            paths["approval"], active
        )
    return importlib.import_module("executor_birth_semantic_authority")._load_semantic_authority_in_session(
        paths["semantic_authority"], paths["semantic_public"], paths["semantic_evidence"], active
    )


@pytest.mark.parametrize("case", R8_CASES, ids=R8_CASES)
def test_r8_windows_loader_locking(case: str, tmp_path: Path) -> None:
    support.product()
    sid = support.service_sid()
    root = tmp_path / "birth"
    root.mkdir()
    support.apply_profile(root, "integrity_only", directory=True, sid=sid)
    paths = support.provision_authorities(root, sid)
    with support.session(root, create_root=False) as active:
        with active.global_lock(exclusive=True, create=True):
            pass
    if case == "semantic-use-after-close":
        import importlib

        active = support.session(root, create_root=False)
        entered = active.__enter__()
        with entered.global_lock(exclusive=False, create=False):
            authority = _loader_in_parent("semantic", entered, paths)
        active.__exit__(None, None, None)
        try:
            authority.inputs_for(None)
        except importlib.import_module("executor_birth_semantic_review").SemanticReviewError as exc:
            if exc.code != "semantic_review_unavailable":
                raise AssertionError("use-after-close had the wrong stable code") from exc
        else:
            raise AssertionError("semantic authority retained a closed capability")
        return
    kind = case.split("-", 1)[0]
    marker = tmp_path / "loader-marker"
    with support.session(root, create_root=False) as holder:
        with contextlib.ExitStack() as locks:
            if case == "keystore-local-exclusive":
                locks.enter_context(
                    holder.global_lock(exclusive=False, create=False)
                )
                locks.enter_context(
                    holder.local_lock(
                        paths["keystore"], exclusive=True, create=False
                    )
                )
            else:
                locks.enter_context(
                    holder.global_lock(exclusive=True, create=False)
                )
            process = _worker("loader", kind, str(root), str(marker))
            try:
                _wait_for(marker.with_suffix(".attempt"), process)
                if marker.with_suffix(".result").exists() or process.poll() is not None:
                    raise AssertionError("loader crossed an incompatible held lock")
            finally:
                if process.poll() is None and marker.with_suffix(".result").exists():
                    process.wait(timeout=30)
        _wait_for(marker.with_suffix(".result"), process)
        if process.wait(timeout=30) != 0:
            raise AssertionError("loader did not resume after lock release")


def _start_lock(root: Path, tmp_path: Path, mode: str, label: str):
    marker = tmp_path / label
    process = _worker("lock", mode, str(root), str(marker))
    _wait_for(marker.with_suffix(".ready"), process)
    return process, marker


@pytest.mark.parametrize("case", C1_CASES, ids=C1_CASES)
def test_c1_windows_byte_lock_matrix(case: str, tmp_path: Path) -> None:
    sf = support.product()
    root = tmp_path / "birth"
    with support.session(root) as active:
        if case == "reader-never-creates":
            def read_lock():
                with active.global_lock(exclusive=False, create=False):
                    pass

            support.require_code(
                read_lock,
                "birth_provisioning_lock_unavailable",
            )
            if (root / "provisioning-v1.lock").exists():
                raise AssertionError("reader created the absent lock")
            return
        if case == "empty-lock-crash-recovery":
            process, marker = _start_lock(root, tmp_path, "empty", "empty-holder")
            support.terminate_process(process)
            with active.global_lock(exclusive=True, create=True):
                pass
            lock_path = root / "provisioning-v1.lock"
            if lock_path.read_bytes() != b"0":
                raise AssertionError("empty lock was not recovered canonically")
            return
        with active.global_lock(exclusive=True, create=True):
            pass
        first_mode = "exclusive" if case.startswith("exclusive") or case.startswith("killed") else "shared"
        first, first_marker = _start_lock(root, tmp_path, first_mode, "first-holder")
        try:
            if case == "killed-holder-releases":
                support.terminate_process(first)
                with active.global_lock(exclusive=True, create=False, timeout=2):
                    pass
                return
            second_mode = "exclusive" if case.endswith("exclusive") else "shared"
            second, second_marker = _start_lock(root, tmp_path, second_mode, "second-holder") if case == "shared-shared" else (
                _worker("lock", second_mode, str(root), str(tmp_path / "second-holder")),
                tmp_path / "second-holder",
            )
            try:
                if case == "shared-shared":
                    second_marker.with_suffix(".release").write_bytes(b"go")
                    _wait_for(second_marker.with_suffix(".result"), second)
                    if second.wait(timeout=30) != 0:
                        raise AssertionError("second shared holder failed")
                else:
                    _wait_for(second_marker.with_suffix(".attempt"), second)
                    if second_marker.with_suffix(".ready").exists() or second.poll() is not None:
                        raise AssertionError("incompatible contender acquired before release")
                    first_marker.with_suffix(".release").write_bytes(b"go")
                    _wait_for(second_marker.with_suffix(".ready"), second)
                    second_marker.with_suffix(".release").write_bytes(b"go")
                    _wait_for(second_marker.with_suffix(".result"), second)
                    if second.wait(timeout=30) != 0:
                        raise AssertionError("contender failed after release")
            finally:
                if second.poll() is None:
                    support.terminate_process(second)
        finally:
            if first.poll() is None:
                first_marker.with_suffix(".release").write_bytes(b"go")
                if first.wait(timeout=30) != 0:
                    raise AssertionError("first lock holder failed")


@pytest.mark.parametrize("case", C2_CASES, ids=C2_CASES)
def test_c2_windows_handle_bound_substitution(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    root = tmp_path / "birth"
    with support.session(root) as provisioner:
        support.create_directory(provisioner, ("first",), "birth_integrity_only")
        support.create_directory(provisioner, ("first", "middle"), "birth_integrity_only")
        support.create_directory(provisioner, ("first", "middle", "last"), "birth_integrity_only")
        support.create_file(
            provisioner,
            ("first", "middle", "last", "payload.bin"),
            b"authentic",
            "birth_integrity_only",
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.bin").write_bytes(b"attacker")
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
    target_is_directory = case != "swap-final-object"
    original_target = support.identity(target, directory=target_is_directory)
    marker = tmp_path / "swap-marker"
    process = _worker("swap", case, str(root), str(marker))
    response = None
    opened_target = None
    try:
        with support.session(root, create_root=False) as active:
            original_open = support.required(sf, "_win_open_relative_v1")
            count = 0
            points = {
                "swap-after-first": 1,
                "swap-after-middle": 2,
                "swap-after-last": 3,
                "swap-final-object": 4,
            }

            def release_process_b() -> dict[str, object]:
                import json

                marker.with_suffix(".go").write_bytes(b"go")
                _wait_for(marker.with_suffix(".result"), process)
                if process.wait(timeout=30) != 0:
                    raise AssertionError("substitution process B failed")
                return json.loads(
                    marker.with_suffix(".result").read_text(encoding="utf-8")
                )

            def opened(*args, **kwargs):
                nonlocal count, response, opened_target
                result = original_open(*args, **kwargs)
                count += 1
                if case != "swap-after-root" and count == points[case]:
                    handle_before = support.handle_identity(result)
                    response = release_process_b()
                    opened_target = support.handle_identity(result)
                    if (
                        opened_target["volume"] != handle_before["volume"]
                        or opened_target["file_id"] != handle_before["file_id"]
                    ):
                        raise AssertionError(
                            "process A handle identity changed across substitution"
                        )
                return result

            monkeypatch.setattr(sf, "_win_open_relative_v1", opened)
            if case == "swap-after-root":
                handle_before = support.handle_identity(active._root_handle)
                response = release_process_b()
                opened_target = support.handle_identity(active._root_handle)
                if (
                    opened_target["volume"] != handle_before["volume"]
                    or opened_target["file_id"] != handle_before["file_id"]
                ):
                    raise AssertionError(
                        "process A root handle identity changed across substitution"
                    )
            try:
                value = active.read_file(
                    ("first", "middle", "last", "payload.bin"),
                    maximum=32,
                    role=sf._BirthObjectRole("birth_integrity_only"),
                )
            except sf.BirthSecureFSError as exc:
                if exc.code not in {
                    "birth_provisioning_io_unavailable",
                    "birth_provisioning_acl_unsafe",
                    "birth_provisioning_recovery_ambiguous",
                }:
                    raise
                value = None
        if response is None or opened_target is None:
            raise AssertionError("process B did not run at the selected depth")
        for key in ("volume", "file_id"):
            if response[f"original_{key}"] != original_target[key]:
                raise AssertionError("process B observed a different original identity")
            if opened_target[key] != original_target[key]:
                raise AssertionError("process A did not retain the original opened FileId")
        if response["outcome"] == "installed":
            if not response["reparse_tag"]:
                raise AssertionError("process B did not install a reparse object")
            if (
                response["replacement_volume"] == original_target["volume"]
                and response["replacement_file_id"] == original_target["file_id"]
            ):
                raise AssertionError("replacement reused the authenticated FileId")
        elif response["outcome"] == "denied":
            if response.get("winerror") not in {5, 32}:
                raise AssertionError("process B was not denied by access/share protection")
            current = support.identity(target, directory=target_is_directory)
            if current["volume"] != original_target["volume"] or current["file_id"] != original_target["file_id"]:
                raise AssertionError("denied substitution still changed the namespace")
        else:
            raise AssertionError("process B returned an unknown substitution outcome")
        if value not in {None, b"authentic"} or value == b"attacker":
            raise AssertionError("product followed attacker-controlled replacement")
    finally:
        if process.poll() is None:
            support.terminate_process(process)
