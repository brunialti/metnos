"""Proofs for the bounded durable-executor transport."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from bounded_subprocess import (
    SubprocessOutputLimitExceeded,
    SubprocessTerminationError,
    run_bounded_subprocess,
)
from durable_workloads.models import ExecutionContext
from loader import Executor


def _run(code: str, *, payload: str = "", timeout: float = 2.0, cap: int = 4096):
    return run_bounded_subprocess(
        [sys.executable, "-c", code],
        input_text=payload,
        timeout_s=timeout,
        env=None,
        stdout_limit_bytes=cap,
        stderr_limit_bytes=cap,
    )


def _context() -> ExecutionContext:
    return ExecutionContext(
        owner_user_id="owner-bounded",
        workload_id="workload-bounded",
        revision_id="revision-bounded",
        stage_id="stage-bounded",
        unit_key="unit-bounded",
        attempt_id="attempt-bounded",
        priority="normal",
        resource_claims=tuple((name, 0) for name in (
            "cpu", "device", "llm", "local_io", "network_io", "vlm",
        )),
        deadline_at=(
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat(),
    )


def _executor(tmp_path) -> Executor:
    code = tmp_path / "bounded_fixture.py"
    code.write_text("print('{}')", encoding="utf-8")
    return Executor(
        name="bounded_fixture",
        version="1",
        description="",
        affinity=[],
        args_schema={},
        capabilities=[],
        tests=[],
        code_path=code,
        manifest_path=tmp_path / "manifest.toml",
        signed_by="test",
    )


def test_bounded_transport_drains_input_stdout_and_stderr_without_deadlock():
    result = _run(
        "import sys; data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data[::-1]); "
        "sys.stderr.buffer.write(b'notice')",
        payload="durable",
    )

    assert result.returncode == 0
    assert result.stdout == "elbarud"
    assert result.stderr == "notice"


@pytest.mark.parametrize(
    ("file_descriptor", "stream"),
    [(1, "stdout"), (2, "stderr")],
)
def test_output_breach_stops_the_process_and_retains_only_the_cap(
    file_descriptor: int,
    stream: str,
):
    with pytest.raises(SubprocessOutputLimitExceeded) as raised:
        _run(
            f"import os, time; os.write({file_descriptor}, b'x' * 1048576); "
            "time.sleep(10)",
            cap=1024,
        )

    assert raised.value.stream == stream
    assert len(raised.value.stdout.encode("utf-8")) <= 1024
    assert len(raised.value.stderr.encode("utf-8")) <= 1024


def test_one_deadline_covers_a_child_that_never_returns():
    with pytest.raises(subprocess.TimeoutExpired):
        _run("import time; time.sleep(10)", timeout=0.05)


def test_durable_invocation_maps_an_output_breach_to_a_localized_contract_error(
    tmp_path,
    monkeypatch,
):
    import agent_runtime
    import bounded_subprocess
    import sandbox

    monkeypatch.setattr(
        sandbox,
        "wrap_command",
        lambda _executor, command, **_kwargs: command,
    )

    def overflow(cmd, **_kwargs):
        raise SubprocessOutputLimitExceeded(
            cmd=cmd,
            stream="stdout",
            limit_bytes=1024,
            stdout="x" * 1024,
            stderr="",
        )

    monkeypatch.setattr(
        bounded_subprocess,
        "run_bounded_subprocess",
        overflow,
    )
    result = agent_runtime._invoke_executor_impl(
        _executor(tmp_path),
        {},
        timeout_s=5,
        owner_user_id="owner-bounded",
        execution_context=_context(),
    )

    assert result == {
        "ok": False,
        "error_class": "contract_violation",
        "error_code": "ERR_DURABLE_RESULT_CONTRACT_VIOLATION",
        "error": result["error"],
    }
    assert isinstance(result["error"], str) and result["error"]


def test_durable_invocation_converts_subprocess_timeout_for_retry_classification(
    tmp_path,
    monkeypatch,
):
    import agent_runtime
    import bounded_subprocess
    import sandbox

    monkeypatch.setattr(
        sandbox,
        "wrap_command",
        lambda _executor, command, **_kwargs: command,
    )

    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout_s"])

    monkeypatch.setattr(
        bounded_subprocess,
        "run_bounded_subprocess",
        timeout,
    )
    with pytest.raises(TimeoutError, match="durable executor deadline"):
        agent_runtime._invoke_executor_impl(
            _executor(tmp_path),
            {},
            timeout_s=5,
            owner_user_id="owner-bounded",
            execution_context=_context(),
        )


def test_ordinary_invocation_timeout_never_exposes_host_command(tmp_path, monkeypatch):
    import agent_runtime
    import sandbox
    from messages import get as msg

    monkeypatch.setattr(sandbox, "wrap_command", lambda *a, **k: ["bwrap", "/private/host/path"])
    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(agent_runtime.subprocess, "run", timeout)
    result = agent_runtime._invoke_executor_impl(_executor(tmp_path), {}, timeout_s=5)
    assert result == {
        "ok": False, "error_class": "timeout",
        "error": msg("ERR_EXECUTOR_TIMEOUT", tool="bounded_fixture", seconds=5),
    }
    assert "bwrap" not in result["error"]
    assert "/private" not in result["error"]


def test_unreaped_process_group_requires_attention_instead_of_another_spawn(
    tmp_path,
    monkeypatch,
):
    import agent_runtime
    import bounded_subprocess
    import sandbox

    monkeypatch.setattr(
        sandbox,
        "wrap_command",
        lambda _executor, command, **_kwargs: command,
    )
    monkeypatch.setattr(
        bounded_subprocess,
        "run_bounded_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SubprocessTerminationError("synthetic unreaped process")
        ),
    )

    result = agent_runtime._invoke_executor_impl(
        _executor(tmp_path),
        {},
        timeout_s=5,
        owner_user_id="owner-bounded",
        execution_context=_context(),
    )

    assert result["ok"] is False
    assert result["error_class"] == "capability_unavailable"
    assert result["error_code"] == "ERR_DURABLE_EXECUTION_FAILED"


@pytest.mark.parametrize("field", ["stdout", "stderr"])
def test_output_limits_must_be_positive(field: str):
    values = {"stdout_limit_bytes": 1, "stderr_limit_bytes": 1}
    values[f"{field}_limit_bytes"] = 0
    with pytest.raises(ValueError, match=f"{field}_limit_bytes"):
        run_bounded_subprocess(
            [sys.executable, "-c", "pass"],
            input_text="",
            timeout_s=1,
            env=None,
            **values,
        )
