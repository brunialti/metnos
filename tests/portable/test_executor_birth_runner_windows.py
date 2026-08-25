from __future__ import annotations

import os
import sys

import pytest

import executor_birth_runner
import executor_birth_runner_windows as windows_runner


def test_windows_job_policy_is_fixed_and_host_environment_is_not_copied(monkeypatch):
    monkeypatch.setenv("METNOS_HOST_SECRET", "must-not-leak")
    assert windows_runner.WINDOWS_JOB_MEMORY_LIMIT_BYTES == 256 * 1024 * 1024
    assert windows_runner.WINDOWS_JOB_MAX_PROCESSES == 32
    assert windows_runner.WINDOWS_JOB_TIMEOUT_S == 10.0
    assert windows_runner.WINDOWS_JOB_DRAIN_S == 2.0
    assert "METNOS_HOST_SECRET" not in windows_runner.WINDOWS_PROBE_ENV


def test_job_probe_is_typed_unavailable_off_windows():
    if os.name == "nt":
        pytest.skip("non-Windows contract")
    result = windows_runner._run_job_object_probe((sys.executable, "-c", "pass"))
    assert result.available is False
    assert result.error_code == "windows_job_object_platform_unavailable"
    assert result.host_isolation_attested is False


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
def test_real_job_object_assigns_before_resume_and_drains_tree():
    result = windows_runner._run_job_object_probe((sys.executable, "-I", "-c", "pass"))
    assert result.available is True, result.error_code
    assert result.assigned_before_resume is True
    assert result.kill_on_job_close is True
    assert result.memory_limit_bytes == 256 * 1024 * 1024
    assert result.max_processes == 32
    assert result.active_processes == 0
    assert result.tree_empty is True
    # A Job Object is containment, not an AppContainer/restricted token.
    assert result.host_isolation_attested is False


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
def test_real_job_object_terminates_a_descendant_left_by_the_root():
    child = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])"
    )
    result = windows_runner._run_job_object_probe((sys.executable, "-I", "-c", child))
    assert result.available is True, result.error_code
    assert result.assigned_before_resume is True
    assert result.active_processes == 0
    assert result.tree_empty is True


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
def test_productive_gate_requires_the_core_owned_sandbox_registry():
    job = windows_runner._run_job_object_probe((sys.executable, "-I", "-c", "pass"))
    assert job.available is True, job.error_code
    result = executor_birth_runner.run_birth_phase((sys.executable, "-I", "-c", "pass"))
    assert result.status is executor_birth_runner.RunnerStatus.UNAVAILABLE
    assert result.error_code == "windows_sandbox_registry_unavailable"
    assert result.attestation.sandboxed is False
    assert result.attestation.termination_attested is False
