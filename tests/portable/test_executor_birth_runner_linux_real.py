from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux cgroup-v2 certification",
)


def _runner():
    runtime = Path(__file__).resolve().parents[2] / "runtime"
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    import executor_birth_runner
    return executor_birth_runner


def _require_real(result) -> None:
    if result.status.value == "test_environment_unavailable":
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail(f"real Linux Birth runner unavailable: {result.error_code}")
        pytest.skip(f"real Linux Birth runner unavailable: {result.error_code}")


def test_real_linux_runner_hides_host_environment_paths_and_network() -> None:
    runner = _runner()
    source = b"""
import json, os, socket
network_denied = False
try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.2)
except OSError:
    network_denied = True
print(json.dumps({
    'host_secret_absent': 'METNOS_BIRTH_HOST_SECRET' not in os.environ,
    'home_absent': not os.path.exists('/home'),
    'network_denied': network_denied,
}, sort_keys=True))
"""
    os.environ["METNOS_BIRTH_HOST_SECRET"] = "must-not-cross-boundary"
    result = runner.run_birth_phase(
        ("/usr/bin/python3", "/work/candidate/main.py"),
        candidate_id="sha256:" + "a" * 64,
        candidate_files={"main.py": source},
    )
    _require_real(result)
    assert result.status is runner.RunnerStatus.PASSED, result.error_code
    assert json.loads(result.stdout) == {
        "home_absent": True,
        "host_secret_absent": True,
        "network_denied": True,
    }
    attestation = result.attestation
    assert (
        attestation.backend == "linux-bwrap-cgroup-v2"
        and attestation.sandboxed
        and attestation.network_unshared
        and attestation.pid_unshared
        and attestation.user_unshared
        and attestation.ipc_unshared
        and attestation.uts_unshared
        and attestation.cgroup_v2
        and attestation.tree_empty
        and attestation.termination_attested
    )
    assert attestation.cgroup_path is not None
    assert not Path(attestation.cgroup_path).exists()


def test_real_linux_timeout_terminates_child_and_grandchild_cgroup() -> None:
    runner = _runner()
    source = b"""
import subprocess, time
subprocess.Popen(['/usr/bin/python3', '-c',
                  'import subprocess,time; subprocess.Popen([\"/usr/bin/python3\",\"-c\",\"import time;time.sleep(60)\"]); time.sleep(60)'])
time.sleep(60)
"""
    started = time.monotonic()
    deadline = runner.BirthDeadline(started, started + 0.8)
    result = runner.run_birth_phase(
        ("/usr/bin/python3", "/work/candidate/main.py"),
        deadline=deadline,
        candidate_id="sha256:" + "b" * 64,
        candidate_files={"main.py": source},
    )
    _require_real(result)
    assert result.status is runner.RunnerStatus.FAILED
    assert result.error_code == "phase_timeout"
    assert result.attestation.tree_empty
    assert result.attestation.termination_attested
    assert result.attestation.cgroup_path is not None
    assert not Path(result.attestation.cgroup_path).exists()
