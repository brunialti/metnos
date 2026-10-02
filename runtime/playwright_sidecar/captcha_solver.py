# SPDX-License-Identifier: MIT
"""One local CAPTCHA attempt on the existing guarded page; never an API service.

The library's Playwright interface also accepts Camoufox's Playwright Page.
Do not use its CAMOUFOX workaround: that patches a shared extension and needs
main-world evaluation. Unsupported challenges remain available to the user.
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit

import sites_audit

MAX_ATTEMPT_S = 10.0

# The library logs raw browser exceptions, which may contain page URLs/tokens.
# Keep only our bounded, metadata-only audit below, including failed attempts.
_library_log = logging.getLogger("playwright_captcha")
_library_log.addHandler(logging.NullHandler())
_library_log.propagate = False

# Provider protocol, not a target-site rule. Do not pass a lookalike frame to
# a library which finds frames by substring. Never return tokens or page text.
_CHALLENGE_KIND_JS = r"""
() => {
  const prefix = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/';
  const frames = Array.from(document.querySelectorAll('iframe'));
  const candidates = frames.filter(f => (f.src || '').includes(prefix));
  if (!candidates.length) return null;
  if (candidates.some(f => {
    try {
      const u = new URL(f.src);
      return u.protocol !== 'https:' || u.hostname !== 'challenges.cloudflare.com' ||
        !u.pathname.startsWith('/cdn-cgi/challenge-platform/');
    } catch (_) { return true; }
  })) return null;
  if (document.querySelector('input[name="cf-turnstile-response"]')) return 'turnstile';
  if (document.querySelector('script[src*="/cdn-cgi/challenge-platform/"]')) return 'interstitial';
  return null;
}
"""


async def solve_once(*, page, state: dict, allowed_hosts, pending_script: str,
                     owner: str = "", session_id: str = "", domain: str = "",
                     timeout_s: float = MAX_ATTEMPT_S) -> bool:
    """Attempt once per login flow, including failures, cancellation and resume.

    The caller owns the session lock, network guards and whole-login deadline.
    A library return is not proof: re-observe the challenge independently.
    """
    if state.get("attempted"):
        return False
    state["attempted"] = True
    state["status"] = "unresolved"
    started = time.monotonic()

    def origin_allowed():
        parsed = urlsplit(page.url)
        return parsed.scheme in {"http", "https"} and parsed.hostname in allowed_hosts

    try:
        async with asyncio.timeout(min(MAX_ATTEMPT_S, max(0.0, timeout_s))):
            if not origin_allowed():
                state["status"] = "origin_unverified"
                return False
            kind = await page.evaluate(_CHALLENGE_KIND_JS)
            if kind not in {"turnstile", "interstitial"}:
                state["status"] = "unsupported"
                return False
            from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

            captcha_type = (CaptchaType.CLOUDFLARE_TURNSTILE if kind == "turnstile"
                            else CaptchaType.CLOUDFLARE_INTERSTITIAL)
            async with ClickSolver(framework=FrameworkType.PLAYWRIGHT, page=page,
                                   max_attempts=1, attempt_delay=0) as solver:
                await solver.solve_captcha(
                    captcha_container=page, captcha_type=captcha_type,
                    wait_checkbox_attempts=1, wait_checkbox_delay=0,
                    checkbox_click_attempts=1, solve_click_delay=3)
            # Provider success and the site's completion callback are separate
            # events. Wait briefly for the latter within the same total budget.
            settle_until = time.monotonic() + 1.0
            while True:
                if not origin_allowed():
                    state["status"] = "origin_unverified"
                    return False
                if await page.evaluate(pending_script) is False:
                    state["status"] = "solved"
                    return True
                if time.monotonic() >= settle_until:
                    break
                await asyncio.sleep(0.05)
    except ImportError:
        state["status"] = "unavailable"
    except TimeoutError:
        state["status"] = "timeout"
    except asyncio.CancelledError:
        state["status"] = "cancelled"
        raise
    except Exception:
        # Third-party exception strings can include URLs or page content.
        state["status"] = "failed"
    finally:
        sites_audit.record(
            "captcha_attempt", owner=owner, session_id=session_id, domain=domain,
            solver="playwright-captcha/local-click", status=state["status"],
            elapsed_ms=round((time.monotonic() - started) * 1000))
    return False
