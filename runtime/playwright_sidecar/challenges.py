# SPDX-License-Identifier: MIT
"""Bounded CAPTCHA observations and a separate, opt-in handler contract.

No credential, response token, page text or frame URL leaves this module.
Provider protocol recognition is independent of the site being visited.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

MAX_FRAMES = 32
OBSERVATION_TIMEOUT_S = 2.0
KINDS = frozenset({"checkbox", "interactive", "unknown"})
PROVIDERS = frozenset({"recaptcha", "hcaptcha", "turnstile", "generic"})
# Domains whose cookies hold a challenge provider's own state for this
# browser (its risk history), independent of the site being visited.
PROVIDER_COOKIE_DOMAINS = ("google.com", "recaptcha.net", "hcaptcha.com",
                           "challenges.cloudflare.com")


def provider_cookie(cookie: dict) -> bool:
    """A cookie of a challenge provider, not of the visited site."""
    domain = str((cookie or {}).get("domain") or "").lower().lstrip(".")
    return any(domain == known or domain.endswith("." + known)
               for known in PROVIDER_COOKIE_DOMAINS)

# Shared by the document observer and the frame visibility check. Response
# fields are inspected only in the nearest isolated widget, never the page.
_HELPERS_JS = r"""
const visible = el => {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  for (let p = el; p; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (s.display === 'none' || s.visibility === 'hidden' ||
        Number.parseFloat(s.opacity || '1') < 0.05 || p.hidden) return false;
  }
  return true;
};
const providerFrame = el => {
  try {
    const u = new URL(el.src, document.baseURI);
    if (u.protocol !== 'https:') return null;
    const h = u.hostname.toLowerCase(), p = u.pathname;
    if (['www.google.com', 'www.recaptcha.net', 'recaptcha.google.com'].includes(h) &&
        /^\/recaptcha\/(api2|enterprise)\/(anchor|bframe)$/.test(p))
      return {provider:'recaptcha', kind:p.endsWith('/anchor') ? 'checkbox' : 'interactive'};
    if ((h === 'hcaptcha.com' || h.endsWith('.hcaptcha.com')) &&
        /^\/captcha\//.test(p))
      return {provider:'hcaptcha', kind:u.searchParams.get('frame') === 'checkbox' ? 'checkbox' : 'interactive'};
    if (h === 'challenges.cloudflare.com' && p.startsWith('/cdn-cgi/challenge-platform/'))
      return {provider:'turnstile', kind:'checkbox'};
  } catch (_) {}
  return null;
};
const widgetSelector = '.g-recaptcha, #g-recaptcha, .h-captcha, .cf-turnstile';
const widgetProvider = el => el.matches('.h-captcha') ? 'hcaptcha' :
  el.matches('.cf-turnstile') ? 'turnstile' : 'recaptcha';
const answered = (el, provider) => {
  const widget = el.closest(widgetSelector);
  const frame = el.matches('iframe') ? el : el.querySelector('iframe');
  const name = {recaptcha:'g-recaptcha-response', hcaptcha:'h-captcha-response',
                turnstile:'cf-turnstile-response'}[provider];
  if (!name) return false;
  for (let p = widget || el.parentElement; p && !['BODY','HTML','FORM'].includes(p.tagName); p = p.parentElement) {
    const frames = [...p.querySelectorAll('iframe')].filter(providerFrame);
    if (frames.some(f => f !== frame)) return false;
    const values = p.querySelectorAll(`[name="${name}"]`);
    if (values.length) return values.length === 1 && Boolean(values[0].value.trim());
    if (p === widget) break;
  }
  return false;
};
"""

_OBSERVE_JS = "() => {" + _HELPERS_JS + r"""
  const challenges = [];
  for (const el of document.querySelectorAll(widgetSelector)) {
    const provider = widgetProvider(el);
    // Invisible execution and a passive badge do not ask for interaction.
    if (el.dataset.size === 'invisible' || el.closest('.grecaptcha-badge')) continue;
    if (visible(el) && !answered(el, provider))
      challenges.push({provider, kind:'checkbox'});
  }
  for (const el of document.querySelectorAll('iframe')) {
    const info = providerFrame(el);
    if (info && visible(el) && !el.closest('.grecaptcha-badge') && !answered(el, info.provider))
      challenges.push(info);
  }
  // Generic human-verification checkboxes: match the control's own label,
  // never nearby prose, marketing text, a script name or a data-sitekey alone.
  const humanLabel = /^(?:i(?:['’]m| am) not a robot|(?:io )?non sono un robot|i am human|sono umano)[.!]?$/i;
  for (const el of document.querySelectorAll('input[type=checkbox], [role=checkbox]')) {
    if (!visible(el) || el.checked || el.getAttribute('aria-checked') === 'true') continue;
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ');
    const label = el.getAttribute('aria-label') || labelled.trim() ||
      [...(el.labels || [])].map(l => l.textContent).join(' ') || el.textContent;
    if (humanLabel.test((label || '').replace(/\s+/g,' ').trim()))
      challenges.push({provider:'generic', kind:'checkbox'});
  }
  // An image/input pair explicitly identified as a CAPTCHA is interactive.
  const images = [...document.querySelectorAll('img[alt*="captcha" i], img[id*="captcha" i]')].filter(visible);
  const inputs = [...document.querySelectorAll('input[name*="captcha" i], input[id*="captcha" i]')]
    .filter(el => el.type !== 'hidden' && visible(el));
  if (images.length && inputs.length) challenges.push({provider:'generic', kind:'interactive'});
  return {ready:document.readyState !== 'loading', challenges};
}
"""

_FRAME_JS = "el => {" + _HELPERS_JS + """
  const info = providerFrame(el);
  return {visible:visible(el) && !el.closest('.grecaptcha-badge'),
          answered:Boolean(info && answered(el, info.provider))};
}
"""


def capabilities() -> dict:
    """Advertise only implemented capabilities; automatic handling is off."""
    return {"detection": True, "manual": True, "automatic": False,
            "kinds": sorted(KINDS)}


async def observe(page) -> dict:
    """Observe visible frames, waiting only for loading documents within one deadline."""
    found = []
    incomplete = False
    try:
        async with asyncio.timeout(OBSERVATION_TIMEOUT_S):
            for _ in range(MAX_FRAMES + 1):
                frames = getattr(page, "frames", None)
                if not isinstance(frames, (list, tuple)) or not frames:
                    frames = [page]
                incomplete = len(frames) > MAX_FRAMES
                loading = []
                for frame in frames[:MAX_FRAMES]:
                    parent = getattr(frame, "parent_frame", None)
                    visible = True
                    child = frame
                    depth = 0
                    while parent is not None:
                        depth += 1
                        if depth > MAX_FRAMES:
                            raise ValueError("frame_depth")
                        element = await child.frame_element()
                        state = await element.evaluate(_FRAME_JS)
                        if state.get("visible") is not True or state.get("answered") is True:
                            visible = False
                            break
                        child, parent = parent, getattr(parent, "parent_frame", None)
                    if not visible:
                        continue
                    value = await frame.evaluate(_OBSERVE_JS)
                    if (not isinstance(value, dict)
                            or not isinstance(value.get("challenges"), list)
                            or type(value.get("ready")) is not bool):
                        raise ValueError("invalid_observation")
                    if value.get("ready") is not True:
                        incomplete = True
                        loading.append(frame)
                    for item in value["challenges"]:
                        if (isinstance(item, dict) and item.get("provider") in PROVIDERS
                                and item.get("kind") in KINDS):
                            found.append({"provider": item["provider"], "kind": item["kind"]})
                # Scan every visible frame before waiting: an already visible
                # challenge must pause immediately, even while another loads.
                if found or not loading or len(frames) > MAX_FRAMES:
                    break
                waits = [getattr(frame, "wait_for_load_state", None) for frame in loading]
                if not all(callable(wait) for wait in waits):
                    break
                for wait in waits:
                    await wait("domcontentloaded")
                # Re-read the frame tree and DOM; readiness is never inferred
                # from the load event alone. No browser action has occurred.
    except asyncio.CancelledError:
        raise
    except Exception:
        incomplete = True
    if found:
        # An image challenge takes precedence over its underlying checkbox.
        priority = {"interactive": 0, "checkbox": 1, "unknown": 2}
        selected = min(found, key=lambda item: priority[item["kind"]])
        return {"status": "required", **selected, "handler": "human",
                "automatic": False}
    return {"status": "unavailable" if incomplete else "clear"}


class Handler(Protocol):
    """Future local handler: the caller controls authority and browser access."""

    async def handle(self, *, page, observation: dict) -> None: ...


async def handle_once(*, page, observation: dict, handler: Handler | None = None,
                      timeout_s: float = 10.0) -> dict:
    """Explicit extension point. No handler is registered or enabled by default.

    A handler's assertion of success is never accepted; observe independently.
    Browser/session locks, origin guards and the operation deadline belong to
    the caller, just as they do for authenticated human control.
    """
    if handler is None or observation.get("status") != "required":
        return dict(observation)
    async with asyncio.timeout(min(10.0, max(0.0, timeout_s))):
        await handler.handle(page=page, observation=dict(observation))
        return await observe(page)
