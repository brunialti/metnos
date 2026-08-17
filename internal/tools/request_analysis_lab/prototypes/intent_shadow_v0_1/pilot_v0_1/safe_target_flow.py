"""Minimal, language-neutral safety boundary for operation targets.

This is laboratory code.  It deliberately separates two facts that
``from_step`` currently conflates:

* where the exact target comes from;
* whether an operation must run after another operation.

An unresolved target never becomes an implicit wildcard or an independent
operation.  Approval, when required by the operation contract, is bound to the
exact materialized target set.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import TypeAlias


class Effect(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    UNKNOWN = "unknown"


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass(frozen=True, slots=True, order=True)
class TargetRef:
    """One exact, materialized target in a provider-neutral namespace."""

    kind: str
    scope: str
    identifier: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.kind, self.scope, self.identifier)
        ):
            raise ValueError("target fields must be non-empty strings")


@dataclass(frozen=True, slots=True)
class DirectTarget:
    items: tuple[TargetRef, ...]


@dataclass(frozen=True, slots=True)
class PreviousTarget:
    source_step: int
    items: tuple[TargetRef, ...]


@dataclass(frozen=True, slots=True)
class UnknownTarget:
    pass


TargetBinding: TypeAlias = DirectTarget | PreviousTarget | UnknownTarget


@dataclass(frozen=True, slots=True)
class OperationStep:
    route: str
    ordinal: int
    effect: Effect
    requires_target: bool
    approval_required: bool
    target: TargetBinding | None
    after_step: int | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    plan_sha256: str
    target_sha256: str
    target_count: int


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    code: str
    plan_sha256: str | None


def target_ref(kind: str, scope: str, identifier: str) -> TargetRef:
    return TargetRef(kind, scope, identifier)


def _materialized(items: tuple[TargetRef, ...]) -> tuple[TargetRef, ...]:
    if type(items) is not tuple or any(type(item) is not TargetRef for item in items):
        raise ValueError("materialized targets must be a tuple of TargetRef")
    if len(items) != len(set(items)):
        raise ValueError("materialized targets must be unique")
    return tuple(sorted(items))


def direct_target(*items: TargetRef) -> DirectTarget:
    return DirectTarget(_materialized(tuple(items)))


def previous_target(source_step: int, *items: TargetRef) -> PreviousTarget:
    if type(source_step) is not int:
        raise ValueError("source_step must be an integer")
    return PreviousTarget(source_step, _materialized(tuple(items)))


def _target_payload(target: TargetBinding | None) -> dict[str, object] | None:
    if target is None:
        return None
    if type(target) is UnknownTarget:
        return {"source": "unknown"}
    if type(target) is DirectTarget:
        source = "direct"
        source_step = None
        items = target.items
    elif type(target) is PreviousTarget:
        source = "previous_step"
        source_step = target.source_step
        items = target.items
    else:
        raise ValueError("unknown target binding")
    return {
        "source": source,
        "source_step": source_step,
        "items": [
            {"kind": item.kind, "scope": item.scope, "identifier": item.identifier}
            for item in items
        ],
    }


def _plan_payload(step: OperationStep) -> dict[str, object]:
    return {
        "route": step.route,
        "ordinal": step.ordinal,
        "effect": step.effect.value,
        "requires_target": step.requires_target,
        "approval_required": step.approval_required,
        "target": _target_payload(step.target),
        "after_step": step.after_step,
    }


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _block(code: str) -> Verdict:
    return Verdict(Decision.BLOCK, code, None)


def _validated_target(step: OperationStep) -> tuple[tuple[TargetRef, ...], str] | Verdict:
    target = step.target
    if not step.requires_target:
        if target is not None:
            return _block("TARGET_UNEXPECTED")
        return (), _digest(None)
    if target is None:
        return _block("TARGET_MISSING")
    if type(target) is UnknownTarget:
        return _block("TARGET_UNKNOWN")
    if type(target) is DirectTarget:
        items = target.items
    elif type(target) is PreviousTarget:
        if (
            type(target.source_step) is not int
            or target.source_step < 0
            or target.source_step >= step.ordinal
        ):
            return _block("TARGET_SOURCE_NOT_PRIOR")
        items = target.items
    else:
        return _block("TARGET_BINDING_INVALID")
    try:
        normalized = _materialized(items)
    except ValueError:
        return _block("TARGET_SET_INVALID")
    if normalized != items:
        return _block("TARGET_SET_NOT_CANONICAL")
    return normalized, _digest(_target_payload(target))


def evaluate(step: OperationStep, approval: Approval | None = None) -> Verdict:
    """Return the only safe next action for one already-compiled step."""
    if type(step.route) is not str or not step.route:
        return _block("ROUTE_INVALID")
    if type(step.ordinal) is not int or step.ordinal < 0:
        return _block("ORDINAL_INVALID")
    if type(step.effect) is not Effect or step.effect is Effect.UNKNOWN:
        return _block("EFFECT_UNKNOWN")
    if type(step.requires_target) is not bool or type(step.approval_required) is not bool:
        return _block("CONTRACT_INVALID")
    if step.after_step is not None:
        if type(step.after_step) is not int or step.after_step < 0 or step.after_step >= step.ordinal:
            return _block("ORDER_SOURCE_NOT_PRIOR")

    checked = _validated_target(step)
    if type(checked) is Verdict:
        return checked
    items, target_sha = checked
    plan_sha = _digest(_plan_payload(step))

    if step.approval_required and items:
        if approval is None:
            return Verdict(Decision.REQUIRE_APPROVAL, "APPROVAL_REQUIRED", plan_sha)
        if (
            type(approval) is not Approval
            or approval.plan_sha256 != plan_sha
            or approval.target_sha256 != target_sha
            or approval.target_count != len(items)
        ):
            return Verdict(Decision.BLOCK, "APPROVAL_STALE_OR_WRONG_TARGET", plan_sha)
    return Verdict(Decision.ALLOW, "SAFE_TO_EXECUTE", plan_sha)


def approval_for(step: OperationStep) -> Approval:
    verdict = evaluate(step)
    if verdict.decision is not Decision.REQUIRE_APPROVAL or verdict.plan_sha256 is None:
        raise ValueError("step is not ready for approval")
    checked = _validated_target(step)
    if type(checked) is Verdict:
        raise ValueError(checked.code)
    items, target_sha = checked
    return Approval(verdict.plan_sha256, target_sha, len(items))
