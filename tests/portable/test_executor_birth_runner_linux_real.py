from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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


def _group4_support():
    portable = Path(__file__).resolve().parent
    if str(portable) not in sys.path:
        sys.path.insert(0, str(portable))
    from rm0008_2b import support
    return support


def _group6c_systemd_test_module():
    portable = Path(__file__).resolve().parent
    if str(portable) not in sys.path:
        sys.path.insert(0, str(portable))
    import test_executor_birth_systemd
    return test_executor_birth_systemd


def _group6c_activation_test_module():
    portable = Path(__file__).resolve().parent
    if str(portable) not in sys.path:
        sys.path.insert(0, str(portable))
    import test_executor_birth_systemd_activation
    return test_executor_birth_systemd_activation


def _registered_backend(runner):
    """Register the real bwrap and the real interpreter, as an operator would.

    The backend no longer accepts whatever ``PATH`` names at that instant, so a
    real proof has to say which two programs it authorises.
    """
    import shutil
    import sys

    found = shutil.which("bwrap")
    if not found:
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail("real Linux Birth runner unavailable: bwrap absent")
        pytest.skip("real Linux Birth runner unavailable: bwrap absent")
    bwrap = Path(found).resolve()
    interpreter = Path(sys.executable).resolve()
    return runner.LinuxSandboxRegistry(
        bwrap_path=bwrap,
        bwrap_binary_hash=runner._binary_digest_v1(bwrap),
        interpreter_path=interpreter,
        interpreter_binary_hash=runner._binary_digest_v1(interpreter),
    )


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
        linux_registry=_registered_backend(runner),
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


def test_real_delegated_executor_dependency_sandbox(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing delegated job also certifies the G4-A runtime sandbox."""
    if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") != "1":
        pytest.skip("delegated Linux certification step only")
    monkeypatch.setenv("METNOS_REQUIRE_REAL_EXECUTOR_SANDBOX", "1")
    _group4_support().exercise_authenticated_dependency_subprocess(tmp_path)


def test_root_owned_deployment_authorities_deny_service_identity() -> None:
    """G5-A: real root ownership, cold load and a real unprivileged denial."""
    if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") != "1":
        pytest.skip("delegated Linux certification step only")
    assert os.geteuid() == 0, "delegated ownership certification must run as root"

    repository = Path(__file__).resolve().parents[2]
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    from install.birth_ownership_authority_provisioner import (
        _provision_ownership_authorities_at_v1,
    )
    from executor_birth_ownership_authorities import (
        OwnershipAuthorityError, _PRIVATE_BASENAMES, _REGISTRY_BASENAMES,
        _load_private_at_v1, _root_owned_chain,
    )
    import pwd

    with tempfile.TemporaryDirectory(
        prefix="metnos-rm0008-g5a-", dir="/var/lib",
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        _root_owned_chain(root)
        _provision_ownership_authorities_at_v1(
            root, forbidden_public_keys=(), root_owned=True,
        )
        directory = root / "authorities-v1"
        cold = _load_private_at_v1(directory, root_owned=True)
        assert len({
            cold.distribution_private.public_key().public_bytes_raw(),
            cold.cutover_private.public_key().public_bytes_raw(),
            cold.head_private.public_key().public_bytes_raw(),
        }) == 3
        for basename in _PRIVATE_BASENAMES.values():
            info = (directory / basename).stat()
            assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, 0, 0o600)
        for basename in _REGISTRY_BASENAMES.values():
            info = (directory / basename).stat()
            assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, 0, 0o644)

        service = next((entry for entry in pwd.getpwall()
                        if 1000 <= entry.pw_uid < 65534), None)
        assert service is not None, "no unprivileged service identity available"
        public = directory / _REGISTRY_BASENAMES["distribution"]
        private = directory / _PRIVATE_BASENAMES["distribution"]

        def demote() -> None:
            os.setgroups([])
            os.setgid(service.pw_gid)
            os.setuid(service.pw_uid)

        probe = """
import json, pathlib, sys
public, private = map(pathlib.Path, sys.argv[1:])
def denied(operation):
    try:
        operation()
    except OSError:
        return True
    return False
result = {
    'public_read': bool(public.read_bytes()),
    'private_read_denied': denied(private.read_bytes),
    'public_write_denied': denied(lambda: public.write_bytes(b'tamper')),
    'rename_denied': denied(lambda: public.rename(public.with_suffix('.moved'))),
    'remove_denied': denied(public.unlink),
    'create_denied': denied(lambda: (public.parent / 'extra').write_bytes(b'x')),
}
print(json.dumps(result, sort_keys=True))
"""
        observed = subprocess.run(
            [sys.executable, "-c", probe, str(public), str(private)],
            capture_output=True, text=True, timeout=10, check=True,
            preexec_fn=demote,
        )
        assert json.loads(observed.stdout) == {
            "create_denied": True,
            "private_read_denied": True,
            "public_read": True,
            "public_write_denied": True,
            "remove_denied": True,
            "rename_denied": True,
        }
        _load_private_at_v1(directory, root_owned=True)
        os.chown(private, service.pw_uid, service.pw_gid)
        with pytest.raises(OwnershipAuthorityError, match="authority_unsafe"):
            _load_private_at_v1(directory, root_owned=True)


def test_signed_isolated_systemd_cell_daemon_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6-C: reuse the frozen workflow's disposable root systemd runner."""
    if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") != "1":
        pytest.skip("delegated Linux certification step only")
    monkeypatch.setenv("METNOS_REQUIRE_REAL_G6C_SYSTEMD", "1")
    module = _group6c_systemd_test_module()
    module.test_signed_isolated_cell_daemon_reload_on_disposable_vm(tmp_path)


def test_signed_systemd_cell_denies_then_admits_real_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6-C3: execute the installed signed gate in the same disposable VM."""
    if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") != "1":
        pytest.skip("delegated Linux certification step only")
    monkeypatch.setenv("METNOS_REQUIRE_REAL_G6C_SYSTEMD", "1")
    module = _group6c_activation_test_module()
    module.test_signed_systemd_cell_denies_then_admits_real_timer(tmp_path)


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
        linux_registry=_registered_backend(runner),
    )
    _require_real(result)
    assert result.status is runner.RunnerStatus.FAILED
    assert result.error_code == "phase_timeout"
    assert result.attestation.tree_empty
    assert result.attestation.termination_attested
    assert result.attestation.cgroup_path is not None
    assert not Path(result.attestation.cgroup_path).exists()
