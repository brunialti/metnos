"""Windows inventory, loader locking, byte locking and substitution cells."""
from __future__ import annotations

import os
import ast
import inspect
import subprocess
import time
import contextlib
import ctypes
import textwrap
from pathlib import Path
from unittest import mock

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
    "swap-after-middle",
    "swap-final-object",
)


def _assert_uniform_windows_component_walk(source: str) -> None:
    tree = ast.parse(textwrap.dedent(source))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_directory_chain"
    ]
    if len(functions) != 1:
        raise AssertionError("Windows component walk must have one _directory_chain")
    function = functions[0]
    loops = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.For)
        and any(
            isinstance(candidate, ast.Name) and candidate.id == "components"
            for candidate in ast.walk(node.iter)
        )
    ]
    if len(loops) != 1:
        raise AssertionError("Windows component traversal must use one common loop")
    loop = loops[0]
    if not isinstance(loop.target, ast.Name):
        raise AssertionError("Windows component traversal cannot expose depth")
    if not any(
        isinstance(node, ast.Call)
        and getattr(node.func, "attr", getattr(node.func, "id", None))
        == "_win_open_relative_v1"
        for node in ast.walk(loop)
    ):
        raise AssertionError("Windows traversal is not relative-handle-bound")
    depth_names = {"components", "prefix", "index", "depth", "position", "offset"}
    for node in ast.walk(loop):
        if isinstance(node, (ast.Break, ast.Continue)):
            raise AssertionError("Windows traversal exits at a special depth")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "components"
        ):
            raise AssertionError("Windows traversal indexes by depth")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"enumerate", "len"}
            and any(
                isinstance(candidate, ast.Name)
                and candidate.id in {"components", "prefix"}
                for candidate in ast.walk(node)
            )
        ):
            raise AssertionError("Windows traversal inspects depth")
        if isinstance(node, (ast.If, ast.IfExp, ast.Match)):
            selector = node.subject if isinstance(node, ast.Match) else node.test
            referenced = {
                candidate.id
                for candidate in ast.walk(selector)
                if isinstance(candidate, ast.Name)
            }
            if referenced & depth_names:
                raise AssertionError("Windows traversal has a depth-specific branch")
        if any(
            isinstance(candidate, ast.Constant)
            and candidate.value in {"first", "middle", "last", "payload.bin"}
            for candidate in ast.walk(node)
        ):
            raise AssertionError("Windows traversal recognizes a sentinel name")


def _assert_windows_depth_guard_rejects_mutants() -> None:
    valid = """
    def _directory_chain(self, components):
        for component in components:
            child = _win_open_relative_v1(current, component)
    """
    mutants = (
        """
        def _directory_chain(self, components):
            for index, component in enumerate(components):
                child = _win_open_relative_v1(current, component)
                if index == 1:
                    return
        """,
        """
        def _directory_chain(self, components):
            for component in components:
                child = _win_open_relative_v1(current, component)
                if component == components[-1]:
                    return
        """,
        """
        def _directory_chain(self, components):
            for component in components:
                child = _win_open_relative_v1(current, component)
                if component == "middle":
                    return
        """,
    )
    _assert_uniform_windows_component_walk(valid)
    for source in mutants:
        with pytest.raises(AssertionError):
            _assert_uniform_windows_component_walk(source)


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


def _assert_c1_retry_and_unlock_contract(sf, active) -> None:
    """Prove the exact retry schedule and matching Windows unlock call."""
    native_lock = sf._KERNEL32.LockFileEx
    native_unlock = sf._KERNEL32.UnlockFileEx
    sf_close = sf._KERNEL32.CloseHandle
    clock = [100.0]
    sleeps: list[float] = []
    lock_attempts = 0
    acquired: dict[str, int] = {}
    unlock_calls = 0

    def scalar(value) -> int:
        return int(getattr(value, "value", value) or 0)

    def monotonic() -> float:
        return clock[0]

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    def assert_shared_lock_attempt(args) -> int:
        if len(args) != 6:
            raise AssertionError("LockFileEx retry used the wrong ABI arity")
        if (
            scalar(args[1]) != 0x00000001
            or scalar(args[2]) != 0
            or scalar(args[3]) != 1
            or scalar(args[4]) != 0
            or not args[5]
        ):
            raise AssertionError("LockFileEx retry changed the shared byte-lock ABI")
        return scalar(args[0])

    def lock_file_ex(*args):
        nonlocal lock_attempts
        lock_attempts += 1
        assert_shared_lock_attempt(args)
        if lock_attempts <= 6:
            ctypes.set_last_error(33)  # ERROR_LOCK_VIOLATION, independent literal.
            sf._KERNEL32.SetLastError(33)
            return False
        result = native_lock(*args)
        if result:
            acquired["handle"] = scalar(args[0])
            acquired["overlapped"] = ctypes.cast(args[5], ctypes.c_void_p).value
        return result

    def unlock_file_ex(*args):
        nonlocal unlock_calls
        unlock_calls += 1
        if len(args) != 5:
            raise AssertionError("UnlockFileEx used the wrong ABI arity")
        if (
            scalar(args[0]) != acquired.get("handle")
            or scalar(args[1]) != 0
            or scalar(args[2]) != 1
            or scalar(args[3]) != 0
            or not args[4]
            or ctypes.cast(args[4], ctypes.c_void_p).value
            != acquired.get("overlapped")
        ):
            raise AssertionError("UnlockFileEx did not release the acquired byte lock")
        return native_unlock(*args)

    with mock.patch.object(sf._KERNEL32, "LockFileEx", lock_file_ex), mock.patch.object(
        sf._KERNEL32, "UnlockFileEx", unlock_file_ex
    ), mock.patch.object(sf.time, "monotonic", monotonic), mock.patch.object(
        sf.time, "sleep", sleep
    ):
        with active.global_lock(exclusive=False, create=False, timeout=1.0):
            pass

    if lock_attempts != 7:
        raise AssertionError("Windows retry did not stop after the first success")
    if sleeps != [0.005, 0.010, 0.020, 0.040, 0.080, 0.100]:
        raise AssertionError(f"Windows retry schedule drifted: {sleeps!r}")
    if unlock_calls != 1:
        raise AssertionError("Windows retry lock was not released exactly once")

    non_retry_attempts = 0
    non_retry_handle: int | None = None
    non_retry_closes: list[int] = []

    def non_retryable(*args):
        nonlocal non_retry_attempts, non_retry_handle
        non_retry_attempts += 1
        handle = assert_shared_lock_attempt(args)
        if non_retry_handle is not None and handle != non_retry_handle:
            raise AssertionError("non-retryable failure changed lock handle")
        non_retry_handle = handle
        ctypes.set_last_error(87)
        sf._KERNEL32.SetLastError(87)
        return False

    def reject_non_retry_unlock(*args):
        raise AssertionError("failed non-retryable acquisition called UnlockFileEx")

    def close_non_retry(handle):
        numeric = scalar(handle)
        if numeric == non_retry_handle:
            non_retry_closes.append(numeric)
        return sf_close(handle)

    def acquire_non_retryable() -> None:
        with active.global_lock(exclusive=False, create=False, timeout=1.0):
            pass

    with mock.patch.object(
        sf._KERNEL32, "LockFileEx", non_retryable
    ), mock.patch.object(
        sf._KERNEL32, "UnlockFileEx", reject_non_retry_unlock
    ), mock.patch.object(
        sf._KERNEL32, "CloseHandle", close_non_retry
    ), mock.patch.object(
        sf.time,
        "sleep",
        lambda delay: (_ for _ in ()).throw(
            AssertionError("non-retryable lock error slept")
        ),
    ):
        error = support.require_code(
            acquire_non_retryable,
            "birth_provisioning_lock_unsafe",
        )
    if str(error) != "birth_provisioning_lock_unsafe":
        raise AssertionError("non-retryable lock error leaked native diagnostics")
    if (
        non_retry_attempts != 1
        or non_retry_handle is None
        or non_retry_closes != [non_retry_handle]
    ):
        raise AssertionError(
            "non-retryable Windows lock error was retried or leaked its handle"
        )

    deadline_clock = [200.0]
    deadline_sleeps: list[float] = []
    deadline_attempts = 0
    deadline_handle: int | None = None
    deadline_closes: list[int] = []

    def deadline_monotonic() -> float:
        return deadline_clock[0]

    def deadline_sleep(delay: float) -> None:
        deadline_sleeps.append(delay)
        deadline_clock[0] += delay

    def permanent_contention(*args):
        nonlocal deadline_attempts, deadline_handle
        deadline_attempts += 1
        handle = assert_shared_lock_attempt(args)
        if deadline_handle is not None and handle != deadline_handle:
            raise AssertionError("deadline retry changed lock handle")
        deadline_handle = handle
        ctypes.set_last_error(33)  # ERROR_LOCK_VIOLATION, independent literal.
        sf._KERNEL32.SetLastError(33)
        return False

    def reject_deadline_unlock(*args):
        raise AssertionError("timed-out acquisition called UnlockFileEx")

    def close_deadline(handle):
        numeric = scalar(handle)
        if numeric == deadline_handle:
            deadline_closes.append(numeric)
        return sf_close(handle)

    def acquire_until_deadline() -> None:
        with active.global_lock(exclusive=False, create=False, timeout=0.012):
            pass

    with mock.patch.object(
        sf._KERNEL32, "LockFileEx", permanent_contention
    ), mock.patch.object(
        sf._KERNEL32, "UnlockFileEx", reject_deadline_unlock
    ), mock.patch.object(
        sf._KERNEL32, "CloseHandle", close_deadline
    ), mock.patch.object(
        sf.time, "monotonic", deadline_monotonic
    ), mock.patch.object(sf.time, "sleep", deadline_sleep):
        error = support.require_code(
            acquire_until_deadline,
            "birth_provisioning_lock_unavailable",
        )
    if str(error) != "birth_provisioning_lock_unavailable":
        raise AssertionError("deadline error leaked native diagnostics")
    if (
        deadline_attempts != 3
        or len(deadline_sleeps) != 2
        or deadline_sleeps[0] != 0.005
        or deadline_sleeps[1] != pytest.approx(0.007, abs=1e-12)
        or sum(deadline_sleeps) != pytest.approx(0.012, abs=1e-12)
        or deadline_handle is None
        or deadline_closes != [deadline_handle]
    ):
        raise AssertionError(
            "Windows contention crossed its deadline or leaked its lock handle"
        )


@pytest.mark.parametrize("case", R7_CASES, ids=R7_CASES)
def test_r7_windows_inventory_records(case: str, tmp_path: Path, monkeypatch) -> None:
    sf = support.product()
    root = tmp_path / "birth"
    if case in {"directory-record", "junction-reparse-record-rejected"}:
        role_specs = [(("record-dir",), True, "birth_integrity_only")]
    else:
        role_specs = [(("record.bin",), False, "birth_confidential")]
        if case == "hardlink-rejected":
            role_specs.append(
                (("second-link.bin",), False, "birth_confidential")
            )
    sentinel_bindings = support.explicit_role_bindings(sf, *role_specs)
    with support.session(root, role_bindings=sentinel_bindings) as active:
        relative_open = support.required(sf, "_win_open_relative_v1")
        real_info = sf._win_info
        real_close = sf._KERNEL32.CloseHandle
        audit_active = False
        inventory_scan = 0
        audit_proofs: list[dict[str, object]] = []
        open_generations: dict[int, dict[str, object]] = {}

        def independent_tag_facts(handle: int) -> tuple[int, int]:
            class FileAttributeTagInfo(ctypes.Structure):
                _fields_ = [
                    ("FileAttributes", sf.wintypes.DWORD),
                    ("ReparseTag", sf.wintypes.DWORD),
                ]

            oracle = support.identity_oracle()
            query = oracle._KERNEL32.GetFileInformationByHandleEx
            value = FileAttributeTagInfo()
            if not query(handle, 9, ctypes.byref(value), ctypes.sizeof(value)):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(value.FileAttributes), int(value.ReparseTag)

        def common_relative_open(*args, **kwargs):
            call = inspect.signature(relative_open).bind_partial(
                *args, **kwargs
            ).arguments
            result = relative_open(*args, **kwargs)
            if audit_active:
                names = [
                    value
                    for value in call.values()
                    if isinstance(value, str)
                    and value
                    in {"provisioning-v1.lock", "record.bin", "second-link.bin", "record-dir"}
                ]
                if names and call.get("purpose") is sf._NtOpenPurposeV1.lock_reader:
                    # Taking the global lock opens the same name for its own
                    # reason, inside this window: it is not one of the reopens
                    # the enumeration performs, and the enumeration's own proof
                    # for that name is still required below.
                    names = []
                if names:
                    handle = int(getattr(result, "value", result) or 0)
                    if not handle or handle in open_generations:
                        raise AssertionError("inventory reused an active handle generation")
                    if call.get("purpose") != sf._NtOpenPurposeV1.read_required:
                        raise AssertionError("inventory reopen used a mutating purpose")
                    proof = {
                        "name": names[0],
                        "handle": handle,
                        "info_calls": 0,
                        "closed": False,
                        "reparse_tag": None,
                        "identity_facts": None,
                        "scan": inventory_scan,
                    }
                    audit_proofs.append(proof)
                    open_generations[handle] = proof
            return result

        def common_info(handle):
            result = real_info(handle)
            numeric = int(getattr(handle, "value", handle) or 0)
            proof = open_generations.get(numeric)
            if audit_active and proof is not None:
                facts = support.handle_identity(numeric)
                attributes, reparse_tag = independent_tag_facts(numeric)
                identity, observed_attributes, links, pending, directory, size = result
                if (
                    identity.volume != facts["volume"]
                    or identity.object_id != facts["file_id"]
                    or observed_attributes != attributes
                    or links != facts["links"]
                    or pending != facts["delete_pending"]
                    or directory != facts["directory"]
                    or size != facts["size"]
                ):
                    raise AssertionError(
                        "inventory FileId/standard/tag facts differ on the reopened handle"
                    )
                proof["info_calls"] = int(proof["info_calls"]) + 1
                proof["reparse_tag"] = reparse_tag
                proof["identity_facts"] = facts
            return result

        def common_close(handle):
            numeric = int(getattr(handle, "value", handle) or 0)
            proof = open_generations.get(numeric)
            if audit_active and proof is not None:
                if proof["closed"]:
                    raise AssertionError("inventory handle generation was closed twice")
                if int(proof["info_calls"]) != 1:
                    raise AssertionError(
                        "inventory handle closed without one identity/standard/tag query"
                    )
                proof["closed"] = True
                del open_generations[numeric]
            return real_close(handle)

        def finish_audit(*, required_names: set[str]) -> None:
            nonlocal audit_active
            audit_active = False
            relevant = [proof for proof in audit_proofs if proof["name"] in required_names]
            if not required_names <= {str(proof["name"]) for proof in relevant}:
                raise AssertionError("inventory did not reopen every required entry")
            if any(
                int(proof["info_calls"]) != 1 or not proof["closed"]
                for proof in relevant
            ):
                raise AssertionError("inventory entry handle lifecycle is incomplete")
            if open_generations:
                raise AssertionError("inventory leaked a reopened handle generation")

        # A later watcher in this cell binds the published signature to name the
        # component it observes: the audit keeps the wrapped function visible so
        # introspection still sees ``component`` and not ``*args``.
        common_relative_open.__wrapped__ = relative_open
        monkeypatch.setattr(sf, "_win_open_relative_v1", common_relative_open)
        monkeypatch.setattr(sf, "_win_info", common_info)
        monkeypatch.setattr(sf._KERNEL32, "CloseHandle", common_close)
        if case == "junction-reparse-record-rejected":
            with support.exclusive(active):
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
                before = support.windows_tree_snapshot(root)
                audit_active = True
                error = support.require_code(
                    lambda: active._inventory_state(()),
                    "birth_provisioning_recovery_ambiguous",
                )
                if str(error) != "birth_provisioning_recovery_ambiguous":
                    raise AssertionError("junction rejection exposed a native message")
                finish_audit(required_names={"record-dir"})
                if not any(
                    proof["name"] == "record-dir"
                    and int(proof["reparse_tag"] or 0) != 0
                    for proof in audit_proofs
                ):
                    raise AssertionError("junction rejection was not caused by its handle tag")
                if support.windows_tree_snapshot(root) != before:
                    raise AssertionError("junction rejection mutated the inventory")
            return
        with support.exclusive(active):
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
            with support.exclusive(active):
                os.link(target, root / "second-link.bin")
                before = support.windows_tree_snapshot(root)
                audit_active = True
                error = support.require_code(
                    lambda: active._inventory_state(()),
                    "birth_provisioning_recovery_ambiguous",
                )
                if str(error) != "birth_provisioning_recovery_ambiguous":
                    raise AssertionError("hardlink rejection exposed a native message")
                finish_audit(required_names={"record.bin"})
                if not any(
                    proof["name"] == "record.bin"
                    and isinstance(proof["identity_facts"], dict)
                    and proof["identity_facts"]["links"] == 2
                    for proof in audit_proofs
                ):
                    raise AssertionError("hardlink fixture was not handle-authenticated")
                if support.identity(target, directory=False)["links"] != 2:
                    raise AssertionError("hardlink fixture was not established")
                if support.windows_tree_snapshot(root) != before:
                    raise AssertionError("hardlink rejection mutated the inventory")
            return
        if case == "mutation-between-scans-rejected":
            native = sf._win_inventory
            calls = 0
            injected_snapshot = None
            before_mutation = support.windows_tree_snapshot(root)
            before_by_name = {row[0]: row for row in before_mutation}
            original_identity = support.identity(target, directory=False)

            def mutate(*args, **kwargs):
                nonlocal calls, injected_snapshot, inventory_scan
                calls += 1
                inventory_scan = calls
                try:
                    result = native(*args, **kwargs)
                finally:
                    inventory_scan = 0
                if calls != 1:
                    return result
                target.write_bytes(b"mutated-between-scans")
                if target.read_bytes() != b"mutated-between-scans":
                    raise AssertionError("inventory mutation payload was not installed")
                mutated_identity = support.identity(target, directory=False)
                if any(
                    mutated_identity[field] != original_identity[field]
                    for field in (
                        "volume",
                        "file_id",
                        "links",
                        "directory",
                        "delete_pending",
                    )
                ):
                    raise AssertionError(
                        "inventory mutation replaced the object instead of changing bytes"
                    )
                support.assert_profile(
                    target,
                    "confidential",
                    directory=False,
                    sid=support.service_sid(),
                )
                injected_snapshot = support.windows_tree_snapshot(root)
                injected_by_name = {row[0]: row for row in injected_snapshot}
                if set(injected_by_name) != set(before_by_name):
                    raise AssertionError("inventory mutation changed the namespace")
                for name, before_row in before_by_name.items():
                    after_row = injected_by_name[name]
                    if name == target.name:
                        if (
                            after_row[:4] != before_row[:4]
                            or after_row[5:7] != before_row[5:7]
                            or (after_row[7] ^ before_row[7]) & ~0x20
                            or after_row[8:10] != before_row[8:10]
                            or after_row[4] != len(b"mutated-between-scans")
                            or after_row[10] == before_row[10]
                        ):
                            raise AssertionError(
                                "inventory mutation changed more than payload size/content"
                            )
                    elif after_row != before_row:
                        raise AssertionError(
                            "inventory mutation changed an unrelated object"
                        )
                return result

            with support.exclusive(active):
                monkeypatch.setattr(sf, "_win_inventory", mutate)
                audit_active = True
                support.require_code(
                    lambda: active._inventory_state(()),
                    "birth_provisioning_recovery_ambiguous",
                )
                if calls != 2:
                    raise AssertionError("inventory did not perform its two scans")
                finish_audit(required_names={target.name})
                target_proofs = [
                    proof for proof in audit_proofs if proof["name"] == target.name
                ]
                if (
                    len(target_proofs) != 2
                    or {int(proof["scan"]) for proof in target_proofs} != {1, 2}
                ):
                    raise AssertionError(
                        "inventory mutation did not reopen the target once per scan"
                    )
                if injected_snapshot is None:
                    raise AssertionError("inventory mutation was not injected")
                if support.windows_tree_snapshot(root) != injected_snapshot:
                    raise AssertionError(
                        "rejected Windows inventory mutated beyond the injected change"
                    )
            return
        relative = support.required(sf, "_win_open_relative_v1")
        native_inventory = sf._win_inventory
        expected_names = {"provisioning-v1.lock", target.name}
        active_scan: list[dict[str, object]] | None = None
        scan_proofs: list[tuple[object, ...]] = []

        def contains_handle(call, expected) -> bool:
            for value in call.values():
                try:
                    if int(getattr(value, "value", value) or 0) == int(
                        getattr(expected, "value", expected) or 0
                    ):
                        return True
                except (TypeError, ValueError):
                    continue
            return False

        def observe_relative(*args, **kwargs):
            call = inspect.signature(relative).bind_partial(
                *args, **kwargs
            ).arguments
            result = relative(*args, **kwargs)
            if active_scan is not None:
                names = [
                    value
                    for value in call.values()
                    if isinstance(value, str) and value in expected_names
                ]
                if names:
                    if len(names) != 1:
                        raise AssertionError(
                            "inventory relative open has an ambiguous component"
                        )
                    if not contains_handle(call, active._root_handle):
                        raise AssertionError(
                            "inventory child open was not parent-relative"
                        )
                    if call.get("purpose") != sf._NtOpenPurposeV1.read_required:
                        raise AssertionError(
                            "inventory child open used a mutating access domain"
                        )
                    active_scan.append(
                        {
                            "name": names[0],
                            "facts": support.handle_identity(
                                int(getattr(result, "value", result) or 0)
                            ),
                        }
                    )
            return result

        def observe_inventory(*args, **kwargs):
            nonlocal active_scan
            if active_scan is not None:
                raise AssertionError("nested Windows inventory scan")
            active_scan = []
            try:
                records = native_inventory(*args, **kwargs)
                if {item.name for item in records} != expected_names:
                    raise AssertionError(
                        "Windows inventory returned an unexpected record set"
                    )
                by_name: dict[str, list[dict[str, object]]] = {}
                for proof in active_scan:
                    by_name.setdefault(str(proof["name"]), []).append(proof)
                if set(by_name) != expected_names or any(
                    len(proofs) != 1 for proofs in by_name.values()
                ):
                    raise AssertionError(
                        "each enumerated entry was not reopened relatively once"
                    )
                for record in records:
                    facts = by_name[record.name][0]["facts"]
                    expected_kind = sf._ObjectKind(
                        "directory" if facts["directory"] else "regular_file"
                    )
                    if (
                        record.identity.volume != facts["volume"]
                        or record.identity.object_id != facts["file_id"]
                        or record.kind is not expected_kind
                        or record.links != facts["links"]
                        or record.size
                        != (None if facts["directory"] else facts["size"])
                        or facts["delete_pending"]
                    ):
                        raise AssertionError(
                            "inventory record is not bound to its reopened handle"
                        )
                scan_proofs.append(records)
                return records
            finally:
                active_scan = None

        monkeypatch.setattr(sf, "_win_open_relative_v1", observe_relative)
        monkeypatch.setattr(sf, "_win_inventory", observe_inventory)
        audit_active = True
        with active.global_lock(exclusive=False, create=False):
            entries = active._inventory_state(())
        finish_audit(required_names=expected_names)
        if scan_proofs != [entries, entries]:
            raise AssertionError(
                "Windows inventory did not repeat the exact handle-bound scan"
            )
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
    sf = support.product()
    sid = support.service_sid()
    root = tmp_path / "birth"
    root.mkdir()
    support.apply_profile(root, "integrity_only", directory=True, sid=sid)
    paths = support.provision_birth_authorities(root, sid)
    key_id = paths["fixture_key_id"][0]
    bindings = support.birth_authority_role_bindings(sf, key_id)
    with support.session(
        root, create_root=False, role_bindings=bindings
    ) as active:
        with active.global_lock(exclusive=True, create=True):
            pass
    if case == "semantic-use-after-close":
        import importlib

        review = importlib.import_module("executor_birth_semantic_review")
        request = review.SemanticReviewRequest(
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "model:generator",
            b"name='fixture'",
            b"{}",
            {"main.py": b"print('ok')"},
        )
        active = support.session(
            root, create_root=False, role_bindings=bindings
        )
        entered = active.__enter__()
        with entered.global_lock(exclusive=False, create=False):
            authority = _loader_in_parent("semantic", entered, paths)
            policy, facts, evidence = authority.inputs_for(request)
            if policy is not authority.policy or facts is None or evidence != ():
                raise AssertionError("valid semantic capability did not work before close")
            evidence_path = root.joinpath(*paths["semantic_evidence"])
            original_evidence = evidence_path.with_name("evidence.original")
            before = support.windows_tree_snapshot(root)
            original_identity = support.identity(
                evidence_path, directory=True
            )
            try:
                evidence_path.rename(original_evidence)
            except OSError as exc:
                if exc.winerror not in {5, 32}:
                    raise AssertionError(
                        "evidence substitution failed with an unexpected Windows code"
                    ) from exc
                if support.windows_tree_snapshot(root) != before:
                    raise AssertionError("denied evidence substitution mutated the tree")
                if support.identity(evidence_path, directory=True) != original_identity:
                    raise AssertionError("denied evidence substitution changed FileId")
            else:
                evidence_path.mkdir()
                support.apply_profile(
                    evidence_path,
                    "integrity_only",
                    directory=True,
                    sid=sid,
                )
                attacker_file = evidence_path / "evil.json"
                attacker_file.write_bytes(b"not-json")
                support.apply_profile(
                    attacker_file,
                    "integrity_only",
                    directory=False,
                    sid=sid,
                )
                replacement_identity = support.identity(
                    evidence_path, directory=True
                )
                if replacement_identity == original_identity:
                    raise AssertionError("evidence replacement reused the original FileId")
            if authority.inputs_for(request)[2] != ():
                raise AssertionError(
                    "semantic authority followed a substituted evidence path"
                )
        active.__exit__(None, None, None)
        try:
            authority.inputs_for(request)
        except review.SemanticReviewError as exc:
            if exc.code != "semantic_review_unavailable":
                raise AssertionError("use-after-close had the wrong stable code") from exc
        else:
            raise AssertionError("semantic authority retained a closed capability")
        return
    kind = case.split("-", 1)[0]
    marker = tmp_path / "loader-marker"
    with support.session(
        root, create_root=False, role_bindings=bindings
    ) as holder:
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
            process = _worker("loader", kind, str(root), str(marker), key_id)
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
    lock_bindings = support.explicit_role_bindings(sf)
    with support.session(root, role_bindings=lock_bindings) as active:
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
            with active.global_lock(exclusive=True, create=True):
                pass
            _assert_c1_retry_and_unlock_contract(sf, active)
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
    _assert_windows_depth_guard_rejects_mutants()
    _assert_uniform_windows_component_walk(
        inspect.getsource(sf._SecureRootSession._directory_chain)
    )
    root = tmp_path / "birth"
    bindings = support.explicit_role_bindings(
        sf,
        (("first",), True, "birth_integrity_only"),
        (("first", "middle"), True, "birth_integrity_only"),
        (("first", "middle", "last"), True, "birth_integrity_only"),
        (("first", "middle", "last", "payload.bin"), False, "birth_integrity_only"),
    )
    with support.session(root, role_bindings=bindings) as provisioner:
        with support.exclusive(provisioner):
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
        with support.session(
            root, create_root=False, role_bindings=bindings
        ) as active:
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

            with active.global_lock(exclusive=False, create=False):
                # The selected open counters cover only the target walk, not
                # acquisition of provisioning-v1.lock.
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
