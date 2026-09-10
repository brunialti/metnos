# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded semantic cookie rejection; browser observations never grant authority."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CookieOutcome:
    status: Literal["clear", "resolved", "blocked"]
    kind: Literal["cookie", "other", "unknown"] = "unknown"
    reason: str = ""
    # Counts only, never observed text, and the largest seen during the flow:
    # a caller must be able to tell "no panel exists" from "panels exist and
    # none was actable", and from "a panel existed and was dismissed".
    frames: int = 0
    panels: int = 0


# A consent panel is frequently rendered inside a nested browsing context. A
# main-document observation cannot see it at all, so it reports no panel
# instead of an obstruction and the caller proceeds against a covered page.
MAX_OBSERVED_FRAMES = 8
MAX_OBSERVED_PANELS = 3
# The same blindness a second time, one level down: a consent platform that
# renders its banner into an open shadow root is invisible to a light-DOM
# query, and the observation reports a clear page while the page is covered.
# Observed on a real site: zero panels seen, no obstruction recorded, and the
# login discovery that followed found no form because a modal was over it.
MAX_OBSERVED_SHADOW_ROOTS = 24
# The per-origin budget above is two clicks. Consent state belongs to an
# origin and is reset when one is crossed - correctly, because a new origin's
# banner is a new panel - but the ceiling lived inside that same state, so a
# site bouncing between two origins reset it every time and never reached it.
# This one is carried across the resets: it is the whole session's ceiling.
MAX_SESSION_DISMISSALS = 6


# References remain inside a JSHandle, so identical replacement nodes cannot
# inherit a model decision. Text is bounded and excludes editable/secret areas.
_OBSERVE_JS = r"""() => {
  const SHADOW_ROOT_BUDGET = %d;
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && s.display !== 'none' &&
      s.visibility !== 'hidden' && Number(s.opacity) >= .05 &&
      r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  // Upwards through the composed tree: a shadow root has no parent element,
  // its host does. Plain `parentElement` stops at the boundary and would make
  // every control inside a shadow root look like a top-level one.
  const up = el => {
    const parent = el.parentElement;
    if (parent) return parent;
    const root = el.getRootNode();
    return (root && root.host) ? root.host : null;
  };
  const within = (outer, inner) => {
    for (let n = inner; n; n = up(n)) if (n === outer) return true;
    return false;
  };
  // `elementFromPoint` answers per root, so on a shadow host it returns the
  // host and never the control the user would actually hit. Descend until it
  // stops changing.
  const deepFromPoint = (x, y) => {
    let node = document.elementFromPoint(x, y);
    for (let depth = 0; node && node.shadowRoot && depth < 14; depth++) {
      const inner = node.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === node) break;
      node = inner;
    }
    return node;
  };
  const topmost = el => {
    const r = el.getBoundingClientRect();
    const top = deepFromPoint(
      Math.max(0, Math.min(innerWidth - 1, r.x + r.width / 2)),
      Math.max(0, Math.min(innerHeight - 1, r.y + r.height / 2)));
    return top === el || !!(top && within(el, top));
  };
  const excluded = 'input:not([type=button]):not([type=submit]),textarea,select,' +
    '[contenteditable=true],[data-metnos-redact],script,style,form';
  const text = root => {
    const parts = [];
    let seen = 0;
    const walk = node => {
      if (seen >= 400 || !node) return;
      if (node.nodeType === 3) {
        const parent = node.parentElement;
        if (parent && !parent.closest(excluded) && visible(parent)) {
          parts.push(node.textContent);
          seen++;
        }
        return;
      }
      if (node.nodeType !== 1 && node.nodeType !== 11) return;
      if (node.nodeType === 1 && node.shadowRoot) walk(node.shadowRoot);
      for (const child of node.childNodes) walk(child);
    };
    walk(root);
    return parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 4000);
  };
  const editableInside = root => {
    const selector =
      'input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]),' +
      'textarea,[contenteditable=true]';
    const stack = [root];
    for (let guard = 0; stack.length && guard < 4000; guard++) {
      const node = stack.pop();
      if (!node.querySelectorAll) continue;
      for (const el of node.querySelectorAll(selector)) if (visible(el)) return true;
      for (const el of node.querySelectorAll('*')) if (el.shadowRoot) stack.push(el.shadowRoot);
    }
    return false;
  };
  const searchRoots = [document];
  for (let index = 0; index < searchRoots.length; index++) {
    const node = searchRoots[index];
    if (!node.querySelectorAll) continue;
    for (const el of node.querySelectorAll('*')) {
      if (!el.shadowRoot) continue;
      if (searchRoots.length >= SHADOW_ROOT_BUDGET + 1) break;
      searchRoots.push(el.shadowRoot);
    }
  }
  const candidates = [];
  const selector = 'button,input[type=button],input[type=submit],a,[role=button]';
  for (const node of searchRoots) {
    if (candidates.length >= 640) break;
    if (node.querySelectorAll) candidates.push(...node.querySelectorAll(selector));
  }
  const roots = new Map();
  for (const el of candidates.slice(0, 640)) {
    if (!visible(el) || !topmost(el)) continue;
    let root = null;
    for (let p = up(el), depth = 0;
         p && p !== document.body && p !== document.documentElement && depth < 14;
         p = up(p), depth++) {
      const r = p.getBoundingClientRect(), s = getComputedStyle(p);
      const ratio = r.width * r.height / (innerWidth * innerHeight);
      if (p.matches('dialog,[role=dialog],[role=alertdialog],[aria-modal=true]') && ratio >= .03) {
        root = p; break;
      }
      if ((s.position === 'fixed' || s.position === 'sticky') && ratio >= .12) root = p;
    }
    if (!root || editableInside(root)) continue;
    if (!roots.has(root)) {
      if (roots.size >= 3) continue;
      roots.set(root, []);
    }
    if (roots.get(root).length < 16) roots.get(root).push(el);
  }
  const nodes = [], panels = [];
  for (const [root, controls] of roots) {
    const panel_id = 'p' + (panels.length + 1);
    const items = controls.map((el, i) => {
      const scope = el.getRootNode();
      const byId = id => (scope && scope.getElementById)
        ? scope.getElementById(id) : document.getElementById(id);
      const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(byId).filter(x => x && within(root, x))
        .map(text).join(' ');
      const name = (el.getAttribute('aria-label') || labelled ||
        el.getAttribute('title') || text(el) ||
        (el.matches('input[type=button],input[type=submit]') ? el.value : '')).trim();
      const form = el.form || el.closest('form');
      const submit = !!form && (el.matches('input[type=submit],input[type=image]') ||
        (el.tagName === 'BUTTON' && el.type === 'submit'));
      const safe = !submit && !el.closest('a[href],[formaction],[download]') &&
        !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
        !el.isContentEditable && /[\p{L}\p{N}]/u.test(name) && !/^x$/i.test(name);
      return {id: panel_id + 'c' + (i + 1), name: name.slice(0, 300), safe: !!safe};
    });
    nodes.push({root, controls, signatures: controls.map(el =>
      [el.outerHTML, String(el.onclick || '')])});
    panels.push({id: panel_id, text: text(root), controls: items});
  }
  return {nodes, public: panels, url: location.href};
}""" % (MAX_OBSERVED_SHADOW_ROOTS,)

_COMMIT_JS = r"""(saved, choice) => {
  const fresh = (""" + _OBSERVE_JS + r""")();
  if (fresh.url !== saved.url || JSON.stringify(fresh.public) !== JSON.stringify(saved.public) ||
      fresh.nodes.some((p, i) => p.root !== saved.nodes[i].root ||
        JSON.stringify(p.signatures) !== JSON.stringify(saved.nodes[i].signatures) ||
        p.controls.some((el, j) => el !== saved.nodes[i].controls[j]))) return 'stale_dom';
  if (choice.check_only) return 'unchanged';
  const pi = saved.public.findIndex(p => p.id === choice.panel_id);
  const ci = pi < 0 ? -1 : saved.public[pi].controls.findIndex(c => c.id === choice.control_id);
  if (ci < 0 || !fresh.public[pi].controls[ci].safe) return 'unsafe_control';
  saved.nodes[pi].controls[ci].click();
  return 'clicked';
}"""


def _classify(panels: list[dict], timeout_s: float) -> dict:
    import i18n
    import prompt_loader
    from llm_router import LLMRouter
    from llm_workloads import tier_for

    provider = LLMRouter().provider(tier_for("sites.cookie_resolution"))
    if getattr(provider, "mode", "") != "local":
        raise RuntimeError("local_model_required")
    result = provider.chat(
        prompt_loader.get("sites_cookie_resolution", i18n.current_lang()),
        json.dumps(panels, ensure_ascii=False), max_tokens=160,
        request_timeout_s=timeout_s)
    decision = json.loads(str(getattr(result, "text", "") or ""))
    if (not isinstance(decision, dict) or set(decision) != {
            "kind", "panel_id", "control_id", "effect"}
            or decision["kind"] not in {"cookie", "other", "unknown"}
            or decision["effect"] not in {"reject_optional", "none"}
            or not all(isinstance(value, str) for value in decision.values())):
        raise ValueError("invalid_decision")
    return decision


async def _observe_frames(page) -> list[tuple[int, object, list[dict]]]:
    """Observe the main document and its nested contexts, bounded and in order.

    Each context keeps its own handle, because the live node references that
    the commit step re-verifies belong to that context alone. The panel budget
    stays global, so widening the observation never widens what the model sees.
    """
    observed: list[tuple[int, object, list[dict]]] = []
    total = 0
    for index, frame in enumerate(page.frames[:MAX_OBSERVED_FRAMES]):
        if total >= MAX_OBSERVED_PANELS:
            break
        handle = None
        try:
            handle = await frame.evaluate_handle(_OBSERVE_JS)
            panels = await handle.evaluate('(saved) => saved.public')
        except Exception:
            # A nested context can detach or navigate while it is read; that is
            # not an observation failure of the page as a whole.
            panels = None
        if not panels:
            await _dispose_one(handle)
            continue
        allowed = panels[:MAX_OBSERVED_PANELS - total]
        total += len(allowed)
        observed.append((index, handle, allowed))
    return observed


async def _dispose_one(handle) -> None:
    if handle is None:
        return
    try:
        await handle.dispose()
    except Exception:
        pass  # Navigation may already have destroyed the handle.


async def _dispose_all(observed) -> None:
    for _index, handle, _panels in observed:
        await _dispose_one(handle)


def _global_id(index: int, identifier: str) -> str:
    return f"f{index}{identifier}"


def _local_id(index: int, identifier: str) -> str:
    return identifier[len(f"f{index}"):]


async def reject_cookies(page, state: dict, *, redact=None,
                         timeout_s: float = 6.0, enabled: bool = True) -> CookieOutcome:
    """Resolve at most two cookie panels, with four local calls per login flow.

    Cache stores only hashes and typed decisions; every click rechecks live
    node identity, text, visibility and HTML navigation/submit constraints.
    ``redact`` is an additional local defence supplied by credential injection.
    """
    resolved = False
    previous_click = ""
    frames = panels_seen = 0
    for _ in range(3):
        observed = await _observe_frames(page)
        # The most this flow ever saw, not what is left at the end: a panel
        # that was found and dismissed did exist, and a run that reported zero
        # after resolving one would hide the only case worth recording.
        frames = max(frames, len(observed))
        panels_seen = max(
            panels_seen, sum(len(items) for _i, _h, items in observed))
        try:
            if not observed:
                return CookieOutcome(
                    "resolved" if resolved else "clear",
                    "cookie" if resolved else "unknown", "",
                    frames, panels_seen)
            # Redact only observed text, never IDs or JSON structure: a short
            # credential can coincide with an ID or the literal ``true``.
            clean = redact if redact is not None else lambda text: text
            safe_panels = []
            owner = {}
            for index, _handle, items in observed:
                for panel in items:
                    owner[_global_id(index, panel["id"])] = index
                    safe_panels.append({
                        **panel,
                        "id": _global_id(index, panel["id"]),
                        "text": clean(panel["text"]),
                        "controls": [
                            {**control,
                             "id": _global_id(index, control["id"]),
                             "name": clean(control["name"])}
                            for control in panel["controls"]],
                    })
            serialized = json.dumps(safe_panels, ensure_ascii=False)
            key = hashlib.sha256(serialized.encode()).hexdigest()
            if key == previous_click:
                return CookieOutcome("blocked", "cookie", "obstruction_remains",
                                     frames, panels_seen)
            cache = state.setdefault("decisions", {})
            decision = cache.get(key)
            if decision is None:
                if not enabled:
                    return CookieOutcome("blocked", "unknown", "model_disabled",
                                         frames, panels_seen)
                if state.get("calls", 0) >= 4:
                    return CookieOutcome("blocked", "unknown", "decision_limit",
                                         frames, panels_seen)
                state["calls"] = state.get("calls", 0) + 1
                try:
                    decision = await asyncio.wait_for(
                        asyncio.to_thread(_classify, safe_panels, timeout_s), timeout_s)
                except (asyncio.TimeoutError, TimeoutError):
                    return CookieOutcome("blocked", "unknown", "model_timeout",
                                         frames, panels_seen)
                except Exception:
                    return CookieOutcome("blocked", "unknown", "model_failed",
                                         frames, panels_seen)
                cache[key] = decision
            if decision["kind"] == "other" and decision["effect"] == "none":
                for _index, handle, _items in observed:
                    if await handle.evaluate(
                            _COMMIT_JS, {"check_only": True}) != "unchanged":
                        return CookieOutcome("blocked", "unknown", "stale_dom",
                                             frames, panels_seen)
                return CookieOutcome("resolved" if resolved else "clear", "other",
                                     "", frames, panels_seen)
            if decision["kind"] != "cookie" or decision["effect"] != "reject_optional":
                return CookieOutcome("blocked", decision["kind"], "no_safe_rejection",
                                     frames, panels_seen)
            index = owner.get(decision["panel_id"])
            if index is None:
                return CookieOutcome("blocked", "cookie", "unknown_panel",
                                     frames, panels_seen)
            handle = next(item[1] for item in observed if item[0] == index)
            if state.get("clicks", 0) >= 2:
                return CookieOutcome("blocked", "cookie", "dismissal_limit",
                                     frames, panels_seen)
            if (state.get("clicks", 0) + state.get("carried", 0)
                    >= MAX_SESSION_DISMISSALS):
                return CookieOutcome("blocked", "cookie", "session_limit",
                                     frames, panels_seen)
            outcome = await handle.evaluate(_COMMIT_JS, {
                **decision,
                "panel_id": _local_id(index, decision["panel_id"]),
                "control_id": _local_id(index, decision["control_id"]),
            })
            if outcome != "clicked":
                return CookieOutcome("blocked", "cookie", outcome,
                                     frames, panels_seen)
            state["clicks"] = state.get("clicks", 0) + 1
            previous_click = key
            await page.wait_for_timeout(150)
            if await handle.evaluate('(saved) => saved.url !== location.href'):
                return CookieOutcome("blocked", "cookie", "page_changed",
                                     frames, panels_seen)
            resolved = True
        except Exception:
            return CookieOutcome("blocked", "unknown", "observation_failed",
                                 frames, panels_seen)
        finally:
            await _dispose_all(observed)
    return CookieOutcome("blocked", "cookie", "dismissal_limit",
                         frames, panels_seen)
