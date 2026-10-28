# SPDX-License-Identifier: MIT
"""Same-Page CAPTCHA adapter for the login delegate and shared vision transport.

The concrete broker supports visible, protocol-recognized provider iframes.
It never lends Page/Locator to a proposer, never submits login, and never
interprets a token as site acceptance. Arbitrary/generic widget binding and the
opaque installed local solver are deliberately outside its effect capability.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import math
import time
from typing import Awaitable, Callable

from agentic_executor import AgenticContext, AgenticProposal
from spin_supervisor import (Budget, BudgetExhausted, CallbackBroker, DrainIncomplete, Fence,
                             FenceChanged, ModelLease, ResourceBusy, Strategy, run_many)
from playwright_sidecar import challenges

VISION_TOKEN_RESERVATION = 8192  # Conservative LAB reservation; not provider-qualified.

WIDGET_QUALIFICATION_REASONS = frozenset({
    "widget_not_visible", "widget_obscured", "widget_provider_mismatch",
    "widget_crop_root", "widget_crop_ancestor_effect", "widget_crop_iframe_style",
    "widget_crop_node_budget", "widget_crop_custom_or_shadow",
    "widget_crop_generated_content", "widget_crop_overlap", "widget_crop_text_budget",
    "widget_crop_text_overlap", "widget_crop_observer_error",
})

# A conservative geometric guard, not an atomic compositor/privacy proof. Reject
# overlap even when behind the iframe or pointer-events:none; uncertainty is an
# abstention. A closed shadow root on an ordinary host whose box lies elsewhere
# cannot be discovered by standard DOM APIs. Rectangles also do not bound every
# painted effect (e.g. an external box-shadow or ::marker). Diagnostics describe
# the failed check; "ok" does not certify compositor privacy.
_CROP_GUARD_JS = r"""
function cropQualification(el) {
  try {
    if (el.getRootNode() !== document || !el.isConnected) return 'widget_crop_root';
    const box = el.getBoundingClientRect();
    const intersects = r => r.width > 0 && r.height > 0 &&
      r.left < box.right + 1 && r.right > box.left - 1 &&
      r.top < box.bottom + 1 && r.bottom > box.top - 1;
    const chain = new Set();
    for (let node = el; node; node = node.parentElement) {
      chain.add(node);
      const s = getComputedStyle(node);
      if (s.transform !== 'none' || s.perspective !== 'none' ||
          s.filter !== 'none' || (s.backdropFilter && s.backdropFilter !== 'none') ||
          s.mixBlendMode !== 'normal' || Number(s.opacity) !== 1 ||
          s.clipPath !== 'none' || (s.maskImage && s.maskImage !== 'none') ||
          s.backgroundImage !== 'none' || (s.zoom && !['1','normal'].includes(s.zoom))) return 'widget_crop_ancestor_effect';
    }
    const own = getComputedStyle(el);
    for (const key of ['borderTopWidth','borderRightWidth','borderBottomWidth',
                       'borderLeftWidth','paddingTop','paddingRight','paddingBottom','paddingLeft',
                       'borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius']) {
      if (parseFloat(own[key]) !== 0) return 'widget_crop_iframe_style';
    }
    const nodes = document.querySelectorAll('*');
    if (nodes.length > 4000) return 'widget_crop_node_budget';
    for (const node of nodes) {
      if (node === el) continue;
      // Do not claim to inspect hidden/closed custom-element trees.
      if (node.shadowRoot || node.localName.includes('-')) return 'widget_crop_custom_or_shadow';
      const s = getComputedStyle(node);
      if (s.display === 'none') continue;
      for (const pseudo of ['::before','::after']) {
        const p = getComputedStyle(node,pseudo);
        if (p.display !== 'none' && p.visibility !== 'hidden' &&
            p.content !== 'none' && p.content !== 'normal') return 'widget_crop_generated_content';
      }
      // A pseudo may override its host's visibility and paint outside its box.
      if (s.visibility === 'hidden' || s.visibility === 'collapse') continue;
      if (!chain.has(node) && Array.from(node.getClientRects()).some(intersects)) return 'widget_crop_overlap';
    }
    const walk = document.createTreeWalker(document.documentElement,NodeFilter.SHOW_TEXT);
    let text, count = 0;
    while ((text = walk.nextNode())) {
      if (++count > 10000) return 'widget_crop_text_budget';
      if (!text.textContent.trim() || !text.parentElement || !chain.has(text.parentElement)) continue;
      const range = document.createRange();range.selectNodeContents(text);
      if (Array.from(range.getClientRects()).some(intersects)) return 'widget_crop_text_overlap';
    }
    return 'ok';
  } catch (_) { return 'widget_crop_observer_error'; }
}
"""


def _crop_qualification_reason(value) -> str:
    """Accept one explicit success value; expose only closed diagnostic codes."""
    if type(value) is str and (value == "ok" or value in WIDGET_QUALIFICATION_REASONS):
        return value
    return "unqualified_widget"


class WidgetUnavailable(Exception):
    pass


@dataclass(frozen=True)
class WidgetSnapshot:
    crop_png: bytes
    provider: str
    challenge_id: str
    frame_id: str
    geometry: tuple[float, float, float, float]
    fingerprint: str
    fence: Fence

    def identity(self):
        return (self.provider, self.challenge_id, self.frame_id, self.geometry,
                self.fingerprint, self.fence)


def valid_plan(value) -> bool:
    if type(value) is not dict:
        return False
    if value == {"action": "abstain"}:
        return True
    if set(value) != {"action", "x", "y"} or value.get("action") != "click":
        return False
    return all(type(value[key]) in (int, float) and 0 <= value[key] <= 1
               and math.isfinite(value[key]) for key in ("x", "y"))


class VisualAttempts:
    """A second request requires an independently identified changed challenge."""

    def __init__(self):
        self.identities: list[str] = []

    def admit(self, snapshot: WidgetSnapshot) -> bool:
        if not self.identities:
            self.identities.append(snapshot.challenge_id)
            return True
        if (len(self.identities) == 1 and snapshot.challenge_id
                and self.identities[0] and snapshot.challenge_id != self.identities[0]):
            self.identities.append(snapshot.challenge_id)
            return True
        return False


def local_solver_boundary() -> dict:
    """Reuse the local solver's declared cap without granting its opaque Page API.

    solve_once hands Page to a third-party solver with no per-effect callback.
    Calling it here would bypass this broker. The existing parent-owned path
    remains unchanged; a protected binding requires an interceptable solver.
    """
    from playwright_sidecar import captcha_solver
    return {"status": "unsupported", "reason": "local_effects_not_interceptable",
            "max_attempt_s": captcha_solver.MAX_ATTEMPT_S}


@dataclass(frozen=True)
class ChallengeResult:
    status: str  # accepted | manual | blocked; never "logged_in".
    reason: str
    page_ref: str
    visual_attempts: int


class WidgetBroker:
    """Bound to exactly one caller-owned Page and one trusted locator.

    challenge_identity is an optional trusted read-only provider adapter. Its
    opaque value must identify the actual challenge, not a timestamp, pixel
    change or token. Without it no second model request is admitted.
    """

    def __init__(self, *, page, widget, provider: str, session: str, owner: str,
                 resource_lock: asyncio.Lock, resource_loan=None,
                 binding_valid: Callable[[], bool] | None = None,
                 site_accepted: Callable[[], Awaitable[bool]],
                 challenge_identity: Callable[[], Awaitable[str | None]] | None = None):
        if widget.page is not page:
            raise ValueError("widget_page_mismatch")
        self.page = page
        self.widget = widget
        self.provider = provider
        self._session, self._owner, self._revision = session, owner, 0
        self._site_accepted = site_accepted
        self._binding_valid = binding_valid
        self._challenge_identity = challenge_identity
        self._element = None
        self._node_revision = 0
        self._approved: WidgetSnapshot | None = None
        self._verification_until = 0.0
        self._verification_budget = None
        self._cleanup_task = None
        self.page_ref = hashlib.sha256(f"{session}:{id(page)}".encode()).hexdigest()[:24]
        self._closed = False
        self._navigation_listener = lambda frame: self._navigated()
        page.on("framenavigated", self._navigation_listener)
        self.callback = CallbackBroker(resource_lock=resource_lock, resource_loan=resource_loan,
                                       current_fence=self.fence, apply=self._click,
                                       verify=self._accepted)

    def _navigated(self) -> None:
        self._revision += 1

    def fence(self) -> Fence:
        if self._binding_valid is not None and self._binding_valid() is not True:
            self.callback.revoke()
        return Fence(self._session, self._owner, self._revision)

    def revoke_for_human(self, owner: str = "human") -> None:
        self.callback.revoke()
        self._owner = owner
        self._revision += 1

    async def close(self) -> None:
        self.callback.revoke()
        if self._cleanup_task is None:
            self._cleanup_task = self.callback.defer_cleanup(self._dispose)
        await self.callback.drained()

    async def _dispose(self) -> None:
        if not self._closed:
            self.page.remove_listener("framenavigated", self._navigation_listener)
            self._closed = True
        if self._element is not None:
            await self._element.dispose()
            self._element = None

    async def snapshot(self, budget: Budget) -> WidgetSnapshot:
        expected = self.fence()
        self.callback.check(expected, budget)
        async with asyncio.timeout_at(budget.deadline):
            if self._closed or self.page.is_closed() or await self.widget.count() != 1:
                raise WidgetUnavailable("widget_not_unique")
            observation = await challenges.observe(self.page)
            if observation.get("status") != "required" or observation.get("provider") != self.provider:
                raise WidgetUnavailable("challenge_not_required")
            element = await self.widget.element_handle()
            if element is None:
                raise WidgetUnavailable("widget_missing")
            if self._element is not None:
                same = await element.evaluate("(el, old) => el === old", self._element)
                if same:
                    await element.dispose()
                    element = self._element
                else:
                    await self._element.dispose()
                    self._node_revision += 1
            self._element = element
            metadata = await element.evaluate("el => {" + challenges._HELPERS_JS + _CROP_GUARD_JS + """
                const box = el.getBoundingClientRect();
                const top = document.elementFromPoint(box.x + box.width/2, box.y + box.height/2);
                return {visible:visible(el), provider:el.tagName === 'IFRAME' ? providerFrame(el) : null,
                    src:el.getAttribute('src') || '', unobscured:top === el,
                    crop_reason:cropQualification(el),
                    viewport:[innerWidth,innerHeight]};
            }""")
            recognized = metadata.get("provider") or {}
            if metadata.get("visible") is not True:
                raise WidgetUnavailable("widget_not_visible")
            if metadata.get("unobscured") is not True:
                raise WidgetUnavailable("widget_obscured")
            if type(recognized) is not dict or recognized.get("provider") != self.provider:
                raise WidgetUnavailable("widget_provider_mismatch")
            crop_reason = _crop_qualification_reason(metadata.get("crop_reason"))
            if crop_reason != "ok":
                raise WidgetUnavailable(crop_reason)
            box = await element.bounding_box()
            if not box:
                raise WidgetUnavailable("widget_geometry")
            geometry = tuple(float(box[key]) for key in ("x", "y", "width", "height"))
            x, y, width, height = geometry
            viewport = metadata["viewport"]
            if (not all(math.isfinite(value) for value in geometry)
                    or x < 0 or y < 0 or not 2 <= width <= 1024 or not 2 <= height <= 1024
                    or x + width > viewport[0] or y + height > viewport[1]):
                raise WidgetUnavailable("widget_geometry")
            # Page clip avoids Locator.screenshot's implicit scrolling.
            crop = await self.page.screenshot(clip=box, full_page=False,
                                             animations="allow", caret="initial",
                                             timeout=max(1, (budget.deadline - time.monotonic()) * 1000))
            if len(crop) > 512 * 1024:
                raise WidgetUnavailable("widget_crop_budget")
            crop_reason = _crop_qualification_reason(await element.evaluate(
                "el => {" + _CROP_GUARD_JS + "return cropQualification(el)}"))
            if crop_reason != "ok":
                raise WidgetUnavailable(crop_reason)
            after = await element.bounding_box()
            after_src = await element.get_attribute("src")
            if after != box or (after_src or "") != metadata["src"]:
                raise WidgetUnavailable("widget_changed_during_capture")
            identity = await self._challenge_identity() if self._challenge_identity else None
            if identity is not None and (type(identity) is not str or not identity or len(identity) > 256):
                raise WidgetUnavailable("unqualified_challenge_identity")
            self.callback.check(expected, budget)
            frame_key = f"{self.page_ref}:{self._node_revision}:{metadata['src']}"
            return WidgetSnapshot(crop, self.provider,
                                  hashlib.sha256(identity.encode()).hexdigest() if identity else "",
                                  hashlib.sha256(frame_key.encode()).hexdigest(), geometry,
                                  hashlib.sha256(crop).hexdigest(), expected)

    async def stable_snapshot(self, budget: Budget) -> WidgetSnapshot:
        first = await self.snapshot(budget)
        async with asyncio.timeout_at(budget.deadline):
            await asyncio.sleep(0.03)
        second = await self.snapshot(budget)
        if first.identity() != second.identity():
            raise WidgetUnavailable("unstable_widget")
        return second

    def approve(self, snapshot: WidgetSnapshot) -> None:
        self._approved = snapshot

    async def _click(self, plan: dict, budget: Budget) -> None:
        if not valid_plan(plan) or plan["action"] != "click" or self._approved is None:
            raise WidgetUnavailable("invalid_plan")
        fresh = await self.stable_snapshot(budget)
        if fresh.identity() != self._approved.identity():
            raise WidgetUnavailable("stale_widget")
        self.callback.check(self._approved.fence, budget)
        x, y, width, height = fresh.geometry
        px = x + min(width - 0.5, max(0.5, float(plan["x"]) * width))
        py = y + min(height - 0.5, max(0.5, float(plan["y"]) * height))
        # Exactly one command on the already bound Page. No locator auto-wait,
        # selector generated by a model, tab lookup, navigation or login submit.
        import sites_audit
        audit_fields = {
            "owner": self._owner, "session_id": self._session, "action": "click",
            "action_origin": {"actor_type": "executor", "executor": "playwright_sidecar",
                              "source": "captcha_broker"},
            # The callback receives the chosen action, not its proposer. Local
            # strategies are allowed; never infer Frontier or a human identity.
            "decision_actor": {"actor_type": "unknown"},
        }
        requested = False

        async def click():
            nonlocal requested
            requested = True
            sites_audit.record("captcha_action", outcome="requested", **audit_fields)
            await self.page.mouse.click(px, py)

        try:
            await self.callback.native_command(click, self._approved.fence, budget,
                                               name="captcha-broker-click")
            sites_audit.record("captcha_action", outcome="completed", **audit_fields)
            self._verification_budget = budget
            self._verification_until = budget.deadline - .01
        except BaseException:
            if requested:
                sites_audit.record("captcha_action", outcome="unconfirmed", **audit_fields)
            raise

    async def _accepted(self) -> bool:
        while True:
            remaining = getattr(self, "_verification_until", 0.0) - time.monotonic()
            if remaining <= 0:
                return False
            self.callback.check(self._approved.fence, self._verification_budget)
            try:
                async with asyncio.timeout_at(self._verification_until):
                    fresh = await challenges.observe(self.page)
                    if fresh.get("status") == "unavailable":
                        raise WidgetUnavailable("observation_unavailable")
                    if fresh.get("kind") == "interactive":
                        return False
                    # A cleared challenge is not a proof of authenticated login.
                    if fresh.get("status") == "clear" and await self._site_accepted() is True:
                        self.callback.check(self._approved.fence, self._verification_budget)
                        return True
            except TimeoutError:
                raise WidgetUnavailable("observation_timeout") from None
            await asyncio.sleep(max(0, min(.05, self._verification_until-time.monotonic())))


async def solve_widget(*, broker: WidgetBroker, budget: Budget,
                       visual: Callable[[bytes, str, ModelLease], Awaitable[dict]] | None,
                       local_proposers: tuple[Strategy, ...] = ()) -> ChallengeResult:
    """Local read-only proposals + at most 1+1 visual requests, one writer.

    This async callback seam is qualified only by synthetic tests. An opaque
    synchronous provider must not be put in to_thread without genuine drain.
    """
    attempts = VisualAttempts()
    reason = "unresolved"
    owns_resource = False
    has_resource = False
    try:
        owns_resource = await broker.callback.claim_resource()
        has_resource = True
        if any(strategy.max_tokens for strategy in local_proposers):
            raise WidgetUnavailable("extra_model_strategy")
        while True:
            # Never buy a proposal whose single effect cannot be afforded.
            budget.check()
            if budget.effects >= budget.max_effects:
                reason = "effect_budget"
                break
            snapshot = await broker.stable_snapshot(budget)
            if not attempts.admit(snapshot):
                reason = "visual_attempt_limit"
                break
            strategies = list(local_proposers)
            if visual is not None:
                async def propose(context, lease, captured=snapshot):
                    plan = await visual(captured.crop_png, captured.provider, lease)
                    return AgenticProposal(plan)
                strategies.append(Strategy("frontier_visual", propose, priority=100,
                                           max_tokens=VISION_TOKEN_RESERVATION))
            if not strategies:
                reason = "visual_transport_unbound"
                break
            broker.approve(snapshot)
            result = await run_many(
                context=AgenticContext("Resolve the visible widget only",
                                      {"provider": snapshot.provider,
                                       "width": snapshot.geometry[2], "height": snapshot.geometry[3]},
                                      {"no_login_submit": True, "no_navigation": True}),
                strategies=strategies, budget=budget, broker=broker.callback,
                expected=snapshot.fence,
                validate=lambda proposal: valid_plan(proposal.action)
                and proposal.action["action"] == "click")
            if result.status == "completed":
                return ChallengeResult("accepted", "site_postcondition", broker.page_ref,
                                       budget.model_calls)
            reason = result.reason
            if result.reason == "drain_incomplete":
                return ChallengeResult("blocked", reason, broker.page_ref, budget.model_calls)
            if result.reason == "resource_busy":
                return ChallengeResult("blocked", reason, broker.page_ref, budget.model_calls)
            if result.reason != "postcondition_failed":
                break
            # A next round is possible only if the visible stable challenge has
            # a different independently supplied identity. Budget/deadline stay.
        return ChallengeResult("manual", reason, broker.page_ref, budget.model_calls)
    except ResourceBusy:
        return ChallengeResult("blocked", "resource_busy", broker.page_ref, budget.model_calls)
    except asyncio.CancelledError:
        broker.callback.revoke()
        raise
    except DrainIncomplete as error:
        broker.callback.block_for_drain(error)
        return ChallengeResult("blocked", "drain_incomplete", broker.page_ref, budget.model_calls)
    except (WidgetUnavailable, FenceChanged, BudgetExhausted, TimeoutError) as error:
        reason = str(error) if isinstance(error, WidgetUnavailable) else "budget_or_authority"
        return ChallengeResult("manual", reason, broker.page_ref, budget.model_calls)
    finally:
        if has_resource:
            try:
                await broker.callback.drained()
                if owns_resource:
                    broker.callback.release_resource()
            except DrainIncomplete:
                return ChallengeResult("blocked", "drain_incomplete", broker.page_ref, budget.model_calls)
