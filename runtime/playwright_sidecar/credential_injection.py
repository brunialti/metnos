# SPDX-License-Identifier: AGPL-3.0-only
"""credential_injection — iniezione credenziali RIPROGETTATA (spec sites §3.2).

Questo modulo gira DENTRO il broker (processo sidecar). È l'UNICO punto in cui
`credentials.load(domain)` viene chiamato per un login web e in cui un segreto
di login tocca la pagina. Il segreto NON esce mai da qui: né al planner, né
all'executor, né nel result, né nell'audit (solo il fingerprint).

I 3 CRITICI del red-team (spec §3.2, §12) sono implementati DETERMINISTICAMENTE
(mai «ci fidiamo del modello» — Qwn locale non ha la robustezza anti-injection):

  CRITICO-1 (anti-phishing): si inietta SOLO se
    (1) l'origine dell'`action` del form di login coincide ESATTAMENTE col
        dominio del vault (D-D: match esatto, niente sottodomini);
    (2) il campo password è nel FRAME TOP-LEVEL (mai iframe);
    (3) l'origine è verificata PRIMA di digitare.
  Mismatch → RIFIUTO (`origin_unverified`), nessuna digitazione.

  CRITICO-2 (destinazione non scelta dall'LLM): il broker risolve AUTONOMAMENTE
    i campi credenziale del form legittimo. L'executor/planner NON passano mai un
    selettore. I campi sono taggati qui (`data-metnos-*`) dal broker stesso.

  CRITICO-3 (niente segreto negli screenshot): il campo password è marcato
    `data-metnos-redact` PRIMA della digitazione; NESSUNO screenshot avviene fra
    `fill` e `submit`; il VLM non è mai invocato qui.

§7.9 deterministico. §2.8 onestà: nessun `logged_in:true` non verificato.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import os
import re
import struct
import time
import urllib.parse

import credentials  # runtime/credentials.py — vault cifrato (dentro il broker)
import sites_audit
import sites_origin  # ADR 0191 P2 — origine credenziale (scheme,host,port)
from playwright_sidecar import factor_resolvers
from sites_url_scrub import scrub_url

try:
    import detection_lexicon as _detlex
except ImportError:  # pragma: no cover - sidecar install incompleto
    _detlex = None


_LOGIN_SURFACE_SETTLE_S = 5.0
_EMAIL_FACTOR_WAIT_S = 22.0
_FACTOR_SUBMIT_SETTLE_S = 15.0
_AUTH_SUCCESS_STABLE_POLLS = 6


class _LoginBudget:
    def __init__(self, total_s: float):
        self.deadline = time.monotonic() + max(1.0, float(total_s))

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def remaining(self, cap_s: float, *, floor_s: float = 0.1) -> float:
        return max(floor_s, min(float(cap_s), self.deadline - time.monotonic()))


async def _checkpoint(callback, stage: str) -> None:
    if callback is None:
        return
    try:
        result = callback(stage)
        if inspect.isawaitable(result):
            await result
    except Exception:
        # Checkpoints are observability, never authority or success evidence.
        pass


# JS (main frame): individua il form di login, TAGGA i campi credenziale con
# attributi broker-owned e ritorna l'origine di submit per la verifica §3.2.
# Cerca SOLO in `document` (main frame): `document.forms`/`querySelectorAll` NON
# discendono negli iframe → un campo password in un iframe NON viene trovato
# (CRITICO-1 punto 2: mai iframe).
_LOCATE_LOGIN_FORM_JS = r"""
() => {
  document.querySelectorAll(
    '[data-metnos-pw],[data-metnos-user],[data-metnos-submit]')
    .forEach(el => {
      el.removeAttribute('data-metnos-pw');
      el.removeAttribute('data-metnos-user');
      el.removeAttribute('data-metnos-submit');
    });
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
      !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  };
  const passwordFields = Array.from(
    document.querySelectorAll('input[type=password]')).filter(visible);
  if (passwordFields.length !== 1)
    return {found: false, ambiguous: passwordFields.length > 1};
  const pw = passwordFields[0];
  const form = pw.closest('form');
  // Origine di submit: form.action riflette già l'URL ASSOLUTO risolto; se non
  // c'è form, l'origine è quella della pagina (submit-to-self).
  const actionResolved = (form && form.action) ? form.action : location.href;
  const scope = form || document;
  // username field: match esplicito per name/id, poi fallback all'input
  // testuale che PRECEDE la password in DOM order.
  const RE = /user|email|login|userid|account|utente|matricola|nome/i;
  let userEl = null;
  const cand = Array.from(scope.querySelectorAll(
    'input[type=text], input[type=email], input[type=tel], input:not([type])'))
    .filter(visible);
  for (const c of cand) {
    if (RE.test(c.name || '') || RE.test(c.id || '')) { userEl = c; break; }
  }
  if (!userEl) {
    const all = Array.from(scope.querySelectorAll('input'));
    const pwIdx = all.indexOf(pw);
    for (let i = pwIdx - 1; i >= 0; i--) {
      const t = (all[i].type || 'text').toLowerCase();
      if (visible(all[i]) && (t === 'text' || t === 'email' ||
          t === 'tel' || !all[i].type)) {
        userEl = all[i]; break;
      }
    }
  }
  let submitEl = null;
  if (form) {
    submitEl = Array.from(form.querySelectorAll(
      'button[type=submit], input[type=submit], input[type=image], button:not([type])'))
      .find(visible) || null;
  }
  // Tag deterministici broker-owned. Redaction del pw field SUBITO (prima di
  // qualunque digitazione o capture — CRITICO-3).
  pw.setAttribute('data-metnos-pw', '1');
  pw.setAttribute('data-metnos-redact', '1');
  if (userEl) {
    userEl.setAttribute('data-metnos-user', '1');
    userEl.setAttribute('data-metnos-redact', '1');
  }
  if (submitEl) submitEl.setAttribute('data-metnos-submit', '1');
  return {found: true, actionResolved, hasUser: !!userEl, hasSubmit: !!submitEl,
          inForm: !!form};
}
"""

# JS: rileva un campo OTP/2FA nel main frame (D-E: F1 non lo auto-risolve).
_DETECT_OTP_JS = r"""
() => {
  const RE = /otp|2fa|totp|one[-_ ]?time|verification|verify|codice|token|auth[-_ ]?code/i;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
      !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  };
  const inputs = Array.from(document.querySelectorAll('input'));
  for (const el of inputs) {
    if (!visible(el)) continue;
    if ((el.autocomplete || '') === 'one-time-code') return true;
    if (el.type === 'password') continue;  // la password è gestita a parte
    if (RE.test(el.name || '') || RE.test(el.id || '') ||
        RE.test(el.getAttribute('aria-label') || '') ||
        RE.test(el.placeholder || '')) return true;
  }
  return false;
}
"""

_LOCATE_OTP_FORM_JS = r"""
() => {
  document.querySelectorAll('[data-metnos-otp],[data-metnos-otp-submit]')
    .forEach(el => {
      el.removeAttribute('data-metnos-otp');
      el.removeAttribute('data-metnos-otp-submit');
    });
  const RE = /otp|2fa|totp|one[-_ ]?time|verification|verify|codice|token|auth[-_ ]?code/i;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
      !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  };
  const candidates = Array.from(document.querySelectorAll('input')).filter(el => {
    if (!visible(el) || el.type === 'password') return false;
    const attrs = [el.name, el.id, el.getAttribute('aria-label'), el.placeholder]
      .filter(Boolean).join(' ');
    return (el.autocomplete || '') === 'one-time-code' || RE.test(attrs);
  });
  if (!candidates.length) return {found: false};
  let segmented = false;
  let fields = candidates;
  if (candidates.length > 1) {
    const form = candidates[0].closest('form');
    const sameForm = candidates.every(el => el.closest('form') === form);
    const oneChar = candidates.every(el =>
      Number(el.maxLength || 0) === 1 ||
      (el.inputMode || '').toLowerCase() === 'numeric');
    const homogeneous = candidates.length <= 8 && candidates.every(el =>
      ['text', 'tel', 'number'].includes((el.type || 'text').toLowerCase()));
    if (!sameForm || (!oneChar && !homogeneous))
      return {found: false, ambiguous: true};
    segmented = true;
  }
  const otp = candidates[0];
  const form = otp.closest('form');
  const actionResolved = (form && form.action) ? form.action : location.href;
  let submitEl = null;
  if (form) submitEl = form.querySelector(
    'button[type=submit],input[type=submit],input[type=image],button:not([type])');
  fields.forEach(el => {
    el.setAttribute('data-metnos-otp', '1');
    el.setAttribute('data-metnos-redact', '1');
  });
  if (submitEl) submitEl.setAttribute('data-metnos-otp-submit', '1');
  const singleMaxLength = !segmented && Number(otp.maxLength || 0) > 0 &&
    Number(otp.maxLength) <= 12 ? Number(otp.maxLength) : 0;
  const numericOnly = fields.every(el => {
    const inputMode = (el.inputMode || '').toLowerCase();
    const pattern = (el.getAttribute('pattern') || '').replace(/\s/g, '');
    return (el.type || '').toLowerCase() === 'number' ||
      inputMode === 'numeric' || inputMode === 'decimal' ||
      pattern === '[0-9]*' || pattern === '\\d*';
  });
  return {found: true, actionResolved, hasSubmit: !!submitEl,
          segmented, fieldCount: fields.length,
          expectedLength: segmented ? fields.length : singleMaxLength,
          numericOnly};
}
"""

# JS: rileva un CAPTCHA (recaptcha/hcaptcha/turnstile) nel main frame.
_DETECT_CAPTCHA_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05;
  };
  const markers = Array.from(document.querySelectorAll(
    '.g-recaptcha, #g-recaptcha, .h-captcha, [data-sitekey], .cf-turnstile'));
  if (markers.some(visible)) return true;
  const ifr = Array.from(document.querySelectorAll('iframe'));
  for (const f of ifr) {
    if (!visible(f)) continue;
    const s = (f.src || '').toLowerCase();
    if (s.includes('recaptcha') || s.includes('hcaptcha') ||
        s.includes('turnstile')) return true;
  }
  return false;
}
"""

# JS: la pagina ha ancora un campo password interagibile nel main frame?
# I portali SPA spesso mantengono form di login nascosti anche nella dashboard.
_HAS_PASSWORD_JS = r"""
() => Array.from(document.querySelectorAll('input[type=password]')).some(el => {
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
    st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
    !el.disabled && el.getAttribute('aria-disabled') !== 'true';
})
"""

# Post-submit: una pagina puo' cambiare URL prima che il framework client
# rimonti il form di accesso. Il successo cookieless richiede quindi che una
# superficie autenticativa resti assente per piu' osservazioni consecutive.
# Oltre alla password copre gli stadi username-first tramite attributi web
# standard e struttura del form; non contiene sinonimi o parole di una lingua.
_HAS_LOGIN_SURFACE_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
      !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  };
  if (Array.from(document.querySelectorAll('input[type=password]')).some(visible))
    return true;
  const fields = Array.from(document.querySelectorAll(
    'input[autocomplete=username i],input[type=email],'
    + 'input[autocomplete=email i],[data-metnos-user-step],'
    + '[data-metnos-user]'));
  return fields.some(el => {
    if (!visible(el)) return false;
    const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
    const form = el.closest('form');
    if (el.hasAttribute('data-metnos-user-step') ||
        el.hasAttribute('data-metnos-user') || autocomplete === 'username')
      return true;
    if (!form || !(autocomplete === 'email' || el.type === 'email'))
      return false;
    const identityFields = Array.from(form.querySelectorAll(
      'input[autocomplete=username i],input[type=email],'
      + 'input[autocomplete=email i]')).filter(visible);
    const submit = Array.from(form.querySelectorAll(
      'button[type=submit],input[type=submit],input[type=image],button:not([type])'))
      .some(visible);
    return identityFields.length === 1 && submit;
  });
}
"""

_PASSWORD_REJECTED_JS = r"""
() => Array.from(document.querySelectorAll('input[type=password]')).some(el => {
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  const visible = r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
    st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
    !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  return visible && (el.getAttribute('aria-invalid') === 'true' ||
    (Boolean(el.value) && !el.checkValidity()));
})
"""

# Username-first (identity provider, SSO, portali a due schermate). La
# selezione resta deterministica e top-level: autocomplete/attributi semantici
# standard + contesto auth dell'action. Un normale campo newsletter `email`
# su una landing page non supera il requisito.
_LOCATE_USERNAME_STAGE_JS = r"""
() => {
  document.querySelectorAll('[data-metnos-user-step],[data-metnos-user-submit]')
    .forEach(el => {
      el.removeAttribute('data-metnos-user-step');
      el.removeAttribute('data-metnos-user-submit');
    });
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
      !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  };
  const candidates = [];
  for (const el of Array.from(document.querySelectorAll(
      'input[type=text],input[type=email],input[type=tel],input:not([type])'))) {
    if (!visible(el)) continue;
    const form = el.closest('form');
    const actionResolved = (form && form.action) ? form.action : location.href;
    const attrs = [el.name, el.id, el.getAttribute('aria-label'),
      el.getAttribute('placeholder')].filter(Boolean).join(' ').toLowerCase();
    const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
    const strong = autocomplete === 'username' ||
      /(^|[^a-z])(user(name|id)?|login|account|identifier|utente|matricola)([^a-z]|$)/i
        .test(attrs);
    const emailish = autocomplete === 'email' || el.type === 'email' ||
      /(^|[^a-z])(e-?mail)([^a-z]|$)/i.test(attrs);
    let authAction = false;
    try {
      const u = new URL(actionResolved, location.href);
      authAction = /(^|[^a-z])(auth|login|signin|sign-in|session|account|sso)([^a-z]|$)/i
        .test(`${u.hostname} ${u.pathname}`);
    } catch (_) {}
    if (!strong && !(emailish && authAction)) continue;
    const scope = form || document;
    const textFields = Array.from(scope.querySelectorAll(
      'input[type=text],input[type=email],input[type=tel],input:not([type])'))
      .filter(visible);
    let score = (strong ? 8 : 0) + (emailish ? 2 : 0) +
      (authAction ? 3 : 0) + (textFields.length === 1 ? 1 : 0);
    if (autocomplete === 'username') score += 4;
    candidates.push({el, form, actionResolved, score});
  }
  candidates.sort((a, b) => b.score - a.score);
  if (!candidates.length) return {found: false};
  if (candidates.length > 1 && candidates[0].score === candidates[1].score)
    return {found: false, ambiguous: true};
  const best = candidates[0];
  let submitEl = null;
  if (best.form) {
    submitEl = best.form.querySelector(
      'button[type=submit],input[type=submit],input[type=image],button:not([type])');
  }
  best.el.setAttribute('data-metnos-user-step', '1');
  best.el.setAttribute('data-metnos-redact', '1');
  if (submitEl) submitEl.setAttribute('data-metnos-user-submit', '1');
  return {found: true, actionResolved: best.actionResolved,
          hasSubmit: !!submitEl};
}
"""

_CURRENT_FORM_ACTION_JS = r"""
() => {
  const pw = document.querySelector('[data-metnos-pw="1"]');
  if (!pw) return '';
  const form = pw.closest('form');
  return (form && form.action) ? form.action : location.href;
}
"""

_CURRENT_USERNAME_ACTION_JS = r"""
() => {
  const user = document.querySelector('[data-metnos-user-step="1"]');
  if (!user) return '';
  const form = user.closest('form');
  return (form && form.action) ? form.action : location.href;
}
"""

_CURRENT_OTP_ACTION_JS = r"""
() => {
  const otp = document.querySelector('[data-metnos-otp="1"]');
  if (!otp) return '';
  const form = otp.closest('form');
  return (form && form.action) ? form.action : location.href;
}
"""


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


async def _human_pause(page, *, stealth_techniques=(), selector: str = "",
                       locator=None) -> None:
    """Prepara l'interazione e applica la pausa opt-in condivisa.

    Presidio ANTI-RILEVAMENTO (ritmo non-uniforme), NON stabilizzazione UI:
    SOLO in modalita' stealth PER-SESSIONE (ADR 0191 P1, layer BEHAVIOR del
    registro stealth — non piu' env globale). Default off = nessuna pausa; la
    stabilita' pagina si ottiene con attese su postcondizioni. Bounded via
    `METNOS_SITES_HUMAN_DELAY_MS`.
    """
    try:
        from playwright_sidecar import stealth as _st
        if locator is None and selector and hasattr(page, "locator"):
            locator = page.locator(selector).first
        await _st.prepare_interaction(
            page, locator, techniques=stealth_techniques)
        await _st.pause_before_interaction(
            page, techniques=stealth_techniques)
    except Exception:
        pass


async def _has_toplevel_password(page) -> bool:
    try:
        return bool(await page.evaluate(_HAS_PASSWORD_JS))
    except Exception:
        return False


async def _login_surface_state(page) -> bool | None:
    """Ritorna True/False soltanto se il DOM e' stato osservato con successo."""
    try:
        return bool(await page.evaluate(_HAS_LOGIN_SURFACE_JS))
    except Exception:
        return None


async def _wait_for_password_stage(page, op_timeout_s: float) -> bool:
    """Riosserva per un intervallo breve senza usare il modello."""
    attempts = max(1, min(25, int(op_timeout_s * 5)))
    for _ in range(attempts):
        if await _has_toplevel_password(page):
            return True
        try:
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(200)
            else:
                await asyncio.sleep(0.2)
        except Exception:
            await asyncio.sleep(0.2)
    return False


async def _wait_for_login_surface(page, op_timeout_s: float) -> str:
    """Attende una superficie login deterministica dopo una transizione UI.

    Ritorna ``password`` o ``username``; stringa vuota su timeout. Uno stato
    ambiguo non viene mai scelto e viene soltanto riosservato.
    """
    attempts = max(1, min(25, int(op_timeout_s * 5)))
    for _ in range(attempts):
        if await _has_toplevel_password(page):
            return "password"
        try:
            info = await page.evaluate(_LOCATE_USERNAME_STAGE_JS)
        except Exception:
            info = None
        if info and info.get("found") and not info.get("ambiguous"):
            return "username"
        try:
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(200)
            else:
                await asyncio.sleep(0.2)
        except Exception:
            await asyncio.sleep(0.2)
    return ""


async def _page_matches_concept(page, concept: str) -> bool:
    if _detlex is None:
        return False
    try:
        body = await page.locator("body").inner_text(timeout=1500)
        return bool(_detlex.match(concept, body[:200_000]))
    except Exception:
        return False


async def classify_login_surface(page) -> str | None:
    """Return the most specific stable authentication blocker on the page."""
    try:
        if bool(await page.evaluate(_DETECT_CAPTCHA_JS)):
            return "captcha_required"
        if bool(await page.evaluate(_DETECT_OTP_JS)):
            return "two_factor_required"
    except Exception:
        pass
    if await _page_matches_concept(page, "sites.two_factor_push_marker"):
        return "two_factor_push_required"
    return None


def _email_factor_allowed(payload: dict) -> bool:
    scopes = payload.get("scopes") if isinstance(payload, dict) else None
    return (isinstance(scopes, list)
            and factor_resolvers.EMAIL_FACTOR_SCOPE in {
                str(scope) for scope in scopes if isinstance(scope, str)})


async def _arm_email_factor(*, username: str, payload: dict,
                            factor_state: dict, budget: _LoginBudget,
                            checkpoint=None, force: bool = False) -> None:
    """Snapshot the exact mailbox immediately before an auth submit."""
    if (not _email_factor_allowed(payload)
            or not isinstance(username, str) or "@" not in username):
        return
    if factor_state.get("email_cursor") and not force:
        return
    await _checkpoint(checkpoint, "factor_prepare")
    try:
        cursor = await asyncio.wait_for(
            factor_resolvers.prepare_email_factor(
                username, allowed=True),
            timeout=budget.remaining(10.0))
    except Exception:
        cursor = None
    if cursor:
        factor_state["email_cursor"] = cursor
    # This timestamp precedes the click that can issue the factor.  It is only
    # a skew-tolerant fallback when a server did not provide a UID cursor.
    factor_state["requested_at"] = time.time()
    factor_state.pop("auto_attempted", None)


async def _resolve_email_factor_code(*, page, username: str, domain: str,
                                     payload: dict, factor_state: dict,
                                     budget: _LoginBudget, owner: str,
                                     session_id: str, checkpoint=None
                                     ) -> tuple[str | None, str]:
    if factor_state.get("auto_attempted"):
        return None, "already_attempted"
    try:
        page_text = await page.evaluate(
            "() => (document.body && document.body.innerText || '')")
    except Exception:
        return None, "page_unavailable"
    if not factor_resolvers.is_email_factor_page(str(page_text or "")):
        return None, "channel_unavailable"
    try:
        otp_info = await page.evaluate(_LOCATE_OTP_FORM_JS)
    except Exception:
        otp_info = None
    expected_length = 0
    numeric_only = False
    if isinstance(otp_info, dict) and otp_info.get("found"):
        try:
            expected_length = int(otp_info.get("expectedLength") or 0)
        except (TypeError, ValueError):
            expected_length = 0
        if not 4 <= expected_length <= 12:
            expected_length = 0
        numeric_only = bool(otp_info.get("numericOnly"))
    factor_state["auto_attempted"] = True
    factor_state["channel"] = "email"
    await _checkpoint(checkpoint, "factor_resolving")
    resolution = await factor_resolvers.resolve_email_factor(
        page_text=str(page_text or ""), address=username,
        issuer_domain=domain,
        requested_at=float(factor_state.get("requested_at") or time.time()),
        cursor=(factor_state.get("email_cursor")
                if isinstance(factor_state.get("email_cursor"), dict)
                else None),
        allowed=_email_factor_allowed(payload),
        wait_s=budget.remaining(_EMAIL_FACTOR_WAIT_S),
        expected_length=expected_length or None,
        numeric_only=numeric_only)
    diagnostics = resolution.diagnostics or {}
    sites_audit.record(
        "factor_resolution", owner=owner, session_id=session_id,
        domain=domain, channel="email", status=resolution.status,
        relevant_messages=int(diagnostics.get("relevant_messages") or 0),
        candidate_messages=int(diagnostics.get("candidate_messages") or 0),
        candidate_count=int(diagnostics.get("candidate_count") or 0),
        expected_length=int(diagnostics.get("expected_length") or 0),
        numeric_only=bool(diagnostics.get("numeric_only")),
        top_tie_count=int(diagnostics.get("top_tie_count") or 0))
    return resolution.code, resolution.status


def _changed_cookies(cookies: list[dict], before: dict) -> list[dict]:
    return [c for c in cookies if c.get("value") and before.get(
        (c.get("name"), c.get("domain"), c.get("path"))) != c.get("value")]


def _install_nav_status_listener(page) -> None:
    """Registra UNA volta un listener che memorizza sullo `page` lo status HTTP
    dell'ultima response DOCUMENTO del main-frame (fix adversarial #5). submit e
    `goto` non restituivano la Response; questo la cattura in modo bounded, letto
    da `_observe_post_submit` per alimentare `rate_limited`. Solo status osservato,
    mai inferenza dal testo."""
    if page is None or getattr(page, "_metnos_nav_listener", False):
        return

    def _on_response(resp):
        try:
            if (getattr(resp.request, "resource_type", "") == "document"
                    and resp.frame == page.main_frame):
                page._metnos_nav_status = int(resp.status)
        except Exception:
            pass

    try:
        page.on("response", _on_response)
        page._metnos_nav_listener = True
    except Exception:
        pass


_AUTH_ENTRY_SEGMENTS = frozenset({
    "login", "log-in", "signin", "sign-in", "signon", "sign-on",
    "register", "registration", "signup", "sign-up",
    "recover", "recovery", "forgot", "reset",
})


def _is_auth_entry_route(url: str) -> bool:
    """Reject route changes that remain on an authentication entry surface.

    A disappearing password field plus a route change is useful for cookieless
    appliances, but a transition from sign-in to registration/recovery is not
    evidence of an authenticated session.  Inspect only normalized URL path
    and fragment segments; no page copy, hostname, or provider special-case is
    involved.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    route = f"{parsed.path} {parsed.fragment.split('?', 1)[0]}".lower()
    segments = {
        segment for segment in re.split(r"[^a-z0-9-]+", route)
        if segment
    }
    if segments & _AUTH_ENTRY_SEGMENTS:
        return True
    compact = {segment.replace("-", "") for segment in segments}
    return bool(compact & {"login", "signin", "signon", "signup"})


async def _observe_post_submit(*, page, context, cookies_before: dict,
                               url_before: str,
                               op_timeout_s: float,
                               await_challenge_clear: bool = False) -> dict:
    """Osserva una transizione di autenticazione SPA senza inferenze premature."""
    attempts = max(1, min(25, int(op_timeout_s * 5)))
    state = {
        "cookies": [], "changed": [], "still_pw": False,
        "otp": False, "captcha": False, "push": False,
        "password_rejected": False, "navigation_confirmed": False,
        "login_surface": False, "surface_checked": False,
        "auth_entry_route": False,
        "stable_positive": False,
    }
    positive_streak = 0
    required_stable_polls = min(
        _AUTH_SUCCESS_STABLE_POLLS, max(2, attempts - 1))
    for attempt in range(attempts):
        try:
            cookies = await context.cookies()
        except Exception:
            cookies = []
        surface_state = await _login_surface_state(page)
        login_surface = surface_state is True
        surface_checked = surface_state is not None
        still_pw = login_surface
        otp = captcha = password_rejected = False
        try:
            otp = bool(await page.evaluate(_DETECT_OTP_JS))
            captcha = bool(await page.evaluate(_DETECT_CAPTCHA_JS))
            password_rejected = bool(
                await page.evaluate(_PASSWORD_REJECTED_JS))
        except Exception:
            pass
        push = await _page_matches_concept(
            page, "sites.two_factor_push_marker")
        changed = _changed_cookies(cookies, cookies_before)
        try:
            navigation_confirmed = (
                scrub_url(page.url) != scrub_url(url_before))
        except Exception:
            navigation_confirmed = False
        auth_entry_route = _is_auth_entry_route(getattr(page, "url", ""))
        state = {
            "cookies": cookies, "changed": changed, "still_pw": still_pw,
            "otp": otp, "captcha": captcha, "push": push,
            "password_rejected": password_rejected,
            "navigation_confirmed": navigation_confirmed,
            "login_surface": login_surface,
            "surface_checked": surface_checked,
            "auth_entry_route": auth_entry_route,
            "stable_positive": False,
            # Fix adversarial #5: status HTTP top-level catturato dal listener di
            # navigazione (perform_login), riletto FRESCO a ogni poll (la Response
            # puo' arrivare durante l'osservazione). Alimenta `rate_limited`.
            "http_status": getattr(page, "_metnos_nav_status", None),
        }
        positive = bool(
            surface_checked and not login_surface
            and not auth_entry_route
            and (changed or navigation_confirmed))
        positive_streak = positive_streak + 1 if positive else 0
        state["stable_positive"] = (
            positive_streak >= required_stable_polls)
        # Challenge e rifiuti sono terminali. La superficie login puo' essere
        # ancora visibile nel primo frame dopo il click: azzera la sequenza ma
        # deve essere riosservata fino al budget. Se persiste o ricompare, lo
        # stato finale resta negativo; un frame vuoto non prova il login.
        if (await_challenge_clear and (otp or push)):
            pass
        elif (otp or captcha or push or password_rejected
                or state["stable_positive"]):
            break
        if attempt + 1 >= attempts:
            break
        try:
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(200)
            else:
                await asyncio.sleep(0.2)
        except Exception:
            await asyncio.sleep(0.2)
    return state


def _authed_success(observed: dict,
                    session_cookie_names: list[str]) -> bool:
    """Segnale POSITIVO di sessione (password, TOTP e OTP esterno). True solo se
    stabile-positivo e SENZA rifiuto/sfida.

    Un cookie di sessione esplicitamente dichiarato dal vault e cambiato dopo
    il submit resta una prova forte anche quando il provider conserva una URL
    ``/signin`` (alcuni endpoint autenticano e renderizzano la pagina account
    senza cambiare route).  Su una route di ingresso auth, invece, navigazione
    e cookie generici non bastano: cosi' recovery/registration non diventano
    falsi positivi.
    """
    if (observed.get("still_pw") or observed.get("login_surface")
            or not observed.get("surface_checked")
            or observed.get("otp")
            or observed.get("captcha") or observed.get("push")
            or observed.get("password_rejected")):
        return False
    changed = list(observed.get("changed") or ())
    if session_cookie_names:
        return any(c.get("name") in session_cookie_names for c in changed)
    if (not observed.get("stable_positive")
            or observed.get("auth_entry_route")):
        return False
    auth_cookie = any(
        re.search(r"(^|_)(sess(?:ion)?|auth|login|sid|jwt)($|_)",
                  str(c.get("name") or ""), re.IGNORECASE)
        and not re.search(r"csrf|xsrf", str(c.get("name") or ""),
                          re.IGNORECASE)
        for c in changed
    )
    # Una navigazione di rotta confermata verso una superficie non-login (form
    # password sparito, nessun rifiuto/sfida — gia' esclusi sopra) e' di per se'
    # un segnale di sessione positivo (§2.8): distingue il successo dal solo
    # "campo password scomparso". I pannelli LAN/self-hosted cookieless (router,
    # NAS) tracciano la sessione lato server e riusano il cookie pre-login, quindi
    # NON producono un cookie cambiato; senza questo ramo un login riuscito su
    # SPA hash-route (`#/login` -> `#/home`) verrebbe dichiarato fallito.
    return bool(auth_cookie or observed.get("navigation_confirmed"))


# Esiti post-submit (ADR 0191 §6.2). Solo `credentials_rejected` e `rate_limited`
# alimentano il cooldown (§7). `challenge_observed` (2FA/CAPTCHA corretta) e
# `login_inconclusive` (remount/timeout) lo lasciano INVARIATO.
POST_SUBMIT_OUTCOMES = frozenset({
    "login_verified", "credentials_rejected", "rate_limited",
    "challenge_observed", "login_inconclusive",
})
COOLDOWN_OUTCOMES = frozenset({"credentials_rejected", "rate_limited"})


def post_submit_outcome(observed: dict,
                        session_cookie_names: list[str]) -> str:
    """Classifica l'esito post-submit in uno dei 5 stati disgiunti (§6.2), da
    segnali GIA' calcolati da `_observe_post_submit` (nessun detector parallelo).

    Precedenza (§6.1: STATUS server-autoritativo PRIMA del contenuto): rate-limit
    (429) > successo verificato > sfida > rifiuto > neutro. Il 429 batte il
    successo perche' una navigazione verso la pagina d'errore 429 sembrerebbe
    `stable_positive` (fix adversarial #5: senza questo, un 429 reale veniva
    mascherato da un falso `login_verified`)."""
    if observed.get("http_status") == 429 or observed.get("rate_limited"):
        return "rate_limited"
    if _authed_success(observed, session_cookie_names):
        return "login_verified"
    if observed.get("otp") or observed.get("captcha") or observed.get("push"):
        return "challenge_observed"
    if observed.get("password_rejected"):
        return "credentials_rejected"
    return "login_inconclusive"


def _post_submit_authenticated(observed: dict,
                               session_cookie_names: list[str]) -> bool:
    """Compat bool: True sse l'esito e' `login_verified`."""
    return post_submit_outcome(observed, session_cookie_names) == "login_verified"


def _fingerprint_payload(payload: dict | None) -> str | None:
    """Fingerprint (sha256[:16] della pwd) dal payload GIA' caricato — stessa
    algoritmica di `credentials.fingerprint` ma SENZA ri-caricare il vault.

    Fix regressione F#4: `credentials.fingerprint(storage_domain)` ri-carica il
    record e poteva DIVERGERE dal payload in mano (storage_domain mockato/
    risolto diversamente) → falso `credential_identity_unavailable`. Derivando
    dal payload, con una password presente il fp e' SEMPRE calcolabile."""
    if not isinstance(payload, dict):
        return None
    pwd = payload.get("password") or payload.get("pwd") or payload.get("passwd")
    if not pwd:
        form = payload.get("form_data") or {}
        pwd = form.get("password") or form.get("pwd") or form.get("passwd")
    if not pwd:
        return None
    return hashlib.sha256(str(pwd).encode("utf-8")).hexdigest()[:16]


def cooldown_block(owner: str, storage_domain: str,
                   *, payload: dict | None) -> dict | None:
    """Choke-point del cooldown (fix adversarial #1/#4). Result FAIL-CLOSED se il
    fill NON deve procedere, altrimenti None. La chiave usa il fp DERIVATO DAL
    PAYLOAD (nessun re-load, nessuna divergenza).

    - cooldown attivo → blocco con `sites_cooldown_active` + `retry_after_s`;
    - password presente + store non interrogabile → fail-closed transiente;
    - passwordless → best-effort (nessun blocco).
    """
    if not owner or not storage_domain:
        return None
    fp = _fingerprint_payload(payload)  # None se passwordless
    try:
        import sites_cooldown
        wait = sites_cooldown.retry_after_s(
            owner, storage_domain, fp or "passwordless")
    except Exception:
        if fp:  # esiste una password: fail-closed transiente (§7)
            return {"ok": True, "logged_in": False,
                    "reason_code": "sites_cooldown_active",
                    "error_class": "cooldown_active", "retry_after_s": 0}
        return None
    if wait > 0:
        return {"ok": True, "logged_in": False,
                "reason_code": "sites_cooldown_active",
                "error_class": "cooldown_active", "retry_after_s": wait}
    return None


def _apply_cooldown_outcome(owner: str, storage_domain: str, outcome: str,
                            *, payload: dict | None) -> None:
    """Login verificato → reset; `credentials_rejected`/`rate_limited` → incrementa
    (§7). fp derivato dal payload (coerente con `cooldown_block`). Best-effort."""
    if not owner or not storage_domain:
        return
    try:
        import sites_cooldown
        fp = _fingerprint_payload(payload) or "passwordless"
        if outcome == "login_verified":
            sites_cooldown.reset(owner, storage_domain, fp)
        elif outcome in sites_cooldown._COOLDOWN_REASONS:
            sites_cooldown.record_failure(owner, storage_domain, fp, outcome)
    except Exception:
        pass


def _totp_code(secret: str, *, now: float | None = None,
               digits: int = 6, period: int = 30,
               algorithm: str = "sha1") -> str:
    """RFC 6238 puro, usato solo per il totp_secret opt-in nel vault."""
    raw = str(secret or "").strip()
    if raw.lower().startswith("otpauth://"):
        try:
            raw = urllib.parse.parse_qs(
                urllib.parse.urlsplit(raw).query).get("secret", [""])[0]
        except ValueError:
            raw = ""
    raw = re.sub(r"[\s-]+", "", raw).upper()
    if not raw or digits not in (6, 7, 8) or not 15 <= int(period) <= 120:
        raise ValueError("invalid TOTP configuration")
    digest = {"sha1": hashlib.sha1, "sha256": hashlib.sha256,
              "sha512": hashlib.sha512}.get(str(algorithm).lower())
    if digest is None:
        raise ValueError("invalid TOTP algorithm")
    padding = "=" * ((8 - len(raw) % 8) % 8)
    try:
        key = base64.b32decode(raw + padding, casefold=True)
    except Exception as exc:
        raise ValueError("invalid TOTP secret") from exc
    counter = int((time.time() if now is None else now) // int(period))
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


async def _submit_otp_stage(*, page, vault_domain: str,
                            origin_ok, code: str,
                            storage_domain: str, owner: str,
                            session_id: str, op_timeout_s: float,
                            audit_field: str,
                            failure_class: str,
                            stealth_techniques=()) -> dict:
    try:
        info = await page.evaluate(_LOCATE_OTP_FORM_JS)
    except Exception:
        info = None
    if not info or not info.get("found"):
        return {"ok": False, "error_class": (
            "selector_ambiguous" if info and info.get("ambiguous")
            else "selector_missing")}
    if not origin_ok(info.get("actionResolved") or page.url):
        return {"ok": False, "error_class": "origin_mismatch"}
    code = str(code or "").strip()
    if (not 3 <= len(code) <= 32
            or any(ord(char) < 32 for char in code)):
        return {"ok": False, "error_class": failure_class}
    try:
        current_action = await page.evaluate(_CURRENT_OTP_ACTION_JS)
        if not origin_ok(current_action):
            return {"ok": False, "error_class": "origin_mismatch"}
        if info.get("segmented"):
            count = int(info.get("fieldCount") or 0)
            if count <= 0 or len(code) != count:
                return {"ok": False, "error_class": failure_class}
            fields = page.locator('[data-metnos-otp="1"]')
            for index, char in enumerate(code):
                await _human_pause(
                    page, stealth_techniques=stealth_techniques,
                    locator=fields.nth(index))
                await fields.nth(index).fill(
                    char, timeout=int(op_timeout_s * 1000))
        else:
            await _human_pause(
                page, stealth_techniques=stealth_techniques,
                selector='[data-metnos-otp="1"]')
            await page.fill('[data-metnos-otp="1"]', code,
                            timeout=int(op_timeout_s * 1000))
        try:
            fp = credentials.fingerprint(storage_domain)
        except Exception:
            fp = None
        # The factor has already touched the page at this point.  Record use
        # before submit so a navigation timeout cannot erase the audit fact.
        sites_audit.record(
            "credential_use", owner=owner, session_id=session_id,
            domain=vault_domain, fingerprint=fp, field=audit_field)
        current_action = await page.evaluate(_CURRENT_OTP_ACTION_JS)
        if not origin_ok(current_action):
            return {"ok": False, "error_class": "origin_mismatch"}
        await _human_pause(
            page, stealth_techniques=stealth_techniques,
            selector=('[data-metnos-otp-submit="1"]'
                      if info.get("hasSubmit")
                      else '[data-metnos-otp="1"]'))
        if info.get("hasSubmit"):
            await page.click('[data-metnos-otp-submit="1"]',
                             timeout=int(op_timeout_s * 1000),
                             no_wait_after=True)
        else:
            await page.press('[data-metnos-otp="1"]', "Enter",
                             timeout=int(op_timeout_s * 1000),
                             no_wait_after=True)
    except Exception:
        return {"ok": False, "error_class": failure_class}
    try:
        await page.wait_for_load_state(
            "load", timeout=int(min(op_timeout_s, _FACTOR_SUBMIT_SETTLE_S) * 1000))
    except Exception:
        pass
    return {"ok": True}


async def _advance_totp_stage(*, page, vault_domain: str,
                              origin_ok, totp_secret: str,
                              storage_domain: str, owner: str,
                              session_id: str, op_timeout_s: float,
                              digits: int = 6, period: int = 30,
                              algorithm: str = "sha1",
                              stealth_techniques=()) -> dict:
    try:
        code = _totp_code(totp_secret, digits=int(digits), period=int(period),
                          algorithm=str(algorithm))
    except Exception:
        return {"ok": False, "error_class": "totp_failed"}
    return await _submit_otp_stage(
        page=page, vault_domain=vault_domain,
        origin_ok=origin_ok, code=code,
        storage_domain=storage_domain, owner=owner,
        session_id=session_id, op_timeout_s=op_timeout_s,
        audit_field="totp", failure_class="totp_failed",
        stealth_techniques=stealth_techniques)


async def _complete_one_time_code_stage(*, page, context,
                                        vault_domain: str,
                                        origin_ok,
                                        one_time_code: str,
                                        storage_domain: str,
                                        session_cookie_names: list[str],
                                        owner: str, session_id: str,
                                        op_timeout_s: float,
                                        payload: dict | None = None,
                                        stealth_techniques=()) -> dict:
    try:
        cookies_before = {
            (c.get("name"), c.get("domain"), c.get("path")): c.get("value")
            for c in await context.cookies()
        }
    except Exception:
        cookies_before = {}
    url_before = page.url
    advanced = await _submit_otp_stage(
        page=page, vault_domain=vault_domain,
        origin_ok=origin_ok, code=one_time_code,
        storage_domain=storage_domain, owner=owner,
        session_id=session_id, op_timeout_s=op_timeout_s,
        audit_field="one_time_code", failure_class="otp_failed",
        stealth_techniques=stealth_techniques)
    if not advanced.get("ok"):
        error_class = str(advanced.get("error_class") or "otp_failed")
        reason = ("origin_unverified" if error_class == "origin_mismatch"
                  else "two_factor_required")
        return {"ok": True, "logged_in": False,
                "reason_code": reason, "error_class": error_class}
    observed = await _observe_post_submit(
        page=page, context=context, cookies_before=cookies_before,
        url_before=url_before,
        op_timeout_s=min(op_timeout_s, _FACTOR_SUBMIT_SETTLE_S),
        await_challenge_clear=True)
    outcome = post_submit_outcome(observed, session_cookie_names)
    logged_in = (outcome == "login_verified")
    reason = None
    if not logged_in:
        if observed.get("captcha"):
            reason = "captcha_required"
        elif observed.get("otp"):
            reason = "two_factor_required"
        elif observed.get("push"):
            reason = "two_factor_push_required"
        else:
            reason = "login_failed"
    sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                       domain=vault_domain, outcome=logged_in, reason=reason)
    _apply_cooldown_outcome(owner, storage_domain, outcome, payload=payload)
    return {"ok": True, "logged_in": logged_in, "reason_code": reason}


async def _advance_username_stage(*, page, vault_domain: str,
                                  origin_ok, username: str,
                                  storage_domain: str, owner: str,
                                  session_id: str,
                                  op_timeout_s: float,
                                  before_submit=None,
                                  stealth_techniques=()) -> dict:
    """Compila l'identita' e avanza UNA volta verso la password."""
    try:
        info = await page.evaluate(_LOCATE_USERNAME_STAGE_JS)
    except Exception:
        info = None
    if not info or not info.get("found"):
        return {"ok": False, "error_class": (
            "selector_ambiguous" if info and info.get("ambiguous")
            else "selector_missing")}
    action_url = info.get("actionResolved") or page.url
    if not origin_ok(action_url):
        sites_audit.record("origin_mismatch", owner=owner,
                           session_id=session_id, domain=vault_domain,
                           form_host=_host_of(action_url),
                           phase="username_stage")
        return {"ok": False, "error_class": "origin_mismatch"}
    try:
        current_action = await page.evaluate(_CURRENT_USERNAME_ACTION_JS)
        if not origin_ok(current_action):
            sites_audit.record("origin_mismatch", owner=owner,
                               session_id=session_id, domain=vault_domain,
                               form_host=_host_of(current_action),
                               phase="username_pre_fill")
            return {"ok": False, "error_class": "origin_mismatch"}
        await _human_pause(
            page, stealth_techniques=stealth_techniques,
            selector='[data-metnos-user-step="1"]')
        await page.fill('[data-metnos-user-step="1"]', username,
                        timeout=int(op_timeout_s * 1000))
        await _human_pause(
            page, stealth_techniques=stealth_techniques,
            selector=('[data-metnos-user-submit="1"]' if info.get("hasSubmit")
                      else '[data-metnos-user-step="1"]'))
        try:
            fp = credentials.fingerprint(storage_domain)
        except Exception:
            fp = None
        sites_audit.record(
            "credential_use", owner=owner, session_id=session_id,
            domain=vault_domain, fingerprint=fp, field="username")
        current_action = await page.evaluate(_CURRENT_USERNAME_ACTION_JS)
        if not origin_ok(current_action):
            sites_audit.record("origin_mismatch", owner=owner,
                               session_id=session_id, domain=vault_domain,
                               form_host=_host_of(current_action),
                               phase="username_pre_submit")
            return {"ok": False, "error_class": "origin_mismatch"}
        if before_submit is not None:
            prepared = before_submit()
            if inspect.isawaitable(prepared):
                await prepared
        if info.get("hasSubmit"):
            await page.click('[data-metnos-user-submit="1"]',
                             timeout=int(op_timeout_s * 1000),
                             no_wait_after=True)
        else:
            await page.press('[data-metnos-user-step="1"]', "Enter",
                             timeout=int(op_timeout_s * 1000),
                             no_wait_after=True)
    except Exception:
        return {"ok": False, "error_class": "fill_failed"}
    try:
        await page.wait_for_load_state(
            "load", timeout=int(min(op_timeout_s, 5) * 1000))
    except Exception:
        pass
    return {"ok": True,
            "password_visible": await _wait_for_password_stage(
                page, min(op_timeout_s, 4))}


def _load_site_credentials(domain: str) -> tuple[dict | None, str]:
    """Carica il record esatto o il prefisso legacy ``web_<domain>``.

    ADR 0191 P2: NESSUN fold ``www.host -> host`` qui. La risoluzione del
    candidate legacy (``www.D -> D``) avviene a monte in ``op_open`` via
    ``legacy_storage_candidate``; l'autorizzazione al fill resta vincolata a
    ``credential_origins``, mai a un'equivalenza calcolata al load.
    """
    domain = str(domain or "").strip().rstrip(".").lower()
    for binding in (domain, f"web_{domain}"):
        payload = credentials.load(binding)
        if payload:
            return payload, binding
    return None, domain


def legacy_storage_candidate(host: str) -> str:
    """Candidate discovery per ``op_open`` (ADR 0191 P2): se non esiste un record
    sotto ``host`` ma esiste sotto la radice (``host == www.<root>``), ritorna la
    radice. SOLO per TROVARE il record legacy; NON autorizza il fill (vincolato a
    ``credential_origins``)."""
    h = str(host or "").strip().rstrip(".").lower()
    if not h:
        return h
    try:
        known = set(credentials.list_domains())
    except Exception:
        return h
    if h in known or f"web_{h}" in known:
        return h
    if h.startswith("www.") and h[4:].count(".") >= 1:
        root = h[4:]
        if root in known or f"web_{root}" in known:
            return root
    return h


def _credential_form_data(payload: dict) -> dict:
    """Normalizza lo schema ADR 0082 e quello piatto di ``metnos-cli``."""
    form_data = dict(payload.get("form_data") or {})
    for key in ("username", "user", "email", "password", "passwd"):
        if not form_data.get(key) and payload.get(key):
            form_data[key] = payload[key]
    return form_data


async def fill_credential_ref(*, page, expected_domain: str, value_ref: str,
                              owner: str, session_id: str,
                              op_timeout_s: float,
                              stealth_techniques=()) -> dict:
    """Risolve e riempie ``cred:<domain>:<field>`` senza esporre il valore.

    Il campo e' scelto esclusivamente dai tag broker-owned prodotti da
    ``_LOCATE_LOGIN_FORM_JS``. Nessun selettore o valore torna al chiamante.
    """
    parts = (value_ref or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "cred":
        return {"ok": False, "error_class": "invalid_value_ref"}
    domain, field = parts[1].lower(), parts[2].lower()
    if domain != (expected_domain or "").lower():
        return {"ok": False, "error_class": "origin_mismatch"}
    if field not in ("username", "user", "email", "password", "passwd"):
        return {"ok": False, "error_class": "invalid_value_ref"}
    try:
        payload, storage_domain = _load_site_credentials(domain)
        payload = payload or {}
    except Exception:
        return {"ok": False, "error_class": "vault_error"}
    form_data = _credential_form_data(payload)
    aliases = (("username", "user", "email") if field in
               ("username", "user", "email") else ("password", "passwd"))
    value = next((form_data.get(k) for k in aliases if form_data.get(k)), None)
    if not value:
        return {"ok": False, "error_class": "no_credentials"}
    # Fix adversarial #1: il cooldown vive al choke-point di OGNI esposizione
    # credenziale, non solo in login_sites. `act_sites`/`fill_credential_ref` NON
    # deve poter riesporre la credenziale durante un cooldown attivo (fail-closed).
    _blk = cooldown_block(owner, storage_domain, payload=payload)
    if _blk is not None:
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False,
                           reason=_blk.get("reason_code"))
        return {"ok": False, "error_class": _blk["error_class"],
                "reason_code": _blk["reason_code"],
                **({"retry_after_s": _blk["retry_after_s"]}
                   if "retry_after_s" in _blk else {})}
    info = await page.evaluate(_LOCATE_LOGIN_FORM_JS)
    if not info or not info.get("found"):
        return {"ok": False, "error_class": "selector_missing"}
    current_action = await page.evaluate(_CURRENT_FORM_ACTION_JS)
    if not sites_origin.origin_authorized(
            sites_origin.origin_of_url(current_action), payload, storage_domain):
        sites_audit.record("origin_mismatch", owner=owner,
                           session_id=session_id, domain=domain,
                           form_host=_host_of(current_action), phase="action_fill")
        return {"ok": False, "error_class": "origin_mismatch"}
    selector = ('[data-metnos-user="1"]' if field in
                ("username", "user", "email") else '[data-metnos-pw="1"]')
    if field in ("username", "user", "email") and not info.get("hasUser"):
        return {"ok": False, "error_class": "selector_missing"}
    try:
        await _human_pause(
            page, stealth_techniques=stealth_techniques, selector=selector)
        await page.fill(selector, value, timeout=int(op_timeout_s * 1000))
    except Exception:
        return {"ok": False, "error_class": "fill_failed"}
    try:
        fp = credentials.fingerprint(storage_domain)
    except Exception:
        fp = None
    sites_audit.record("credential_use", owner=owner, session_id=session_id,
                       domain=domain, fingerprint=fp, field=field)
    return {"ok": True, "filled": True}


async def perform_login(*, page, context, domain: str, form_hint: str | None,
                        owner: str, session_id: str, op_timeout_s: float,
                        one_time_code: str | None = None,
                        reach_login=None, authorize_origin=None,
                        approved_origin: str | None = None,
                        max_entry_steps: int = 3,
                        page_provider=None, factor_state: dict | None = None,
                        checkpoint=None,
                        total_timeout_s: float | None = None,
                        stealth_techniques=()) -> dict:
    """Esegue il login nel session-context. Ritorna
    `{ok, logged_in: bool, reason_code: str|None, error_class?: str}`.
    ZERO segreti nel return (reason_code = slug i18n, mai username/password).
    """
    # 1. Carica la credenziale DENTRO il broker. Mai fuori da qui.
    try:
        payload, storage_domain = _load_site_credentials(domain)
    except Exception as e:  # decrypt/malformed → onesto, nessun leak
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False, reason="vault_error")
        return {"ok": True, "logged_in": False, "reason_code": "vault_error",
                "error_class": "vault_error", "_detail": type(e).__name__}
    if not payload:
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False, reason="credentials_missing")
        return {"ok": True, "logged_in": False,
                "reason_code": "credentials_missing", "error_class": "no_credentials"}

    login_url = payload.get("login_url")
    form_data = _credential_form_data(payload)
    username = (form_data.get("username") or form_data.get("user")
                or form_data.get("email") or "")
    password = (form_data.get("password") or form_data.get("passwd") or "")
    totp_secret = (form_data.get("totp_secret")
                   or payload.get("totp_secret") or "")
    totp_digits = form_data.get("totp_digits") or payload.get("totp_digits") or 6
    totp_period = form_data.get("totp_period") or payload.get("totp_period") or 30
    totp_algorithm = (form_data.get("totp_algorithm")
                      or payload.get("totp_algorithm") or "sha1")
    session_cookie_names = list(payload.get("session_cookie_names") or [])
    # ADR 0191 P2 (rev. 14/7): autorita' del fill = `sites_origin.origin_authorized`.
    # `credential_origins` esplicite = match esatto fail-closed (#3); chiave
    # assente = STESSO SITO del domain handle (sottodomini first-party inclusi:
    # il login `account.booking.com` per `booking.com` NON chiede consenso —
    # contratto storico, turn 025c53fa). One-shot IdP (#2) a MATCH ESATTO
    # (scheme,host,port), mai persistito.
    approved_origins = set()
    if approved_origin:
        _ao = sites_origin.normalize_entry(str(approved_origin))
        if _ao:
            approved_origins.add(_ao)

    def _origin_ok(action_url) -> bool:
        origin = sites_origin.origin_of_url(action_url)
        return bool(origin and (origin in approved_origins
                                or sites_origin.origin_authorized(
                                    origin, payload, storage_domain)))

    budget = _LoginBudget(
        total_timeout_s if total_timeout_s is not None
        else max(op_timeout_s * 5, 60.0))
    factor_state = factor_state if isinstance(factor_state, dict) else {}
    if not username and not password:
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False,
                           reason="credentials_missing")
        return {"ok": True, "logged_in": False,
                "reason_code": "credentials_missing",
                "error_class": "no_credentials"}

    # ADR 0191 P6 + fix #1/#4: choke-point anti-lockout fail-closed. Se in
    # cooldown, o identita'/store non disponibili con password esistente, NON
    # esporre la credenziale.
    _blk = cooldown_block(owner, storage_domain, payload=payload)
    if _blk is not None:
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False,
                           reason=_blk.get("reason_code"))
        return _blk

    def current_page(previous):
        """Follow only pages already adopted by the broker's guarded context."""
        if page_provider is None:
            return previous
        try:
            candidate = page_provider()
        except Exception:
            return previous
        return candidate if candidate is not None else previous

    page = current_page(page)
    # Fix adversarial #5: cattura lo status HTTP del documento top-level (submit
    # e goto NON restituivano la Response). Listener bounded: aggiorna un attributo
    # sulla pagina, riletto da `_observe_post_submit` → alimenta `rate_limited`.
    _install_nav_status_listener(page)
    if one_time_code is not None:
        await _checkpoint(checkpoint, "factor_submit")
        completed = await _complete_one_time_code_stage(
            page=page, context=context, vault_domain=domain,
            origin_ok=_origin_ok,
            one_time_code=one_time_code,
            storage_domain=storage_domain,
            session_cookie_names=session_cookie_names,
            owner=owner, session_id=session_id,
            op_timeout_s=budget.remaining(op_timeout_s), payload=payload,
            stealth_techniques=stealth_techniques)
        await _checkpoint(
            checkpoint,
            "complete" if completed.get("logged_in") else "factor_pending")
        return completed

    async def _handle_email_factor() -> dict:
        await _checkpoint(checkpoint, "factor_pending")
        email_code, _status = await _resolve_email_factor_code(
            page=page, username=username, domain=domain, payload=payload,
            factor_state=factor_state, budget=budget, owner=owner,
            session_id=session_id, checkpoint=checkpoint)
        if email_code:
            await _checkpoint(checkpoint, "factor_submit")
            completed = await _complete_one_time_code_stage(
                page=page, context=context, vault_domain=domain,
                origin_ok=_origin_ok,
                one_time_code=email_code,
                storage_domain=storage_domain,
                session_cookie_names=session_cookie_names,
                owner=owner, session_id=session_id,
                op_timeout_s=budget.remaining(op_timeout_s), payload=payload,
                stealth_techniques=stealth_techniques)
            if completed.get("logged_in"):
                await _checkpoint(checkpoint, "complete")
                return completed
            # _complete_one_time_code_stage records the rejected attempt.
            await _checkpoint(checkpoint, "factor_pending")
            return completed
        sites_audit.record(
            "login_attempt", owner=owner, session_id=session_id,
            domain=domain, outcome=False, reason="two_factor_required")
        await _checkpoint(checkpoint, "factor_pending")
        return {"ok": True, "logged_in": False,
                "reason_code": "two_factor_required"}

    # A resumed executor can already be on the factor page.  Recognize that
    # checkpoint before attempting to rediscover or click the login entry.
    initial_blocker = await classify_login_surface(page)
    if initial_blocker == "two_factor_required" and not totp_secret:
        return await _handle_email_factor()
    if initial_blocker:
        await _checkpoint(checkpoint, "factor_pending")
        return {"ok": True, "logged_in": False,
                "reason_code": initial_blocker}

    # 2. Macchina a stati bounded, invisibile al planner:
    #    landing -> ingresso login -> [username ->] password. Il modello puo'
    #    essere usato soltanto dentro `reach_login`, prima di qualunque fill;
    #    campi e origine restano risolti deterministicamente qui.
    login_url_attempted = False
    username_advanced = False
    privacy_attempted = False
    continue_attempted = False
    entry_steps = 0
    await _checkpoint(checkpoint, "discovering")
    password_visible = await _has_toplevel_password(page)
    # Un SPA hash-route puo' completare il `load` su `/` e instradare a
    # `#/login` rendendo il form solo dopo. Se la superficie di login non e'
    # ancora presente, attendila bounded prima di dichiararla assente: il
    # gate ritorna appena trova password/username e non aggiunge latenza a un
    # form gia' pronto. Copre sia il landing iniziale sia una transizione UI
    # esplicita gia' avvenuta.
    if not password_visible:
        await _wait_for_login_surface(
            page, budget.remaining(_LOGIN_SURFACE_SETTLE_S))
        password_visible = await _has_toplevel_password(page)
    for _ in range(max(1, int(max_entry_steps)) + 3):
        if budget.expired:
            return {"ok": True, "logged_in": False,
                    "reason_code": "login_timeout",
                    "error_class": "timeout"}
        page = current_page(page)
        if password_visible:
            break
        if login_url and not login_url_attempted:
            login_url_attempted = True
            try:
                goto_timeout = budget.remaining(op_timeout_s)
                await asyncio.wait_for(
                    page.goto(login_url, wait_until="load",
                              timeout=int(goto_timeout * 1000)),
                    timeout=goto_timeout)
            except Exception:
                pass
            password_visible = await _has_toplevel_password(page)
            if password_visible:
                break

        username_info = None
        if username and not username_advanced:
            try:
                username_info = await page.evaluate(
                    _LOCATE_USERNAME_STAGE_JS)
            except Exception:
                username_info = None
        if username_info and username_info.get("ambiguous"):
            # Dopo una navigazione SPA il form puo' attraversare un DOM
            # transitorio con piu' candidati prima di rendere visibile il
            # campo password definitivo. Non scegliere fra candidati: attendi
            # bounded un segnale non ambiguo e deterministico.
            password_visible = await _wait_for_password_stage(
                page, budget.remaining(_LOGIN_SURFACE_SETTLE_S))
            if password_visible:
                break
            return {"ok": True, "logged_in": False,
                    "reason_code": "selector_missing",
                    "error_class": "selector_ambiguous"}
        if username_info and username_info.get("found"):
            privacy_attempted = True
            username_action = (
                username_info.get("actionResolved") or page.url)
            username_origin = _host_of(username_action)
            _uao = sites_origin.origin_of_url(username_action)
            if not _origin_ok(username_action):
                if authorize_origin is not None and not username_advanced:
                    decision = await authorize_origin(
                        _uao or username_origin, "username")
                    if decision.get("approval_required"):
                        out = dict(decision)
                        out.update({"ok": True, "logged_in": False})
                        return out
                    if decision.get("approved") and _uao:
                        approved_origins.add(_uao)
                if not _origin_ok(username_action):
                    sites_audit.record(
                        "origin_mismatch", owner=owner,
                        session_id=session_id, domain=domain,
                        form_host=username_origin,
                        phase="username_authorization")
                    return {"ok": True, "logged_in": False,
                            "reason_code": "origin_unverified",
                            "error_class": "origin_mismatch"}
            async def _before_username_submit():
                await _checkpoint(checkpoint, "username_submit")
                await _arm_email_factor(
                    username=username, payload=payload,
                    factor_state=factor_state, budget=budget,
                    checkpoint=checkpoint, force=True)

            advanced = await _advance_username_stage(
                page=page, vault_domain=domain,
                origin_ok=_origin_ok, username=username,
                storage_domain=storage_domain, owner=owner,
                session_id=session_id,
                op_timeout_s=budget.remaining(op_timeout_s),
                before_submit=_before_username_submit,
                stealth_techniques=stealth_techniques)
            if not advanced.get("ok"):
                error_class = advanced.get("error_class")
                mismatch = error_class == "origin_mismatch"
                return {"ok": True, "logged_in": False,
                        "reason_code": (
                            "origin_unverified" if mismatch else
                            "mandate_scope_exceeded"
                            if error_class == "mandate_scope_exceeded" else
                            "selector_missing"),
                        "error_class": error_class}
            username_advanced = True
            password_visible = bool(advanced.get("password_visible"))
            if password_visible:
                break
            # Dopo aver esposto l'identita' non si invoca piu' alcun modello e
            # non si tenta un click fuzzy: fail-closed sul nuovo stato.
            try:
                if await page.evaluate(_DETECT_CAPTCHA_JS):
                    await _checkpoint(checkpoint, "factor_pending")
                    return {"ok": True, "logged_in": False,
                            "reason_code": "captcha_required"}
                if await page.evaluate(_DETECT_OTP_JS):
                    if not totp_secret:
                        return await _handle_email_factor()
                    await _checkpoint(checkpoint, "factor_pending")
                    return {"ok": True, "logged_in": False,
                            "reason_code": "two_factor_required"}
            except Exception:
                pass
            if reach_login is not None and not continue_attempted:
                continue_attempted = True
                continued = await reach_login("continue")
                if continued.get("approval_required"):
                    out = dict(continued)
                    out.update({"ok": True, "logged_in": False})
                    return out
                if continued.get("error_class") == "mandate_scope_exceeded":
                    return {"ok": True, "logged_in": False,
                            "reason_code": "mandate_scope_exceeded",
                            "error_class": "mandate_scope_exceeded"}
                if continued.get("ok") and continued.get("executed"):
                    entry_steps += 1
                    page = current_page(page)
                    password_visible = await _wait_for_password_stage(
                        page, budget.remaining(4))
                    if password_visible:
                        break
            if await _page_matches_concept(
                    page, "sites.two_factor_push_marker"):
                await _checkpoint(checkpoint, "factor_pending")
                return {"ok": True, "logged_in": False,
                        "reason_code": "two_factor_push_required"}
            return {"ok": True, "logged_in": False,
                    "reason_code": "selector_missing",
                    "error_class": "no_password_stage"}

        if reach_login is None or entry_steps >= max(1, int(max_entry_steps)):
            break
        if not privacy_attempted:
            privacy_attempted = True
            dismissed = await reach_login("privacy_reject")
            if dismissed.get("approval_required"):
                out = dict(dismissed)
                out.update({"ok": True, "logged_in": False})
                return out
            if dismissed.get("error_class") == "mandate_scope_exceeded":
                return {"ok": True, "logged_in": False,
                        "reason_code": "mandate_scope_exceeded",
                        "error_class": "mandate_scope_exceeded"}
            if dismissed.get("ok") and dismissed.get("executed"):
                entry_steps += 1
                page = current_page(page)
                password_visible = await _has_toplevel_password(page)
                continue
        reached = await reach_login("login")
        entry_steps += 1
        if reached.get("approval_required"):
            out = dict(reached)
            out.update({"ok": True, "logged_in": False})
            return out
        if not reached.get("ok") or not reached.get("executed"):
            error_class = reached.get("error_class") or "no_login_form"
            return {"ok": True, "logged_in": False,
                    "reason_code": (
                        "mandate_scope_exceeded"
                        if error_class == "mandate_scope_exceeded"
                        else "selector_missing"),
                    "error_class": error_class}
        page = current_page(page)
        await _wait_for_login_surface(
            page, budget.remaining(_LOGIN_SURFACE_SETTLE_S))
        password_visible = await _has_toplevel_password(page)

    if not password_visible:
        return {"ok": True, "logged_in": False,
                "reason_code": "selector_missing",
                "error_class": "no_login_form"}
    if not password:
        sites_audit.record("login_attempt", owner=owner,
                           session_id=session_id, domain=domain,
                           outcome=False, reason="credentials_missing")
        return {"ok": True, "logged_in": False,
                "reason_code": "credentials_missing",
                "error_class": "no_credentials"}

    # 3. CRITICO-1 — verifica ORIGINE prima di digitare. Il JS tagga anche i
    #    campi (CRITICO-2: risoluzione autonoma del broker, mai selettori LLM).
    info = await page.evaluate(_LOCATE_LOGIN_FORM_JS)
    if not info or not info.get("found"):
        return {"ok": True, "logged_in": False, "reason_code": "selector_missing",
                "error_class": "no_login_form"}
    form_action = info.get("actionResolved") or page.url
    form_host = _host_of(form_action)
    _fao = sites_origin.origin_of_url(form_action)
    # ADR 0191 P2: match ESATTO (scheme,host,port) contro credential_origins;
    # nessun fold `www` implicito (www autorizzato solo se entry esplicita o
    # migrazione). Il consenso one-shot (ADR 0188) approva l'ORIGINE ESATTA per
    # QUESTO login, mai persistita. Nessun iframe (JS solo main frame).
    if not _origin_ok(form_action):
        if authorize_origin is not None:
            decision = await authorize_origin(_fao or form_host, "password")
            if decision.get("approval_required"):
                out = dict(decision)
                out.update({"ok": True, "logged_in": False})
                return out
            if decision.get("approved") and _fao:
                approved_origins.add(_fao)
        if not _origin_ok(form_action):
            sites_audit.record("origin_mismatch", owner=owner,
                               session_id=session_id, domain=domain,
                               form_host=form_host)
            return {"ok": True, "logged_in": False,
                    "reason_code": "origin_unverified",
                    "error_class": "origin_mismatch"}

    # Snapshot pre-login: senza un segnale positivo di sessione non dichiariamo
    # mai il successo solo perche' il form password e' sparito (§2.8).
    try:
        cookies_before = {
            (c.get("name"), c.get("domain"), c.get("path")): c.get("value")
            for c in await context.cookies()
        }
    except Exception:
        cookies_before = {}
    url_before = page.url

    # 4. CRITICO-2/3 — digita nei SOLI campi risolti dal broker. Il pw field è
    #    già marcato `data-metnos-redact`. Nessuno screenshot fra fill e submit.
    try:
        action_timeout = budget.remaining(op_timeout_s)
        # TOCTOU: una pagina puo' cambiare form.action dopo il primo controllo.
        # Riverifica immediatamente prima di esporre qualunque credenziale.
        current_action = await page.evaluate(_CURRENT_FORM_ACTION_JS)
        if not _origin_ok(current_action):
            sites_audit.record("origin_mismatch", owner=owner,
                               session_id=session_id, domain=domain,
                               form_host=_host_of(current_action), phase="pre_fill")
            return {"ok": True, "logged_in": False,
                    "reason_code": "origin_unverified",
                    "error_class": "origin_mismatch"}
        if username and info.get("hasUser"):
            await _human_pause(
                page, stealth_techniques=stealth_techniques,
                selector='[data-metnos-user="1"]')
            await page.fill('[data-metnos-user="1"]', username,
                            timeout=int(action_timeout * 1000))
        await _human_pause(
            page, stealth_techniques=stealth_techniques,
            selector='[data-metnos-pw="1"]')
        await page.fill('[data-metnos-pw="1"]', password,
                        timeout=int(action_timeout * 1000))
        await _human_pause(
            page, stealth_techniques=stealth_techniques,
            selector=('[data-metnos-submit="1"]' if info.get("hasSubmit")
                      else '[data-metnos-pw="1"]'))
        try:
            fp = credentials.fingerprint(storage_domain)
        except Exception:
            fp = None
        if username and info.get("hasUser"):
            sites_audit.record(
                "credential_use", owner=owner, session_id=session_id,
                domain=domain, fingerprint=fp, field="username")
        sites_audit.record(
            "credential_use", owner=owner, session_id=session_id,
            domain=domain, fingerprint=fp, field="password")
    except Exception:
        return {"ok": True, "logged_in": False, "reason_code": "selector_missing",
                "error_class": "fill_failed"}

    # 5-6. Submit deterministico + attesa navigazione/idle (bounded).
    try:
        await _checkpoint(checkpoint, "primary_submit")
        current_action = await page.evaluate(_CURRENT_FORM_ACTION_JS)
        if not _origin_ok(current_action):
            sites_audit.record("origin_mismatch", owner=owner,
                               session_id=session_id, domain=domain,
                               form_host=_host_of(current_action), phase="pre_submit")
            return {"ok": True, "logged_in": False,
                    "reason_code": "origin_unverified",
                    "error_class": "origin_mismatch"}
        await _arm_email_factor(
            username=username, payload=payload, factor_state=factor_state,
            budget=budget, checkpoint=checkpoint, force=True)
        action_timeout = budget.remaining(op_timeout_s)
        await _human_pause(
            page, stealth_techniques=stealth_techniques,
            selector=('[data-metnos-submit="1"]' if info.get("hasSubmit")
                      else '[data-metnos-pw="1"]'))
        if info.get("hasSubmit"):
            await page.click('[data-metnos-submit="1"]',
                             timeout=int(action_timeout * 1000),
                             no_wait_after=True)
        else:
            await page.press('[data-metnos-pw="1"]', "Enter",
                             timeout=int(action_timeout * 1000),
                             no_wait_after=True)
    except Exception:
        pass  # il submit può innescare navigazione che chiude il contesto DOM
    try:
        load_timeout = budget.remaining(op_timeout_s)
        await asyncio.wait_for(
            page.wait_for_load_state(
                "load", timeout=int(load_timeout * 1000)),
            timeout=load_timeout)
    except Exception:
        pass

    # 7. Rilevazioni oneste. Il click di una SPA puo' completare molto dopo il
    # ritorno di `wait_for_load_state`; si osservano solo segnali deterministici
    # per un intervallo bounded prima di classificare l'esito.
    observed = await _observe_post_submit(
        page=page, context=context, cookies_before=cookies_before,
        url_before=url_before,
        op_timeout_s=budget.remaining(_LOGIN_SURFACE_SETTLE_S))
    otp = bool(observed["otp"])
    captcha = bool(observed["captcha"])
    push = bool(observed["push"])
    forced_reason = None
    if otp and totp_secret and not captcha:
        await _checkpoint(checkpoint, "factor_submit")
        advanced = await _advance_totp_stage(
            page=page, vault_domain=domain,
            origin_ok=_origin_ok, totp_secret=str(totp_secret),
            storage_domain=storage_domain, owner=owner,
            session_id=session_id,
            op_timeout_s=budget.remaining(op_timeout_s),
            digits=totp_digits, period=totp_period,
            algorithm=str(totp_algorithm),
            stealth_techniques=stealth_techniques)
        if advanced.get("ok"):
            observed = await _observe_post_submit(
                page=page, context=context, cookies_before=cookies_before,
                url_before=url_before,
                op_timeout_s=budget.remaining(_LOGIN_SURFACE_SETTLE_S))
            otp = bool(observed["otp"])
            captcha = bool(observed["captcha"])
            push = bool(observed["push"])
        elif advanced.get("error_class") == "origin_mismatch":
            forced_reason = "origin_unverified"

    # Email is the first channel resolved automatically.  It is attempted
    # only when the page explicitly identifies email as the factor channel and
    # the login identity is an email address; otherwise the manual OTP dialog
    # remains the deterministic fallback.
    if otp and not captcha and not totp_secret:
        return await _handle_email_factor()

    # 8. Verifica ESITO onesta (§2.8): cookie di sessione dichiarati presenti,
    #    OPPURE il campo password è sparito (e non c'è OTP/errore residuo).
    password_rejected = bool(observed["password_rejected"])
    # Un cookie gia' presente sulla pagina di login non prova
    # l'autenticazione: il segnale deve essere nuovo o ruotato dal submit.
    outcome = post_submit_outcome(observed, session_cookie_names)
    logged_in = (outcome == "login_verified")

    reason = None
    if not logged_in:
        if forced_reason:
            reason = forced_reason
        elif captcha:
            reason = "captcha_required"
        elif otp:
            reason = "two_factor_required"
        elif push:
            reason = "two_factor_push_required"
        elif password_rejected:
            reason = "password_wrong"
        else:
            reason = "login_failed"

    # 9. Audit dell'esito; ogni campo usato e' gia' registrato subito dopo il
    # fill, prima che una navigazione possa interrompere il controllo.
    sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                       domain=domain, outcome=logged_in, reason=reason)
    await _checkpoint(
        checkpoint,
        "complete" if logged_in else
        "factor_pending" if reason in {
            "two_factor_required", "two_factor_push_required",
            "captcha_required"} else "failed")

    _apply_cooldown_outcome(owner, storage_domain, outcome, payload=payload)
    return {"ok": True, "logged_in": logged_in, "reason_code": reason}
