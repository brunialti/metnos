from __future__ import annotations

import asyncio
import sys
from pathlib import Path


import agentic_executor as runtime
from agentic_executor import (
    AgenticContext, AgenticLimits, AgenticProposal,
    deterministic_then_fallback, deterministic_then_fallback_sync,
    run_bounded, run_bounded_sync,
)


def test_bounded_agent_executes_valid_proposal():
    async def run():
        calls = []

        async def propose(ctx):
            return AgenticProposal("allowed", evidence={"private": "value"})

        async def execute(proposal, ctx):
            calls.append(proposal.action)
            return {"ok": True}

        context = AgenticContext(goal="g", observed=["m1"])
        out = await run_bounded(
            context=context,
            propose=propose,
            execute=execute,
            validate=lambda proposal, _ctx: proposal.action == "allowed",
            postcondition=lambda result, _ctx: result.get("ok") is True,
        )
        assert out.status == "completed"
        assert calls == ["allowed"]
        assert "private" not in repr(context.history)

    asyncio.run(run())


def test_no_valid_proposal_is_inconclusive_and_bounded():
    async def run():
        async def propose(ctx):
            return AgenticProposal("forbidden")

        async def execute(proposal, ctx):
            raise AssertionError("must not execute invalid proposals")

        out = await run_bounded(
            context=AgenticContext(goal="g", observed=[]),
            propose=propose,
            execute=execute,
            validate=lambda proposal, _ctx: False,
            postcondition=lambda result, _ctx: bool(result),
            limits=AgenticLimits(max_attempts=2),
        )
        assert out.status == "inconclusive"
        assert out.reason == "proposal_rejected"
        assert out.attempts == 2

    asyncio.run(run())


def test_executed_proposals_without_postcondition_are_exhausted():
    out = run_bounded_sync(
        context=AgenticContext(goal="g", observed=[]),
        propose=lambda _ctx: AgenticProposal("allowed"),
        execute=lambda _proposal, _ctx: {"ok": False},
        validate=lambda _proposal, _ctx: True,
        postcondition=lambda result, _ctx: result.get("ok") is True,
        limits=AgenticLimits(max_attempts=2),
    )
    assert out.status == "exhausted"
    assert out.reason == "postcondition_not_met"
    assert out.attempts == 2


def test_sync_runner_is_bounded():
    out = run_bounded_sync(
        context=AgenticContext(goal="g", observed=[]),
        propose=lambda _ctx: AgenticProposal("x"),
        execute=lambda _proposal, _ctx: {"ok": True},
        validate=lambda _proposal, _ctx: True,
        postcondition=lambda result, _ctx: result.get("ok") is True,
    )
    assert out.status == "completed"


def test_observation_and_history_budgets_are_applied_before_proposal():
    context = AgenticContext(
        goal={"query": "y" * 500},
        observed={"body": "x" * 500},
        history=[f"event-{index}" for index in range(20)],
    )
    seen = {}

    def propose(ctx):
        seen["observed"] = ctx.observed
        seen["history"] = list(ctx.history)
        return None

    out = run_bounded_sync(
        context=context,
        propose=propose,
        execute=lambda _proposal, _ctx: None,
        validate=lambda _proposal, _ctx: True,
        postcondition=lambda _result, _ctx: False,
        limits=AgenticLimits(
            max_attempts=1, max_observation_chars=40,
            max_history_items=3,
        ),
    )
    assert out.status == "inconclusive"
    assert isinstance(seen["observed"], str)
    assert len(seen["observed"]) == 40
    assert isinstance(context.goal, str)
    assert len(context.goal) == 40
    assert len(seen["history"]) <= 3


def test_observe_refreshes_state_between_attempts():
    states = iter([{"ready": False}, {"ready": True}])
    context = AgenticContext(goal="g", observed=None)
    out = run_bounded_sync(
        context=context,
        observe=lambda _ctx: next(states),
        propose=lambda _ctx: AgenticProposal("check"),
        execute=lambda _proposal, ctx: ctx.observed,
        validate=lambda _proposal, _ctx: True,
        postcondition=lambda result, _ctx: result.get("ready") is True,
        limits=AgenticLimits(max_attempts=2),
    )
    assert out.status == "completed"
    assert out.attempts == 2


def test_elapsed_budget_stops_before_another_attempt(monkeypatch):
    times = iter([0.0, 1.0])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(times))
    out = run_bounded_sync(
        context=AgenticContext(goal="g", observed=[]),
        propose=lambda _ctx: AgenticProposal("x"),
        execute=lambda _proposal, _ctx: True,
        validate=lambda _proposal, _ctx: True,
        postcondition=lambda result, _ctx: result is True,
        limits=AgenticLimits(max_elapsed_s=0.5),
    )
    assert out.status == "exhausted"
    assert out.reason == "elapsed_budget_exhausted"
    assert out.attempts == 0


def test_stale_proposal_is_not_executed_after_elapsed_budget(monkeypatch):
    times = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(times))
    executed = []
    out = run_bounded_sync(
        context=AgenticContext(goal="g", observed=[]),
        propose=lambda _ctx: AgenticProposal("x"),
        execute=lambda _proposal, _ctx: executed.append(True),
        validate=lambda _proposal, _ctx: True,
        postcondition=lambda _result, _ctx: True,
        limits=AgenticLimits(max_elapsed_s=0.5),
    )
    assert out.status == "exhausted"
    assert out.reason == "elapsed_budget_exhausted"
    assert out.attempts == 1
    assert executed == []


def test_deterministic_result_skips_intelligence():
    proposed = []
    result = deterministic_then_fallback_sync(
        deterministic=lambda: {"ok": True, "complete": True},
        needs_fallback=lambda primary: not primary["complete"],
        context=lambda primary: AgenticContext("g", primary),
        propose=lambda _ctx: proposed.append(True),
        execute=lambda _proposal, _ctx: None,
        validate=lambda _proposal, _ctx: False,
        postcondition=lambda _result, _ctx: False,
    )
    assert result["complete"] is True
    assert proposed == []


def test_failed_fallback_preserves_deterministic_result():
    primary = {"ok": True, "complete": False}
    result = deterministic_then_fallback_sync(
        deterministic=lambda: primary,
        needs_fallback=lambda _value: True,
        context=lambda value: AgenticContext("g", value),
        propose=lambda _ctx: AgenticProposal("invalid"),
        execute=lambda _proposal, _ctx: None,
        validate=lambda _proposal, _ctx: False,
        postcondition=lambda _result, _ctx: False,
        limits=AgenticLimits(max_attempts=1),
    )
    assert result is primary


def test_async_deterministic_fallback_has_the_same_contract():
    async def run():
        async def deterministic():
            return "weak"

        async def propose(_ctx):
            return AgenticProposal("strong")

        async def execute(proposal, _ctx):
            return proposal.action

        return await deterministic_then_fallback(
            deterministic=deterministic,
            needs_fallback=lambda value: value == "weak",
            context=lambda value: AgenticContext("improve", value),
            propose=propose,
            execute=execute,
            validate=lambda proposal, _ctx: proposal.action == "strong",
            postcondition=lambda result, _ctx: result == "strong",
        )

    assert asyncio.run(run()) == "strong"
