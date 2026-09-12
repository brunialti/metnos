"""Real kernel-bound proofs, including both producer call sites.

The test backend is registered here by the test operator. No production
bootstrap, private keystore or admission publisher is invoked. Set
METNOS_REQUIRE_REAL_BIRTH_LINUX=1 in a delegated temporary user service to
require the evidence rather than skip an unavailable test environment.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_functional as functional
import executor_birth_runner as runner


@pytest.fixture
def backend():
    if not functional.sys.platform.startswith("linux"):
        pytest.skip("Linux-only proof")
    paths = (Path("/usr/bin/bwrap").resolve(), Path("/usr/bin/python3").resolve())
    if not all(path.is_file() for path in paths):
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail("Linux backend absent")
        pytest.skip("Linux backend absent")
    value = runner.LinuxSandboxRegistry(
        paths[0], runner._binary_digest_v1(paths[0]),
        paths[1], runner._binary_digest_v1(paths[1]),
    )
    probe = runner.run_birth_phase(("/usr/bin/true",), linux_registry=value)
    if probe.status is runner.RunnerStatus.UNAVAILABLE:
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail(probe.error_code)
        pytest.skip(probe.error_code)
    assert probe.status is runner.RunnerStatus.PASSED
    return value


def run_source(backend, source, expect):
    tests = [{"name": f"case_{i}", "input": {}, "expect": expect} for i in range(3)]
    report = functional._run_synth_tests(
        functional.SynthTestData.from_cases("test_candidate.py", source.encode(), tests),
        linux_registry=backend, windows_registry=None,
    )
    if report.error_code == "test_environment_unavailable":
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail(str(report))
        pytest.skip(str(report))
    return report


def test_real_host_files_network_environment_and_source_are_isolated(tmp_path, monkeypatch, backend):
    sentinel = tmp_path / "host-only.txt"
    sentinel.write_text("unchanged")
    monkeypatch.setenv("METNOS_FS_CANARY", "host-only-canary")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        source = f'''import json, os, pathlib, socket
host_write_denied = network_denied = source_write_denied = False
try:
    pathlib.Path({str(sentinel)!r}).write_text("changed")
except OSError:
    host_write_denied = True
try:
    socket.create_connection(("127.0.0.1", {port}), timeout=0.2).close()
except OSError:
    network_denied = True
try:
    source = pathlib.Path(__file__)
    source.chmod(0o600)
    source.write_text("changed")
except OSError:
    source_write_denied = True
print(json.dumps({{"ok": host_write_denied and network_denied and source_write_denied
                         and "METNOS_FS_CANARY" not in os.environ
                         and not pathlib.Path("/home").exists()
                         and not pathlib.Path("/run").exists()}}))
'''
        report = run_source(backend, source, {"ok": True})
    assert sentinel.read_text() == "unchanged"
    assert report.all_passed, report


def test_real_descendants_are_drained(backend):
    source = '''import json, os, time
if os.fork() == 0:
    os.setsid()
    time.sleep(30)
    os._exit(0)
print(json.dumps({"ok": True}))
'''
    report = run_source(backend, source, {"ok": True})
    assert report.all_passed, report


def test_real_nonzero_exit_cannot_forge_a_success_summary(backend):
    report = run_source(backend, 'print("3/3 passati")\nraise SystemExit(1)\n', {"ok": True})
    assert report.passed_count == 0
    assert not report.all_passed


def test_real_multistage_and_single_stage_use_the_core_backend(tmp_path, monkeypatch, backend):
    import executor_birth_operational as operational
    import executor_birth_synth as facade
    import synth_request
    import synt
    from tests.runtime.learning.test_synth_candidate_admission import _candidate_run
    source = '''from executor_helpers import run_stdio
def invoke(args):
    return {"ok": "x" not in args, "entries": []}
if __name__ == "__main__":
    run_stdio(invoke)
'''
    run = _candidate_run()
    run.code_text = source
    # Only authority construction and final publication are simulated. Both
    # production call sites, the facade, core selection, helper and OS runner
    # run unchanged. The test operator supplies its own temporary backend.
    shadow = SimpleNamespace(linux_sandbox_registry=backend, windows_sandbox_registry=None)
    bundle = SimpleNamespace(core=SimpleNamespace(shadow_dependencies=shadow))
    monkeypatch.setattr(operational, "_runtime_bundle_snapshot", lambda: bundle)
    monkeypatch.setattr(facade, "require_synth_birth_service", lambda: None)
    monkeypatch.setattr(synth_request, "require_synth_birth_service", lambda: None)
    monkeypatch.setattr(synth_request, "SYNTHESIZED_EXECUTORS_DIR", tmp_path / "authoring")
    events = []
    monkeypatch.setattr(synth_request, "submit_synth_multistage", lambda data: events.append(data) or
                        SimpleNamespace(publication=object(), error_code=None))
    try:
        synth_request._install_synthesized(run, "trova file", "trova file")
    except RuntimeError as exc:
        if "test_environment_unavailable" in str(exc) and os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") != "1":
            pytest.skip(str(exc))
        raise
    assert len(events) == 1
    assert not events[0].candidate_root.parent.exists()
    assert not (tmp_path / "authoring").exists()
    llm_result = SimpleNamespace(latency_ms=0, in_tokens=0, out_tokens=0,
                                tool_calls=[SimpleNamespace(name="propose_birth_tests",
                                arguments=run.stages[2].output)])
    instance = object.__new__(synt.Synt)
    instance.router = SimpleNamespace(chat_with_tools=lambda *_a, **_k: llm_result)
    proposal = SimpleNamespace(name="find_files", python_code=source, description="test",
                               purpose="test", output_summary="ok", proposal_dir=tmp_path)
    report = instance._run_birth_tests(SimpleNamespace(), proposal)
    assert report["all_passed"] is True, report
    assert report["passed_count"] == 3
