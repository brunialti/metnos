# SPDX-License-Identifier: MIT
"""One native activation of a uniquely bound provider checkbox, without pixels.

This is not a grid solver or a login proof. DOM checks and native input are not
atomic; held handles avoid selector retargeting, not every compositor race.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from urllib.parse import urlsplit

from spin_supervisor import (Budget, BudgetExhausted, CallbackBroker, DrainIncomplete,
                             Fence, FenceChanged, ResourceBusy)
from playwright_sidecar import challenges


REASONS = frozenset({
    "provider_not_unique_checkbox", "actual_frame_origin_mismatch", "checkbox_not_unique",
    "iframe_changed", "frame_changed", "provider_changed", "checkbox_changed",
    "checkbox_unavailable", "checkbox_obscured", "parent_obscured", "checkbox_geometry",
    "checkbox_observer_error", "checkbox_unconfirmed", "checkbox_command_failed",
    "checkbox_interactive", "checkbox_observation_timeout", "checkbox_not_ready",
    "provider_frame_not_loaded",
})

AUTHORITY_FAILURES = frozenset({
    "main_frame_navigated", "provider_frame_navigated", "frame_tree_unavailable",
    "binding_changed", "page_closed", "resource_busy", "fence_changed",
})
AUTHORITY_EVENT_RELATIONS = frozenset({"main", "self", "descendant", "unknown"})
AUTHORITY_PHASES = frozenset({
    "bind", "after_bind", "activate_validate", "native_dispatch", "verify", "cleanup",
})

_OUTSIDE_VIEWPORT_JS = """el=>{const r=el.getBoundingClientRect();
  return !(r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight);}"""

READINESS_WAIT_S = 1.0
READINESS_POLL_S = .05
FRAME_LOAD_WAIT_S = 5.0

_CANDIDATES_JS = "els=>{" + challenges._HELPERS_JS + """
  return els.map((el,index)=>({index,info:providerFrame(el),visible:visible(el)}))
    .filter(i=>i.visible&&i.info);
}"""

# Keep coordinate qualification separate from the image-crop guard. Pseudo CSS
# elsewhere is irrelevant here: no page pixels are captured or transported.
_SIMPLE_GEOMETRY_JS = """
const simpleGeometry = el => {
  for(let p=el;p;p=p.parentElement){
    const s=getComputedStyle(p);
    if(s.transform!=='none'||s.perspective!=='none'||
       (s.zoom&&!['1','normal'].includes(s.zoom))||Number(s.opacity)<=0) return false;
  }
  return true;
};
"""


class CheckboxUnavailable(Exception):
    pass


@dataclass(frozen=True)
class CheckboxResult:
    status: str  # accepted | manual | blocked; never logged_in.
    reason: str
    attempted: bool


@dataclass(frozen=True)
class _AuthorityEvent:
    relation: str
    phase: str
    command_requested: bool


class CheckboxBroker:
    """Trusted adapter; private handles never leave the session's resource loan."""

    def __init__(self, *, page, session: str, owner: str, resource_lock,
                 resource_loan, binding_valid, site_accepted):
        self.page, self.session, self.owner = page, session, owner
        self.binding_valid, self.site_accepted = binding_valid, site_accepted
        self.revision = 0
        self.authority_failure = None
        self.navigation_reason = None
        self.authority_first_event = None
        self.authority_additional_events = 0
        self.authority_lifecycle_phase = "bind"
        self.unrelated_frame_navigations = 0
        self.non_owner_frame_navigations = 0
        self.checkbox_counts = {"checkbox_count_first": None, "checkbox_count_last": None,
                                "checkbox_count_polls": 0, "checkbox_phase": None,
                                "checkbox_expected_same": None}
        self.checkbox_document = None
        self.iframe = self.frame = self.control = self.locator = None
        self.source = ""
        self.attempted = False
        self.verification_until = 0.0
        self.verification_budget = None
        self.verification_reason = "checkbox_unconfirmed"
        self._cleanup_task = None
        self._closed = False
        self.callback = CallbackBroker(resource_lock=resource_lock, resource_loan=resource_loan,
                                       current_fence=self.fence, apply=self._activate,
                                       verify=self._accepted)
        self.listener = self._navigated
        self.page.on("framenavigated", self.listener)

    def _navigated(self, frame):
        # A sibling analytics/support frame does not own this capability.
        # A proven child document does not own the held provider control.
        # Its navigation does not bypass handle validation or native actionability.
        # Main/provider changes and uncertain ancestry still revoke.
        relation = "unknown"
        try:
            if frame.page is not self.page:
                raise ValueError("different_page")
            if frame is self.page.main_frame:
                self.navigation_reason = "main_frame_navigated"
                relation = "main"
            else:
                current = frame
                descendants = []
                for _ in range(challenges.MAX_FRAMES):
                    if self.frame is not None and current is self.frame:
                        if frame is not self.frame:
                            if (self.frame.parent_frame is not self.page.main_frame
                                    or self.frame.url != self.source
                                    or any(item.page is not self.page or item.is_detached() is not False
                                           for item in descendants + [self.frame])):
                                raise ValueError("non_owner_binding_unavailable")
                            self.non_owner_frame_navigations = min(
                                self.non_owner_frame_navigations + 1, 1_000_000)
                            return
                        self.navigation_reason = "provider_frame_navigated"
                        relation = "self" if frame is self.frame else "descendant"
                        break
                    if current is self.page.main_frame:
                        self.unrelated_frame_navigations += 1
                        return
                    descendants.append(current)
                    current = current.parent_frame
                    if current is None:
                        raise ValueError("detached_frame")
                else:
                    raise ValueError("frame_depth")
        except Exception:
            self.navigation_reason = "frame_tree_unavailable"
        if self.authority_first_event is None:
            self.authority_first_event = _AuthorityEvent(
                relation, self.authority_lifecycle_phase, self.attempted)
        else:
            self.authority_additional_events = min(self.authority_additional_events + 1, 1_000_000)
        self.revision += 1

    def fence(self):
        if self.binding_valid() is not True:
            self.authority_failure = "binding_changed"
            self.callback.revoke()
        if self.page.is_closed():
            self.authority_failure = "page_closed"
            self.callback.revoke()
        return Fence(self.session, self.owner, self.revision)

    async def bind(self, budget: Budget):
        self.expected = self.fence()
        self.callback.check(self.expected, budget)
        observed = await challenges.observe(self.page)
        candidates = await self.page.locator("iframe").evaluate_all(_CANDIDATES_JS)
        if (observed.get("status") != "required" or observed.get("kind") != "checkbox"
                or len(candidates) != 1
                or candidates[0]["info"].get("kind") != "checkbox"
                or candidates[0]["info"].get("provider") != observed.get("provider")):
            raise CheckboxUnavailable("provider_not_unique_checkbox")
        self.provider = observed["provider"]
        self.iframe = await self.page.locator("iframe").nth(candidates[0]["index"]).element_handle(
            timeout=min(100, max(1, (budget.deadline-time.monotonic())*1000)))
        if self.iframe is None:
            raise CheckboxUnavailable("iframe_changed")
        frame = await self.iframe.content_frame()
        # The widget is recognised by its declared address while its frame may
        # still be loading (real run R34): wait, within the budget, until the
        # frame has left its initial blank document. Only then is it the held
        # frame, so its first document is not a navigation of it; the
        # comparison below is unchanged and a frame that never loads is named.
        loaded_until = min(budget.deadline, time.monotonic()+FRAME_LOAD_WAIT_S)
        while (frame is not None and frame.url in {"", "about:blank"}
               and time.monotonic() < loaded_until):
            self.callback.check(self.expected, budget)
            await asyncio.sleep(min(READINESS_POLL_S, max(0, loaded_until-time.monotonic())))
        if frame is not None and frame.url in {"", "about:blank"}:
            raise CheckboxUnavailable("provider_frame_not_loaded")
        self.frame = frame
        self.source = await self.iframe.evaluate("el=>el.src")
        source = urlsplit(self.source)
        actual = urlsplit(self.frame.url) if self.frame else None
        if (self.frame is None or self.frame.page is not self.page
                or self.frame.parent_frame is not self.page.main_frame
                or source.scheme != "https" or source.port not in {None, 443}
                or source.username is not None or source.password is not None
                or actual.scheme != "https" or actual.port not in {None, 443}
                or actual.username is not None or actual.password is not None
                or actual.netloc != source.netloc or self.frame.url != self.source):
            raise CheckboxUnavailable("actual_frame_origin_mismatch")
        self.locator = self.frame.get_by_role("checkbox")
        # Role enumeration does not wait for a provider's asynchronous render.
        # Wait only for absence; ambiguity never authorizes choosing a control.
        ready_until = min(budget.deadline, time.monotonic()+READINESS_WAIT_S)
        try:
            async with asyncio.timeout_at(ready_until):
                while True:
                    self.callback.check(self.expected, budget)
                    count = await self.locator.count()
                    self._record_count(count, "bind")
                    self.callback.check(self.expected, budget)
                    if count != 0:
                        if count != 1:
                            raise CheckboxUnavailable("checkbox_not_unique")
                        break
                    await self._validate_frame(budget)
                    await asyncio.sleep(min(READINESS_POLL_S, max(0, ready_until-time.monotonic())))
        except TimeoutError:
            raise CheckboxUnavailable("checkbox_not_ready") from None
        self.control = await self.locator.element_handle(
            timeout=min(100, max(1, (budget.deadline-time.monotonic())*1000)))
        if self.control is None:
            raise CheckboxUnavailable("checkbox_changed")
        # A widget below the visible area (met while navigating, real run R40)
        # is brought into view by its own frame first; nothing is clicked off
        # screen and every check below still runs on the moved widget.
        if await self.iframe.evaluate(_OUTSIDE_VIEWPORT_JS):
            await self.iframe.evaluate(
                "el=>el.scrollIntoView({block:'center',inline:'center',behavior:'instant'})")
            self.callback.check(self.expected, budget)
        await self._validate(budget)

    def _record_count(self, count, phase, expected_same=None):
        counts = self.checkbox_counts
        if counts["checkbox_count_first"] is None:
            counts["checkbox_count_first"] = min(count, 1_000_000)
        counts["checkbox_count_last"] = min(count, 1_000_000)
        counts["checkbox_count_polls"] = min(counts["checkbox_count_polls"]+1, 1_000_000)
        counts["checkbox_phase"] = phase
        counts["checkbox_expected_same"] = expected_same

    async def _validate_frame(self, budget):
        self.callback.check(self.expected, budget)
        observed = await challenges.observe(self.page)
        if (observed.get("status") != "required" or observed.get("kind") != "checkbox"
                or observed.get("provider") != self.provider):
            raise CheckboxUnavailable("provider_changed")
        if not await self.iframe.evaluate("el=>el.isConnected") or await self.iframe.content_frame() is not self.frame:
            raise CheckboxUnavailable("iframe_changed")
        if self.frame.url != self.source or await self.iframe.evaluate("el=>el.src") != self.source:
            raise CheckboxUnavailable("frame_changed")
        qualified = await self.iframe.evaluate("el=>{" + challenges._HELPERS_JS + _SIMPLE_GEOMETRY_JS + """
          const candidates=[...document.querySelectorAll('iframe')].filter(f=>visible(f)&&providerFrame(f));
          const s=getComputedStyle(el);
          return candidates.length===1&&candidates[0]===el&&providerFrame(el).kind==='checkbox'
            &&simpleGeometry(el)&&['borderTopWidth','borderRightWidth','borderBottomWidth',
              'borderLeftWidth','paddingTop','paddingRight','paddingBottom','paddingLeft']
              .every(k=>parseFloat(s[k])===0);
        }""")
        if qualified is not True:
            raise CheckboxUnavailable("provider_changed")
        self.callback.check(self.expected, budget)

    async def _validate(self, budget):
        await self._validate_frame(budget)
        values = await self.control.evaluate("el=>{" + _SIMPLE_GEOMETRY_JS + """
          const native=el.tagName==='INPUT'&&el.type==='checkbox';
          const box=el.getBoundingClientRect(), top=document.elementFromPoint(box.x+box.width/2,box.y+box.height/2);
          return {connected:el.isConnected,kind:native||el.getAttribute('role')==='checkbox',
            unchecked:native ? el.checked===false&&el.indeterminate===false : el.getAttribute('aria-checked')==='false',
            available:!el.matches(':disabled')&&!el.closest('[inert],[aria-disabled="true"]')
              &&el.getAttribute('aria-readonly')!=='true'&&!!el.getClientRects().length
              &&getComputedStyle(el).visibility==='visible'&&simpleGeometry(el),
            top:top===el||el.contains(top)};
        }""")
        if not values["connected"]:
            raise CheckboxUnavailable("checkbox_changed")
        observed = await self.locator.evaluate_all(
            "(els,expected)=>({count:els.length,same:els.length===1&&els[0]===expected})", self.control)
        self._record_count(observed["count"], "validate", observed["same"])
        if observed["same"] is not True:
            raise CheckboxUnavailable("checkbox_not_unique")
        if not values["kind"] or not values["unchecked"] or not values["available"]:
            raise CheckboxUnavailable("checkbox_unavailable")
        if values["top"] is not True:
            raise CheckboxUnavailable("checkbox_obscured")
        box = await self.control.bounding_box()
        viewport = await self.page.evaluate("[innerWidth,innerHeight]")
        if (not box or not all(math.isfinite(box[k]) for k in ("x", "y", "width", "height"))
                or not (0 <= box["x"] < box["x"]+box["width"] <= viewport[0]
                        and 0 <= box["y"] < box["y"]+box["height"] <= viewport[1])):
            raise CheckboxUnavailable("checkbox_geometry")
        if await self.iframe.evaluate("(el,p)=>document.elementFromPoint(p.x,p.y)===el",
                {"x": box["x"]+box["width"]/2, "y": box["y"]+box["height"]/2}) is not True:
            raise CheckboxUnavailable("parent_obscured")
        # All DOM reads suspend. Check authority again at the last input boundary.
        self.callback.check(self.expected, budget)
        if self.frame.url != self.source:
            raise CheckboxUnavailable("frame_changed")

    async def diagnose_count_failure(self, budget):
        """One bounded passive census on refusal; never contributes to authority."""
        try:
            self.callback.check(self.expected, budget)
            if self.frame is None or self.frame.is_detached() or self.frame.url != self.source:
                return
            async with asyncio.timeout_at(min(budget.deadline, time.monotonic()+.1)):
                values = await self.frame.evaluate("""()=>{
                  const nodes=document.querySelectorAll('input[type="checkbox"],[role="checkbox"]');
                  let visible=0, hidden=0, inert=0;
                  for(const el of [...nodes].slice(0,64)){
                    const s=getComputedStyle(el);
                    if(el.getClientRects().length&&s.visibility==='visible'&&s.display!=='none')visible++;
                    if(el.closest('[aria-hidden="true"]'))hidden++;
                    if(el.closest('[inert]'))inert++;
                  }
                  return {ready:document.readyState,total:Math.min(nodes.length,1000000),
                    visible,aria_hidden:hidden,inert,sampled:Math.min(nodes.length,64)};
                }""")
            self.callback.check(self.expected, budget)
            if self.frame.url == self.source and not self.frame.is_detached():
                values["phase"] = "post_refusal"
                self.checkbox_document = values
        except FenceChanged:
            raise
        except Exception:
            pass  # Diagnostic failure does not replace the original refusal.

    async def _activate(self, action, budget):
        if action != {"action": "activate_checkbox"} or self.attempted:
            raise CheckboxUnavailable("checkbox_unconfirmed")
        self.authority_lifecycle_phase = "activate_validate"
        await self._validate(budget)
        self.callback.check(self.expected, budget)
        import sites_audit
        fields = {"owner": self.owner, "session_id": self.session, "action": "click",
                  "action_origin": {"actor_type": "executor", "executor": "playwright_sidecar",
                                    "source": "provider_checkbox"},
                  "decision_actor": {"actor_type": "executor", "executor": "playwright_sidecar",
                                     "source": "provider_checkbox"}}
        async def command():
            self.attempted = True  # Input requested, rather than budget reserved.
            sites_audit.record("captcha_action", outcome="requested", **fields)
            await self.control.click(
                timeout=min(500, max(1, (budget.deadline-time.monotonic())*1000)),
                force=False, no_wait_after=True)
        try:
            self.authority_lifecycle_phase = "native_dispatch"
            await self.callback.native_command(command, self.expected, budget,
                                               name="captcha-checkbox-click")
            sites_audit.record("captcha_action", outcome="completed", **fields)
            self.verification_budget = budget
            self.verification_until = budget.deadline - .01
        except BaseException:
            if self.attempted:
                sites_audit.record("captcha_action", outcome="unconfirmed", **fields)
            raise

    async def _accepted(self):
        self.authority_lifecycle_phase = "verify"
        while True:
            remaining = self.verification_until-time.monotonic()
            if remaining <= 0:
                self.verification_reason = "checkbox_observation_timeout"
                return False
            self.callback.check(self.expected, self.verification_budget)
            try:
                async with asyncio.timeout_at(self.verification_until):
                    observed = await challenges.observe(self.page)
                    if observed.get("status") == "unavailable":
                        raise CheckboxUnavailable("checkbox_observer_error")
                    if observed.get("kind") == "interactive":
                        self.verification_reason = "checkbox_interactive"
                        return False
                    # Absence of a challenge is observed here. Login is proven
                    # later by the credential flow, never by this input command.
                    if observed.get("status") == "clear" and await self.site_accepted() is True:
                        self.callback.check(self.expected, self.verification_budget)
                        return True
            except TimeoutError:
                self.verification_reason = "checkbox_observation_timeout"
                return False
            await asyncio.sleep(max(0, min(.05, self.verification_until-time.monotonic())))

    async def _dispose(self):
        if not self._closed:
            self.page.remove_listener("framenavigated", self.listener)
            self._closed = True
        for name in ("control", "iframe"):
            handle = getattr(self, name)
            if handle is not None:
                await handle.dispose()
                setattr(self, name, None)

    async def close(self):
        self.authority_lifecycle_phase = "cleanup"
        self.callback.revoke()
        if self._cleanup_task is None:
            self._cleanup_task = self.callback.defer_cleanup(self._dispose)
        await self.callback.drained()
        self.callback.release_resource()


async def solve_checkbox(*, budget: Budget, state: dict | None = None, **binding) -> CheckboxResult:
    """Decline before input permits existing fallback; issued input never does."""
    broker = None
    try:
        broker = CheckboxBroker(**binding)
        await broker.callback.claim_resource()
        async with asyncio.timeout_at(budget.deadline):
            await broker.bind(budget)
        broker.authority_lifecycle_phase = "after_bind"
        await broker.callback.effect({"action": "activate_checkbox"}, broker.expected, budget)
        if await broker.callback.verified(broker.expected, budget):
            return CheckboxResult("accepted", "site_postcondition", broker.attempted)
        return CheckboxResult("manual", broker.verification_reason, broker.attempted)
    except ResourceBusy:
        if broker is not None:
            broker.authority_failure = "resource_busy"
        return CheckboxResult("blocked", "resource_busy", bool(broker and broker.attempted))
    except FenceChanged:
        if broker is not None and broker.authority_failure is None:
            broker.authority_failure = broker.navigation_reason or "fence_changed"
        return CheckboxResult("blocked", "stale_authority", bool(broker and broker.attempted))
    except (CheckboxUnavailable, BudgetExhausted, TimeoutError) as error:
        reason = str(error) if isinstance(error, CheckboxUnavailable) else "budget_or_authority"
        if broker is not None and reason in {"checkbox_not_unique", "checkbox_not_ready"}:
            try:
                await broker.diagnose_count_failure(budget)
            except FenceChanged:
                broker.authority_failure = broker.authority_failure or broker.navigation_reason or "fence_changed"
                return CheckboxResult("blocked", "stale_authority", broker.attempted)
        return CheckboxResult("manual", reason, bool(broker and broker.attempted))
    except asyncio.CancelledError:
        raise
    except DrainIncomplete:
        raise
    except Exception:
        return CheckboxResult("manual", "checkbox_command_failed" if broker and broker.attempted
                              else "checkbox_observer_error", bool(broker and broker.attempted))
    finally:
        if state is not None:
            state["attempted"] = bool(broker and broker.attempted)
            state["authority_failure"] = broker.authority_failure if broker is not None else None
            state["unrelated_frame_navigations"] = broker.unrelated_frame_navigations if broker is not None else 0
            state["non_owner_frame_navigations"] = broker.non_owner_frame_navigations if broker is not None else 0
            if broker is not None:
                first_event = broker.authority_first_event
                state["authority_first_event"] = ({
                    "relation": first_event.relation, "phase": first_event.phase,
                    "command_requested": first_event.command_requested,
                } if first_event is not None else None)
                state["authority_additional_events"] = broker.authority_additional_events
                state["authority_lifecycle_phase"] = broker.authority_lifecycle_phase
                state["command_requested"] = broker.attempted
                state.update(broker.checkbox_counts)
                state["checkbox_document"] = broker.checkbox_document
        if broker is not None:
            await broker.close()
