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


# References remain inside a JSHandle, so identical replacement nodes cannot
# inherit a model decision. Text is bounded and excludes editable/secret areas.
_OBSERVE_JS = r"""() => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && s.display !== 'none' &&
      s.visibility !== 'hidden' && Number(s.opacity) >= .05 &&
      r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  const topmost = el => {
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(
      Math.max(0, Math.min(innerWidth - 1, r.x + r.width / 2)),
      Math.max(0, Math.min(innerHeight - 1, r.y + r.height / 2)));
    return top === el || !!(top && el.contains(top));
  };
  const excluded = 'input:not([type=button]):not([type=submit]),textarea,select,' +
    '[contenteditable=true],[data-metnos-redact],script,style,form';
  const text = root => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const parts = [];
    for (let node = walker.nextNode(), n = 0; node && n < 400;
         node = walker.nextNode(), n++) {
      if (!node.parentElement.closest(excluded) && visible(node.parentElement))
        parts.push(node.textContent);
    }
    return parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 4000);
  };
  const roots = new Map();
  for (const el of Array.from(document.querySelectorAll(
      'button,input[type=button],input[type=submit],a,[role=button]')).slice(0, 640)) {
    if (!visible(el) || !topmost(el)) continue;
    let root = null;
    for (let p = el.parentElement, depth = 0; p && p !== document.body && depth < 14;
         p = p.parentElement, depth++) {
      const r = p.getBoundingClientRect(), s = getComputedStyle(p);
      const ratio = r.width * r.height / (innerWidth * innerHeight);
      if (p.matches('dialog,[role=dialog],[role=alertdialog],[aria-modal=true]') && ratio >= .03) {
        root = p; break;
      }
      if ((s.position === 'fixed' || s.position === 'sticky') && ratio >= .12) root = p;
    }
    if (!root || Array.from(root.querySelectorAll(
        'input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]),' +
        'textarea,[contenteditable=true]')).some(visible)) continue;
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
      const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => document.getElementById(id)).filter(x => x && root.contains(x))
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
}"""

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


async def reject_cookies(page, state: dict, *, redact=None,
                         timeout_s: float = 6.0, enabled: bool = True) -> CookieOutcome:
    """Resolve at most two cookie panels, with four local calls per login flow.

    Cache stores only hashes and typed decisions; every click rechecks live
    node identity, text, visibility and HTML navigation/submit constraints.
    ``redact`` is an additional local defence supplied by credential injection.
    """
    resolved = False
    previous_click = ""
    for _ in range(3):
        handle = None
        try:
            handle = await page.evaluate_handle(_OBSERVE_JS)
            panels = await handle.evaluate('(saved) => saved.public')
            if not panels:
                return CookieOutcome("resolved" if resolved else "clear",
                                     "cookie" if resolved else "unknown")
            # Redact only observed text, never IDs or JSON structure: a short
            # credential can coincide with an ID or the literal ``true``.
            clean = redact if redact is not None else lambda text: text
            safe_panels = [{**panel, "text": clean(panel["text"]), "controls": [
                {**control, "name": clean(control["name"])}
                for control in panel["controls"]]} for panel in panels]
            serialized = json.dumps(safe_panels, ensure_ascii=False)
            key = hashlib.sha256(serialized.encode()).hexdigest()
            if key == previous_click:
                return CookieOutcome("blocked", "cookie", "obstruction_remains")
            cache = state.setdefault("decisions", {})
            decision = cache.get(key)
            if decision is None:
                if not enabled:
                    return CookieOutcome("blocked", reason="model_disabled")
                if state.get("calls", 0) >= 4:
                    return CookieOutcome("blocked", reason="decision_limit")
                state["calls"] = state.get("calls", 0) + 1
                try:
                    decision = await asyncio.wait_for(
                        asyncio.to_thread(_classify, safe_panels, timeout_s), timeout_s)
                except (asyncio.TimeoutError, TimeoutError):
                    return CookieOutcome("blocked", reason="model_timeout")
                except Exception:
                    return CookieOutcome("blocked", reason="model_failed")
                cache[key] = decision
            if decision["kind"] == "other" and decision["effect"] == "none":
                if await handle.evaluate(_COMMIT_JS, {"check_only": True}) != "unchanged":
                    return CookieOutcome("blocked", reason="stale_dom")
                return CookieOutcome("resolved" if resolved else "clear", "other")
            if decision["kind"] != "cookie" or decision["effect"] != "reject_optional":
                return CookieOutcome("blocked", decision["kind"], "no_safe_rejection")
            if state.get("clicks", 0) >= 2:
                return CookieOutcome("blocked", "cookie", "dismissal_limit")
            outcome = await handle.evaluate(_COMMIT_JS, decision)
            if outcome != "clicked":
                return CookieOutcome("blocked", "cookie", outcome)
            state["clicks"] = state.get("clicks", 0) + 1
            previous_click = key
            await page.wait_for_timeout(150)
            if await handle.evaluate('(saved) => saved.url !== location.href'):
                return CookieOutcome("blocked", "cookie", "page_changed")
            resolved = True
        except Exception:
            return CookieOutcome("blocked", reason="observation_failed")
        finally:
            if handle is not None:
                try:
                    await handle.dispose()
                except Exception:
                    pass  # Navigation may already have destroyed the handle.
    return CookieOutcome("blocked", "cookie", "dismissal_limit")
