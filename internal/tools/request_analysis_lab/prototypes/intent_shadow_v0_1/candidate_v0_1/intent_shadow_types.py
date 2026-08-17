"""Immutable types for the intent-shadow laboratory candidate."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


PathPart: TypeAlias = str | int


@dataclass(frozen=True, slots=True)
class FrozenArray:
    items: tuple[FrozenJson, ...]


@dataclass(frozen=True, slots=True)
class FrozenObject:
    items: tuple[tuple[str, FrozenJson], ...]


FrozenJson: TypeAlias = None | bool | int | float | str | FrozenArray | FrozenObject


@dataclass(frozen=True, slots=True)
class DataEdge:
    source_ordinal: int
    output_port: str
    input_port: str


@dataclass(frozen=True, slots=True)
class Operation:
    node_path: tuple[PathPart, ...]
    ordinal: int
    route: str
    data_from: tuple[DataEdge, ...]


@dataclass(frozen=True, slots=True)
class OutcomeCase:
    outcome: str
    body: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class BarrierRegion:
    node_path: tuple[PathPart, ...]
    barrier: str
    data_from: tuple[DataEdge, ...]
    cases: tuple[OutcomeCase, ...]


Node: TypeAlias = Operation | BarrierRegion


@dataclass(frozen=True, slots=True)
class OperationGraph:
    body: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class SystemControl:
    control: str
    inputs: FrozenObject | None


@dataclass(frozen=True, slots=True)
class Unrepresentable:
    reason: str


RootDocument: TypeAlias = OperationGraph | SystemControl | Unrepresentable


@dataclass(frozen=True, slots=True)
class Continuation:
    contract_version: str
    registry_sha256: str
    root_document_sha256: str
    barrier_path: tuple[PathPart, ...]
    outcome_ref: str
    typed_body: tuple[Node, ...]
    continuation_sha256: str


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class TechnicalFailure:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    document: RootDocument
    normalized_bytes: bytes
    normalized_document_sha256: str
    continuations: tuple[Continuation, ...]


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    document: RootDocument
    semantic_bytes: bytes
    semantic_sha256: str


@dataclass(frozen=True, slots=True)
class ExtractionEnvelope:
    contract_version: str
    registry_sha256: str
    raw_model_output: bytes
    raw_model_output_sha256: str
    decoded_document_canonical: bytes | None
    technical_failure: TechnicalFailure | None
    validation_result: ValidationResult
    normalization_result: NormalizationResult | None
    projection_result: ProjectionResult | None


@dataclass(frozen=True, slots=True)
class ModelContract:
    contract_version: str
    registry_sha256: str
    schema: FrozenObject
    prompt: str
    schema_sha256: str
    prompt_sha256: str

