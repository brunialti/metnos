# SPDX-License-Identifier: MIT
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
  §3.1 FIX D — route() abortisce fuori-allowlist; WebRTC neutralizzato;
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
from playwright_sidecar import captcha_solver
from playwright_sidecar import redaction
from playwright_sidecar import action_resolver
from playwright_sidecar import browser_surface
from playwright_sidecar import cookie_privacy
from playwright_sidecar import login_navigation
from playwright_sidecar import collection_context
import sites_audit
import sites_observed  # ADR 0191 P4 — codici osservativi navigazione
import sites_origin  # ADR 0191 P2 — il consenso appartiene a un'ORIGINE
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
    from playwright_sidecar import browser_engine
    if browser_engine.incompatible_techniques(stealth_techniques):
        raise RuntimeError("browser_technique_unsupported")
    # UA del motore selezionato — nessun override (forzare
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
    "METNOS_SITES_LOCAL_RESOLVER_TIMEOUT_MS", default=20000,
    minimum=1000, maximum=20000
)
_CLICK_TIMEOUT_MS = _bounded_int_env(
    "METNOS_SITES_CLICK_TIMEOUT_MS", default=6000, minimum=1000, maximum=20000
)
_MODEL_FALLBACKS_ENABLED = _enabled_env(
    "METNOS_SITES_MODEL_FALLBACKS", default=True)
# On collection pages without requested items the route is chosen by the
# Frontier workload when it is configured (decision of 7/10/2026); without
# Frontier the local decision stands. METNOS_SITES_FRONTIER_ROUTES=0 disables.
_FRONTIER_COLLECTION_ROUTES = _enabled_env(
    "METNOS_SITES_FRONTIER_ROUTES", default=True)
_RESOURCE_DISCOVERY_MS = 1000 # finestra bounded per richieste client-side
_REVEAL_SETTLE_MS = 2000      # attesa bounded target dopo controllo reveal
_LOGIN_ENTRY_SETTLE_S = 5.0   # attesa totale per un ingresso ancora vuoto
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
_MAX_LOGIN_ENTRY_STEPS = login_navigation.MAX_ACTIONS
_MAX_GOAL_STEPS = 4           # steps that MOVED something
_MAX_GOAL_STERILE = 3         # own budget for empty steps: a click that moves
                              # nothing must neither starve the four
                              # exploration steps nor repeat forever
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

_ACCESSIBLE_ACTION_NAME_JS = r"""
  const metnosNameOf = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .filter(Boolean).map(id => document.getElementById(id))
      .filter(Boolean).map(x => x.innerText || x.textContent || '').join(' ');
    const alt = el.querySelector && el.querySelector('img[alt]');
    return el.getAttribute('aria-label') || labelled ||
      el.getAttribute('title') || (alt ? alt.getAttribute('alt') : '') ||
      el.innerText ||
      ((el.type === 'submit' || el.type === 'button') ? el.value : '') || '';
  };
"""


_ENUMERATE_ACTION_TARGETS_JS = r"""
() => {
""" + _ACCESSIBLE_ACTION_NAME_JS + r"""
  // An accessible name is authored by hand and can be wrong: a page may ship
  // an unresolved translation key as its aria-label, which then hides the
  // label the page actually displays. The visible text travels next to the
  // name instead of being replaced by it, so neither can hide the other. It
  // is bounded like the pointer fallback: a control label is short.
  const metnosTextOf = el => {
    const text = (el.innerText || el.textContent || '').trim()
      .replace(/\s+/g, ' ');
    return text.length <= 160 ? text : '';
  };
  document.querySelectorAll('[data-metnos-action-id]').forEach(
    el => el.removeAttribute('data-metnos-action-id'));
  const standardSelector =
    'a,button,input,textarea,select,[role=button],[role=link],'
    + '[role=tab],[role=menuitem],[role=checkbox],[role=radio],'
    + '[role=combobox],[role=option],[role=menuitemradio],'
    + '[contenteditable=true],summary,'
    + '[tabindex]:not([tabindex="-1"]),[onclick]';
  const standard = Array.from(document.querySelectorAll(standardSelector));
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
  // cursor:pointer is inherited: a custom component, its native control and
  // the control's label can describe one action. Keep the semantic control
  // only when the pointer node has the same observed label and a single
  // standard target. Explicit independent actions and distinct labels stay.
  // querySelectorAll is already in document order. Binary search locates the
  // first possible descendant without rescanning every wrapper's subtree.
  const duplicateOfStandard = el => {
    if (typeof el.onclick === 'function' || el.matches(
        '[aria-controls],[aria-expanded],[aria-haspopup],[popovertarget],[commandfor]'))
      return false;
    const sameLabel = target => {
      const name = metnosNameOf(el).trim().replace(/\s+/g, ' ');
      const text = metnosTextOf(el);
      if (!name || !text || name !== metnosNameOf(target).trim().replace(/\s+/g, ' ')
          || text !== metnosTextOf(target)) return false;
      const rect = target.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) return false;
      for (let p = target, depth = 0; p; p = p.parentElement, depth++) {
        const style = getComputedStyle(p);
        if (depth >= 64 || p.hidden || style.display === 'none' || style.visibility !== 'visible'
            || style.contentVisibility === 'hidden'
            || Number.parseFloat(style.opacity || '1') < 0.05) return false;
      }
      return true;
    };
    const ancestor = el.parentElement?.closest(standardSelector);
    if (ancestor) return sameLabel(ancestor);
    let lo = 0, hi = standard.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (standard[mid].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
        lo = mid + 1;
      else hi = mid;
    }
    const target = standard[lo];
    return !!target && el.contains(target)
      && !(standard[lo + 1] && el.contains(standard[lo + 1])) && sameLabel(target);
  };
  const canonicalPointer = pointer.filter(el => !duplicateOfStandard(el));
  // Expensive accessible-name/context/topmost extraction is bounded. Preserve
  // every visible semantic control ahead of off-viewport/hidden controls so a
  // portal menu appended late in a very large DOM is still observable, while
  // retaining a bounded tail for scroll/reveal discovery.
  const visibleEls = [];
  const otherEls = [];
  const ordered = Array.from(new Set([...standard, ...canonicalPointer]));
  ordered.sort((a, b) => {
    const relation = a.compareDocumentPosition(b);
    if (relation & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (relation & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  for (const [order, el] of ordered.entries()) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    const rendered = r.width >= 2 && r.height >= 2 &&
      st.visibility !== 'hidden' && st.display !== 'none' &&
      Number.parseFloat(st.opacity || '1') >= 0.05;
    const ix = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const iy = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    const visibleRatio = r.width > 0 && r.height > 0
      ? (ix * iy) / (r.width * r.height) : 0;
    const item = {el, r, st, rendered, visibleRatio, order};
    (rendered && visibleRatio >= 0.2 ? visibleEls : otherEls).push(item);
  }
  const els = [
    ...visibleEls.slice(0, 480),
    ...otherEls.slice(0, 160),
  ].slice(0, 640);
  const out = [];
  let n = 0;
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
    const levels = [];
    for (let p = el.parentElement, depth = 0; p && depth < 8;
         p = p.parentElement, depth++) {
      const labelled = p.getAttribute('aria-label') || '';
      const parts = labelled.trim() ? [labelled.trim().slice(0, 200)] : [];
      const headings = Array.from(p.querySelectorAll(
        ':scope > h1,:scope > h2,:scope > h3,:scope > h4,' +
        ':scope > [role=heading]')).slice(0, 4);
      for (const heading of headings) {
        const r = heading.getBoundingClientRect();
        const st = getComputedStyle(heading);
        if (r.width < 2 || r.height < 2 || st.display === 'none' ||
            st.visibility === 'hidden' ||
            Number.parseFloat(st.opacity || '1') < 0.05) continue;
        const text = (heading.innerText || '').trim().replace(/\s+/g, ' ');
        if (text) parts.push(text.slice(0, 200));
      }
      if (parts.length) levels.push(parts);
      if (p.matches('main,[role=main],body')) break;
    }
    // A field heading must not hide the collection containing its row. Keep
    // only observed labels/headings, never the row's private record text.
    return Array.from(new Set(levels.reverse().flat())).join(' | ').slice(0, 300);
  };
  const enumeratedIds = new Set();
  // Keep record context local: candidates expose its digest; the collector
  // retains row text privately for extraction-source association. Repeated
  // actions follow visible rows, never their position or editable values.
  const recordExcluded = 'nav,menu,header,footer,aside,[role=navigation],'
    + '[role=menu],[role=menuitem],input,textarea,select,option,'
    + '[contenteditable]:not([contenteditable="false"]),[data-metnos-redact="1"]';
  const recordFamilies = new Map();
  const actionFamilies = new Map();
  const familyMembers = new Map();
  for (const {el, rendered} of els) {
    if (!rendered || !el.matches('a,button,[role=button],[role=link]')
        || el.closest(recordExcluded)) continue;
    const family = JSON.stringify([el.tagName, el.getAttribute('role') || '',
      el.getAttribute('type') || '', metnosNameOf(el).trim().replace(/\s+/g, ' ')]);
    actionFamilies.set(el, family);
    if (!familyMembers.has(family)) familyMembers.set(family, []);
    familyMembers.get(family).push(el);
    // Match the consumer's eight ancestor scopes: it starts at el.parentElement
    // and looks up each scope in its parent, including the eighth scope.
    for (let child = el.parentElement, depth = 0;
         child && child.parentElement && depth < 8;
         child = child.parentElement, depth++) {
      const parent = child.parentElement;
      if (!recordFamilies.has(parent)) recordFamilies.set(parent, new Map());
      const families = recordFamilies.get(parent);
      if (!families.has(family)) families.set(family, new Map());
      const children = families.get(family);
      children.set(child, (children.get(child) || 0) + 1);
    }
  }
  const completeRecordFamilies = new Map();
  const recordFamilyComplete = (parent, family, children) => {
    if (!completeRecordFamilies.has(parent)) completeRecordFamilies.set(parent, new Map());
    const cached = completeRecordFamilies.get(parent);
    if (!cached.has(family)) {
      // The depth-bounded index can omit a deeper action, or an action that
      // is itself another action's scope. Check only the already observed
      // family members before trusting both row and sibling counts.
      const indexed = Array.from(children.values()).reduce((sum, count) => sum + count, 0);
      const observed = familyMembers.get(family).filter(
        member => member !== parent && parent.contains(member)).length;
      cached.set(family, indexed === observed);
    }
    return cached.get(family);
  };
  const recordTexts = new Map();
  const recordTextDiagnostics = new Map();
  const recordDiagnostics = new Map();
  const recordTextOf = scope => {
    if (recordTexts.has(scope)) return recordTexts.get(scope);
    const diagnostic = {nodes: 0, excluded: 0, hidden: 0, depth_limit: 0,
      hidden_attribute: 0, display_none: 0, visibility: 0,
      content_visibility: 0, opacity: 0, geometry: 0, visible: 0, chars: 0,
      node_limit: false, char_limit: false};
    recordTextDiagnostics.set(scope, diagnostic);
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    const parts = [];
    let inspected = 0, size = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      diagnostic.nodes = ++inspected;
      if (inspected > 512) {
        diagnostic.node_limit = true; recordTexts.set(scope, ''); return '';
      }
      const parent = node.parentElement;
      const text = (node.nodeValue || '').trim().replace(/\s+/g, ' ');
      if (!parent || !text) continue;
      if (parent.closest(recordExcluded)) { diagnostic.excluded++; continue; }
      let hidden = false;
      for (let p = parent, depth = 0; p; p = p.parentElement, depth++) {
        const style = getComputedStyle(p);
        if (depth >= 64 || p.hidden || style.display === 'none' || style.visibility !== 'visible'
            || style.contentVisibility === 'hidden'
            || Number.parseFloat(style.opacity || '1') < 0.05) {
          diagnostic.hidden++;
          diagnostic.depth_limit += Number(depth >= 64);
          diagnostic.hidden_attribute += Number(p.hidden);
          diagnostic.display_none += Number(style.display === 'none');
          diagnostic.visibility += Number(style.visibility !== 'visible');
          diagnostic.content_visibility += Number(style.contentVisibility === 'hidden');
          diagnostic.opacity += Number(Number.parseFloat(style.opacity || '1') < 0.05);
          hidden = true; break;
        }
      }
      if (hidden) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rect = range.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) { diagnostic.geometry++; continue; }
      size += text.length;
      diagnostic.chars = size;
      if (size > 4000) {
        diagnostic.char_limit = true; recordTexts.set(scope, ''); return '';
      }
      diagnostic.visible++;
      parts.push(text);
    }
    const text = parts.join(' ');
    recordTexts.set(scope, text);
    return text;
  };
  const recordContextOf = el => {
    const family = actionFamilies.get(el);
    const diagnostic = {family: family ? 'eligible' :
      !el.matches('a,button,[role=button],[role=link]') ? 'not_action' :
      el.closest(recordExcluded) ? 'excluded' : 'not_rendered',
      levels: [], depth_limit: false,
      pool_truncated: visibleEls.length > 480 || otherEls.length > 160};
    recordDiagnostics.set(el, diagnostic);
    if (!family) return '';
    for (let scope = el.parentElement, depth = 0;
         scope && scope.parentElement && depth < 8;
         scope = scope.parentElement, depth++) {
      const level = {depth, outcome: ''};
      diagnostic.levels.push(level);
      if (scope.matches('body,main,nav,header,footer,aside,form')) {
        level.outcome = 'boundary'; level.boundary = scope.tagName.toLowerCase(); break;
      }
      const children = recordFamilies.get(scope.parentElement)?.get(family);
      if (!children) { level.outcome = 'family_missing'; continue; }
      level.actions = children.get(scope) || 0;
      level.siblings = children.size;
      level.unique_siblings = Array.from(children.values()).filter(count => count === 1).length;
      if (!children.has(scope)) { level.outcome = 'scope_missing'; continue; }
      if (children.get(scope) !== 1) { level.outcome = 'multiple_actions'; continue; }
      const semantic = scope.matches('tr,li,article,[role=row],[role=listitem]');
      const repeated = children.size > 1 && level.unique_siblings > 1;
      level.semantic = semantic; level.repeated = repeated;
      if (!semantic && !repeated) { level.outcome = 'not_record'; continue; }
      if (!recordFamilyComplete(scope.parentElement, family, children)) {
        level.outcome = 'family_incomplete'; continue;
      }
      const text = recordTextOf(scope);
      level.text = recordTextDiagnostics.get(scope);
      if (text && text !== metnosNameOf(el).trim().replace(/\s+/g, ' ')) {
        level.outcome = 'bound';
        return JSON.stringify([scope.tagName, scope.getAttribute('role') || '', text]);
      }
      level.outcome = text ? 'action_only' : 'no_text';
    }
    diagnostic.depth_limit = diagnostic.levels.length === 8
      && diagnostic.levels[7].outcome !== 'boundary';
    return '';
  };
  for (const item of els) {
    const {el, r, st, rendered, visibleRatio, order} = item;
    const id = `m${++n}`;
    el.setAttribute('data-metnos-action-id', id);
    enumeratedIds.add(id);
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
    const ancestorActionIds = [];
    for (let p = el.parentElement, depth = 0; p && depth < 12;
         p = p.parentElement, depth++) {
      const actionId = p.getAttribute('data-metnos-action-id');
      if (actionId && enumeratedIds.has(actionId)) ancestorActionIds.push(actionId);
    }
    const topmost = (() => {
      if (!visible) return false;
      const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
      const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
      const top = document.elementFromPoint(x, y);
      return top === el || !!(top && el.contains(top));
    })();
    out.push({
      id, dom_order: order, tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      name: metnosNameOf(el),
      text: metnosTextOf(el),
      label, context_name: contextOf(el),
      _record_context: recordContextOf(el),
      _record_diagnostics: recordDiagnostics.get(el),
      placeholder: el.getAttribute('placeholder') || '',
      href: el.href || '', download: el.hasAttribute('download'),
      dom_id: el.id || '', ancestor_ids: ancestors,
      ancestor_action_ids: ancestorActionIds,
      control_targets: controlsOf(el),
      aria_expanded: el.getAttribute('aria-expanded') || '',
      aria_haspopup: el.getAttribute('aria-haspopup') || '',
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
    const glyph = /^[x\u00d7\u2715\u2716]$/i.test((rawName || '').trim());
    // A close control is very often a bare SVG with no accessible name at
    // all: requiring a literal "x" left those modals standing. Measured on
    // turn 6a4a16c3 (10/9/2026), where the app-promotion modal over the login
    // form was never dismissed and its backdrop swallowed the submit.
    // Namelessness alone decides nothing: the geometry below still requires
    // the close corner of a modal root, `visible` requires it to be topmost,
    // and a submitter or navigating control is refused before this point.
    const er = el.getBoundingClientRect();
    const nameless = !normalize(rawName) &&
      er.width <= 64 && er.height <= 64;
    if (!glyph && !nameless) return false;
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
  // A close affordance is often not a control at all. Measured on the real
  // page (10/9/2026): `<span class="popup-close">`, 24x32, pointer cursor, no
  // text and no accessible name, over a fixed full-viewport overlay at
  // z-index 999999 - so every click on the form underneath went into it.
  // A semantic-only query cannot see that node, so nothing was ever
  // dismissed. These candidates are added, never preferred: they must still
  // pass `visible` (topmost at their centre), the close-corner geometry of a
  // modal root, and the submitter/navigation refusals below.
  const layers = [];
  for (const el of document.querySelectorAll('div,section,aside')) {
    const st = getComputedStyle(el);
    if (st.position !== 'fixed' && st.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < innerWidth * innerHeight * 0.12) continue;
    layers.push(el);
    if (layers.length >= 4) break;
  }
  for (const layer of layers) {
    let scanned = 0;
    for (const el of layer.querySelectorAll('*')) {
      if (++scanned > 400) break;
      if (el.closest('svg') || controls.includes(el)) continue;
      if (getComputedStyle(el).cursor !== 'pointer') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8 || r.width > 64 || r.height > 64) continue;
      if (normalize(el.textContent || '')) continue;   // muto, non etichettato
      controls.push(el);
    }
  }
  const ranked = [];
  const navigating = [];
  for (const el of controls) {
    if (!visible(el)) continue;
    const root = modalRoot(el);
    if (!root) continue;
    const rawName = nameOf(el).trim();
    const name = normalize(rawName);
    const exactExit = allowed.has(name);
    // Closing a privacy panel does not prove that optional cookies were
    // rejected. That procedure requires an exact rejection label.
    const icon = !markers.length && iconExit(el, root, rawName);
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
(includeInteractiveContent = false) => {
  const excluded = [
    'input', 'textarea', 'select', 'option',
    'nav', 'menu', 'header', 'footer', 'aside',
    '[role=navigation]', '[role=menu]', '[role=menuitem]',
    ...(includeInteractiveContent ? [
      '[contenteditable]:not([contenteditable="false"])', '[data-metnos-redact="1"]'
    ] : [
      'a', 'button', 'summary', '[role=button]', '[role=link]', '[role=tab]',
      '[contenteditable=true]'
    ])
  ].join(',');
  const groups = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    const text = (node.nodeValue || '').trim().replace(/\s+/g, ' ');
    if (!parent || !text || parent.closest(excluded)) continue;
    let r = parent.getBoundingClientRect();
    const st = getComputedStyle(parent);
    if (includeInteractiveContent) {
      if (st.visibility !== 'visible') continue;
      // A hidden ancestor can leave its descendants' own rectangles intact.
      let opacity = 1, hidden = false;
      for (let el = parent; el; el = el.parentElement) {
        const style = el === parent ? st : getComputedStyle(el);
        opacity *= Number.parseFloat(style.opacity || '1');
        if (style.display === 'none' || style.contentVisibility === 'hidden' || opacity < 0.05) {
          hidden = true;
          break;
        }
      }
      if (hidden) continue;
      // display:contents has no element box, but its record text is visible.
      // Any other parent keeps its own box: a clipped 1px or zero-size box
      // hides text whose own layout rectangle is still non-empty.
      if (st.display === 'contents') {
        const range = document.createRange();
        range.selectNodeContents(node);
        r = range.getBoundingClientRect();
      }
    }
    if (r.width < 2 || r.height < 2 || st.display === 'none' ||
        st.visibility === 'hidden' || Number.parseFloat(st.opacity || '1') < 0.05)
      continue;
    const block = parent.closest(
      'h1,h2,h3,h4,h5,h6,[role=heading],tr,p,li,dt,dd,' +
      'a,button,summary,[role=link],[role=button],[role=tab],section,article,main,div')
      || parent;
    const headingSelector = 'h1,h2,h3,h4,h5,h6,[role=heading]';
    const heading = includeInteractiveContent
      ? parent.closest(headingSelector) !== null : block.matches(headingSelector);
    const previous = groups.get(block) || (heading ? '[heading]' : '');
    groups.set(block, `${previous} ${text}`.trim().slice(0, 4000));
    if (groups.size >= 400) break;
  }
  // Preserve the cap: deduplicating here can hide that group 400 stopped
  // the traversal, making a partial observation appear complete.
  return Array.from(groups.values());
}
"""

_GOAL_LINKS_JS = r"""
() => Array.from(document.querySelectorAll('a[href]'))
        .map(a => a.href).slice(0, 400)
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
  const step = Math.max(240, Number(target.clientHeight || innerHeight) * 0.85);
  const next = Math.min(maximum, before + step);
  target.scrollTop = next;
  return {moved: next > before + 2, before, after: next, maximum};
}
"""

_ELEMENT_STATE_JS = r"""
(el) => {
""" + _ACCESSIBLE_ACTION_NAME_JS + r"""
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
    name: metnosNameOf(el),
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
      aria_haspopup: el.getAttribute('aria-haspopup') || '',
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
    """Rimuove screenshot e copie diagnostiche oltre il TTL (§3.3)."""
    d = _SHOTS_ROOT / _owner_slug(owner)
    if not d.exists():
        return
    now = time.time()
    for p in (*d.glob("*.png"), *d.glob("*.page.json")):
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
                             provenance: dict | None = None,
                             request_url: str = "") -> None:
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
    # Keep the exact blocked top-level destination only in the private,
    # short-lived browser session.  It may contain a one-time login token:
    # never put it in an approval, audit event or agent result.
    if (resource_type == "document" and provenance.get("navigation")
            and request_url
            and urllib.parse.urlsplit(request_url).scheme in {"http", "https"}
            and _host_of_url(request_url) == host):
        observation["navigation_url"] = request_url


def _audit_auto_allow(ctx: dict | None, host: str, resource_type: str) -> None:
    """Traccia la PRIMA ammissione automatica di un host.

    Lo sblocco automatico toglie il gate, non la memoria: senza questa riga
    «con chi ha parlato la sessione» diventerebbe irricostruibile proprio nel
    modo d'uso piu' permissivo (§2.8)."""
    try:
        sites_audit.record("allowlist_auto_allow",
                           owner=(ctx or {}).get("owner", ""),
                           domain=(ctx or {}).get("domain", ""),
                           added_host=host, resource_type=resource_type,
                           source="user_pref_auto_allow")
    except Exception:  # noqa: BLE001 — un audit non blocca una navigazione
        pass


def _make_route_guard(allowlist: set[str],
                      blocked_requests: dict[str, dict] | None = None,
                      auto_allow: bool = False,
                      auto_allowed: set[str] | None = None,
                      audit_ctx: dict | None = None):
    """Ritorna un handler `context.route` che ABORTISCE le richieste fuori
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
                            _request_provenance(request), request_url=url)
                    # Sblocco automatico (preferenza per-utente, default OFF).
                    # L'utente ha scelto di rinunciare al gate: l'host entra
                    # nell'allowlist VIVA della sessione — la closure osserva
                    # lo stesso insieme — e ci resta per il resto della
                    # sessione. Non e' silenzioso: la PRIMA ammissione di ogni
                    # host finisce nel registro d'audit, cosi' «chi ha parlato
                    # con chi» resta ricostruibile anche quando nessuno ha
                    # dovuto approvare.
                    if auto_allow and host:
                        allowlist.add(host)
                        if auto_allowed is not None and host not in auto_allowed:
                            auto_allowed.add(host)
                            _audit_auto_allow(audit_ctx, host, resource_type)
                        await route.continue_()
                        return
                    await route.abort()
                return
            if scheme in ("about", "chrome-error"):
                await route.continue_()
            else:
                await route.abort()
        except Exception:
            # In dubbio: abortisce (fail-closed sul confine di rete).
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
                deadline = 600 if e.get("user_control_pending") else _GATE_MAX_S
                if now - started <= deadline:
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

async def _reuse_compatible_session(*, owner: str, owner_user_id: str,
                                    open_host: str,
                                    allowlist: set[str], session_label: str,
                                    credential_mode: str, browser_mode: str,
                                    stealth_techniques: tuple[str, ...],
                                    task_binding, credential_binding) -> dict | None:
    """Trova una sessione autenticata viva con identico confine operativo."""
    for session_id, entry in reversed(tuple(_sessions.items())):
        if (_validate(session_id) is None
                or entry.get("owner") != owner
                or entry.get("owner_user_id") != owner_user_id
                or entry.get("open_host") != open_host
                or set(entry.get("allowlist_declared")
                       or entry.get("allowlist") or ()) != set(allowlist)
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
            "session_reuse", owner=owner, owner_user_id=owner_user_id,
            session_id=session_id,
            domain=entry.get("domain", ""), url=scrub_url(page.url))
        return {
            "ok": True, "session_id": session_id,
            "url": scrub_url(page.url), "title": title, "reused": True,
            **({"reason_code": entry["observed_reason"]}
               if entry.get("observed_reason") else {}),
        }
    return None


async def op_open(*, owner: str, url: str, allowlist_arg=None,
                  owner_user_id: str | None = None,
                  session_label: str = "",
                  approval_token: str | None = None,
                  task_name: str | None = None,
                  task_owner_user_id: str | None = None,
                  credential_mode: str = "default",
                  stealth: bool = False,
                  stealth_techniques=None,
                  browser_mode: str = "headless",
                  auto_allow_resources: bool = False,
                  lang: str | None = None) -> dict:
    """Apre UNA sessione su `url` (§3.4 open_sites fa fan-out su N url).

    `stealth` e' il master per-turno; `stealth_techniques` e' la selezione
    indipendente fissata alla sessione. Il ceiling deployment puo' azzerarla.
    `lang` determina locale/timezone (fix #9)."""
    if not isinstance(owner, str) or not owner:
        return {"ok": False, "error": "owner required",
                "error_class": "forbidden"}
    if (not isinstance(owner_user_id, str)
            or not owner_user_id.strip() or len(owner_user_id) > 160):
        return {"ok": False, "error": "logical owner required",
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
        if (not isinstance(task_name, str) or len(task_name) > 160
                or not isinstance(task_owner_user_id, str)
                or not task_owner_user_id.strip()
                or len(task_owner_user_id) > 160
                or task_owner_user_id != owner_user_id):
            return {"ok": False, "error": "invalid task mandate",
                    "error_class": "mandate_scope_exceeded"}
        task_binding = task_mandates.sites_binding(
            task_name, task_owner_user_id, default_host)
        if not isinstance(task_binding, dict):
            return {"ok": False, "error": "task has no sites mandate",
                    "error_class": "mandate_scope_exceeded"}
    elif credential_mode == "default":
        credential_binding = credential_mandates.resolve_sites_binding(
            owner_user_id, default_host)
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
    # Host ammessi dallo sblocco automatico: serve solo a non ripetere
    # la riga d'audit a ogni richiesta dello stesso host.
    auto_allowed_hosts: set[str] = set()
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
    from playwright_sidecar import browser_engine
    try:
        incompatible = browser_engine.incompatible_techniques(effective_techniques)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc),
                "error_class": "browser_unavailable",
                "reason_code": "browser_unavailable"}
    if incompatible:
        return {"ok": False, "error": "browser technique unsupported",
                "error_class": "browser_unavailable",
                "reason_code": "browser_unavailable",
                "browser_engine": browser_engine.selected(),
                "unsupported_techniques": list(incompatible)}
    if _st.technique_enabled(
            "reuse_live_session", techniques=effective_techniques):
        reused = await _reuse_compatible_session(
            owner=owner, owner_user_id=owner_user_id,
            open_host=default_host, allowlist=allowlist,
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
        # «Quota piena» da sola non dice niente a chi legge. I fatti che
        # servono per capire e per decidere sono tre, e li sappiamo tutti e
        # tre: quante sessioni sono aperte, SU QUALI SITI, e fra quanto la
        # piu' vecchia scade da sola (7/8/2026, richiesta di Roberto).
        aperte = [e for e in _sessions.values() if e.get("owner") == owner]
        ospiti = sorted({str(e.get("open_host") or "") for e in aperte if
                         e.get("open_host")})
        try:
            attesa = max(0, int(min(
                (_TTL_IDLE_S - (time.time() - float(e.get("last_used") or 0))
                 for e in aperte), default=0)))
        except Exception:  # noqa: BLE001 — un conto approssimato non blocca
            attesa = 0
        return {"ok": False, "error": "per-user session quota reached",
                "error_class": "quota_exceeded",
                "open_sessions": len(aperte), "open_hosts": ospiti[:4],
                "quota": _PER_USER_QUOTA,
                "idle_release_s": int(_TTL_IDLE_S),
                "retry_after_s": attesa}
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
            "**/*", _make_route_guard(
                allowlist, blocked_requests,
                auto_allow=bool(auto_allow_resources),
                auto_allowed=auto_allowed_hosts,
                audit_ctx={"owner": owner, "domain": _canonical_host(
                    _host_of_url(url)), "session_label": session_label}))
        await browser_engine.apply_websocket_policy(context)
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
        # Confine DICHIARATO all'apertura, congelato. `allowlist` e' il
        # set VIVO che il guardiano muta quando lo sblocco automatico
        # ammette un host: confrontare quello per il riuso significava
        # non riusare mai piu' una sessione appena un asset veniva
        # ammesso — e la richiesta successiva sbatteva nella quota
        # (turno reale cfc1b52e, 7/8/2026).
        "allowlist_declared": frozenset(allowlist),
        # Pre-autorizzazione esplicita dell'utente (preferenza «sblocco
        # automatico»): vale per la sessione, non oltre.
        "auto_allow": bool(auto_allow_resources),
        # Consensi gia dati in QUESTA sessione, per destinazione: chi ha
        # appena detto «vai» a un posto non deve ridirlo per quel posto.
        "approved_destinations": set(),
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
        "owner_user_id": owner_user_id,
        "credential_mandate": credential_binding,
        "credential_mode": credential_mode,
        "observed_reason": observed_reason,  # ADR 0191 P4 (slug o None)
        "lock": asyncio.Lock(),
    }
    try:
        title = await page.title()
    except Exception:
        title = ""
    sites_audit.record("session_open", owner=owner,
                       owner_user_id=owner_user_id, session_id=session_id,
                       domain=domain, url=page.url, allowlist=sorted(allowlist),
                       **({"reason": observed_reason} if observed_reason else {}))
    return {"ok": True, "session_id": session_id, "url": scrub_url(page.url),
            "title": title,
            **({"reason_code": observed_reason} if observed_reason else {})}


async def _redacted_viewport(entry: dict) -> bytes | None:
    """One privacy boundary for diagnostic and interactive screenshots."""
    page = entry["page"]
    if entry.get("secret_pending"):
        return None
    try:
        frames = page.frames
        for frame in frames:
            if await redaction.apply_redaction(frame) < 0:
                return None
        mask = [frame.locator(
            'input[type=password], input[type=email], '
            'input[autocomplete="username" i], '
            'input[autocomplete="email" i], '
            'input[autocomplete="one-time-code" i], '
            'input[name*="otp" i], input[id*="otp" i], '
            'input[name*="verification" i], input[id*="verification" i], '
            '[data-metnos-redact="1"]') for frame in frames]
        return await page.screenshot(full_page=False, mask=mask,
                                     mask_color="#000000")
    except Exception:
        return None


async def _capture_screenshot(entry: dict) -> str | None:
    data = await _redacted_viewport(entry)
    if data is None:
        return None
    owner = entry["owner"]
    _sweep_old_shots(owner)
    path = _shots_dir(owner) / f"{entry.get('_sid','s')}_{int(time.time()*1000)}.png"
    try:
        # O_EXCL + 0600 avoid a window with broader permissions.
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
    except OSError:
        return None
    return str(path)


async def op_user_control(*, action: dict, proof: str) -> dict:
    """An authenticated human controls only a pending login challenge.

    Coordinates refer to the last redacted viewport, never to a selector or
    page-provided code. Revisions reject duplicate/stale clicks. No polling,
    files, keyboard input or new browser/context are created here.
    """
    import base64
    import math
    from playwright_sidecar import user_control

    def fail(reason):
        return {"ok": False, "error_class": reason}

    if not isinstance(action, dict) or not user_control.verify(
            action, proof, user_control.instance_key()):
        return fail("forbidden")
    sid, owner = action.get("session_id"), action.get("owner")
    entry, error = _validate_owned(sid, owner)
    if entry is None:
        return fail(error)
    async with entry["lock"]:
        if (not entry.get("user_control_pending") or entry.get("gate_pending")
                or entry.get("authenticated") or entry.get("secret_pending")
                or time.time() - float(entry.get("factor_started", 0)) > 600):
            return fail("user_control_unavailable")
        page = entry["page"]
        operation = action.get("operation")
        if not isinstance(operation, str) or operation not in {"snapshot", "click", "scroll"}:
            return fail("invalid_args")
        if operation != "snapshot":
            frame = entry.get("user_control_frame") or {}
            if (not action.get("revision") or action["revision"] != frame.get("revision")
                    or page.url != frame.get("url")):
                return fail("page_changed")
            # A completed challenge closes manual control even before Resume.
            if await credential_injection.classify_login_surface(page) != "captcha_required":
                return fail("user_control_complete")
            if operation == "click":
                x, y = action.get("x"), action.get("y")
                if any(type(v) not in (int, float) or not math.isfinite(v)
                       or not 0 <= v <= 1 for v in (x, y)):
                    return fail("invalid_args")
            elif (not isinstance(action.get("direction"), str)
                  or action["direction"] not in {"up", "down"}):
                return fail("invalid_args")
            # Invalidate before acting, including on timeout: never replay.
            entry.pop("user_control_frame", None)
            if operation == "click":
                await page.mouse.click(x * frame["width"], y * frame["height"])
            else:
                await page.mouse.wheel(0, (1 if action["direction"] == "down" else -1)
                                       * frame["height"] * 0.65)
            # One bounded render interval, not a background watcher.
            await asyncio.sleep(0.35)
            sites_audit.record("login_user_action", owner=owner,
                               session_id=sid, action=operation)
        await _touch(entry)
        data = await _redacted_viewport(entry)
        if data is None:
            entry.pop("user_control_frame", None)
            return fail("redaction_failed")
        viewport = await page.evaluate("() => ({width:innerWidth,height:innerHeight})")
        revision = secrets.token_hex(16)
        entry["user_control_frame"] = {**viewport, "revision": revision, "url": page.url}
        return {"ok": True, "image": base64.b64encode(data).decode("ascii"),
                "revision": revision, "url": scrub_url(page.url),
                "pending": await credential_injection.classify_login_surface(page)
                           == "captcha_required"}


async def op_read(*, session_id: str, owner: str | None = None,
                  include_screenshot: bool = True,
                  include_forms: bool = False,
                  goal: str = "") -> dict:
    entry, validation_error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "error": validation_error,
                "error_class": validation_error}
    async with entry["lock"]:
        try:
            return await asyncio.wait_for(
                _read_impl(entry, session_id, include_screenshot,
                           include_forms, goal=goal),
                timeout=_OP_TIMEOUT_S)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "read timeout", "error_class": "timeout"}


# Quante righe estranee si attraversano prima di considerare finita la
# parte di pagina che riguarda il fine: fra due voci di un elenco ce ne
# stanno una o due; fra l'elenco e il piede, decine.
_MAX_VUOTO_TRATTO = 6
# Quanto si aspetta che una pagina finisca di caricare prima di leggerla.
_ATTESA_CARICAMENTO_S = 8.0


def _goal_text_span(testo: str, goal: str, *, max_char: int = 1800) -> str:
    """Il tratto di pagina che riguarda il fine: dalla prima all'ultima riga.

    Perche' non i «blocchi di contenuto» (l'estrattore che serve a decidere se
    il fine e' raggiunto): quello scarta per costruzione link e pulsanti, e un
    elenco di prenotazioni E' fatto di link — misurato il 7/8/2026, restituiva
    una riga su undici. E perche' non il testo pieno: sono per due terzi menu e
    piede, cioe' il traboccamento che l'utente ha visto.

    La regola e' una sola e non conosce siti: si prende dalla PRIMA riga che
    tocca il fine all'ULTIMA che lo tocca, tenendo tutto ciò che sta in mezzo —
    le righe interne (una citta', una data) non contengono la parola del fine
    ma sono la risposta. Fuori restano l'intestazione e il piede, che il fine
    non tocca. Tetto di caratteri dichiarato dal chiamante (§2.7).
    """
    # A movement verb ("go", "open") is how the request is phrased, not what
    # is being looked for: without dropping it the span used to start at "Skip
    # to main content", the first link of every accessible page in the world.
    # `goal_tokens` now drops it for everyone (verbs are goal noise), so it is
    # not redone by hand here: one single definition of what a token is.
    voluti = set(action_resolver.goal_tokens(goal, navigation=True))
    if not voluti:
        return ""
    righe = [r.strip() for r in str(testo or "").splitlines()]
    toccate = [i for i, r in enumerate(righe)
               if r and (voluti & set(
                   action_resolver.goal_tokens(r, navigation=True)))]
    if not toccate:
        return ""
    # Il tratto e' una CATENA, non un intervallo: si prosegue finche' i punti
    # che toccano il fine restano vicini. Un piede di pagina che nomina i
    # «viaggi» combacia col fine ma sta venti righe dopo l'ultima prenotazione:
    # con l'intervallo secco ci finiva dentro mezzo sito (7/8/2026).
    fine_catena = toccate[0]
    for precedente, successivo in zip(toccate, toccate[1:]):
        if successivo - precedente > _MAX_VUOTO_TRATTO:
            break
        fine_catena = successivo
    else:
        fine_catena = toccate[-1]
    tratto = [r for r in righe[toccate[0]:fine_catena + 1] if r]
    return "\n".join(tratto)[:max_char]


async def _read_impl(entry, session_id, include_screenshot, include_forms,
                     goal: str = "") -> dict:
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
    sensitive = bool(entry.get("authenticated"))
    shot = None
    if include_screenshot:
        shot = await _capture_screenshot(entry)
    out = {
        "ok": True, "session_id": session_id, "url": scrub_url(page.url),
        "title": title, "text": text, "sensitive": sensitive,
    }
    if entry.get("_source_scope_label"):
        out["_source_scope_label"] = str(entry["_source_scope_label"])
    if goal:
        # Leggere mentre la pagina carica restituisce «Caricamento…» al posto
        # del dato: il marcatore e' nel lessico (concetto `sites.loading_marker`)
        # e vale per ogni lingua e ogni sito. Attesa BOUNDED, poi si legge
        # comunque cio' che c'e' — mai un blocco.
        scaduta = time.time() + _ATTESA_CARICAMENTO_S
        marcatori = tuple(action_resolver.loading_marker_forms())
        while marcatori and time.time() < scaduta:
            minuscolo = text.lower()
            if not any(m in minuscolo for m in marcatori):
                break
            try:
                await page.wait_for_timeout(400)
                text = await page.locator("body").inner_text(timeout=3000)
            except Exception:  # noqa: BLE001
                break
    if collected:
        # Collection membership comes from an observation, not from the page
        # where traversal happened to return. Keep each captured snapshot;
        # an unobserved SPA change must not replace it at the same URL.
        current = _page_identity(page.url, _context_key(
            await _enumerate_candidates(page)), text)
        from sites_collection_sources import record_views
        sources = record_views(collected, entry.get("collection_record_views") or [], session_id)
        pages = [{"url": scrub_url(str(item.get("url") or "")),
                  "title": str(item.get("title") or ""), "text": str(item["text"]),
                  **({"_source_record_view": item["_source_record_view"]}
                     if item.get("_source_record_view") else {}),
                  "current": _page_identity(
                      str(item.get("url") or ""), str(item.get("context") or ""),
                      str(item["text"])) == current}
                 for item in sources]
        out["pages"] = pages
        text = "\n\n".join(item["text"] for item in pages if item["text"])
        out["collected_page_count"] = len(pages)
    out["text"] = text
    if goal:
        tratto = text if collected else _goal_text_span(text, goal)
        if tratto:
            out["goal_span"] = tratto
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
        # Un login e' uno STATO, non un'azione: se lo stato c'e' gia',
        # l'operazione e' gia' riuscita. Senza questa riga una sessione
        # autenticata e riusata rientrava nella macchina di accesso, cercava un
        # modulo che non c'e' — perche' l'utente e' dentro — e rispondeva «non
        # ho trovato un modulo di login», chiudendo il turno su un successo
        # (turno reale e43fefafb0a6463e, 7/8/2026). Il flag lo scrive solo un
        # login verificato di questo processo, e il riuso di sessione lo
        # pretende: non e' una supposizione.
        if entry.get("authenticated"):
            return {"ok": True, "logged_in": True,
                    "reason_code": "already_authenticated",
                    "session_id": session_id}
        entry["_sid"] = session_id
        entry.pop("user_control_pending", None)
        entry.pop("user_control_frame", None)
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
                if plan.get("kind") == "credential_origin" or plan.get("login_choice"):
                    entry.pop("login_flow", None)
                    return {"ok": True, "logged_in": False,
                            "reason_code": ("origin_unverified" if plan.get(
                                "kind") == "credential_origin" else "selector_missing"),
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

        async def _reject_privacy_overlay(*, settle: bool = False,
                                          redact=None) -> cookie_privacy.CookieOutcome:
            # Discovery and credential filling share the same bounded cookie
            # precondition; dismissals never consume login navigation steps.
            if redact is not None:
                entry["_cookie_redact"] = redact
            return await _clear_login_surface(entry, settle=settle)

        async def _reach_login_area(purpose: str = "login") -> dict:
            if purpose == "privacy_reject":
                outcome = await _reject_privacy_overlay(settle=True)
                return {"ok": outcome.status != "blocked",
                        "executed": outcome.status == "resolved",
                        "error_class": ("cookie_precondition_unresolved"
                                        if outcome.status == "blocked" else ""),
                        "primitive": "click"}
            if purpose == "login":
                reached = await _discover_login_entry(entry, session_id, flow)
                if reached.get("login_entry_refreshed"):
                    return await _discover_login_entry(entry, session_id, flow)
                return reached
            if int(flow.get("steps", 0)) >= _MAX_LOGIN_ENTRY_STEPS:
                return {"ok": False, "error_class": "login_step_limit"}
            if purpose != "continue":
                return {"ok": False, "error_class": "unsupported_action"}
            # After filling identity, continuation remains deterministic.
            action = "click login continue"
            prepared = await _prepare_action(
                entry, session_id, action, None, allow_model=False)
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
            if stage in {"username_fill", "primary_fill", "username_submit",
                         "primary_submit", "factor_pending", "factor_submit"}:
                flow["credentials_started"] = True
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
                    prepare_page=_reject_privacy_overlay,
                    resolve_captcha=lambda page: _resolve_login_captcha(
                        entry, session_id, flow, page),
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
            entry.pop("_cookie_redact", None)
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
        entry["user_control_pending"] = (res.get("reason_code") == "captcha_required"
                                           and not res.get("approval_required"))
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


def _goal_state_signature(url: str, candidates: list[dict]) -> str:
    """What the pilot can observe from here: the place and the open controls.

    It tells a step that moved something from an empty one. The place alone
    would not do: revealing the account menu does NOT change the URL and is
    real progress (real turn 2026-08-07, where that step opens the only way
    into the personal area). The controls alone would not do either: two
    different pages can expose the same menu.
    """
    posto = action_resolver.url_place_key(url)
    controlli = sorted(
        action_resolver.goal_candidate_key(item) for item in candidates)
    return hashlib.sha256(
        "\0".join([posto, *controlli]).encode("utf-8")).hexdigest()


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


def _safe_record_diagnostics(value) -> dict:
    """Keep only bounded counters and fixed outcomes, never DOM strings."""
    if not isinstance(value, dict) or value.get("family") not in (
            "eligible", "not_action", "not_rendered", "excluded"):
        return {}
    outcomes = {"boundary", "family_missing", "family_incomplete", "scope_missing", "multiple_actions",
                "not_record", "no_text", "action_only", "bound"}
    counters = {"nodes", "excluded", "hidden", "depth_limit", "hidden_attribute",
                "display_none", "visibility", "content_visibility", "opacity",
                "geometry", "visible", "chars"}
    result = {"family": value["family"], "levels": [],
              "depth_limit": value.get("depth_limit") is True,
              "pool_truncated": value.get("pool_truncated") is True}
    levels = value.get("levels")
    for level in (levels[:8] if isinstance(levels, list) else []):
        if (not isinstance(level, dict) or type(level.get("depth")) is not int
                or not 0 <= level["depth"] < 8
                or not isinstance(level.get("outcome"), str)
                or level["outcome"] not in outcomes):
            continue
        safe = {"depth": level["depth"], "outcome": level["outcome"]}
        if level.get("boundary") in ("body", "main", "nav", "header", "footer", "aside", "form"):
            safe["boundary"] = level["boundary"]
        safe.update({key: min(level[key], 1_000_000)
                     for key in ("actions", "siblings", "unique_siblings")
                     if type(level.get(key)) is int and level[key] >= 0})
        safe.update({key: level[key] for key in ("semantic", "repeated")
                     if type(level.get(key)) is bool})
        stats = level.get("text")
        if isinstance(stats, dict):
            safe["text"] = {key: min(stats[key], 1_000_000) for key in counters
                            if type(stats.get(key)) is int and stats[key] >= 0}
            safe["text"].update({key: stats[key] for key in ("node_limit", "char_limit")
                                 if type(stats.get(key)) is bool})
        result["levels"].append(safe)
    return result


async def _enumerate_candidates(page, *, record_contexts: dict | None = None) -> list[dict]:
    try:
        out = await asyncio.wait_for(
            page.evaluate(_ENUMERATE_ACTION_TARGETS_JS),
            timeout=_ENUMERATE_TIMEOUT_MS / 1000.0,
        )
        if not isinstance(out, list):
            return []
        for candidate in out:
            diagnostic = _safe_record_diagnostics(candidate.pop("_record_diagnostics", None))
            if diagnostic:
                candidate["_record_diagnostics"] = diagnostic
            context = candidate.pop("_record_context", "")
            if isinstance(context, str) and context:
                candidate["_record_context_key"] = hashlib.sha256(
                    context.encode("utf-8")).hexdigest()
                if record_contexts is not None:
                    try:
                        parts = json.loads(context)
                        if isinstance(parts, list) and len(parts) == 3 and isinstance(parts[2], str):
                            record_contexts[candidate["_record_context_key"]] = parts[2]
                    except (ValueError, TypeError):
                        pass
        return out
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
        if (candidate.get("in_viewport") is True
                and candidate.get("topmost") is False):
            # A fixed header can cover a fully intersecting element: the
            # ordinary "if needed" scroll would leave that element in place.
            await handle.evaluate("node => node.scrollIntoView({block: 'center', "
                                  "inline: 'center', behavior: 'instant'})")
        else:
            await handle.scroll_into_view_if_needed(timeout=1500)
        if hasattr(entry["page"], "wait_for_timeout"):
            await entry["page"].wait_for_timeout(100)
        return True
    except Exception:
        return False


def _bounded_action_prompt(*, goal: dict, state: dict,
                           observed: list[str] | dict, history: list[str],
                           forbidden: str) -> str:
    """Prompt chiuso per risolvere una sola azione elementare.

    I testi accessibili arrivano da una pagina non fidata: serializzarli come
    dati e ribadire il confine impedisce che diventino istruzioni operative.
    La primitiva e' gia' fissata dal codice; il modello sceglie soltanto un ID.
    """
    import i18n
    import prompt_loader
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
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


async def _expand_collection_by_scrolling(entry: dict, flow: dict, *,
                                           max_scrolls: int | None = None) -> bool:
    """Carica porzioni lazy di una collezione con scroll progressivo bounded."""
    if (not flow.get("collection") or flow.get("collection_scroll_complete")):
        return False
    changed_any = False
    limit = _MAX_COLLECTION_SCROLLS if max_scrolls is None else max_scrolls
    while int(flow.get("collection_scrolls", 0)) < limit:
        before = await _goal_content_signature(entry)
        try:
            scroll = await entry["page"].evaluate(_SCROLL_COLLECTION_JS)
        except Exception:
            flow["collection_scroll_limited"] = True
            break
        if not isinstance(scroll, dict):
            flow["collection_scroll_limited"] = True
            break
        if not scroll.get("moved"):
            flow["collection_scroll_complete"] = True
            break
        flow["collection_scrolls"] = int(
            flow.get("collection_scrolls", 0)) + 1
        progressed, _intermediate = await _wait_for_goal_content_change(
            entry, before)
        await _wait_for_content_settle(entry)
        after = await _goal_content_signature(entry)
        if (not progressed and after == before
                and scroll.get("after", scroll.get("maximum", 0))
                >= scroll.get("maximum", 0) - 2):
            flow["collection_scroll_complete"] = True
            break
        changed_any = changed_any or after != before
        target = str(flow.get("collection_target") or "")
        if target:
            candidates = await _enumerate_candidates(entry["page"])
            record = action_resolver.choose_goal_drilldown_candidate(
                target, candidates,
                collection_tokens=set(
                    flow.get("collection_scope_tokens") or ()))
            if record.get("ok") or record.get("error_class") \
                    == "selector_ambiguous":
                break
    if int(flow.get("collection_scrolls", 0)) >= limit:
        flow["collection_scroll_limited"] = not flow.get("collection_scroll_complete")
        flow["collection_scroll_complete"] = True
    return changed_any


async def _page_satisfies_goal(entry: dict, target: str,
                               candidates: list[dict] | None = None) -> bool:
    # The scope the planner declared, when it did. Empty means the resolver
    # falls back to the possession marker, exactly as before.
    scope = str(entry.get("goal_scope") or "")
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
    scope_url = scrub_url(entry["page"].url)
    # Which state facets this page OFFERS as a control: that is what tells
    # "I still have to press Past" from "here the upcoming ones have no name,
    # only dates".
    facce_offerte = action_resolver.offered_facet_tokens(candidates)
    if (isinstance(evidence, list)
            and action_resolver.page_satisfies_goal(
                target, evidence, scope_text=scope_url,
                facets_offered=facce_offerte, scope=scope)):
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
        target, filtered_lines, scope_text=scope_url,
        facets_offered=facce_offerte, scope=scope)


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


async def _goal_facet_signature(entry: dict) -> str:
    """The CONTENT of a facet: its evidence and the places it leads to.

    Not the whole body text, which `_goal_content_signature` signs: a toolbar
    that appears when a tab gets selected changes the body text without the
    content having moved an inch, and that is exactly the case to recognise.
    The destinations answer the opposite case: a list made of links alone
    leaves no textual evidence, but changes every place it points at.
    """
    page = entry["page"]
    try:
        evidence = await page.evaluate(_GOAL_EVIDENCE_JS)
    except Exception:
        evidence = []
    try:
        destinations = await page.evaluate(_GOAL_LINKS_JS)
    except Exception:
        destinations = []
    payload = [action_resolver.url_place_key(page.url) or scrub_url(page.url),
               evidence if isinstance(evidence, list) else [],
               sorted(destinations) if isinstance(destinations, list) else []]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _ritira_il_passo(flow: dict) -> None:
    """The budget is spent on progress: a step that moved nothing is taken back.

    Not an amnesty: the step returns to the exploration budget but weighs on
    the sterile ceiling, because not even nothing may repeat forever.
    """
    flow["steps"] = max(0, int(flow.get("steps", 0)) - 1)
    flow["sterile"] = int(flow.get("sterile", 0)) + 1


async def _wait_for_goal_content_change(
        entry: dict, before: str, firma=None) -> tuple[bool, str]:
    """Wait, bounded, for the page to differ from `before` by `firma`."""
    firma = firma or _goal_content_signature
    attempts = max(1, _REVEAL_SETTLE_MS // _REVEAL_POLL_MS)
    current = before
    for _ in range(attempts):
        if hasattr(entry["page"], "wait_for_timeout"):
            await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
        else:
            await asyncio.sleep(_REVEAL_POLL_MS / 1000)
        current = await firma(entry)
        if current != before:
            return True, current
    return False, current


def _context_key(candidates: list[dict]) -> str:
    """Selected values of account/category selectors; empty without any."""
    chosen = collection_context.selectors(action_resolver.goal_navigation_candidates(
        candidates, include_offscreen=True, include_covered=True))
    return collection_context.value_key(chosen) if chosen else ""


def _page_identity(url: str, context: str, text: str) -> str:
    # Equal text does not prove the same page, nor does the URL alone: the
    # selected account or category is part of what a page shows.
    return hashlib.sha256("\0".join(
        (scrub_url(url or ""), context or "", text or "")).encode("utf-8")).hexdigest()


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
            "text": str(text or "")[:100000],
            "truncated": len(str(text or "")) > 100000}


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
            from llm_workloads import tier_for

            provider = LLMRouter().provider(tier_for("sites.goal_reduce"))
            if getattr(provider, "mode", "") != "local":
                return ""
            prompt = prompt_loader.get(
                "sites_goal_reducer", i18n.current_lang(),
                query_json=json.dumps(bounded_query, ensure_ascii=False))
            result = provider.chat(prompt, "", max_tokens=64)
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


def _login_entry_candidates(candidates: list[dict], current_url: str = "") -> list[dict]:
    """Navigation only: no fields, form submissions or preference toggles."""
    current_place = action_resolver.url_place_key(current_url)
    return [candidate for candidate in
            action_resolver.goal_navigation_candidates(candidates)
            if not (current_place
                    and action_resolver.goal_place_key(candidate) == current_place
                    and str(candidate.get("aria_current") or "").lower() in {
                        "true", "page", "step", "location"})
            if str(candidate.get("form_method") or "").upper() != "POST"
            and str(candidate.get("type") or "").lower() not in {
                "reset", "checkbox", "radio"}
            and str(candidate.get("role") or "").lower() not in {
                "checkbox", "radio", "switch", "combobox"}]


async def _resolve_login_captcha(entry: dict, session_id: str, flow: dict, page) -> bool:
    """One local attempt within the existing login's permission and budgets."""
    attempt = flow.setdefault("captcha_state", {})
    search = flow.setdefault("entry_search", {})
    if (attempt.get("attempted") or page is not entry.get("page")
            or int(search.get("actions", 0)) >= login_navigation.MAX_ACTIONS):
        return False
    remaining = float(search.get("remaining_s", _LOGIN_TIMEOUT_S))
    if remaining <= 0:
        return False
    started = _monotonic()
    try:
        return await captcha_solver.solve_once(
            page=page, state=attempt, allowed_hosts=set(entry.get("allowlist") or ()),
            pending_script=credential_injection._DETECT_CAPTCHA_JS,
            owner=entry.get("owner", ""), session_id=session_id,
            domain=entry.get("domain", ""), timeout_s=remaining)
    finally:
        if attempt.get("status") not in {"unsupported", "unavailable", "origin_unverified"}:
            search["actions"] = int(search.get("actions", 0)) + 1
        search["remaining_s"] = max(0.0, remaining - (_monotonic() - started))


async def _discover_login_entry(entry: dict, session_id: str, flow: dict) -> dict:
    """Explore before fill; all forward/replayed controls use ordinary gates."""
    state = flow.setdefault("entry_search", {})

    def unsafe() -> bool:
        return bool(entry.get("authenticated") or entry.get("secret_pending")
                    or flow.get("credentials_started"))

    async def observe() -> dict:
        if unsafe():
            return {"terminal": {"ok": False,
                                 "error_class": "mandate_scope_exceeded"}}
        # A redirect can finish after the click returned. Check the current
        # top-level destination before reading or interacting with its page.
        host = _host_of_url(getattr(entry.get("page"), "url", ""))
        if host and host not in set(entry.get("allowlist") or ()):
            return {"terminal": await _resume_blocked_login_destination(
                entry, session_id, state, {"ok": True, "executed": True})}
        blocker = await credential_injection.classify_login_surface(entry["page"])
        if blocker == "captcha_required" and await _resolve_login_captcha(
                entry, session_id, flow, entry["page"]):
            blocker = await credential_injection.classify_login_surface(entry["page"])
        if blocker:
            return {"terminal": {"ok": False, "reason_code": blocker,
                                 "error_class": blocker}}
        outcome = await _clear_login_surface(entry, settle=False)
        if outcome.status == "blocked":
            return {"terminal": {"ok": False,
                    "error_class": "cookie_precondition_unresolved",
                    "obstruction_kind": outcome.kind,
                    "obstruction_reason": outcome.reason}}
        page = entry["page"]
        host = _host_of_url(page.url)
        if host and host not in set(entry.get("allowlist") or ()):
            return {"terminal": await _resume_blocked_login_destination(
                entry, session_id, state, {"ok": True, "executed": True})}
        state.setdefault("pages", set()).add(page)
        blocker = await credential_injection.classify_login_surface(page)
        if blocker == "captcha_required" and await _resolve_login_captcha(
                entry, session_id, flow, page):
            blocker = await credential_injection.classify_login_surface(page)
        if blocker:
            return {"terminal": {"ok": False, "reason_code": blocker,
                                 "error_class": blocker}}
        if await credential_injection._wait_for_login_surface(
                page, _REVEAL_SETTLE_MS / 1000.0):
            return {"terminal": {"ok": True, "executed": True}}
        candidates = await _enumerate_candidates(page)
        # The document may be loaded while its login UI is still rendering.
        # Spend a little more of the same search budget only after a click
        # that left neither a form nor a usable next entry on screen.
        if (state.get("pending")
                and not action_resolver.choose_candidate(
                    "login", candidates, "click").get("ok")
                and not action_resolver.choose_reveal_candidate(
                    "login", candidates, "click").get("ok")):
            extra_s = max(0.0, _LOGIN_ENTRY_SETTLE_S
                          - _REVEAL_SETTLE_MS / 1000.0)
            if extra_s and await credential_injection._wait_for_login_surface(
                    page, extra_s):
                return {"terminal": {"ok": True, "executed": True}}
            candidates = await _enumerate_candidates(page)
        username_stage = await page.evaluate(
            credential_injection._LOCATE_USERNAME_STAGE_JS)
        if isinstance(username_stage, dict) and username_stage.get("ambiguous"):
            return {"terminal": {"ok": False, "error_class": "selector_ambiguous"}}
        scroll = action_resolver.choose_scroll_candidate(
            "login", candidates, "click")
        if (not action_resolver.choose_candidate(
                "login", candidates, "click").get("ok")
                and scroll.get("ok") and await _scroll_candidate_into_view(
                    entry, scroll["candidate"])):
            candidates = await _enumerate_candidates(page)
        return {"key": login_navigation.state_key(page.url, candidates),
                "url": page.url, "page": page,
                "candidates": _login_entry_candidates(candidates, page.url),
                "all_candidates": candidates}

    async def choose(observation: dict, tried: set[str]) -> dict | None:
        candidates = [candidate for candidate in observation["candidates"]
                      if login_navigation.candidate_key(candidate) not in tried]
        chosen = action_resolver.choose_candidate("login", candidates, "click")
        if chosen.get("ok") and not chosen.get("ambiguous"):
            return {"candidate": chosen["candidate"],
                    "confidence": chosen.get("confidence", 1.0)}
        # Reuse the multilingual resolver and DOM relations. A sole menu is
        # also useful on pages that expose no login words yet.
        reveal = action_resolver.choose_reveal_candidate(
            "login", [item for item in observation["all_candidates"]
                      if login_navigation.candidate_key(item) not in tried], "click")
        reveals = [candidate for candidate in candidates
                   if action_resolver.is_reveal_control(candidate)
                   and str(candidate.get("aria_expanded") or "").lower() != "true"]
        candidate = reveal.get("candidate") if reveal.get("ok") else None
        if candidate is None and len(reveals) == 1:
            candidate = reveals[0]
        if candidate is not None and candidate in candidates:
            return {"candidate": candidate, "confidence": 0.5}
        hint = action_resolver.choose_login_area_hint(
            candidates, observation["url"])
        if hint.get("ok"):
            return {"candidate": hint["candidate"],
                    "confidence": hint["confidence"]}
        candidate = await _local_llm_choose_goal_candidate(
            entry, "login", candidates, [], set(), login=True)
        if candidate is not None:
            return {"candidate": candidate, "confidence": 0.5,
                    "model_selected": True}
        return None

    async def execute(choice: dict) -> dict:
        prepared = await _prepare_action(
            entry, session_id, "click login", None, allow_model=False,
            login_choice=choice)
        if prepared.get("ok"):
            prepared["plan"].update({"login_flow": True,
                                     "login_procedure": "login",
                                     "login_choice": choice})
            _apply_login_intent_grant(entry, prepared)
        handled = await _handle_prepared_action(
            entry, session_id, "click login", prepared)
        if handled.get("ok") and handled.get("executed"):
            return await _resume_blocked_login_destination(
                entry, session_id, state, handled)
        return handled

    async def restore(root: dict) -> dict:
        # A GET to the broker-observed entry URL is the only rewind primitive.
        # Never reload/re-submit a form or synthesize an inverse UI action.
        url = root["url"]
        if (unsafe() or urllib.parse.urlsplit(url).scheme not in {"http", "https"}
                or _host_of_url(url) not in set(entry.get("allowlist") or ())):
            return {"ok": False, "error_class": "mandate_scope_exceeded"}
        page = root["page"]
        if page.is_closed():
            return {"ok": False, "error_class": "session_lost"}
        entry["page"] = page
        try:
            await page.goto(url, wait_until="load", timeout=int(_OP_TIMEOUT_S * 1000))
            # Close only tabs adopted by this search (including nested ones).
            for child in state.get("pages", set()) - {page}:
                await child.close()
            state["pages"] = {page}
        except Exception:
            return {"ok": False, "error_class": "navigation_failed"}
        sites_audit.record(
            "login_backtrack", owner=entry.get("owner", ""),
            session_id=session_id, domain=entry.get("domain", ""),
            depth=len(state.get("frames") or ()) - 1,
            steps=int(state.get("actions", 0)))
        return {"ok": True, "executed": True}

    remaining = float(state.setdefault("remaining_s", _LOGIN_TIMEOUT_S))
    if remaining <= 0:
        return {"ok": False, "error_class": "login_timeout"}
    started = _monotonic()
    try:
        result = await asyncio.wait_for(login_navigation.discover(
            state, observe=observe, choose=choose, execute=execute, restore=restore),
            timeout=remaining)
        if (result.get("error_class") == "selector_missing"
                and not state.get("resource_attempted")
                and int(state.get("actions", 0)) < login_navigation.MAX_ACTIONS):
            expansion = _prepare_resource_expansion(
                entry, session_id, "click login", None)
            if expansion is not None:
                state["resource_attempted"] = True
                state["actions"] = int(state.get("actions", 0)) + 1
                expansion["plan"].update({"login_flow": True,
                    "login_procedure": "login", "login_entry_refresh": True})
                return await _handle_prepared_action(
                    entry, session_id, "click login", expansion)
        return result
    except asyncio.TimeoutError:
        return {"ok": False, "error_class": "login_timeout"}
    finally:
        state["remaining_s"] = max(0.0, remaining - (_monotonic() - started))


async def _resume_blocked_login_destination(entry: dict, session_id: str,
                                            state: dict, handled: dict) -> dict:
    """Gate an observed cross-host login redirect before exploring its page."""
    page_url = getattr(entry.get("page"), "url", "")
    parsed = urllib.parse.urlsplit(page_url)
    host = (_host_of_url(page_url) if parsed.scheme in {"http", "https"}
            else "")
    if host in set(entry.get("allowlist") or ()):
        return handled
    if not host and _browser_navigation_failure(page_url):
        hosts = _blocked_login_navigation_hosts(entry)
        if len(hosts) != 1:
            return {"ok": False, "error_class": "navigation_failed"}
        host = hosts[0]
    if not host:
        return {"ok": False, "error_class": "mandate_scope_exceeded"}
    observed = (entry.get("blocked_requests") or {}).get(host) or {}
    blocked_document = (observed.get("main_frame")
                        and observed.get("navigation")
                        and "document" in set(observed.get("types") or ()))
    # Chromium may follow an allowed HTTP redirect before route interception;
    # only its script/style requests are then blocked. The browser's current
    # top-level URL plus same-host main-frame assets are exact evidence for a
    # one-host approval gate, never permission to load those assets silently.
    redirected_page = (host == _host_of_url(page_url)
                       and observed.get("main_frame")
                       and observed.get("top_host") == host
                       and bool({"script", "stylesheet"}
                                & set(observed.get("types") or ())))
    if not (blocked_document or redirected_page):
        return {"ok": False, "error_class": "mandate_scope_exceeded"}
    if int(state.get("actions", 0)) >= login_navigation.MAX_ACTIONS:
        return {"ok": False, "error_class": "login_step_limit"}
    expansion = _prepare_resource_expansion(
        entry, session_id, "click login", None, required_hosts={host})
    if expansion is None:
        return {"ok": False, "error_class": "mandate_scope_exceeded"}
    if not expansion.get("ok"):
        return expansion
    expansion["plan"].update({"login_flow": True,
                              "login_procedure": "login",
                              "login_entry_cross_host": True})
    return await _handle_prepared_action(
        entry, session_id, "click login", expansion)


def _blocked_login_navigation_hosts(entry: dict, *, popup: bool = False
                                    ) -> list[str]:
    """Only observed HTTP(S) documents can become login destinations.

    Playwright may not expose the frame for a popup's first navigation.  A
    unique blocked document plus the observed failed popup is sufficient;
    ordinary navigation still requires an identified main frame.
    """
    allowed = set(entry.get("allowlist") or ())
    return sorted(host for host, observed in
                  (entry.get("blocked_requests") or {}).items()
                  if host not in allowed and isinstance(observed, dict)
                  and (popup or observed.get("main_frame"))
                  and observed.get("navigation")
                  and "document" in set(observed.get("types") or ())
                  and _host_of_url(observed.get("navigation_url") or "") == host
                  and urllib.parse.urlsplit(
                      observed.get("navigation_url") or "").scheme
                  in {"http", "https"})


def _link_destination(candidate: dict, base: str) -> str:
    """Absolute HTTP destination of a plain link, or "" when not comparable.

    A fragment may select client-side state, so such links keep no identity.
    """
    url = urllib.parse.urljoin(base, str(candidate.get("href") or ""))
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or "#" in url or not candidate.get("href"):
        return ""
    return url


def _collapse_collection_links(candidates: list[dict]) -> list[dict]:
    """Repeated HTTP links are one edge only with full semantic equality.

    Keep query, fragment, context and non-link ambiguity: a matching label or
    URL path alone does not establish equivalent destinations.
    """
    groups: dict[str, list[dict]] = {}
    for candidate in candidates:
        groups.setdefault(login_navigation.candidate_key(candidate), []).append(candidate)
    out = []
    for group in groups.values():
        collapsed = (action_resolver.prefer_verifiable_goal_candidates(group)
                     if group[0].get("tag") == "a" else group)
        if len(group) > 1 and len(collapsed) == 1:
            # Exact equivalent HTTP links may have multiple DOM copies. Use
            # an already available copy before trying to reveal a covered one;
            # opaque links and non-link ambiguity remain unchanged.
            available = [candidate for candidate in group
                         if all(candidate.get(field) is True for field in (
                             "visible", "in_viewport", "topmost"))
                         and not candidate.get("disabled")]
            if available:
                collapsed = action_resolver.prefer_verifiable_goal_candidates(available)
        out.extend(collapsed)
    return out


async def _discover_collection(entry: dict, session_id: str,
                               action: str, target: str) -> dict:
    """Collect evidence throughout the existing bounded navigation search.

    Record filters never prune a branch. A leaf ends one path; only exhaustion
    of the search ends the collection. All effects still use the ordinary gate.
    """
    key = hashlib.sha256(action.encode()).hexdigest()
    state = entry.get("collection_search")
    if not isinstance(state, dict) or state.get("key") != key:
        state = {"key": key, "action": action, "target": target,
                 "remaining_s": login_navigation.COLLECTION_TIMEOUT_S,
                 "found": False, "pages": set()}
        entry["collection_search"] = state
        entry.pop("collected_pages", None)
        entry.pop("collection_record_views", None)
        entry.pop("_source_scope_label", None)

    record_contexts = {}

    async def read_controls(page, observed=None):
        candidates = action_resolver.goal_navigation_candidates(
            observed if observed is not None else await _enumerate_candidates(
                page, record_contexts=record_contexts),
            include_offscreen=True, include_covered=True)
        return _collapse_collection_links([c for c in candidates
            if str(c.get("form_method") or "").upper() != "POST"
            and action_resolver.goal_candidate_is_admissible(
                target, c, scope=str(entry.get("goal_scope") or ""))])

    def covered_controls(observed):
        return [c for c in observed
                if c.get("rendered") is True and c.get("visible") is True
                and c.get("in_viewport") is True and c.get("topmost") is False
                and action_resolver.goal_navigation_candidates(
                    [{**c, "topmost": True}])
                and action_resolver.goal_candidate_is_admissible(
                    target, c, scope=str(entry.get("goal_scope") or ""))]

    async def observe():
        privacy = await _dismiss_privacy_obstruction(entry)
        if isinstance(privacy, cookie_privacy.CookieOutcome) and privacy.status == "blocked":
            return {"terminal": {"ok": False,
                    "error_class": "cookie_precondition_unresolved"}}
        await _dismiss_obstructing_overlay(entry)
        if not await _wait_for_content_settle(entry):
            return {"terminal": {"ok": False, "error_class": "target_unstable"}}
        page = entry["page"]
        if _host_of_url(page.url) not in set(entry.get("allowlist") or ()):
            return {"terminal": {"ok": False, "error_class": "mandate_scope_exceeded"}}
        state["pages"].add(page)
        observed, passing = None, False
        replay_index = (state.get("replay") or {}).get("index", -1)
        replay_frames = state.get("frames", [])
        if 0 <= replay_index < len(replay_frames) - 1:
            # A replay passes through pages already read at their first visit:
            # scroll only to reach a saved control or to read a page to decide.
            observed = await _enumerate_candidates(page, record_contexts=record_contexts)
            saved = replay_frames[replay_index + 1]["via"]["key"]
            passing = any(login_navigation.candidate_key(c) == saved
                          for c in await read_controls(page, observed))
        if not passing:
            remaining = max(0, login_navigation.COLLECTION_MAX_ACTIONS - int(state.get("actions", 0)))
            scrolling = {"collection": True}
            await _expand_collection_by_scrolling(
                entry, scrolling, max_scrolls=min(_MAX_COLLECTION_SCROLLS, remaining))
            scrolls = scrolling.get("collection_scrolls", 0)
            state["actions"] = int(state.get("actions", 0)) + scrolls
            if scrolls or scrolling.get("collection_scroll_limited"):
                sites_audit.record("collection_scrolls", session_id=session_id,
                                   scrolls=scrolls, replay=replay_index >= 0,
                                   limited=bool(scrolling.get("collection_scroll_limited")),
                                   steps=int(state.get("actions", 0)))
            if scrolling.get("collection_scroll_limited"):
                # Name the limit that stopped scrolling: the page cap only
                # while the shared action budget still exceeded it.
                state["observation_limit"] = (
                    ("max_scrolls", _MAX_COLLECTION_SCROLLS)
                    if remaining > _MAX_COLLECTION_SCROLLS else
                    ("max_actions", login_navigation.COLLECTION_MAX_ACTIONS))
            if observed is None or scrolls:
                observed = await _enumerate_candidates(page, record_contexts=record_contexts)
        if covered_controls(observed):
            # A modal may appear during scrolling. Try its existing safe
            # exit, but keep covered routes for semantic selection: a fixed
            # header over an unrelated footer link is not a page-wide veto.
            location = page.url
            await _dismiss_obstructing_overlay(entry, settle=True)
            if entry["page"] is not page or page.url != location:
                state["rejection"] = {"phase": "overlay_changed_page",
                                      "same_page": entry["page"] is page}
                return {"terminal": {"ok": False, "error_class": "target_changed"}}
            observed = await _enumerate_candidates(page, record_contexts=record_contexts)
        context_controls = await read_controls(page, observed)

        async def read_context():
            nonlocal context_controls, observed
            observed = await _enumerate_candidates(entry["page"], record_contexts=record_contexts)
            context_controls = await read_controls(entry["page"], observed)
            return context_controls

        context = await collection_context.ensure(
            state.setdefault("context", {}), target, context_controls,
            read=read_context, execute=execute,
            settle=lambda: _wait_for_content_settle(entry), budget=state,
            enabled=_MODEL_FALLBACKS_ENABLED, timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000)
        if context.get("terminal") is not None:
            if context.get("rejection"):
                sites_audit.record("collection_context_rejected", session_id=session_id,
                                   steps=int(state.get("actions", 0)),
                                   **context["rejection"])
            return context
        candidates = await read_controls(entry["page"], context["candidates"])
        page = entry["page"]
        index = (state.get("replay") or {}).get("index", -1)
        frames = state.get("frames", [])
        if 0 <= index < len(frames) - 1:
            expected = frames[index + 1]["via"]["key"]
            location = frames[index]["observation"]["url"]
            deadline = _monotonic() + _REVEAL_SETTLE_MS / 1000.0
            reads = 0
            read_timeout = False
            # A loaded document may still be rendering without a spinner.
            # Read only the exact saved edge at the same full URL. Do not
            # repeat scrolling, clicks or decisions, or wait out ambiguity.
            while (entry["page"] is page and page.url == location and _monotonic() < deadline
                   and not any(login_navigation.candidate_key(c) == expected
                               for c in candidates)):
                await asyncio.sleep(_REVEAL_POLL_MS / 1000.0)
                remaining_read = deadline - _monotonic()
                if entry["page"] is not page or page.url != location or remaining_read <= 0:
                    break
                reads += 1
                try:
                    candidates = await asyncio.wait_for(read_controls(page), timeout=remaining_read)
                    candidates = collection_context.navigation_candidates(candidates)
                except asyncio.TimeoutError:
                    read_timeout = True
                    break
            if reads:
                sites_audit.record("collection_replay_observed", session_id=session_id,
                    index=index, extra_reads=reads, same_location=page.url == location,
                    read_timeout=read_timeout,
                    match_count=sum(login_navigation.candidate_key(c) == expected
                                    for c in candidates))
            if entry["page"] is not page:
                current_url = entry["page"].url
                current_context = state.get("context", {}).get("value_key", "")
                state["rejection"] = {"phase": "replay_page", "index": index,
                    **login_navigation.replay_page_diagnostics(
                        frames[index]["observation"],
                        {"page": entry["page"], "url": current_url,
                         "context_key": hashlib.sha256(current_context.encode()).hexdigest(),
                         "location_key": hashlib.sha256(
                             (current_url + current_context).encode()).hexdigest()},
                        producer="broker_page")}
                return {"terminal": {"ok": False, "error_class": "target_changed"}}
        # A saved ambiguous edge still fails replay immediately. New choices
        # must see ambiguous controls to assess relevance; a selected one is
        # rejected at binding without hiding the other available branches.
        if not (0 <= index < len(frames) - 1 and sum(
                login_navigation.candidate_key(c) == expected
                for c in candidates) > 1):
            candidates = _collapse_collection_links(candidates)
        snapshot = await _continuation_snapshot(entry)
        # Context selection may have changed the controls and page since the
        # initial enumeration. Key the captured text with its observed context.
        snapshot_context = _context_key(observed)
        if snapshot.get("truncated"):
            state["observation_limit"] = ("max_text_chars", 100000)
        content_key = hashlib.sha256(snapshot["text"].encode()).hexdigest()
        # A refused bind is recoverable only against this page, URL and the
        # selected values of its account/category selectors.
        state["observed_scene"] = (page, page.url, collection_context.value_key(
            collection_context.selectors(context_controls)))
        # Scrolling is not a new node; a changed SPA page at the same URL is.
        controls = [{**c, "visible": True, "in_viewport": True, "topmost": True}
                    for c in candidates]
        pending_view = state.pop("pending_record_view", None)
        snapshot_key = _page_identity(
            snapshot.get("url") or page.url, snapshot_context, snapshot["text"])
        if (pending_view and pending_view["parent"] != snapshot_key and any(
                p.get("key") == snapshot_key for p in entry.get("collected_pages", []))):
            # A successful button can return to a detail already observed
            # through the archive. The navigator need not decide that cycle
            # again; its unchanged captured snapshot proves the new edge.
            edge = {**pending_view, "detail": snapshot_key}
            edges = entry.setdefault("collection_record_views", [])
            if edge not in edges:
                edges.append(edge)
        return {"key": login_navigation.state_key(page.url, controls) + content_key,
                # Returning to the same full URL may redraw unrelated cards.
                # The navigator rechecks each saved edge, including context;
                # a fresh semantic decision reads any remaining alternatives.
                "location_key": hashlib.sha256((page.url + state.get("context", {}).get(
                    "value_key", "")).encode()).hexdigest(),
                "context_key": hashlib.sha256(state.get("context", {}).get(
                    "value_key", "").encode()).hexdigest(),
                "url": page.url, "page": page, "candidates": candidates,
                "record_parent": pending_view,
                "snapshot": {**snapshot, "context": snapshot_context,
                    "records": {c["_record_context_key"]: record_contexts[c["_record_context_key"]]
                        for c in candidates if c.get("_record_context_key") in record_contexts},
                    "key": snapshot_key}}

    async def choose(observation, tried):
        # Pages repeat menus, breadcrumbs and links to themselves. A GET of a
        # link already opened in this search, or of the root that backtracking
        # restores, reaches a document already explored: do not open it again.
        # Links only offered elsewhere stay available in every context.
        first_visit = not tried
        root = state["frames"][0]["observation"]["url"]
        opened = state.setdefault("opened_links", set()) | {
            _link_destination({"href": root}, root)}
        for c in observation["candidates"]:
            destination = _link_destination(c, observation["url"])
            if destination in opened - {""}:
                # The same bound row may occur in a preview and its archive.
                # A previously observed detail link establishes their shared
                # resource without clicking it again. Text alone never does.
                record = c.get("_record_context_key")
                known = {e["detail"] for e in entry.get("collection_record_views", [])
                         if record and e.get("record") == record}
                matches = [p for p in entry.get("collected_pages", [])
                           if p.get("key") in known
                           and _link_destination({"href": p.get("url")},
                                                 observation["url"]) == destination]
                if len(matches) == 1:
                    edge = {"parent": observation["snapshot"]["key"],
                            "record": record, "detail": matches[0]["key"]}
                    edges = entry.setdefault("collection_record_views", [])
                    if edge not in edges:
                        edges.append(edge)
                tried.add(login_navigation.candidate_key(c))
        candidates = [c for c in observation["candidates"]
                      if login_navigation.candidate_key(c) not in tried]
        def preserve(candidate):
            if candidate.get("collection_observed"):
                state["found"] = True
                snapshot = observation["snapshot"]
                collected = entry.setdefault("collected_pages", [])
                if snapshot["text"] and not any(
                        item.get("key") == snapshot["key"] for item in collected):
                    collected.append(snapshot)
                parent = observation.get("record_parent")
                if parent and snapshot["text"] and parent["parent"] != snapshot["key"]:
                    edge = {**parent, "detail": snapshot["key"]}
                    edges = entry.setdefault("collection_record_views", [])
                    if edge not in edges:
                        edges.append(edge)
            sites_audit.record(
                "collection_observation", owner=entry.get("owner", ""),
                session_id=session_id, domain=entry.get("domain", ""),
                collection_observed=bool(candidate.get("collection_observed")),
                proximity=candidate.get("proximity", "none"),
                steps=int(state.get("actions", 0)))

        candidate = await _collection_route_decision(
            entry, target, candidates, first_visit=first_visit, preserve=preserve)
        if not candidate.get("id"):
            return None
        # Next/more is pagination, not another level in the site's hierarchy.
        probe = {**candidate, "visible": True, "in_viewport": True, "topmost": True}
        continuation = candidate.get("collection_continuation")
        if not isinstance(continuation, bool):
            continuation = action_resolver.choose_goal_continuation_candidate(
                target, [probe]).get("ok", False)
        return {"candidate": candidate, "model_selected": True,
                "confidence": 0.5, "continuation": continuation,
                "record_parent": {"parent": observation["snapshot"]["key"],
                    "record": candidate["_record_context_key"]}
                    if candidate.get("collection_purpose") == "detail"
                    and candidate.get("_record_context_key") in observation["snapshot"]["records"]
                    else None}

    def audit_failed_action(result, primitive, replay, started):
        # Every refused effect names itself: a terminal `target_changed`
        # without a recorded phase cannot be attributed afterwards (live turn
        # 51b9870051144541: 30 s of silence between the last event and it).
        if not result.get("ok") and not result.get("approval_required"):
            sites_audit.record(
                "collection_action_failed", session_id=session_id,
                error_class=str(result.get("error_class") or ""),
                primitive=primitive, replay=replay,
                elapsed_ms=round((_monotonic() - started) * 1000),
                steps=int(state.get("actions", 0)))

    async def execute(choice):
        started = _monotonic()
        destination = _link_destination(choice.get("candidate") or {}, entry["page"].url)
        prepared = await _prepare_action(
            entry, session_id, action, None, primitive_override="search",
            target_override=target, allow_model=False, collection_choice=choice)
        if prepared.get("ok"):
            prepared["plan"]["collection_search"] = key
            # The audit separates a replayed saved edge from a new choice.
            prepared["plan"]["collection_replay"] = bool(choice.get("replay"))
        result = await _handle_prepared_action(entry, session_id, action, prepared)
        audit_failed_action(result, str((prepared.get("plan") or {}).get("primitive")
                                        or "click"), bool(choice.get("replay")), started)
        if result.get("collection_bind_refused"):
            page, location, selected = state.get("observed_scene") or (None, "", "")
            current = entry["page"]
            if (prepared.get("ok") or current is not page or current.url != location
                    or selected != collection_context.value_key(collection_context.selectors(
                        await read_controls(current)))):
                # Only a refused preparation on the observed page, URL and
                # context leaves the branch unresolved; anything else is terminal.
                result = {k: v for k, v in result.items() if k != "collection_bind_refused"}
        if result.get("ok") and result.get("executed"):
            if destination:
                state.setdefault("opened_links", set()).add(destination)
            await _wait_for_goal_content_change(
                entry, (prepared.get("plan") or {}).get("facet_sig_before", ""),
                _goal_facet_signature)
            if not choice.get("replay") and choice.get("record_parent"):
                state["pending_record_view"] = choice["record_parent"]
        return result

    async def restore(root):
        url = root["url"]
        if (urllib.parse.urlsplit(url).scheme not in {"http", "https"}
                or _host_of_url(url) not in set(entry.get("allowlist") or ())
                or entry.get("secret_pending")):
            return {"ok": False, "error_class": "mandate_scope_exceeded"}
        if root["page"].is_closed():
            return {"ok": False, "error_class": "session_lost"}
        entry["page"] = root["page"]
        started = _monotonic()
        prepared = await _prepare_action(
            entry, session_id, action, None, primitive_override="goto",
            target_override=url, allow_model=False)
        if prepared.get("ok"):
            prepared["plan"].update(kind="goal_navigation", collection_search=key)
        result = await _handle_prepared_action(entry, session_id, action, prepared)
        audit_failed_action(result, "goto", False, started)
        if result.get("ok") and result.get("executed"):
            for page in state["pages"] - {root["page"]}:
                await page.close()
            state["pages"] = {root["page"]}
            sites_audit.record("collection_backtrack", session_id=session_id,
                               steps=int(state.get("actions", 0)))
        return result

    remaining = float(state["remaining_s"])
    started = _monotonic()
    try:
        result = await asyncio.wait_for(login_navigation.discover(
            state, observe=observe, choose=choose, execute=execute,
            restore=restore, max_depth=3,
            max_actions=login_navigation.COLLECTION_MAX_ACTIONS,
            exhaustive=True), timeout=remaining)
    except asyncio.TimeoutError:
        result = {"ok": False, "error_class": "goal_step_limit",
                  "cap_field": "active_seconds",
                  "cap_value": login_navigation.COLLECTION_TIMEOUT_S}
    except ValueError as exc:
        if str(exc) != "collection_observation_unresolved":
            raise
        result = {"ok": False, "error_class": "selector_missing"}
    finally:
        state["remaining_s"] = max(0.0, remaining - (_monotonic() - started))
    if result.get("approval_required"):
        return result
    if result.get("error_class") == "target_changed" and state.get("rejection"):
        sites_audit.record("collection_navigation_rejected", session_id=session_id,
                           steps=int(state.get("actions", 0)), **state["rejection"])
    limited = state.get("observation_limit") or result.get("depth_limited") or result.get("error_class") in {
        "login_step_limit", "goal_step_limit"}
    if limited:
        # The exhausted action budget ended the search: a wider page limit
        # would not have continued it.
        budget = ("max_actions", login_navigation.COLLECTION_MAX_ACTIONS)
        if result.get("error_class") in {"login_step_limit", "goal_step_limit"}:
            cap_field, cap_value = budget
        else:
            cap_field, cap_value = state.get("observation_limit") or (
                ("max_depth", 3) if result.get("depth_limited") else budget)
        result = {"ok": bool(state["found"]), "truncated": True,
                  "truncated_what": "MSG_TRUNCATED_DEFAULT_WHAT",
                  # Action/page counts are not counts of matching records.
                  "used": 0, "available_total": None,
                  "cap_field": result.get("cap_field") or cap_field,
                  "cap_value": result.get("cap_value") or cap_value,
                  "error_class": "goal_step_limit"}
        if state.get("unresolved_control"):
            # A wider limit cannot resolve a refused branch: report its cause
            # with the limit, as an incomplete result keeping the records.
            result.update(ok=False, error_class=state["unresolved_control"])
    elif result.get("exhausted"):
        # A refused relevant branch keeps its first cause; never empty success.
        result = ({"ok": False, "error_class": state["unresolved_control"]}
                  if state.get("unresolved_control") else
                  {"ok": True, "no_match": not state["found"]})
    if not result.get("ok") and state["found"]:
        # A later blocked branch does not erase already observed records.
        # Return saved evidence only: the current page may be outside scope.
        chunks = [item["text"] for item in entry.get("collected_pages", [])
                  if isinstance(item, dict) and item.get("text")]
        if chunks:
            result = {**result, "collection_partial": True,
                      "text": "\n\n".join(chunks)}
    entry.pop("collection_search", None)
    cloud = int(state.get("cloud_llm_calls") or 0)
    return {**result, "executed": bool(state["found"]), "primitive": "observe",
            "url": scrub_url(entry["page"].url),
            **({"cloud_llm_calls": cloud} if cloud else {})}


def _goal_eligible(entry: dict, target: str, candidates: list[dict],
                   excluded: set[str], *, collection: bool) -> list[dict]:
    eligible = action_resolver.goal_navigation_candidates(
        candidates, excluded=excluded, include_offscreen=collection,
        include_covered=collection)
    eligible = [candidate for candidate in eligible
                if action_resolver.goal_candidate_is_admissible(
                    target, candidate,
                    scope=str(entry.get("goal_scope") or ""))]
    if not collection:
        return action_resolver.prefer_verifiable_goal_candidates(eligible)
    eligible = _collapse_collection_links(eligible)
    # Enumeration prioritizes the current viewport for atomic actions.
    # Collection search must read page routes in document order, otherwise
    # a visible footer can fill the first model window before main links.
    eligible.sort(key=lambda item: item.get("dom_order", float("inf")))
    return eligible


def _frontier_routes_configured() -> bool:
    """True when the Frontier workload resolves; configuration errors only."""
    from llm_router import LLMRouter, TierConfigError
    from llm_workloads import tier_for
    try:
        LLMRouter().provider(tier_for("sites.collection_route"))
    except TierConfigError:
        return False
    return True


async def _collection_route_decision(entry: dict, target: str,
                                     candidates: list[dict], *, first_visit: bool,
                                     preserve) -> dict:
    """Local content recognition and, when configured, Frontier's route.

    The two answer independent questions (a page with invoices can still offer
    unrelated menus), so both requests start together. The local evidence is
    preserved as soon as it exists, even if Frontier then fails. A local
    failure discards the pending route: an unavailable or invalid model answer
    is not proof of a dead end, and no late answer may change the state.
    """
    route = None
    if _FRONTIER_COLLECTION_ROUTES and _frontier_routes_configured():
        route = asyncio.create_task(
            _frontier_collection_route(entry, target, candidates))
    try:
        candidate = await _local_llm_choose_goal_candidate(
            entry, target, candidates, [], set(), collection=True,
            first_visit=first_visit)
        if candidate is None:
            raise ValueError("collection_observation_unresolved")
        preserve(candidate)
        if route is not None:
            candidate = await route
            if candidate is None:
                raise ValueError("collection_observation_unresolved")
        return candidate
    finally:
        if route is not None:
            if not route.done():
                route.cancel()
            await asyncio.gather(route, return_exceptions=True)


async def _frontier_collection_route(entry: dict, target: str,
                                     candidates: list[dict]) -> dict | None:
    """Frontier route choice independent of the local content observation.

    The request carries the user's target and the observed control names and
    contexts only: no page content, field values, URLs or credentials. Any
    provider, timeout or format failure leaves the decision unresolved; the
    local model is not used as a silent fallback.
    """
    import i18n
    import prompt_loader
    from llm_router import LLMRouter
    from llm_workloads import tier_for
    eligible = _goal_eligible(entry, target, candidates, set(), collection=True)
    started, outcome = time.monotonic(), "none"
    try:
        provider = LLMRouter().provider(tier_for("sites.collection_route"))
        system = prompt_loader.get("agentic_sites_action_system",
                                   i18n.current_lang(), route=True)
        search = entry.get("collection_search") or {}
        # Previously selected observed labels help avoid leaving an archive
        # for the site's global navigation. No URLs or page content are sent.
        history = [str((f.get("via") or {}).get("candidate", {}).get("name") or "")
                   for f in search.get("frames", [])]
        history = [label[:300] for label in history if label][-8:]
        for offset in range(0, len(eligible), 64):
            by_id = {str(c.get("id")): c for c in eligible[offset:offset + 64] if c.get("id")}
            sites_audit.record(
                "collection_route_window", owner=entry.get("owner", ""),
                session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
                offset=offset, controls=[{
                    "id": cid, "kind": c.get("role") or c.get("tag") or "",
                    "name": str(c.get("name") or c.get("label") or "")[:300],
                    "context": str(c.get("context_name") or "")[:300],
                    "record_bound": bool(c.get("_record_context_key")),
                    **({"record_diagnostics": diagnostic} if (diagnostic :=
                        _safe_record_diagnostics(c.get("_record_diagnostics"))) else {}),
                } for cid, c in by_id.items()])
            user = _bounded_action_prompt(
                goal={"primitive": "navigate_toward_goal", "target": target,
                      "collection": True, "exhaustive": True,
                      "expected_content": str(entry.get("goal_done_when") or "")},
                state={"authenticated": bool(entry.get("authenticated")),
                       "collection_seen": bool(search.get("found"))},
                observed={"CURRENT_CONTENT": "", "CONTROLS": [
                    f"{cid}: {c.get('role') or c.get('tag')} "
                    f"{c.get('name') or c.get('label') or ''} | "
                    f"{str(c.get('context_name') or '')[:300]}"
                    + (" | record_action=true" if c.get("_record_context_key") else "")
                    for cid, c in by_id.items()]},
                history=history, forbidden="collection_navigation")
            search = entry.get("collection_search")
            if isinstance(search, dict):
                # Counted before the call: the request has left the host.
                search["cloud_llm_calls"] = int(search.get("cloud_llm_calls") or 0) + 1
            reply = await asyncio.to_thread(
                provider.chat, system, user, max_tokens=128,
                request_timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0)
            text = str(getattr(reply, "text", "") or "")
            decision = json.loads(text[text.find("{"): text.rfind("}") + 1])
            choice, purpose = decision.get("next_control"), decision.get("purpose")
            if (not isinstance(choice, str)
                    or (choice == "NONE" and purpose != "done")
                    or (choice != "NONE" and (choice not in by_id or purpose not in
                        {"navigate", "expand_collection", "detail"}))):
                outcome = "invalid_response"
                return None
            sites_audit.record(
                "collection_route_selection", owner=entry.get("owner", ""),
                session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
                offset=offset, selected_control=choice, purpose=purpose)
            if choice in by_id:
                outcome = "route"
                return {**by_id[choice],
                        "collection_purpose": purpose,
                        "collection_continuation": purpose == "expand_collection"}
        return {}
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:
        outcome = type(exc).__name__
        return None
    finally:
        sites_audit.record(
            "collection_route_frontier", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            candidate_count=len(eligible), outcome=outcome,
            elapsed_ms=round((time.monotonic() - started) * 1000))


async def _local_llm_choose_goal_candidate(entry: dict, target: str,
                                           candidates: list[dict],
                                           history: list[str],
                                           excluded: set[str], *,
                                           login: bool = False,
                                           collection: bool = False,
                                           first_visit: bool = True) -> dict | None:
    """Fallback testuale locale per un passo di navigazione nel mandato.

    Il modello vede ID e nomi accessibili enumerati dal broker. Per una
    collezione vede anche testo visibile delle etichette, senza valori dei
    campi, DOM, screenshot, URL di destinazione o credenziali; la
    scelta resta un ID esatto e passa comunque dal gate se non deterministica.
    """
    if not _MODEL_FALLBACKS_ENABLED:
        return None
    collection = collection and not login
    eligible = _goal_eligible(entry, target, candidates, excluded,
                              collection=collection)
    if not collection:
        return await _local_llm_choose_goal_window(
            entry, target, eligible, history, login=login, collection=False)
    # A window bounds the model input, not the site's navigation. A leaf is
    # proved only after every window has been considered; a failed decision
    # cannot stand in for exhaustion. Keep the existing shared search deadline.
    observed_result = None
    for offset in range(0, max(1, len(eligible)), 64):
        result = await _local_llm_choose_goal_window(
            entry, target, eligible[offset:offset + 64], history,
            login=False, collection=True)
        if result is None:
            return None
        if result.get("collection_observed"):
            observed_result = result
        if result.get("id"):
            if observed_result and not result.get("collection_observed"):
                # Windows partition controls, not the page's content. A later
                # route choice must not erase records observed in this page.
                result = {**result, "collection_observed": True,
                          "proximity": observed_result["proximity"]}
            return result
    if observed_result and eligible and first_visit:
        # Items already satisfying the request tend to end the branch, though
        # an exhaustive collection still needs more items of the same list.
        # Ask that narrower question on the first decision of a page only: on
        # a return, the remaining controls are what the page did not offer.
        for offset in range(0, len(eligible), 64):
            more = await _local_llm_choose_goal_window(
                entry, target, eligible[offset:offset + 64], history,
                login=False, collection=True, continuation=True)
            if more is None:
                return None
            if more.get("id"):
                return {**more, "collection_observed": True,
                        "collection_continuation": True,
                        "proximity": observed_result["proximity"]}
    return observed_result or result


async def _local_llm_choose_goal_window(entry, target, eligible, history, *,
                                        login, collection, continuation=False):
    by_id = {}
    observed = []
    limit = 64 if collection else 24
    observation_complete = len(eligible) <= limit
    for candidate in eligible[:limit]:
        cid = str(candidate.get("id") or "")
        if not cid:
            continue
        by_id[cid] = candidate
        observed.append(
            f"{cid}: {candidate.get('role') or candidate.get('tag')} "
            f"{candidate.get('name') or candidate.get('label') or ''}"
            + (f" | {str(candidate.get('context_name') or '')[:300]}"
               if collection else ""))
        if collection:
            # An accessible name can name every dropdown alternative while
            # its visible label shows the current choice. Preserve both;
            # neither the label nor an expanded state is a record by itself.
            control_state = {key: candidate[key] for key in (
                "aria_expanded", "aria_selected", "aria_pressed",
                "aria_checked", "aria_current") if candidate.get(key)}
            if candidate.get("text") and candidate["text"] != (
                    candidate.get("name") or candidate.get("label")):
                control_state["visible_text"] = candidate["text"]
            if control_state:
                observed[-1] += " | " + json.dumps(control_state, ensure_ascii=False)
    content = ""
    if collection:
        try:
            blocks = await entry["page"].evaluate(_GOAL_EVIDENCE_JS, True)
            observation_complete &= isinstance(blocks, list)
            if isinstance(blocks, list):
                observation_complete &= len(blocks) < 400 and all(
                    len(str(block)) < 4000 for block in blocks)
                content = "\n".join(str(block) for block in blocks
                                    if isinstance(block, str))
                observation_complete &= len(content) <= 12000
                content = content[:12000]
        except Exception:
            observation_complete = False
        observed = {"CURRENT_CONTENT": content, "CONTROLS": observed}
        if content.strip():
            by_id["CURRENT_CONTENT"] = {"goal_observed": True}
        by_id["NONE"] = {"goal_observed": False}
    if not observed:
        return None
    observation_complete &= len(json.dumps(observed, ensure_ascii=True)) <= 64000
    from agentic_executor import AgenticContext, AgenticLimits, AgenticProposal, run_bounded
    context = AgenticContext(
        goal={"primitive": "navigate_toward_goal", "target": target,
              "collection": collection,
              "expected_content": str(entry.get("goal_done_when") or "") if collection else "",
              "exhaustive": action_resolver.goal_is_exhaustive(target)},
        observed=observed,
        constraints={
            "forbidden": ("login_entry" if login else
                          "collection_navigation" if collection else
                          "unrelated_control"),
        },
        history=history[-_MAX_GOAL_STEPS:],
    )

    def _call_local(prompt: str) -> str:
        from llm_router import LLMRouter
        from llm_workloads import tier_for
        provider = LLMRouter().provider(tier_for("sites.action_reduce"))
        if getattr(provider, "mode", "") != "local":
            return ""
        import i18n
        import prompt_loader
        system_prompt = prompt_loader.get(
            "agentic_sites_action_system", i18n.current_lang(),
            collection=collection, continuation=continuation)
        response_options = {}
        if continuation:
            response_options["grammar"] = (
                'root ::= "{" ws "\\\"next_control\\\"" ws ":" ws choice ws "}"\n'
                'choice ::= ' + ' | '.join(json.dumps(json.dumps(cid)) for cid in by_id if cid != "CURRENT_CONTENT") + '\n'
                'ws ::= [ \\t\\n\\r]*\n')
        elif collection:
            # Presence and the next route are independent decisions. The
            # broker derives its internal end-of-branch sentinel below.
            response_options["grammar"] = (
                'root ::= "{" ws "\\\"content_status\\\"" ws ":" ws status ws "," ws "\\\"next_control\\\"" ws ":" ws choice ws "}"\n'
                'status ::= "\\\"no_items\\\"" | "\\\"unmatched_items\\\"" | "\\\"matching_items\\\""\n'
                'choice ::= ' + ' | '.join(json.dumps(json.dumps(cid)) for cid in by_id if cid != "CURRENT_CONTENT") + '\n'
                'ws ::= [ \\t\\n\\r]*\n')
        result = provider.chat(
            system_prompt, prompt, max_tokens=64,
            request_timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0,
            **response_options)
        return str(getattr(result, "text", "") or "")

    decision_failure = ""
    started = time.monotonic()

    async def propose(ctx):
        nonlocal decision_failure
        decision_failure = "invalid_response"
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
            decision_failure = "model_timeout"
            return None
        except Exception:
            decision_failure = "provider_error"
            return None
        raw = raw.strip()
        if not raw:
            decision_failure = "empty_response"
            return None
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
        description = str(payload.get("description") or "").strip()
        if continuation:
            choice = payload.get("next_control")
            if not isinstance(choice, str) or choice == "CURRENT_CONTENT" or choice not in by_id:
                return None
            description = choice
        elif collection:
            status = payload.get("content_status")
            statuses = {"no_items": "none", "unmatched_items": "related",
                        "matching_items": "matching"}
            if not isinstance(status, str) or status not in statuses:
                return None
            proximity = statuses[status]
            observed_collection = proximity != "none"
            if observed_collection and not content.strip():
                return None
            choice = payload.get("next_control")
            if not isinstance(choice, str) or choice == "CURRENT_CONTENT" or choice not in by_id:
                return None
            if choice == "NONE":
                if not observation_complete:
                    decision_failure = "incomplete_observation"
                    return None
                description = "CURRENT_CONTENT" if observed_collection else "NONE"
            else:
                description = choice
            by_id[description] = {
                **by_id[description],
                "collection_observed": observed_collection,
                "proximity": proximity,
            }
        decision_failure = ""
        return AgenticProposal(description)

    async def execute(proposal, _ctx):
        return by_id.get(str(proposal.action))

    outcome = await run_bounded(
        context=context, propose=propose, execute=execute,
        validate=lambda proposal, _ctx: str(proposal.action) in by_id,
        limits=AgenticLimits(max_attempts=1, max_observation_chars=64000),
        postcondition=lambda result, _ctx: result is not None,
    )
    if collection:
        sites_audit.record(
            "collection_decision", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            candidate_count=len(eligible), observation_complete=observation_complete,
            outcome=outcome.status, reason=outcome.reason,
            decision_failure=decision_failure,
            selected_control=str((outcome.result or {}).get("id") or ""),
            collection_observed=bool((outcome.result or {}).get("collection_observed")),
            proximity=str((outcome.result or {}).get("proximity") or "none"),
            elapsed_ms=round((time.monotonic() - started) * 1000))
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
                                       settle: bool = False) -> cookie_privacy.CookieOutcome:
    """Reject a privacy overlay as a bounded precondition for any action.

    Consent belongs to an ORIGIN, not to a session. Crossing to the login
    origin raises that origin's own banner, and the state of the previous one
    says nothing about it: a new origin gets a new budget, and is worth
    waiting for. Carrying the old state across made the second banner look
    like a repeat of the first, which is already dismissed.

    `settle` was accepted and never used, so every caller that asked to wait
    for a late panel got no wait at all - and a consent platform renders after
    its own script has loaded, which is exactly the case worth waiting for.

    Measured on turn `a8dbe80b` (10/9/2026): the banner on `www` was
    dismissed, the click to `login.` navigated, the credentials were filled
    three seconds later, and the submit landed underneath that origin's own
    consent banner and an app promotion. The site reported a failed login.
    """
    flow = entry.get("login_flow")
    holder = flow if isinstance(flow, dict) else entry
    state = holder.setdefault("cookie_state", {})
    origin = sites_origin.origin_of_url(
        getattr(entry.get("page"), "url", "") or "")
    if origin and state.get("origin") not in (None, origin):
        # Il tetto NON si azzera con l'origine. Lo stato si', perche' il
        # pannello dell'origine nuova e' un pannello nuovo; ma il conteggio
        # dei clic vive nello stesso stato, e un sito che rimbalza fra due
        # origini lo riportava a zero a ogni salto senza raggiungere mai il
        # limite. Quello che si porta dietro e' il tetto dell'intera sessione.
        state = {"carried": int(state.get("clicks", 0))
                 + int(state.get("carried", 0))}
        holder["cookie_state"] = state
        settle = True
    if origin:
        state["origin"] = origin
    clicks_before = state.get("clicks", 0)
    deadline = _monotonic() + (_REVEAL_SETTLE_MS / 1000.0 if settle else 0.0)
    reobservations = 0
    while True:
        if (reobservations and sites_origin.origin_of_url(
                getattr(entry["page"], "url", "") or "") != origin):
            break
        outcome = await cookie_privacy.reject_cookies(
            entry["page"], state, redact=entry.get("_cookie_redact"),
            timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0,
            enabled=_MODEL_FALLBACKS_ENABLED)
        # A SPA can replace the observed nodes while the local classifier
        # runs. Never click that stale snapshot. Reobserve fresh nodes within
        # the same origin and the existing decision/click/time budgets.
        refresh = (outcome.status == "blocked" and outcome.reason == "stale_dom"
                   and reobservations < _MAX_ACTION_REPLANS and bool(origin)
                   and sites_origin.origin_of_url(
                       getattr(entry["page"], "url", "") or "") == origin)
        if refresh:
            reobservations += 1
        elif (outcome.panels or outcome.status == "blocked"
              or _monotonic() >= deadline):
            break
        if hasattr(entry["page"], "wait_for_timeout"):
            await entry["page"].wait_for_timeout(_REVEAL_POLL_MS)
        else:
            await asyncio.sleep(_REVEAL_POLL_MS / 1000)
    if state.get("clicks", 0) > clicks_before:
        sites_audit.record(
            "overlay_dismiss", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            procedure="privacy_reject", method="semantic",
            outcome=outcome.status == "resolved", reason=outcome.reason)
    # Without this, a page left covered is indistinguishable from a page with
    # no panel at all: both are silent. Counts only, never observed text.
    if outcome.panels or outcome.status == "blocked":
        sites_audit.record(
            "cookie_observation", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            procedure="privacy_reject", phase=outcome.status,
            kind=outcome.kind, reason=outcome.reason,
            frames=outcome.frames, panels=outcome.panels)
    return outcome


def _prossimo_aggancio(drilldown: dict, chosen: dict) -> float:
    """Quanto vale il clic che si sta per fare, da qualunque ramo venga.

    Il drilldown ha la precedenza sulla classifica testuale, quindi la soglia
    va misurata su quello quando c'e': guardare solo `chosen` lasciava passare
    proprio i clic che portavano via (turno `ac7d0cea`).
    """
    if drilldown.get("ok"):
        return float(drilldown.get("confidence", 0.0))
    if chosen.get("ok"):
        return float(chosen.get("confidence", 0.0))
    return 0.0


def _is_goal_drift(flow: dict, url: str, confidence: float) -> bool:
    """Un aggancio PEGGIORE, sullo stesso posto, non e' un passo avanti.

    Misurato sul turno `f33eb0da` (10/9/2026): la pagina delle fatture ha due
    schede, la ricerca ha cliccato «FATTURE» a 0,78 - quella giusta - e poi ha
    continuato sulla stessa pagina con 0,686 e infine «MOVIMENTI» a 0,56,
    tornando sulla scheda sbagliata. Poi ha letto quella. Le confidenze
    scendono in fila: non e' cecita', e' deriva.

    Il budget si spende sul progresso, e un candidato che sullo stesso posto
    vale meno di quello gia' preso non ne e' uno: la ricerca si ferma li' e
    lascia decidere all'arrivo. Vale per qualunque sito con schede o filtri,
    non serve sapere quali siano.
    """
    if confidence <= 0:
        return False
    migliore = (flow.get("best_by_place") or {}).get(url)
    return migliore is not None and confidence < float(migliore)


def _record_goal_progress(flow: dict, url: str, confidence: float) -> None:
    """Il miglior aggancio accettato su questo posto: la soglia da battere."""
    if confidence <= 0 or not url:
        return
    posti = flow.setdefault("best_by_place", {})
    posti[url] = max(float(posti.get(url, 0.0)), confidence)


async def _clear_login_surface(
        entry: dict, *, settle: bool = False) -> cookie_privacy.CookieOutcome:
    """Clear what covers a login form: consent first, then any other overlay.

    Consent is not the only thing that can sit over it. Turn `b6c37087`
    (10/9/2026): with the banner gone, the form filled and the submit button
    plainly uncovered, the login still failed - an app-promotion modal beside
    it kept a full-viewport backdrop that swallowed the click.

    `_prepare_action` has always done both, in this order, for every ordinary
    action; the login path did only the first. The consent outcome is the one
    returned, because only that one can refuse.
    """
    outcome = await _dismiss_privacy_obstruction(entry, settle=settle)
    if outcome.status != "blocked":
        await _dismiss_obstructing_overlay(entry, settle=settle)
    return outcome


def _collection_bind_refusal(entry: dict, page, location: str | None,
                             error_class: str) -> dict:
    """Refuse a collection bind before its click; the error class is the cause.

    Only a refusal on the page and URL where the bind started is marked as
    free of effects. The search still checks its own observation and context.
    """
    unchanged = entry["page"] is page and page.url == location
    return {"ok": False, "error_class": error_class,
            **({"collection_bind_refused": True} if unchanged else {})}


async def _prepare_action(entry: dict, session_id: str, action: str,
                          value_ref: str | None, primitive_override: str | None = None,
                          target_override: str | None = None,
                          allow_model: bool = True,
                          login_choice: dict | None = None,
                          collection_choice: dict | None = None) -> dict:
    # A collection refusal is free of effects only against the page and URL
    # seen before any consent or overlay dismissal of this preparation.
    page_before, url_before = entry["page"], entry["page"].url
    privacy = await _dismiss_privacy_obstruction(entry)
    if isinstance(privacy, cookie_privacy.CookieOutcome) and privacy.status == "blocked":
        return {"ok": False, "error_class": "cookie_precondition_unresolved",
                "obstruction_kind": privacy.kind,
                "obstruction_reason": privacy.reason}
    acted = (isinstance(privacy, cookie_privacy.CookieOutcome)
             and privacy.status == "resolved")
    acted = bool(await _dismiss_obstructing_overlay(entry)) or acted
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
    collection_facet_key = ""
    # goto/wait non hanno un elemento DOM; cred:* viene risolto esclusivamente
    # dal broker, quindi anche il fill ignora ogni target suggerito.
    if collection_choice is not None:
        if value_ref or primitive != "search" or entry.get("secret_pending"):
            return {"ok": False, "error_class": "mandate_scope_exceeded"}
        expected = collection_choice.get("candidate") or {}
        page, location = page_before, url_before
        # A framework render or an overlay dismissal can replace nodes while
        # the model chooses. Broker IDs describe one observation only: bind
        # the same semantic control in the current DOM before scrolling it.
        observed = await _enumerate_candidates(entry["page"])
        fresh = action_resolver.goal_navigation_candidates(
            observed, include_offscreen=True, include_covered=True)
        matches = [item for item in fresh
                   if login_navigation.candidate_key(item)
                   == login_navigation.candidate_key(expected)
                   and action_resolver.goal_candidate_is_admissible(
                       parsed["target"], item,
                       scope=str(entry.get("goal_scope") or ""))]
        # Equal semantic keys include the full href and context. Repeated
        # HTTP links are equivalent destinations, as during model selection;
        # controls without a verifiable link must remain unambiguous.
        matches = _collapse_collection_links(matches)
        if len(matches) != 1:
            sites_audit.record(
                "collection_control_rejected", session_id=session_id,
                phase="before_scroll", match_count=len(matches),
                candidate_key=login_navigation.candidate_key(expected),
                same_name_count=sum(item.get("name") == expected.get("name")
                                    for item in fresh),
                observed_matches=[
                    {k: item.get(k) for k in (
                        "visible", "rendered", "in_viewport", "topmost", "disabled")}
                    for item in observed if login_navigation.candidate_key(item)
                    == login_navigation.candidate_key(expected)])
            return _collection_bind_refusal(
                entry, page, None if acted else location,
                "selector_ambiguous" if len(matches) > 1 else "target_changed")
        if (matches[0].get("in_viewport") is False
                or matches[0].get("topmost") is False):
            if not await _scroll_candidate_into_view(entry, matches[0]):
                return _collection_bind_refusal(
                    entry, page, None if acted else location, "selector_hidden")
        if entry["page"] is not page or page.url != location:
            return {"ok": False, "error_class": "target_changed"}
        observed = await _enumerate_candidates(entry["page"])
        fresh = action_resolver.goal_navigation_candidates(observed)
        matches = [item for item in fresh
                   if login_navigation.candidate_key(item)
                   == login_navigation.candidate_key(expected)
                   and action_resolver.goal_candidate_is_admissible(
                       parsed["target"], item,
                       scope=str(entry.get("goal_scope") or ""))]
        matches = _collapse_collection_links(matches)
        if not matches:
            # A transient modal can reappear between the first safe-exit and
            # this post-scroll read (for example after a SPA backtrack). Only
            # retry when the exact saved control is still uniquely present
            # and visibly covered. The existing safe-exit never navigates;
            # the control itself is rebound from a fresh DOM afterwards.
            covered = [item for item in observed
                       if login_navigation.candidate_key(item)
                       == login_navigation.candidate_key(expected)
                       and item.get("rendered") is True
                       and item.get("visible") is True
                       and item.get("in_viewport") is True
                       and item.get("topmost") is False
                       and not item.get("disabled")]
            if len(covered) == 1:
                if await _dismiss_obstructing_overlay(entry, settle=True):
                    # Closing the overlay acted on the page: a later refusal
                    # is no longer free of effects.
                    acted = True
                    if entry["page"] is not page or page.url != location:
                        return {"ok": False, "error_class": "target_changed"}
                    observed = await _enumerate_candidates(page)
                    fresh = action_resolver.goal_navigation_candidates(observed)
                    matches = _collapse_collection_links([
                        item for item in fresh
                        if login_navigation.candidate_key(item)
                        == login_navigation.candidate_key(expected)
                        and action_resolver.goal_candidate_is_admissible(
                            parsed["target"], item,
                            scope=str(entry.get("goal_scope") or ""))])
        if len(matches) != 1:
            sites_audit.record(
                "collection_control_rejected", session_id=session_id,
                phase="after_scroll", match_count=len(matches),
                candidate_key=login_navigation.candidate_key(expected),
                same_name_count=sum(item.get("name") == expected.get("name")
                                    for item in fresh),
                observed_matches=[
                    {k: item.get(k) for k in (
                        "visible", "rendered", "in_viewport", "topmost", "disabled")}
                    for item in observed if login_navigation.candidate_key(item)
                    == login_navigation.candidate_key(expected)])
            return _collection_bind_refusal(
                entry, page, None if acted else location,
                "selector_ambiguous" if len(matches) > 1 else "target_changed")
        candidate = matches[0]
        primitive = "click"
        plan_kind = "goal_navigation"
        confidence = float(collection_choice.get("confidence", 0.5))
        model_selected = bool(collection_choice.get("model_selected"))
    elif login_choice is not None:
        # Private broker choice, revalidated against a fresh DOM. It never
        # accepts a selector, URL or candidate supplied by the model/client.
        if (primitive != "click" or value_ref
                or entry.get("secret_pending") or entry.get("authenticated")
                or (entry.get("login_flow") or {}).get("credentials_started")):
            return {"ok": False, "error_class": "mandate_scope_exceeded"}
        expected = login_choice.get("candidate") or {}
        fresh = await _enumerate_candidates(entry["page"])
        matches = [item for item in _login_entry_candidates(fresh)
                   if _candidate_signature(item) == _candidate_signature(expected)]
        if len(matches) != 1:
            return {"ok": False, "error_class": "target_changed"}
        candidate = matches[0]
        confidence = float(login_choice.get("confidence", 0.5))
        model_selected = bool(login_choice.get("model_selected"))
    elif primitive == "search":
        goal_flow_key = hashlib.sha256(
            action_resolver.normalize(action).encode("utf-8")).hexdigest()
        flows = entry.setdefault("goal_flows", {})
        flow = flows.get(goal_flow_key)
        if (not isinstance(flow, dict)
                or time.time() - float(flow.get("started", 0)) > _GATE_MAX_S):
            entry.pop("collected_pages", None)
            flow = {"started": time.time(), "steps": 0, "approved": False,
                    "visited": set(), "history": [], "continuations": 0,
                    "sterile": 0, "last_state": "",
                    "continuation_exhausted": set(),
                    "content_signatures": set(),
                    "collection": action_resolver.is_collection_search_request(
                        action),
                    "collection_attested": False,
                    "collection_facets_visited": set(),
                    "collection_scrolls": 0,
                    "collection_scroll_complete": False}
            flows[goal_flow_key] = flow
        elif action_resolver.is_collection_search_request(action):
            flow["collection"] = True
        candidates = await _enumerate_candidates(entry["page"])
        observed_scope = action_resolver.collection_control_tokens(
            parsed.get("target", ""), candidates)
        target_tokens = set(action_resolver.goal_tokens(
            parsed.get("target", ""), navigation=True))
        # The planner may shorten "find all the bookings for Luxor" to
        # "find bookings for Luxor", losing the grammatical collection
        # marker.  Page evidence is stronger: a generic search control covers
        # only the container while a discriminator remains in the goal.
        # This is language/site neutral and never turns a scalar target into a
        # collection when the control already covers the whole target.
        if observed_scope and (
                flow.get("collection") or target_tokens - observed_scope):
            flow["collection"] = True
            flow["collection_attested"] = True
            flow["collection_scope_tokens"] = sorted(
                set(flow.get("collection_scope_tokens") or ())
                | set(observed_scope))
        if flow.get("collection"):
            flow["collection_target"] = parsed.get("target", "")
        url_corrente = getattr(entry.get("page"), "url", "") or ""
        # The budget is spent on PROGRESS, not on attempts. If after a
        # navigation exactly the previous state is observed, that step moved
        # nothing: it does not consume one of the four exploration steps, but
        # draws on a ceiling of its own, because not even nothing may repeat
        # forever. The candidate that led nowhere is already among the
        # visited ones, so it will not be picked again.
        stato = _goal_state_signature(url_corrente, candidates)
        if (flow.pop("navigazione_da_verificare", False)
                and stato == flow.get("last_state")):
            _ritira_il_passo(flow)
        # Un clic che non ha cambiato il contenuto non e' un passo, e
        # soprattutto non e' un arrivo: il candidato e' gia' fra i visitati,
        # quindi il giro successivo prova un altro modo di aprire la stessa
        # cosa invece di dichiarare fatto e leggere quel che c'era prima.
        arrivo_non_provato = bool(flow.pop("facet_unchanged", False))
        if arrivo_non_provato:
            _ritira_il_passo(flow)
        flow["last_state"] = stato
        flow_steps = int(flow.get("steps", 0))
        at_goal_limit = (flow_steps >= _MAX_GOAL_STEPS
                         or int(flow.get("sterile", 0)) >= _MAX_GOAL_STERILE)
        excluded = set(flow.get("visited") or ())
        # The place already occupied is not a destination: a link leading back
        # here is not an exploration step. Derived on every pass rather than
        # stored once, so it holds after a reload too — which clears the
        # visited set without moving the pilot.
        posto_corrente = action_resolver.url_place_key(url_corrente)
        if posto_corrente:
            excluded.add(posto_corrente)
        # Il sito su cui siamo: serve a sapere se il fine distingue qualcosa
        # qui dentro (un fine che coincide col nome del sito non distingue).
        sito_corrente = _host_of_url(url_corrente)
        chosen = ({"ok": False, "error_class": "goal_step_limit"}
                  if at_goal_limit else action_resolver.choose_goal_candidate(
                      parsed.get("target", ""), candidates,
                      excluded=excluded, site_host=sito_corrente,
                      scope=str(entry.get("goal_scope") or "")))
        if not at_goal_limit and not chosen.get("ok"):
            scroll = action_resolver.choose_goal_scroll_candidate(
                parsed.get("target", ""), candidates, excluded=excluded)
            if scroll.get("ok") and await _scroll_candidate_into_view(
                    entry, scroll["candidate"]):
                candidates = await _enumerate_candidates(entry["page"])
                chosen = action_resolver.choose_goal_candidate(
                    parsed.get("target", ""), candidates,
                    excluded=excluded, site_host=sito_corrente,
                    scope=str(entry.get("goal_scope") or ""))
        # Il canale STRUTTURALE (aprire l'area personale) non e' un ripiego di
        # cortesia riservato al primo passo: e' la mossa GIUSTA ogni volta che
        # la classifica testuale non puo' decidere — perche' ha fallito, o
        # perche' il fine non discrimina su questo sito. Limitarlo a
        # `flow_steps == 0` lo rendeva irraggiungibile proprio dove serviva:
        # al passo 0 vinceva il logo, e dal passo 1 in poi nessuno lo chiedeva
        # piu' (turno reale 7/8/2026, quattro passi spesi per restare fermi).
        if (not at_goal_limit and not chosen.get("ok")
                and entry.get("authenticated")):
            chosen = action_resolver.choose_authenticated_reveal_candidate(
                candidates, excluded=excluded,
                account_only=flow_steps > 0)
        # Il contenitore puo' essere gia' la pagina corrente (URL diretto o
        # landing utile): verificare prima evita click artificiali. La prova
        # esclude i soli label interattivi, quindi un menu omonimo non basta.
        goal_satisfied = await _page_satisfies_goal(
            entry, entry.get("goal_done_when") or parsed.get("target", ""),
            candidates)
        collection_no_match = False
        collection_facet = {"ok": False,
                            "error_class": "selector_missing"}
        goal_drilldown = {"ok": False,
                          "error_class": "selector_missing"}
        drilldown_ambiguous = False
        if goal_satisfied:
            goal_drilldown = action_resolver.choose_goal_drilldown_candidate(
                parsed.get("target", ""), candidates,
                collection_tokens=set(
                    flow.get("collection_scope_tokens") or ()),
                excluded=excluded)
            if goal_drilldown.get("error_class") == "selector_ambiguous":
                drilldown_ambiguous = True
                goal_drilldown = {"ok": False,
                                  "error_class": "selector_missing"}
        # A collection page can expose a generic control whose label shares
        # only the collection noun with the goal (for example a separate
        # lookup form).  Clicking that partial match is not evidence of
        # progress toward the requested record.  Once the current page itself
        # attests the collection, scan its bounded lazy content first.  If the
        # complete scan still lacks the full goal, report an empty result
        # instead of opening an unrelated control.
        if flow.get("collection") and not goal_drilldown.get("ok"):
            collection_attested = bool(flow.get("collection_attested"))
            if (not collection_attested and chosen.get("ok")
                    and not action_resolver.goal_candidate_is_exact(
                        parsed.get("target", ""), chosen["candidate"])):
                partial_name = str(
                    chosen["candidate"].get("name")
                    or chosen["candidate"].get("label") or "")
                observed_scope = action_resolver.collection_control_tokens(
                    parsed.get("target", ""), [chosen["candidate"]])
                collection_attested = bool(observed_scope) and \
                    await _page_satisfies_goal(entry, partial_name, candidates)
                if collection_attested:
                    flow["collection_scope_tokens"] = sorted(observed_scope)
            if collection_attested:
                flow["collection_attested"] = True
                await _expand_collection_by_scrolling(entry, flow)
                candidates = await _enumerate_candidates(entry["page"])
                goal_drilldown = (
                    action_resolver.choose_goal_drilldown_candidate(
                        parsed.get("target", ""), candidates,
                        collection_tokens=set(
                            flow.get("collection_scope_tokens") or ()),
                        excluded=excluded))
                if goal_drilldown.get("error_class") == "selector_ambiguous":
                    drilldown_ambiguous = True
                    goal_drilldown = {"ok": False,
                                      "error_class": "selector_missing"}
                if goal_drilldown.get("ok"):
                    chosen = goal_drilldown
                goal_satisfied = await _page_satisfies_goal(
                    entry,
                    entry.get("goal_done_when") or parsed.get("target", ""),
                    candidates)
                goal_satisfied = goal_satisfied or drilldown_ambiguous
                if (not goal_satisfied and not goal_drilldown.get("ok")
                        and flow.get("collection_scroll_complete")):
                    visited_facets = flow.setdefault(
                        "collection_facets_visited", set())
                    # The selected facet is the collection just scanned. Mark
                    # it before choosing another one, so a tab strip cannot
                    # send the search back and forth between two views.
                    visited_facets.update(
                        action_resolver.active_collection_facet_keys(
                            candidates))
                    collection_facet = (
                        action_resolver.choose_collection_facet_candidate(
                            candidates, excluded=set(visited_facets)))
                    if (not collection_facet.get("ok")
                            and collection_facet.get("error_class")
                            != "selector_missing"):
                        return collection_facet
                    collection_no_match = not collection_facet.get("ok")
        continuation = {"ok": False, "error_class": "selector_missing"}
        if goal_satisfied and not goal_drilldown.get("ok"):
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
        elif collection_facet.get("ok"):
            continuation = collection_facet
        if (continuation.get("ok")
                and int(flow.get("continuations", 0))
                    >= _MAX_GOAL_CONTINUATIONS):
            return {"ok": False,
                    "error_class": "goal_continuation_limit"}
        if collection_no_match:
            candidate = None
            primitive = "observe"
            plan_kind = "goal_no_match"
            confidence = 1.0
        elif continuation.get("ok"):
            candidate = continuation["candidate"]
            primitive = "click"
            plan_kind = "goal_continuation"
            confidence = float(continuation.get("confidence", 0.0))
            collection_facet_key = str(
                continuation.get("facet_key") or "")
        elif (int(flow.get("steps", 0)) > 0 and not arrivo_non_provato
                and _is_goal_drift(flow, url_corrente, _prossimo_aggancio(
                    goal_drilldown, chosen))):
            # Si e' gia' fatto meglio, qui: quello che resta porta altrove.
            # Si dichiara l'arrivo invece di consumare un altro passo per
            # peggiorare - e invece di fallire, perche' un posto raggiunto
            # resta raggiunto. Sopra il drilldown, non sotto: i clic che
            # portavano via venivano proprio da li'.
            primitive = "observe"
            plan_kind = "goal_complete"
        elif goal_drilldown.get("ok"):
            candidate = goal_drilldown["candidate"]
            primitive = "click"
            plan_kind = "goal_navigation"
            confidence = float(goal_drilldown.get("confidence", 0.0))
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
        # La soglia da battere sul posto in cui ci si trova: un aggancio piu'
        # debole di questo, qui, sarebbe deriva e non progresso.
        if plan_kind in ("goal_navigation", "goal_continuation"):
            _record_goal_progress(flow, url_corrente, confidence)
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
        plan["context_key"] = _context_key(candidates)
        if collection_facet_key:
            plan["collection_facet_key"] = collection_facet_key
    elif plan_kind == "goal_navigation":
        # Anche una navigazione va verificata quando resta sulla stessa
        # pagina: una scheda, un filtro, una fisarmonica cambiano il
        # CONTENUTO senza cambiare indirizzo, e finora nessuno controllava
        # che il clic avesse fatto qualcosa. Turno reale del 10/9/2026: il
        # clic su «FATTURE» non ha aperto la scheda, la tabella dei movimenti
        # e' rimasta li', e il turno l'ha letta credendo fossero le fatture.
        plan["facet_sig_before"] = await _goal_facet_signature(entry)
        plan["place_before"] = action_resolver.url_place_key(
            getattr(entry.get("page"), "url", "") or "")
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


_MOTIVI_NAVIGAZIONE = frozenset({
    "navigation", "navigation_or_submit", "tainted_turn", "low_confidence",
    "reveal_target", "allowlist_extension", "blocked_resources",
})


def _destinazione_di(plan: dict) -> str:
    """Identita stabile del posto dove un piano porta: schema, host, percorso.

    I parametri di query non fanno identita — Booking li riemette in ordine
    diverso fra due render, e un consenso ricordato su quella stringa non
    varrebbe mai due volte.
    """
    grezzo = str(plan.get("destination_url") or plan.get("target") or "")
    if not grezzo:
        return ""
    try:
        parti = urllib.parse.urlsplit(grezzo)
    except ValueError:
        return ""
    if not parti.hostname:
        return ""
    return f"{parti.scheme.lower()}://{parti.hostname.lower()}{parti.path or '/'}"


def _navigazione_preautorizzata(entry: dict, plan: dict) -> bool:
    """Il consenso c'e gia: dato prima dall'utente, o dato qui poco fa.

    Vale SOLO per la navigazione: i motivi di sensibilita del piano devono
    stare tutti dentro la famiglia «mi sposto / rivelo un menu / ammetto una
    risorsa». Un invio di modulo, una compilazione di credenziali o un
    download non sono navigazione e continuano a chiedere — lo switch si
    chiama suicida, non cieco.
    """
    motivi = set(plan.get("sensitivity_reasons") or ())
    if not motivi <= _MOTIVI_NAVIGAZIONE:
        return False
    candidato = plan.get("candidate") or {}
    if str(candidato.get("form_method") or "").upper() == "POST":
        return False
    if entry.get("auto_allow"):
        return True
    destinazione = _destinazione_di(plan)
    return bool(destinazione
                and destinazione in (entry.get("approved_destinations") or set()))


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
    if parent.get("login_choice"):
        child["login_choice"] = parent["login_choice"]


async def _execute_resource_expansion(entry: dict, token: str,
                                      plan: dict) -> dict:
    hosts = plan.get("resource_hosts") or []
    if (not isinstance(hosts, list) or not hosts
            or any(_canonical_host(host) != host for host in hosts)):
        return {"ok": False, "error_class": "approval_invalid"}
    observed = _blocked_hosts_for_action(entry)
    if any(host not in observed for host in hosts):
        return {"ok": False, "error_class": "target_changed"}
    navigation_url = ""
    if plan.get("login_entry_cross_host"):
        current_url = getattr(entry.get("page"), "url", "")
        current_host = (_host_of_url(current_url)
                        if urllib.parse.urlsplit(current_url).scheme
                        in {"http", "https"} else "")
        if current_host not in hosts:
            if len(hosts) != 1:
                return {"ok": False, "error_class": "target_changed"}
            navigation_url = str((entry.get("blocked_requests") or {})
                                 .get(hosts[0], {}).get("navigation_url") or "")
            if (urllib.parse.urlsplit(navigation_url).scheme
                    not in {"http", "https"}
                    or _host_of_url(navigation_url) != hosts[0]):
                return {"ok": False, "error_class": "target_changed"}
    if plan.get("login_choice") or plan.get("login_entry_cross_host"):
        state = (entry.get("login_flow") or {}).get("entry_search", {})
        if int(state.get("actions", 0)) >= login_navigation.MAX_ACTIONS:
            return {"ok": False, "error_class": "login_step_limit"}
        state["actions"] = int(state.get("actions", 0)) + 1
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
            owner_user_id=entry.get("owner_user_id", ""),
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
            (page.goto(navigation_url, wait_until="load",
                       timeout=int(_OP_TIMEOUT_S * 1000))
             if navigation_url else
             page.reload(wait_until="load",
                         timeout=int(_OP_TIMEOUT_S * 1000))),
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
    if plan.get("login_entry_cross_host"):
        # The original click already happened. Re-observe the exact blocked
        # destination; a Chromium error page itself is never a destination.
        return {"ok": True, "executed": True,
                "primitive": "goto" if navigation_url else "reload"}
    if plan.get("login_entry_refresh"):
        entry["login_flow"]["entry_search"]["refreshed"] = True
        return {"ok": True, "executed": True, "login_entry_refreshed": True}
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
    if plan.get("login_choice"):
        choice = dict(plan["login_choice"])
        candidates = await _enumerate_candidates(entry["page"])
        matches = [candidate for candidate in _login_entry_candidates(candidates)
                   if login_navigation.candidate_key(candidate) ==
                   login_navigation.candidate_key(choice["candidate"])]
        if len(matches) != 1:
            return {"ok": False, "error_class": "target_changed"}
        choice["candidate"] = matches[0]
        prepared = await _prepare_action(
            entry, entry.get("_sid", ""), "click login", None,
            allow_model=False, login_choice=choice)
    else:
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
        "resolved_name": str(candidate.get("name") or candidate.get("label")
                             or candidate.get("text") or "")[:160],
        "verifiable_destination": bool(
            action_resolver._safe_navigation_identity(candidate)),
        "confidence": confidence,
        "model_selected": bool(plan.get("model_selected")),
        "replay": bool(plan.get("collection_replay")),
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
    """Classify browser-owned top-level error documents.

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
    # Un consenso appena dato vale per QUESTA sessione e per QUEL posto: se il
    # flusso ci ripassa (una riosservazione, un secondo passo che rientra),
    # non si richiede all'utente cio' che ha appena concesso. La memoria muore
    # con la sessione, e non copre nulla che non sia navigazione.
    if not plan.get("_ripreso"):
        destinazione = _destinazione_di(plan)
        if destinazione and set(plan.get("sensitivity_reasons") or ()) <= _MOTIVI_NAVIGAZIONE:
            entry.setdefault("approved_destinations", set()).add(destinazione)
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
                owner_user_id=entry.get("owner_user_id", ""),
                session_id=entry.get("_sid", ""),
                domain=entry.get("domain", ""), added_host=observed,
                source="approved_credential_origin")
        entry["pending_actions"].pop(token, None)
        entry["gate_pending"] = False
        await _touch(entry)
        sites_audit.record(
            "credential_origin_approval", owner=entry.get("owner", ""),
            owner_user_id=entry.get("owner_user_id", ""),
            session_id=entry.get("_sid", ""),
            domain=plan.get("vault_domain", ""), origin=observed,
            outcome=True)
        return {"ok": True, "executed": True, "approved": True,
                "primitive": "authorize",
                "credential_origin": plan.get("exact_origin") or observed,
                "url": scrub_url(entry["page"].url)}
    # A scope label belongs to the page reached by the last completed goal.
    # Any new effect invalidates it; a collection goal will bind a fresh label
    # after its navigation has actually succeeded.
    if primitive not in {"wait", "observe"}:
        entry.pop("_source_scope_label", None)
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
                owner_user_id=entry.get("owner_user_id", ""),
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
                continuation_snapshot = {**(await _continuation_snapshot(entry)),
                                         "context": str(plan.get("context_key") or "")}
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
            if (new_pages and plan.get("login_flow") and candidate
                    and action_resolver.is_reveal_control(candidate)):
                # A menu reveal stays on its page. Any simultaneous popup
                # is not the selected navigation branch.
                try:
                    await new_pages[0].close()
                except Exception:
                    pass
                new_pages = []
            if new_pages:
                popup = new_pages[0]
                try:
                    await popup.wait_for_load_state("domcontentloaded", timeout=1500)
                except Exception:
                    pass
                popup_url = popup.url
                popup_host = (_host_of_url(popup_url)
                              if urllib.parse.urlsplit(popup_url).scheme
                              in {"http", "https"} else "")
                blocked_login_popup = bool(
                    plan.get("login_flow")
                    and (_browser_navigation_failure(popup_url)
                         or popup_url == "about:blank"))
                if blocked_login_popup:
                    # Firefox can abort before committing an error document.
                    # A blank popup alone proves no destination: require the
                    # same unique blocked HTTP(S) document as an error popup.
                    observed_hosts = _blocked_login_navigation_hosts(
                        entry, popup=True)
                    if len(observed_hosts) != 1:
                        await popup.close()
                        return {"ok": False,
                                "error_class": "popup_host_unverified"}
                    popup_host = observed_hosts[0]
                allowlist = set(entry.get("allowlist") or ())
                if popup_host and popup_host not in allowlist:
                    if not blocked_login_popup:
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
                    if blocked_login_popup and expansion.get("ok"):
                        expansion["plan"]["login_entry_cross_host"] = True
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
            if continuation_snapshot:
                # Pagination and facets can replace records without changing
                # the URL. Keep every observed page, deduplicated by identity.
                collected = entry.setdefault("collected_pages", [])
                snap_key = _page_identity(
                    str(continuation_snapshot.get("url") or ""),
                    str(continuation_snapshot.get("context") or ""),
                    str(continuation_snapshot.get("text") or ""))
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
            if goal_flow.get("collection") and candidate:
                matched_label = str(
                    candidate.get("name") or candidate.get("label") or ""
                ).strip()
                if matched_label:
                    goal_flow["matched_record_label"] = matched_label[:160]
            # The step is counted now, but it only counts if it moved
            # something: the proof is read at the next observation, where the
            # page controls are already enumerated and measuring costs
            # nothing.
            goal_flow["navigazione_da_verificare"] = True
            # Un clic che resta sullo stesso indirizzo deve provare di aver
            # cambiato il CONTENUTO: e' l'unica prova che una scheda si sia
            # davvero aperta. Se non cambia, il clic non e' avvenuto - e
            # leggere quel che c'era prima significa leggere la scheda
            # sbagliata credendo di essere sull'altra.
            prima = str(plan.get("facet_sig_before") or "")
            if prima and action_resolver.url_place_key(
                    getattr(entry.get("page"), "url", "") or ""
                    ) == str(plan.get("place_before") or ""):
                mosso, _dopo = await _wait_for_goal_content_change(
                    entry, prima, _goal_facet_signature)
                if not mosso:
                    goal_flow["facet_unchanged"] = True
                    sites_audit.record(
                        "goal_facet_unchanged", owner=entry.get("owner", ""),
                        session_id=entry.get("_sid", ""),
                        domain=entry.get("domain", ""), outcome=False)
            visited = goal_flow.setdefault("visited", set())
            # Si segna il POSTO, non l'etichetta dell'elemento: la seconda
            # cambia fra due render dello stesso link, il primo no. E si segna
            # anche dove si e' arrivati, cosi' un collegamento che riporta
            # indietro e' escluso senza doverlo riconoscere dal nome.
            visited.add(action_resolver.goal_place_key(candidate or {}))
            arrivo = action_resolver.url_place_key(
                getattr(entry.get("page"), "url", "") or "")
            if arrivo:
                visited.add(arrivo)
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
            facet_key = str(plan.get("collection_facet_key") or "")
            if facet_key:
                goal_flow.setdefault(
                    "collection_facets_visited", set()).add(facet_key)
                # The scroll bound is per collection partition.  Reusing the
                # exhausted counter would make the newly opened facet look
                # fully scanned before its first scroll.
                goal_flow["collection_scrolls"] = 0
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
    if (plan.get("kind") in {"goal_navigation", "goal_continuation"}
            and not plan.get("collection_search")):
        prepared = await _prepare_after_goal_navigation(
            entry, entry.get("_sid", ""),
            plan.get("original_action") or "", plan.get("value_ref"),
            goal_target=plan.get("goal_target"))
        return await _handle_prepared_action_with_replans(
            entry, entry.get("_sid", ""),
            plan.get("original_action") or "", plan.get("value_ref"),
            prepared)
    if plan.get("kind") == "goal_complete":
        if (isinstance(goal_flow, dict) and goal_flow.get("collection")
                and goal_flow.get("matched_record_label")):
            entry["_source_scope_label"] = str(
                goal_flow["matched_record_label"])
        else:
            entry.pop("_source_scope_label", None)
    if plan.get("kind") in {
            "goal_complete", "goal_search", "goal_no_match"}:
        entry.get("goal_flows", {}).pop(goal_flow_key, None)
    no_match = plan.get("kind") == "goal_no_match"
    return {"ok": True, "executed": not no_match,
            "primitive": primitive,
            **({"no_match": True} if no_match else {}),
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
    # Due modi, entrambi dell'utente, di non farsi chiedere la stessa cosa:
    #  - la pre-autorizzazione esplicita (preferenza «sblocco automatico», che
    #    il proprietario ha chiesto valga anche per il consenso, non solo per
    #    le risorse di rete);
    #  - il consenso GIA dato in questa sessione per la stessa destinazione.
    # Restano fuori per costruzione le cose che non sono navigazione: invio di
    # modulo, compilazione di credenziali, download.
    if not mandated and _navigazione_preautorizzata(entry, plan):
        mandated = True
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
        # An approval needs the destination site and path, not opaque query
        # values that may be one-time login credentials.
        destination = _destinazione_di(plan)
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
                              (plan.get("candidate") or {}).get("text") or
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
            # The label a person reads must be one a person can read: an
            # accessible name can be an unresolved translation key, and the
            # visible text is then the only honest description of the control.
            out["resolved_target"] = str(
                candidate.get("name") or candidate.get("label")
                or candidate.get("text") or "")[:160]
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
            or entry.get("page") is None):
        return result
    if (result.get("error_class") == "selector_hidden"
            and not result.get("page_snapshot")
            and not any(entry.get(k) for k in (
                "secret_pending", "factor_pending", "gate_pending", "user_control_pending"))):
        from . import page_snapshot
        try:
            _sweep_old_shots(entry["owner"])
            evidence = await page_snapshot.save(entry["page"], _shots_dir(entry["owner"]))
        except OSError as exc:
            evidence = {"status": "unavailable", "reason": type(exc).__name__}
        result = {**result, "page_snapshot": evidence}
        sites_audit.record("action_page_snapshot", owner=entry["owner"],
                           session_id=entry.get("_sid", ""), **evidence)
    if result.get("screenshot_path"):
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
                 goal_query: str | None = None,
                 done_when: str | None = None,
                 scope: str | None = None) -> dict:
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
            if plan.get("collection_search"):
                search = entry.get("collection_search") or {}
                if search.get("key") != plan["collection_search"]:
                    executed = {"ok": False, "error_class": "action_expired"}
                elif executed.get("ok") and executed.get("executed"):
                    executed = await _discover_collection(
                        entry, session_id, search["action"], search["target"])
                # A changed approved target cannot silently become a new
                # collection action or lose its cursor and spent budget.
                if not executed.get("ok"):
                    entry.get("pending_actions", {}).pop(approval_token, None)
                    entry["gate_pending"] = False
                    entry.pop("collection_search", None)
                executed = await _with_action_failure_evidence(entry, executed)
                entry.setdefault("completed_approvals", {})[approval_token] = {
                    "ts": time.time(), "result": dict(executed)}
                return executed
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
        # Consent is a precondition, never a step. When the action names the
        # panel, the answer is the state of that precondition - not a control
        # hunted across the page, which is how "accept necessary cookies"
        # became a click on "Open chat". `executed` stays honest: true only if
        # a panel was actually dismissed here.
        if action_resolver.names_privacy_container(action):
            outcome = await _dismiss_privacy_obstruction(entry, settle=True)
            if outcome.status == "blocked":
                return await _with_action_failure_evidence(entry, {
                    "ok": False,
                    "error_class": "cookie_precondition_unresolved",
                    "obstruction_kind": outcome.kind,
                    "obstruction_reason": outcome.reason})
            return {"ok": True, "executed": outcome.status == "resolved",
                    "primitive": "privacy reject",
                    "precondition": "privacy_consent",
                    "panels": outcome.panels, "frames": outcome.frames,
                    "url": getattr(entry.get("page"), "url", "") or ""}
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
        # Come si riconosce l'ARRIVO. Il fine dice dove andare; questo dice
        # quando si e' arrivati, e sono due cose diverse: «le mie prenotazioni»
        # nomina un posto, «l'elenco con destinazione e date» descrive cio' che
        # deve comparire. Senza dichiarazione resta il fine a fare da criterio,
        # che e' il comportamento di prima.
        entry["goal_done_when"] = (done_when.strip()
                                   if isinstance(done_when, str) else "")
        # WHERE the goal lives: the user's own area of the site, or the public
        # one. Declared, it wins over the possession marker, which is the most
        # fragile signal there is — a language expresses possession in many
        # ways and the goal reducer can strip it. Undeclared, nothing changes.
        dichiarato = str(scope or "").strip().lower()
        entry["goal_scope"] = (dichiarato
                               if dichiarato in action_resolver.GOAL_SCOPES
                               else "")
        if (goal_target and not value_ref
                and action_resolver.goal_is_exhaustive(action)):
            result = await _discover_collection(
                entry, session_id, action, goal_target)
            return await _with_action_failure_evidence(entry, result)
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
