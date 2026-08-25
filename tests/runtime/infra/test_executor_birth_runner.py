from __future__ import annotations

import json
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
    assert set(parameters) == {"command", "fixture_ops", "phase"}
    assert not {"shell", "setup", "teardown", "env", "policy"} & set(parameters)
