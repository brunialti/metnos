"""Immutable domain types for the query-free holdout blueprint preflight."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


Cell = Literal[
    "G1_SINGLE", "G2_COMPOUND_INDEPENDENT", "G3_COMPOUND_DEPENDENT",
    "G4_COVERAGE_BOUNDARY", "G5_LINGUISTIC_VARIATION", "S1_APPROVAL",
    "S2_NEGATION", "S3_CONDITIONAL_BRANCH", "S4_UNDO",
    "S5_MIXED_CONTROL", "S6_FALSE_ACTION_TRAP",
]


@dataclass(frozen=True, slots=True)
class Positive:
    route: str


@dataclass(frozen=True, slots=True)
class Negated:
    route: str


@dataclass(frozen=True, slots=True)
class Outside:
    family: str
    tempting_route: str | None


@dataclass(frozen=True, slots=True)
class Control:
    control: str


Obligation: TypeAlias = Positive | Negated | Outside | Control


@dataclass(frozen=True, slots=True)
class Independent:
    members: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Consumes:
    source: int
    target: int
    output_port: str
    input_port: str


@dataclass(frozen=True, slots=True)
class ExplicitOrder:
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class ApprovalOwns:
    members: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ConditionalBranches:
    true_members: tuple[int, ...]
    false_members: tuple[int, ...]
    ordered: bool


Relation: TypeAlias = Independent | Consumes | ExplicitOrder | ApprovalOwns | ConditionalBranches


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    language_tag: str
    cell: Cell
    subtype: str
    obligations: tuple[Obligation, ...]
    relations: tuple[Relation, ...]
    surface_constraints: tuple[str, ...]
    process_freeze_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewerIdentity:
    reviewer_id: str
    context_id: str


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    role: Literal["author", "pool_a", "pool_b", "native_reviewer", "cross_language_reviewer"]
    identity: ReviewerIdentity
    case_id: str
    language_tag: str
    input_bundle_sha256: str
    output_sha256: str
    query_count: int
    forbidden_reads: int
    network_calls: int
    gpu_calls: int
    postseal_edits: int
