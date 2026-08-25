"""Core-owned orchestration of the seven RM-0008 property groups."""
from __future__ import annotations

import hashlib
import math
import re
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
    observations: Mapping[str, object]
    runner_attestation_hash: str


class PropertyRunner(Protocol):
    def run(self, case: PropertyCase, *, fixture_id: str, isolation: str) -> PropertyRunResult: ...


@dataclass(frozen=True, slots=True)
class PropertyCandidateProfile:
    """Core-derived applicability facts; never decoded from a manifest table."""
    output_schema: tuple[tuple[str, str], ...] = ()
    collection_output: bool = False
    limit_input: bool = False
    truncation_declared: bool = False
    revertible: bool = False
    destructive_with_undo: bool = False
    entries_and_results: bool = False

    def __post_init__(self) -> None:
        allowed_types = {"array", "boolean", "integer", "null", "number", "object", "string"}
        keys: set[str] = set()
        for item in self.output_schema:
            if (
                not isinstance(item, tuple) or len(item) != 2
                or not isinstance(item[0], str) or not item[0]
                or item[0] in keys or not isinstance(item[1], str)
                or item[1] not in allowed_types
            ):
                raise PropertyContractError("property_candidate_invalid", "output_schema")
            keys.add(item[0])
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


def _truncation_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    expectation = {"fixture_total": 3, "limit": 2}
    return (PropertyCase("truncation.boundary", expectation, expectation),)


_GENERATORS = {
    "declared_output_cases": _single("output.actual"),
    "cardinality_cases": _collection_cases,
    "limit_boundary_cases": _limit_cases,
    "truncation_cases": _truncation_cases,
    "undo_round_trip_cases": _single("undo.round_trip"),
    "delete_copy_cases": _single("delete.copy_before"),
    "entries_results_cases": _single("entries.results"),
}


def _output_schema(output, candidate, _expect, _observations):
    def matches(value: object, type_name: str) -> bool:
        return {
            "array": lambda: isinstance(value, list),
            "boolean": lambda: type(value) is bool,
            "integer": lambda: type(value) is int,
            "null": lambda: value is None,
            "number": lambda: type(value) is int or (
                type(value) is float and math.isfinite(value)
            ),
            "object": lambda: isinstance(value, Mapping),
            "string": lambda: isinstance(value, str),
        }[type_name]()

    return bool(candidate.output_schema) and all(
        key in output and matches(output[key], type_name)
        for key, type_name in candidate.output_schema
    )


def _cardinality(output, _candidate, expect, _observations):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) == expect["count"]


def _limit(output, _candidate, expect, _observations):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) <= expect["max_count"]


def _truncation(output, _candidate, expect, observations):
    entries = output.get("entries", output.get("results"))
    return (
        isinstance(entries, list)
        and len(entries) == expect["limit"]
        and output.get("truncated") is True
        and observations.get("fixture_total") == expect["fixture_total"]
    )


def _state_round_trip(_output, _candidate, _expect, observations):
    before = observations.get("state_before_hash")
    mutated = observations.get("state_after_forward_hash")
    restored = observations.get("state_after_undo_hash")
    return (
        isinstance(before, str) and _DIGEST_RE.fullmatch(before) is not None
        and isinstance(mutated, str) and _DIGEST_RE.fullmatch(mutated) is not None
        and isinstance(restored, str) and _DIGEST_RE.fullmatch(restored) is not None
        and restored == before and mutated != before
    )


def _copy_precedes_delete(_output, _candidate, _expect, observations):
    events = observations.get("filesystem_events")
    source = observations.get("source_before_hash")
    recovery = observations.get("recovery_copy_hash")
    return (
        isinstance(events, list)
        and events.count("copy") == 1
        and events.count("delete") == 1
        and events.index("copy") < events.index("delete")
        and isinstance(source, str) and _DIGEST_RE.fullmatch(source) is not None
        and isinstance(recovery, str) and _DIGEST_RE.fullmatch(recovery) is not None
        and recovery == source
    )


def _coherent(output, _candidate, _expect, _observations):
    return isinstance(output.get("entries"), list) and output.get("entries") == output.get("results")


_ORACLES = {
    "output_schema": _output_schema,
    "cardinality": _cardinality,
    "limit_semantics": _limit,
    "truncation": _truncation,
    "state_round_trip": _state_round_trip,
    "copy_precedes_delete": _copy_precedes_delete,
    "entries_results_coherence": _coherent,
}

_FIXTURES = frozenset({
    "empty_private_root", "bounded_collection", "oversized_collection",
    "private_mutable_state", "private_deletion_tree",
})
_APPLICABILITY = {
    "output_schema_declared": lambda c: bool(c.output_schema),
    "collection_output": lambda c: c.collection_output,
    "bounded_collection_input": lambda c: c.limit_input,
    "truncation_declared": lambda c: c.truncation_declared,
    "revertible_executor": lambda c: c.revertible,
    "destructive_with_undo": lambda c: c.destructive_with_undo,
    "entries_and_results_output": lambda c: c.entries_and_results,
}

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


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
        except PropertyContractError:
            raise
        except Exception as exc:  # runner unavailability is fail-closed evidence
            status = PropertyStatus.UNAVAILABLE
            error = "property_runner_unavailable"
            output_hash = _hash({"unavailable": type(exc).__name__})
            attestation_hash = _hash({"attestation": "unavailable"})
        else:
            if not isinstance(result, PropertyRunResult):
                raise PropertyContractError("property_runner_result_invalid")
            if not isinstance(result.output, Mapping) or not isinstance(result.observations, Mapping):
                raise PropertyContractError("property_runner_result_invalid", "mappings")
            if not isinstance(result.runner_attestation_hash, str) or _DIGEST_RE.fullmatch(result.runner_attestation_hash) is None:
                raise PropertyContractError("property_runner_result_invalid", "attestation")
            passed = oracle(
                result.output, candidate, case.expectation, result.observations,
            )
            status = PropertyStatus.PASSED if passed else PropertyStatus.FAILED
            error = "" if passed else "property_oracle_failed"
            output_hash = _hash({
                "output": dict(result.output),
                "trusted_observations": dict(result.observations),
            })
            attestation_hash = result.runner_attestation_hash
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
