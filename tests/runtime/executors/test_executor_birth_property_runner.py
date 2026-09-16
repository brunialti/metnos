import pytest
from types import MappingProxyType, SimpleNamespace

from executor_birth import ObservedCandidate
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_snapshot import CandidateSnapshot
from manifest_inventory import ContractId, ManifestOrigin
from executor_birth_properties import PropertyContractError, PropertyStatus
from executor_birth_property_runner import (
    ObservedPropertyRunner, PropertyCandidateProfile, PropertyRunResult,
    run_applicable_properties, run_property,
)
from executor_birth_runner import ProcessAttestation, RunnerResult, RunnerStatus
from executor_birth_runner import LinuxSandboxRegistry, WindowsSandboxRegistry


D = "sha256:" + "1" * 64


class Runner:
    def __init__(self, output=None, observations=None, error=None):
        self.output = output or {}
        self.observations = observations or {}
        self.error = error
        self.calls = []

    def run(self, case, *, fixture_id, isolation):
        self.calls.append((case.case_id, fixture_id, isolation))
        if self.error:
            raise self.error
        output = dict(self.output)
        if "fixture_count" in case.input_value:
            count = min(case.input_value["fixture_count"], case.input_value.get("limit", 999))
            output["entries"] = [{} for _ in range(count)]
        return PropertyRunResult(output, dict(self.observations), D)


def test_ids_resolve_only_from_closed_core_registries():
    with pytest.raises(PropertyContractError, match="property_unknown"):
        run_property("manifest.supplied.callable", PropertyCandidateProfile(), _runner=Runner())


def test_non_applicable_property_does_not_call_runner():
    runner = Runner()
    assert run_property("undo.round_trip", PropertyCandidateProfile(revertible=False), _runner=runner) == ()
    assert runner.calls == []


def test_cardinality_and_limit_generate_bounded_evidence():
    runner = Runner()
    cardinality = run_property("cardinality.zero_one_many", PropertyCandidateProfile(collection_output=True), _runner=runner)
    assert len(cardinality) == 3 and all(item.status is PropertyStatus.PASSED for item in cardinality)
    limits = run_property(
        "limit.zero_and_below_total",
        PropertyCandidateProfile(collection_output=True, limit_input=True),
        _runner=runner,
    )
    assert len(limits) == 2 and all(item.status is PropertyStatus.PASSED for item in limits)


def test_limit_without_a_structured_collection_is_not_applicable():
    runner = Runner()
    evidence = run_property(
        "limit.zero_and_below_total",
        PropertyCandidateProfile(limit_input=True),
        _runner=runner,
    )
    assert evidence == ()
    assert runner.calls == []


def test_oracle_failure_and_runner_unavailability_are_evidence_not_bypass():
    profile = PropertyCandidateProfile(output_schema=(("ok", "boolean"),))
    failed = run_property("output.schema.actual", profile, _runner=Runner({"wrong": True}))
    assert failed[0].status is PropertyStatus.FAILED
    unavailable = run_property("output.schema.actual", profile, _runner=Runner(error=RuntimeError("down")))
    assert unavailable[0].status is PropertyStatus.UNAVAILABLE


def test_all_applicable_keeps_property_evidence_distinct():
    evidence = run_applicable_properties(PropertyCandidateProfile(collection_output=True), _runner=Runner())
    assert {item.property_id for item in evidence} == {"cardinality.zero_one_many"}


def test_manifest_mapping_cannot_supply_applicability_or_runner():
    with pytest.raises(PropertyContractError, match="property_candidate_invalid"):
        run_property("undo.round_trip", {"revertible": True}, _runner=Runner())


def test_candidate_truncation_flag_cannot_replace_trusted_observation():
    profile = PropertyCandidateProfile(truncation_declared=True)
    forged = Runner({"entries": [{}, {}], "truncated": True,
                     "truncation_attested": True})
    assert run_property("truncation.contract", profile, _runner=forged)[0].status is PropertyStatus.FAILED
    trusted = Runner({"entries": [{}, {}], "truncated": True}, {"fixture_total": 3})
    assert run_property("truncation.contract", profile, _runner=trusted)[0].status is PropertyStatus.PASSED


def test_candidate_round_trip_flag_cannot_replace_runner_state_hashes():
    profile = PropertyCandidateProfile(revertible=True)
    forged = Runner({"state_round_trip_attested": True})
    assert run_property("undo.round_trip", profile, _runner=forged)[0].status is PropertyStatus.FAILED
    trusted = Runner({}, {
        "state_before_hash": D,
        "state_after_forward_hash": "sha256:" + "2" * 64,
        "state_after_undo_hash": D,
    })
    assert run_property("undo.round_trip", profile, _runner=trusted)[0].status is PropertyStatus.PASSED


def test_candidate_copy_flag_cannot_replace_runner_filesystem_trace():
    profile = PropertyCandidateProfile(destructive_with_undo=True)
    forged = Runner({"copy_precedes_delete_attested": True})
    assert run_property("delete.copy_before_delete", profile, _runner=forged)[0].status is PropertyStatus.FAILED
    trusted = Runner({}, {
        "filesystem_events": ["copy", "delete"],
        "source_before_hash": D,
        "recovery_copy_hash": D,
    })
    assert run_property("delete.copy_before_delete", profile, _runner=trusted)[0].status is PropertyStatus.PASSED


def test_output_schema_checks_core_declared_types_not_only_keys():
    profile = PropertyCandidateProfile(output_schema=(("ok", "boolean"), ("count", "integer")))
    assert run_property("output.schema.actual", profile, _runner=Runner({"ok": True, "count": 1}))[0].status is PropertyStatus.PASSED
    assert run_property("output.schema.actual", profile, _runner=Runner({"ok": 1, "count": True}))[0].status is PropertyStatus.FAILED


@pytest.mark.parametrize("observations", [
    {"state_before_hash": "sha256:a", "state_after_forward_hash": "sha256:b", "state_after_undo_hash": "sha256:a"},
    {"state_before_hash": D, "state_after_forward_hash": "sha256:" + "2" * 64, "state_after_undo_hash": None},
])
def test_round_trip_requires_three_canonical_non_null_digests(observations):
    evidence = run_property("undo.round_trip", PropertyCandidateProfile(revertible=True), _runner=Runner({}, observations))
    assert evidence[0].status is PropertyStatus.FAILED


def test_copy_before_delete_requires_two_canonical_non_null_equal_digests():
    observations = {"filesystem_events": ["copy", "delete"]}
    evidence = run_property("delete.copy_before_delete", PropertyCandidateProfile(destructive_with_undo=True), _runner=Runner({}, observations))
    assert evidence[0].status is PropertyStatus.FAILED


def test_malformed_runner_contract_is_not_downgraded_to_unavailability():
    class MalformedRunner:
        def run(self, *args, **kwargs):
            return {"output": {}}

    with pytest.raises(PropertyContractError, match="property_runner_result_invalid"):
        run_property(
            "output.schema.actual", PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
            _runner=MalformedRunner(),
        )


def test_runner_contract_error_is_not_reported_as_transport_unavailability():
    class ContractFailingRunner:
        def run(self, *args, **kwargs):
            raise PropertyContractError("property_runner_result_invalid", "trusted adapter")

    with pytest.raises(PropertyContractError, match="property_runner_result_invalid"):
        run_property(
            "output.schema.actual",
            PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
            _runner=ContractFailingRunner(),
        )


def _observed_candidate(tmp_path):
    manifest = b'[code]\nfiles=["candidate.py"]\n'
    snapshot = CandidateSnapshot(
        tmp_path, manifest, b"{}",
        MappingProxyType({"candidate.py": b"print('{}')\n"}),
    )
    return ObservedCandidate(
        ContractId(ManifestOrigin.USER, "x/manifest.toml"), snapshot,
        SimpleNamespace(candidate_id=D), ExecutorOrigin.HUMAN,
        RevisionAuthor.HUMAN, D,
    )


def _linux_registry(tmp_path):
    # Only for tests replacing the process runner; these are not live programs
    # or an attestation of a usable sandbox.
    return LinuxSandboxRegistry(
        tmp_path / "bwrap", D, tmp_path / "registered-python", D,
    )


def test_observed_runner_executes_exact_snapshot_with_closed_fixture(monkeypatch, tmp_path):
    observed = _observed_candidate(tmp_path)
    captured = {}
    attestation = ProcessAttestation(
        "linux-bwrap-cgroup-v2", True, True, True, True, True, True,
        True, "/scope", True, True,
    )

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return RunnerResult(RunnerStatus.PASSED, None, 0,
                            '{"output":{"entries":[]},"observations":{}}',
                            "", 0.1, attestation)

    monkeypatch.setattr("executor_birth_property_runner.sys.platform", "linux")
    monkeypatch.setattr("executor_birth_property_runner.run_birth_phase", fake_run)
    result = ObservedPropertyRunner(observed, linux_registry=_linux_registry(tmp_path)).run(
        __import__("executor_birth_property_runner").PropertyCase(
            "cardinality.0", {"fixture_count": 0}, {"count": 0}),
        fixture_id="bounded_collection", isolation="private_read_only",
    )
    assert result.output == {"entries": []}
    assert captured["candidate_id"] == D
    assert captured["candidate_files"]["candidate.py"] == observed.snapshot.code_files["candidate.py"]
    from executor_birth_functional import _support_files
    supports = _support_files()
    assert len(supports) == 8
    assert set(captured["candidate_files"]) == {
        "candidate.py", "_metnos_birth_property_harness_v1.py",
        "_metnos_birth_property_stdio_v1.py", "_metnos_birth_helper_model_v1.py",
        "helper", "package-app", *supports,
        "_metnos_birth_reverse_v1.py", "runtime/reverse_patterns.py",
        "runtime/reverse_patterns_patch.py", "runtime/playwright_sidecar/__init__.py",
        "runtime/playwright_sidecar/session_client.py", "runtime/playwright_sidecar/stealth.py",
    }
    assert all(captured["candidate_files"][name] == payload
               for name, payload in supports.items())
    assert set(observed.snapshot.code_files) == {"candidate.py"}
    request = next(op for op in captured["fixture_ops"] if op.path == "request.json")
    assert request.payload["case_id"] == "cardinality.0"


def test_observed_runner_ignores_candidate_self_attestation(monkeypatch, tmp_path):
    observed = _observed_candidate(tmp_path)
    attestation = ProcessAttestation(
        "linux-bwrap-cgroup-v2", True, True, True, True, True, True,
        True, "/scope", True, True,
    )
    forged = '{"output":{"entries":[],"fixture_total":999,"state_before_hash":"' + D + '"},"observations":{}}'
    monkeypatch.setattr("executor_birth_property_runner.sys.platform", "linux")
    monkeypatch.setattr(
        "executor_birth_property_runner.run_birth_phase",
        lambda *a, **k: RunnerResult(RunnerStatus.PASSED, None, 0, forged, "", 0.1, attestation),
    )
    result = ObservedPropertyRunner(observed, linux_registry=_linux_registry(tmp_path)).run(
        __import__("executor_birth_property_runner").PropertyCase(
            "truncation.boundary", {}, {"fixture_total": 3}),
        fixture_id="oversized_collection", isolation="private_read_only",
    )
    assert result.observations == {"fixture_total": 3}
    assert "state_before_hash" not in result.observations


def test_observed_runner_uses_registered_linux_interpreter_not_process_venv(
        monkeypatch, tmp_path):
    import executor_birth_property_runner as runner_module

    registry = _linux_registry(tmp_path)
    captured = {}
    monkeypatch.setattr(runner_module.sys, "platform", "linux")
    monkeypatch.setattr(runner_module.sys, "executable", "/unmounted-venv/python")
    monkeypatch.setenv("PATH", "/untrusted-bin")

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return RunnerResult(
            RunnerStatus.PASSED, None, 0, '{"output":{},"observations":{}}',
            "", 0.1, ProcessAttestation(
                "linux-bwrap-cgroup-v2", True, True, True, True, True, True,
                True, "/scope", True, True,
            ),
        )

    monkeypatch.setattr(runner_module, "run_birth_phase", fake_run)
    ObservedPropertyRunner(_observed_candidate(tmp_path), linux_registry=registry).run(
        runner_module.PropertyCase("output.actual", {}, {}),
        fixture_id="empty_private_root", isolation="private_read_only",
    )
    assert captured["command"] == (
        str(registry.interpreter_path), "-I",
        "candidate/_metnos_birth_property_harness_v1.py", "candidate.py",
    )
    assert captured["linux_registry"] is registry


@pytest.mark.parametrize("registry", [None, object(), SimpleNamespace(
    interpreter_path="/untrusted-bin/python",
)])
def test_observed_runner_refuses_missing_or_invalid_linux_registry_before_runner(
        monkeypatch, tmp_path, registry):
    import executor_birth_property_runner as runner_module

    monkeypatch.setattr(runner_module.sys, "platform", "linux")
    calls = []

    def unexpected_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("runner reached without its registered backend")

    monkeypatch.setattr(runner_module, "run_birth_phase", unexpected_run)
    runner = ObservedPropertyRunner(_observed_candidate(tmp_path), linux_registry=registry)
    with pytest.raises(RuntimeError, match="^linux_sandbox_registry_unavailable$"):
        runner.run(runner_module.PropertyCase("output.actual", {}, {}),
                   fixture_id="empty_private_root", isolation="private_read_only")
    evidence = run_property(
        "output.schema.actual",
        PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
        _runner=runner,
    )
    assert evidence[0].status is PropertyStatus.UNAVAILABLE
    assert evidence[0].error_code == "linux_sandbox_registry_unavailable"
    assert calls == []


def test_observed_runner_preserves_registered_backend_refusal(monkeypatch, tmp_path):
    import executor_birth_property_runner as runner_module

    monkeypatch.setattr(runner_module.sys, "platform", "linux")
    monkeypatch.setattr(runner_module, "run_birth_phase", lambda *args, **kwargs: RunnerResult(
        RunnerStatus.UNAVAILABLE, "linux_sandbox_program_mismatch", None, "", "", 0.1,
        ProcessAttestation(
            "linux-bwrap-cgroup-v2", False, False, False, False, False, False,
            False, None, False, False,
        ),
    ))
    runner = ObservedPropertyRunner(
        _observed_candidate(tmp_path), linux_registry=_linux_registry(tmp_path),
    )
    with pytest.raises(RuntimeError, match="^linux_sandbox_program_mismatch$"):
        runner.run(runner_module.PropertyCase("output.actual", {}, {}),
                   fixture_id="empty_private_root", isolation="private_read_only")
    evidence = run_property(
        "output.schema.actual",
        PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
        _runner=runner,
    )
    assert evidence[0].status is PropertyStatus.UNAVAILABLE


def test_observed_runner_does_not_reuse_linux_backend_on_unsupported_platform(
        monkeypatch, tmp_path):
    import executor_birth_property_runner as runner_module

    monkeypatch.setattr(runner_module.sys, "platform", "darwin")
    calls = []

    def unexpected_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unsupported platform reached process runner")

    monkeypatch.setattr(runner_module, "run_birth_phase", unexpected_run)
    with pytest.raises(RuntimeError, match="^platform_backend_unavailable$"):
        ObservedPropertyRunner(
            _observed_candidate(tmp_path), linux_registry=_linux_registry(tmp_path),
        ).run(runner_module.PropertyCase("output.actual", {}, {}),
              fixture_id="empty_private_root", isolation="private_read_only")
    assert calls == []


def test_observed_runner_uses_relative_trusted_harness_protocol_on_windows(monkeypatch, tmp_path):
    observed = _observed_candidate(tmp_path)
    captured = {}
    registry = WindowsSandboxRegistry(
        tmp_path / "helper.exe", D, tmp_path / "helper.json", D, D,
    )
    attestation = ProcessAttestation(
        "windows-appcontainer-job-v1", True, True, False, True, False, False,
        False, None, True, True,
    )
    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return RunnerResult(
            RunnerStatus.PASSED, None, 0,
            '{"output":{"entries":[]},"observations":{}}', "", 0.1,
            attestation,
        )
    monkeypatch.setattr("executor_birth_property_runner.sys.platform", "win32")
    monkeypatch.setattr("executor_birth_property_runner.run_birth_phase", fake_run)
    ObservedPropertyRunner(observed, windows_registry=registry).run(
        __import__("executor_birth_property_runner").PropertyCase(
            "cardinality.0", {"fixture_count": 0}, {"count": 0}),
        fixture_id="bounded_collection", isolation="private_read_only",
    )
    assert captured["command"] == (
        "_metnos_birth_property_harness_v1.py", "candidate.py",
    )
    assert captured["windows_registry"] is registry


def test_real_linux_observed_runner_exercises_all_seven_groups_or_skips(tmp_path):
    code = b'''import pathlib, shutil
from executor_helpers import run_stdio
root = pathlib.Path('fixture')
def invoke(args):
    undo = {'outcome': 'no_effect'}
    if (root/'state.json').is_file():
        undo = {'outcome': 'reversible', 'before': (root/'state.json').read_text()}
        (root/'state.json').write_text('changed')
    if (root/'source.bin').is_file():
        if (root/'recovery.bin').is_file(): (root/'source.bin').unlink()
        else: shutil.copyfile(root/'source.bin', root/'recovery.bin')
    count = int(args.get('fixture_count', args.get('fixture_total', 0)))
    limit = args.get('limit')
    entries = [{'index': i} for i in range(min(count, int(limit)) if limit is not None else count)]
    return {'ok': True, 'entries': entries, 'results': entries, '_undo': undo,
            'truncated': limit is not None and int(limit) < count}
def reverse(plan, results):
    (root/'state.json').write_text(results['_undo']['before'])
    return {'ok': True}
run_stdio(invoke)
'''
    manifest = b'[code]\nfiles=["candidate.py"]\n'
    observed = ObservedCandidate(
        ContractId(ManifestOrigin.USER, "x/manifest.toml"),
        CandidateSnapshot(tmp_path, manifest, b"{}", MappingProxyType({"candidate.py": code})),
        SimpleNamespace(candidate_id=D), ExecutorOrigin.HUMAN, RevisionAuthor.HUMAN, D,
    )
    profile = PropertyCandidateProfile(
        output_schema=(("entries", "array"), ("results", "array"), ("truncated", "boolean")),
        collection_output=True, limit_input=True, truncation_declared=True,
        revertible=True, destructive_with_undo=True, entries_and_results=True,
        positive_inputs=({},),
    )
    evidence = run_applicable_properties(profile, _runner=ObservedPropertyRunner(observed))
    if evidence and evidence[0].status is PropertyStatus.UNAVAILABLE:
        pytest.skip(evidence[0].error_code)
    assert {item.property_id for item in evidence} == {
        "output.schema.actual", "cardinality.zero_one_many", "limit.zero_and_below_total",
        "truncation.contract", "undo.round_trip", "delete.copy_before_delete",
        "entries.results.coherence",
    }
    assert all(item.status is PropertyStatus.PASSED for item in evidence)


def test_the_registries_and_the_closed_table_must_agree_exactly():
    """Both directions: nothing reachable unlisted, nothing listed unbuilt."""
    from executor_birth_primitive_table_v1 import (
        PRIMITIVE_TABLE_V1, PrimitiveTableError, check_registry_v1,
    )
    import executor_birth_property_runner as runner_module

    for kind, registry in (
        ("applicability", runner_module._APPLICABILITY),
        ("fixture", runner_module._FIXTURES),
        ("generator", runner_module._GENERATORS),
        ("oracle", runner_module._ORACLES),
    ):
        check_registry_v1(kind, registry)
        assert tuple(sorted(registry)) == PRIMITIVE_TABLE_V1[kind]

    with pytest.raises(PrimitiveTableError, match="primitive_registry_mismatch"):
        check_registry_v1("oracle", {*PRIMITIVE_TABLE_V1["oracle"], "invented"})
    with pytest.raises(PrimitiveTableError, match="primitive_registry_mismatch"):
        check_registry_v1("fixture", set(PRIMITIVE_TABLE_V1["fixture"][:-1]))


def test_every_property_of_the_catalog_names_only_admitted_primitives():
    """The seven groups resolve entirely inside the closed table."""
    from executor_birth_primitive_table_v1 import check_primitive_v1
    from executor_birth_properties import PROPERTY_CATALOG_V1

    for spec in PROPERTY_CATALOG_V1.values():
        check_primitive_v1("applicability", spec.applicability_id)
        check_primitive_v1("fixture", spec.fixture_id)
        check_primitive_v1("generator", spec.generator_id)
        check_primitive_v1("oracle", spec.oracle_id)


def test_a_primitive_outside_the_table_is_refused_by_the_resolution():
    """An invented identifier does not reach an implementation."""
    from dataclasses import replace

    from executor_birth_properties import PROPERTY_CATALOG_V1, PropertyContractError
    import executor_birth_property_runner as runner_module

    spec = replace(
        next(iter(PROPERTY_CATALOG_V1.values())), oracle_id="invented",
    )
    with pytest.raises(PropertyContractError, match="property_registry_invalid"):
        runner_module._resolve(spec)


def test_the_table_carries_one_stable_digest():
    """The digest is what the context component will carry; it must be fixed."""
    from executor_birth_primitive_table_v1 import primitive_table_digest_v1

    assert primitive_table_digest_v1() == (
        "sha256:b63167a560356f542abcb6a3a3f30186a5906cae9508a36d4455493c11fe1de9"
    )


@pytest.mark.parametrize("name", [
    "_metnos_birth_property_harness_v1.py",
    "_metnos_birth_property_stdio_v1.py",
    "./_metnos_birth_property_harness_v1.py",
    "_METNOS_BIRTH_PROPERTY_STDIO_V1.PY",
    "runtime/executor_helpers.py",
    "Runtime/Executor_Helpers.py",
    "runtime/./executor_helpers.py",
    "runtime/../runtime/executor_helpers.py",
    "runtime\\executor_helpers.py",
    "runtime",
    "runtime/executor_helpers.py/child.py",
    "install/data/i18n_seed.sqlite",
    "install/data",
])
def test_reserved_support_paths_are_refused_before_runner(monkeypatch, tmp_path, name):
    from dataclasses import replace
    import executor_birth_property_runner as runner_module

    observed = _observed_candidate(tmp_path)
    files = {**observed.snapshot.code_files, name: b"raise RuntimeError('untrusted')\n"}
    observed = replace(observed, snapshot=replace(
        observed.snapshot, code_files=MappingProxyType(files)))
    monkeypatch.setattr(runner_module.sys, "platform", "linux")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("reserved path reached process runner")

    monkeypatch.setattr(runner_module, "run_birth_phase", unexpected_run)
    with pytest.raises(PropertyContractError, match="property_candidate_invalid"):
        ObservedPropertyRunner(observed, linux_registry=_linux_registry(tmp_path)).run(
            runner_module.PropertyCase("output.actual", {}, {}),
            fixture_id="empty_private_root", isolation="private_read_only")


@pytest.mark.parametrize("error", [OSError("private/source"), ValueError("private/content")])
def test_missing_or_invalid_support_is_public_unavailability_before_runner(
        monkeypatch, tmp_path, error):
    import executor_birth_functional as functional
    import executor_birth_property_runner as runner_module

    def unavailable():
        raise error

    def unexpected_run(*args, **kwargs):
        raise AssertionError("missing support reached process runner")

    monkeypatch.setattr(functional, "_support_files", unavailable)
    monkeypatch.setattr(runner_module.sys, "platform", "linux")
    monkeypatch.setattr(runner_module, "run_birth_phase", unexpected_run)
    evidence = run_property(
        "output.schema.actual", PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
        _runner=ObservedPropertyRunner(_observed_candidate(tmp_path),
                                       linux_registry=_linux_registry(tmp_path)))
    assert evidence[0].status is PropertyStatus.UNAVAILABLE
    assert evidence[0].error_code == "property_support_unavailable"
    assert "private/" not in repr(evidence)


@pytest.mark.parametrize("code", [
    "linux_sandbox_registry_unavailable", "linux_sandbox_program_unavailable",
    "linux_sandbox_program_mismatch", "platform_backend_unavailable",
    "property_support_unavailable",
    "sandbox_setup_unattested", "candidate_process_failed",
    "cgroup_delegate_subgroup_missing", "cgroup_delegate_not_writable",
    "cgroup_scope_unavailable", "phase_timeout", "total_timeout",
])
def test_only_closed_infrastructure_reasons_are_exposed(code):
    evidence = run_property(
        "output.schema.actual", PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
        _runner=Runner(error=RuntimeError(code)))
    assert evidence[0].status is PropertyStatus.UNAVAILABLE
    assert evidence[0].error_code == code


@pytest.mark.parametrize("error", [
    RuntimeError("private/path and secret"),
    RuntimeError("linux_sandbox_program_mismatch: private/path"),
    RuntimeError("property_support_unavailable", "private/path"),
])
def test_arbitrary_exception_payload_is_not_a_public_reason(error):
    evidence = run_property(
        "output.schema.actual", PropertyCandidateProfile(output_schema=(("ok", "boolean"),)),
        _runner=Runner(error=error))
    assert evidence[0].error_code == "property_runner_unavailable"
    assert "private/" not in repr(evidence)


def test_stdio_shim_imports_shared_helpers_only_in_separate_child(monkeypatch, tmp_path):
    """A controlled unit fixture, NOT a native sandbox/admission proof."""
    from dataclasses import replace
    import json
    from pathlib import Path
    import subprocess
    import sys
    import executor_birth_property_runner as runner_module
    from executor_birth_runner import materialize_candidate_files, materialize_fixture

    source = b'''import os, pathlib, sys
assert __name__ == '__main__', 'candidate imported by observer'
root = pathlib.Path(__file__).resolve().parent
assert sys.flags.isolated == 1
assert os.environ['METNOS_SHIM_DIR'] == str(root / 'runtime')
assert os.environ['METNOS_WORKSPACE'] == str(pathlib.Path.cwd() / 'workspace')
sys.path.insert(0, os.environ['METNOS_SHIM_DIR'])
from executor_helpers import run_stdio
def invoke(args):
    assert args == {'value': 'real input'}
    return {'ok': True, 'pid': os.getpid(), 'parent_pid': os.getppid()}
run_stdio(invoke)
'''
    observed = _observed_candidate(tmp_path)
    observed = replace(observed, snapshot=replace(
        observed.snapshot, code_files=MappingProxyType({"candidate.py": source})))
    interpreter = Path(sys.executable).resolve()
    registry = LinuxSandboxRegistry(tmp_path / "unused-bwrap", D, interpreter, D)
    captured = {}

    def run_trusted_fixture(command, **kwargs):
        work = tmp_path / "work"
        work.mkdir()
        candidate = work / "candidate"
        candidate.mkdir()
        materialize_candidate_files(candidate, kwargs["candidate_files"])
        materialize_fixture(work, kwargs["fixture_ops"])
        # No ambient Metnos paths or credentials enter this unit child.
        env = {"METNOS_USER_DATA": str(work / "data"),
               "METNOS_USER_STATE": str(work / "state"),
               "METNOS_USER_CONFIG": str(work / "config")}
        with subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            captured["harness_pid"] = process.pid
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
            assert process.returncode == 0, stderr.decode("utf-8", "replace")
        json.loads(stdout)
        return RunnerResult(
            RunnerStatus.PASSED, None, 0, stdout.decode(), stderr.decode(), 0.1,
            ProcessAttestation("unit-fixture-only", False, False, False, False,
                               False, False, False, None, False, False))

    monkeypatch.setattr(runner_module.sys, "platform", "linux")
    monkeypatch.setattr(runner_module, "run_birth_phase", run_trusted_fixture)
    result = ObservedPropertyRunner(observed, linux_registry=registry).run(
        runner_module.PropertyCase("output.actual", {"value": "real input"}, {}),
        fixture_id="empty_private_root", isolation="private_read_only")
    assert result.output["ok"] is True
    assert result.output["pid"] != captured["harness_pid"]
    assert result.output["parent_pid"] == captured["harness_pid"]
    assert observed.snapshot.code_files["candidate.py"] == source


@pytest.fixture
def controlled_property_stdio(monkeypatch, tmp_path):
    """Real child processes over trusted test code; not a native Birth proof."""
    import json
    from pathlib import Path
    import subprocess
    import sys
    import executor_birth_property_runner as runner_module
    from executor_birth_runner import materialize_candidate_files, materialize_fixture

    outputs = []
    def run(command, **kwargs):
        work = tmp_path / f"stdio-{len(outputs)}"
        work.mkdir()
        candidate = work / "candidate"
        candidate.mkdir()
        materialize_candidate_files(candidate, kwargs["candidate_files"])
        materialize_fixture(work, kwargs["fixture_ops"])
        environment = {f"METNOS_USER_{kind.upper()}": str(work / kind)
                       for kind in ("data", "state", "config")}
        result = subprocess.run(command, cwd=work, env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        if result.returncode != 0:
            pytest.fail(result.stderr.decode())
        outputs.append(json.loads(result.stdout))
        return RunnerResult(
            RunnerStatus.PASSED, None, 0, result.stdout.decode(), result.stderr.decode(), .1,
            ProcessAttestation("unit-fixture-only", False, False, False, False,
                               False, False, False, None, False, False))
    monkeypatch.setattr(runner_module, "run_birth_phase", run)
    registry = LinuxSandboxRegistry(tmp_path / "not-used-bwrap", D,
                                    Path(sys.executable).resolve(), D)
    return registry, outputs


def _run_processes_observation(tmp_path, *, mutation=""):
    from pathlib import Path
    root = Path(__file__).resolve().parents[3] / "executors/run_processes"
    source = (root / "run_processes.py").read_bytes()
    if mutation:
        marker = b'if __name__ == "__main__":'
        assert source.count(marker) == 1
        source = source.replace(marker, mutation.encode() + b"\n" + marker)
    return ObservedCandidate(
        ContractId(ManifestOrigin.CORE, "run_processes/manifest.toml"),
        CandidateSnapshot(tmp_path, (root / "manifest.toml").read_bytes(), b"{}",
                          MappingProxyType({"run_processes.py": source})),
        SimpleNamespace(candidate_id=D), ExecutorOrigin.HUMAN, RevisionAuthor.HUMAN, D)


@pytest.mark.parametrize("mutation, expected", [
    ("", PropertyStatus.PASSED),
    ("def invoke(args):\n    return {'ok': True, '_undo': {'outcome': 'no_effect'}}\n",
     PropertyStatus.FAILED),
    ("def reverse(plan, results):\n    return {'ok': True}\n", PropertyStatus.FAILED),
    ("original_invoke = invoke\ndef invoke(args):\n    result = original_invoke(args)\n"
     "    if result.get('_undo', {}).get('processes'):\n"
     "        result['_undo']['processes'][0]['creation_time'] += 1\n"
     "    return result\n", PropertyStatus.FAILED),
    ("def invoke(args):\n    return {'ok': True, 'state_round_trip_attested': True, "
     "'observations': {'restored': True}}\n", PropertyStatus.FAILED),
])
def test_real_stdio_helper_contract_roundtrip_and_counterexamples(
        controlled_property_stdio, tmp_path, mutation, expected):
    import tomllib
    from executor_birth_shadow import _profile
    observed = _run_processes_observation(tmp_path, mutation=mutation)
    registry, outputs = controlled_property_stdio
    profile = _profile(tomllib.loads(observed.snapshot.manifest_bytes.decode()))
    evidence = run_property("undo.round_trip", profile,
                           _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert evidence and all(item.status is expected for item in evidence)
    assert all("helper_contract" in item.case_id for item in evidence)
    if expected is PropertyStatus.PASSED:
        assert outputs[0]["output"]["ok"] is True
        observation = outputs[0]["observations"]
        assert observation["state_before_hash"] != observation["state_after_forward_hash"]
        assert observation["state_before_hash"] == observation["state_after_undo_hash"]
        assert observation["fixture_contract"] == "managed_helper/v1"


@pytest.mark.parametrize("name", [
    "helper", "HELPER", "./helper", "helper/sub.py", "package-app",
    "Package-App", "_metnos_birth_helper_model_v1.py",
])
def test_helper_fixture_support_names_cannot_be_shadowed(name):
    import executor_birth_property_runner as runner_module
    with pytest.raises(PropertyContractError, match="property_candidate_invalid"):
        runner_module._candidate_files_with_support({"candidate.py": b"pass\n", name: b"pass\n"})


def test_stdio_roundtrip_is_general_and_passes_the_original_plan(
        controlled_property_stdio, tmp_path):
    from dataclasses import replace
    source = b'''import pathlib
from executor_helpers import run_stdio
def invoke(args):
    assert set(args) == {'path', 'value'}
    path = pathlib.Path(args['path'])
    before = path.read_text()
    path.write_text(args['value'])
    return {'ok': True, '_undo': {'outcome': 'reversible', 'before': before}}
def reverse(plan, results):
    assert set(plan) == {'args'}
    assert plan['args']['value'] == 'after'
    pathlib.Path(plan['args']['path']).write_text(results['_undo']['before'])
    return {'ok': True}
run_stdio(invoke)
'''
    observed = _observed_candidate(tmp_path)
    observed = replace(observed, snapshot=replace(observed.snapshot,
        code_files=MappingProxyType({"candidate.py": source})))
    registry, outputs = controlled_property_stdio
    profile = PropertyCandidateProfile(revertible=True,
        positive_inputs=({"path": "fixture/state.json", "value": "after"},))
    evidence = run_property("undo.round_trip", profile,
        _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert evidence[0].status is PropertyStatus.PASSED
    assert "fixture_contract" not in outputs[0]["observations"]


def test_missing_positive_case_never_invokes_the_candidate(monkeypatch, tmp_path):
    import executor_birth_property_runner as runner_module
    monkeypatch.setattr(runner_module, "run_birth_phase",
                        lambda *a, **kw: pytest.fail("missing positive case reached candidate"))
    evidence = run_property("undo.round_trip", PropertyCandidateProfile(revertible=True),
        _runner=ObservedPropertyRunner(_observed_candidate(tmp_path),
                                       linux_registry=_linux_registry(tmp_path)))
    assert evidence[0].status is PropertyStatus.UNAVAILABLE
    assert evidence[0].error_code == "property_case_unavailable"


def _core_observation(tmp_path, name, mutation=""):
    from pathlib import Path
    root = Path(__file__).resolve().parents[3] / 'executors' / name
    source = (root / f'{name}.py').read_bytes()
    if mutation:
        marker = b'if __name__ == "__main__":'
        assert source.count(marker) == 1
        source = source.replace(marker, mutation.encode() + b'\n' + marker)
    return ObservedCandidate(
        ContractId(ManifestOrigin.CORE, f'{name}/manifest.toml'),
        CandidateSnapshot(tmp_path, (root/'manifest.toml').read_bytes(), b'{}',
                          MappingProxyType({f'{name}.py': source})),
        SimpleNamespace(candidate_id=D), ExecutorOrigin.HUMAN, RevisionAuthor.HUMAN, D)


def test_session_fixture_requires_the_declared_module_inverse(controlled_property_stdio, tmp_path):
    import tomllib
    from dataclasses import replace
    from executor_birth_shadow import _profile
    observed = _core_observation(tmp_path, 'open_sites')
    observed = replace(observed, snapshot=replace(observed.snapshot,
        manifest_bytes=observed.snapshot.manifest_bytes.replace(
            b'reverse_pattern = "module.reverse"',
            b'reverse_pattern = "delete_created_paths"')))
    profile = _profile(tomllib.loads(observed.snapshot.manifest_bytes.decode()))
    assert profile.domain_contract == ''
    registry, outputs = controlled_property_stdio
    evidence = run_property('undo.round_trip', profile,
        _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert evidence and all(row.status is PropertyStatus.UNAVAILABLE for row in evidence)
    assert not outputs


@pytest.mark.parametrize('name,mutation,expected', [
    ('compress_files', '', PropertyStatus.PASSED),
    ('open_sites', '', PropertyStatus.PASSED),
    ('compress_files', "original_invoke=invoke\ndef invoke(args):\n    out=original_invoke(args)\n    out['dirs_created']=[]\n    return out\n", PropertyStatus.FAILED),
    ('open_sites', "original_close=session_client.session_close\ndef close_all(**kwargs):\n    return original_close(**{**kwargs, 'all':True})\nsession_client.session_close=close_all\n", PropertyStatus.FAILED),
])
def test_native_created_paths_and_sessions(tmp_path, monkeypatch, name, mutation, expected):
    """Explicit native diagnostic: no registry injection, admission or publishing."""
    import os
    import tomllib
    from executor_birth_shadow import _profile
    from executor_birth_sandbox_registry_v1 import (
        measure_sandbox_backend_v1, decode_sandbox_registry_v1,
    )
    if os.environ.get('METNOS_TEST_NATIVE_BIRTH') != '1':
        pytest.skip('requires an explicitly delegated native test unit')
    registry = decode_sandbox_registry_v1(measure_sandbox_backend_v1())
    assert registry is not None
    import executor_birth_property_runner as runner_module
    native_run = runner_module.run_birth_phase
    results = []
    def capture(*args, **kwargs):
        result = native_run(*args, **kwargs)
        results.append(result)
        return result
    monkeypatch.setattr(runner_module, 'run_birth_phase', capture)
    observed = _core_observation(tmp_path, name, mutation)
    evidence = run_property('undo.round_trip',
        _profile(tomllib.loads(observed.snapshot.manifest_bytes.decode())),
        _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert len(evidence) >= 2
    assert all(row.status is expected for row in evidence), [
        (result.error_code, result.stderr, result.stdout) for result in results]


@pytest.mark.parametrize('name', ['compress_files', 'open_sites'])
def test_real_created_paths_and_sessions_roundtrip(controlled_property_stdio, tmp_path, name):
    import tomllib
    from executor_birth_shadow import _profile
    observed = _core_observation(tmp_path, name)
    registry, outputs = controlled_property_stdio
    profile = _profile(tomllib.loads(observed.snapshot.manifest_bytes.decode()))
    evidence = run_property('undo.round_trip', profile,
                           _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert len(evidence) >= 2
    assert all(row.status is PropertyStatus.PASSED for row in evidence), outputs
    for result in outputs:
        assert result['output']['ok'] is True
        obs = result['observations']
        assert obs['state_after_forward_hash'] != obs['state_before_hash']
        assert obs['state_after_undo_hash'] == obs['state_before_hash']


@pytest.mark.parametrize('name,mutation', [
    ('compress_files', "original_invoke=invoke\ndef invoke(args):\n    out=original_invoke(args)\n    out['results']=[]\n    return out\n"),
    ('compress_files', "original_invoke=invoke\ndef invoke(args):\n    out=original_invoke(args)\n    out['dirs_created']=[]\n    return out\n"),
    ('compress_files', "original_invoke=invoke\ndef invoke(args):\n    out=original_invoke(args)\n    out['results'].append({'path':'fixture/source-1.bin','created':True})\n    return out\n"),
    ('open_sites', "def reverse(plan, results):\n    return {'ok':True}\n"),
    ('open_sites', "original_close=session_client.session_close\ndef close_all(**kwargs):\n    return original_close(**{**kwargs, 'all':True})\nsession_client.session_close=close_all\n"),
    ('open_sites', "original_invoke=invoke\ndef invoke(args):\n    out=original_invoke(args)\n    out['_undo']['session_ids'].append('existing')\n    return out\n"),
    ('open_sites', "original_invoke=invoke\ndef invoke(args):\n    out=original_invoke(args)\n    out['_undo']['session_ids']=['wrong-id']\n    return out\n"),
])
def test_created_paths_and_session_receipts_cannot_lie(
        controlled_property_stdio, tmp_path, name, mutation):
    import tomllib
    from executor_birth_shadow import _profile
    observed = _core_observation(tmp_path, name, mutation)
    registry, _ = controlled_property_stdio
    evidence = run_property('undo.round_trip', _profile(tomllib.loads(observed.snapshot.manifest_bytes.decode())),
                           _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert evidence and all(row.status is PropertyStatus.FAILED for row in evidence)


@pytest.mark.parametrize('path', [
    '_metnos_birth_reverse_v1.py', 'runtime/reverse_patterns.py',
    'runtime/playwright_sidecar/session_client.py', 'runtime/playwright_sidecar/stealth.py',
])
def test_new_property_support_cannot_be_replaced(path):
    import executor_birth_property_runner as runner_module
    with pytest.raises(PropertyContractError, match='property_candidate_invalid'):
        runner_module._candidate_files_with_support({'candidate.py': b'pass\n', path: b'pass\n'})


def test_real_linux_stdio_without_a_client_refuses_honestly(
        controlled_property_stdio, tmp_path):
    from executor_birth_property_runner import PropertyCase
    registry, _outputs = controlled_property_stdio
    observed = _run_processes_observation(tmp_path)
    result = ObservedPropertyRunner(observed, linux_registry=registry).run(
        PropertyCase("linux.unavailable", {"programs": ["Metnos.BirthFixture"]}, {}),
        fixture_id="empty_private_root", isolation="private_read_only")
    assert result.output["error_code"] == "platform_unsupported"
    assert result.output["error_class"] == "capability_missing"
    assert "<missing:" not in result.output["error"]


@pytest.mark.parametrize("respond", [False, True])
def test_helper_client_timeout_and_no_dependency_shadowing(tmp_path, respond):
    import json
    import socket
    import subprocess
    import sys
    import threading
    import executor_birth_property_runner as runner_module

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "helper").write_bytes(runner_module._HELPER_CLIENT_SOURCE)
    (candidate / "json.py").write_text("raise RuntimeError('candidate shadow imported')\n")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(tmp_path / "helper.sock"))
    server.listen(1)
    finished = threading.Event()
    def silent():
        with server.accept()[0] as client:
            client.recv(4096)
            if respond:
                client.sendall(b'{"ok":true}\n')
            else:
                finished.wait(3)
    thread = threading.Thread(target=silent, daemon=True)
    thread.start()
    try:
        result = subprocess.run([sys.executable, str(candidate / "helper"),
                                 "query", "--package-id", "Metnos.BirthFixture"],
                                env={"PYTHONSAFEPATH": "1"}, capture_output=True, timeout=2.5)
        expected = {"ok": True} if respond else {
            "ok": False, "error_code": "property_helper_unavailable"}
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == expected
    finally:
        finished.set()
        server.close()
        thread.join(timeout=3)


def test_appx_helper_contract_preserves_preexisting_processes(
        controlled_property_stdio, tmp_path):
    from dataclasses import replace
    import hashlib
    import json
    import tomllib
    from executor_birth_shadow import _profile
    observed = _run_processes_observation(tmp_path)
    manifest = tomllib.loads(observed.snapshot.manifest_bytes.decode())
    args = next(case["input"] for case in manifest["tests"] if case["expect"].get("ok") is True)
    package = "appx:Metnos.BirthFixture_1.0_x64__fixture"
    token = hashlib.sha256(json.dumps({"programs": [package], "lifetime": "session",
                                      "authorization_scope": "once", "authorization_boot_id": ""},
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    changed = observed.snapshot.manifest_bytes.replace(
        args["programs"][0].encode(), package.encode()).replace(
            args["actor_consent_token"].encode(), token.encode())
    observed = replace(observed, snapshot=replace(observed.snapshot, manifest_bytes=changed))
    profile = _profile(tomllib.loads(changed.decode()))
    registry, outputs = controlled_property_stdio
    evidence = run_property("undo.round_trip", profile,
        _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert evidence[0].status is PropertyStatus.PASSED
    receipt = outputs[0]["output"]["_undo"]["processes"][0]
    assert receipt["preexisting_processes"]
    assert outputs[0]["observations"]["state_before_hash"] == outputs[0]["observations"]["state_after_undo_hash"]


def test_writing_a_fake_processes_file_cannot_forge_helper_observations(
        controlled_property_stdio, tmp_path):
    import tomllib
    from executor_birth_shadow import _profile
    mutation = '''from pathlib import Path
def invoke(args):
    root = Path(os.environ['METNOS_WORKSPACE']).parent / 'fixture'
    (root / 'processes.json').write_text('fake started process')
    return {'ok': True, '_undo': {'outcome': 'reversible'}}
def reverse(plan, results):
    root = Path(os.environ['METNOS_WORKSPACE']).parent / 'fixture'
    (root / 'processes.json').unlink()
    return {'ok': True}
'''
    observed = _run_processes_observation(tmp_path, mutation=mutation)
    registry, outputs = controlled_property_stdio
    evidence = run_property("undo.round_trip",
        _profile(tomllib.loads(observed.snapshot.manifest_bytes.decode())),
        _runner=ObservedPropertyRunner(observed, linux_registry=registry))
    assert evidence[0].status is PropertyStatus.FAILED
    assert outputs[0]["observations"]["state_before_hash"] == outputs[0]["observations"]["state_after_forward_hash"]
