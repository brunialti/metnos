"""Does this installation meet the approved F5 threshold, and why not."""
from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

import install.birth_certification_qualification as qualification
from executor_birth_history import HistoricalActV1, HistoricalEvidenceIssueV1
from install.birth_certification_qualification import (
    QualificationRefused, derive_qualification_v1, evidence_scope_id_v1,
)


HEAD = "sha256:" + "1" * 64


@dataclass(frozen=True)
class Reconciliation:
    """The reconciler's shape, without running the whole historical join."""

    required_head_id: str | None
    technical_acts: tuple
    technical_issuers: tuple
    issues: tuple


@dataclass(frozen=True)
class Frontier:
    """The evidence owner's frontier, as its reader returns it."""

    head: str | None
    census_scope: str | None
    open_findings: tuple
    profile: str | None
    consecutive_successes: tuple
    pending_cycle: str | None
    profile_bindings: object


def act(number, issuer="builtin_contract_generator"):
    return HistoricalActV1(
        object(), "sha256:" + f"{number:064x}", issuer, "namespace",
        "sha256:" + "c" * 64, "technical_candidate",
    )


def reconciliation(*, admissions=5, issuers=2, issues=()):
    acts = tuple(
        act(number, "builtin_contract_generator" if number % 2 or issuers < 2
            else "stack_reconcile")
        for number in range(admissions)
    )
    names = tuple(sorted({item.issuer_id for item in acts}))
    return Reconciliation(HEAD, acts, names[:issuers] if issuers else (), issues)


def frontier(reconciled, **overrides):
    from install.birth_certification_evidence import ProfileBindingsV1

    base = dict(
        head="sha256:" + "e" * 64,
        census_scope=evidence_scope_id_v1(reconciled.issues),
        open_findings=(), profile="sha256:" + "p" * 64,
        consecutive_successes=("cycle-a", "cycle-b"), pending_cycle=None,
        profile_bindings=ProfileBindingsV1(
            HEAD, reconciled.required_head_id, HEAD, HEAD, HEAD,
        ),
    )
    base.update(overrides)
    return Frontier(**base)


# --- the accepted case -------------------------------------------------------

def test_a_qualifying_installation_binds_the_evidence_that_proved_it():
    reconciled = reconciliation()
    result = derive_qualification_v1(reconciled, frontier(reconciled))
    assert result.technical_admissions == 5
    assert len(result.authenticated_producers) == 2
    assert result.required_head_id == HEAD
    assert result.cycle_ids == ("cycle-a", "cycle-b")
    assert result.qualification_id.startswith("sha256:")


def test_the_same_evidence_always_names_the_same_qualification():
    reconciled = reconciliation()
    first = derive_qualification_v1(reconciled, frontier(reconciled))
    second = derive_qualification_v1(reconciled, frontier(reconciled))
    assert first == second


@pytest.mark.parametrize("change", ["admissions", "issuers", "cycles", "profile", "head"])
def test_different_evidence_names_a_different_qualification(change):
    reconciled = reconciliation()
    base = derive_qualification_v1(reconciled, frontier(reconciled))
    if change == "admissions":
        other = reconciliation(admissions=6)
        result = derive_qualification_v1(other, frontier(other))
    elif change == "issuers":
        other = replace(reconciled, technical_issuers=("a", "b", "c"))
        result = derive_qualification_v1(other, frontier(other))
    elif change == "cycles":
        result = derive_qualification_v1(
            reconciled, frontier(reconciled, consecutive_successes=("cycle-b", "cycle-c")))
    elif change == "profile":
        result = derive_qualification_v1(
            reconciled, frontier(reconciled, profile="sha256:" + "q" * 64))
    else:
        other = replace(reconciled, required_head_id="sha256:" + "9" * 64)
        result = derive_qualification_v1(other, frontier(other))
    assert result.qualification_id != base.qualification_id


# --- every refusal names itself ---------------------------------------------

def test_a_gap_the_census_never_declared_refuses_the_certificate():
    """The point of the scope digest: evidence that grew a hole since."""
    reconciled = reconciliation()
    declared = frontier(reconciled)
    grown = replace(reconciled, issues=(
        HistoricalEvidenceIssueV1("receipt", "later", "producer_binding_missing_or_ambiguous"),))
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(grown, declared)
    assert raised.value.code == "undisclosed_evidence_gap"


def test_completed_cycles_cannot_qualify_a_different_required_head():
    reconciled = reconciliation()
    frozen = frontier(reconciled)
    changed = replace(reconciled, required_head_id="sha256:" + "9" * 64)
    with pytest.raises(QualificationRefused) as refused:
        derive_qualification_v1(changed, frozen)
    assert refused.value.code == "profile_head_mismatch"


def test_a_declared_and_closed_gap_does_not_block_the_certificate():
    issues = (HistoricalEvidenceIssueV1("receipt", "known", "historical_context_policy_unavailable"),)
    reconciled = reconciliation(issues=issues)
    assert derive_qualification_v1(reconciled, frontier(reconciled)).technical_admissions == 5


@pytest.mark.parametrize("overrides,expected", [
    ({"census_scope": None}, "census_absent"),
    ({"open_findings": ("terminal-binding",)}, "open_defect"),
    ({"pending_cycle": "cycle-c"}, "cycle_interrupted"),
    ({"profile": None}, "profile_absent"),
    ({"profile_bindings": None}, "profile_bindings_absent"),
    ({"consecutive_successes": ("cycle-a",)}, "consecutive_cycles_insufficient"),
    ({"consecutive_successes": ()}, "consecutive_cycles_insufficient"),
])
def test_an_incomplete_evidence_ledger_refuses_and_says_which_part(overrides, expected):
    reconciled = reconciliation()
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(reconciled, frontier(reconciled, **overrides))
    assert raised.value.code == expected


@pytest.mark.parametrize("admissions", [0, 4])
def test_too_few_admissions_refuses_with_the_count(admissions):
    reconciled = reconciliation(admissions=admissions)
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(reconciled, frontier(reconciled))
    assert raised.value.code == "technical_admissions_insufficient"
    assert raised.value.detail == str(admissions)


def test_one_producer_is_not_two():
    reconciled = replace(reconciliation(), technical_issuers=("builtin_contract_generator",))
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(reconciled, frontier(reconciled))
    assert raised.value.code == "authenticated_producers_insufficient"


def test_the_same_admission_counted_twice_is_refused():
    reconciled = reconciliation()
    duplicated = replace(reconciled, technical_acts=reconciled.technical_acts[:4]
                         + (reconciled.technical_acts[0],))
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(duplicated, frontier(duplicated))
    assert raised.value.code == "duplicate_admission"


def test_a_history_without_a_frontier_cannot_qualify():
    reconciled = replace(reconciliation(), required_head_id=None)
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(reconciled, frontier(reconciled))
    assert raised.value.code == "qualification_input_invalid"


def test_no_caller_supplied_count_is_accepted():
    """There is no parameter to inflate: the two observations are the input."""
    import inspect

    parameters = inspect.signature(derive_qualification_v1).parameters
    assert list(parameters) == ["reconciliation", "frontier"]
    assert all(item.default is inspect.Parameter.empty for item in parameters.values())


# --- the quarantine exclusion -----------------------------------------------

def test_a_quarantine_is_not_a_technical_admission():
    """A withdrawal of authority must not raise the admission threshold."""
    from executor_birth_history import HistoricalBirthReconciliationV1

    acts = tuple(act(number) for number in range(4)) + (
        HistoricalActV1(object(), "sha256:" + "f" * 64, "promoter", "namespace",
                        "sha256:" + "c" * 64, "quarantine"),)
    observed = HistoricalBirthReconciliationV1(HEAD, 5, 5, 5, acts, ())
    assert len(observed.technical_acts) == 4
    with pytest.raises(QualificationRefused) as raised:
        derive_qualification_v1(observed, frontier(observed))
    assert raised.value.code == "technical_admissions_insufficient"
