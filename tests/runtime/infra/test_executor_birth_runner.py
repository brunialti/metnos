from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import executor_birth_runner as runner


def test_v1_policy_is_fixed_and_complete():
    assert runner.V1_POLICY == runner.RunnerPolicy(
        phase_timeout_s=10.0,
        total_timeout_s=30.0,
        memory_limit_bytes=256 * 1024 * 1024,
        stdout_limit_bytes=1024 * 1024,
        stderr_limit_bytes=1024 * 1024,
        max_processes=32,
        termination_drain_s=2.0,
    )


def test_sandbox_environment_is_constructed_from_constants(monkeypatch):
    monkeypatch.setenv("METNOS_SECRET_FROM_HOST", "must-not-leak")
    monkeypatch.setenv("HOME", "/sensitive/home")
    assert "METNOS_SECRET_FROM_HOST" not in runner.SANDBOX_ENV
    assert runner.SANDBOX_ENV["HOME"] == "/work"
    assert dict(runner.SANDBOX_ENV) == {
        "HOME": "/work",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
        "TZ": "UTC",
    }


def test_fixture_language_materializes_only_declared_operations(tmp_path):
    runner.materialize_fixture(tmp_path, (
        runner.FixtureOp(runner.FixtureOpKind.MKDIR, "data"),
        runner.FixtureOp(runner.FixtureOpKind.WRITE_BYTES, "data/raw.bin", b"\x00x"),
        runner.FixtureOp(runner.FixtureOpKind.SEED_JSON, "seed.json", {"b": 2, "a": 1}),
    ))
    assert (tmp_path / "data/raw.bin").read_bytes() == b"\x00x"
    assert (tmp_path / "seed.json").read_text() == '{"a":1,"b":2}\n'
    assert json.loads((tmp_path / "seed.json").read_text()) == {"a": 1, "b": 2}


@pytest.mark.parametrize("path", [
    "", ".", "..", "../escape", "/absolute", "a/../b", "a\\b", "a\x00b",
])
def test_fixture_rejects_non_relative_or_ambiguous_paths(path):
    with pytest.raises(runner.RunnerInputError, match="fixture_path_invalid"):
        runner.validate_fixture_ops((
            runner.FixtureOp(runner.FixtureOpKind.MKDIR, path),
        ))


def test_fixture_rejects_links_and_parent_creation_by_construction(tmp_path):
    outside = tmp_path.parent / f"outside-birth-runner-{tmp_path.name}"
    outside.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(runner.RunnerInputError, match="fixture_parent_invalid"):
        runner.materialize_fixture(tmp_path, (
            runner.FixtureOp(runner.FixtureOpKind.WRITE_BYTES, "link/x", b"bad"),
        ))
    assert not (outside / "x").exists()


def test_fixture_rejects_wrong_payloads_and_duplicates():
    bad = (
        runner.FixtureOp(runner.FixtureOpKind.MKDIR, "same"),
        runner.FixtureOp(runner.FixtureOpKind.WRITE_BYTES, "same", b"x"),
    )
    with pytest.raises(runner.RunnerInputError, match="fixture_path_duplicate"):
        runner.validate_fixture_ops(bad)
    with pytest.raises(runner.RunnerInputError, match="fixture_bytes_payload"):
        runner.validate_fixture_ops((
            runner.FixtureOp(runner.FixtureOpKind.WRITE_BYTES, "x", "not-bytes"),
        ))
    with pytest.raises(runner.RunnerInputError, match="fixture_json_payload"):
        runner.validate_fixture_ops((
            runner.FixtureOp(runner.FixtureOpKind.SEED_JSON, "x", float("nan")),
        ))


def test_bwrap_command_contains_every_required_namespace(tmp_path):
    command = runner._bwrap_command("/usr/bin/bwrap", tmp_path, ("/usr/bin/true",))
    for flag in (
        "--unshare-net", "--unshare-pid", "--unshare-user",
        "--unshare-ipc", "--unshare-uts", "--clearenv",
    ):
        assert flag in command
    assert "--share-net" not in command
    assert "/sensitive/home" not in command


def test_linux_without_bwrap_is_typed_unavailable(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    result = runner.run_birth_phase(("/usr/bin/true",))
    assert result.status is runner.RunnerStatus.UNAVAILABLE
    assert result.error_code == "bwrap_unavailable"
    assert result.attestation.sandboxed is False
    assert result.attestation.termination_attested is False


def test_linux_without_delegated_cgroup_never_falls_back(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        runner, "_cgroup_v2_delegate", lambda: (None, "cgroup_delegate_missing"),
    )
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("host execution fallback")

    monkeypatch.setattr(runner, "run_bounded_subprocess", forbidden)
    result = runner.run_birth_phase(("/usr/bin/true",))
    assert result.status is runner.RunnerStatus.UNAVAILABLE
    assert result.error_code == "cgroup_delegate_missing"
    assert called is False


def test_windows_backend_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")
    result = runner.run_birth_phase(("python.exe", "-V"))
    assert result.status is runner.RunnerStatus.UNAVAILABLE
    assert result.error_code == "windows_backend_unattested"
    assert result.attestation.backend == "windows"
    assert result.attestation.termination_attested is False


@pytest.mark.parametrize("command", [(), "echo hi", ("",), ("ok\x00bad",)])
def test_command_schema_is_closed(command):
    with pytest.raises(runner.RunnerInputError, match="command_invalid"):
        runner.run_birth_phase(command)


def test_shell_setup_and_teardown_are_not_part_of_public_api():
    import inspect

    parameters = inspect.signature(runner.run_birth_phase).parameters
    assert set(parameters) == {"command", "fixture_ops", "phase", "deadline"}
    assert not {"shell", "setup", "teardown", "env", "policy"} & set(parameters)


def test_shared_deadline_cannot_exceed_fixed_total_budget(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(runner.time, "monotonic", lambda: now)
    deadline = runner.begin_birth_deadline()
    assert deadline.expires_at - deadline.started_at == runner.TOTAL_TIMEOUT_S
    now = deadline.expires_at
    result = runner.run_birth_phase(("/usr/bin/true",), deadline=deadline)
    assert result.status is runner.RunnerStatus.UNAVAILABLE
    assert result.error_code == "total_timeout"


def test_caller_cannot_extend_shared_deadline():
    deadline = runner.BirthDeadline(1.0, 1.0 + runner.TOTAL_TIMEOUT_S + 1.0)
    with pytest.raises(runner.RunnerInputError, match="deadline_invalid"):
        runner.run_birth_phase(("/usr/bin/true",), deadline=deadline)


def test_setup_handshake_is_strict_and_core_owned(tmp_path):
    status = tmp_path / "status.json"
    status.write_text('{"child_started":true,"exit_code":17}', encoding="utf-8")
    assert runner._read_setup_handshake(status) == (True, 17)
    status.write_text(
        '{"child_started":true,"exit_code":0,"candidate_claim":true}',
        encoding="utf-8",
    )
    assert runner._read_setup_handshake(status) == (False, None)


def _install_fake_linux_backend(monkeypatch, tmp_path, *, handshake):
    delegate = tmp_path / "delegate"
    delegate.mkdir()
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(runner, "_cgroup_v2_delegate", lambda: (delegate, None))
    monkeypatch.setattr(runner, "_write_control", lambda _path, _value: None)
    monkeypatch.setattr(runner, "_tree_empty", lambda _scope: True)

    def fake_run(command, **_kwargs):
        status_path = Path(command[5])
        if handshake is not None:
            status_path.write_text(json.dumps(handshake), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner, "run_bounded_subprocess", fake_run)


def test_candidate_failure_requires_successful_core_setup_handshake(monkeypatch, tmp_path):
    _install_fake_linux_backend(
        monkeypatch, tmp_path,
        handshake={"child_started": True, "exit_code": 17},
    )
    result = runner.run_birth_phase(("/bin/false",))
    assert result.status is runner.RunnerStatus.FAILED
    assert result.error_code == "candidate_process_failed"
    assert result.returncode == 17
    assert result.attestation.sandboxed is True


def test_missing_setup_handshake_is_unavailable_not_candidate_failure(monkeypatch, tmp_path):
    _install_fake_linux_backend(monkeypatch, tmp_path, handshake=None)
    result = runner.run_birth_phase(("/bin/false",))
    assert result.status is runner.RunnerStatus.UNAVAILABLE
    assert result.error_code == "sandbox_setup_unattested"
    assert result.returncode is None
    assert result.attestation.sandboxed is False


def _real_linux_result(command):
    if not runner.sys.platform.startswith("linux"):
        pytest.skip("Linux-only isolation proof")
    result = runner.run_birth_phase(command)
    if result.status is runner.RunnerStatus.UNAVAILABLE:
        pytest.skip(f"complete Linux backend unavailable: {result.error_code}")
    return result


def test_real_linux_sandbox_cannot_see_undeclared_host_files():
    result = _real_linux_result((
        "/bin/sh", "-c", "test ! -e /etc/passwd && test ! -e /opt/metnos",
    ))
    assert result.status is runner.RunnerStatus.PASSED
    assert result.attestation.sandboxed is True
    assert result.attestation.network_unshared is True


def test_real_linux_sandbox_terminates_detached_descendants():
    result = _real_linux_result((
        "/bin/sh", "-c", "sleep 60 </dev/null >/dev/null 2>&1 & exit 0",
    ))
    assert result.status is runner.RunnerStatus.PASSED
    assert result.attestation.tree_empty is True
    assert result.attestation.termination_attested is True


def test_real_linux_candidate_exit_is_not_misreported_as_setup_failure():
    result = _real_linux_result(("/bin/sh", "-c", "exit 17"))
    assert result.status is runner.RunnerStatus.FAILED
    assert result.error_code == "candidate_process_failed"
    assert result.returncode == 17
    assert result.attestation.sandboxed is True
