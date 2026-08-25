from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from executor_birth_identity import CandidateIdentities, ExecutorOrigin, RevisionAuthor
from executor_birth_shadow import (
    BirthOutcome, RevisionClass, RevisionFacts, _assemble_production_dependencies,
    _observe_birth_for_test, _sealed_dependencies_for_test,
    classify_revision, observe_birth,
)
from manifest_inventory import ContractId, ManifestOrigin

D = "sha256:" + "1" * 64
MANIFEST = Path("dist/metnos-public/executors/consult_frontier/manifest.toml").read_bytes()


@dataclass
class _Snapshot:
    manifest_bytes: bytes = MANIFEST
    language_state_bytes: bytes = b"{}"
    code_files: dict[str, bytes] | None = None
    closed: bool = False

    def __post_init__(self):
        if self.code_files is None:
            self.code_files = {"consult_frontier.py": b"pass\n"}

    def close(self):
        self.closed = True


@dataclass
class _Observed:
    identities: CandidateIdentities
    snapshot: _Snapshot
    executor_origin: ExecutorOrigin
    revision_authorship: RevisionAuthor

    def close(self):
        self.snapshot.close()


def _observer_holder(*, origin=ExecutorOrigin.HUMAN, authorship=RevisionAuthor.HUMAN):
    value = _Observed(CandidateIdentities(D, D, D), _Snapshot(), origin, authorship)
    return value, lambda *args, **kwargs: value


def _call(observer, *, origin=ExecutorOrigin.HUMAN, authorship=RevisionAuthor.HUMAN,
          facts=RevisionFacts(first_birth=True), **services):
    return _observe_birth_for_test(
        Path("unused"), contract_id=ContractId(ManifestOrigin.USER, "test/manifest.toml"),
        executor_origin=origin, revision_authorship=authorship,
        objective_hash=D, admission_context=object(), revision_facts=facts,
        _dependencies=_sealed_dependencies_for_test(observer=observer, **services),
    )


def test_classification_is_conservative_and_preserves_union():
    decision = classify_revision(RevisionFacts(code_changed=True, authority_changed=True))
    assert decision.revision_class is RevisionClass.CODE
    assert decision.changed_dimensions == ("code", "authority")
    assert classify_revision(RevisionFacts(linguistic_surface_changed=True)).revision_class is RevisionClass.CONTRACT
    assert classify_revision(RevisionFacts(
        linguistic_surface_changed=True, localization_proof_valid=True,
        semantic_core_unchanged=True,
    )).revision_class is RevisionClass.LOCALIZATION


def test_public_api_has_no_check_catalog_or_publisher_authority():
    parameters = inspect.signature(observe_birth).parameters
    assert {"check_specs", "applicability", "publisher", "observer"}.isdisjoint(parameters)


def test_core_catalog_cannot_be_omitted_and_snapshot_is_closed():
    observed, observer = _observer_holder()
    report = _call(observer)
    assert [item.check_id for item in report.checks] == ["manifest_standard", "properties"]
    assert report.outcome is BirthOutcome.REJECTED
    assert report.publisher_call_count == 0
    assert observed.snapshot.closed


def test_core_assembler_does_not_make_ordinary_human_birth_unavailable():
    observed, observer = _observer_holder()
    assembled = _assemble_production_dependencies()
    deps = _sealed_dependencies_for_test(
        observer=observer, property_runner=assembled.property_runner,
        semantic_policy=assembled.semantic_policy, semantic_risk=assembled.semantic_risk,
        independent_evidence=assembled.independent_evidence,
        approval_subject=assembled.approval_subject,
        approval_evidence=assembled.approval_evidence, now=assembled.now,
    )
    report = _observe_birth_for_test(
        Path("unused"), contract_id=ContractId(ManifestOrigin.USER, "test/manifest.toml"),
        executor_origin=ExecutorOrigin.HUMAN, revision_authorship=RevisionAuthor.HUMAN,
        objective_hash=D, admission_context=object(), revision_facts=RevisionFacts(first_birth=True),
        _dependencies=deps,
    )
    assert report.outcome is BirthOutcome.ADMITTED
    assert [item.status.value for item in report.checks] == [
        "passed", "passed", "not_applicable", "not_applicable",
    ]
    assert observed.snapshot.closed


def test_caller_supplied_check_specs_is_rejected_instead_of_narrowing_catalog():
    _, observer = _observer_holder()
    with pytest.raises(TypeError):
        observe_birth(
            Path("unused"), contract_id=ContractId(ManifestOrigin.USER, "test/manifest.toml"),
            executor_origin=ExecutorOrigin.HUMAN, revision_authorship=RevisionAuthor.HUMAN,
            objective_hash=D, admission_context=object(), revision_facts=RevisionFacts(first_birth=True),
            check_specs=(),  # type: ignore[call-arg]
        )


def test_unrecognized_dependency_cannot_inject_checks_or_applicability():
    _, observer = _observer_holder()
    with pytest.raises(ValueError, match="birth_dependencies_invalid"):
        _sealed_dependencies_for_test(observer=observer, check_specs=())


def test_core_applicability_requires_semantic_review_for_model_authorship():
    _, observer = _observer_holder(authorship=RevisionAuthor.MODEL)
    report = _call(observer, authorship=RevisionAuthor.MODEL, property_runner=object())
    assert [item.check_id for item in report.checks] == [
        "manifest_standard", "properties", "semantic_review",
    ]
    assert report.checks[-1].error_code == "check_unavailable"
    assert report.outcome is BirthOutcome.REJECTED


def test_core_applicability_requires_approval_for_authority_revision():
    _, observer = _observer_holder()
    report = _call(observer, property_runner=object(),
                   facts=RevisionFacts(authority_changed=True))
    assert [item.check_id for item in report.checks] == [
        "manifest_standard", "properties", "semantic_review", "approval",
    ]
    assert report.checks[-1].error_code == "approval_required"
    assert report.outcome is BirthOutcome.NEEDS_HUMAN


@pytest.mark.parametrize("facts", [
    RevisionFacts(first_birth=True), RevisionFacts(code_changed=True),
    RevisionFacts(contract_changed=True),
])
def test_synthesized_birth_always_reaches_core_approval_gate(facts):
    _, observer = _observer_holder(origin=ExecutorOrigin.SYNTHESIZED)
    report = _call(observer, origin=ExecutorOrigin.SYNTHESIZED,
                   facts=facts, property_runner=object())
    # Semantic review is mandatory first for model-authored syntheses in the
    # real path. Human-authorship here isolates and proves approval applicability.
    assert report.checks[-1].check_id == "approval"
    assert report.checks[-1].error_code == "approval_required"


def test_observation_failure_has_closed_rejection_report():
    def broken(*args, **kwargs):
        raise OSError("live source unavailable")
    report = _call(broken)
    assert report.outcome is BirthOutcome.REJECTED
    assert report.error_code == "candidate_observation_unavailable"
    assert report.candidate_id is None
    assert report.publisher_call_count == 0
