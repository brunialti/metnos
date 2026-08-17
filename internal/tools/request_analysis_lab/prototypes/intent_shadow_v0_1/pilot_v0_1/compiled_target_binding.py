"""Bind a compiled Metnos step to an exact target, without another LLM.

Laboratory only.  The adapter reads the same ``StepSpec.args`` and producer
``StepRun.result`` shapes used by the runtime.  Route-specific knowledge stays
in the typed contract supplied from an executor manifest.

The rule is intentionally small:

* a concrete target argument wins;
* otherwise ``from_step`` is projected from the materialized producer result;
* otherwise a required target is unknown and execution is blocked downstream.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from runtime.engine.types import StepRun, StepSpec
from runtime.from_step_projection import (
    CONTEXT_ERRORS_KEY,
    project_from_entries,
)

from .safe_target_flow import (
    Effect,
    OperationStep,
    TargetRef,
    UnknownTarget,
    direct_target,
    previous_target,
    target_ref,
)


@dataclass(frozen=True, slots=True)
class TargetContract:
    """Manifest-derived description of the target-bearing arguments."""

    schema: dict[str, Any]
    target_args: tuple[str, ...]
    context_args: tuple[str, ...] = ()
    requires_target: bool = True
    effect: Effect = Effect.READ_ONLY
    approval_required: bool = False

    def __post_init__(self) -> None:
        names = self.target_args + self.context_args
        if type(self.schema) is not dict:
            raise ValueError("schema must be a dict")
        if any(type(name) is not str or not name for name in names):
            raise ValueError("argument names must be non-empty strings")
        if len(names) != len(set(names)):
            raise ValueError("argument names must be unique")
        if self.requires_target and not self.target_args:
            raise ValueError("a required target needs at least one target arg")
        if type(self.requires_target) is not bool:
            raise ValueError("requires_target must be bool")
        if type(self.approval_required) is not bool:
            raise ValueError("approval_required must be bool")
        if type(self.effect) is not Effect:
            raise ValueError("effect must be Effect")


def _provided(value: object) -> bool:
    if value is None:
        return False
    if type(value) is str:
        return bool(value.strip())
    if type(value) in (list, tuple, dict, set):
        return bool(value)
    return True


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _scope(args: dict[str, Any], names: tuple[str, ...]) -> str | None:
    values: list[tuple[str, object]] = []
    for name in names:
        value = args.get(name)
        if not _provided(value):
            return None
        values.append((name, value))
    return _canonical(values)


def _target_family(name: str, alternatives: tuple[str, ...]) -> str:
    """Collapse an explicitly declared singular/plural argument pair."""
    if name.endswith("s") and name[:-1] in alternatives:
        return name[:-1]
    if f"{name}s" in alternatives:
        return name
    return name


def _items(args: dict[str, Any], contract: TargetContract) -> tuple[TargetRef, ...] | None:
    present = [
        name for name in contract.target_args
        if name in args and _provided(args[name])
    ]
    if not present:
        return None
    families = {
        _target_family(name, contract.target_args)
        for name in present
    }
    if len(families) != 1:
        return None
    family = next(iter(families))
    values: list[object] = []
    for name in present:
        value = args[name]
        values.extend(value if type(value) in (list, tuple) else (value,))
    scope = _scope(args, contract.context_args)
    if scope is None:
        return None
    try:
        items = tuple(
            target_ref(family, scope, _canonical(item))
            for item in values
        )
        # The boundary treats targets as a set.  Repeating the exact same
        # target is harmless input noise: collapse it, never execute it twice.
        return tuple(dict.fromkeys(items))
    except (TypeError, ValueError):
        return None


def _payload(result: object) -> list | None:
    if type(result) is not dict:
        return None
    for name in ("entries", "results", "lines", "matches"):
        value = result.get(name)
        if type(value) is list:
            return value
    return None


def _producer(history: tuple[StepRun, ...], one_based_step: int) -> StepRun | None:
    for step in history:
        if step.step_idx == one_based_step:
            return step
    if 1 <= one_based_step <= len(history):
        return history[one_based_step - 1]
    return None


def bind_compiled_step(
    step: StepSpec,
    ordinal: int,
    contract: TargetContract,
    history: tuple[StepRun, ...] = (),
) -> OperationStep:
    """Convert one compiled step into the deterministic safety boundary."""
    if type(step) is not StepSpec or type(step.args) is not dict:
        raise ValueError("step must be a StepSpec with dict args")
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer")

    args = dict(step.args)
    direct_names = [
        name for name in contract.target_args
        if name in args and _provided(args[name])
    ]
    direct = _items(args, contract) if direct_names else None
    if direct_names:
        # A malformed/incomplete explicit target must never fall through to a
        # broader producer result.  This is the important over-deletion guard.
        target = direct_target(*direct) if direct is not None else UnknownTarget()
        after_step = None
    elif not contract.requires_target:
        target = None
        after_step = None
    else:
        source = args.get("from_step")
        if type(source) is str and source.isdigit():
            source = int(source)
        source_ordinal = source - 1 if type(source) is int else None
        producer = _producer(history, source) if type(source) is int else None
        payload = _payload(producer.result) if producer is not None else None
        if source_ordinal is None or payload is None:
            target = UnknownTarget()
            after_step = None
        elif not payload:
            target = previous_target(source_ordinal)
            after_step = source_ordinal
        else:
            projected_args = {
                key: value for key, value in args.items()
                if key != "from_step"
                and (key not in contract.target_args or _provided(value))
            }
            projected_args, _ = project_from_entries(
                projected_args,
                payload,
                contract.schema,
            )
            projected = None if projected_args.get(CONTEXT_ERRORS_KEY) else _items(
                projected_args,
                contract,
            )
            if projected is None:
                target = UnknownTarget()
                after_step = None
            else:
                target = previous_target(source_ordinal, *projected)
                after_step = source_ordinal

    return OperationStep(
        route=step.tool,
        ordinal=ordinal,
        effect=contract.effect,
        requires_target=contract.requires_target,
        approval_required=contract.approval_required,
        target=target,
        after_step=after_step,
    )
