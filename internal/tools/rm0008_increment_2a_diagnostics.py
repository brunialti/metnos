#!/usr/bin/env python3
"""Historical D reproducers for the frozen RM-0008 increment-2A prototype.

This program is deliberately not a certification test.  Success means that it
observed the known defects on the exact prototype fingerprint recorded in the
companion report.  Acceptance tests live under ``tests`` and assert the
opposite invariants after correction.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[2]
FROZEN_INPUTS = {
    "runtime/executor_birth_secure_fs.py":
        "fd194b9c89a57ef94ddd2de0fb79717f488cffeafee3f26d1d0f4f4d3930a7d8",
    "runtime/executor_birth_keystore.py":
        "5e52dbe2508d4a379c08bc89f37e602279c51167c095b7cf6284abcc5f3b19a3",
    "runtime/executor_birth_approval_authority.py":
        "65fefed1891bdcbb7601859c6085402b109ebef58a25ca15cf97b9bb2d224974",
    "runtime/executor_birth_semantic_authority.py":
        "d130d8e4b2207faaad8341c8dec6658a99c5ed2f8231f47a3b91dc8dc8148c6f",
    "tests/runtime/contracts/test_executor_birth_secure_fs.py":
        "790b62c6854ded6c2205bf08100a3b00cddb29569859ce2cf3e9be0a8a92b25b",
    "tests/portable/test_executor_birth_secure_fs_native.py":
        "d70d8a26dadb73c0751559e90eaa955d3da3b8cfdd068ce0f10b094a4aa10d8f",
    "tests/runtime/contracts/test_executor_birth_keystore.py":
        "71f4316eabe65ef4dc2b80887344bc6e5889a03b192934e129e1dee2c28233a8",
    "tests/runtime/contracts/test_executor_birth_approval_authority.py":
        "1131b3bc6a616588bedbbaa765f00d6b0b929afc8c2e99a85f9219658a9de963",
    "tests/runtime/contracts/test_executor_birth_semantic_authority.py":
        "58793cc9b73854cdaaa90cc84600069e84516e06861878a32fa20aeaf11cdd19",
}


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_frozen_inputs() -> dict[str, str]:
    observed = {
        relative: hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest()
        for relative in FROZEN_INPUTS
    }
    _require(
        observed == FROZEN_INPUTS,
        "diagnostic inputs differ from the frozen prototype",
    )
    return observed


# Verify every byte before importing any frozen product or test-oracle module.
_FROZEN_OBSERVED = verify_frozen_inputs()
sys.path.insert(0, str(REPOSITORY / "runtime"))

import executor_birth_approval_authority as approval  # noqa: E402
import executor_birth_secure_fs as secure_fs  # noqa: E402
import executor_birth_semantic_authority as semantic  # noqa: E402


def _call_sites(names: set[str]) -> list[str]:
    sites: list[str] = []
    # A historical D must not change when later diagnostic or acceptance files
    # are added.  Scan exactly the files whose bytes are frozen above.
    for relative in sorted(FROZEN_INPUTS):
        path = REPOSITORY / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else (
                target.id if isinstance(target, ast.Name) else ""
            )
            if name in names:
                sites.append(f"{relative}:{node.lineno}:{name}")
    return sites


def _authoritative_session(root: Path) -> secure_fs._SecureRootSession:
    handles, absolute = secure_fs._open_posix_root(
        root,
        exact_private=True,
        expected_uid=os.geteuid(),
    )
    descriptor = secure_fs._AuthenticatedRootDescriptor(
        secure_fs._DESCRIPTOR_TOKEN,
        handles,
        absolute,
        secure_fs._PlatformIdentity(os.geteuid(), None),
    )
    return secure_fs._adopt_authenticated_root(descriptor)


def diagnose_r1_mutable_constructible_descriptor(root: Path) -> dict[str, object]:
    handles, absolute = secure_fs._open_posix_root(
        root,
        exact_private=True,
        expected_uid=os.geteuid(),
    )
    descriptor = secure_fs._AuthenticatedRootDescriptor(
        secure_fs._DESCRIPTOR_TOKEN,
        handles,
        absolute,
        secure_fs._PlatformIdentity(os.geteuid(), None),
    )
    altered = absolute + "-caller-selected"
    descriptor.root_path = altered
    observed = descriptor.root_path == altered
    for handle in reversed(descriptor.handles):
        os.close(handle)
    descriptor.handles.clear()
    _require(observed, "D-R1 did not observe the mutable descriptor")
    return {
        "criterion": "D-R1",
        "constructed_outside_installer": True,
        "descriptor_mutable": True,
    }


def diagnose_r2_mutations_without_exclusive_global(root: Path) -> dict[str, object]:
    lock = root / "provisioning-v1.lock"
    lock.write_bytes(b"0")
    lock.chmod(0o600)
    with _authoritative_session(root) as session:
        before = session.inventory(())
        session.create_directory_exclusive(
            ("without-global",), profile="confidential"
        )
        session.create_file_exclusive(
            ("without-global", "pending.bin"),
            b"unlocked",
            profile="confidential",
        )
        session.rename_no_replace(
            ("without-global", "pending.bin"),
            ("without-global", "final.bin"),
            directory=False,
        )
        with session.global_lock(exclusive=False, create=False):
            session.create_directory_exclusive(
                ("under-shared",), profile="confidential"
            )
            session.create_file_exclusive(
                ("under-shared", "pending.bin"),
                b"shared",
                profile="confidential",
            )
            session.rename_no_replace(
                ("under-shared", "pending.bin"),
                ("under-shared", "final.bin"),
                directory=False,
            )
        after = session.inventory(())
    _require(
        (root / "without-global" / "final.bin").read_bytes() == b"unlocked",
        "D-R2 did not observe the unlocked mutation",
    )
    _require(
        (root / "under-shared" / "final.bin").read_bytes() == b"shared",
        "D-R2 did not observe the shared-lock mutation",
    )
    _require(before != after, "D-R2 inventory did not change")
    return {
        "criterion": "D-R2",
        "create_file_without_global": True,
        "create_directory_without_global": True,
        "rename_without_global": True,
        "create_file_under_shared_global": True,
        "create_directory_under_shared_global": True,
        "rename_under_shared_global": True,
        "inventory_changed": True,
    }


def diagnose_r4_existing_empty_lock_omits_parent_sync(root: Path) -> dict[str, object]:
    lock = root / "provisioning-v1.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    synchronized: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        synchronized.append(fd)
        real_fsync(fd)

    with _authoritative_session(root) as session:
        directory_fd = session._root_handle
        with patch.object(secure_fs.os, "fsync", recording_fsync):
            with session.global_lock(exclusive=True, create=True):
                pass
        parent_was_synced = directory_fd in synchronized
    _require(not parent_was_synced, "D-R4 unexpectedly synchronized the parent")
    return {
        "criterion": "D-R4",
        "existing_empty_lock_recovered": True,
        "parent_directory_synced": False,
    }


def diagnose_r7_inventory_loses_link_type(root: Path) -> dict[str, object]:
    regular = root / "regular.bin"
    regular.write_bytes(b"payload")
    regular.chmod(0o600)
    (root / "linked.bin").symlink_to(regular.name)
    with secure_fs._open_legacy_root_session(root, exact_private=True) as session:
        entries = {item.name: item for item in session._inventory_state(())}
    _require(
        entries["regular.bin"].directory is False,
        "D-R7 did not classify the regular file as expected",
    )
    _require(
        entries["linked.bin"].directory is False,
        "D-R7 distinguished the symbolic link unexpectedly",
    )
    _require(
        tuple(secure_fs._InventoryEntry.__dataclass_fields__)
        == ("name", "identity", "directory", "links"),
        "D-R7 inventory schema differs from the frozen prototype",
    )
    return {
        "criterion": "D-R7-POSIX",
        "regular_directory_flag": entries["regular.bin"].directory,
        "symlink_directory_flag": entries["linked.bin"].directory,
        "record_has_object_kind": False,
        "record_has_reparse_tag": False,
    }


def diagnose_static_absences() -> list[dict[str, object]]:
    constructor_sites = _call_sites(
        {"_AuthenticatedRootDescriptor", "_adopt_authenticated_root"}
    )
    mutator_sites = _call_sites(
        {
            "create_file_exclusive",
            "create_directory_exclusive",
            "rename_no_replace",
            "dispose_transaction_object",
        }
    )
    rollback_sites = _call_sites({"_win_dispose_created"})
    contract_test = (
        REPOSITORY / "tests/runtime/contracts/test_executor_birth_secure_fs.py"
    ).read_text(encoding="utf-8")
    portable_test = (
        REPOSITORY / "tests/portable/test_executor_birth_secure_fs_native.py"
    ).read_text(encoding="utf-8")
    return [
        {
            "criterion": "D-R1-static",
            "construction_and_adoption_sites": constructor_sites,
            "mutation_sites": mutator_sites,
        },
        {
            "criterion": "D-R3",
            "dispose_transaction_object_present": hasattr(
                secure_fs._SecureRootSession, "dispose_transaction_object"
            ),
            "direct_rollback_helper_sites": rollback_sites,
        },
        {
            "criterion": "D-R8-approval",
            "session_loader_present": hasattr(
                approval, "_load_approval_authority_in_session"
            ),
        },
        {
            "criterion": "D-R8-semantic",
            "session_loader_present": hasattr(
                semantic, "_load_semantic_authority_in_session"
            ),
        },
        {
            "criterion": "D-R8-contention",
            "keystore_contract_uses_multiprocessing": "multiprocessing" in contract_test,
        },
        {
            "criterion": "D-C1",
            "portable_test_uses_spawn": "get_context(\"spawn\")" in portable_test,
            "portable_test_uses_terminate_process": "TerminateProcess" in portable_test,
        },
        {
            "criterion": "D-C2",
            "portable_test_has_synchronized_adversary": "Barrier(" in portable_test,
        },
        {
            "criterion": "D-C3",
            "contract_test_has_synchronized_adversary": "Barrier(" in contract_test,
        },
        {
            "criterion": "D-C4",
            "contract_test_uses_multiprocessing": "multiprocessing" in contract_test,
            "existing_name_is_in_process":
                "test_in_process_file_descriptions_enforce_shared_exclusive_conflict"
                in contract_test,
        },
    ]


def main() -> int:
    if os.name != "posix":
        raise SystemExit("the historical dynamic D reproducers are POSIX-owned")
    fingerprints = _FROZEN_OBSERVED
    results: list[dict[str, object]] = []
    for diagnostic in (
        diagnose_r1_mutable_constructible_descriptor,
        diagnose_r2_mutations_without_exclusive_global,
        diagnose_r4_existing_empty_lock_omits_parent_sync,
        diagnose_r7_inventory_loses_link_type,
    ):
        with tempfile.TemporaryDirectory(prefix="rm0008-2a-d-") as temporary:
            root = Path(temporary) / "root"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            results.append(diagnostic(root))
    results.extend(diagnose_static_absences())
    expected_static = [
        {
            "criterion": "D-R1-static",
            "construction_and_adoption_sites": [
                "tests/portable/test_executor_birth_secure_fs_native.py:38:_AuthenticatedRootDescriptor",
                "tests/portable/test_executor_birth_secure_fs_native.py:41:_adopt_authenticated_root",
                "tests/runtime/contracts/test_executor_birth_keystore.py:260:_AuthenticatedRootDescriptor",
                "tests/runtime/contracts/test_executor_birth_keystore.py:266:_adopt_authenticated_root",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:32:_AuthenticatedRootDescriptor",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:38:_adopt_authenticated_root",
            ],
            "mutation_sites": [
                "tests/portable/test_executor_birth_secure_fs_native.py:53:rename_no_replace",
                "tests/portable/test_executor_birth_secure_fs_native.py:110:create_directory_exclusive",
                "tests/portable/test_executor_birth_secure_fs_native.py:111:create_file_exclusive",
                "tests/portable/test_executor_birth_secure_fs_native.py:70:rename_no_replace",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:159:create_directory_exclusive",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:160:create_file_exclusive",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:163:rename_no_replace",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:234:create_file_exclusive",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:171:create_file_exclusive",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:184:rename_no_replace",
                "tests/runtime/contracts/test_executor_birth_secure_fs.py:255:create_file_exclusive",
            ],
        },
        {
            "criterion": "D-R3",
            "dispose_transaction_object_present": False,
            "direct_rollback_helper_sites": [
                "runtime/executor_birth_secure_fs.py:1734:_win_dispose_created",
                "runtime/executor_birth_secure_fs.py:1811:_win_dispose_created",
            ],
        },
        {"criterion": "D-R8-approval", "session_loader_present": False},
        {"criterion": "D-R8-semantic", "session_loader_present": False},
        {
            "criterion": "D-R8-contention",
            "keystore_contract_uses_multiprocessing": False,
        },
        {
            "criterion": "D-C1",
            "portable_test_uses_spawn": False,
            "portable_test_uses_terminate_process": False,
        },
        {
            "criterion": "D-C2",
            "portable_test_has_synchronized_adversary": False,
        },
        {
            "criterion": "D-C3",
            "contract_test_has_synchronized_adversary": False,
        },
        {
            "criterion": "D-C4",
            "contract_test_uses_multiprocessing": False,
            "existing_name_is_in_process": True,
        },
    ]
    _require(
        results[4:] == expected_static,
        "static diagnostic vector differs from the frozen expectation",
    )
    print(json.dumps({
        "schema_version": 1,
        "frozen_inputs": fingerprints,
        "observations": results,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
