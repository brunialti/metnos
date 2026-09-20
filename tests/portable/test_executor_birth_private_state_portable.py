"""Portable checks for the durable one-shot Birth authority stores."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from executor_birth_bootstrap import (
    BirthBootstrapError,
    _resolve_private_db,
    _rollback_created_private_paths,
    _secure_approval_db,
    _secure_state_db,
)
from executor_birth_keystore import _harden_windows_private_acl


def test_private_database_configuration_is_relative_and_confined(
    tmp_path: Path,
) -> None:
    assert _resolve_private_db(
        tmp_path, "authority/approvals.sqlite",
    ) == tmp_path / "authority" / "approvals.sqlite"
    for rejected in (
        str((tmp_path / "outside.sqlite").resolve()),
        "../outside.sqlite",
        "authority\\approvals.sqlite",
        "one/two/approvals.sqlite",
        "authority/approvals.db",
    ):
        with pytest.raises(
            BirthBootstrapError, match="birth_approval_store_invalid",
        ):
            _resolve_private_db(tmp_path, rejected)


def test_private_database_rollback_never_removes_a_replacement(
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "config-root"
    config_root.mkdir(mode=0o700)
    if os.name == "nt":
        _harden_windows_private_acl(config_root)
    created = []
    approval = config_root / "authority" / "approvals.sqlite"
    _secure_approval_db(
        approval,
        config_dir=config_root,
        created_paths=created,
    )
    approval.unlink()
    approval.write_bytes(b"")
    if os.name == "posix":
        approval.chmod(0o600)
    else:
        _harden_windows_private_acl(approval)

    _rollback_created_private_paths(created)

    assert approval.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL and junction contract")
def test_windows_private_state_rejects_shared_acl_and_junction(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    _harden_windows_private_acl(private_root)

    valid = private_root / "valid-state"
    assert _secure_state_db(valid).is_file()

    shared = private_root / "shared"
    shared.mkdir(mode=0o700)
    _harden_windows_private_acl(shared)
    changed = subprocess.run(
        ["icacls", str(shared), "/grant", "*S-1-5-32-545:(OI)(CI)M"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stderr
    with pytest.raises(BirthBootstrapError, match="birth_state_permissions"):
        _secure_state_db(shared / "birth")
    assert not (shared / "birth").exists()

    target = private_root / "junction-target"
    target.mkdir(mode=0o700)
    _harden_windows_private_acl(target)
    junction = private_root / "junction-state"
    linked = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert linked.returncode == 0, linked.stderr
    with pytest.raises(BirthBootstrapError, match="birth_state_permissions"):
        _secure_state_db(junction)
    assert not (target / "producer-receipts.sqlite").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ancestor junction contract")
def test_windows_private_state_rejects_junction_above_anchor(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    _harden_windows_private_acl(real_parent)
    real_anchor = real_parent / "anchor"
    real_anchor.mkdir(mode=0o700)
    _harden_windows_private_acl(real_anchor)
    junction_parent = tmp_path / "junction-parent"
    linked = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction_parent), str(real_parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert linked.returncode == 0, linked.stderr

    with pytest.raises(BirthBootstrapError, match="birth_state_permissions"):
        _secure_state_db(junction_parent / "anchor" / "state")
    assert not (real_anchor / "state" / "producer-receipts.sqlite").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows private approval DACL contract")
def test_windows_private_approval_store_rejects_shared_read_acl(
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "config-root"
    config_root.mkdir(mode=0o700)
    _harden_windows_private_acl(config_root)
    approval = config_root / "authority" / "approvals.sqlite"
    assert _secure_approval_db(
        approval, config_dir=config_root,
    ) == approval

    changed = subprocess.run(
        ["icacls", str(approval), "/grant", "*S-1-5-32-545:R"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stderr
    with pytest.raises(
        BirthBootstrapError, match="birth_approval_store_permissions",
    ):
        _secure_approval_db(approval, config_dir=config_root)

    _harden_windows_private_acl(approval)
    sidecar = Path(str(approval) + "-wal")
    sidecar.write_bytes(b"")
    _harden_windows_private_acl(sidecar)
    changed = subprocess.run(
        ["icacls", str(sidecar), "/grant", "*S-1-5-32-545:R"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stderr
    with pytest.raises(
        BirthBootstrapError, match="birth_approval_store_permissions",
    ):
        _secure_approval_db(approval, config_dir=config_root)
