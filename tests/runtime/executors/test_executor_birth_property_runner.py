import pytest

from executor_birth_properties import PropertyContractError, PropertyStatus
from executor_birth_property_runner import (
    PropertyCandidateProfile, PropertyRunResult, run_applicable_properties, run_property,
)


D = "sha256:" + "1" * 64


class Runner:
    def __init__(self, output=None, error=None):
        self.output = output or {}
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
        return PropertyRunResult(output, D)


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
    limits = run_property("limit.zero_and_below_total", PropertyCandidateProfile(limit_input=True), _runner=runner)
    assert len(limits) == 2 and all(item.status is PropertyStatus.PASSED for item in limits)


def test_oracle_failure_and_runner_unavailability_are_evidence_not_bypass():
    profile = PropertyCandidateProfile(output_required=("ok",))
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
