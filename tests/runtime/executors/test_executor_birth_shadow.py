from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from executor_birth_identity import CandidateIdentities, ExecutorOrigin, RevisionAuthor
from executor_birth_shadow import (
    BirthOutcome, CheckResult, CheckSpec, CheckStatus, RevisionClass,
    RevisionFacts, classify_revision, observe_birth,
)
from manifest_inventory import ContractId, ManifestOrigin


D = "sha256:" + "1" * 64


@dataclass
class _Snapshot:
    closed: bool = False

    def close(self):
        self.closed = True


@dataclass
class _Observed:
    identities: CandidateIdentities
    snapshot: _Snapshot

    def close(self):
        self.snapshot.close()


def _observer_holder():
    value = _Observed(CandidateIdentities(D, D, D), _Snapshot())
    return value, lambda *args, **kwargs: value


def _call(observer, specs=(), *, origin=ExecutorOrigin.HUMAN, facts=RevisionFacts(first_birth=True)):
    return observe_birth(
        Path("unused"), contract_id=ContractId(ManifestOrigin.USER, "test/manifest.toml"),
        executor_origin=origin, revision_authorship=RevisionAuthor.HUMAN,
        objective_hash=D, admission_context=object(), revision_facts=facts,
        check_specs=specs, observer=observer,
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


def test_shadow_admits_without_any_publisher_surface_and_closes_snapshot():
    observed, observer = _observer_holder()
    result = _call(observer, (CheckSpec(
        "standard", "1", True, lambda *_: True,
        lambda *_: CheckResult("standard", "1", CheckStatus.PASSED, None, D, "ok"),
    ),))
    assert result.outcome is BirthOutcome.ADMITTED
    assert result.publisher_call_count == 0
    assert observed.snapshot.closed
    assert "publisher" not in observe_birth.__code__.co_varnames


def test_mandatory_failure_short_circuits_and_explains_divergence():
    _, observer = _observer_holder()
    called = []
    specs = (
        CheckSpec("ast", "1", True, lambda *_: True,
                  lambda *_: CheckResult("ast", "1", CheckStatus.FAILED,
                                         "contract_nonconformant", D, "bad AST")),
        CheckSpec("later", "1", True, lambda *_: True,
                  lambda *_: called.append(True)),
    )
    report = _call(observer, specs)
    assert report.outcome is BirthOutcome.REJECTED
    assert report.error_code == "contract_nonconformant"
    assert [item.check_id for item in report.checks] == ["ast"]
    assert called == []


@pytest.mark.parametrize("fault_at", ["predicate", "runner"])
def test_dependency_fault_is_unavailable_and_fail_closed(fault_at):
    _, observer = _observer_holder()
    def broken(*_):
        raise RuntimeError("secret /home/user")
    spec = CheckSpec("isolated", "7", True,
                     broken if fault_at == "predicate" else lambda *_: True,
                     broken if fault_at == "runner" else lambda *_: None)
    report = _call(observer, (spec,))
    assert report.outcome is BirthOutcome.REJECTED
    assert report.checks[0].status is CheckStatus.UNAVAILABLE
    assert report.checks[0].redacted_detail == "RuntimeError"


def test_not_applicable_requires_false_predicate_and_synth_enters_preexercise():
    _, observer = _observer_holder()
    inapplicable = CheckSpec("semantic", "2", True, lambda *_: False,
                             lambda *_: pytest.fail("must not run"))
    report = _call(observer, (inapplicable,), origin=ExecutorOrigin.SYNTHESIZED)
    assert report.checks[0].status is CheckStatus.NOT_APPLICABLE
    assert report.outcome is BirthOutcome.PREEXERCISE


def test_observation_failure_has_closed_rejection_report():
    def broken(*args, **kwargs):
        raise OSError("live source unavailable")
    report = _call(broken)
    assert report.outcome is BirthOutcome.REJECTED
    assert report.error_code == "candidate_observation_unavailable"
    assert report.candidate_id is None
    assert report.publisher_call_count == 0
