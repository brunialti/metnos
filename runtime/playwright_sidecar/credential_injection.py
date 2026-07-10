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
  Mismatch → RIFIUTO (`origine_non_verificata`), nessuna digitazione.

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
import urllib.parse

import credentials  # runtime/credentials.py — vault cifrato (dentro il broker)
import sites_audit


# JS (main frame): individua il form di login, TAGGA i campi credenziale con
# attributi broker-owned e ritorna l'origine di submit per la verifica §3.2.
# Cerca SOLO in `document` (main frame): `document.forms`/`querySelectorAll` NON
# discendono negli iframe → un campo password in un iframe NON viene trovato
# (CRITICO-1 punto 2: mai iframe).
_LOCATE_LOGIN_FORM_JS = r"""
() => {
  const forms = Array.from(document.forms);
  let form = null, pw = null;
  for (const f of forms) {
    const p = f.querySelector('input[type=password]');
    if (p) { form = f; pw = p; break; }
  }
  if (!pw) {
    const p = document.querySelector('input[type=password]');
    if (p) { pw = p; form = p.closest('form'); }
  }
  if (!pw) return {found: false};
  // Origine di submit: form.action riflette già l'URL ASSOLUTO risolto; se non
  // c'è form, l'origine è quella della pagina (submit-to-self).
  const actionResolved = (form && form.action) ? form.action : location.href;
  const scope = form || document;
  // username field: match esplicito per name/id, poi fallback all'input
  // testuale che PRECEDE la password in DOM order.
  const RE = /user|email|login|userid|account|utente|matricola|nome/i;
  let userEl = null;
  const cand = Array.from(scope.querySelectorAll(
    'input[type=text], input[type=email], input[type=tel], input:not([type])'));
  for (const c of cand) {
    if (RE.test(c.name || '') || RE.test(c.id || '')) { userEl = c; break; }
  }
  if (!userEl) {
    const all = Array.from(scope.querySelectorAll('input'));
    const pwIdx = all.indexOf(pw);
    for (let i = pwIdx - 1; i >= 0; i--) {
      const t = (all[i].type || 'text').toLowerCase();
      if (t === 'text' || t === 'email' || t === 'tel' || !all[i].type) {
        userEl = all[i]; break;
      }
    }
  }
  let submitEl = null;
  if (form) {
    submitEl = form.querySelector(
      'button[type=submit], input[type=submit], input[type=image], button:not([type])');
  }
  // Tag deterministici broker-owned. Redaction del pw field SUBITO (prima di
  // qualunque digitazione o capture — CRITICO-3).
  pw.setAttribute('data-metnos-pw', '1');
  pw.setAttribute('data-metnos-redact', '1');
  if (userEl) userEl.setAttribute('data-metnos-user', '1');
  if (submitEl) submitEl.setAttribute('data-metnos-submit', '1');
  return {found: true, actionResolved, hasUser: !!userEl, hasSubmit: !!submitEl,
          inForm: !!form};
}
"""

# JS: rileva un campo OTP/2FA nel main frame (D-E: F1 non lo auto-risolve).
_DETECT_OTP_JS = r"""
() => {
  const RE = /otp|2fa|totp|one[-_ ]?time|verification|verify|codice|token|auth[-_ ]?code/i;
  const inputs = Array.from(document.querySelectorAll('input'));
  for (const el of inputs) {
    if ((el.autocomplete || '') === 'one-time-code') return true;
    if (el.type === 'password') continue;  // la password è gestita a parte
    if (RE.test(el.name || '') || RE.test(el.id || '') ||
        RE.test(el.getAttribute('aria-label') || '') ||
        RE.test(el.placeholder || '')) return true;
  }
  return false;
}
"""

# JS: rileva un CAPTCHA (recaptcha/hcaptcha/turnstile) nel main frame.
_DETECT_CAPTCHA_JS = r"""
() => {
  if (document.querySelector(
      '.g-recaptcha, #g-recaptcha, .h-captcha, [data-sitekey], .cf-turnstile')) return true;
  const ifr = Array.from(document.querySelectorAll('iframe'));
  for (const f of ifr) {
    const s = (f.src || '').toLowerCase();
    if (s.includes('recaptcha') || s.includes('hcaptcha') ||
        s.includes('turnstile')) return true;
  }
  return false;
}
"""

# JS: la pagina ha ancora un campo password nel main frame? (verifica esito)
_HAS_PASSWORD_JS = "() => !!document.querySelector('input[type=password]')"


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


async def _has_toplevel_password(page) -> bool:
    try:
        return bool(await page.evaluate(_HAS_PASSWORD_JS))
    except Exception:
        return False


async def perform_login(*, page, context, domain: str, form_hint: str | None,
                        owner: str, session_id: str, op_timeout_s: float) -> dict:
    """Esegue il login nel session-context. Ritorna
    `{ok, logged_in: bool, reason_code: str|None, error_class?: str}`.
    ZERO segreti nel return (reason_code = slug i18n, mai username/password).
    """
    # 1. Carica la credenziale DENTRO il broker. Mai fuori da qui.
    try:
        payload = credentials.load(domain)
    except Exception as e:  # decrypt/malformed → onesto, nessun leak
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False, reason="vault_error")
        return {"ok": True, "logged_in": False, "reason_code": "vault_errore",
                "error_class": "vault_error", "_detail": type(e).__name__}
    if not payload:
        sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                           domain=domain, outcome=False, reason="credenziali_assenti")
        return {"ok": True, "logged_in": False,
                "reason_code": "credenziali_assenti", "error_class": "no_credentials"}

    login_url = payload.get("login_url")
    form_data = dict(payload.get("form_data") or {})
    username = (form_data.get("username") or form_data.get("user")
                or form_data.get("email") or "")
    password = (form_data.get("password") or form_data.get("passwd") or "")
    session_cookie_names = list(payload.get("session_cookie_names") or [])

    # 2. Portati su un form di login. Se la pagina corrente non mostra un campo
    #    password top-level, naviga a login_url (dentro allowlist: il route-guard
    #    aborta fuori allowlist → goto fallisce onestamente).
    if not await _has_toplevel_password(page) and login_url:
        try:
            await asyncio.wait_for(
                page.goto(login_url, wait_until="load", timeout=int(op_timeout_s * 1000)),
                timeout=op_timeout_s)
        except Exception:
            pass  # onesto sotto: se ancora niente form → selettore_assente
    if not await _has_toplevel_password(page):
        return {"ok": True, "logged_in": False, "reason_code": "selettore_assente",
                "error_class": "no_login_form"}

    # 3. CRITICO-1 — verifica ORIGINE prima di digitare. Il JS tagga anche i
    #    campi (CRITICO-2: risoluzione autonoma del broker, mai selettori LLM).
    info = await page.evaluate(_LOCATE_LOGIN_FORM_JS)
    if not info or not info.get("found"):
        return {"ok": True, "logged_in": False, "reason_code": "selettore_assente",
                "error_class": "no_login_form"}
    form_host = _host_of(info.get("actionResolved") or page.url)
    # D-D: match ESATTO col dominio del vault. Niente sottodomini, niente iframe
    # (il JS cerca solo nel main frame).
    if form_host != domain.lower():
        sites_audit.record("origin_mismatch", owner=owner, session_id=session_id,
                           domain=domain, form_host=form_host)
        return {"ok": True, "logged_in": False,
                "reason_code": "origine_non_verificata",
                "error_class": "origin_mismatch"}

    # 4. CRITICO-2/3 — digita nei SOLI campi risolti dal broker. Il pw field è
    #    già marcato `data-metnos-redact`. Nessuno screenshot fra fill e submit.
    try:
        if username and info.get("hasUser"):
            await page.fill('[data-metnos-user="1"]', username, timeout=int(op_timeout_s * 1000))
        await page.fill('[data-metnos-pw="1"]', password, timeout=int(op_timeout_s * 1000))
    except Exception:
        return {"ok": True, "logged_in": False, "reason_code": "selettore_assente",
                "error_class": "fill_failed"}

    # 5-6. Submit deterministico + attesa navigazione/idle (bounded).
    try:
        if info.get("hasSubmit"):
            await page.click('[data-metnos-submit="1"]', timeout=int(op_timeout_s * 1000))
        else:
            await page.press('[data-metnos-pw="1"]', "Enter", timeout=int(op_timeout_s * 1000))
    except Exception:
        pass  # il submit può innescare navigazione che chiude il contesto DOM
    try:
        await asyncio.wait_for(
            page.wait_for_load_state("load", timeout=int(op_timeout_s * 1000)),
            timeout=op_timeout_s)
    except Exception:
        pass

    # 7. Rilevazioni oneste (D-E: F1 NON auto-risolve 2FA/CAPTCHA, cede all'utente).
    otp = False
    captcha = False
    try:
        otp = bool(await page.evaluate(_DETECT_OTP_JS))
        captcha = bool(await page.evaluate(_DETECT_CAPTCHA_JS))
    except Exception:
        pass

    # 8. Verifica ESITO onesta (§2.8): cookie di sessione dichiarati presenti,
    #    OPPURE il campo password è sparito (e non c'è OTP/errore residuo).
    logged_in = False
    try:
        cookies = await context.cookies()
    except Exception:
        cookies = []
    if session_cookie_names:
        have = {c.get("name") for c in cookies if c.get("value")}
        logged_in = any(n in have for n in session_cookie_names)
    still_pw = await _has_toplevel_password(page)
    if not logged_in and not session_cookie_names:
        # Senza nomi-cookie dichiarati: euristica onesta = niente più password
        # form, niente OTP pendente.
        logged_in = (not still_pw) and (not otp)

    reason = None
    if not logged_in:
        if captcha:
            reason = "captcha"
        elif otp:
            reason = "2fa_richiesto"
        elif still_pw:
            reason = "password_errata"
        else:
            reason = "login_fallito"

    # 9. Audit: esito + uso credenziale (FINGERPRINT, mai il valore).
    try:
        fp = credentials.fingerprint(domain)
    except Exception:
        fp = None
    sites_audit.record("login_attempt", owner=owner, session_id=session_id,
                       domain=domain, outcome=logged_in, reason=reason)
    sites_audit.record("credential_use", owner=owner, session_id=session_id,
                       domain=domain, fingerprint=fp)

    return {"ok": True, "logged_in": logged_in, "reason_code": reason}
