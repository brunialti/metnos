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
    assert set(captured["candidate_files"]) == {
        "candidate.py", "_metnos_birth_property_harness_v1.py",
    }
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
    assert evidence[0].error_code == "property_runner_unavailable"
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
    code = b'''import json, pathlib, shutil, sys
r=json.load(sys.stdin); root=pathlib.Path(r["fixture_root"]); action=r["birth_property_action"]
if r["fixture_id"] == "private_mutable_state":
    (root/"state.json").write_text('{"value":"changed"}' if action == "forward" else '{"value":"before"}')
if r["fixture_id"] == "private_deletion_tree" and action == "prepare_delete":
    shutil.copyfile(root/"source.bin", root/"recovery.bin")
if r["fixture_id"] == "private_deletion_tree" and action == "commit_delete":
    (root/"source.bin").unlink()
n=int(r["input"].get("fixture_count", r["input"].get("fixture_total", 0)))
limit=r["input"].get("limit"); n=min(n, int(limit)) if limit is not None else n
entries=[{"index":i} for i in range(n)]
print(json.dumps({"entries":entries,"results":entries,"truncated":limit is not None and int(limit)<int(r["input"].get("fixture_total",n))}))
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
