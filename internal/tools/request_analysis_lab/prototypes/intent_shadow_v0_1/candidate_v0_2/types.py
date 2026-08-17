"""Immutable model-facing and compiled types for candidate 0.2."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


PathPart: TypeAlias = str | int


@dataclass(frozen=True, slots=True)
class IROperation:
    route: str
    sources: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class IRBarrier:
    barrier: str
    body: tuple[IRStep, ...]


IRStep: TypeAlias = IROperation | IRBarrier


@dataclass(frozen=True, slots=True)
class IROperationGraph:
    steps: tuple[IRStep, ...]


@dataclass(frozen=True, slots=True)
class IRSystemControl:
    control: str


@dataclass(frozen=True, slots=True)
class IRUnrepresentable:
    reason: str


IRDocument: TypeAlias = IROperationGraph | IRSystemControl | IRUnrepresentable


@dataclass(frozen=True, slots=True)
class DataEdge:
    source_ordinal: int
    output_port: str
    input_port: str


@dataclass(frozen=True, slots=True)
class CompiledOperation:
    node_path: tuple[PathPart, ...]
    ordinal: int
    route: str
    data_from: tuple[DataEdge, ...]


@dataclass(frozen=True, slots=True)
class CompiledOutcome:
    outcome: str
    body: tuple[CompiledStep, ...]


@dataclass(frozen=True, slots=True)
class CompiledBarrier:
    node_path: tuple[PathPart, ...]
    barrier: str
    outcomes: tuple[CompiledOutcome, ...]


CompiledStep: TypeAlias = CompiledOperation | CompiledBarrier


@dataclass(frozen=True, slots=True)
class CompiledOperationGraph:
    steps: tuple[CompiledStep, ...]


@dataclass(frozen=True, slots=True)
class CompiledSystemControl:
    control: str


@dataclass(frozen=True, slots=True)
class CompiledUnrepresentable:
    reason: str


CompiledDocument: TypeAlias = (
    CompiledOperationGraph | CompiledSystemControl | CompiledUnrepresentable
)


@dataclass(frozen=True, slots=True)
class Continuation:
    contract_version: str
    registry_sha256: str
    root_document_sha256: str
    barrier_path: tuple[PathPart, ...]
    outcome: str
    body: tuple[CompiledStep, ...]
    continuation_sha256: str


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    document: IRDocument | None
    issues: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class CompilationResult:
    valid: bool
    document: CompiledDocument | None
    document_sha256: str | None
    continuations: tuple[Continuation, ...]
    issues: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class DisagreementSignal:
    required: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CriticRequest:
    query: str
    primary_ir: bytes
    registry_sha256: str
    prompt_sha256: str
    schema_sha256: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    request_sha256: str
    raw_response_sha256: str
    compilation: CompilationResult
    disagreement: DisagreementSignal
    critic_request: CriticRequest | None
    model_calls: int
