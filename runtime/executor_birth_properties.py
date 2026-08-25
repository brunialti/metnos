"""Closed property and independent-signal contracts for RM-0008 Birth.

The catalog contains identifiers only.  Candidate manifests cannot supply
callables, module paths, generators, fixtures or oracles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class PropertyContractError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class PropertyStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class IsolationProfile(str, Enum):
    PURE = "pure"
    PRIVATE_READ_ONLY = "private_read_only"
    PRIVATE_MUTATING = "private_mutating"


class IndependentSignalKind(str, Enum):
    DETERMINISTIC_ORACLE = "deterministic_oracle"
    VERSIONED_HUMAN_CASE = "versioned_human_case"
    METAMORPHIC_RELATION = "metamorphic_relation"


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PropertyContractError("property_contract_invalid", field)
    return value


def _digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise PropertyContractError("property_evidence_invalid", field)
    return value


@dataclass(frozen=True, slots=True)
class PropertySpec:
    property_id: str
    version: str
    applicability_id: str
    owner_id: str
    generator_id: str
    fixture_id: str
    oracle_id: str
    isolation: IsolationProfile
    mandatory: bool
    max_cases: int

    def __post_init__(self) -> None:
        for field in (
            "property_id", "version", "applicability_id", "owner_id",
            "generator_id", "fixture_id", "oracle_id",
        ):
            _identifier(getattr(self, field), field=field)
        if not isinstance(self.isolation, IsolationProfile):
            raise PropertyContractError("property_contract_invalid", "isolation")
        if type(self.mandatory) is not bool:
            raise PropertyContractError("property_contract_invalid", "mandatory")
        if type(self.max_cases) is not int or not 1 <= self.max_cases <= 64:
            raise PropertyContractError("property_contract_invalid", "max_cases")


def _spec(
    property_id: str, applicability: str, generator: str, fixture: str,
    oracle: str, isolation: IsolationProfile, max_cases: int,
) -> PropertySpec:
    return PropertySpec(
        property_id=property_id,
        version="v1",
        applicability_id=applicability,
        owner_id="executor_birth",
        generator_id=generator,
        fixture_id=fixture,
        oracle_id=oracle,
        isolation=isolation,
        mandatory=True,
        max_cases=max_cases,
    )


_SPECS = (
    _spec("output.schema.actual", "output_schema_declared", "declared_output_cases",
          "empty_private_root", "output_schema", IsolationProfile.PRIVATE_READ_ONLY, 8),
    _spec("cardinality.zero_one_many", "collection_output", "cardinality_cases",
          "bounded_collection", "cardinality", IsolationProfile.PRIVATE_READ_ONLY, 3),
    _spec("limit.zero_and_below_total", "bounded_collection_input", "limit_boundary_cases",
          "bounded_collection", "limit_semantics", IsolationProfile.PRIVATE_READ_ONLY, 4),
    _spec("truncation.contract", "truncation_declared", "truncation_cases",
          "oversized_collection", "truncation", IsolationProfile.PRIVATE_READ_ONLY, 4),
    _spec("undo.round_trip", "revertible_executor", "undo_round_trip_cases",
          "private_mutable_state", "state_round_trip", IsolationProfile.PRIVATE_MUTATING, 8),
    _spec("delete.copy_before_delete", "destructive_with_undo", "delete_copy_cases",
          "private_deletion_tree", "copy_precedes_delete", IsolationProfile.PRIVATE_MUTATING, 8),
    _spec("entries.results.coherence", "entries_and_results_output", "entries_results_cases",
          "bounded_collection", "entries_results_coherence", IsolationProfile.PRIVATE_READ_ONLY, 8),
)

PROPERTY_CATALOG_V1: Mapping[str, PropertySpec] = MappingProxyType(
    {spec.property_id: spec for spec in _SPECS}
)
if len(PROPERTY_CATALOG_V1) != 7:  # pragma: no cover - import-time invariant
    raise RuntimeError("RM-0008 property catalog must contain exactly seven groups")


def property_spec(property_id: str) -> PropertySpec:
    _identifier(property_id, field="property_id")
    try:
        return PROPERTY_CATALOG_V1[property_id]
    except KeyError as exc:
        raise PropertyContractError("property_unknown", property_id) from exc


@dataclass(frozen=True, slots=True)
class PropertyEvidence:
    property_id: str
    property_version: str
    case_id: str
    status: PropertyStatus
    input_hash: str
    output_hash: str
    oracle_hash: str
    runner_attestation_hash: str
    error_code: str = ""

    def __post_init__(self) -> None:
        spec = property_spec(self.property_id)
        if self.property_version != spec.version:
            raise PropertyContractError("property_evidence_invalid", "property_version")
        _identifier(self.case_id, field="case_id")
        if not isinstance(self.status, PropertyStatus):
            raise PropertyContractError("property_evidence_invalid", "status")
        for field in ("input_hash", "output_hash", "oracle_hash", "runner_attestation_hash"):
            _digest(getattr(self, field), field=field)
        if not isinstance(self.error_code, str):
            raise PropertyContractError("property_evidence_invalid", "error_code")
        if self.status is PropertyStatus.PASSED and self.error_code:
            raise PropertyContractError("property_evidence_invalid", "passed_error")
        if self.status in {PropertyStatus.FAILED, PropertyStatus.UNAVAILABLE} and not self.error_code:
            raise PropertyContractError("property_evidence_invalid", "missing_error")


@dataclass(frozen=True, slots=True)
class IndependentSignalEvidence:
    signal_id: str
    signal_version: str
    kind: IndependentSignalKind
    status: PropertyStatus
    candidate_id: str
    admission_context_id: str
    evidence_hash: str
    runner_attestation_hash: str
    property_evidence_hash: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.signal_id, field="signal_id")
        _identifier(self.signal_version, field="signal_version")
        if not isinstance(self.kind, IndependentSignalKind):
            raise PropertyContractError("independent_signal_invalid", "kind")
        if not isinstance(self.status, PropertyStatus):
            raise PropertyContractError("independent_signal_invalid", "status")
        _digest(self.candidate_id, field="candidate_id")
        _digest(self.admission_context_id, field="admission_context_id")
        _digest(self.evidence_hash, field="evidence_hash")
        _digest(self.runner_attestation_hash, field="runner_attestation_hash")
        if self.property_evidence_hash is not None:
            _digest(self.property_evidence_hash, field="property_evidence_hash")
