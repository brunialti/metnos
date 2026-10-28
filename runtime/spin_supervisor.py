# SPDX-License-Identifier: MIT
"""Opt-in bounded proposal supervision; no browser, provider, or login policy.

Trusted adapters only: this is not a sandbox for arbitrary Python. Proposers
receive separate JSON contexts and pre-reserved immutable model leases. Only
the broker owns effects. A single event loop owns each Budget and broker.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import math
import time
from typing import Any, Awaitable, Callable

from agentic_executor import AgenticContext, AgenticOutcome, AgenticProposal


class BudgetExhausted(Exception):
    pass


class FenceChanged(Exception):
    pass


class ResourceBusy(Exception):
    pass


class DrainIncomplete(Exception):
    """Work is still alive; its resource must not be handed off or reused.

    An external transport without an awaitable completion handle sets external.
    That resource cannot be recovered by this supervisor: transport-level
    cleanup or destruction must be established separately.
    """

    def __init__(self, pending=(), *, external: bool = False):
        super().__init__("drain_incomplete")
        self.pending = tuple(pending)
        self.external = external or not self.pending


@dataclass(frozen=True)
class Fence:
    session: str
    owner: str
    revision: int


@dataclass
class _Claim:
    used: bool = False


@dataclass(frozen=True)
class ModelLease:
    deadline: float
    max_tokens: int
    _claim: _Claim = field(default_factory=_Claim, init=False, repr=False, compare=False)

    def claim(self) -> None:
        """One transport request only; limits are immutable, consumption is not."""
        if self._claim.used or time.monotonic() >= self.deadline:
            raise BudgetExhausted("model_lease_unavailable")
        self._claim.used = True


@dataclass
class Budget:
    deadline: float
    max_model_calls: int
    max_tokens: int
    max_effects: int = 1
    model_calls: int = field(default=0, init=False)
    tokens: int = field(default=0, init=False)
    effects: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.deadline):
            raise ValueError("invalid_deadline")
        if any(type(value) is not int or value < 0 for value in
               (self.max_model_calls, self.max_tokens, self.max_effects)):
            raise ValueError("invalid_budget")

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise BudgetExhausted("deadline")

    def reserve_model(self, max_tokens: int) -> ModelLease:
        """Reserve total input/output cost before task creation/await; no refunds."""
        self.check()
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("invalid_token_reservation")
        if self.model_calls >= self.max_model_calls or self.tokens + max_tokens > self.max_tokens:
            raise BudgetExhausted("model_budget")
        self.model_calls += 1
        self.tokens += max_tokens
        return ModelLease(self.deadline, max_tokens)

    def reserve_effect(self) -> None:
        self.check()
        if self.effects >= self.max_effects:
            raise BudgetExhausted("effect_budget")
        self.effects += 1


@dataclass(frozen=True)
class Strategy:
    name: str
    propose: Callable[[AgenticContext, ModelLease | None], Awaitable[AgenticProposal]]
    priority: int = 0
    max_tokens: int = 0


def _plain(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("observation_depth")
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return
    if kind is float and math.isfinite(value):
        return
    if kind is list:
        for item in value:
            _plain(item, depth + 1)
        return
    if kind is dict and all(type(key) is str for key in value):
        for item in value.values():
            _plain(item, depth + 1)
        return
    raise ValueError("non_json_observation")


def _copy_context(context: AgenticContext) -> AgenticContext:
    value = dict(goal=context.goal, observed=context.observed,
                 constraints=context.constraints, history=context.history)
    _plain(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 12000:
        raise ValueError("observation_budget")
    return AgenticContext(**json.loads(encoded))


async def _drain(tasks, *, cancel: bool, grace_s: float = 0.25) -> None:
    """Wait boundedly for actual completion, never claim a live worker is done.

    Cleanup has its own bounded grace after the work deadline. Repeated caller
    cancellation cannot detach work or extend this grace indefinitely.
    """
    tasks = set(tasks)
    until = time.monotonic() + grace_s
    if cancel:
        for task in tasks:
            if not task.done():
                task.cancel()
    while any(not task.done() for task in tasks):
        remaining = until - time.monotonic()
        if remaining <= 0:
            break
        try:
            await asyncio.wait(tasks, timeout=remaining)
        except asyncio.CancelledError:
            continue
    pending = tuple(task for task in tasks if not task.done())
    for task in tasks:
        if task.done() and not task.cancelled():
            task.exception()  # Consume errors without mistaking them for success.
    if pending:
        raise DrainIncomplete(pending)


class LockScope:
    """Owner-bound loan of an existing session lock to one explicit child task.

    No new lock or resource registry. The owner retains the physical lock across
    all observations, effects and cleanup. A failed drain retains the original
    lock and known handles; recovery is an explicit completion check, never a
    background retry or a new lock. Trusted adapter API, not a Python sandbox.
    """

    def __init__(self, lock: asyncio.Lock, *, cleanup_grace_s: float = 0.25,
                 held_by: asyncio.Task | None = None):
        if not math.isfinite(cleanup_grace_s) or not 0 < cleanup_grace_s <= 5:
            raise ValueError("invalid_cleanup_grace")
        self.lock = lock
        # The owner already holds the lock for a longer operation, as the
        # holder it names: the scope neither acquires nor releases it, and
        # only that task may enter it. Everything else is unchanged.
        self.held_by = held_by
        self.held = held_by is not None
        self.cleanup_grace_s = cleanup_grace_s
        self.owner = None
        self.delegate = None
        self.brokers = set()
        self.active = None
        self.entered = False
        self.exited = False
        self.quarantined = False
        self.revoked = False

    async def __aenter__(self):
        if self.entered:
            raise ResourceBusy("scope_already_entered")
        if self.held:
            if not self.lock.locked() or self.held_by is not asyncio.current_task():
                raise ResourceBusy("lock_not_held")
        else:
            await self.lock.acquire()
        self.owner = asyncio.current_task()
        self.entered = True
        return self

    def bind(self, task: asyncio.Task) -> None:
        if (asyncio.current_task() is not self.owner or not self.entered
                or self.exited or self.revoked or self.delegate is not None or task.done()):
            raise ResourceBusy("invalid_delegate")
        self.delegate = task

    def authorized(self) -> bool:
        return bool(self.entered and not self.exited and not self.quarantined and not self.revoked
                    and self.lock.locked() and asyncio.current_task() is self.delegate)

    def revoke(self) -> None:
        self.revoked = True
        for broker in self.brokers:
            broker.revoke()

    async def cancel_delegate(self) -> None:
        """Revoke first; a live credential task remains an owned completion handle."""
        self.revoke()
        if self.delegate is not None:
            await _drain([self.delegate], cancel=not self.delegate.done(),
                         grace_s=self.cleanup_grace_s)

    def claim(self, broker) -> None:
        if not self.authorized() or self.active not in (None, broker):
            raise ResourceBusy("invalid_resource_loan")
        self.active = broker
        self.brokers.add(broker)

    def give_back(self, broker) -> None:
        if broker is self.active:
            self.active = None

    def _release(self) -> None:
        for broker in self.brokers:
            broker._resource_owned = False
            broker._resource_task = None
        self.active = None
        if not self.held:
            self.lock.release()
        self.quarantined = False

    async def __aexit__(self, kind, value, traceback):
        if asyncio.current_task() is not self.owner:
            raise ResourceBusy("foreign_scope_exit")
        self.revoke()
        incomplete = False
        if self.delegate is not None:
            try:
                await self.cancel_delegate()
            except DrainIncomplete:
                incomplete = True  # delegate remains a known handle on this scope
        for broker in self.brokers:
            try:
                await broker.drained()
            except DrainIncomplete:
                incomplete = True
        self.exited = True
        self.quarantined = incomplete
        if not incomplete:
            self._release()
        return False

    def recover_if_complete(self) -> bool:
        """Only known completed handles can release a quarantined original lock."""
        if not self.exited or not self.quarantined:
            return False
        if self.delegate is not None:
            if not self.delegate.done():
                return False
            if not self.delegate.cancelled():
                self.delegate.exception()
        for broker in self.brokers:
            if (broker.pending_tasks or broker._external_pending or broker._lock.locked()
                    or not broker.cleanup_complete):
                return False
            for task in broker._pending:
                if not task.cancelled():
                    task.exception()
        self._release()
        return True


class CallbackBroker:
    """The sole effect capability, permanently revoked after an incomplete drain.

    Human handoff must revoke, await supervisor completion and await_cleanup()
    successfully before taking the same resource. Callbacks are trusted and
    must expose unfinished child work as DrainIncomplete, never hide threads.
    """

    def __init__(self, *, resource_lock: asyncio.Lock, current_fence: Callable[[], Fence],
                 apply: Callable[[Any, Budget], Awaitable[Any]],
                 verify: Callable[[], Awaitable[bool]], cleanup_grace_s: float = 0.25,
                 resource_loan: LockScope | None = None):
        if not math.isfinite(cleanup_grace_s) or not 0 < cleanup_grace_s <= 5:
            raise ValueError("invalid_cleanup_grace")
        self.current_fence = current_fence
        self._apply, self._verify = apply, verify
        # Mandatory caller-owned lock: all brokers for one resource share it.
        # Use the session entry's lock, never a fresh per-invocation lock.
        self._resource_lock = resource_lock
        if resource_loan is not None and resource_loan.lock is not resource_lock:
            raise ValueError("loan_lock_mismatch")
        self._resource_loan = resource_loan
        self._resource_owned = False
        self._resource_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.revoked = asyncio.Event()
        self._pending: set[asyncio.Task] = set()
        self._unfinished_cleanup: set[asyncio.Task] = set()
        self._external_pending = False
        self.cleanup_grace_s = cleanup_grace_s

    @property
    def pending_tasks(self) -> tuple[asyncio.Task, ...]:
        return tuple(task for task in self._pending if not task.done())

    @property
    def cleanup_complete(self) -> bool:
        # Task completion/cancellation is not a positive disposal receipt.
        return not self._unfinished_cleanup

    def revoke(self) -> None:
        self.revoked.set()

    async def claim_resource(self) -> bool:
        # A trusted caller in the same task may lend its existing ownership to
        # run_many. Foreign tasks cannot borrow it, even on this broker object.
        current = asyncio.current_task()
        if self._resource_owned and self._resource_task is current:
            if self.revoked.is_set() or self.pending_tasks or self._external_pending:
                raise ResourceBusy("resource_busy")
            return False
        # Lock.acquire does not suspend when unlocked; rivals never queue and
        # execute a stale request after the current operation has ended.
        if self._resource_loan is not None:
            self._resource_loan.claim(self)
        else:
            if self._resource_lock.locked():
                raise ResourceBusy("resource_busy")
            await self._resource_lock.acquire()
        self._resource_owned = True
        self._resource_task = current
        return True

    def release_resource(self) -> None:
        if (self._resource_owned and not self.pending_tasks and not self._external_pending
                and self.cleanup_complete):
            if (self._resource_task is not asyncio.current_task()
                    and self._resource_task is not None and not self._resource_task.done()):
                raise ResourceBusy("owner_still_running")
            if self._resource_loan is not None:
                self._resource_loan.give_back(self)
            else:
                self._resource_lock.release()
            self._resource_owned = False
            self._resource_task = None

    def block_for_drain(self, error: DrainIncomplete) -> None:
        self.revoke()
        self._pending.update(error.pending)
        self._external_pending |= error.external

    def check(self, expected: Fence, budget: Budget) -> None:
        budget.check()
        if self.revoked.is_set():
            raise FenceChanged("stale_authority")
        observed = self.current_fence()
        # Observing authority is trusted code, but may itself revoke or use
        # the remaining budget without changing the identity it returns.
        if self.revoked.is_set() or observed != expected:
            raise FenceChanged("stale_authority")
        budget.check()

    async def native_command(self, operation, expected: Fence, budget: Budget, *, name: str):
        """Track the original input response; cancellation is not remote undo."""
        async def invoke():
            self.check(expected, budget)
            if not self._resource_owned:
                raise FenceChanged("resource_not_owned")
            # No scheduling boundary between this check and native invocation.
            # DOM/compositor changes after invocation are still not atomic.
            return await operation()

        task = asyncio.create_task(invoke(), name=name)
        self._pending.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await _drain([task], cancel=False, grace_s=self.cleanup_grace_s)
            except DrainIncomplete as error:
                self.block_for_drain(error)
                raise
            raise
        finally:
            if task.done():
                self._pending.discard(task)

    def defer_cleanup(self, cleanup) -> asyncio.Task:
        """Retain cleanup in the loan until original, already known work ends."""
        self.revoke()
        originals = tuple(self._pending)

        async def finish():
            try:
                # Snapshot before registering this finalizer: never await itself.
                await asyncio.gather(*(asyncio.shield(task) for task in originals),
                                     return_exceptions=True)
                if self._external_pending or self._lock.locked():
                    raise DrainIncomplete(external=True)
                await cleanup()
                self._unfinished_cleanup.discard(asyncio.current_task())
            except BaseException:
                self._external_pending = True  # Failed cleanup is not recovery.
                raise

        task = asyncio.create_task(finish(), name="spin-late-cleanup")
        self._unfinished_cleanup.add(task)
        self._pending.add(task)
        return task

    async def _owned(self, operation, budget: Budget, name: str):
        task = asyncio.create_task(operation(), name=name)
        self._pending.add(task)
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0, budget.deadline - time.monotonic()))
            if not done:
                raise TimeoutError
            return task.result()
        except BaseException:
            try:
                await _drain([task], cancel=True, grace_s=self.cleanup_grace_s)
            except DrainIncomplete as error:
                self.block_for_drain(error)
                raise
            if task.done() and not task.cancelled():
                error = task.exception()
                if isinstance(error, DrainIncomplete):
                    self.block_for_drain(error)
                    raise error
            raise
        finally:
            if task.done():
                self._pending.discard(task)

    async def effect(self, action: Any, expected: Fence, budget: Budget) -> None:
        async with self._lock:
            if not self._resource_owned:
                raise FenceChanged("resource_not_owned")
            self.check(expected, budget)
            budget.reserve_effect()

            async def apply():
                self.check(expected, budget)
                await self._apply(action, budget)

            await self._owned(apply, budget, "spin-effect")
            self.check(expected, budget)

    async def verified(self, expected: Fence, budget: Budget) -> bool:
        async with self._lock:
            self.check(expected, budget)
            accepted = await self._owned(self._verify, budget, "spin-verifier")
            self.check(expected, budget)
            return accepted is True

    async def drained(self) -> None:
        try:
            await _drain(self._pending, cancel=False, grace_s=self.cleanup_grace_s)
        except DrainIncomplete as error:
            self.block_for_drain(error)
            raise
        self._pending = {task for task in self._pending if not task.done()}
        if self._external_pending or self._lock.locked() or not self.cleanup_complete:
            error = DrainIncomplete(self.pending_tasks, external=(self._external_pending
                                                                  or not self.cleanup_complete))
            self.block_for_drain(error)
            raise error

    async def await_cleanup(self) -> bool:
        """Report real completion; successful cleanup does not restore authority."""
        self.revoke()
        try:
            await self.drained()
            self.release_resource()
            return True
        except DrainIncomplete:
            return False


_delegating: ContextVar[bool] = ContextVar("spin_delegating", default=False)


async def run_many(*, context: AgenticContext, strategies: list[Strategy],
                   budget: Budget, broker: CallbackBroker,
                   validate: Callable[[AgenticProposal], bool],
                   expected: Fence | None = None,
                   verification_margin_s: float = 0.05) -> AgenticOutcome:
    """Reserve -> propose concurrently -> deterministic valid choice -> one effect.

    Clean cancellation propagates. Incomplete cancellation returns the explicit
    drain_incomplete outcome with revoked authority and tracked work instead.
    """
    if _delegating.get():
        return AgenticOutcome("inconclusive", reason="nested_delegation")
    tasks: list[asyncio.Task] = []
    watcher = None
    owns_resource = False
    has_resource = False
    token = _delegating.set(True)
    try:
        if (not strategies or len(strategies) > 8
                or len({item.name for item in strategies}) != len(strategies)):
            return AgenticOutcome("inconclusive", reason="invalid_strategies")
        initial = _copy_context(context)
        owns_resource = await broker.claim_resource()
        has_resource = True
        expected = expected or broker.current_fence()
        broker.check(expected, budget)
        if not 0 <= verification_margin_s <= 30:
            raise ValueError("invalid_margin")
        proposal_deadline = budget.deadline - verification_margin_s
        if time.monotonic() >= proposal_deadline:
            return AgenticOutcome("exhausted", reason="verification_reserve")
        admitted = []
        for strategy in sorted(strategies, key=lambda item: (item.priority, item.name)):
            if type(strategy.max_tokens) is not int or strategy.max_tokens < 0:
                raise ValueError("invalid_strategy_cost")
            try:
                lease = budget.reserve_model(strategy.max_tokens) if strategy.max_tokens else None
            except BudgetExhausted:
                continue
            admitted.append((strategy, lease, _copy_context(initial)))
        if not admitted:
            return AgenticOutcome("exhausted", reason="proposal_budget")

        async def propose(strategy, isolated, lease):
            proposal = await strategy.propose(isolated, lease)
            return time.monotonic(), proposal

        # Every reservation precedes every task creation and the first await.
        for strategy, lease, isolated in admitted:
            tasks.append(asyncio.create_task(propose(strategy, isolated, lease),
                                             name="spin-proposal:" + strategy.name))
        watcher = asyncio.create_task(broker.revoked.wait(), name="spin-revocation")
        pending = set(tasks)
        ready: dict[asyncio.Task, AgenticProposal | None] = {}

        def detached_proposal(task):
            if task in ready:
                return ready[task]
            proposal = None
            if (task.done() and not task.cancelled() and task.exception() is None
                    and task.result()[0] <= proposal_deadline):
                try:
                    offered = task.result()[1]
                    if isinstance(offered, AgenticProposal) and validate(offered) is True:
                        _plain(offered.action)
                        proposal = AgenticProposal(json.loads(json.dumps(offered.action, allow_nan=False)))
                except Exception:
                    pass
            ready[task] = proposal
            return proposal

        while pending and not broker.revoked.is_set():
            remaining = proposal_deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _ = await asyncio.wait(pending | {watcher}, timeout=remaining,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            pending -= done
            # All superior candidates must be finished before a valid choice
            # can be final. Lower priorities are then cancelled and drained.
            determined = False
            for task in tasks:
                if not task.done():
                    break
                if detached_proposal(task) is not None:
                    determined = True
                    break
            if determined:
                break
        # Freeze eligibility BEFORE cancellation: late return from a cancellation
        # handler cannot join this set, even if verification time remains.
        eligible = {task for task in tasks if task.done() and not task.cancelled()
                    and task.exception() is None and task.result()[0] <= proposal_deadline}
        await _drain(pending, cancel=True, grace_s=broker.cleanup_grace_s)
        for task in tasks:
            if task.done() and not task.cancelled() and isinstance(task.exception(), DrainIncomplete):
                raise task.exception()
        broker.check(expected, budget)
        selected = None
        for (strategy, _, _), task in zip(admitted, tasks):
            if task not in eligible:
                continue
            proposal = detached_proposal(task)
            if proposal is not None:
                selected = strategy, proposal
                break
        if selected is None:
            return AgenticOutcome("inconclusive", attempts=len(tasks), reason="no_valid_proposal")
        strategy, proposal = selected
        await broker.effect(proposal.action, expected, budget)
        accepted = await broker.verified(expected, budget)
        return AgenticOutcome("completed" if accepted else "inconclusive",
                              result={"strategy": strategy.name, "verified": accepted},
                              attempts=len(tasks), reason="" if accepted else "postcondition_failed")
    except DrainIncomplete as error:
        broker.block_for_drain(error)
        return AgenticOutcome("inconclusive", attempts=len(tasks), reason="drain_incomplete")
    except asyncio.CancelledError:
        broker.revoke()
        raise
    except (BudgetExhausted, TimeoutError):
        return AgenticOutcome("exhausted", attempts=len(tasks), reason="budget_or_deadline")
    except FenceChanged:
        return AgenticOutcome("inconclusive", attempts=len(tasks), reason="stale_authority")
    except ResourceBusy:
        return AgenticOutcome("inconclusive", attempts=len(tasks), reason="resource_busy")
    except Exception:
        return AgenticOutcome("inconclusive", attempts=len(tasks), reason="adapter_failed")
    finally:
        incomplete = False
        try:
            await _drain(tasks + ([watcher] if watcher is not None else []), cancel=True,
                         grace_s=broker.cleanup_grace_s)
        except DrainIncomplete as error:
            broker.block_for_drain(error)
            incomplete = True
        if has_resource:
            try:
                await broker.drained()
            except DrainIncomplete:
                incomplete = True
            if not incomplete and owns_resource:
                broker.release_resource()
        _delegating.reset(token)
        if incomplete:
            return AgenticOutcome("inconclusive", attempts=len(tasks), reason="drain_incomplete")


async def run_one(*, strategy: Strategy, **kwargs) -> AgenticOutcome:
    return await run_many(strategies=[strategy], **kwargs)
