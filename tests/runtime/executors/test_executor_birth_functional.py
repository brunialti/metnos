from __future__ import annotations

import inspect
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_functional as functional
import executor_birth_runner as runner
import executor_birth_synth as facade


def cases():
    return [{"name": f"case_{i}", "input": {"entries": [i]},
             "expect": {"ok": True}} for i in range(3)]


def data():
    return functional.SynthTestData.from_cases("find_texts.py", b"pass\n", cases())


def registry():
    return runner.LinuxSandboxRegistry(
        Path("/usr/bin/bwrap"), "a" * 64, Path("/usr/bin/python3.12"), "b" * 64,
    )


def completed(stdout='{"ok": true}', **changes):
    result = runner.RunnerResult(
        runner.RunnerStatus.PASSED, None, 0, stdout, "", 0.01,
        runner.ProcessAttestation(
            "linux-bwrap-cgroup-v2", True, True, True, True, True, True,
            True, "/test-scope", True, True,
        ),
    )
    return replace(result, **changes)


def run(monkeypatch, result):
    monkeypatch.setattr(functional.sys, "platform", "linux")
    monkeypatch.setattr(functional, "run_birth_phase", lambda *_a, **_k: result)
    return functional._run_synth_tests(data(), linux_registry=registry(), windows_registry=None)


def test_public_data_has_no_authority_or_host_path():
    assert set(inspect.signature(facade.validate_synth_tests).parameters) == {"data"}
    assert set(functional.SynthTestData.__dataclass_fields__) == {
        "source_name", "source_bytes", "cases_json",
    }
    assert not functional.SynthTestReport().all_passed


@pytest.mark.parametrize("field", ["setup", "teardown", "env", "reference", "command", "backend"])
@pytest.mark.parametrize("value", ["", "echo forbidden"])
def test_unsupported_fields_are_rejected_even_when_empty(field, value):
    candidate = cases()
    candidate[0][field] = value
    with pytest.raises(ValueError, match="fields_invalid"):
        functional.SynthTestData.from_cases("find_texts.py", b"pass\n", candidate)


@pytest.mark.parametrize("bad", [[], [{}], cases() * 3, "not a list"])
def test_empty_short_or_unbounded_cases_are_rejected(bad):
    with pytest.raises(ValueError):
        functional.SynthTestData.from_cases("find_texts.py", b"pass\n", bad)


def test_empty_expectation_duplicate_name_and_nonfinite_input_are_rejected():
    for mutate in (
        lambda value: value[0].update(expect={}),
        lambda value: value[1].update(name=value[0]["name"]),
        lambda value: value[0].update(input={"x": float("nan")}),
    ):
        value = cases()
        mutate(value)
        with pytest.raises(ValueError):
            functional.SynthTestData.from_cases("find_texts.py", b"pass\n", value)


@pytest.mark.parametrize("name", ["../escape.py", "/tmp/escape.py", "x/y.py", "x\\y.py", "_metnos_functional_v1.py"])
def test_source_cannot_choose_a_host_path_or_harness(name):
    with pytest.raises(ValueError, match="source_name_invalid"):
        functional.SynthTestData.from_cases(name, b"pass\n", cases())


@pytest.mark.parametrize("stdout", [
    "", "=== 0/0 passati ===", "=== 3/3 passati ===", "[]",
    '{"ok":true}\n=== 3/3 passati ===', '{"ok":false,"ok":true}',
    '{"ok":true,"value":NaN}', '{"ok":1}',
])
def test_empty_forged_or_ambiguous_output_never_passes(monkeypatch, stdout):
    report = run(monkeypatch, completed(stdout))
    assert not report.all_passed
    assert report.passed_count == 0


def test_exit_failure_cannot_be_overridden_by_output_or_status(monkeypatch):
    report = run(monkeypatch, completed(returncode=1))
    assert not report.all_passed
    assert report.passed_count == 0


def test_nonempty_json_checked_by_trusted_matchers_passes(monkeypatch):
    report = run(monkeypatch, completed())
    assert report.all_passed
    assert report.passed_count == 3
    assert len(report.tests) == 3


@pytest.mark.parametrize("field", [
    "sandboxed", "network_unshared", "pid_unshared", "user_unshared",
    "ipc_unshared", "uts_unshared", "cgroup_v2", "tree_empty", "termination_attested",
])
def test_missing_isolation_evidence_blocks_success(monkeypatch, field):
    result = completed()
    report = run(monkeypatch, replace(
        result, attestation=replace(result.attestation, **{field: False}),
    ))
    assert report.error_code == "test_environment_unavailable"
    assert not report.all_passed


def test_one_fixed_backend_budget_support_and_private_fixture(monkeypatch):
    seen = []
    monkeypatch.setattr(functional.sys, "platform", "linux")
    monkeypatch.setenv("SECRET_CANARY", "never copied")
    monkeypatch.setenv("METNOS_UNDO", "1")
    def capture(command, **kwargs):
        seen.append((command, {**kwargs, "candidate_files": dict(kwargs["candidate_files"])}))
        return completed()
    monkeypatch.setattr(functional, "run_birth_phase", capture)
    selected = registry()
    result = functional._run_synth_tests(data(), linux_registry=selected, windows_registry=None)
    assert result.all_passed
    assert len({id(item[1]["deadline"]) for item in seen}) == 1
    for command, kwargs in seen:
        assert command[0] == str(selected.interpreter_path)
        assert kwargs["linux_registry"] is selected
        assert set(kwargs) == {"candidate_files", "candidate_id", "linux_registry",
                               "windows_registry", "deadline", "fixture_ops"}
        files = kwargs["candidate_files"]
        assert set(files) == {"find_texts.py", functional._HARNESS_NAME,
                              functional._INPUT_NAME, "install/data/i18n_seed.sqlite"} | {
            "runtime/" + name for name in functional.FUNCTIONAL_SUPPORT_FILES_V1
        }
        assert not any(b"never copied" in value for value in files.values())
        assert json.loads(files[functional._INPUT_NAME])["entries"]
        assert {op.path for op in kwargs["fixture_ops"]} == {
            "fixture", "fixture/input.txt", "fixture/empty.txt", "fixture/output",
        }


def test_no_bundle_or_backend_cannot_fall_back(monkeypatch):
    import executor_birth_operational as operational
    monkeypatch.setattr(operational, "_runtime_bundle_snapshot", lambda: None)
    monkeypatch.setattr(facade, "require_synth_birth_service", lambda: None)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("host execution fallback")
    monkeypatch.setattr(functional, "run_birth_phase", forbidden)
    assert facade.validate_synth_tests(data()).error_code == "test_environment_unavailable"
    assert functional._run_synth_tests(
        data(), linux_registry=None, windows_registry=None,
    ).error_code == "test_environment_unavailable"


def test_core_supplies_only_its_backend(monkeypatch):
    import executor_birth_operational as operational
    backend = registry()
    shadow = SimpleNamespace(linux_sandbox_registry=backend, windows_sandbox_registry=None)
    bundle = SimpleNamespace(core=SimpleNamespace(shadow_dependencies=shadow))
    monkeypatch.setattr(operational, "_runtime_bundle_snapshot", lambda: bundle)
    monkeypatch.setattr(facade, "require_synth_birth_service", lambda: None)
    seen = []
    monkeypatch.setattr(functional, "_run_synth_tests",
                        lambda value, **kw: seen.append((value, kw)) or functional.SynthTestReport())
    facade.validate_synth_tests(data())
    assert seen[0][1] == {"linux_registry": backend, "windows_registry": None}


def test_candidate_is_a_readonly_mount_not_just_mode_bits(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    command = runner._bwrap_command("/usr/bin/bwrap", tmp_path, ("/usr/bin/true",))
    position = command.index(str(candidate))
    assert command[position - 1:position + 2] == ("--ro-bind", str(candidate), "/work/candidate")


def test_matcher_and_support_sources_are_in_context_commitment():
    from executor_birth_context_v1 import CONTEXT_CATALOG_V1
    files = next(value[2] for value in CONTEXT_CATALOG_V1 if value[0] == "runner")
    assert {"executor_birth_functional.py", "test_runner.py"}.issubset(files)
    assert set(functional.FUNCTIONAL_SUPPORT_FILES_V1).issubset(files)


def test_real_functional_stdio_with_runtime_helpers_and_private_fixtures():
    if not functional.sys.platform.startswith("linux"):
        pytest.skip("Linux sandbox proof")
    paths = (Path("/usr/bin/bwrap").resolve(), Path("/usr/bin/python3").resolve())
    if not all(path.is_file() for path in paths):
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail("Linux backend absent")
        pytest.skip("Linux backend absent")
    backend = runner.LinuxSandboxRegistry(
        paths[0], runner._binary_digest_v1(paths[0]),
        paths[1], runner._binary_digest_v1(paths[1]),
    )
    source = b'''from executor_helpers import run_stdio
from pathlib import Path
def invoke(args):
    return {"ok": True, "content": Path("fixture/input.txt").read_text()}
if __name__ == "__main__":
    run_stdio(invoke)
'''
    value = cases()
    for case in value:
        case["expect"] = {"ok": True, "content_contains": "birth fixture"}
    report = functional._run_synth_tests(
        functional.SynthTestData.from_cases("read_texts.py", source, value),
        linux_registry=backend, windows_registry=None,
    )
    if report.error_code == "test_environment_unavailable":
        if os.environ.get("METNOS_REQUIRE_REAL_BIRTH_LINUX") == "1":
            pytest.fail(str(report))
        pytest.skip(str(report))
    assert report.all_passed, report
