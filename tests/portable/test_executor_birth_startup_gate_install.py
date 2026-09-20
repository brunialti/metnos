from __future__ import annotations

import os
import stat
import sys

import pytest

from executor_birth_distribution_assembler import DistributionAssemblerError
from executor_birth_ownership_coordinator import _deployment_lock_for_test_v1
from executor_birth_startup_gate import (
    _exclusive_startup_gate_for_test_v1,
    _require_exclusive_startup_gate_session_for_test_v1,
)
from install.executor_birth_startup_gate import (
    _install_startup_gate_for_test_v1, install_startup_gate_v1,
)


LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux startup gate",
)


@LINUX_ONLY
def test_installer_creates_reopens_and_locks_exact_gate(tmp_path):
    ownership_root = tmp_path / "ownership"
    runtime_parent = tmp_path / "run"
    runtime_parent.mkdir(mode=0o755)
    runtime_root = runtime_parent / "metnos-executor-birth-v1"

    with _deployment_lock_for_test_v1(ownership_root) as deployment:
        first = _install_startup_gate_for_test_v1(
            deployment, ownership_root, runtime_root,
        )
        second = _install_startup_gate_for_test_v1(
            deployment, ownership_root, runtime_root,
        )
        assert first == second
        assert stat.S_IMODE(runtime_root.stat().st_mode) == 0o700
        assert stat.S_IMODE(first.gate_path.stat().st_mode) == 0o600
        assert first.gate_path.stat().st_size == 0
        with _exclusive_startup_gate_for_test_v1(
            first.gate_path,
        ) as startup:
            _require_exclusive_startup_gate_session_for_test_v1(
                startup, first.gate_path,
            )


@LINUX_ONLY
@pytest.mark.parametrize("foreign", ("extra", "gate-mode"))
def test_installer_rejects_foreign_runtime_state(tmp_path, foreign):
    ownership_root = tmp_path / "ownership"
    runtime_parent = tmp_path / "run"
    runtime_parent.mkdir(mode=0o755)
    runtime_root = runtime_parent / "metnos-executor-birth-v1"
    runtime_root.mkdir(mode=0o700)
    gate = runtime_root / "startup-v1.lock"
    gate.touch(mode=0o600)
    if foreign == "extra":
        (runtime_root / "other").write_bytes(b"x")
    else:
        gate.chmod(0o644)

    with _deployment_lock_for_test_v1(ownership_root) as deployment:
        with pytest.raises(DistributionAssemblerError):
            _install_startup_gate_for_test_v1(
                deployment, ownership_root, runtime_root,
            )


@LINUX_ONLY
def test_product_installer_never_accepts_a_test_session(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as deployment:
        with pytest.raises(DistributionAssemblerError):
            install_startup_gate_v1(deployment)
