from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux systemd startup preparation", allow_module_level=True)

from executor_birth_distribution_assembler import DistributionAssemblerError
from install.executor_birth_startup_gate import (
    STARTUP_BOOT_RULE_NAME_V1, STARTUP_BOOT_RULE_V1, _install_boot_rule_v1,
)


def test_boot_rule_publication_is_exact_and_preserves_existing_inode(tmp_path):
    owner = (os.geteuid(), os.getegid())
    _install_boot_rule_v1(tmp_path, owner, lambda: None)
    target = tmp_path / STARTUP_BOOT_RULE_NAME_V1
    before = target.stat()
    _install_boot_rule_v1(tmp_path, owner, lambda: None)
    assert target.read_bytes() == STARTUP_BOOT_RULE_V1
    assert target.stat().st_ino == before.st_ino
    assert stat.S_IMODE(before.st_mode) == 0o644
    assert before.st_nlink == 1
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("mutation", ["content", "mode", "link", "directory"])
def test_boot_rule_refuses_existing_foreign_objects(tmp_path, mutation):
    target = tmp_path / STARTUP_BOOT_RULE_NAME_V1
    if mutation == "link":
        target.symlink_to(tmp_path / "absent")
    elif mutation == "directory":
        target.mkdir()
    else:
        target.write_bytes(STARTUP_BOOT_RULE_V1 if mutation == "mode" else b"bad")
        target.chmod(0o600 if mutation == "mode" else 0o644)
    before = target.lstat()
    with pytest.raises((DistributionAssemblerError, OSError)):
        _install_boot_rule_v1(tmp_path, (os.geteuid(), os.getegid()), lambda: None)
    assert target.lstat() == before


def test_boot_rule_requires_deployment_session_before_writing(tmp_path):
    def refused():
        raise RuntimeError("invalid deployment session")

    with pytest.raises(RuntimeError, match="invalid deployment session"):
        _install_boot_rule_v1(tmp_path, (os.geteuid(), os.getegid()), refused)
    assert not list(tmp_path.iterdir())


def test_real_tmpfiles_recreates_gate_after_cold_boot_and_preserves_live_inode(tmp_path):
    executable = shutil.which("systemd-tmpfiles")
    if executable is None:
        pytest.skip("requires the real systemd-tmpfiles command")
    root = tmp_path / "isolated-host"
    (root / "run").mkdir(parents=True)
    rules = tmp_path / STARTUP_BOOT_RULE_NAME_V1
    # Only ownership is translated for an unprivileged fake host; production
    # publishes the exact root-owned rule checked by the previous tests.
    rules.write_bytes(STARTUP_BOOT_RULE_V1.replace(
        b" 0 0 -", f" {os.geteuid()} {os.getegid()} -".encode(),
    ))
    gate = root / "run/metnos-executor-birth-v1/startup-v1.lock"
    command = [executable, "--create", f"--root={root}", str(rules)]
    subprocess.run(command, check=True, capture_output=True)
    assert not gate.exists(), "boot-only rules must not run during normal cleanup"
    for _ in range(2):
        subprocess.run([*command, "--boot"], check=True, capture_output=True)
        assert gate.read_bytes() == b""
        assert stat.S_IMODE(gate.stat().st_mode) == 0o600
        assert stat.S_IMODE(gate.parent.stat().st_mode) == 0o700
        with gate.open("rb") as held:
            import fcntl

            fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
            subprocess.run([*command, "--boot"], check=True, capture_output=True)
            assert gate.stat().st_ino == os.fstat(held.fileno()).st_ino
            with gate.open("rb") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gate.unlink()
        gate.parent.rmdir()


def test_production_rule_has_no_truncation_cleanup_or_recursive_operations():
    assert STARTUP_BOOT_RULE_V1.decode().splitlines() == [
        "d! /run/metnos-executor-birth-v1 0700 0 0 -",
        "f! /run/metnos-executor-birth-v1/startup-v1.lock 0600 0 0 -",
    ]
