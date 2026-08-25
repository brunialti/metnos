"""Core-owned orchestration of the seven RM-0008 property groups."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Protocol

from executor_birth_identity import encode_framed_v1
from executor_birth_properties import (
    PROPERTY_CATALOG_V1,
    PropertyContractError,
    PropertyEvidence,
    PropertySpec,
    PropertyStatus,
)


@dataclass(frozen=True, slots=True)
class PropertyCase:
    case_id: str
    input_value: Mapping[str, object]
    expectation: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PropertyRunResult:
    output: Mapping[str, object]
    runner_attestation_hash: str


class PropertyRunner(Protocol):
    def run(self, case: PropertyCase, *, fixture_id: str, isolation: str) -> PropertyRunResult: ...


@dataclass(frozen=True, slots=True)
class PropertyCandidateProfile:
    """Core-derived applicability facts; never decoded from a manifest table."""
    output_required: tuple[str, ...] = ()
    collection_output: bool = False
    limit_input: bool = False
    truncation_declared: bool = False
    revertible: bool = False
    destructive_with_undo: bool = False
    entries_and_results: bool = False

    def __post_init__(self) -> None:
        if any(not isinstance(item, str) or not item for item in self.output_required):
            raise PropertyContractError("property_candidate_invalid", "output_required")
        for name in (
            "collection_output", "limit_input", "truncation_declared", "revertible",
            "destructive_with_undo", "entries_and_results",
        ):
            if type(getattr(self, name)) is not bool:
                raise PropertyContractError("property_candidate_invalid", name)


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(encode_framed_v1(value)).hexdigest()


def _collection_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    return tuple(PropertyCase(f"cardinality.{count}", {"fixture_count": count}, {"count": count}) for count in (0, 1, 3))


def _limit_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    return (PropertyCase("limit.0", {"fixture_count": 3, "limit": 0}, {"max_count": 0}),
            PropertyCase("limit.below_total", {"fixture_count": 3, "limit": 2}, {"max_count": 2}))


def _single(case_id: str):
    def generate(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
        return (PropertyCase(case_id, {}, {}),)
    return generate


_GENERATORS = {
    "declared_output_cases": _single("output.actual"),
    "cardinality_cases": _collection_cases,
    "limit_boundary_cases": _limit_cases,
    "truncation_cases": _single("truncation.boundary"),
    "undo_round_trip_cases": _single("undo.round_trip"),
    "delete_copy_cases": _single("delete.copy_before"),
    "entries_results_cases": _single("entries.results"),
}


def _output_schema(output, candidate, _expect):
    return bool(candidate.output_required) and all(key in output for key in candidate.output_required)


def _cardinality(output, _candidate, expect):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) == expect["count"]


def _limit(output, _candidate, expect):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) <= expect["max_count"]


def _flag(name: str):
    return lambda output, _candidate, _expect: output.get(name) is True


def _coherent(output, _candidate, _expect):
    return isinstance(output.get("entries"), list) and output.get("entries") == output.get("results")


_ORACLES = {
    "output_schema": _output_schema,
    "cardinality": _cardinality,
    "limit_semantics": _limit,
    "truncation": _flag("truncation_attested"),
    "state_round_trip": _flag("state_round_trip_attested"),
    "copy_precedes_delete": _flag("copy_precedes_delete_attested"),
    "entries_results_coherence": _coherent,
}

_FIXTURES = frozenset({
    "empty_private_root", "bounded_collection", "oversized_collection",
    "private_mutable_state", "private_deletion_tree",
})
_APPLICABILITY = {
    "output_schema_declared": lambda c: bool(c.output_required),
    "collection_output": lambda c: c.collection_output,
    "bounded_collection_input": lambda c: c.limit_input,
    "truncation_declared": lambda c: c.truncation_declared,
    "revertible_executor": lambda c: c.revertible,
    "destructive_with_undo": lambda c: c.destructive_with_undo,
    "entries_and_results_output": lambda c: c.entries_and_results,
}


def _resolve(spec: PropertySpec):
    try:
        return (_APPLICABILITY[spec.applicability_id], _GENERATORS[spec.generator_id],
                _ORACLES[spec.oracle_id])
    except KeyError as exc:  # closed core configuration failure
        raise PropertyContractError("property_registry_invalid", str(exc)) from exc


def run_property(
    property_id: str,
    candidate: PropertyCandidateProfile,
    *,
    _runner: PropertyRunner,
) -> tuple[PropertyEvidence, ...]:
    """Run a core property.  `_runner` is a Birth-owned/test seam, not manifest data."""
    if not isinstance(candidate, PropertyCandidateProfile):
        raise PropertyContractError("property_candidate_invalid", "profile")
    try:
        spec = PROPERTY_CATALOG_V1[property_id]
    except KeyError as exc:
        raise PropertyContractError("property_unknown", property_id) from exc
    applicable, generate, oracle = _resolve(spec)
    if spec.fixture_id not in _FIXTURES:
        raise PropertyContractError("property_registry_invalid", spec.fixture_id)
    if not applicable(candidate):
        return ()
    cases = generate(candidate)
    if not cases or len(cases) > spec.max_cases:
        raise PropertyContractError("property_cases_invalid", property_id)
    evidence = []
    for case in cases:
        try:
            result = _runner.run(case, fixture_id=spec.fixture_id, isolation=spec.isolation.value)
            passed = oracle(result.output, candidate, case.expectation)
            status = PropertyStatus.PASSED if passed else PropertyStatus.FAILED
            error = "" if passed else "property_oracle_failed"
            output_hash = _hash(dict(result.output))
            attestation_hash = result.runner_attestation_hash
        except Exception as exc:  # runner unavailability is fail-closed evidence
            status = PropertyStatus.UNAVAILABLE
            error = "property_runner_unavailable"
            output_hash = _hash({"unavailable": type(exc).__name__})
            attestation_hash = _hash({"attestation": "unavailable"})
        evidence.append(PropertyEvidence(
            property_id=spec.property_id, property_version=spec.version,
            case_id=case.case_id, status=status,
            input_hash=_hash(dict(case.input_value)), output_hash=output_hash,
            oracle_hash=_hash({"oracle_id": spec.oracle_id, "version": spec.version}),
            runner_attestation_hash=attestation_hash, error_code=error,
        ))
    return tuple(evidence)


def run_applicable_properties(
    candidate: PropertyCandidateProfile, *, _runner: PropertyRunner,
) -> tuple[PropertyEvidence, ...]:
    return tuple(
        evidence
        for property_id in PROPERTY_CATALOG_V1
        for evidence in run_property(property_id, candidate, _runner=_runner)
    )
