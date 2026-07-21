"""Bounded intelligence runtime for drop-in executors.

The planner-facing executor contract remains unchanged. This module owns the
internal fallback loop: bounded observations, validated proposals, explicit
postconditions and causal failure outcomes. It is provider-agnostic; adapters
may use an LLM, VLM or another domain-specific reasoner.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class AgenticLimits:
    max_attempts: int = 3
    max_observation_chars: int = 12000
    max_history_items: int = 24
    max_elapsed_s: float = 30.0

    def bounded_attempts(self) -> int:
        return max(1, min(int(self.max_attempts), 10))

    def bounded_observation_chars(self) -> int:
        return max(1, min(int(self.max_observation_chars), 1_000_000))

    def bounded_history_items(self) -> int:
        return max(1, min(int(self.max_history_items), 100))

    def bounded_elapsed_s(self) -> float:
        return max(0.01, min(float(self.max_elapsed_s), 3600.0))


@dataclass
class AgenticContext:
    goal: Any
    observed: Any
    constraints: dict[str, Any] = field(default_factory=dict)
    history: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class AgenticProposal:
    action: Any
    evidence: Any = None


@dataclass(frozen=True)
class AgenticOutcome:
    status: str  # completed | inconclusive | exhausted
    result: Any = None
    attempts: int = 0
    reason: str = ""


def _bounded_data(value: Any, max_chars: int) -> Any:
    """Keep JSON-like observations within a deterministic prompt budget.

    Values that already fit retain their type. Oversized values become a plain
    preview string, so adapters fail closed rather than reasoning over an
    incomplete structure that still looks authoritative.
    """
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                             default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= max_chars:
        return value
    return encoded[:max_chars]


def _prepare_context(context: AgenticContext, limits: AgenticLimits) -> None:
    max_chars = limits.bounded_observation_chars()
    context.goal = _bounded_data(context.goal, max_chars)
    context.observed = _bounded_data(context.observed, max_chars)
    history = list(context.history[-limits.bounded_history_items():])
    bounded_history = _bounded_data(history, max_chars)
    context.history = (bounded_history if isinstance(bounded_history, list)
                       else [bounded_history])


def _record(context: AgenticContext, limits: AgenticLimits,
            status: str, attempt: int) -> None:
    # Evidence can contain provider or page data, so the common trace records
    # only language-neutral control state.
    context.history.append({"status": status, "attempt": attempt})
    context.history[:] = context.history[-limits.bounded_history_items():]


def _final_outcome(*, attempts: int, executed: int,
                   last_reason: str) -> AgenticOutcome:
    if last_reason == "elapsed_budget_exhausted":
        return AgenticOutcome("exhausted", None, attempts, last_reason)
    if executed == 0:
        return AgenticOutcome(
            "inconclusive", None, attempts,
            last_reason or "no_valid_proposal",
        )
    return AgenticOutcome(
        "exhausted", None, attempts,
        last_reason or "postcondition_not_met",
    )


async def run_bounded(
    *,
    context: AgenticContext,
    propose: Callable[[AgenticContext], Awaitable[AgenticProposal | None]],
    execute: Callable[[AgenticProposal, AgenticContext], Awaitable[Any]],
    validate: Callable[[AgenticProposal, AgenticContext], bool],
    postcondition: Callable[[Any, AgenticContext], bool],
    observe: Callable[[AgenticContext], Awaitable[Any]] | None = None,
    limits: AgenticLimits | None = None,
) -> AgenticOutcome:
    """Run a bounded internal-agent fallback without changing executor I/O.

    ``max_elapsed_s`` is a retry budget, not a cancellation mechanism for a
    possibly mutating action. Individual adapters still own operation timeouts.
    """
    lim = limits or AgenticLimits()
    _prepare_context(context, lim)
    started = time.monotonic()
    attempts = 0
    executed = 0
    last_reason = ""

    for _ in range(lim.bounded_attempts()):
        if time.monotonic() - started >= lim.bounded_elapsed_s():
            last_reason = "elapsed_budget_exhausted"
            break
        attempts += 1
        if observe is not None:
            context.observed = await observe(context)
            _prepare_context(context, lim)
        proposal = await propose(context)
        if proposal is None:
            last_reason = "no_proposal"
            _record(context, lim, last_reason, attempts)
            continue
        if not validate(proposal, context):
            last_reason = "proposal_rejected"
            _record(context, lim, last_reason, attempts)
            continue
        if time.monotonic() - started >= lim.bounded_elapsed_s():
            last_reason = "elapsed_budget_exhausted"
            _record(context, lim, last_reason, attempts)
            break
        result = await execute(proposal, context)
        executed += 1
        if postcondition(result, context):
            _record(context, lim, "completed", attempts)
            return AgenticOutcome("completed", result, attempts)
        last_reason = "postcondition_not_met"
        _record(context, lim, last_reason, attempts)

    return _final_outcome(
        attempts=attempts, executed=executed, last_reason=last_reason)


def run_bounded_sync(
    *,
    context: AgenticContext,
    propose: Callable[[AgenticContext], AgenticProposal | None],
    execute: Callable[[AgenticProposal, AgenticContext], Any],
    validate: Callable[[AgenticProposal, AgenticContext], bool],
    postcondition: Callable[[Any, AgenticContext], bool],
    observe: Callable[[AgenticContext], Any] | None = None,
    limits: AgenticLimits | None = None,
) -> AgenticOutcome:
    """Synchronous equivalent for stdio executors."""
    lim = limits or AgenticLimits()
    _prepare_context(context, lim)
    started = time.monotonic()
    attempts = 0
    executed = 0
    last_reason = ""

    for _ in range(lim.bounded_attempts()):
        if time.monotonic() - started >= lim.bounded_elapsed_s():
            last_reason = "elapsed_budget_exhausted"
            break
        attempts += 1
        if observe is not None:
            context.observed = observe(context)
            _prepare_context(context, lim)
        proposal = propose(context)
        if proposal is None:
            last_reason = "no_proposal"
            _record(context, lim, last_reason, attempts)
            continue
        if not validate(proposal, context):
            last_reason = "proposal_rejected"
            _record(context, lim, last_reason, attempts)
            continue
        if time.monotonic() - started >= lim.bounded_elapsed_s():
            last_reason = "elapsed_budget_exhausted"
            _record(context, lim, last_reason, attempts)
            break
        result = execute(proposal, context)
        executed += 1
        if postcondition(result, context):
            _record(context, lim, "completed", attempts)
            return AgenticOutcome("completed", result, attempts)
        last_reason = "postcondition_not_met"
        _record(context, lim, last_reason, attempts)

    return _final_outcome(
        attempts=attempts, executed=executed, last_reason=last_reason)


async def deterministic_then_fallback(
    *,
    deterministic: Callable[[], Awaitable[Any]],
    needs_fallback: Callable[[Any], bool],
    context: Callable[[Any], AgenticContext],
    propose: Callable[[AgenticContext], Awaitable[AgenticProposal | None]],
    execute: Callable[[AgenticProposal, AgenticContext], Awaitable[Any]],
    validate: Callable[[AgenticProposal, AgenticContext], bool],
    postcondition: Callable[[Any, AgenticContext], bool],
    observe: Callable[[AgenticContext], Awaitable[Any]] | None = None,
    limits: AgenticLimits | None = None,
) -> Any:
    """Asynchronous deterministic-first adapter preserving fallback safety."""
    primary = await deterministic()
    if not needs_fallback(primary):
        return primary
    outcome = await run_bounded(
        context=context(primary), propose=propose, execute=execute,
        validate=validate, postcondition=postcondition, observe=observe,
        limits=limits,
    )
    return outcome.result if outcome.status == "completed" else primary


def deterministic_then_fallback_sync(
    *,
    deterministic: Callable[[], Any],
    needs_fallback: Callable[[Any], bool],
    context: Callable[[Any], AgenticContext],
    propose: Callable[[AgenticContext], AgenticProposal | None],
    execute: Callable[[AgenticProposal, AgenticContext], Any],
    validate: Callable[[AgenticProposal, AgenticContext], bool],
    postcondition: Callable[[Any, AgenticContext], bool],
    observe: Callable[[AgenticContext], Any] | None = None,
    limits: AgenticLimits | None = None,
) -> Any:
    """Return deterministic output unless a validated fallback improves it."""
    primary = deterministic()
    if not needs_fallback(primary):
        return primary
    outcome = run_bounded_sync(
        context=context(primary), propose=propose, execute=execute,
        validate=validate, postcondition=postcondition, observe=observe,
        limits=limits,
    )
    return outcome.result if outcome.status == "completed" else primary
