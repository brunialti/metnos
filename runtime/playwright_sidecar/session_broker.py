# SPDX-License-Identifier: AGPL-3.0-only
"""session_broker — contesti browser nominati e persistenti (spec sites §3.1/§3.3).

Estende il sidecar Playwright (un solo Chromium persistente) con SESSIONI
autenticabili: `new_context()` isolati, con confine di rete per-sessione, TTL
idle, screenshot redatti. È il MOTORE del dominio `sites`; gli executor
(`open/login/read/close_sites`) sono client HTTP thin che NON vedono mai un
segreto — solo il broker chiama `credentials.load` (via credential_injection).

Presidi implementati ESATTAMENTE come da spec (zero variazione creativa):
  §3.1 FIX A — session_id validato a ogni op; assente/scaduto → `session_lost`.
  §3.1 FIX B — TTL in PAUSA finché `gate_pending` (attesa OTP/approvazione).
  §3.1 FIX C — timeout per-op (20s), cap contesti concorrenti (4), quota
               per-utente (2), lock per-sessione (un'op appesa non stalla le altre).
  §3.1 FIX D — route() aborta fuori-allowlist; WebRTC neutralizzato;
               navigazione top-level data:/blob: bloccata.
  §3.2      — login delegato a credential_injection (origine verificata,
               destinazione risolta dal broker, no-segreto).
  §3.3      — screenshot in dir per-owner 0700 (file 0600), TTL 30min,
               SEMPRE redatti (redaction.apply_redaction) prima del capture.

§7.9 deterministico salvo il browser (isolato dietro l'HTTP boundary del sidecar).
§2.8 fail-loud: ogni path d'errore → dict esplicito con `error_class`.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path

from playwright_sidecar import credential_injection
from playwright_sidecar import redaction
from playwright_sidecar import action_resolver
from playwright_sidecar import browser_surface
import sites_audit
import sites_observed  # ADR 0191 P4 — codici osservativi navigazione
import task_mandates
import credential_mandates
from sites_url_scrub import scrub_url

_monotonic = time.monotonic

try:
    import config as _C  # §7.11
    _SHOTS_ROOT = _C.PATH_USER_DATA / "sites-shots"
except Exception:  # pragma: no cover
    _SHOTS_ROOT = Path.home() / ".local" / "share" / "metnos" / "sites-shots"

# ── Cap di sicurezza (§3.1 FIX C) ──────────────────────────────────────────
_OP_TIMEOUT_S = 20.0            # timeout per singola operazione
_LOGIN_TIMEOUT_S = 120.0        # budget assoluto della macchina login
_MAX_CONTEXTS = 4              # contesti concorrenti totali
_PER_USER_QUOTA = 2           # sessioni per owner
_TTL_IDLE_S = 15 * 60         # scadenza idle sessione
_SHOT_TTL_S = 30 * 60         # scadenza screenshot su disco
_GATE_MAX_S = 60 * 60         # nessun gate puo' bloccare una sessione per sempre
_OPEN_APPROVAL_TTL_S = _GATE_MAX_S
_REAP_INTERVAL_S = 60.0       # cadenza del reaper
def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a numeric deployment limit without allowing unsafe extremes."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _enabled_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# Profilo browser dei context sites. L'anti-automazione dei portali (Amazon in
# primis) penalizza gli UA palesi ("...playwright") e le incoerenze
# UA/viewport/JS. Default = Chrome desktop reale COERENTE col SO host (Linux),
# viewport desktop: massima coerenza e nessun cambio di layout rispetto ai
# flussi validati. L'emulazione mobile/Android (UA + viewport + touch, coerente)
# e' disponibile via env `METNOS_SITES_MOBILE=1` ma altera geometria/overlay dei
# portali (regressione overlay osservata nel simulatore): opt-in.
# UA stealth spostati in `stealth.py` (registro drop-in, fix #13).


_LOCALE_BY_LANG = {"it": "it-IT", "en": "en-US"}


def _locale_for(lang: str | None) -> str | None:
    """Locale di rendering derivato dalla lingua (ADR 0191 H1: niente costante
    it-IT). Override esplicito `METNOS_SITES_LOCALE`; poi `lang`/`METNOS_LANG`;
    altrimenti None → nessun override (Chromium nativo)."""
    override = os.getenv("METNOS_SITES_LOCALE")
    if override:
        return override
    code = (lang or os.getenv("METNOS_LANG") or "").strip().lower()[:2]
    return _LOCALE_BY_LANG.get(code)


def _system_timezone_id() -> str | None:
    """Timezone IANA di sistema (ADR 0191 H1: niente costante Europe/Rome).
    Override `METNOS_SITES_TIMEZONE`/`TZ`; poi `/etc/timezone` o il symlink
    `/etc/localtime`; altrimenti None → nessun override."""
    for env_key in ("METNOS_SITES_TIMEZONE", "TZ"):
        v = os.getenv(env_key)
        if v and "/" in v:
            return v
    try:
        with open("/etc/timezone", encoding="utf-8") as fh:
            v = fh.read().strip()
        if v and "/" in v:
            return v
    except Exception:
        pass
    try:
        link = os.readlink("/etc/localtime")
        marker = "zoneinfo/"
        idx = link.find(marker)
        if idx >= 0:
            cand = link[idx + len(marker):]
            if "/" in cand:
                return cand
    except Exception:
        pass
    return None


def _stealth_allowed() -> bool:
    """Ceiling di deployment (ADR 0191 P1): kill-switch admin. Default ON.
    `METNOS_SITES_STEALTH_ALLOWED=0` disabilita lo stealth deployment-wide."""
    return _enabled_env("METNOS_SITES_STEALTH_ALLOWED", default=True)


def _context_kwargs(*, stealth_techniques=(),
                    lang: str | None = None,
                    browser_version: str = "") -> dict:
    # DEFAULT (stealth off): UA NATIVO del Chromium — nessun override (forzare
    # una UA e' spoofing, non igiene). Localizzazione benigna derivata dalla
    # lingua dell'istanza + timezone di sistema (H1), viewport, WebRTC off.
    kw = {
        "service_workers": "block",
        "viewport": {"width": 1280, "height": 800},
    }
    locale = _locale_for(lang)
    if locale:
        kw["locale"] = locale
    tz = _system_timezone_id()
    if tz:
        kw["timezone_id"] = tz
    # ── Layer CONTEXT stealth (registro DROP-IN stealth.py, fix #13) ───────
    from playwright_sidecar import stealth as _st
    kw.update(_st.context_kwargs(
        techniques=stealth_techniques, browser_version=browser_version))
    return kw


# Host cap remains finite and fail-closed; deployments can tune it for sites
# with larger dependency graphs without changing the executor contract.
_MAX_ALLOWLIST_HOSTS = _bounded_int_env(
    "METNOS_SITES_MAX_ALLOWLIST_HOSTS", default=64, minimum=16, maximum=128
)
_ENUMERATE_TIMEOUT_MS = _bounded_int_env(
    "METNOS_SITES_ENUMERATE_TIMEOUT_MS", default=3000, minimum=500, maximum=10000
)
_LOCAL_RESOLVER_TIMEOUT_MS = _bounded_int_env(
    "METNOS_SITES_LOCAL_RESOLVER_TIMEOUT_MS", default=6000,
    minimum=1000, maximum=20000
)
_CLICK_TIMEOUT_MS = _bounded_int_env(
    "METNOS_SITES_CLICK_TIMEOUT_MS", default=6000, minimum=1000, maximum=20000
)
_MODEL_FALLBACKS_ENABLED = _enabled_env(
    "METNOS_SITES_MODEL_FALLBACKS", default=True)
_RESOURCE_DISCOVERY_MS = 1000 # finestra bounded per richieste client-side
_REVEAL_SETTLE_MS = 2000      # attesa bounded target dopo controllo reveal
_REVEAL_POLL_MS = 100
_CONTENT_SETTLE_MS = _bounded_int_env(
    "METNOS_SITES_CONTENT_SETTLE_MS", default=10000,
    minimum=1000, maximum=30000)
_GOAL_NAVIGATION_COMMIT_MS = _bounded_int_env(
    "METNOS_SITES_GOAL_NAVIGATION_COMMIT_MS", default=45000,
    minimum=5000, maximum=60000)
_MAX_COLLECTION_SCROLLS = _bounded_int_env(
    "METNOS_SITES_MAX_COLLECTION_SCROLLS", default=20,
    minimum=1, maximum=100)
_MAX_ACTION_REPLANS = 2
_MAX_LOGIN_ENTRY_STEPS = 4
_MAX_PRIVACY_DISMISSALS = 2   # budget PROPRIO (non login-step): un overlay che
                             # riappare non deve affamare la navigazione login
_MAX_GOAL_STEPS = 4
_MAX_GOAL_CONTINUATIONS = 6
_APPROVAL_RESULT_TTL_S = 120.0
_DISCOVERABLE_RESOURCE_TYPES = frozenset({
    "document", "script", "stylesheet", "xhr", "fetch",
})

# ── Stato globale del broker ───────────────────────────────────────────────
_browser_provider = None              # BrowserProvider, impostato da configure() (B1)
_sessions: dict[str, dict] = {}       # session_id -> entry
_pending_opens: dict[str, dict] = {}  # token opaco -> piano allowlist
_reaper_task = None

_WEBRTC_OFF_JS = r"""
() => {
  const undef = {value: undefined, configurable: false, writable: false};
  try { Object.defineProperty(window, 'RTCPeerConnection', undef); } catch(e){}
  try { Object.defineProperty(window, 'webkitRTCPeerConnection', undef); } catch(e){}
  try { Object.defineProperty(window, 'RTCDataChannel', undef); } catch(e){}
  try { if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia =
        () => Promise.reject(new Error('disabled')); } catch(e){}
}
"""

# Init-script CONTEXT stealth spostato in `stealth.py::_CONTEXT_JS` (registro
# drop-in, fix #13). Il default resta onesto; il webdriver-hiding vive nel
# LAUNCH-arg del browser stealth, non qui.

_ENUMERATE_ACTION_TARGETS_JS = r"""
() => {
  document.querySelectorAll('[data-metnos-action-id]').forEach(
    el => el.removeAttribute('data-metnos-action-id'));
  const standard = Array.from(document.querySelectorAll(
    'a,button,input,textarea,select,[role=button],[role=link],'
    + '[role=tab],[role=menuitem],[role=checkbox],[role=radio],'
    + '[contenteditable=true],summary,'
    + '[tabindex]:not([tabindex="-1"]),[onclick]'));
  // React e altri framework possono rendere cliccabile un div senza ruolo o
  // onclick DOM. Accetta solo un insieme bounded di nodi VISIBILI con
  // cursor:pointer e testo breve: restano poi soggetti a topmost, firma e gate
  // come ogni candidato.
  // Il vecchio `filter(...).slice(0, 200)` visitava TUTTO il DOM e, per ogni
  // nodo pointer, scandiva di nuovo tutti i discendenti. Su pagine grandi era
  // quadratico: il timeout scartava anche i controlli HTML gia' enumerati.
  // Questo fallback resta bounded e lineare; i controlli semantici standard
  // hanno comunque precedenza nel resolver.
  const pointer = [];
  const standardSet = new Set(standard);
  const POINTER_SCAN_LIMIT = 4000;
  const walker = document.createTreeWalker(
    document.body, NodeFilter.SHOW_ELEMENT);
  let inspected = 0;
  for (let el = walker.nextNode(); el && inspected < POINTER_SCAN_LIMIT;
       el = walker.nextNode()) {
    inspected += 1;
    if (pointer.length >= 200) break;
    if (standardSet.has(el)) continue;
    const st = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (st.cursor !== 'pointer' || r.width < 2 || r.height < 2 ||
        st.display === 'none' || st.visibility === 'hidden' ||
        Number.parseFloat(st.opacity || '1') < 0.05) continue;
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
    if (!text || text.length > 160) continue;
    pointer.push(el);
  }
  // Expensive accessible-name/context/topmost extraction is bounded. Preserve
  // every visible semantic control ahead of off-viewport/hidden controls so a
  // portal menu appended late in a very large DOM is still observable, while
  // retaining a bounded tail for scroll/reveal discovery.
  const visibleEls = [];
  const otherEls = [];
  for (const el of Array.from(new Set([...standard, ...pointer]))) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    const rendered = r.width >= 2 && r.height >= 2 &&
      st.visibility !== 'hidden' && st.display !== 'none' &&
      Number.parseFloat(st.opacity || '1') >= 0.05;
    const ix = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const iy = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    const visibleRatio = r.width > 0 && r.height > 0
      ? (ix * iy) / (r.width * r.height) : 0;
    const item = {el, r, st, rendered, visibleRatio};
    (rendered && visibleRatio >= 0.2 ? visibleEls : otherEls).push(item);
  }
  const els = [
    ...visibleEls.slice(0, 480),
    ...otherEls.slice(0, 160),
  ].slice(0, 640);
  const out = [];
  let n = 0;
  const nameOf = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .filter(Boolean).map(id => document.getElementById(id))
      .filter(Boolean).map(x => x.innerText || x.textContent || '').join(' ');
    const alt = el.querySelector && el.querySelector('img[alt]');
    return el.getAttribute('aria-label') || labelled ||
      el.getAttribute('title') || (alt ? alt.getAttribute('alt') : '') ||
      el.innerText ||
      ((el.type === 'submit' || el.type === 'button') ? el.value : '') || '';
  };
  const controlsOf = el => {
    const out = [];
    for (const attr of ['aria-controls', 'popovertarget', 'commandfor']) {
      out.push(...(el.getAttribute(attr) || '').split(/\s+/).filter(Boolean));
    }
    const href = el.getAttribute('href') || '';
    if (href.startsWith('#') && href.length > 1) out.push(href.slice(1));
    return Array.from(new Set(out));
  };
  const contextOf = el => {
    for (let p = el.parentElement, depth = 0; p && depth < 8;
         p = p.parentElement, depth++) {
      const labelled = p.getAttribute('aria-label') || '';
      if (labelled.trim()) return labelled.trim().slice(0, 200);
      const heading = p.querySelector(
        ':scope > h1,:scope > h2,:scope > h3,:scope > h4,' +
        ':scope > [role=heading]');
      const text = heading ? (heading.innerText || heading.textContent || '') : '';
      if (text.trim()) return text.trim().replace(/\s+/g, ' ').slice(0, 200);
    }
    return '';
  };
  for (const item of els) {
    const {el, r, st, rendered, visibleRatio} = item;
    const id = `m${++n}`;
    el.setAttribute('data-metnos-action-id', id);
    const form = el.form || el.closest('form');
    const label = el.labels && el.labels.length
      ? Array.from(el.labels).map(x => x.innerText || x.textContent || '').join(' ')
      : '';
    const inViewport = visibleRatio >= 0.2;
    const visible = rendered && inViewport;
    const ancestors = [];
    for (let p = el.parentElement, depth = 0; p && depth < 12;
         p = p.parentElement, depth++) {
      if (p.id) ancestors.push(p.id);
    }
    const topmost = (() => {
      if (!visible) return false;
      const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
      const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
      const top = document.elementFromPoint(x, y);
      return top === el || !!(top && el.contains(top));
    })();
    out.push({
      id, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      name: nameOf(el),
      label, context_name: contextOf(el),
      placeholder: el.getAttribute('placeholder') || '',
      href: el.href || '', download: el.hasAttribute('download'),
      dom_id: el.id || '', ancestor_ids: ancestors,
      control_targets: controlsOf(el),
      aria_expanded: el.getAttribute('aria-expanded') || '',
      aria_selected: el.getAttribute('aria-selected') || '',
      aria_pressed: el.getAttribute('aria-pressed') || '',
      aria_checked: el.getAttribute('aria-checked') || '',
      aria_current: el.getAttribute('aria-current') || '',
      checked: !!el.checked,
      form_action: form ? form.action : '',
      form_method: form ? (form.method || 'get').toUpperCase() : '',
      secret_input: el.type === 'password' ||
        (el.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code' ||
        /(^|[^a-z])(otp|verification|2fa|one.time|pin)([^a-z]|$)/i.test(
          [el.name, el.id, label, el.getAttribute('placeholder') || ''].join(' ')),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      editable: el.isContentEditable,
      rendered, visible, in_viewport: inViewport, visible_ratio: visibleRatio,
      topmost,
      rect: {x: Math.round(r.x), y: Math.round(r.y),
             width: Math.round(r.width), height: Math.round(r.height)}
    });
  }
  return out;
}
"""

_LOCATE_SAFE_OVERLAY_DISMISS_JS = r"""
(config) => {
  document.querySelectorAll('[data-metnos-overlay-dismiss]').forEach(
    el => el.removeAttribute('data-metnos-overlay-dismiss'));
  const normalize = value => (value || '').normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
  const allowed = new Set((Array.isArray(config && config.forms)
    ? config.forms : [])
    .map(normalize).filter(Boolean));
  const markers = (Array.isArray(config && config.markers)
    ? config.markers : []).map(normalize).filter(Boolean);
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.display === 'none' ||
        st.visibility === 'hidden' || Number.parseFloat(st.opacity || '1') < 0.05 ||
        el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
    const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
    const top = document.elementFromPoint(x, y);
    return top === el || !!(top && el.contains(top));
  };
  const modalRoot = el => {
    let structural = null;
    for (let p = el.parentElement, depth = 0; p && p !== document.body && depth < 14;
         p = p.parentElement, depth++) {
      const r = p.getBoundingClientRect();
      const st = getComputedStyle(p);
      const area = Math.max(0, r.width) * Math.max(0, r.height);
      const semantic = p.tagName.toLowerCase() === 'dialog' ||
        p.getAttribute('role') === 'dialog' ||
        p.getAttribute('role') === 'alertdialog' ||
        p.getAttribute('aria-modal') === 'true';
      if (semantic && area >= innerWidth * innerHeight * 0.03) return p;
      if ((st.position === 'fixed' || st.position === 'sticky') &&
          area >= innerWidth * innerHeight * 0.12) structural = p;
    }
    return structural;
  };
  const nameOf = el => el.getAttribute('aria-label') ||
    el.getAttribute('title') || el.innerText ||
    ((el.type === 'button' || el.type === 'submit') ? el.value : '') || '';
  const iconExit = (el, root, rawName) => {
    if (!/^[x\u00d7\u2715\u2716]$/i.test((rawName || '').trim())) return false;
    const er = el.getBoundingClientRect();
    for (let p = el.parentElement, depth = 0; p && p !== root.parentElement && depth < 10;
         p = p.parentElement, depth++) {
      const r = p.getBoundingClientRect();
      if (r.width < 180 || r.height < 100 || r.width > innerWidth * 0.98) continue;
      if (er.left >= r.right - Math.min(100, r.width * 0.25) &&
          er.top <= r.top + Math.min(100, r.height * 0.25)) return true;
    }
    return false;
  };
  // ADR 0191 P5 (#9): la dismissione NON deve mai attivare un submitter di form
  // ne' una navigazione. Un controllo navigante passa dal piano firmato + gate,
  // non da qui.
  const isFormSubmitter = el => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && (type === 'submit' || type === 'image')) return true;
    if (tag === 'button') {
      if (type === 'submit') return true;
      // button SENZA `type` dentro un <form> = submit implicito (HTML default).
      if (!type && el.closest('form')) return true;
    }
    return false;
  };
  const isNavigatingLink = el => {
    if (el.tagName.toLowerCase() !== 'a') return false;
    const href = el.getAttribute('href') || '';
    // Fragment same-page (`#`, `#sez`) = non navigante, ammesso. Tutto il resto
    // (http(s), relativo, `javascript:`) = navigante/attivo, vietato.
    if (!href || href === '#' || href.startsWith('#')) return false;
    return true;
  };
  // Include ANCHE i submitter, per RICONOSCERLI come controlli di chiusura
  // naviganti (fix #11): non li clicchiamo (P5), ma li segnaliamo per il gate.
  const controls = Array.from(document.querySelectorAll(
    'button,[role=button],input[type=button],input[type=submit],'
    + 'input[type=image],a'));
  const ranked = [];
  const navigating = [];
  for (const el of controls) {
    if (!visible(el)) continue;
    const root = modalRoot(el);
    if (!root) continue;
    const rawName = nameOf(el).trim();
    const name = normalize(rawName);
    const exactExit = allowed.has(name);
    const icon = iconExit(el, root, rawName);
    if (!exactExit && !icon) continue;
    const rootText = ` ${normalize(root.innerText || root.textContent || '')} `;
    if (markers.length && !markers.some(marker =>
        rootText.includes(` ${marker} `))) continue;
    // Fix adversarial #11: e' un controllo di CHIUSURA. Se navigante/submitter,
    // NON lo dismettiamo silenziosamente (P5) — lo segnaliamo per il piano
    // firmato (gate), evitando lo STALLO su overlay che richiedono navigazione.
    if (isFormSubmitter(el) || isNavigatingLink(el)) {
      navigating.push({
        name: rawName,
        action: (el.form && el.form.action) || el.getAttribute('href') || ''});
      continue;
    }
    const rootSemantic = root.tagName.toLowerCase() === 'dialog' ||
      root.getAttribute('role') === 'dialog' ||
      root.getAttribute('role') === 'alertdialog' ||
      root.getAttribute('aria-modal') === 'true';
    ranked.push({el, score: (exactExit ? 100 : 80) + (rootSemantic ? 10 : 0),
                 kind: exactExit ? 'label' : 'icon'});
  }
  ranked.sort((a, b) => b.score - a.score);
  if (ranked.length) {
    ranked[0].el.setAttribute('data-metnos-overlay-dismiss', '1');
    return {found: true, kind: ranked[0].kind, candidates: ranked.length};
  }
  if (navigating.length) {
    return {found: false, navigating_only: true, control: navigating[0]};
  }
  return {found: false};
}
"""

_GOAL_EVIDENCE_JS = r"""
() => {
  const excluded = [
    'a', 'button', 'input', 'textarea', 'select', 'option', 'summary',
    'nav', 'menu', 'header', 'footer', 'aside',
    '[role=navigation]', '[role=menu]', '[role=menuitem]',
    '[role=button]', '[role=link]', '[role=tab]', '[contenteditable=true]'
  ].join(',');
  const groups = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    const text = (node.nodeValue || '').trim().replace(/\s+/g, ' ');
    if (!parent || !text || parent.closest(excluded)) continue;
    const r = parent.getBoundingClientRect();
    const st = getComputedStyle(parent);
    if (r.width < 2 || r.height < 2 || st.display === 'none' ||
        st.visibility === 'hidden' || Number.parseFloat(st.opacity || '1') < 0.05)
      continue;
    const block = parent.closest(
      'h1,h2,h3,h4,h5,h6,[role=heading],tr,p,li,dt,dd,section,article,main,div')
      || parent;
    groups.set(block, `${groups.get(block) || ''} ${text}`.trim().slice(0, 4000));
    if (groups.size >= 400) break;
  }
  return Array.from(new Set(groups.values())).slice(0, 400);
}
"""

_TRANSIENT_LOADING_JS = r"""
(markers) => {
  const normalize = value => (value || '').normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
  const wanted = (Array.isArray(markers) ? markers : [])
    .map(normalize).filter(Boolean);
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number.parseFloat(st.opacity || '1') >= 0.05 &&
      r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  const semantic = Array.from(document.querySelectorAll(
    '[aria-busy="true"],[role="progressbar"],progress,' +
    '[class*="loading" i],[class*="spinner" i]')).some(visible);
  if (semantic) return true;
  if (!wanted.length) return false;
  return Array.from(document.querySelectorAll('main *,[role="main"] *,body > *'))
    .some(el => {
      if (!visible(el)) return false;
      const text = normalize(el.innerText || el.textContent || '');
      if (!text || text.length > 120) return false;
      return wanted.some(marker => text === marker || text.startsWith(marker + ' '));
    });
}
"""

_SCROLL_COLLECTION_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width >= 20 && r.height >= 20 && st.display !== 'none' &&
      st.visibility !== 'hidden';
  };
  const root = document.scrollingElement || document.documentElement;
  const candidates = [root, ...Array.from(document.querySelectorAll(
    'main,[role="main"],section,div')).filter(el =>
      visible(el) && el.scrollHeight > el.clientHeight + 80)];
  candidates.sort((a, b) =>
    (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
  const target = candidates[0] || root;
  const before = Number(target.scrollTop || 0);
  const maximum = Math.max(0, target.scrollHeight - target.clientHeight);
  target.scrollTop = target.scrollHeight;
  return {moved: maximum > before + 2, before, maximum};
}
"""

_CANDIDATE_STATE_JS = r"""
(id) => {
  const el = document.querySelector(`[data-metnos-action-id="${id}"]`);
  if (!el) return null;
  const form = el.form || el.closest('form');
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  const rendered = r.width >= 2 && r.height >= 2 &&
    st.visibility !== 'hidden' && st.display !== 'none' &&
    Number.parseFloat(st.opacity || '1') >= 0.05;
  const ix = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
  const iy = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
  const visibleRatio = r.width > 0 && r.height > 0
    ? (ix * iy) / (r.width * r.height) : 0;
  const inViewport = visibleRatio >= 0.2;
  const visible = rendered && inViewport;
  const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
  const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
  const top = visible ? document.elementFromPoint(x, y) : null;
  return {
    id, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
    role: el.getAttribute('role') || '',
    name: el.getAttribute('aria-label') || el.innerText ||
          ((el.type === 'submit' || el.type === 'button') ? el.value : '') || '',
    href: el.href || '', download: el.hasAttribute('download'),
    form_action: form ? form.action : '',
    form_method: form ? (form.method || 'get').toUpperCase() : '',
    secret_input: el.type === 'password' ||
      (el.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code' ||
      /(^|[^a-z])(otp|verification|2fa|one.time|pin)([^a-z]|$)/i.test(
        [el.name, el.id, el.getAttribute('aria-label') || '',
         el.getAttribute('placeholder') || ''].join(' ')),
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    rendered, visible, in_viewport: inViewport,
    aria_expanded: el.getAttribute('aria-expanded') || '',
    aria_selected: el.getAttribute('aria-selected') || '',
    aria_pressed: el.getAttribute('aria-pressed') || '',
    aria_checked: el.getAttribute('aria-checked') || '',
    aria_current: el.getAttribute('aria-current') || '',
    checked: !!el.checked,
    topmost: top === el || !!(top && el.contains(top)),
    rect: {x: Math.round(r.x), y: Math.round(r.y),
           width: Math.round(r.width), height: Math.round(r.height)}
  };
}
"""

_ELEMENT_STATE_JS = r"""
(el) => {
  if (!el || !el.isConnected) return null;
  const form = el.form || el.closest('form');
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  const rendered = r.width >= 2 && r.height >= 2 &&
    st.visibility !== 'hidden' && st.display !== 'none' &&
    Number.parseFloat(st.opacity || '1') >= 0.05;
  const ix = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
  const iy = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
  const visibleRatio = r.width > 0 && r.height > 0
    ? (ix * iy) / (r.width * r.height) : 0;
  const inViewport = visibleRatio >= 0.2;
  const visible = rendered && inViewport;
  const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
  const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
  const top = visible ? document.elementFromPoint(x, y) : null;
  return {
    id: el.getAttribute('data-metnos-action-id') || '',
    tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
    role: el.getAttribute('role') || '',
    name: el.getAttribute('aria-label') || el.innerText ||
          ((el.type === 'submit' || el.type === 'button') ? el.value : '') || '',
    href: el.href || '', download: el.hasAttribute('download'),
    form_action: form ? form.action : '',
    form_method: form ? (form.method || 'get').toUpperCase() : '',
    secret_input: el.type === 'password' ||
      (el.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code' ||
      /(^|[^a-z])(otp|verification|2fa|one.time|pin)([^a-z]|$)/i.test(
        [el.name, el.id, el.getAttribute('aria-label') || '',
         el.getAttribute('placeholder') || ''].join(' ')),
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    rendered, visible, in_viewport: inViewport,
    aria_expanded: el.getAttribute('aria-expanded') || '',
    aria_selected: el.getAttribute('aria-selected') || '',
    aria_pressed: el.getAttribute('aria-pressed') || '',
    aria_checked: el.getAttribute('aria-checked') || '',
    aria_current: el.getAttribute('aria-current') || '',
    checked: !!el.checked,
    topmost: top === el || !!(top && el.contains(top)),
    rect: {x: Math.round(r.x), y: Math.round(r.y),
           width: Math.round(r.width), height: Math.round(r.height)}
  };
}
"""

_ENUMERATE_FORMS_JS = r"""
() => Array.from(document.forms).slice(0, 20).map((form, index) => ({
  index,
  method: (form.method || 'get').toUpperCase(),
  action: form.action || location.href,
  fields: Array.from(form.elements).slice(0, 50).map(el => ({
    tag: (el.tagName || '').toLowerCase(),
    type: (el.type || '').toLowerCase(),
    name: el.name || '',
    label: el.getAttribute('aria-label') ||
      (el.labels && el.labels.length
        ? Array.from(el.labels).map(x => x.innerText || x.textContent || '').join(' ')
        : '') || el.getAttribute('placeholder') || '',
    required: !!el.required,
    disabled: !!el.disabled
  }))
}))
"""


def configure(browser_provider) -> None:
    """Chiamato da server._on_startup. Riceve un BrowserProvider (B1): il broker
    NON possiede/lancia browser; chiede `await browser_provider(stealth)` a
    ogni `op_open`. Owner esclusivo di Playwright = server.py."""
    global _browser_provider
    _browser_provider = browser_provider


def health_snapshot() -> dict:
    """Bounded, non-sensitive broker state for the sidecar health endpoint."""
    task = _reaper_task
    # Il broker non possiede piu' i browser (B1): la connessione e' esposta da
    # server.py (`browser_honest_connected`/`browser_stealth_state`). Qui si
    # riporta solo se il provider e' configurato.
    provider_ready = _browser_provider is not None
    return {
        "browser_connected": provider_ready,
        "provider_configured": provider_ready,
        "reaper_running": bool(task is not None and not task.done()),
        "active_sessions": len(_sessions),
        "approval_pending_sessions": sum(
            1 for entry in _sessions.values()
            if entry.get("gate_pending")),
        "factor_pending_sessions": sum(
            1 for entry in _sessions.values()
            if entry.get("factor_pending")),
        "pending_opens": len(_pending_opens),
    }


def _owner_slug(owner: str) -> str:
    """Owner → segmento di path sicuro (no traversal). `telegram:123` → `telegram_123`."""
    slug = re.sub(r"[^a-z0-9_.-]", "_", (owner or "host").lower())
    return slug or "host"


def _shots_dir(owner: str) -> Path:
    d = _SHOTS_ROOT / _owner_slug(owner)
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    _SHOTS_ROOT.chmod(0o700)
    return d


def _sweep_old_shots(owner: str) -> None:
    """Rimuove gli screenshot oltre il TTL (§3.3)."""
    d = _SHOTS_ROOT / _owner_slug(owner)
    if not d.exists():
        return
    now = time.time()
    for p in d.glob("*.png"):
        try:
            if now - p.stat().st_mtime > _SHOT_TTL_S:
                p.unlink()
        except OSError:
            pass


# ── Confine di rete per-sessione (§3.1 FIX D) ──────────────────────────────

def _canonical_host(value: str) -> str:
    """Normalizza un hostname esatto senza accettare URL, porte o userinfo."""
    if not isinstance(value, str):
        return ""
    host = value.strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        return ipaddress.ip_address(host).compressed.lower()
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return ""
    if len(host) > 253:
        return ""
    labels = host.split(".")
    if any(not label or len(label) > 63
           or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
           for label in labels):
        return ""
    return host


def _host_of_url(url: str) -> str:
    try:
        split = urllib.parse.urlsplit(url)
    except (TypeError, ValueError):
        return ""
    return _canonical_host(split.hostname or "")


def _request_resource_type(request) -> str:
    try:
        value = request.resource_type
        if callable(value):
            value = value()
        return str(value or "").lower()
    except Exception:
        return ""


def _request_provenance(request) -> dict:
    """Evidenza bounded dalla relazione request/frame di Playwright.

    Solo boolean e hostname: mai URL con token o contenuto di pagina. Serve a
    distinguere un document di navigazione top-level da un subframe terzo
    (adv/telemetria) quando l'host viene poi valutato per un fallback risorse.
    """
    main_frame = False
    navigation = False
    top_host = ""
    parent_host = ""
    try:
        navigation = bool(request.is_navigation_request())
    except Exception:
        pass
    try:
        frame = request.frame
        parent = getattr(frame, "parent_frame", None)
        main_frame = frame is not None and parent is None
        if parent is not None:
            parent_host = _host_of_url(getattr(parent, "url", "") or "")
        top = frame
        for _ in range(32):
            above = getattr(top, "parent_frame", None)
            if above is None:
                break
            top = above
        if top is not None:
            top_host = _host_of_url(getattr(top, "url", "") or "")
    except Exception:
        pass
    return {"main_frame": main_frame, "navigation": navigation,
            "top_host": top_host, "parent_host": parent_host}


def _observe_blocked_request(store: dict, host: str, resource_type: str,
                             provenance: dict | None = None) -> None:
    """Registra un host negato con tipi e provenienza bounded (§2.3 handoff).

    L'osservazione non concede accesso: alimenta soltanto la preparazione di
    un gate esatto e la classificazione di rilevanza del fallback risorse.
    """
    observation = store.setdefault(host, {
        "types": set(), "main_frame": False, "navigation": False,
        "top_host": "", "parent_host": "",
    })
    observation["types"].add(resource_type)
    if not provenance:
        return
    observation["main_frame"] = (observation["main_frame"]
                                 or bool(provenance.get("main_frame")))
    observation["navigation"] = (observation["navigation"]
                                 or bool(provenance.get("navigation")))
    if provenance.get("top_host"):
        observation["top_host"] = str(provenance["top_host"])
    if provenance.get("parent_host"):
        observation["parent_host"] = str(provenance["parent_host"])


def _make_route_guard(allowlist: set[str],
                      blocked_requests: dict[str, dict] | None = None):
    """Ritorna un handler `context.route` che ABORTA le richieste fuori
    allowlist e la navigazione top-level `data:`/`blob:`.

    Gli host negati vengono osservati solo per una whitelist chiusa di tipi di
    risorsa che puo' influire sull'interazione. L'osservazione non concede
    accesso: serve esclusivamente a preparare un gate esatto e monouso.
    """
    async def _guard(route, request):
        try:
            url = request.url
            scheme = url.split(":", 1)[0].lower() if ":" in url else ""
            # data:/blob: — blocca solo la NAVIGAZIONE top-level (esfil out-of-band);
            # i subresource data: (inline img/css) restano leciti.
            if scheme in ("data", "blob"):
                is_nav = False
                try:
                    is_nav = request.is_navigation_request()
                except Exception:
                    is_nav = False
                if is_nav:
                    await route.abort()
                    return
                await route.continue_()
                return
            if scheme in ("http", "https"):
                host = _host_of_url(url)
                if host in allowlist:
                    await route.continue_()
                else:
                    resource_type = _request_resource_type(request)
                    if (blocked_requests is not None and host
                            and resource_type in _DISCOVERABLE_RESOURCE_TYPES):
                        _observe_blocked_request(
                            blocked_requests, host, resource_type,
                            _request_provenance(request))
                    await route.abort()
                return
            if scheme in ("about", "chrome-error"):
                await route.continue_()
            else:
                await route.abort()
        except Exception:
            # In dubbio: aborta (fail-closed sul confine di rete).
            try:
                await route.abort()
            except Exception:
                pass
    return _guard


def _default_allowlist(url: str, allowlist_arg) -> set[str]:
    """D-D: default = dominio ESATTO dell'url. `allowlist_arg` (lista hostname)
    la sostituisce se fornita (estensione = decisione dell'executor/utente)."""
    hosts: set[str] = set()
    if allowlist_arg and isinstance(allowlist_arg, list):
        for h in allowlist_arg:
            normalized = _canonical_host(h)
            if normalized:
                hosts.add(normalized)
    url_host = _host_of_url(url)
    if url_host:
        hosts.add(url_host)
    # Mutabile solo dentro il broker: un target DOM puo' richiedere un host
    # aggiuntivo, che viene inserito esclusivamente dopo il gate legato a quel
    # target. La closure route() osserva lo stesso set aggiornato.
    return hosts


def _new_open_approval(*, owner: str, url: str, allowlist: set[str],
                       session_label: str, extra_hosts: set[str],
                       error: str, credential_mode: str = "default",
                       stealth: bool = False,
                       stealth_techniques=(),
                       browser_mode: str = "headless",
                       redirect_url: str = "",
                       blocked_requests: dict[str, dict] | None = None) -> dict:
    """Crea un token one-shot legato all'espansione esatta osservata."""
    expected = {
        "owner": owner, "url": url,
        "allowlist": tuple(sorted(allowlist)),
        "session_label": session_label or "",
        "credential_mode": credential_mode,
        # Fix adversarial #8: la modalita' stealth e' parte del binding del token
        # → un token non puo' essere ripresentato con una modalita' diversa.
        "stealth": bool(stealth),
        "stealth_techniques": tuple(stealth_techniques),
        "browser_mode": browser_mode,
    }
    token = secrets.token_urlsafe(24)
    _pending_opens[token] = {**expected, "created": time.time()}
    out = {
        "ok": False, "error": error, "error_class": "approval_required",
        "extra_hosts": sorted(extra_hosts), "approval_token": token,
        "approved_allowlist": sorted(allowlist),
    }
    if redirect_url:
        out["redirect_url"] = redirect_url
    if blocked_requests:
        out["blocked_resource_types"] = {
            host: sorted(blocked_requests[host].get("types") or ())
            for host in sorted(extra_hosts) if host in blocked_requests
        }
    return out


async def _settle_resource_discovery(page) -> None:
    """Finestra fissa per richieste avviate subito dopo il load."""
    if not hasattr(page, "wait_for_timeout"):
        return
    try:
        await asyncio.wait_for(
            page.wait_for_timeout(_RESOURCE_DISCOVERY_MS),
            timeout=(_RESOURCE_DISCOVERY_MS / 1000) + 0.25)
    except Exception:
        pass


# ── Validazione sessione (§3.1 FIX A) ──────────────────────────────────────

def _validate(session_id: str) -> dict | None:
    entry = _sessions.get(session_id)
    if entry is None:
        return None
    # FIX B: approval e factor handoff sospendono entrambi il TTL idle.
    if not (entry.get("gate_pending") or entry.get("factor_pending")):
        if time.time() - entry["last_used"] > _TTL_IDLE_S:
            return None
    return entry


def _validate_owned(session_id: str, owner: str | None) -> tuple[dict | None, str | None]:
    """Valida esistenza, TTL e appartenenza della sessione.

    Il session_id ha alta entropia ma non e' un bearer token: ogni operazione
    resta isolata per actor anche in caso di leak accidentale dell'id.
    """
    if not isinstance(owner, str) or not owner:
        return None, "forbidden"
    entry = _validate(session_id)
    if entry is None:
        return None, "session_lost"
    if entry.get("owner") != owner:
        return None, "forbidden"
    return entry, None


async def _touch(entry: dict) -> None:
    entry["last_used"] = time.time()


async def _close_entry(entry: dict) -> None:
    try:
        await entry["context"].close()
    except Exception:
        pass


# ── Reaper (§3.1 FIX B: salta gate_pending) ────────────────────────────────

async def _reaper_loop() -> None:
    while True:
        await asyncio.sleep(_REAP_INTERVAL_S)
        now = time.time()
        dead = []
        for sid, e in list(_sessions.items()):
            if e.get("gate_pending") or e.get("factor_pending"):
                starts = []
                if e.get("gate_pending"):
                    starts.append(float(e.get("gate_started") or now))
                if e.get("factor_pending"):
                    starts.append(float(e.get("factor_started") or now))
                started = min(starts or [now])
                if now - started <= _GATE_MAX_S:
                    continue  # TTL in pausa, ma bounded
                dead.append(sid)
                continue
            if now - e["last_used"] > _TTL_IDLE_S:
                dead.append(sid)
        for sid in dead:
            e = _sessions.pop(sid, None)
            if e:
                await _close_entry(e)
                sites_audit.record("session_reap", owner=e.get("owner", ""),
                                   session_id=sid, domain=e.get("domain", ""))


def start_reaper() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.ensure_future(_reaper_loop())


async def shutdown() -> None:
    """Close every browser context and reset process-local broker state.

    I browser sono chiusi da server._on_shutdown (owner, B1); qui si chiudono
    solo i context di sessione e si azzera il provider."""
    global _browser_provider, _reaper_task
    task = _reaper_task
    _reaper_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    for _sid, entry in list(_sessions.items()):
        await _close_entry(entry)
    _sessions.clear()
    _pending_opens.clear()
    _browser_provider = None


# ── Operazioni ─────────────────────────────────────────────────────────────

async def _reuse_compatible_session(*, owner: str, open_host: str,
                                    allowlist: set[str], session_label: str,
                                    credential_mode: str, browser_mode: str,
                                    stealth_techniques: tuple[str, ...],
                                    task_binding, credential_binding) -> dict | None:
    """Trova una sessione autenticata viva con identico confine operativo."""
    for session_id, entry in reversed(tuple(_sessions.items())):
        if (_validate(session_id) is None
                or entry.get("owner") != owner
                or entry.get("open_host") != open_host
                or set(entry.get("allowlist") or ()) != set(allowlist)
                or entry.get("label", "") != (session_label or "")
                or entry.get("credential_mode") != credential_mode
                or entry.get("browser_mode") != browser_mode
                or tuple(entry.get("stealth_techniques") or ())
                    != tuple(stealth_techniques)
                or entry.get("task_mandate") != task_binding
                or entry.get("credential_mandate") != credential_binding
                or not entry.get("authenticated")
                or entry.get("gate_pending") or entry.get("factor_pending")
                or entry.get("secret_pending")):
            continue
        lock = entry.get("lock")
        if lock is not None and getattr(lock, "locked", lambda: False)():
            continue
        page = entry.get("page")
        if page is None:
            continue
        try:
            if hasattr(page, "is_closed") and page.is_closed():
                continue
            title = await page.title()
        except Exception:
            continue
        await _touch(entry)
        sites_audit.record(
            "session_reuse", owner=owner, session_id=session_id,
            domain=entry.get("domain", ""), url=scrub_url(page.url))
        return {
            "ok": True, "session_id": session_id,
            "url": scrub_url(page.url), "title": title, "reused": True,
            **({"reason_code": entry["observed_reason"]}
               if entry.get("observed_reason") else {}),
        }
    return None


async def op_open(*, owner: str, url: str, allowlist_arg=None,
                  session_label: str = "",
                  approval_token: str | None = None,
                  task_name: str | None = None,
                  credential_mode: str = "default",
                  stealth: bool = False,
                  stealth_techniques=None,
                  browser_mode: str = "headless",
                  lang: str | None = None) -> dict:
    """Apre UNA sessione su `url` (§3.4 open_sites fa fan-out su N url).

    `stealth` e' il master per-turno; `stealth_techniques` e' la selezione
    indipendente fissata alla sessione. Il ceiling deployment puo' azzerarla.
    `lang` determina locale/timezone (fix #9)."""
    if not isinstance(owner, str) or not owner:
        return {"ok": False, "error": "owner required",
                "error_class": "forbidden"}
    if _browser_provider is None:
        return {"ok": False, "error": "browser not ready", "error_class": "unknown"}
    if not isinstance(url, str) or not url:
        return {"ok": False, "error": "url required", "error_class": "invalid_args"}
    if credential_mode not in {"default", "none"}:
        return {"ok": False, "error": "invalid credential mode",
                "error_class": "invalid_args"}
    if browser_mode not in {"headless", "side"}:
        return {"ok": False, "error": "invalid browser mode",
                "error_class": "invalid_args"}
    from playwright_sidecar import stealth as _st
    if _st.unknown_techniques(stealth_techniques):
        return {"ok": False, "error": "invalid stealth techniques",
                "error_class": "invalid_args"}
    requested_techniques = _st.normalize_selection(
        stealth_techniques or ())
    try:
        split = urllib.parse.urlsplit(url)
    except ValueError:
        split = None
    if (split is None or split.scheme.lower() not in ("http", "https")
            or not split.hostname or split.username or split.password):
        return {"ok": False, "error": "http(s) url required",
                "error_class": "invalid_url"}
    if allowlist_arg is not None and not isinstance(allowlist_arg, list):
        return {"ok": False, "error": "allowlist must be a list of hosts",
                "error_class": "invalid_args"}
    invalid_hosts = [h for h in (allowlist_arg or [])
                     if not isinstance(h, str) or not _canonical_host(h)]
    if invalid_hosts:
        return {"ok": False, "error": "allowlist contains an invalid host",
                "error_class": "invalid_args"}
    allowlist = _default_allowlist(url, allowlist_arg)
    default_host = _canonical_host(split.hostname or "")
    task_binding = None
    credential_binding = None
    if task_name:
        if not isinstance(task_name, str) or len(task_name) > 160:
            return {"ok": False, "error": "invalid task mandate",
                    "error_class": "mandate_scope_exceeded"}
        task_binding = task_mandates.sites_binding(
            task_name, owner, default_host)
        if not isinstance(task_binding, dict):
            return {"ok": False, "error": "task has no sites mandate",
                    "error_class": "mandate_scope_exceeded"}
    elif credential_mode == "default":
        credential_binding = credential_mandates.resolve_sites_binding(
            owner, default_host)
    authority_binding = task_binding or credential_binding
    permitted_hosts = set()
    if authority_binding is not None:
        permitted_hosts = {
            _canonical_host(str(host))
            for host in (authority_binding.get("allowed_hosts") or [])
        }
        permitted_hosts.discard("")
        if default_host not in permitted_hosts:
            return {"ok": False, "error": "host outside task mandate",
                    "error_class": "mandate_scope_exceeded",
                    "required_hosts": sorted(allowlist - permitted_hosts)}
        if task_binding is not None and not allowlist.issubset(permitted_hosts):
            return {"ok": False, "error": "host outside task mandate",
                    "error_class": "mandate_scope_exceeded",
                    "required_hosts": sorted(allowlist - permitted_hosts)}
        allowlist.update(permitted_hosts)
    if len(allowlist) > _MAX_ALLOWLIST_HOSTS:
        return {"ok": False, "error": "allowlist host limit exceeded",
                "error_class": "allowlist_limit",
                "max_hosts": _MAX_ALLOWLIST_HOSTS}
    extras = sorted(h for h in allowlist
                    if h != default_host and h not in permitted_hosts)
    now = time.time()
    for token, pending in list(_pending_opens.items()):
        if now - float(pending.get("created", 0)) > _OPEN_APPROVAL_TTL_S:
            _pending_opens.pop(token, None)
    if extras and task_binding is None:
        expected = {
            "owner": owner, "url": url, "allowlist": tuple(sorted(allowlist)),
            "session_label": session_label or "",
            "credential_mode": credential_mode,
            "stealth": bool(stealth),  # fix #8: binding modalita'
            "stealth_techniques": requested_techniques,
            "browser_mode": browser_mode,
        }
        if not approval_token:
            return _new_open_approval(
                owner=owner, url=url, allowlist=allowlist,
                session_label=session_label, extra_hosts=set(extras),
                error="allowlist extension requires approval",
                credential_mode=credential_mode, stealth=bool(stealth),
                stealth_techniques=requested_techniques,
                browser_mode=browser_mode)
        pending = _pending_opens.pop(str(approval_token), None)
        if not pending or any(pending.get(k) != v for k, v in expected.items()):
            return {"ok": False, "error": "invalid allowlist approval",
                    "error_class": "approval_invalid"}
    blocked_requests: dict[str, dict] = {}
    # ADR 0191 P1: il master e il ceiling delimitano l'insieme selezionato.
    # La superficie e' indipendente dalle tecniche. Solo LAUNCH sceglie la
    # variante WebDriver della superficie; CONTEXT/BEHAVIOR non la implicano.
    ceiling_allows = _stealth_allowed()
    effective_techniques = (
        requested_techniques if bool(stealth) and ceiling_allows else ())
    if stealth and requested_techniques and not ceiling_allows:
        try:
            sites_audit.record("stealth_denied_by_ceiling", owner=owner)
        except Exception:
            pass
    if _st.technique_enabled(
            "reuse_live_session", techniques=effective_techniques):
        reused = await _reuse_compatible_session(
            owner=owner, open_host=default_host, allowlist=allowlist,
            session_label=session_label, credential_mode=credential_mode,
            browser_mode=browser_mode,
            stealth_techniques=effective_techniques,
            task_binding=task_binding, credential_binding=credential_binding)
        if reused is not None:
            return reused
    # Il riuso non consuma un nuovo context. Le quote si applicano soltanto
    # quando serve davvero creare una nuova sessione.
    if len(_sessions) >= _MAX_CONTEXTS:
        return {"ok": False, "error": "max concurrent sessions reached",
                "error_class": "capacity"}
    owner_count = sum(1 for e in _sessions.values() if e.get("owner") == owner)
    if owner_count >= _PER_USER_QUOTA:
        return {"ok": False, "error": "per-user session quota reached",
                "error_class": "quota_exceeded"}
    try:
        browser = await _browser_provider(
            browser_mode,
            _st.launch_browser_required(effective_techniques))
    except Exception as exc:  # noqa: BLE001
        error_class = ("side_browser_unavailable"
                       if "side_browser" in str(exc)
                       else "browser_unavailable")
        return {"ok": False, "error": f"browser unavailable: {exc}",
                "error_class": error_class}
    context = await browser.new_context(
        **_context_kwargs(
            stealth_techniques=effective_techniques, lang=lang,
            browser_version=str(getattr(browser, "version", "") or "")))
    # FIX D: WebRTC off + route-guard per-sessione.
    try:
        await context.add_init_script(_WEBRTC_OFF_JS)
        # Occultamento OPT-IN, default OFF (ADR 0191): il default non nasconde
        # l'automazione. Il layer CONTEXT stealth (init-JS) e' applicato solo su
        # richiesta effettiva; il webdriver-hiding vive nel LAUNCH (browser stealth).
        for _js in _st.context_init_scripts(
                techniques=effective_techniques):
            await context.add_init_script(_js)
        await context.route(
            "**/*", _make_route_guard(allowlist, blocked_requests))
        if hasattr(context, "route_web_socket"):
            async def _ws_guard(ws):
                host = _host_of_url(ws.url)
                if host not in allowlist:
                    await ws.close()
            await context.route_web_socket("**/*", _ws_guard)
    except Exception as e:
        await context.close()
        return {"ok": False, "error": f"context setup failed: {e}",
                "error_class": "unknown"}

    page = await context.new_page()
    navigation_error = None
    nav_response = None
    try:
        nav_response = await asyncio.wait_for(
            page.goto(url, wait_until="load", timeout=int(_OP_TIMEOUT_S * 1000)),
            timeout=_OP_TIMEOUT_S)
    except asyncio.TimeoutError:
        navigation_error = {
            "ok": False, "error": "navigation timeout", "error_class": "timeout"}
    except Exception as e:
        navigation_error = {
            "ok": False, "error": f"navigation failed: {e}",
            "error_class": "network"}

    # ADR 0191 P4: codice osservativo dallo status HTTP della navigazione
    # (429/403/5xx). Slug STABILE, mai `automation_blocked` dedotto.
    _sig = sites_observed.response_signals(nav_response)
    observed_reason = sites_observed.observational_reason(
        status=_sig["status"], retry_after=_sig["retry_after"])

    if navigation_error is None and hasattr(page, "wait_for_timeout"):
        # Non aspetta network-idle, che siti con polling possono non
        # raggiungere mai.
        await _settle_resource_discovery(page)

    # Redirect e subresource interattivi fuori confine non producono una pagina
    # parziale: il context viene chiuso e l'insieme esatto osservato passa da un
    # gate. Un replay puo' scoprire un ulteriore livello, sempre con nuovo gate.
    final_host = _host_of_url(page.url)
    # Prima ammetti soltanto origini di navigazioni DOCUMENTO top-level. Un
    # document di subframe terzo (adv/telemetria) resta abortito e osservato,
    # ma non puo' promuoversi da solo a gate. Script/API/subframe vengono
    # valutati solo piu' tardi con evidenza causale del target richiesto.
    discovered = {
        host for host, observation in blocked_requests.items()
        if host not in allowlist
        and "document" in (observation.get("types") or ())
        and bool(observation.get("navigation"))
        and bool(observation.get("main_frame"))
    }
    if final_host and final_host not in allowlist:
        discovered.add(final_host)
    if discovered:
        expanded = set(allowlist)
        expanded.update(discovered)
        await context.close()
        if len(expanded) > _MAX_ALLOWLIST_HOSTS:
            return {"ok": False, "error": "allowlist host limit exceeded",
                    "error_class": "allowlist_limit",
                    "max_hosts": _MAX_ALLOWLIST_HOSTS,
                    "required_hosts": sorted(discovered)}
        redirect_url = (scrub_url(page.url)
                        if final_host and final_host not in allowlist else "")
        if task_binding is not None:
            return {"ok": False, "error": "observed host outside task mandate",
                    "error_class": "mandate_scope_exceeded",
                    "required_hosts": sorted(discovered),
                    **({"redirect_url": redirect_url} if redirect_url else {})}
        return _new_open_approval(
            owner=owner, url=url, allowlist=expanded,
            session_label=session_label, extra_hosts=discovered,
            error="observed hosts require allowlist approval",
            credential_mode=credential_mode, stealth=bool(stealth),
            stealth_techniques=requested_techniques,
            browser_mode=browser_mode,
            redirect_url=redirect_url, blocked_requests=blocked_requests)

    if navigation_error is None:
        browser_error = _browser_navigation_failure(
            getattr(page, "url", ""))
        if browser_error:
            navigation_error = {
                "ok": False,
                "error": "browser committed an internal navigation error",
                "error_class": "navigation_failed",
                "reason_code": "navigation_failed",
                "detail": browser_error,
            }

    if navigation_error is not None:
        await context.close()
        return navigation_error

    session_id = secrets.token_hex(16)
    # L'host osservato puo' essere un alias gia' verificato (tipicamente
    # ``www``). Il root del mandato resta invece l'handle stabile del vault e
    # dell'audit, evitando di frammentare credenziali e topologia per alias.
    binding_root = _canonical_host(str(
        (authority_binding or {}).get("root_host") or ""))
    # ADR 0191 P2: candidate discovery del record vault. Con un mandato, il
    # root_host e' l'handle canonico. Senza mandato, `legacy_storage_candidate`
    # ripiega SOLO `www.D->D` per TROVARE il record legacy (il fold non vive piu'
    # in `_load_site_credentials`); l'autorizzazione al fill resta a
    # `credential_origins`. Nota: candidate discovery, NON autorizzazione.
    domain = binding_root or credential_injection.legacy_storage_candidate(
        _host_of_url(url))
    _sessions[session_id] = {
        "context": context, "page": page, "allowlist": allowlist,
        # ADR 0191 P1: surface owner-bound + selezione FISSATA all'open per tutta
        # la sessione (il replay gate la riusa, non la ricalcola).
        "surface": browser_surface.PlaywrightSurface(
            context, page, browser_mode=browser_mode,
            stealth_techniques=effective_techniques),
        "browser_mode": browser_mode,
        "stealth": bool(effective_techniques),
        "stealth_techniques": effective_techniques,
        "owner": owner, "domain": domain, "label": session_label or "",
        "open_host": default_host,
        # Internal-only recovery anchor. It may contain a query string and
        # therefore never leaves broker memory or enters audit un-scrubbed.
        "entry_url": page.url,
        "created": time.time(), "last_used": time.time(),
        "gate_pending": False, "factor_pending": False,
        "authenticated": False,
        "web_content_ingested": True, "pending_actions": {},
        "completed_approvals": {},
        "approved_actions": set(), "secret_pending": False,
        "blocked_requests": blocked_requests,
        "reveal_attempts": set(),
        "action_replans": {},
        "goal_flows": {},
        "task_mandate": task_binding,
        "credential_mandate": credential_binding,
        "credential_mode": credential_mode,
        "observed_reason": observed_reason,  # ADR 0191 P4 (slug o None)
        "lock": asyncio.Lock(),
    }
    try:
        title = await page.title()
    except Exception:
        title = ""
    sites_audit.record("session_open", owner=owner, session_id=session_id,
                       domain=domain, url=page.url, allowlist=sorted(allowlist),
                       **({"reason": observed_reason} if observed_reason else {}))
    return {"ok": True, "session_id": session_id, "url": scrub_url(page.url),
            "title": title,
            **({"reason_code": observed_reason} if observed_reason else {})}


async def _capture_screenshot(entry: dict) -> str | None:
    """Cattura uno screenshot REDATTO (§3.3). Ritorna il path (0600) o None.
    §3.2 CRITICO-3: la redazione avviene PRIMA del capture; se fallisce, NON
    si cattura (fail-closed)."""
    page = entry["page"]
    owner = entry["owner"]
    # CRITICO-3: anche con overlay, nessun capture fra fill credenziale e
    # submit. Questo flag viene azzerato solo dopo submit/navigazione.
    if entry.get("secret_pending"):
        return None
    redacted = await redaction.apply_redaction(page)
    if redacted < 0:
        return None  # redazione fallita → mai catturare (fail-closed)
    _sweep_old_shots(owner)
    d = _shots_dir(owner)
    fname = f"{entry.get('_sid','s')}_{int(time.time()*1000)}.png"
    path = d / fname
    try:
        # Le coordinate degli overlay di redazione sono viewport-relative.
        # full_page=True disallineerebbe gli overlay: deve restare False.
        mask = [page.locator(
            'input[type=password], input[type=email], '
            'input[autocomplete="username" i], '
            'input[autocomplete="email" i], '
            'input[autocomplete="one-time-code" i], '
            'input[name*="otp" i], input[id*="otp" i], '
            'input[name*="verification" i], input[id*="verification" i], '
            '[data-metnos-redact="1"]')]
        await page.screenshot(path=str(path), full_page=False, mask=mask,
                              mask_color="#000000")
        path.chmod(0o600)
    except Exception:
        return None
    return str(path)


async def op_read(*, session_id: str, owner: str | None = None,
                  include_screenshot: bool = True,
                  include_forms: bool = False) -> dict:
    entry, validation_error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "error": validation_error,
                "error_class": validation_error}
    async with entry["lock"]:
        try:
            return await asyncio.wait_for(
                _read_impl(entry, session_id, include_screenshot, include_forms),
                timeout=_OP_TIMEOUT_S)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "read timeout", "error_class": "timeout"}


async def _read_impl(entry, session_id, include_screenshot, include_forms) -> dict:
    page = entry["page"]
    entry["web_content_ingested"] = True
    await _touch(entry)
    entry["_sid"] = session_id
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""
    collected = [item for item in (entry.get("collected_pages") or [])
                 if isinstance(item, dict) and item.get("text")]
    if collected:
        chunks = []
        seen_text = set()
        for item in [*collected, {"url": scrub_url(page.url), "text": text}]:
            value = str(item.get("text") or "")
            key = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if value and key not in seen_text:
                seen_text.add(key)
                chunks.append(value)
        text = "\n\n".join(chunks)
    sensitive = bool(entry.get("authenticated"))
    shot = None
    if include_screenshot:
        shot = await _capture_screenshot(entry)
    out = {
        "ok": True, "session_id": session_id, "url": scrub_url(page.url),
        "title": title, "text": text, "sensitive": sensitive,
    }
    if collected:
        out["collected_page_count"] = len(collected) + 1
    if include_forms:
        try:
            raw_forms = await page.evaluate(_ENUMERATE_FORMS_JS)
        except Exception:
            raw_forms = []
        forms = []
        for form in raw_forms if isinstance(raw_forms, list) else []:
            if not isinstance(form, dict):
                continue
            forms.append({
                "index": form.get("index"),
                "method": form.get("method") or "GET",
                "action": scrub_url(form.get("action") or ""),
                "fields": [field for field in (form.get("fields") or [])
                           if isinstance(field, dict)],
            })
        out["forms"] = forms
    if shot:
        out["screenshot_path"] = shot
    return out


async def op_screenshot(*, session_id: str, owner: str | None = None) -> dict:
    entry, validation_error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "error": validation_error,
                "error_class": validation_error}
    async with entry["lock"]:
        entry["_sid"] = session_id
        await _touch(entry)
        shot = await _capture_screenshot(entry)
        if not shot:
            return {"ok": False, "error": "capture failed",
                    "error_class": "screenshot_failed"}
        return {"ok": True, "session_id": session_id, "screenshot_path": shot,
                "sensitive": bool(entry.get("authenticated"))}


async def op_login(*, session_id: str, owner: str | None = None,
                   domain: str | None = None,
                   form_hint: str | None = None,
                   approval_token: str | None = None,
                   one_time_code: str | None = None,
                   credential_mode: str = "default") -> dict:
    entry, validation_error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "logged_in": False,
                "reason_code": validation_error, "error": validation_error,
                "error_class": validation_error}
    # domain default = origine della sessione (verificata poi in §3.2).
    dom = (domain or entry.get("domain") or "").lower()
    async with entry["lock"]:
        if credential_mode not in {"default", "none"}:
            return {"ok": False, "logged_in": False,
                    "reason_code": "invalid_args",
                    "error_class": "invalid_args", "session_id": session_id}
        if credential_mode == "none":
            entry["credential_mode"] = "none"
            entry["credential_mandate"] = None
        if entry.get("credential_mode") == "none":
            return {"ok": True, "logged_in": False,
                    "reason_code": "credential_use_disabled",
                    "error_class": "mandate_scope_exceeded",
                    "session_id": session_id}
        if entry.get("gate_pending") and not approval_token:
            return {"ok": False, "logged_in": False,
                    "error_class": "approval_pending",
                    "reason_code": "approval_pending",
                    "session_id": session_id}
        await _touch(entry)
        entry["_sid"] = session_id
        flow = entry.get("login_flow")
        if (not isinstance(flow, dict) or flow.get("domain") != dom
                or time.time() - float(flow.get("started", 0)) > _GATE_MAX_S):
            flow = {
                "domain": dom, "started": time.time(), "steps": 0,
                "factor_state": {},
            }
            entry["login_flow"] = flow
            entry["factor_pending"] = False

        # Un token emesso durante la ricerca dell'area di login viene eseguito
        # nello stesso lock e poi la macchina riosserva la pagina. Il planner
        # non vede ne' il token ne' questi stati intermedi.
        if approval_token:
            plan = entry.get("pending_actions", {}).get(approval_token)
            if not plan:
                return {"ok": False, "logged_in": False,
                        "error_class": "approval_invalid",
                        "reason_code": "approval_invalid",
                        "session_id": session_id}
            executed = await _execute_plan(entry, approval_token, plan)
            if executed.get("error_class") in {"target_changed", "page_changed"}:
                entry.get("pending_actions", {}).pop(approval_token, None)
                entry["gate_pending"] = False
                if plan.get("kind") == "credential_origin":
                    entry.pop("login_flow", None)
                    return {"ok": True, "logged_in": False,
                            "reason_code": "origin_unverified",
                            "error_class": executed.get("error_class"),
                            "session_id": session_id}
                key = str(plan.get("replan_key") or "")
                replans = entry.setdefault("action_replans", {})
                count = int(replans.get(key, 0)) + 1
                replans[key] = count
                if count > _MAX_ACTION_REPLANS:
                    return {"ok": True, "logged_in": False,
                            "reason_code": "selector_missing",
                            "error_class": "target_unstable",
                            "session_id": session_id}
                prepared = await _prepare_action_with_resource_fallback(
                    entry, session_id,
                    str(plan.get("original_action") or "click login"),
                    plan.get("value_ref"))
                executed = await _handle_prepared_action(
                    entry, session_id,
                    str(plan.get("original_action") or "click login"),
                    prepared)
            if executed.get("approval_required"):
                executed.update({"ok": True, "logged_in": False,
                                 "session_id": session_id})
                return executed
            if not executed.get("ok"):
                entry.pop("login_flow", None)
                return {"ok": True, "logged_in": False,
                        "reason_code": "selector_missing",
                        "error_class": (executed.get("error_class")
                                        or "action_failed"),
                        "session_id": session_id}
            if executed.get("credential_origin"):
                flow["approved_origin"] = executed["credential_origin"]

        async def _reach_login_area(purpose: str = "login") -> dict:
            if int(flow.get("steps", 0)) >= _MAX_LOGIN_ENTRY_STEPS:
                return {"ok": False, "error_class": "login_step_limit"}

            async def _reject_privacy_overlay(*, settle: bool) -> bool:
                # Rimuovere un overlay privacy e' una PRECONDIZIONE per
                # raggiungere l'ingresso login, non un passo di navigazione
                # login. Usa un budget PROPRIO e piccolo: un overlay che
                # riappare (reject navigante -> reload) non deve esaurire il
                # budget d'ingresso e far scattare login_step_limit PRIMA ancora
                # di cliccare "accedi" (bug turn e69dca8e; simulatore). Bounded
                # §7.4.
                if int(flow.get("privacy_dismissals", 0)) >= _MAX_PRIVACY_DISMISSALS:
                    return False
                rejected = await _dismiss_obstructing_overlay(
                    entry, settle=settle,
                    forms=action_resolver.privacy_reject_forms(),
                    markers=action_resolver.privacy_overlay_marker_forms(),
                    procedure="privacy_reject")
                if rejected:
                    flow["privacy_dismissals"] = int(
                        flow.get("privacy_dismissals", 0)) + 1
                return rejected

            if purpose == "privacy_reject":
                if await _reject_privacy_overlay(settle=True):
                    return {"ok": True, "executed": True,
                            "primitive": "click"}
            action = {
                "login": "click login",
                "privacy_reject": "click privacy reject",
                "continue": "click login continue",
            }.get(purpose)
            if not action:
                return {"ok": False, "error_class": "unsupported_action"}
            # SPA lente: per il vero ingresso login attendi fino a due secondi
            # usando solo il resolver deterministico; il modello entra una sola
            # volta, alla fine, se il controllo resta semanticamente ignoto.
            attempts = (max(1, _REVEAL_SETTLE_MS // _REVEAL_POLL_MS)
                        if purpose == "login" else 1)
            prepared = {"ok": False, "error_class": "selector_missing"}
            for attempt in range(attempts):
                # Il banner puo' apparire dopo il primo probe privacy. Prima di
                # ogni riosservazione login rimuovilo solo se struttura, marker
                # e target esatto continuano a provarne la natura. Il ciclo e'
                # gia' bounded da `attempts` e dal limite globale dei passi.
                if purpose == "login":
                    # L'overlay puo' apparire dopo il primo probe: tentane la
                    # rimozione (budget proprio, sopra) prima di ogni
                    # riosservazione, senza consumare il budget d'ingresso.
                    await _reject_privacy_overlay(settle=False)
                prepared = await _prepare_action(
                    entry, session_id, action, None, allow_model=False)
                if (prepared.get("ok") or prepared.get("error_class")
                        not in {"selector_missing", "selector_ambiguous",
                                "target_changed"}):
                    break
                if attempt + 1 < attempts:
                    if hasattr(entry["page"], "wait_for_timeout"):
                        await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
                    else:
                        await asyncio.sleep(_REVEAL_POLL_MS / 1000)
            if (purpose == "login" and not prepared.get("ok")
                    and prepared.get("error_class") in {
                        "selector_missing", "selector_ambiguous"}):
                prepared = await _prepare_action_with_resource_fallback(
                    entry, session_id, action, None, allow_model=True)
            if prepared.get("ok"):
                (prepared.get("plan") or {})["login_flow"] = True
                (prepared.get("plan") or {})["login_procedure"] = purpose
                _apply_login_intent_grant(
                    entry, prepared, allow_submit=(purpose == "continue"))
            handled = await _handle_prepared_action(
                entry, session_id, action, prepared)
            return handled

        async def _authorize_login_origin(origin: str,
                                          form_stage: str) -> dict:
            # Fix adversarial #2: `origin` = tupla ESATTA (scheme://host:port) da
            # perform_login. Il gate/allowlist ragiona per HOST (autorizzazione di
            # rete); l'autorita' del FILL resta l'ORIGINE ESATTA, memorizzata in
            # `credential_origin`→`flow["approved_origin"]`.
            host = _canonical_host(_host_of_url(origin) or origin)
            if flow.get("approved_origin") == origin:
                return {"ok": True, "approved": True}
            prepared = _prepare_credential_origin(
                entry, session_id, dom, origin, form_stage)
            handled = await _handle_prepared_action(
                entry, session_id, host, prepared)
            if handled.get("approval_required"):
                handled.update({
                    "approval_kind": "credential_origin",
                    "vault_domain": dom, "credential_origin": origin,
                })
            return handled

        async def _login_checkpoint(stage: str) -> None:
            stage = str(stage or "")
            if not stage or flow.get("phase") == stage:
                return
            flow["phase"] = stage
            flow["phase_started"] = time.time()
            if stage in {"factor_pending", "factor_resolving",
                         "factor_submit"}:
                if not entry.get("factor_pending"):
                    entry["factor_started"] = time.time()
                entry["factor_pending"] = True
            elif stage in {"complete", "failed"}:
                entry["factor_pending"] = False
                entry.pop("factor_started", None)
            sites_audit.record(
                "login_phase", owner=entry.get("owner", ""),
                session_id=session_id, domain=dom, phase=stage)

        # Il TTL e' in pausa durante il login (puo' attendere navigazioni lente).
        entry["gate_pending"] = True
        entry["gate_started"] = time.time()
        res = None
        try:
            res = await asyncio.wait_for(
                credential_injection.perform_login(
                    page=entry["page"], context=entry["context"], domain=dom,
                    form_hint=form_hint, owner=entry["owner"],
                    session_id=session_id, op_timeout_s=_OP_TIMEOUT_S,
                    one_time_code=one_time_code,
                    reach_login=_reach_login_area,
                    authorize_origin=_authorize_login_origin,
                    approved_origin=flow.get("approved_origin"),
                    max_entry_steps=_MAX_LOGIN_ENTRY_STEPS,
                    page_provider=lambda: entry.get("page"),
                    factor_state=flow.setdefault("factor_state", {}),
                    checkpoint=_login_checkpoint,
                    total_timeout_s=_LOGIN_TIMEOUT_S,
                    stealth_techniques=entry.get(
                        "stealth_techniques", ())),
                timeout=_LOGIN_TIMEOUT_S)
        except asyncio.TimeoutError:
            blocker = await credential_injection.classify_login_surface(
                entry["page"])
            if (not blocker and flow.get("phase") in {
                    "factor_pending", "factor_resolving"}):
                blocker = "two_factor_required"
            res = {
                "ok": True, "logged_in": False,
                "reason_code": blocker or "login_timeout",
                "error_class": "timeout",
            }
        finally:
            entry["gate_pending"] = bool(
                isinstance(res, dict) and res.get("approval_required"))
            await _touch(entry)
        if res.get("logged_in"):
            entry["authenticated"] = True
        factor_reason = res.get("reason_code") in {
            "two_factor_required", "two_factor_push_required",
            "captcha_required"}
        if factor_reason and not res.get("approval_required"):
            if not entry.get("factor_pending"):
                entry["factor_started"] = time.time()
            entry["factor_pending"] = True
        elif not res.get("approval_required"):
            entry["factor_pending"] = False
            entry.pop("factor_started", None)
        # Ogni login non completato deve lasciare evidenza diagnostica
        # redatta. La tassonomia puo' crescere senza creare buchi di
        # osservabilita'; approval resta esclusa perche' ha il proprio gate.
        if (not res.get("logged_in") and not res.get("approval_required")):
            shot = await _capture_screenshot(entry)
            if shot:
                res["screenshot_path"] = shot
                res["sensitive"] = True
        if (not res.get("approval_required") and res.get("reason_code") not in {
                "two_factor_required", "two_factor_push_required",
                "captcha_required", "login_timeout", "selector_missing"}):
            entry.pop("login_flow", None)
        res["session_id"] = session_id
        return res


async def op_close(*, session_id: str | None = None, owner: str | None = None,
                   close_all: bool = False) -> dict:
    """Chiude UNA sessione o TUTTE quelle dell'owner (§9 kill-switch)."""
    if not isinstance(owner, str) or not owner:
        return {"ok": False, "error": "owner required",
                "error_class": "forbidden", "closed": [], "count": 0}
    closed = []
    if close_all:
        for sid, e in list(_sessions.items()):
            if e.get("owner") == owner:
                _sessions.pop(sid, None)
                await _close_entry(e)
                closed.append(sid)
                sites_audit.record("session_close", owner=e.get("owner", ""),
                                   session_id=sid, domain=e.get("domain", ""),
                                   kill_switch=True)
        return {"ok": True, "closed": closed, "count": len(closed)}
    if not session_id:
        return {"ok": False, "error": "session_id or close_all required",
                "error_class": "invalid_args"}
    e = _sessions.pop(session_id, None)
    if e is None:
        # Idempotente: chiudere una sessione già morta è ok (onesto: count 0).
        return {"ok": True, "closed": [], "count": 0}
    if e.get("owner") != owner:
        # Non chiudere sessioni di un altro owner: rimetti e rifiuta.
        _sessions[session_id] = e
        return {"ok": False, "error": "not owner", "error_class": "forbidden"}
    await _close_entry(e)
    sites_audit.record("session_close", owner=e.get("owner", ""),
                       session_id=session_id, domain=e.get("domain", ""))
    return {"ok": True, "closed": [session_id], "count": 1}


# ── F2: azioni tipizzate, risoluzione target e gate HITL ───────────────────

def _candidate_signature(candidate: dict | None) -> str:
    c = candidate or {}
    stable = {k: c.get(k) for k in (
        "id", "tag", "type", "name", "href", "download", "form_action",
        "form_method", "secret_input", "disabled", "topmost", "rect")}
    stable.update({k: c.get(k) for k in (
        "rendered", "visible", "in_viewport", "aria_expanded",
        "aria_selected", "aria_pressed", "aria_checked", "aria_current",
        "checked")})
    return json.dumps(stable, sort_keys=True, ensure_ascii=True)


def _page_signature(url: str) -> str:
    """Firma l'URL completo senza conservarlo nel piano o nei log."""
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def _action_destination(primitive: str, target: str,
                        candidate: dict | None) -> tuple[str, str]:
    """Ritorna ``(url_redatto, host_esatto)`` per una possibile navigazione.

    La destinazione deriva solo dal target broker-owned (o dall'URL tipizzato
    di ``goto``), mai da un selettore del planner. Query e fragment sensibili
    vengono redatti prima di finire nel gate o nell'audit.
    """
    c = candidate or {}
    raw = target if primitive == "goto" else (
        c.get("href") or c.get("form_action") or "")
    if not isinstance(raw, str) or not raw:
        return "", ""
    try:
        split = urllib.parse.urlsplit(raw)
    except ValueError:
        return "", ""
    if (split.scheme.lower() not in ("http", "https") or not split.hostname
            or split.username or split.password):
        return "", ""
    return scrub_url(raw), _canonical_host(split.hostname or "")


async def _enumerate_candidates(page) -> list[dict]:
    try:
        out = await asyncio.wait_for(
            page.evaluate(_ENUMERATE_ACTION_TARGETS_JS),
            timeout=_ENUMERATE_TIMEOUT_MS / 1000.0,
        )
        return out if isinstance(out, list) else []
    except Exception:
        return []


async def _scroll_candidate_into_view(entry: dict, candidate: dict) -> bool:
    cid = str(candidate.get("id") or "")
    if not cid:
        return False
    try:
        handle = await entry["page"].locator(
            f'[data-metnos-action-id="{cid}"]').first.element_handle()
        if handle is None:
            return False
        await handle.scroll_into_view_if_needed(timeout=1500)
        if hasattr(entry["page"], "wait_for_timeout"):
            await entry["page"].wait_for_timeout(100)
        return True
    except Exception:
        return False


def _bounded_action_prompt(*, goal: dict, state: dict,
                           observed: list[str], history: list[str],
                           forbidden: str) -> str:
    """Prompt chiuso per risolvere una sola azione elementare.

    I testi accessibili arrivano da una pagina non fidata: serializzarli come
    dati e ribadire il confine impedisce che diventino istruzioni operative.
    La primitiva e' gia' fissata dal codice; il modello sceglie soltanto un ID.
    """
    import i18n
    import prompt_loader
    dump = lambda value: json.dumps(value, ensure_ascii=True, sort_keys=True)
    return prompt_loader.get(
        "agentic_sites_action", i18n.current_lang(),
        goal_json=dump(goal), state_json=dump(state),
        observed_json=dump(observed), history_json=dump(history),
        forbidden_code=forbidden,
    )


async def _vlm_choose_candidate(entry: dict, target: str,
                                ranked: list[tuple],
                                primitive: str = "click") -> dict | None:
    """Fallback locale-only. Il VLM sceglie un id tra candidati broker-owned;
    non produce mai CSS/XPath. Su errore o risposta dubbia fallisce chiuso."""
    if (not _MODEL_FALLBACKS_ENABLED or not ranked or entry.get("authenticated")
            or entry.get("secret_pending")):
        return None
    entry["_sid"] = entry.get("_sid") or "action"
    shot = await _capture_screenshot(entry)
    if not shot:
        return None
    choices = []
    by_id = {}
    for _score, candidate in ranked[:24]:
        cid = str(candidate.get("id") or "")
        if cid:
            by_id[cid] = candidate
            choices.append(f"{cid}: {candidate.get('role') or candidate.get('tag')} "
                           f"{candidate.get('name') or candidate.get('label')}")
    if not choices:
        return None
    from agentic_executor import AgenticContext, AgenticLimits, AgenticProposal, run_bounded
    context = AgenticContext(
        goal={"primitive": primitive, "target": target},
        observed=choices,
        constraints={"forbidden": "different_primitive"},
        history=["deterministic_resolution_insufficient"],
    )

    async def propose(ctx):
        prompt = _bounded_action_prompt(
            goal=ctx.goal,
            state={"authenticated": False,
                   "url": scrub_url(entry["page"].url)},
            observed=ctx.observed,
            history=ctx.history,
            forbidden=ctx.constraints["forbidden"],
        )
        try:
            import vlm_client
            result = await asyncio.to_thread(vlm_client.describe_image, shot,
                                             prompt=prompt, max_tokens=64)
        except Exception:
            return None
        selected = str((result or {}).get("description") or "").strip()
        return AgenticProposal(selected)

    async def execute(proposal, _ctx):
        return by_id.get(str(proposal.action))

    outcome = await run_bounded(
        context=context, propose=propose, execute=execute,
        validate=lambda proposal, _ctx: str(proposal.action) in by_id,
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: result is not None,
    )
    return outcome.result


async def _page_has_transient_loading(entry: dict) -> bool:
    try:
        observed = await asyncio.wait_for(
            entry["page"].evaluate(
                _TRANSIENT_LOADING_JS,
                list(action_resolver.loading_marker_forms())),
            timeout=0.5)
        return observed is True
    except Exception:
        return False


async def _wait_for_content_settle(entry: dict) -> bool:
    """Attende che uno stato di caricamento visibile scompaia.

    Nessuna attesa viene introdotta sulle pagine gia' stabili. Se il marker
    resta visibile oltre il budget, il goal non viene dichiarato completo.
    """
    if not await _page_has_transient_loading(entry):
        return True
    deadline = _monotonic() + _CONTENT_SETTLE_MS / 1000.0
    while _monotonic() < deadline:
        page = entry["page"]
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(_REVEAL_POLL_MS)
        else:
            await asyncio.sleep(_REVEAL_POLL_MS / 1000)
        if not await _page_has_transient_loading(entry):
            return True
    return False


async def _expand_collection_by_scrolling(entry: dict, flow: dict) -> bool:
    """Carica porzioni lazy di una collezione con scroll progressivo bounded."""
    if (not flow.get("collection") or flow.get("collection_scroll_complete")):
        return False
    changed_any = False
    while int(flow.get("collection_scrolls", 0)) < _MAX_COLLECTION_SCROLLS:
        before = await _goal_content_signature(entry)
        try:
            scroll = await entry["page"].evaluate(_SCROLL_COLLECTION_JS)
        except Exception:
            break
        if not isinstance(scroll, dict) or not scroll.get("moved"):
            flow["collection_scroll_complete"] = True
            break
        flow["collection_scrolls"] = int(
            flow.get("collection_scrolls", 0)) + 1
        progressed, _intermediate = await _wait_for_goal_content_change(
            entry, before)
        await _wait_for_content_settle(entry)
        after = await _goal_content_signature(entry)
        if not progressed and after == before:
            flow["collection_scroll_complete"] = True
            break
        changed_any = changed_any or after != before
    if int(flow.get("collection_scrolls", 0)) >= _MAX_COLLECTION_SCROLLS:
        flow["collection_scroll_complete"] = True
    return changed_any


async def _page_satisfies_goal(entry: dict, target: str,
                               candidates: list[dict] | None = None) -> bool:
    if not await _wait_for_content_settle(entry):
        return False
    try:
        evidence = await entry["page"].evaluate(_GOAL_EVIDENCE_JS)
    except Exception:
        evidence = []
    try:
        body_text = await entry["page"].locator("body").inner_text(timeout=1500)
    except Exception:
        return False
    scope = scrub_url(entry["page"].url)
    if (isinstance(evidence, list)
            and action_resolver.page_satisfies_goal(
                target, evidence, scope_text=scope)):
        return True
    interactive_labels = {
        action_resolver.normalize(str(
            candidate.get("name") or candidate.get("label") or ""))
        for candidate in (candidates or ())
    }
    filtered_lines = []
    for line in str(body_text or "").splitlines():
        normalized_line = action_resolver.normalize(line)
        for label in sorted(interactive_labels, key=len, reverse=True):
            if label:
                normalized_line = re.sub(
                    rf"(?:^|\s){re.escape(label)}(?=\s|$)",
                    " ", normalized_line)
        normalized_line = " ".join(normalized_line.split())
        if normalized_line:
            filtered_lines.append(normalized_line)
    # A selected tab/filter or an expanded disclosure is browser-owned state,
    # not an incidental control label.  Combine that narrow evidence with the
    # page scope: e.g. /mytrips + aria-selected="true" on "Passate".
    for candidate in (candidates or ()):
        active_label = action_resolver.active_goal_control_label(candidate)
        if active_label:
            filtered_lines.append(active_label)
    return action_resolver.page_satisfies_goal(
        target, filtered_lines, scope_text=scope)


async def _goal_content_signature(entry: dict) -> str:
    try:
        evidence = await entry["page"].evaluate(_GOAL_EVIDENCE_JS)
    except Exception:
        evidence = []
    if not isinstance(evidence, list):
        evidence = []
    try:
        body = await entry["page"].locator("body").inner_text(timeout=1500)
    except Exception:
        body = ""
    # The strict goal evidence excludes interactive rows; the broader body
    # signature detects newly appended links without using them as proof that
    # the goal itself was satisfied.
    payload = [scrub_url(entry["page"].url), evidence[:400],
               str(body or "")[:50000]]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


async def _wait_for_goal_content_change(entry: dict, before: str) -> tuple[bool, str]:
    attempts = max(1, _REVEAL_SETTLE_MS // _REVEAL_POLL_MS)
    current = before
    for _ in range(attempts):
        if hasattr(entry["page"], "wait_for_timeout"):
            await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
        else:
            await asyncio.sleep(_REVEAL_POLL_MS / 1000)
        current = await _goal_content_signature(entry)
        if current != before:
            return True, current
    return False, current


async def _continuation_snapshot(entry: dict) -> dict:
    page = entry["page"]
    try:
        text = await page.locator("body").inner_text(timeout=1500)
    except Exception:
        text = ""
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {"url": scrub_url(page.url), "title": title,
            "text": str(text or "")[:100000]}


def _parse_reduced_site_goal(raw: str, query: str) -> str:
    """Valida il fine locale come frase estrattiva, mai come nuova autorita'."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return ""
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return ""
    goal = str(payload.get("goal") or "").strip() \
        if isinstance(payload, dict) else ""
    normalized_goal = action_resolver.normalize(goal)
    goal_tokens = normalized_goal.split()
    query_tokens = set(action_resolver.normalize(query).split())
    if (not goal_tokens or len(goal_tokens) > 6 or len(goal) > 120
            or any(char in goal for char in ("/", "#", "[", "]", "=", ">"))
            or any(token not in query_tokens for token in goal_tokens)):
        return ""
    restored = action_resolver.preserve_goal_qualifiers(
        query, goal, max_words=6)
    if not restored:
        return ""
    # La postcondizione resta estrattiva: anche i qualificatori ripristinati
    # provengono dalla query, mai dal modello o da un vocabolario operativo.
    if any(token not in query_tokens for token in restored.split()):
        return ""
    return restored


async def _reduce_site_goal(query: str) -> str:
    """Riduce la richiesta al contenitore da raggiungere con un solo LLM locale."""
    if not _MODEL_FALLBACKS_ENABLED:
        return ""
    bounded_query = str(query or "").strip()[:2000]
    if not bounded_query:
        return ""

    def _call_local() -> str:
        try:
            import i18n
            import prompt_loader
            from llm_router import LLMRouter

            provider = LLMRouter().provider("fast")
            if getattr(provider, "mode", "") != "local":
                return ""
            prompt = prompt_loader.get(
                "sites_goal_reducer", i18n.current_lang(),
                query_json=json.dumps(bounded_query, ensure_ascii=False))
            result = provider.chat(
                prompt, "", max_tokens=64, temperature=0, think=False)
            return str(getattr(result, "text", "") or "")
        except Exception:
            return ""

    from agentic_executor import AgenticContext, AgenticLimits, AgenticProposal, run_bounded
    context = AgenticContext(
        goal={"operation": "extract_navigation_goal"},
        observed={"query": bounded_query},
        constraints={"extractive_only": True},
    )

    async def propose(_ctx):
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_call_local),
                timeout=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0)
        except asyncio.TimeoutError:
            return None
        reduced = _parse_reduced_site_goal(raw, bounded_query)
        return AgenticProposal(reduced) if reduced else None

    async def execute(proposal, _ctx):
        return str(proposal.action)

    outcome = await run_bounded(
        context=context, propose=propose, execute=execute,
        validate=lambda proposal, _ctx: bool(str(proposal.action).strip()),
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: bool(result),
    )
    return str(outcome.result or "")


async def _local_llm_choose_goal_candidate(entry: dict, target: str,
                                           candidates: list[dict],
                                           history: list[str],
                                           excluded: set[str]) -> dict | None:
    """Fallback testuale locale per un passo di navigazione autenticato.

    Il modello vede solo ID e nomi accessibili enumerati dal broker. Non vede
    DOM, valori dei campi, screenshot, URL di destinazione o credenziali; la
    scelta resta un ID esatto e passa comunque dal gate se non deterministica.
    """
    if not _MODEL_FALLBACKS_ENABLED:
        return None
    eligible = action_resolver.goal_navigation_candidates(
        candidates, excluded=excluded)
    eligible = [candidate for candidate in eligible
                if action_resolver.goal_candidate_is_admissible(
                    target, candidate)]
    eligible = action_resolver.prefer_verifiable_goal_candidates(eligible)
    by_id = {}
    observed = []
    for candidate in eligible[:24]:
        cid = str(candidate.get("id") or "")
        if not cid:
            continue
        by_id[cid] = candidate
        observed.append(
            f"{cid}: {candidate.get('role') or candidate.get('tag')} "
            f"{candidate.get('name') or candidate.get('label') or ''}")
    if not observed:
        return None
    from agentic_executor import AgenticContext, AgenticLimits, AgenticProposal, run_bounded
    context = AgenticContext(
        goal={"primitive": "navigate_toward_goal", "target": target},
        observed=observed,
        constraints={
            "forbidden": "unrelated_control",
        },
        history=history[-_MAX_GOAL_STEPS:],
    )

    def _call_local(prompt: str) -> str:
        try:
            from llm_router import LLMRouter
            provider = LLMRouter().provider("fast")
            if getattr(provider, "mode", "") != "local":
                return ""
            import i18n
            import prompt_loader
            system_prompt = prompt_loader.get(
                "agentic_sites_action_system", i18n.current_lang())
            result = provider.chat(
                system_prompt, prompt,
                max_tokens=64, temperature=0, think=False)
            return str(getattr(result, "text", "") or "")
        except Exception:
            return ""

    async def propose(ctx):
        nonlocal_prompt = _bounded_action_prompt(
            goal=ctx.goal,
            state={"authenticated": bool(entry.get("authenticated")),
                   "url": scrub_url(entry["page"].url)},
            observed=ctx.observed,
            history=ctx.history,
            forbidden=ctx.constraints["forbidden"],
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_call_local, nonlocal_prompt),
                timeout=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0,
            )
        except asyncio.TimeoutError:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1]).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                payload = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
        if not isinstance(payload, dict):
            return None
        return AgenticProposal(str(payload.get("description") or "").strip())

    async def execute(proposal, _ctx):
        return by_id.get(str(proposal.action))

    outcome = await run_bounded(
        context=context, propose=propose, execute=execute,
        validate=lambda proposal, _ctx: str(proposal.action) in by_id,
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: result is not None,
    )
    return outcome.result


async def _vlm_choose_reveal_candidate(entry: dict, target: str,
                                       candidates: list[dict]) -> dict | None:
    """Fallback visuale confinato per un target gia' trovato ma nascosto.

    Il VLM puo' scegliere soltanto un controllo visibile non-submit enumerato
    dal broker. Quel controllo non completa l'azione: dopo il gate viene
    ricercato di nuovo il target originale.
    """
    if (not _MODEL_FALLBACKS_ENABLED or entry.get("authenticated")
            or entry.get("secret_pending")):
        return None
    eligible = []
    for candidate in candidates:
        tag = str(candidate.get("tag") or "").lower()
        role = str(candidate.get("role") or "").lower()
        typ = str(candidate.get("type") or "").lower()
        if (tag != "button" and role != "button") or typ == "submit":
            continue
        if (candidate.get("disabled") or candidate.get("visible") is False
                or candidate.get("in_viewport") is False
                or candidate.get("topmost") is False):
            continue
        eligible.append(candidate)
    if not eligible:
        return None
    entry["_sid"] = entry.get("_sid") or "action"
    shot = await _capture_screenshot(entry)
    if not shot:
        return None
    by_id = {}
    choices = []
    for candidate in eligible[:12]:
        cid = str(candidate.get("id") or "")
        if not cid:
            continue
        rect = candidate.get("rect") or {}
        by_id[cid] = candidate
        choices.append(
            f"{cid}: name={candidate.get('name') or ''!s} "
            f"role={candidate.get('role') or candidate.get('tag')} "
            f"x={rect.get('x')} y={rect.get('y')} "
            f"w={rect.get('width')} h={rect.get('height')}")
    if not choices:
        return None
    from agentic_executor import AgenticContext, AgenticLimits, AgenticProposal, run_bounded
    context = AgenticContext(
        goal={"primitive": "click", "purpose": "reveal_target",
              "target": target},
        observed=choices,
        constraints={
            "forbidden": "non_reveal_control",
        },
        history=["target_text_not_interactable"],
    )

    async def propose(ctx):
        prompt = _bounded_action_prompt(
            goal=ctx.goal,
            state={"authenticated": False,
                   "url": scrub_url(entry["page"].url)},
            observed=ctx.observed,
            history=ctx.history,
            forbidden=ctx.constraints["forbidden"],
        )
        try:
            import vlm_client
            result = await asyncio.to_thread(
                vlm_client.describe_image, shot, prompt=prompt, max_tokens=64)
        except Exception:
            return None
        return AgenticProposal(
            str((result or {}).get("description") or "").strip())

    async def execute(proposal, _ctx):
        return by_id.get(str(proposal.action))

    outcome = await run_bounded(
        context=context, propose=propose, execute=execute,
        validate=lambda proposal, _ctx: str(proposal.action) in by_id,
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: result is not None,
    )
    return outcome.result


def _goal_candidate_diagnostics(choice: dict) -> list[dict]:
    """Return bounded, non-secret evidence for resolver failures."""
    out = []
    for score, candidate in list(choice.get("ranked") or ())[:8]:
        out.append({
            "name": str(candidate.get("name") or candidate.get("label") or "")[:160],
            "role": str(candidate.get("role") or candidate.get("tag") or "")[:32],
            "href": scrub_url(str(candidate.get("href") or "")),
            "score": round(float(score), 3),
        })
    return out


async def _textual_reveal_candidate(entry: dict, target: str,
                                    candidates: list[dict]) -> dict | None:
    """Risoluzione deterministica target-testo -> unico menu revealer."""
    try:
        body_text = await entry["page"].locator("body").inner_text(timeout=1500)
    except Exception:
        return None
    if not action_resolver.page_mentions_target(target, body_text):
        return None
    controls = [candidate for candidate in candidates
                if action_resolver.is_reveal_control(candidate)]
    return controls[0] if len(controls) == 1 else None


def _prepare_credential_origin(entry: dict, session_id: str,
                               vault_domain: str, origin: str,
                               form_stage: str) -> dict:
    """Prepara un consenso one-shot per una origine login delegata.

    Fix bug re-gate: `origin` = tupla ESATTA (scheme://host:port) ed e'
    l'AUTORITA' del fill (§3.2 #2), conservata in `exact_origin`. L'allowlist di
    rete ragiona per HOST. Restituire l'host come origine approvata rompeva il
    match esatto a valle (`normalize_entry(<host nudo>)` == None → approved set
    vuoto → gate infinito). L'origine approvata DEVE tornare esatta.
    """
    exact_origin = str(origin or "")
    host = _canonical_host(_host_of_url(exact_origin) or exact_origin)
    if not host or not exact_origin or form_stage not in {"username", "password"}:
        return {"ok": False, "error_class": "origin_mismatch"}
    allowlist = set(entry.get("allowlist") or ())
    if host not in allowlist and len(allowlist | {host}) > _MAX_ALLOWLIST_HOSTS:
        return {"ok": False, "error_class": "allowlist_limit",
                "max_hosts": _MAX_ALLOWLIST_HOSTS}
    reasons = ["credential_origin"]
    if host not in allowlist:
        reasons.append("allowlist_extension")
    plan = {
        "kind": "credential_origin", "primitive": "authorize",
        "target": host, "original_action": host,
        "exact_origin": exact_origin,
        "vault_domain": vault_domain, "form_stage": form_stage,
        "candidate": None, "candidate_sig": "",
        "page_url": scrub_url(entry["page"].url),
        "page_sig": _page_signature(entry["page"].url),
        "value_ref": None, "destination_url": "",
        "destination_host": host, "sensitive": True,
        "sensitivity_reasons": reasons,
        "confidence": 1.0, "created": time.time(),
        "replan_key": hashlib.sha256(
            f"{_page_signature(entry['page'].url)}\0{host}\0{form_stage}"
            .encode("utf-8")).hexdigest(),
    }
    plan["fingerprint"] = action_resolver.fingerprint_plan(plan)
    token = secrets.token_urlsafe(24)
    entry["pending_actions"][token] = plan
    entry["_sid"] = session_id
    return {"ok": True, "token": token, "plan": plan}


async def _dismiss_obstructing_overlay(entry: dict, *,
                                       settle: bool = False,
                                       forms: tuple[str, ...] | None = None,
                                       markers: tuple[str, ...] = (),
                                       procedure: str = "safe_exit") -> bool:
    """Dismiss a transient overlay through a safe, non-navigating exit.

    Detection combines ARIA dialog semantics with fixed-layer geometry.  The
    control must be topmost and have an exact translated safe-exit label, or
    be an X icon in the close corner of the panel.  No arbitrary page control
    is clicked and no site-specific selector crosses this boundary.
    """
    page = entry.get("page")
    if page is None:
        return False
    attempts = 4 if settle else 1
    allowed_forms = tuple(forms if forms is not None
                          else action_resolver.overlay_dismiss_forms())
    if not allowed_forms:
        return False
    for attempt in range(attempts):
        try:
            info = await page.evaluate(
                _LOCATE_SAFE_OVERLAY_DISMISS_JS,
                {"forms": list(allowed_forms), "markers": list(markers)})
            if isinstance(info, dict) and info.get("found"):
                control = page.locator(
                    '[data-metnos-overlay-dismiss="1"]').first
                await control.click(timeout=1200, no_wait_after=True)
                if hasattr(page, "wait_for_timeout"):
                    await page.wait_for_timeout(150)
                sites_audit.record(
                    "overlay_dismiss", owner=entry.get("owner", ""),
                    session_id=entry.get("_sid", ""),
                    domain=entry.get("domain", ""),
                    procedure=procedure,
                    method=str(info.get("kind") or "safe_exit"),
                    outcome=True)
                return True
            if isinstance(info, dict) and info.get("navigating_only"):
                # Fix adversarial #11: esiste un controllo di chiusura ma e'
                # NAVIGANTE/submitter → non lo clicchiamo (P5). Lo SEGNALIAMO su
                # entry (osservabile, non stallo silenzioso): un passo navigante
                # passa dal piano firmato + gate F2, mai dalla dismissione sicura.
                entry["navigating_obstruction"] = info.get("control") or {}
                sites_audit.record(
                    "overlay_navigating_control", owner=entry.get("owner", ""),
                    session_id=entry.get("_sid", ""),
                    domain=entry.get("domain", ""), procedure=procedure,
                    outcome=False)
                return False
        except Exception:
            pass
        if settle and attempt + 1 < attempts:
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(_REVEAL_POLL_MS)
            else:
                await asyncio.sleep(_REVEAL_POLL_MS / 1000)
    return False


async def _dismiss_privacy_obstruction(entry: dict, *,
                                       settle: bool = False) -> bool:
    """Reject a privacy overlay as a bounded precondition for any action."""
    count = int(entry.get("privacy_action_dismissals", 0))
    if count >= _MAX_PRIVACY_DISMISSALS:
        return False
    dismissed = await _dismiss_obstructing_overlay(
        entry, settle=settle,
        forms=action_resolver.privacy_reject_forms(),
        markers=action_resolver.privacy_overlay_marker_forms(),
        procedure="privacy_reject")
    if dismissed:
        entry["privacy_action_dismissals"] = count + 1
    return dismissed


async def _prepare_action(entry: dict, session_id: str, action: str,
                          value_ref: str | None, primitive_override: str | None = None,
                          target_override: str | None = None,
                          allow_model: bool = True) -> dict:
    await _dismiss_privacy_obstruction(entry)
    await _dismiss_obstructing_overlay(entry)
    parsed = action_resolver.parse_action(action)
    if primitive_override:
        parsed = {"ok": True, "primitive": primitive_override,
                  "target": target_override or action, "seconds": 0,
                  "normalized": action_resolver.normalize(action)}
    if not parsed.get("ok"):
        return parsed
    primitive = parsed["primitive"]
    candidate = None
    confidence = 1.0
    ambiguous = False
    model_selected = False
    plan_kind = ""
    reveal_key = ""
    goal_flow_key = ""
    # goto/wait non hanno un elemento DOM; cred:* viene risolto esclusivamente
    # dal broker, quindi anche il fill ignora ogni target suggerito.
    if primitive == "search":
        goal_flow_key = hashlib.sha256(
            action_resolver.normalize(action).encode("utf-8")).hexdigest()
        flows = entry.setdefault("goal_flows", {})
        flow = flows.get(goal_flow_key)
        if (not isinstance(flow, dict)
                or time.time() - float(flow.get("started", 0)) > _GATE_MAX_S):
            entry.pop("collected_pages", None)
            flow = {"started": time.time(), "steps": 0, "approved": False,
                    "visited": set(), "history": [], "continuations": 0,
                    "continuation_exhausted": set(),
                    "content_signatures": set(),
                    "collection": action_resolver.is_collection_search_request(
                        action),
                    "collection_scrolls": 0,
                    "collection_scroll_complete": False}
            flows[goal_flow_key] = flow
        elif action_resolver.is_collection_search_request(action):
            flow["collection"] = True
        flow_steps = int(flow.get("steps", 0))
        at_goal_limit = flow_steps >= _MAX_GOAL_STEPS
        candidates = await _enumerate_candidates(entry["page"])
        excluded = set(flow.get("visited") or ())
        chosen = ({"ok": False, "error_class": "goal_step_limit"}
                  if at_goal_limit else action_resolver.choose_goal_candidate(
                      parsed.get("target", ""), candidates,
                      excluded=excluded))
        if not at_goal_limit and not chosen.get("ok"):
            scroll = action_resolver.choose_goal_scroll_candidate(
                parsed.get("target", ""), candidates, excluded=excluded)
            if scroll.get("ok") and await _scroll_candidate_into_view(
                    entry, scroll["candidate"]):
                candidates = await _enumerate_candidates(entry["page"])
                chosen = action_resolver.choose_goal_candidate(
                    parsed.get("target", ""), candidates,
                    excluded=excluded)
        if (not at_goal_limit and not chosen.get("ok")
                and entry.get("authenticated") and flow_steps == 0):
            chosen = action_resolver.choose_authenticated_reveal_candidate(
                candidates, excluded=excluded)
        # Il contenitore puo' essere gia' la pagina corrente (URL diretto o
        # landing utile): verificare prima evita click artificiali. La prova
        # esclude i soli label interattivi, quindi un menu omonimo non basta.
        goal_satisfied = await _page_satisfies_goal(
            entry, parsed.get("target", ""), candidates)
        continuation = {"ok": False, "error_class": "selector_missing"}
        if goal_satisfied:
            continuation_excluded = set(
                flow.get("continuation_exhausted") or ())
            continuation = action_resolver.choose_goal_continuation_candidate(
                parsed.get("target", ""), candidates,
                excluded=continuation_excluded)
            if not continuation.get("ok"):
                scroll = (
                    action_resolver.choose_goal_continuation_scroll_candidate(
                        parsed.get("target", ""), candidates,
                        excluded=continuation_excluded))
                if scroll.get("ok") and await _scroll_candidate_into_view(
                        entry, scroll["candidate"]):
                    candidates = await _enumerate_candidates(entry["page"])
                    continuation = (
                        action_resolver.choose_goal_continuation_candidate(
                            parsed.get("target", ""), candidates,
                            excluded=continuation_excluded))
            # Se non esiste un controllo esplicito, una collezione puo' essere
            # caricata progressivamente dallo scroll. Il riconoscimento della
            # richiesta viene dal lessico, lo scroll e' bounded e la route
            # guard resta invariata. Dopo l'espansione si enumerano di nuovo
            # anche eventuali pulsanti "altro/next" comparsi in fondo.
            if (not continuation.get("ok")
                    and await _expand_collection_by_scrolling(entry, flow)):
                candidates = await _enumerate_candidates(entry["page"])
                continuation = (
                    action_resolver.choose_goal_continuation_candidate(
                        parsed.get("target", ""), candidates,
                        excluded=continuation_excluded))
        if (continuation.get("ok")
                and int(flow.get("continuations", 0))
                    >= _MAX_GOAL_CONTINUATIONS):
            return {"ok": False,
                    "error_class": "goal_continuation_limit"}
        if continuation.get("ok"):
            candidate = continuation["candidate"]
            primitive = "click"
            plan_kind = "goal_continuation"
            confidence = float(continuation.get("confidence", 0.0))
        elif (chosen.get("ok") and not (
                goal_satisfied and not action_resolver.goal_candidate_is_exact(
                    parsed.get("target", ""), chosen["candidate"]))):
            candidate = chosen["candidate"]
            primitive = "click"
            plan_kind = "goal_navigation"
            confidence = float(chosen.get("confidence", 0.0))
        elif goal_satisfied:
            primitive = "observe"
            plan_kind = "goal_complete"
        elif at_goal_limit:
            return {"ok": False, "error_class": "goal_step_limit"}
        else:
            search_field = action_resolver.choose_search_field(candidates)
            if not search_field.get("ok"):
                scroll = action_resolver.choose_search_scroll_field(candidates)
                if scroll.get("ok") and await _scroll_candidate_into_view(
                        entry, scroll["candidate"]):
                    candidates = await _enumerate_candidates(entry["page"])
                    search_field = action_resolver.choose_search_field(candidates)
            if search_field.get("ok"):
                candidate = search_field["candidate"]
                primitive = "search"
                plan_kind = "goal_search"
                confidence = float(search_field.get("confidence", 0.0))
            else:
                candidate = (await _local_llm_choose_goal_candidate(
                    entry, parsed.get("target", ""), candidates,
                    list(flow.get("history") or ()), excluded)
                             if allow_model else None)
                if candidate is not None:
                    primitive = "click"
                    plan_kind = "goal_navigation"
                    confidence = 0.5
                    ambiguous = True
                    model_selected = True
                elif (int(flow.get("steps", 0)) > 0
                      and await _page_satisfies_goal(
                          entry, parsed.get("target", ""), candidates)):
                    primitive = "observe"
                    plan_kind = "goal_complete"
                else:
                    return {"ok": False, "error_class": (
                        chosen.get("error_class") or
                        search_field.get("error_class") or
                        "selector_missing"),
                        "observed_candidates": _goal_candidate_diagnostics(
                            chosen)}
    elif primitive not in ("goto", "wait") and not (
            primitive == "fill" and (value_ref or "").startswith("cred:")):
        candidates = await _enumerate_candidates(entry["page"])
        chosen = action_resolver.choose_candidate(parsed.get("target", ""),
                                                   candidates, primitive)
        if not chosen.get("ok"):
            scroll = action_resolver.choose_scroll_candidate(
                parsed.get("target", ""), candidates, primitive)
            if scroll.get("ok") and await _scroll_candidate_into_view(
                    entry, scroll["candidate"]):
                candidates = await _enumerate_candidates(entry["page"])
                chosen = action_resolver.choose_candidate(
                    parsed.get("target", ""), candidates, primitive)
        if not chosen.get("ok"):
            reveal = action_resolver.choose_reveal_candidate(
                parsed.get("target", ""), candidates, primitive)
            reveal_key = hashlib.sha256(
                f"{_page_signature(entry['page'].url)}\0{parsed.get('target', '')}"
                .encode("utf-8")).hexdigest()
            already_revealed = reveal_key in entry.get("reveal_attempts", set())
            reveal_candidate = (reveal.get("candidate")
                                if reveal.get("ok") else None)
            if not reveal_candidate and not already_revealed:
                reveal_candidate = await _textual_reveal_candidate(
                    entry, parsed.get("target", ""), candidates)
            if (allow_model and not reveal_candidate and reveal.get("hidden_target")
                    and not already_revealed):
                reveal_candidate = await _vlm_choose_reveal_candidate(
                    entry, parsed.get("target", ""), candidates)
            if reveal_candidate is not None and not already_revealed:
                candidate = reveal_candidate
                primitive = "click"
                plan_kind = "reveal_target"
                confidence = float(reveal.get("confidence", 0.5))
            else:
                if reveal.get("hidden_target"):
                    return {"ok": False, "error_class": "selector_hidden"}
                candidate = (await _vlm_choose_candidate(
                    entry, parsed.get("target", ""), chosen.get("ranked") or [],
                    primitive=primitive) if allow_model else None)
                if candidate is None:
                    return {"ok": False,
                            "error_class": chosen.get(
                                "error_class", "selector_missing")}
                confidence = 0.5
                ambiguous = True
                model_selected = True
        else:
            candidate = chosen["candidate"]
            confidence = float(chosen.get("confidence", 0.0))
            ambiguous = bool(chosen.get("ambiguous"))
            if ambiguous:
                vlm_candidate = (await _vlm_choose_candidate(
                    entry, parsed.get("target", ""), chosen.get("ranked") or [],
                    primitive=primitive) if allow_model else None)
                if vlm_candidate is not None:
                    candidate = vlm_candidate
                    model_selected = True
                else:
                    # Bassa confidenza su contenuto sensibile: mai indovinare.
                    return {"ok": False, "error_class": "selector_ambiguous"}
    sensitive, reasons = action_resolver.is_sensitive(
        primitive, candidate, tainted=bool(entry.get("web_content_ingested")),
        value_ref=value_ref)
    if plan_kind == "reveal_target":
        sensitive = True
        reasons = sorted(set(reasons + ["reveal_target"]))
    destination_url, destination_host = _action_destination(
        primitive, parsed.get("target", ""), candidate)
    if destination_host and destination_host not in entry.get("allowlist", set()):
        # L'host aggiuntivo viene autorizzato dallo STESSO gate che mostra
        # target e screenshot; l'inserimento effettivo avviene solo nel replay
        # del token, dopo la verifica di pagina ed ElementHandle.
        sensitive = True
        reasons = sorted(set(reasons + ["allowlist_extension"]))
        if len(set(entry.get("allowlist") or ()) | {destination_host}) > \
                _MAX_ALLOWLIST_HOSTS:
            return {"ok": False, "error_class": "allowlist_limit",
                    "max_hosts": _MAX_ALLOWLIST_HOSTS}
    if confidence < 0.65:
        sensitive = True
        reasons = sorted(set(reasons + ["low_confidence"]))
    plan = {
        "primitive": primitive, "target": parsed.get("target", ""),
        "seconds": parsed.get("seconds", 0), "candidate": candidate,
        "candidate_sig": _candidate_signature(candidate),
        "page_url": scrub_url(entry["page"].url),
        "page_sig": _page_signature(entry["page"].url),
        "value_ref": value_ref,
        "destination_url": destination_url,
        "destination_host": destination_host,
        "sensitive": sensitive, "sensitivity_reasons": reasons,
        "confidence": confidence, "created": time.time(),
        "model_selected": model_selected,
        "original_action": action,
        "replan_key": hashlib.sha256(
            f"{_page_signature(entry['page'].url)}\0{parsed.get('target', '')}"
            .encode("utf-8")).hexdigest(),
    }
    if plan_kind:
        plan.update({"kind": plan_kind, "original_action": action,
                     "reveal_key": reveal_key})
    if goal_flow_key:
        plan["goal_flow_key"] = goal_flow_key
    if primitive_override == "search" and target_override:
        plan["goal_target"] = target_override
    if plan_kind == "goal_continuation":
        plan["content_sig_before"] = await _goal_content_signature(entry)
    if candidate:
        try:
            handle = await entry["page"].locator(
                f'[data-metnos-action-id="{candidate.get("id")}"]').first.element_handle()
        except Exception:
            handle = None
        if handle is None:
            return {"ok": False, "error_class": "target_changed"}
        # Conservare l'ElementHandle broker-owned evita che la pagina inserisca
        # un duplicato con lo stesso data attribute fra gate ed esecuzione.
        plan["element_handle"] = handle
    plan["fingerprint"] = action_resolver.fingerprint_plan(plan)
    token = secrets.token_urlsafe(24)
    entry["pending_actions"][token] = plan
    entry["_sid"] = session_id
    return {"ok": True, "token": token, "plan": plan}


def _apply_login_intent_grant(entry: dict, prepared: dict, *,
                              allow_submit: bool = False) -> None:
    """Evita un gate per la normale transizione pre-login same-host.

    L'intento di login autorizza soltanto un click deterministico, stabile e
    gia' confinato alla allowlist. Qualunque scelta VLM, POST, segreto, host
    nuovo, reveal o bassa confidenza conserva il gate ordinario.
    """
    if not prepared.get("ok"):
        return
    plan = prepared.get("plan") or {}
    candidate = plan.get("candidate") or {}
    destination_host = str(plan.get("destination_host") or "")
    allowed_reasons = {"navigation", "tainted_turn"}
    if allow_submit:
        allowed_reasons.add("post")
    reasons = set(plan.get("sensitivity_reasons") or ())
    if (plan.get("primitive") != "click" or plan.get("kind")
            or plan.get("model_selected")
            or float(plan.get("confidence", 0)) < 0.65
            or candidate.get("download") or candidate.get("secret_input")
            or (str(candidate.get("form_method") or "").upper() == "POST"
                and not allow_submit)
            or (destination_host
                and destination_host not in set(entry.get("allowlist") or ()))
            or not reasons.issubset(allowed_reasons)):
        return
    plan["sensitive"] = False
    plan["sensitivity_reasons"] = sorted(reasons | {"login_intent_grant"})


def _goal_intent_grant_allows(entry: dict, plan: dict) -> bool:
    """Riusa il consenso BATCH solo per passi deterministici dello stesso fine."""
    key = str(plan.get("goal_flow_key") or "")
    flow = (entry.get("goal_flows") or {}).get(key)
    if not key or not isinstance(flow, dict) or not flow.get("approved"):
        return False
    if plan.get("kind") not in {
            "goal_navigation", "goal_search", "goal_complete",
            "goal_continuation"}:
        return False
    candidate = plan.get("candidate") or {}
    destination_host = str(plan.get("destination_host") or "")
    reasons = set(plan.get("sensitivity_reasons") or ())
    allowed_reasons = {
        "navigation", "navigation_or_submit", "post", "tainted_turn",
    }
    # Un gate di resource discovery autorizza il goal e gli host mostrati, ma
    # non un submit che non era ancora osservabile prima del reload.
    if (flow.get("approval_source") == "resource_reload"
            and ("post" in reasons
                 or str(candidate.get("form_method") or "").upper() == "POST")):
        return False
    return not (
        plan.get("model_selected")
        or candidate.get("download")
        or candidate.get("secret_input")
        or "allowlist_extension" in reasons
        or (destination_host
            and destination_host not in set(entry.get("allowlist") or ()))
        or not reasons.issubset(allowed_reasons)
    )


def _mandate_goal_matches(binding: dict, plan: dict) -> bool:
    if binding.get("credential_default"):
        return True
    if plan.get("kind") == "goal_complete":
        return True
    parsed = action_resolver.parse_action(
        str(plan.get("original_action") or plan.get("target") or ""))
    target = str(parsed.get("target") or plan.get("target") or "")
    target_tokens = set(action_resolver.goal_tokens(target, navigation=True))
    query_tokens = set(action_resolver.goal_tokens(
        str(binding.get("query") or ""), navigation=True))
    return bool(target_tokens and target_tokens & query_tokens)


def _mandate_allows_plan(entry: dict, plan: dict) -> bool:
    """Apply the credential mandate, plus the task envelope when present."""
    binding = (entry.get("task_mandate")
               or entry.get("credential_mandate"))
    if not isinstance(binding, dict):
        return False
    operations = set(binding.get("operations") or ())
    allowed_hosts = {
        _canonical_host(str(host))
        for host in (binding.get("allowed_hosts") or ())
    }
    allowed_hosts.discard("")
    candidate = plan.get("candidate") or {}
    destination_host = _canonical_host(str(
        plan.get("destination_host") or ""))
    if destination_host and destination_host not in allowed_hosts:
        return False
    if candidate.get("download") or candidate.get("secret_input"):
        return False

    login_flow = bool(plan.get("login_flow"))
    kind = str(plan.get("kind") or "")
    goal_flow = bool(plan.get("goal_flow_key")) or kind.startswith("goal_")
    credential_bindings = tuple(dict.fromkeys(str(value) for value in (
        plan.get("vault_domain"),
        (entry.get("login_flow") or {}).get("domain"),
        binding.get("root_host"), entry.get("domain"),
    ) if value))
    credential_authorized = any(
        credential_mandates.has_scope(candidate, "sites.read")
        for candidate in credential_bindings)

    if kind == "resource_reload":
        hosts = {_canonical_host(str(host))
                 for host in (plan.get("resource_hosts") or ())}
        if not hosts or "" in hosts or not hosts.issubset(allowed_hosts):
            return False
        if login_flow:
            return "login" in operations and credential_authorized
        if goal_flow:
            return ("navigate" in operations
                    and (not entry.get("authenticated")
                         or credential_authorized)
                    and _mandate_goal_matches(binding, plan))
        return False

    if kind == "credential_origin":
        # Entry del binding = origini piene (`https://host:443`) o host nudi
        # (tolleranza §2.4): il confronto di mandato ragiona per HOST
        # (l'esattezza della tupla resta al fill). Fail-closed: senza origini
        # esplicite un task schedulato non approva mai una delega.
        origins = {_host_of_url(str(entry_origin))
                   or _canonical_host(str(entry_origin))
                   for entry_origin in (binding.get("credential_origins") or ())}
        origins.discard("")
        return ("login" in operations and credential_authorized
                and _canonical_host(str(plan.get("target") or "")) in origins)

    form_method = str(candidate.get("form_method") or "").upper()
    if login_flow:
        if "login" not in operations or not credential_authorized:
            return False
        if plan.get("primitive") != "click":
            return False
        if form_method == "POST" and plan.get("login_procedure") != "continue":
            return False
        allowed_reasons = {
            "navigation", "navigation_or_submit", "tainted_turn",
            "low_confidence", "reveal_target",
        }
        if plan.get("login_procedure") == "continue":
            allowed_reasons.add("post")
        return set(plan.get("sensitivity_reasons") or ()).issubset(
            allowed_reasons)

    if goal_flow:
        if ("navigate" not in operations
                or (entry.get("authenticated") and not credential_authorized)
                or not _mandate_goal_matches(binding, plan)
                or form_method == "POST"):
            return False
        if kind not in {"goal_navigation", "goal_search", "goal_complete",
                        "goal_continuation"}:
            return False
        allowed_reasons = {
            "navigation", "navigation_or_submit", "tainted_turn",
            "low_confidence",
        }
        return set(plan.get("sensitivity_reasons") or ()).issubset(
            allowed_reasons)

    return (plan.get("primitive") in {"wait", "observe"}
            and "read" in operations
            and (not entry.get("authenticated") or credential_authorized))


def _blocked_hosts_for_action(entry: dict) -> dict[str, set[str]]:
    allowlist = set(entry.get("allowlist") or ())
    observed = entry.get("blocked_requests") or {}
    if not isinstance(observed, dict):
        return {}
    out: dict[str, set[str]] = {}
    for host, observation in observed.items():
        if (_canonical_host(host) != host or host in allowlist
                or not isinstance(observation, dict)):
            continue
        types = (set(observation.get("types") or ())
                 & _DISCOVERABLE_RESOURCE_TYPES)
        if types:
            out[host] = types
    return out


def _implicitly_relevant_host(entry: dict, host: str) -> bool:
    """Rilevanza di un host per il fallback risorse IMPLICITO.

    Senza evidenza causale target->host un host e' rilevante solo se
    first-party (root del mandato o suo sottodominio), host top-level
    corrente, o origine credenziale esplicitamente delegata nel binding.
    Un document di un subframe terzo (adv/telemetria, hostname casuale) non
    diventa mai rilevante solo perche' un selettore manca: per i terzi serve
    l'evidenza esatta (target DOM risolto, popup unico, redirect top-level),
    che passa da `required_hosts` e dal gate one-shot dedicato.
    """
    root = _canonical_host(str(entry.get("domain") or ""))
    if root and (host == root or host.endswith("." + root)):
        return True
    page = entry.get("page")
    top = _host_of_url(getattr(page, "url", "") or "")
    if top and host == top:
        return True
    binding = entry.get("credential_mandate")
    if isinstance(binding, dict):
        # Entry = origini piene o host nudi (§2.4): rilevanza di rete per host.
        origins = {_host_of_url(str(origin)) or _canonical_host(str(origin))
                   for origin in (binding.get("credential_origins") or ())}
        origins.discard("")
        if host in origins:
            return True
    return False


def _prepare_resource_expansion(entry: dict, session_id: str, action: str,
                                value_ref: str | None,
                                required_hosts: set[str] | None = None,
                                goal_target: str | None = None,
                                ) -> dict | None:
    """Prepara un reload con gli host osservati, senza concederli.

    Viene chiamato soltanto dopo `selector_missing`/`selector_ambiguous`: una
    pagina gia' interagibile non allarga il confine per widget o telemetria.
    Senza `required_hosts` (nessuna evidenza causale) propone solo host
    rilevanti per il mandato; se nessuno lo e', nessuna espansione: il
    chiamante prosegue col fallback modello sul DOM gia' caricato.
    """
    blocked = _blocked_hosts_for_action(entry)
    if required_hosts is not None:
        blocked = {host: blocked[host] for host in sorted(required_hosts)
                   if host in blocked}
    else:
        blocked = {host: types for host, types in blocked.items()
                   if _implicitly_relevant_host(entry, host)}
    if not blocked:
        return None
    hosts = sorted(blocked)
    expanded = set(entry.get("allowlist") or ()) | set(hosts)
    if len(expanded) > _MAX_ALLOWLIST_HOSTS:
        return {"ok": False, "error_class": "allowlist_limit",
                "max_hosts": _MAX_ALLOWLIST_HOSTS,
                "required_hosts": hosts}
    plan = {
        "kind": "resource_reload", "primitive": "reload",
        "target": action, "original_action": action,
        "value_ref": value_ref, "resource_hosts": hosts,
        "blocked_resource_types": {
            host: sorted(blocked[host]) for host in hosts},
        "candidate": None, "candidate_sig": "",
        "page_url": scrub_url(entry["page"].url),
        "page_sig": _page_signature(entry["page"].url),
        "sensitive": True,
        "sensitivity_reasons": ["allowlist_extension", "blocked_resources"],
        "confidence": 1.0, "created": time.time(),
    }
    parsed = action_resolver.parse_action(action)
    if goal_target or (
            parsed.get("ok") and parsed.get("primitive") == "search"):
        goal_flow_key = hashlib.sha256(
            action_resolver.normalize(action).encode("utf-8")).hexdigest()
        flow = (entry.get("goal_flows") or {}).get(goal_flow_key)
        if isinstance(flow, dict):
            plan["goal_flow_key"] = goal_flow_key
        if goal_target:
            plan["goal_target"] = goal_target
    plan["fingerprint"] = action_resolver.fingerprint_plan(plan)
    token = secrets.token_urlsafe(24)
    entry["pending_actions"][token] = plan
    entry["_sid"] = session_id
    return {"ok": True, "token": token, "plan": plan}


async def _recover_authenticated_landing(
        entry: dict, action: str, goal_target: str | None) -> bool:
    """Reset one sterile post-login landing to the session entry point.

    This is not a generic retry: it is available once, before any goal step,
    only under an existing mandate that authorizes the exact same-host
    navigation. No page text, vendor label, or guessed URL participates.
    """
    if not entry.get("authenticated") or entry.get("secret_pending"):
        return False
    entry_url = str(entry.get("entry_url") or "")
    entry_host = _host_of_url(entry_url)
    if (not entry_url or not entry_host
            or entry_host not in set(entry.get("allowlist") or ())):
        return False
    flow_key = hashlib.sha256(
        action_resolver.normalize(action).encode("utf-8")).hexdigest()
    flow = (entry.get("goal_flows") or {}).get(flow_key)
    if (not isinstance(flow, dict) or int(flow.get("steps", 0)) != 0
            or flow.get("landing_recovery_attempted")):
        return False
    mandate_probe = {
        "kind": "goal_navigation", "primitive": "click",
        "target": goal_target or action, "original_action": action,
        "destination_host": entry_host,
        "candidate": {"form_method": "", "download": False,
                      "secret_input": False},
        "sensitivity_reasons": ["navigation", "tainted_turn"],
    }
    if not _mandate_allows_plan(entry, mandate_probe):
        return False

    # Set before I/O so timeout/failure cannot create a retry loop.
    flow["landing_recovery_attempted"] = True
    blocked = entry.get("blocked_requests")
    if isinstance(blocked, dict):
        blocked.clear()
    try:
        _resp = await asyncio.wait_for(
            entry["page"].goto(
                entry_url, wait_until="load",
                timeout=int(_OP_TIMEOUT_S * 1000)),
            timeout=_OP_TIMEOUT_S)
        # ADR 0191 P4: codice osservativo dell'atterraggio goal (side-channel).
        _sig = sites_observed.response_signals(_resp)
        entry["observed_reason"] = sites_observed.observational_reason(
            status=_sig["status"], retry_after=_sig["retry_after"])
        await _settle_resource_discovery(entry["page"])
    except Exception as exc:
        sites_audit.record(
            "landing_recovery", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            domain=entry.get("domain", ""), outcome=False,
            detail=type(exc).__name__)
        return False
    entry["web_content_ingested"] = True
    entry.setdefault("reveal_attempts", set()).clear()
    entry.setdefault("action_replans", {}).clear()
    flow.setdefault("visited", set()).clear()
    flow.setdefault("continuation_exhausted", set()).clear()
    await _touch(entry)
    sites_audit.record(
        "landing_recovery", owner=entry.get("owner", ""),
        session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
        outcome=True, url=scrub_url(entry_url))
    return True


async def _prepare_action_with_resource_fallback(
        entry: dict, session_id: str, action: str,
        value_ref: str | None, *, allow_model: bool = True,
        settle_goal: bool = True,
        goal_target: str | None = None) -> dict:
    parsed = ({"ok": True, "primitive": "search", "target": goal_target}
              if goal_target else action_resolver.parse_action(action))
    is_goal = parsed.get("ok") and parsed.get("primitive") == "search"
    fallback_errors = {"selector_missing", "selector_ambiguous"}

    def _resolution_expansion(result: dict) -> dict | None:
        if result.get("error_class") == "selector_ambiguous":
            # L'ambiguita' su una pagina senza stile richiede soltanto CSS.
            # Script/XHR pubblicitari o telemetrici non diventano necessari
            # solo perche' il DOM contiene due controlli equivalenti.
            stylesheet_hosts = {
                host for host, resource_types in
                _blocked_hosts_for_action(entry).items()
                if "stylesheet" in resource_types
            }
            if not stylesheet_hosts:
                return None
            return _prepare_resource_expansion(
                entry, session_id, action, value_ref,
                required_hosts=stylesheet_hosts, goal_target=goal_target)
        return _prepare_resource_expansion(
            entry, session_id, action, value_ref, goal_target=goal_target)

    if is_goal and settle_goal:
        attempts = max(1, _REVEAL_SETTLE_MS // _REVEAL_POLL_MS)
        settle_deadline = _monotonic() + _REVEAL_SETTLE_MS / 1000.0
        prepared = {"ok": False, "error_class": "selector_missing"}
        for attempt in range(attempts):
            prepared = await _prepare_action(
                entry, session_id, action, value_ref,
                primitive_override=("search" if goal_target else None),
                target_override=goal_target, allow_model=False)
            if (prepared.get("ok") or prepared.get("error_class")
                    not in fallback_errors | {"target_changed"}):
                break
            if _monotonic() >= settle_deadline:
                break
            if attempt + 1 < attempts:
                if hasattr(entry["page"], "wait_for_timeout"):
                    await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
                else:
                    await asyncio.sleep(_REVEAL_POLL_MS / 1000)
        if not prepared.get("ok") and prepared.get(
                "error_class") in fallback_errors:
            if (prepared.get("error_class") == "selector_missing"
                    and not prepared.get("observed_candidates")
                    and await _recover_authenticated_landing(
                        entry, action, goal_target)):
                return await _prepare_action_with_resource_fallback(
                    entry, session_id, action, value_ref,
                    allow_model=allow_model, settle_goal=True,
                    goal_target=goal_target)
            expansion = _resolution_expansion(prepared)
            if expansion is not None:
                return expansion
            if allow_model:
                prepared = await _prepare_action(
                    entry, session_id, action, value_ref,
                    primitive_override=("search" if goal_target else None),
                    target_override=goal_target, allow_model=True)
    else:
        prepared = await _prepare_action(
            entry, session_id, action, value_ref,
            primitive_override=("search" if goal_target else None),
            target_override=goal_target, allow_model=False)
        if not prepared.get("ok") and prepared.get(
                "error_class") in fallback_errors:
            expansion = _resolution_expansion(prepared)
            if expansion is not None:
                return expansion
            if allow_model:
                prepared = await _prepare_action(
                    entry, session_id, action, value_ref,
                    primitive_override=("search" if goal_target else None),
                    target_override=goal_target, allow_model=True)
    if (not prepared.get("ok")
            and prepared.get("error_class") in fallback_errors):
        expansion = _resolution_expansion(prepared)
        if expansion is not None:
            return expansion
    return prepared


async def _prepare_after_reveal(entry: dict, session_id: str, action: str,
                                value_ref: str | None) -> dict:
    """Attende la transizione UI finche' il target diventa interagibile."""
    attempts = max(1, _REVEAL_SETTLE_MS // _REVEAL_POLL_MS)
    settle_deadline = _monotonic() + _REVEAL_SETTLE_MS / 1000.0
    last = {"ok": False, "error_class": "selector_hidden"}
    for _ in range(attempts):
        last = await _prepare_action(entry, session_id, action, value_ref)
        if last.get("ok"):
            plan = last.get("plan") or {}
            handle = plan.get("element_handle")
            if handle is None:
                return last
            if hasattr(entry["page"], "wait_for_timeout"):
                await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
            else:
                await asyncio.sleep(_REVEAL_POLL_MS / 1000)
            try:
                current = await handle.evaluate(_ELEMENT_STATE_JS)
            except Exception:
                current = None
            if _candidate_signature(current) == plan.get("candidate_sig"):
                return last
            entry.get("pending_actions", {}).pop(last.get("token"), None)
            last = {"ok": False, "error_class": "target_changed"}
            continue
        if last.get("error_class") not in {
                "selector_hidden", "selector_missing", "target_changed"}:
            return last
        if _monotonic() >= settle_deadline:
            break
        if hasattr(entry["page"], "wait_for_timeout"):
            await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
        else:
            await asyncio.sleep(_REVEAL_POLL_MS / 1000)
    return last


async def _prepare_after_goal_navigation(entry: dict, session_id: str,
                                         action: str,
                                         value_ref: str | None,
                                         goal_target: str | None = None) -> dict:
    """Riosserva una transizione SPA prima del fallback intelligente."""
    attempts = max(1, _REVEAL_SETTLE_MS // _REVEAL_POLL_MS)
    settle_deadline = _monotonic() + _REVEAL_SETTLE_MS / 1000.0
    last = {"ok": False, "error_class": "selector_missing"}
    for attempt in range(attempts):
        last = await _prepare_action(
            entry, session_id, action, value_ref,
            primitive_override=("search" if goal_target else None),
            target_override=goal_target, allow_model=False)
        if last.get("ok"):
            return last
        if last.get("error_class") not in {
                "selector_hidden", "selector_missing", "selector_ambiguous",
                "target_changed"}:
            return last
        if _monotonic() >= settle_deadline:
            break
        if attempt + 1 < attempts:
            if hasattr(entry["page"], "wait_for_timeout"):
                await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
            else:
                await asyncio.sleep(_REVEAL_POLL_MS / 1000)
    kwargs = {"allow_model": True, "settle_goal": False}
    if goal_target:
        kwargs["goal_target"] = goal_target
    return await _prepare_action_with_resource_fallback(
        entry, session_id, action, value_ref, **kwargs)


def _inherit_login_plan_context(parent: dict, prepared: dict) -> None:
    """Propaga la procedura intelligente quando una transizione crea un piano.

    Resource reload, reveal e popup sono dettagli intermedi del medesimo goal
    di login. Il child viene contato soltanto alla sua esecuzione effettiva.
    """
    child = prepared.get("plan") if isinstance(prepared, dict) else None
    if not isinstance(child, dict) or not parent.get("login_flow"):
        return
    child["login_flow"] = True
    child["login_procedure"] = parent.get("login_procedure") or "login"


async def _execute_resource_expansion(entry: dict, token: str,
                                      plan: dict) -> dict:
    hosts = plan.get("resource_hosts") or []
    if (not isinstance(hosts, list) or not hosts
            or any(_canonical_host(host) != host for host in hosts)):
        return {"ok": False, "error_class": "approval_invalid"}
    observed = _blocked_hosts_for_action(entry)
    if any(host not in observed for host in hosts):
        return {"ok": False, "error_class": "target_changed"}
    allowlist = entry.get("allowlist")
    if not isinstance(allowlist, set):
        allowlist = set(allowlist or ())
        entry["allowlist"] = allowlist
    if len(allowlist | set(hosts)) > _MAX_ALLOWLIST_HOSTS:
        return {"ok": False, "error_class": "allowlist_limit",
                "max_hosts": _MAX_ALLOWLIST_HOSTS}
    for host in hosts:
        if host in allowlist:
            continue
        allowlist.add(host)
        sites_audit.record(
            "allowlist_change", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            domain=entry.get("domain", ""), added_host=host,
            source="approved_blocked_resource")

    entry["pending_actions"].pop(token, None)
    entry["gate_pending"] = False
    blocked_requests = entry.get("blocked_requests")
    if isinstance(blocked_requests, dict):
        blocked_requests.clear()
    page = entry["page"]
    try:
        await asyncio.wait_for(
            page.reload(wait_until="load", timeout=int(_OP_TIMEOUT_S * 1000)),
            timeout=_OP_TIMEOUT_S)
        await _settle_resource_discovery(page)
    except Exception as exc:
        return {"ok": False, "error_class": "action_failed",
                "detail": type(exc).__name__}
    entry["web_content_ingested"] = True
    # Il reload crea una nuova istanza DOM: i tentativi della pagina precedente
    # non possono impedire reveal o replan sulla nuova osservazione.
    entry.setdefault("reveal_attempts", set()).clear()
    entry.setdefault("action_replans", {}).clear()
    await _touch(entry)
    goal_flow_key = str(plan.get("goal_flow_key") or "")
    goal_flow = (entry.get("goal_flows") or {}).get(goal_flow_key)
    if isinstance(goal_flow, dict):
        # A reload destroys transient UI state (open menus, tabs, accordions).
        # Candidate identities from the previous DOM must be eligible again;
        # bounded step/continuation counters still prevent navigation loops.
        goal_flow.setdefault("visited", set()).clear()
        goal_flow.setdefault("continuation_exhausted", set()).clear()
        if not goal_flow.get("approved"):
            goal_flow["approval_source"] = "resource_reload"
        goal_flow["approved"] = True
    prepare_kwargs = {}
    if plan.get("goal_target"):
        prepare_kwargs["goal_target"] = plan["goal_target"]
    prepared = await _prepare_action_with_resource_fallback(
        entry, entry.get("_sid", ""), plan.get("original_action") or "",
        plan.get("value_ref"), **prepare_kwargs)
    # Il reload autorizzativo non e' un passo della procedura. Il nuovo piano
    # DOM verra' contato solo quando l'azione effettiva sara' eseguita.
    _inherit_login_plan_context(plan, prepared)
    if plan.get("login_flow"):
        _apply_login_intent_grant(
            entry, prepared,
            allow_submit=plan.get("login_procedure") == "continue")
    return await _handle_prepared_action(
        entry, entry.get("_sid", ""),
        plan.get("original_action") or "", prepared)


def _plan_audit_fields(plan: dict) -> dict:
    """Metadati sicuri per l'audit di un passo goal/login.

    Solo ruolo e nome accessibile bounded del target risolto, mai valori di
    form, OTP, credenziali o contenuto di pagina autenticata. Rende
    ricostruibile dalla sola audit-trail QUALE controllo e' stato scelto e da
    quale meccanismo (deterministico vs modello), e la transizione URL.
    """
    candidate = plan.get("candidate") or {}
    try:
        confidence = round(float(plan.get("confidence") or 0.0), 3)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "kind": str(plan.get("kind") or ""),
        "resolved_tag": str(candidate.get("tag") or "")[:20],
        "resolved_role": str(candidate.get("role")
                             or candidate.get("tag") or "")[:40],
        "resolved_name": str(candidate.get("name")
                             or candidate.get("label") or "")[:160],
        "verifiable_destination": bool(
            action_resolver._safe_navigation_identity(candidate)),
        "confidence": confidence,
        "model_selected": bool(plan.get("model_selected")),
        "url_before": str(plan.get("page_url") or ""),
    }


async def _apply_interaction_behavior(entry: dict, locator=None) -> None:
    from playwright_sidecar import stealth as _st
    techniques = entry.get("stealth_techniques", ())
    await _st.prepare_interaction(
        entry["page"], locator, techniques=techniques)
    await _st.pause_before_interaction(
        entry["page"], techniques=techniques)


async def _wait_for_goal_navigation_commit(page, before_url: str, *,
                                           timeout_ms: int | None = None
                                           ) -> bool:
    """Wait for a slow top-level anchor navigation without touching the DOM.

    Some authenticated portals keep the old URL and expose an empty document
    for tens of seconds before committing a cross-origin GET. Polling the
    Playwright URL property avoids execution-context races during that window.
    """
    budget_ms = (_GOAL_NAVIGATION_COMMIT_MS if timeout_ms is None
                 else max(0, int(timeout_ms)))
    before_sig = _page_signature(before_url)
    deadline = _monotonic() + budget_ms / 1000.0
    while _monotonic() < deadline:
        if _page_signature(getattr(page, "url", "")) != before_sig:
            return True
        await asyncio.sleep(_REVEAL_POLL_MS / 1000.0)
    return _page_signature(getattr(page, "url", "")) != before_sig


def _browser_navigation_failure(url: str) -> str:
    """Classify Chromium-owned top-level error documents.

    A click can be dispatched successfully while the browser fails the
    resulting network navigation and commits ``chrome-error://chromewebdata``
    (or an equivalent neterror document).  Such a document is never a valid
    goal destination and must not enter DOM settle/replan as if it were the
    requested site.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except Exception:
        return ""
    scheme = parsed.scheme.lower()
    if scheme == "chrome-error":
        return "browser_error_page"
    if scheme == "about" and str(parsed.path or "").lower() in {
            "neterror", "certerror"}:
        return "browser_error_page"
    return ""


async def _execute_plan(entry: dict, token: str, plan: dict) -> dict:
    page = entry["page"]
    primitive = plan["primitive"]
    candidate = plan.get("candidate")
    if time.time() - float(plan.get("created", 0)) > 3600:
        return {"ok": False, "error_class": "action_expired"}
    if _page_signature(page.url) != plan.get("page_sig"):
        return {"ok": False, "error_class": "page_changed"}
    if plan.get("kind") == "resource_reload":
        return await _execute_resource_expansion(entry, token, plan)
    if plan.get("kind") == "credential_origin":
        stage = plan.get("form_stage")
        try:
            script = (credential_injection._LOCATE_USERNAME_STAGE_JS
                      if stage == "username"
                      else credential_injection._LOCATE_LOGIN_FORM_JS)
            info = await page.evaluate(script)
        except Exception:
            info = None
        observed = _host_of_url((info or {}).get("actionResolved") or "")
        if not info or not info.get("found") or observed != plan.get("target"):
            return {"ok": False, "error_class": "target_changed"}
        allowlist = entry.get("allowlist")
        if not isinstance(allowlist, set):
            allowlist = set(allowlist or ())
            entry["allowlist"] = allowlist
        if observed not in allowlist:
            allowlist.add(observed)
            sites_audit.record(
                "allowlist_change", owner=entry.get("owner", ""),
                session_id=entry.get("_sid", ""),
                domain=entry.get("domain", ""), added_host=observed,
                source="approved_credential_origin")
        entry["pending_actions"].pop(token, None)
        entry["gate_pending"] = False
        await _touch(entry)
        sites_audit.record(
            "credential_origin_approval", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            domain=plan.get("vault_domain", ""), origin=observed,
            outcome=True)
        return {"ok": True, "executed": True, "approved": True,
                "primitive": "authorize",
                "credential_origin": plan.get("exact_origin") or observed,
                "url": scrub_url(entry["page"].url)}
    locator = None
    if candidate:
        handle = plan.get("element_handle")
        if handle is None:
            return {"ok": False, "error_class": "target_changed"}
        try:
            current = await handle.evaluate(_ELEMENT_STATE_JS)
        except Exception:
            current = None
        if _candidate_signature(current) != plan.get("candidate_sig"):
            return {"ok": False, "error_class": "target_changed"}
        locator = handle
    value_ref = plan.get("value_ref")
    continuation_snapshot = None
    destination_host = str(plan.get("destination_host") or "")
    if destination_host:
        allowlist = entry.get("allowlist")
        if not isinstance(allowlist, set):
            allowlist = set(allowlist or [])
            entry["allowlist"] = allowlist
        if destination_host not in allowlist:
            allowlist.add(destination_host)
            sites_audit.record(
                "allowlist_change", owner=entry.get("owner", ""),
                session_id=entry.get("_sid", ""),
                domain=entry.get("domain", ""), added_host=destination_host,
                source="approved_action_target")
    try:
        if primitive == "wait":
            await asyncio.sleep(min(20, max(1, int(plan.get("seconds") or 2))))
        elif primitive == "observe":
            pass
        elif primitive == "goto":
            target_url = plan.get("target") or ""
            if not re.match(r"^https?://", target_url):
                return {"ok": False, "error_class": "invalid_url"}
            _resp = await page.goto(target_url, wait_until="load",
                                    timeout=int(_OP_TIMEOUT_S * 1000))
            # ADR 0191 P4: codice osservativo (side-channel su entry).
            _sig = sites_observed.response_signals(_resp)
            entry["observed_reason"] = sites_observed.observational_reason(
                status=_sig["status"], retry_after=_sig["retry_after"])
            entry["web_content_ingested"] = True
        elif primitive == "fill":
            if (value_ref or "").startswith("cred:"):
                cred = await credential_injection.fill_credential_ref(
                    page=page, expected_domain=entry.get("domain", ""),
                    value_ref=value_ref, owner=entry.get("owner", ""),
                    session_id=entry.get("_sid", ""),
                    op_timeout_s=_OP_TIMEOUT_S,
                    stealth_techniques=entry.get("stealth_techniques", ()))
                if not cred.get("ok"):
                    return cred
                entry["secret_pending"] = True
            else:
                if locator is None:
                    return {"ok": False, "error_class": "selector_missing"}
                if candidate and candidate.get("secret_input"):
                    await locator.evaluate(
                        "el => el.setAttribute('data-metnos-redact', '1')")
                    entry["secret_pending"] = True
                await _apply_interaction_behavior(entry, locator)
                await locator.fill(str(value_ref or ""),
                                   timeout=int(_OP_TIMEOUT_S * 1000))
        elif primitive == "search":
            if locator is None:
                return {"ok": False, "error_class": "selector_missing"}
            await _apply_interaction_behavior(entry, locator)
            await locator.fill(str(plan.get("target") or ""),
                               timeout=int(_OP_TIMEOUT_S * 1000))
            await _apply_interaction_behavior(entry, locator)
            await locator.press("Enter", timeout=int(_OP_TIMEOUT_S * 1000))
            try:
                await page.wait_for_load_state("load", timeout=3000)
            except Exception:
                pass
            entry["web_content_ingested"] = True
        elif primitive in ("click", "submit"):
            if plan.get("kind") == "goal_continuation":
                continuation_snapshot = await _continuation_snapshot(entry)
            # Batch credenziale: fill broker-owned solo DOPO l'approvazione e
            # immediatamente prima del submit, senza screenshot intermedio.
            if (value_ref or "").startswith("cred:"):
                cred = await credential_injection.fill_credential_ref(
                    page=page, expected_domain=entry.get("domain", ""),
                    value_ref=value_ref, owner=entry.get("owner", ""),
                    session_id=entry.get("_sid", ""),
                    op_timeout_s=_OP_TIMEOUT_S,
                    stealth_techniques=entry.get("stealth_techniques", ()))
                if not cred.get("ok"):
                    return cred
                entry["secret_pending"] = True
            if locator is None:
                return {"ok": False, "error_class": "selector_missing"}
            await _apply_interaction_behavior(entry, locator)
            click_url_before = page.url
            context = entry.get("context")
            context_pages = getattr(context, "pages", ()) or ()
            before_pages = tuple(context_pages)
            observed_pages = []
            def _record_page(new_page):
                if all(new_page is not old for old in before_pages):
                    observed_pages.append(new_page)
            if hasattr(context, "on"):
                context.on("page", _record_page)
            try:
                try:
                    # Do not let Playwright spend the whole operation waiting
                    # for a navigation implicitly. Navigation is observed by
                    # the bounded load-state wait immediately below.
                    await locator.click(timeout=_CLICK_TIMEOUT_MS,
                                        no_wait_after=True)
                except Exception as exc:
                    # With no_wait_after, a click timeout occurs during the
                    # pre-dispatch actionability checks. No click was emitted,
                    # so a fresh DOM observation is safe and cannot duplicate
                    # a user-visible effect.
                    detail = f"{type(exc).__name__}: {exc}".lower()
                    if "timeout" in detail or "timed out" in detail:
                        sites_audit.record(
                            "site_action", owner=entry.get("owner", ""),
                            session_id=entry.get("_sid", ""),
                            domain=entry.get("domain", ""),
                            primitive=primitive,
                            target=plan.get("target", ""),
                            sensitivity=plan.get(
                                "sensitivity_reasons", []),
                            outcome=False,
                            reason="click_actionability_timeout",
                            pre_dispatch_blocked=True,
                            **_plan_audit_fields(plan))
                        return {"ok": False,
                                "error_class": "target_changed",
                                "detail": "click_actionability_timeout"}
                    raise
                # Un anchor di navigazione goal usa `no_wait_after=True`: non
                # creare subito un waiter DOM mentre il vecchio execution
                # context viene distrutto. Su Chromium questo puo' lasciare un
                # Future Playwright rifiutato dopo che il click e' gia'
                # ritornato. Il commit del top-level viene osservato piu' sotto
                # esclusivamente tramite page.url; il nuovo DOM e' poi letto
                # dal normale settle/replan bounded.
                if plan.get("kind") != "goal_navigation":
                    try:
                        await page.wait_for_load_state(
                            "domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                # L'evento popup puo' seguire il ritorno del click; attesa
                # bounded, interrotta appena il listener osserva una pagina.
                if hasattr(context, "on"):
                    for _ in range(10):
                        if observed_pages:
                            break
                        if hasattr(page, "wait_for_timeout"):
                            await page.wait_for_timeout(50)
                        else:
                            await asyncio.sleep(0.05)
            finally:
                if hasattr(context, "remove_listener"):
                    context.remove_listener("page", _record_page)
            # Un click puo' aprire una nuova scheda senza cambiare page.url.
            # Il context route-guard copre anche il popup; qui lo si adotta solo
            # se e' unico e il suo host e' gia' consentito. Altrimenti chiude e
            # prepara un gate legato al solo host document osservato.
            context_pages = getattr(context, "pages", ()) or ()
            new_pages = list(observed_pages)
            for item in context_pages:
                if (all(item is not old for old in before_pages)
                        and all(item is not seen for seen in new_pages)):
                    new_pages.append(item)
            if len(new_pages) > 1:
                for popup in new_pages:
                    try:
                        await popup.close()
                    except Exception:
                        pass
                return {"ok": False, "error_class": "popup_ambiguous"}
            if new_pages:
                popup = new_pages[0]
                try:
                    await popup.wait_for_load_state("domcontentloaded", timeout=1500)
                except Exception:
                    pass
                popup_host = _host_of_url(popup.url)
                allowlist = set(entry.get("allowlist") or ())
                if popup_host and popup_host not in allowlist:
                    _observe_blocked_request(
                        entry.setdefault("blocked_requests", {}),
                        popup_host, "document",
                        {"main_frame": True, "navigation": True,
                         "top_host": popup_host,
                         "parent_host": _host_of_url(entry["page"].url)})
                    try:
                        await popup.close()
                    except Exception:
                        pass
                    entry["pending_actions"].pop(token, None)
                    entry["gate_pending"] = False
                    expansion = _prepare_resource_expansion(
                        entry, entry.get("_sid", ""),
                        plan.get("original_action") or "", value_ref,
                        required_hosts={popup_host})
                    if expansion is None:
                        return {"ok": False,
                                "error_class": "popup_host_unverified"}
                    _inherit_login_plan_context(plan, expansion)
                    return await _handle_prepared_action(
                        entry, entry.get("_sid", ""),
                        plan.get("original_action") or "", expansion)
                if popup_host:
                    entry["page"] = popup
            elif (plan.get("kind") == "goal_navigation"
                  and destination_host
                  and _page_signature(page.url) == _page_signature(
                      click_url_before)):
                await _wait_for_goal_navigation_commit(
                    page, click_url_before)
            entry["secret_pending"] = False
            entry["web_content_ingested"] = True
            if (continuation_snapshot
                    and scrub_url(entry["page"].url)
                        != continuation_snapshot.get("url")):
                collected = entry.setdefault("collected_pages", [])
                snap_key = hashlib.sha256(str(
                    continuation_snapshot.get("text") or "").encode(
                        "utf-8")).hexdigest()
                if (continuation_snapshot.get("text")
                        and all(item.get("key") != snap_key
                                for item in collected)):
                    collected.append({**continuation_snapshot, "key": snap_key})
                    del collected[12:]
        else:
            return {"ok": False, "error_class": "unsupported_action"}
    except Exception as exc:
        error_match = re.search(
            r"\b(?:net::)?ERR_[A-Z0-9_]+\b", str(exc).upper())
        error_detail = (error_match.group(0) if error_match
                        else type(exc).__name__)
        blocked_navigation_hosts = sorted(
            host for host, observation in (
                entry.get("blocked_requests") or {}).items()
            if isinstance(observation, dict)
            and observation.get("main_frame")
            and observation.get("navigation"))
        sites_audit.record(
            "site_action", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            domain=entry.get("domain", ""), primitive=primitive,
            target=plan.get("target", ""),
            sensitivity=plan.get("sensitivity_reasons", []),
            outcome=False, reason="action_exception",
            detail=error_detail,
            url_after=scrub_url(getattr(entry.get("page"), "url", "")),
            destination_url=str(plan.get("destination_url") or ""),
            blocked_navigation_hosts=blocked_navigation_hosts[:16],
            navigation_trace=list(plan.get("navigation_trace") or ())[:12],
            **_plan_audit_fields(plan))
        return {"ok": False, "error_class": "action_failed",
                "detail": error_detail}
    navigation_failure = (
        _browser_navigation_failure(getattr(entry.get("page"), "url", ""))
        if plan.get("kind") == "goal_navigation" else ""
    )
    if navigation_failure:
        # The click was emitted, therefore it is unsafe to replay it.  Consume
        # the one-shot plan and return a terminal, typed failure before any DOM
        # settle/replan can degrade it to selector_missing.
        entry.get("pending_actions", {}).pop(token, None)
        entry["gate_pending"] = False
        await _touch(entry)
        sites_audit.record(
            "site_action", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            domain=entry.get("domain", ""), primitive=primitive,
            target=plan.get("target", ""),
            sensitivity=plan.get("sensitivity_reasons", []),
            outcome=False, reason="navigation_failed",
            detail=navigation_failure,
            url_after=scrub_url(getattr(entry.get("page"), "url", "")),
            **_plan_audit_fields(plan))
        return {"ok": False, "error_class": "navigation_failed",
                "reason_code": "navigation_failed",
                "detail": navigation_failure}
    goal_flow_key = str(plan.get("goal_flow_key") or "")
    goal_flow = (entry.get("goal_flows") or {}).get(goal_flow_key)
    if isinstance(goal_flow, dict):
        if plan.get("sensitive"):
            goal_flow["approved"] = True
            goal_flow.setdefault("approval_source", "action")
        if plan.get("kind") == "goal_navigation":
            goal_flow["steps"] = int(goal_flow.get("steps", 0)) + 1
            visited = goal_flow.setdefault("visited", set())
            visited.add(action_resolver.goal_candidate_key(candidate or {}))
            goal_flow.setdefault("history", []).append(str(
                (candidate or {}).get("name")
                or (candidate or {}).get("label") or "")[:160])
        elif plan.get("kind") == "goal_continuation":
            before = str(plan.get("content_sig_before") or "")
            progressed, current_sig = await _wait_for_goal_content_change(
                entry, before)
            seen = goal_flow.setdefault("content_signatures", set())
            repeated = bool(current_sig and current_sig in seen)
            if before:
                seen.add(before)
            if current_sig:
                seen.add(current_sig)
            goal_flow["continuations"] = int(
                goal_flow.get("continuations", 0)) + 1
            # Una nuova pagina/porzione esplicita puo' avere a sua volta
            # contenuto lazy: consenti un nuovo ciclo entro il budget globale.
            goal_flow["collection_scroll_complete"] = False
            if not progressed or repeated:
                goal_flow.setdefault("continuation_exhausted", set()).add(
                    action_resolver.goal_candidate_key(candidate or {}))
            goal_flow.setdefault("history", []).append(str(
                (candidate or {}).get("name")
                or (candidate or {}).get("label") or "")[:160])
    entry["approved_actions"].add(plan["fingerprint"])
    entry["pending_actions"].pop(token, None)
    entry["gate_pending"] = False
    if plan.get("login_flow"):
        flow = entry.get("login_flow")
        if isinstance(flow, dict):
            # Conta transizioni osservabili, non richieste di consenso. I
            # resource_reload ritornano prima di questo punto.
            flow["steps"] = int(flow.get("steps", 0)) + 1
    await _touch(entry)
    sites_audit.record("site_action", owner=entry.get("owner", ""),
                       session_id=entry.get("_sid", ""),
                       domain=entry.get("domain", ""),
                       primitive=primitive, target=plan.get("target", ""),
                       sensitivity=plan.get("sensitivity_reasons", []),
                       outcome=True,
                       url_after=scrub_url(entry["page"].url),
                       **_plan_audit_fields(plan))
    entry.get("action_replans", {}).pop(plan.get("replan_key"), None)
    if plan.get("kind") == "reveal_target":
        entry.setdefault("reveal_attempts", set()).add(
            plan.get("reveal_key") or "")
        prepared = await _prepare_after_reveal(
            entry, entry.get("_sid", ""),
            plan.get("original_action") or "", plan.get("value_ref"))
        _inherit_login_plan_context(plan, prepared)
        return await _handle_prepared_action_with_replans(
            entry, entry.get("_sid", ""),
            plan.get("original_action") or "", plan.get("value_ref"),
            prepared)
    if plan.get("kind") in {"goal_navigation", "goal_continuation"}:
        prepared = await _prepare_after_goal_navigation(
            entry, entry.get("_sid", ""),
            plan.get("original_action") or "", plan.get("value_ref"),
            goal_target=plan.get("goal_target"))
        return await _handle_prepared_action_with_replans(
            entry, entry.get("_sid", ""),
            plan.get("original_action") or "", plan.get("value_ref"),
            prepared)
    if plan.get("kind") in {"goal_complete", "goal_search"}:
        entry.get("goal_flows", {}).pop(goal_flow_key, None)
    return {"ok": True, "executed": True, "primitive": primitive,
            "url": scrub_url(entry["page"].url)}


async def _handle_prepared_action(entry: dict, session_id: str, action: str,
                                  prepared: dict) -> dict:
    if not prepared.get("ok"):
        return prepared
    token, plan = prepared["token"], prepared["plan"]
    is_resource_reload = plan.get("kind") == "resource_reload"
    task_scoped = isinstance(entry.get("task_mandate"), dict)
    credential_scoped = isinstance(entry.get("credential_mandate"), dict)
    mandated = ((task_scoped or credential_scoped)
                and _mandate_allows_plan(entry, plan))
    if task_scoped and not mandated:
        entry.get("pending_actions", {}).pop(token, None)
        entry["gate_pending"] = False
        sites_audit.record(
            "task_mandate_denied", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            domain=entry.get("domain", ""),
            task_name=(entry.get("task_mandate") or {}).get("task_name", ""),
            primitive=plan.get("primitive", ""), kind=plan.get("kind", ""))
        return {"ok": False, "error_class": "mandate_scope_exceeded"}
    remembered = (mandated or (not is_resource_reload
                  and (plan["fingerprint"] in entry["approved_actions"]
                       or _goal_intent_grant_allows(entry, plan))))
    if plan["sensitive"] and not remembered:
        entry["gate_pending"] = True
        entry["gate_started"] = time.time()
        shot = None if is_resource_reload else await _capture_screenshot(entry)
        if not is_resource_reload and not shot:
            entry["pending_actions"].pop(token, None)
            entry["gate_pending"] = False
            return {"ok": False, "error_class": "screenshot_failed"}
        destination = plan.get("destination_url") or ""
        description = (scrub_url(plan["target"])
                       if plan["primitive"] == "goto" else action)
        additions = list(plan.get("resource_hosts") or ())
        if (not additions and "allowlist_extension" in
                plan["sensitivity_reasons"]):
            additions = [plan["destination_host"]]
        if destination and destination not in description:
            description = f"{description} -> {destination}"
        if is_resource_reload:
            description = f"{description} [allowlist: {', '.join(additions)}]"
        elif plan.get("kind") == "reveal_target":
            reveal_name = str((plan.get("candidate") or {}).get("name") or
                              (plan.get("candidate") or {}).get("role") or
                              (plan.get("candidate") or {}).get("tag") or "control")
            description = f"{description} [reveal: {reveal_name}]"
        out = {
            "ok": True, "approval_required": True,
            "approval_token": token, "session_id": session_id,
            "description": description,
            "sensitivity_reasons": plan["sensitivity_reasons"],
            "allowlist_additions": additions,
            "sensitive": bool(entry.get("authenticated")),
        }
        candidate = plan.get("candidate") or {}
        if candidate:
            out["resolved_target"] = str(
                candidate.get("name") or candidate.get("label") or "")[:160]
            out["resolved_role"] = str(
                candidate.get("role") or candidate.get("tag") or "")[:40]
        if shot:
            out["screenshot_path"] = shot
        if plan.get("blocked_resource_types"):
            out["blocked_resource_types"] = plan["blocked_resource_types"]
        return out
    return await _execute_plan(entry, token, plan)


async def _handle_prepared_action_with_replans(
        entry: dict, session_id: str, action: str,
        value_ref: str | None, prepared: dict) -> dict:
    """Riosserva il DOM se cambia prima che l'azione venga eseguita.

    `_execute_plan` lascia il token pending quando rifiuta un piano per
    `target_changed`/`page_changed`: questa e' la prova che nessun effetto e'
    stato applicato e che il retry non puo' duplicare un click. Se il token e'
    gia' stato consumato, invece, restituiamo l'errore senza ripetere l'azione.
    """
    replans = 0
    while True:
        token = prepared.get("token") if isinstance(prepared, dict) else None
        plan = prepared.get("plan") if isinstance(prepared, dict) else None
        handled = await _handle_prepared_action(
            entry, session_id, action, prepared)
        if handled.get("error_class") not in {
                "target_changed", "page_changed"}:
            return handled
        if (not token or not isinstance(plan, dict)
                or entry.get("pending_actions", {}).get(token) is not plan):
            return handled

        if handled.get("detail") == "click_actionability_timeout":
            await _dismiss_obstructing_overlay(entry, settle=True)

        entry.get("pending_actions", {}).pop(token, None)
        entry["gate_pending"] = False
        key = str(plan.get("replan_key") or "")
        counts = entry.setdefault("action_replans", {})
        count = int(counts.get(key, 0)) + 1
        counts[key] = count
        replans += 1
        if count > _MAX_ACTION_REPLANS or replans > _MAX_ACTION_REPLANS:
            return {"ok": False, "error_class": "target_unstable",
                    "replans": replans - 1}

        page = entry.get("page")
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(_REVEAL_POLL_MS)
        else:
            await asyncio.sleep(_REVEAL_POLL_MS / 1000)
        prepare_kwargs = {}
        if plan.get("goal_target"):
            prepare_kwargs["goal_target"] = plan["goal_target"]
        prepared = await _prepare_action_with_resource_fallback(
            entry, session_id, action, value_ref, **prepare_kwargs)


async def _with_action_failure_evidence(entry: dict, result: dict) -> dict:
    """Attach a redacted screenshot to terminal action failures."""
    if (result.get("ok") or result.get("approval_required")
            or result.get("error_class") in {"approval_pending", "forbidden"}
            or result.get("screenshot_path") or entry.get("page") is None):
        return result
    shot = await _capture_screenshot(entry)
    if shot:
        result = dict(result)
        result["screenshot_path"] = shot
        result["sensitive"] = bool(entry.get("authenticated"))
    return result


async def op_act(*, session_id: str, owner: str | None, action: str,
                 value_ref: str | None = None,
                 approval_token: str | None = None,
                 goal_query: str | None = None) -> dict:
    entry, validation_error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "error_class": validation_error}
    async with entry["lock"]:
        if approval_token:
            plan = entry["pending_actions"].get(approval_token)
            if not plan:
                cached = entry.get("completed_approvals", {}).get(approval_token)
                if cached and time.time() - cached["ts"] <= _APPROVAL_RESULT_TTL_S:
                    return dict(cached["result"])
                return await _with_action_failure_evidence(
                    entry, {"ok": False, "error_class": "approval_invalid"})
            executed = await _execute_plan(entry, approval_token, plan)
            if executed.get("error_class") not in {
                    "target_changed", "page_changed"}:
                executed = await _with_action_failure_evidence(entry, executed)
                entry.setdefault("completed_approvals", {})[approval_token] = {
                    "ts": time.time(), "result": dict(executed)}
                return executed
            if executed.get("detail") == "click_actionability_timeout":
                await _dismiss_obstructing_overlay(entry, settle=True)
            entry["pending_actions"].pop(approval_token, None)
            entry["gate_pending"] = False
            key = str(plan.get("replan_key") or "")
            replans = entry.setdefault("action_replans", {})
            count = int(replans.get(key, 0)) + 1
            replans[key] = count
            if count > _MAX_ACTION_REPLANS:
                result = {"ok": False, "error_class": "target_unstable",
                          "replans": count - 1}
                entry.setdefault("completed_approvals", {})[approval_token] = {
                    "ts": time.time(), "result": result}
                return await _with_action_failure_evidence(entry, result)
            original_action = str(plan.get("original_action") or action)
            prepare_kwargs = {}
            if plan.get("goal_target"):
                prepare_kwargs["goal_target"] = plan["goal_target"]
            prepared = await _prepare_action_with_resource_fallback(
                entry, session_id, original_action, plan.get("value_ref"),
                **prepare_kwargs)
            result = await _handle_prepared_action_with_replans(
                entry, session_id, original_action, plan.get("value_ref"),
                prepared)
            result = await _with_action_failure_evidence(entry, result)
            entry.setdefault("completed_approvals", {})[approval_token] = {
                "ts": time.time(), "result": dict(result)}
            return result
        if entry.get("gate_pending"):
            return {"ok": False, "error_class": "approval_pending"}
        goal_target = ""
        explicit_goal = (goal_query if isinstance(goal_query, str)
                         and goal_query.strip() else None)
        if explicit_goal is not None:
            goal_target = await _reduce_site_goal(explicit_goal)
        elif action_resolver.is_goal_navigation_request(action):
            # The planner has already reduced this to one elementary action.
            # Keep its complete natural target extractively: a second model
            # pass can otherwise erase a status/year facet such as "passate".
            parsed_goal = action_resolver.parse_action(action)
            if parsed_goal.get("ok"):
                goal_target = str(parsed_goal.get("target") or "").strip()
        if explicit_goal is not None or goal_target:
            if not goal_target:
                return await _with_action_failure_evidence(
                    entry, {"ok": False, "error_class": "goal_unresolved"})
        prepare_kwargs = ({"goal_target": goal_target}
                          if goal_target else {})
        prepared = await _prepare_action_with_resource_fallback(
            entry, session_id, action, value_ref, **prepare_kwargs)
        result = await _handle_prepared_action_with_replans(
            entry, session_id, action, value_ref, prepared)
        return await _with_action_failure_evidence(entry, result)


async def op_goto(**kwargs) -> dict:
    return await op_act(action=f"vai {kwargs.pop('url', '')}", **kwargs)


async def op_click(**kwargs) -> dict:
    return await op_act(action=f"clicca {kwargs.pop('target', '')}", **kwargs)


async def op_fill(**kwargs) -> dict:
    return await op_act(action=f"compila {kwargs.pop('target', '')}", **kwargs)


async def op_submit(**kwargs) -> dict:
    return await op_act(action=f"invia {kwargs.pop('target', '')}", **kwargs)


async def op_wait(**kwargs) -> dict:
    return await op_act(action=f"attendi {kwargs.pop('seconds', 2)}", **kwargs)
