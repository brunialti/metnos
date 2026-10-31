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
  §3.1 FIX C — timeout per-op (20s), cap contesti concorrenti (8), quota
               per-utente (8), lock per-sessione (un'op appesa non stalla le altre).
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
import copy
from contextlib import asynccontextmanager
from spin_supervisor import Budget, DrainIncomplete, LockScope, ResourceBusy
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sites_collection_sources
import time
import urllib.parse
from pathlib import Path

from playwright_sidecar import credential_injection
from playwright_sidecar import captcha_solver
from playwright_sidecar import challenges
from playwright_sidecar import captcha_delegate
from playwright_sidecar import captcha_checkbox
from playwright_sidecar import captcha_grid
from playwright_sidecar import redaction
from playwright_sidecar import action_resolver
from playwright_sidecar import browser_surface
from playwright_sidecar import cookie_privacy
from playwright_sidecar import collection_evidence
from playwright_sidecar import collection_outcome
from playwright_sidecar import collection_termination
from playwright_sidecar import login_navigation
from playwright_sidecar import collection_context
from playwright_sidecar import collection_observation
from playwright_sidecar import collection_cutoff
from playwright_sidecar import action_errors
from playwright_sidecar import downloads
import sites_downloads
from messages import get as _msg
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
# Challenge appearances that get automatic attempts in one browser session;
# later ones go to the person (Roberto, 28/10/2026).
_CAPTCHA_APPEARANCES_MAX = 5
_CAPTCHA_GRID_TIME_S = 60.0  # one appearance's image grids, up to captcha_grid.MAX_DRAWS
_MAX_CONTEXTS = 8              # contesti concorrenti totali
_PER_USER_QUOTA = 8           # sessioni per owner
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
# Collection observation and route choice use Frontier when configured;
# without it the local decision stands. Login and other actions keep their
# existing workloads. METNOS_SITES_FRONTIER_ROUTES=0 disables both requests.
_FRONTIER_COLLECTION_ROUTES = _enabled_env(
    "METNOS_SITES_FRONTIER_ROUTES", default=True)
# The return from a detail opened in place tries a verified «back» first;
# METNOS_SITES_RETURN_BACK=0 keeps the ordinary return only.
_COLLECTION_RETURN_BACK = _enabled_env("METNOS_SITES_RETURN_BACK", default=True)
_RESOURCE_DISCOVERY_MS = 1000 # finestra bounded per richieste client-side
_REVEAL_SETTLE_MS = 2000      # attesa bounded target dopo controllo reveal
_LOGIN_ENTRY_SETTLE_S = 5.0   # attesa totale per un ingresso ancora vuoto
_REVEAL_POLL_MS = 100
# Quiet after a scroll step, with no data request, before its content counts
# as unchanged (the reveal budget still applies while data is fetched).
_SCROLL_QUIET_MS = 500
_CONTENT_SETTLE_MS = _bounded_int_env(
    "METNOS_SITES_CONTENT_SETTLE_MS", default=10000,
    minimum=1000, maximum=30000)
# A marker known only by its class name (a skeleton, a container named
# "loading") that stays while the page neither changes nor asks for data for
# this long is decoration, not loading (real run Fastweb8: four waits of the
# whole bound on unchanged pages, 40 of 41 s of settling).
_STALE_MARKER_QUIET_MS = 1500
_CLASS_MARKERS = frozenset({"class_loading", "class_spinner"})
# A drawn page shows controls: a marker over a page without any visible link,
# button or field is a shell still loading, never decoration (real run fw10
# 21/10/2026: a 230-character page without controls was read after the quiet
# and the collection ended there).
_RENDERED_CONTROLS_JS = """Array.from(document.querySelectorAll(
  'a[href],button,input,select,textarea,[role=button],[role=link],[role=menuitem],[role=tab]'))
  .filter((node) => { const box = node.getBoundingClientRect(); return box.width > 0 && box.height > 0; })"""
_VISIBLE_CONTROLS_JS = "() => " + _RENDERED_CONTROLS_JS + ".length"
# What a control is named on the page, never a value typed into a field.
_CONTROL_IDENTITIES_JS = "() => " + _RENDERED_CONTROLS_JS + """.map((node) => [
  node.tagName.toLowerCase(), (node.getAttribute('role') || '').toLowerCase(),
  (node.getAttribute('aria-label') || node.getAttribute('placeholder') || node.innerText
   || node.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 200)])"""
# A click that navigates issues its document request within this interval.
_NAVIGATION_START_GRACE_MS = 1500
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
(held) => {
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
  // The final held-node check must only observe: rewriting these markers
  // can trigger a page MutationObserver and detach the node before click.
  const bindActionIds = held == null;
  if (bindActionIds) {
    document.querySelectorAll('[data-metnos-action-id]').forEach(
      el => el.removeAttribute('data-metnos-action-id'));
  }
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
  const enumeratedActionIds = new Map();
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
        const peers = Array.from(children.keys())
          .filter(peer => recordTextOf(peer) === text)
          .sort((a, b) => a === b ? 0 :
            (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
        return JSON.stringify(el.href ?
          [scope.tagName, scope.getAttribute('role') || '', text, el.href,
           {occurrence: peers.indexOf(scope), count: peers.length}] :
          [scope.tagName, scope.getAttribute('role') || '', text]);
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
    if (bindActionIds) el.setAttribute('data-metnos-action-id', id);
    // Populate in this loop, not ahead of it: a visible child can precede
    // its off-viewport parent, whose ID was not previously an ancestor.
    enumeratedActionIds.set(el, id);
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
      const actionId = enumeratedActionIds.get(p);
      if (actionId) ancestorActionIds.push(actionId);
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
             width: Math.round(r.width), height: Math.round(r.height)},
      // The broker-owned handle, compared as the same physical node: never
      // through an attribute the page could copy onto another element.
      ...(held != null && el === held ? {_held: true} : {})
    });
  }
  return out;
}
"""

# Closed description of what covers a point: structure only, never text,
# names, classes or destinations. Used to diagnose a covered control.
_COVERING_LAYERS_JS = r"""
({x, y, id}) => {
  const top = document.elementFromPoint(x, y);
  if (!top) return {found: false};
  // How the covering element relates to the chosen control: booleans only.
  const target = id ? document.querySelector(`[data-metnos-action-id="${CSS.escape(id)}"]`) : null;
  const link = el => el && el.closest ? el.closest('a[href]') : null;
  const topLink = link(top), targetLink = link(target);
  const pseudo = el => !!el && ['::before', '::after'].some(part => {
    const st = getComputedStyle(el, part);
    return st.content !== 'none' && ['absolute', 'fixed'].includes(st.position);
  });
  const relation = target ? {
    target_contains_top: target.contains(top), top_contains_target: top.contains(target),
    top_is_link: !!topLink, target_is_link: !!targetLink,
    same_destination: !!(topLink && targetLink && topLink.href === targetLink.href),
    pseudo_cover: pseudo(topLink || top)} : null;
  const area = innerWidth * innerHeight;
  const layers = [];
  for (let el = top, depth = 0; el && el !== document.body && depth < 16;
       el = el.parentElement, depth++) {
    const st = getComputedStyle(el), r = el.getBoundingClientRect();
    layers.push({tag: el.localName.slice(0, 16), position: st.position,
      z: st.zIndex === 'auto' ? null : Number.parseInt(st.zIndex, 10),
      area: Math.round(1000 * Math.max(0, r.width) * Math.max(0, r.height) / area) / 1000,
      full_width: r.width >= innerWidth * 0.98,
      dialog: el.localName === 'dialog' || el.getAttribute('role') === 'dialog' ||
        el.getAttribute('role') === 'alertdialog' || el.getAttribute('aria-modal') === 'true'});
  }
  return {found: true, iframe: top.localName === 'iframe', depth: layers.length,
          layers: layers.slice(0, 12), relation};
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
  // Inside an iframe that is itself the covering panel, its own viewport is
  // the panel: the close corner and unlabelled close affordances are measured
  // against it. Only the parent page decides which frame qualifies.
  const frameRoot = !!(config && config.frameRoot);
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
    return structural || (frameRoot ? document.documentElement : null);
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
    if (frameRoot && er.left >= innerWidth - Math.min(100, innerWidth * 0.25) &&
        er.top <= Math.min(100, innerHeight * 0.25)) return true;
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
    // A `javascript:` URL that evaluates nothing (`void(0)`, `void 0`, empty)
    // neither navigates nor runs code: like `#`, behaviour is the handler's.
    if (/^\s*javascript:\s*(void\s*\(\s*0\s*\)|void\s+0)?\s*;?\s*$/i.test(href))
      return false;
    return true;
  };
  const hrefKind = el => {
    const href = (el.getAttribute('href') || '').trim();
    if (!href) return el.tagName.toLowerCase() === 'a' ? 'none' : 'submitter';
    if (/^javascript:/i.test(href)) return 'javascript_code';
    try {
      const url = new URL(href, location.href);
      return url.origin === location.origin ? 'same_origin' : 'cross_origin';
    } catch (e) { return 'invalid'; }
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
  if (frameRoot && document.body && !layers.includes(document.body))
    layers.push(document.body);
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
        name: rawName, kind: hrefKind(el),
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

# Mark iframes that are the topmost layer over a large part of the viewport:
# a panel drawn inside a frame keeps its close control out of reach of the
# parent document. Geometry only; no frame content is read here.
_MARK_OVERLAY_FRAMES_JS = r"""
() => {
  document.querySelectorAll('[data-metnos-overlay-frame]').forEach(
    el => el.removeAttribute('data-metnos-overlay-frame'));
  let marked = 0;
  for (const frame of document.querySelectorAll('iframe')) {
    const r = frame.getBoundingClientRect();
    const st = getComputedStyle(frame);
    if (r.width < 2 || r.height < 2 || st.display === 'none' ||
        st.visibility === 'hidden' ||
        r.width * r.height < innerWidth * innerHeight * 0.12) continue;
    const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
    const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
    if (document.elementFromPoint(x, y) !== frame) continue;
    // A frame in the page flow (a map, a video) is content, not a panel:
    // require the frame or an ancestor to be a fixed/sticky layer or a dialog.
    let layered = false;
    for (let el = frame, depth = 0; el && el !== document.body && depth < 12;
         el = el.parentElement, depth++) {
      const es = getComputedStyle(el), er = el.getBoundingClientRect();
      if (el.localName === 'dialog' || el.getAttribute('role') === 'dialog' ||
          el.getAttribute('role') === 'alertdialog' ||
          el.getAttribute('aria-modal') === 'true' ||
          ((es.position === 'fixed' || es.position === 'sticky') &&
           er.width * er.height >= innerWidth * innerHeight * 0.12)) {
        layered = true;
        break;
      }
    }
    if (!layered) continue;
    frame.setAttribute('data-metnos-overlay-frame', '1');
    if (++marked >= 2) break;
  }
  return marked;
}
"""


_GOAL_EVIDENCE_JS = r"""
(includeInteractiveContent = false, strictVisibility = false) => {
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
  // These facts are valid only during this synchronous observation.  Shared
  // ancestors and split text nodes need not trigger repeated style/layout
  // reads; a later observation always starts with empty maps.
  const styles = new Map(), rectangles = new Map();
  const styleOf = el => {
    if (!styles.has(el)) styles.set(el, getComputedStyle(el));
    return styles.get(el);
  };
  const rectangleOf = el => {
    if (!rectangles.has(el)) rectangles.set(el, el.getBoundingClientRect());
    return rectangles.get(el);
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    const text = (node.nodeValue || '').trim().replace(/\s+/g, ' ');
    if (!parent || !text || parent.closest(excluded)) continue;
    let r = rectangleOf(parent);
    const st = styleOf(parent);
    if (includeInteractiveContent || strictVisibility) {
      if (st.visibility !== 'visible') continue;
      // A hidden ancestor can leave its descendants' own rectangles intact.
      let opacity = 1, hidden = false;
      for (let el = parent; el; el = el.parentElement) {
        const style = styleOf(el);
        if (strictVisibility && (el.hidden || el.getAttribute('aria-hidden') === 'true')) {
          hidden = true;
          break;
        }
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
(strictVisibility = false) => Array.from(document.querySelectorAll('a[href]'))
  .filter(a => {
    if (!strictVisibility) return true;
    const own = getComputedStyle(a);
    if (own.visibility !== 'visible') return false;
    let opacity = 1;
    for (let el = a; el; el = el.parentElement) {
      const style = getComputedStyle(el);
      opacity *= Number.parseFloat(style.opacity || '1');
      if (el.hidden || el.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || style.contentVisibility === 'hidden' ||
          opacity < 0.05) return false;
    }
    let rect = a.getBoundingClientRect();
    if (own.display === 'contents') {
      const range = document.createRange();
      range.selectNodeContents(a);
      rect = range.getBoundingClientRect();
    }
    return rect.width >= 2 && rect.height >= 2;
  })
  .map(a => a.href).slice(0, 400)
"""

_GOAL_CONTENT_SNAPSHOT_JS = """
() => {
  if (!document.body) return null;
  return {url: document.URL, evidence: (""" + _GOAL_EVIDENCE_JS + """)(),
          body: document.body.innerText};
}
"""

_GOAL_NAVIGATION_DOM_SNAPSHOT_JS = """
() => {
  if (!document.body) return null;
  const busy = Array.from(document.querySelectorAll(
    '[aria-busy="true"],[role="progressbar"],progress')).some(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return r.width >= 2 && r.height >= 2 && s.display !== 'none' &&
        s.visibility === 'visible' && Number.parseFloat(s.opacity || '1') >= 0.05;
    });
  return {url: document.URL, ready: document.readyState === 'complete', busy,
          evidence: (""" + _GOAL_EVIDENCE_JS + """)(false, true)};
}
"""

_GOAL_FACET_SNAPSHOT_JS = """
() => {
  if (!document.body) return null;
  return {url: document.URL, evidence: (""" + _GOAL_EVIDENCE_JS + """)(false, true),
          destinations: (""" + _GOAL_LINKS_JS + """)(true)};
}
"""

_TRANSIENT_LOADING_JS = r"""
(input) => {
  const detailed = input && !Array.isArray(input) && input.detailed === true;
  const markers = Array.isArray(input) ? input : input?.markers;
  const result = (loading, reason, value_attribute_present = null) => detailed
    ? {loading, reason, value_attribute_present} : loading;
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
    '[class*="loading" i],[class*="spinner" i]')).find(visible);
  if (semantic) {
    const reason = semantic.matches('[aria-busy="true"]') ? 'aria_busy'
      : semantic.matches('[role="progressbar"]') ? 'progressbar'
      : semantic.matches('progress') ? 'progress'
      : semantic.matches('[class*="loading" i]') ? 'class_loading' : 'class_spinner';
    const valuePresent = reason === 'progressbar' ? semantic.hasAttribute('aria-valuenow')
      : reason === 'progress' ? semantic.hasAttribute('value') : null;
    return result(true, reason, valuePresent);
  }
  // A textless layer over the whole viewport, while no visible control in
  // the viewport can be reached, is a veil: nobody can answer it, and only
  // its removal makes the page usable.
  const actionable = 'a[href],button,input:not([type="hidden"]),select,' +
    'textarea,[role="button"],[role="link"],[tabindex]:not([tabindex="-1"]),iframe';
  let veil = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  while (veil && veil !== document.body && veil !== document.documentElement) {
    const r = veil.getBoundingClientRect();
    if (r.left <= 1 && r.top <= 1 && r.right >= innerWidth - 1 &&
        r.bottom >= innerHeight - 1) break;
    veil = veil.parentElement;
  }
  const veiled = () => {
    if (!veil || veil === document.body || veil === document.documentElement ||
        (veil.innerText || '').trim()) return false;
    let covered = 0;
    for (const el of Array.from(document.querySelectorAll(actionable))
        .filter(visible).slice(0, 300)) {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(
        (Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2,
        (Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2);
      if (hit && (hit === el || el.contains(hit))) return false;
      if (hit && veil.contains(hit)) covered += 1;
    }
    return covered > 0;
  };
  if (veiled()) return result(true, 'veil');
  if (!wanted.length) return result(false, 'clear');
  const textLoading = Array.from(document.querySelectorAll('main *,[role="main"] *,body > *'))
    .some(el => {
      if (!visible(el)) return false;
      const text = normalize(el.innerText || el.textContent || '');
      if (!text || text.length > 120) return false;
      return wanted.some(marker => text === marker || text.startsWith(marker + ' '));
    });
  return result(textLoading, textLoading ? 'loading_text' : 'clear');
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


class _LoginCaptchaBlocked(BaseException):
    """Private control flow: credential helpers must not turn a live worker manual."""
    def __init__(self, reason="captcha_cleanup_pending"):
        super().__init__(reason)
        self.reason = reason


def _captcha_scope_busy(entry: dict) -> bool:
    scope = entry.get("_captcha_scope")
    return bool(scope is not None and scope.entered and
                (not scope.exited or scope.quarantined))


@asynccontextmanager
async def _session_lock(entry: dict):
    """Hold the session lock naming the task that holds it, so a scope that
    borrows the held lock can prove whose it is (asyncio locks have no owner)."""
    async with entry["lock"]:
        entry["_lock_holder"] = asyncio.current_task()
        try:
            yield
        finally:
            entry.pop("_lock_holder", None)


@asynccontextmanager
async def _login_scope(entry: dict, *, held: bool = False):
    holder = entry.get("_lock_holder") if held else None
    if held and holder is None:
        raise ResourceBusy("lock_not_held")
    scope = LockScope(entry["lock"], held_by=holder)
    try:
        async with scope:
            entry["_captcha_scope"] = scope
            yield scope
    finally:
        if entry.get("_captcha_scope") is scope:
            if scope.quarantined:
                # Keep the original lock and every completion handle reachable.
                entry["gate_pending"] = True
                entry["user_control_pending"] = False
                entry["captcha_cleanup_pending"] = True
            else:
                entry.pop("_captcha_scope", None)
                entry.pop("captcha_cleanup_pending", None)


async def _captcha_visual(crop, provider, lease):
    from playwright_sidecar.challenge_vision_process import (
        choose_widget_action_async, VisionDrainIncomplete)
    try:
        return await choose_widget_action_async(crop, provider, lease)
    except VisionDrainIncomplete as error:
        raise DrainIncomplete(error.pending, external=not error.pending) from None


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
    scope = entry.get("_captcha_scope")
    if scope is not None and scope.quarantined:
        if not scope.recover_if_complete():
            return None, "captcha_cleanup_pending"
        entry.pop("_captcha_scope", None)
        entry.pop("captcha_cleanup_pending", None)
        entry["gate_pending"] = False
        entry["user_control_pending"] = False
    elif _captcha_scope_busy(entry):
        return None, "captcha_resource_busy"
    return entry, None


async def _touch(entry: dict) -> None:
    entry["last_used"] = time.time()


async def _close_entry(entry: dict) -> None:
    if _captcha_scope_busy(entry):
        raise RuntimeError("captcha_cleanup_pending")
    # The site may have renewed its cookies during the session.
    await _save_site_session(entry)
    _note_abandoned_challenge(entry)
    if entry.get("downloads"):
        await entry["downloads"].close()
    try:
        await entry["context"].close()
    except Exception:
        pass


def _session_owner_key(entry: dict) -> str:
    uid = entry.get("owner_user_id")
    return f"uid:{uid}" if uid else f"actor:{entry.get('owner') or ''}"


# A saved state is checked within this bound; the login keeps the rest.
_RESTORE_TIMEOUT_S = 30.0


def _stored_credential(binding: str) -> tuple[dict | None, str | None]:
    """The vault record of a binding and a digest of its identity."""
    try:
        payload, storage = credential_injection._load_site_credentials(binding)
    except Exception:  # noqa: BLE001 -- unreadable means no saved session
        return None, None
    if not payload:
        return None, None
    form = credential_injection._credential_form_data(payload)
    user = form.get("username") or form.get("user") or form.get("email") or ""
    identity = [storage, user, credential_injection._fingerprint_payload(payload) or "",
                sorted(map(str, payload.get("credential_origins") or ()))]
    return payload, hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()


def _session_may_contact(entry: dict, host: str) -> bool:
    """A host this session is allowed to talk to (cookie domains included)."""
    host = str(host or "").lower().lstrip(".")
    return bool(host) and any(
        h == host or h.endswith("." + host) or host.endswith("." + h)
        for h in (str(x).lower() for x in entry.get("allowlist") or ()))


def _origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(str(url or ""))
    return f"{parts.scheme}://{parts.netloc}"


async def _password_in_frames(page) -> bool:
    for frame in getattr(page, "frames", ()) or ():
        try:
            if await frame.evaluate("() => !!document.querySelector('input[type=password]')"):
                return True
        except Exception:  # noqa: BLE001 -- a detached frame shows nothing
            continue
    return False


async def _control_identities(page) -> list[str]:
    """Hashes of the rendered controls of the page (tag, role, name); [] when
    the page cannot be read. Names never leave this function."""
    try:
        controls = await asyncio.wait_for(page.evaluate(_CONTROL_IDENTITIES_JS), timeout=5.0)
    except Exception:  # noqa: BLE001 -- an unreadable page proves nothing
        return []
    return sorted({hashlib.sha256("\0".join(str(part) for part in control).encode("utf-8"))
                   .hexdigest()[:16] for control in controls or ()
                   if isinstance(control, list) and len(control) == 3})


async def _save_site_session(entry: dict, *, landing: bool = False) -> None:
    """Keep the browser state of a verified login, encrypted (best effort).

    Only a private page distinct from where the login started can later
    prove the session: right after the login (``landing``) its controls are
    read once the page has settled, and a later restore must show every one
    of them again (real runs ENI r42-r51, 22/10/2026: the saved state reopened
    the same address, yet the site drew its public page or the private frame
    without the user's data). Only cookies of hosts this session could
    contact and the private origin's storage are kept; no stored credential
    or no controls, nothing kept.
    """
    binding = entry.get("saved_session_binding")
    verify_url = str(entry.get("entry_url") or "")
    if (not binding or not entry.get("authenticated") or entry.get("credential_mode") == "none"
            or not verify_url or verify_url == entry.get("login_start_url")
            or entry.get("session_distrusted")):
        return
    import sites_saved_sessions
    owner_key = _session_owner_key(entry)
    _payload, digest = _stored_credential(binding)
    saved, counts = False, {}
    try:
        if digest is None:
            sites_saved_sessions.remove(owner_key, binding)
        else:
            if landing:
                page = entry["page"]
                await _settle_resource_discovery(page)
                await _wait_for_content_settle(entry)
                entry["landing_controls"] = await _control_identities(page)
            state = await asyncio.wait_for(entry["context"].storage_state(), timeout=5.0)
            origin = _origin_of(verify_url)
            state = {"cookies": [c for c in state.get("cookies") or ()
                                 if _session_may_contact(entry, c.get("domain"))],
                     "origins": [o for o in state.get("origins") or () if o.get("origin") == origin]}
            saved = sites_saved_sessions.save(owner_key, binding, state, verify_url,
                                              credential_digest=digest,
                                              landing=entry.get("landing_controls") or [],
                                              saved_at=entry.get("saved_session_saved_at"))
            counts = {"cookies": len(state["cookies"]), "provider_cookies": sum(
                1 for c in state["cookies"] if challenges.provider_cookie(c)),
                "landing_controls": len(entry.get("landing_controls") or ())}
    except Exception:  # noqa: BLE001 -- never blocks the session
        saved = False
    sites_audit.record("login_session_saved", owner=entry.get("owner", ""),
                       domain=binding, outcome=bool(saved), **(counts if saved else {}))


async def _restore_saved_session(entry: dict, session_id: str, binding: str) -> dict | None:
    """Reopen the saved private page with the saved state; None to log in.

    Accepted only for this session's own site, with the same stored
    credential, when the private page answers below 400 at the same address
    with no password field in any frame, no login form or challenge, the
    site's declared session cookies present, and every control seen right
    after the verified login rendered again. Otherwise the state is removed
    and the page and its earlier cookies are put back.
    """
    if (entry.get("credential_mode") == "none" or not binding
            or binding != entry.get("domain")):
        return None
    import sites_saved_sessions
    owner_key = _session_owner_key(entry)
    payload, digest = _stored_credential(binding)
    if digest is None:
        return None
    try:
        saved = sites_saved_sessions.load(owner_key, binding, credential_digest=digest)
    except Exception:  # noqa: BLE001 -- an unreadable state is not a login
        saved = None
    if not saved or not _session_may_contact(
            entry, urllib.parse.urlsplit(saved["verify_url"]).hostname):
        return None
    page, context = entry["page"], entry["context"]
    start_url, target = page.url, urllib.parse.urlsplit(saved["verify_url"])
    origin = _origin_of(saved["verify_url"])
    items = next((o.get("localStorage") or [] for o in saved["origins"]
                  if o.get("origin") == origin), [])
    declared = set(payload.get("session_cookie_names") or ())
    deadline = _monotonic() + _RESTORE_TIMEOUT_S
    restored_keys, missing = [], None

    async def attempt() -> bool:
        nonlocal restored_keys, missing
        timeout = int(_OP_TIMEOUT_S * 1000)
        # A private page may ask the site whether the session is alive and
        # draw the login or a challenge afterwards (real run R33): its data
        # requests are awaited, then the page is judged as it ends up.
        credential_injection._install_request_tracker(page)
        await context.add_cookies(saved["cookies"])
        response = await page.goto(saved["verify_url"], wait_until="load", timeout=timeout)
        if items and _origin_of(page.url) == origin:
            restored_keys = await page.evaluate(_RESTORE_LOCAL_STORAGE_JS, items)
            if restored_keys:
                response = await page.goto(saved["verify_url"], wait_until="load", timeout=timeout)
        await _settle_resource_discovery(page)
        if not await _wait_for_content_settle(entry):
            return False
        # A page with many frames (chat, ads) can leave the observation
        # unavailable: read once more; only a challenge refuses the state
        # (universality review 21/10/2026). The action checks again later.
        observed = (await challenges.observe(page))["status"]
        if observed == "unavailable":
            observed = (await challenges.observe(page))["status"]
        if observed == "required":
            return False
        status = sites_observed.response_signals(response)["status"]
        landed = urllib.parse.urlsplit(page.url)
        now = time.time()
        live = {c.get("name") for c in await context.cookies()
                if not (isinstance(c.get("expires"), (int, float)) and 0 < c["expires"] < now)}
        if not (status is not None and status < 400
                and (landed.scheme, landed.netloc, landed.path)
                == (target.scheme, target.netloc, target.path)
                and (not declared or bool(declared & live))
                and not await _password_in_frames(page)
                and await credential_injection._login_surface_state(page) is False
                and not await credential_injection.classify_login_surface(page)):
            return False
        # A form-free page at the same address is not yet the private one:
        # the site may draw its public page there, or the private frame
        # without the user's data.
        missing = len(set(saved["landing"]) - set(await _control_identities(page)))
        return missing == 0

    before = await context.cookies()
    try:
        verified = await asyncio.wait_for(attempt(), timeout=_RESTORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 -- unverified means a normal login
        verified = False
    sites_audit.record("login_session_restore", owner=entry.get("owner", ""),
                       session_id=session_id, domain=binding, outcome=verified,
                       **({"landing_controls": len(saved["landing"]), "landing_missing": missing}
                          if missing is not None else {}))
    if verified:
        entry["authenticated"] = True
        entry["session_restored"] = True
        entry["landing_controls"] = list(saved["landing"])
        entry["saved_session_binding"] = binding
        entry["saved_session_saved_at"] = saved.get("saved_at")
        entry["login_start_url"] = start_url
        entry["entry_url"] = str(page.url)
        entry["landing_pending"] = True
        sites_audit.record("login_result", owner=entry.get("owner", ""),
                           session_id=session_id, domain=binding, logged_in=True,
                           approval_required=False, reason_code="session_restored")
        return {"ok": True, "logged_in": True, "reason_code": "session_restored",
                "session_id": session_id}
    sites_saved_sessions.remove(owner_key, binding)
    # The site's stale session goes; a challenge provider's own cookies stay,
    # so its checkbox meets the same browser history at the login (Roberto,
    # 20/10/2026: a fresh history turns the checkbox into an image grid).
    provider = [c for c in saved["cookies"] if challenges.provider_cookie(c)]
    try:
        if restored_keys and _origin_of(page.url) == origin:
            await page.evaluate(_FORGET_LOCAL_STORAGE_JS, restored_keys)
        await context.clear_cookies()
        await context.add_cookies(before)
        if provider:
            await context.add_cookies(provider)
    except Exception:  # noqa: BLE001 -- the login flow observes the page itself
        provider = []
    sites_audit.record("login_session_restore_reverted", owner=entry.get("owner", ""),
                       session_id=session_id, domain=binding, provider_cookies=len(provider))
    remaining = max(5.0, deadline - _monotonic())
    try:
        await asyncio.wait_for(page.goto(start_url, wait_until="load",
                                         timeout=int(remaining * 1000)), timeout=remaining)
    except Exception:  # noqa: BLE001 -- a redirect still in flight: let it land
        try:
            await page.wait_for_load_state("load", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
    try:
        await asyncio.wait_for(_settle_resource_discovery(page), timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
    return None


def _challenge_pause(entry: dict, binding: str) -> dict | None:
    """Refuse a fresh login while a person-only challenge on this site waits
    in another session, or within the pause after one was abandoned."""
    owner_key, now = _session_owner_key(entry), time.time()
    waiting = [other for other in _sessions.values()
               if other is not entry and other.get("user_control_pending")
               and not other.get("authenticated")
               and _session_owner_key(other) == owner_key
               and (other.get("login_flow") or {}).get("domain") == binding]
    wait = max((600 - int(now - float(other.get("factor_started") or now)) for other in waiting),
               default=0)
    try:
        import sites_cooldown
        wait = max(wait, sites_cooldown.challenge_retry_after_s(owner_key, binding))
    except Exception:  # noqa: BLE001 -- the pause protects, it never blocks by error
        pass
    if wait <= 0:
        return None
    sites_audit.record("login_attempt", owner=entry.get("owner", ""), domain=binding,
                       outcome=False, reason="login_interval")
    return {"ok": True, "logged_in": False, "reason_code": "login_interval",
            "error_class": "cooldown_active", "retry_after_s": wait}


def _note_challenge(entry: dict, binding: str, result: dict) -> None:
    """A verified login ends a pause left by an abandoned challenge."""
    if not result.get("logged_in"):
        return
    try:
        import sites_cooldown
        sites_cooldown.clear_challenge(_session_owner_key(entry), binding)
    except Exception:  # noqa: BLE001
        pass


def _note_abandoned_challenge(entry: dict) -> None:
    """A session ending at a person-only challenge nobody solved pauses the
    next fresh login to that site; a person present solves it in the flow.

    The challenge may come at the login or, in a session already logged in,
    while navigating (real runs R33/R38): both escalate on a quick retry.
    """
    if not entry.get("user_control_pending"):
        return
    flow = entry.get("login_flow")
    if entry.get("authenticated"):
        binding = (entry.get("saved_session_binding") or entry.get("domain")
                   if entry.get("navigation_challenge") else None)
    else:
        binding = flow.get("domain") if isinstance(flow, dict) else None
    if not binding:
        return
    try:
        import sites_cooldown
        sites_cooldown.record_challenge(_session_owner_key(entry), str(binding))
    except Exception:  # noqa: BLE001
        pass


# Set saved items only where the page has none; returns the keys it set.
_RESTORE_LOCAL_STORAGE_JS = """items => {
  const set = [];
  for (const item of items) {
    if (item && typeof item.name === 'string' && localStorage.getItem(item.name) === null) {
      localStorage.setItem(item.name, String(item.value));
      set.push(item.name);
    }
  }
  return set;
}"""
_FORGET_LOCAL_STORAGE_JS = """names => { for (const name of names) localStorage.removeItem(name); }"""


# ── Reaper (§3.1 FIX B: salta gate_pending) ────────────────────────────────

async def _reaper_loop() -> None:
    while True:
        await asyncio.sleep(_REAP_INTERVAL_S)
        now = time.time()
        dead = []
        for sid, e in list(_sessions.items()):
            if _captcha_scope_busy(e):
                continue  # Never drop an active loan or quarantined handles.
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
    download_capture = downloads.ContextDownloads(context)
    await download_capture.install()
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
        "downloads": download_capture,
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


async def _navigation_challenge(entry: dict, *, navigation_anchor: tuple | None = None,
                                solve: bool = True) -> dict | None:
    """Suspend navigation on a fresh observation, without consuming its cursor.

    A new challenge is first tried with the login's chain (`solve`); only an
    unresolved one, or one already handed over, waits for the person.
    """
    page = entry["page"]
    observed_url = page.url
    scope = frozenset(entry.get("allowlist") or ())
    reanchored = False
    # Only the collection's first check after a consumed navigation opts in.
    # The additional fresh read shares the existing two-observation budget.
    deadline = (_monotonic() + 2 * challenges.OBSERVATION_TIMEOUT_S
                if navigation_anchor is not None else None)

    def refuse(code, reason):
        sites_audit.record("navigation_challenge_rejected",
                           session_id=entry.get("_sid", ""), reason=reason)
        return {"ok": False, "error_class": code}

    def freshness_error():
        current_page = entry["page"]
        if (entry.get("allowlist") and
                _host_of_url(current_page.url) not in set(entry["allowlist"])):
            return "mandate_scope_exceeded", "scope_changed"
        current_context = entry.get("navigation_challenge")
        if current_context and time.time() - current_context["started"] > 600:
            return "user_control_unavailable", "human_context_expired"
        if current_page is not page:
            return "challenge_observation_unavailable", "page_changed"
        if page.url != observed_url:
            return "challenge_observation_unavailable", "document_changed"
        return None

    for attempt in range(3 if navigation_anchor is not None else 2):
        if attempt == 2 and not reanchored:
            break
        blocked = freshness_error()
        if blocked:
            return refuse(*blocked)
        if deadline is None:
            observation = await challenges.observe(page)
        else:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                return refuse("challenge_observation_unavailable", "observation_budget")
            try:
                async with asyncio.timeout(remaining):
                    observation = await challenges.observe(page)
            except TimeoutError:
                return refuse("challenge_observation_unavailable", "observation_budget")
        blocked = freshness_error()
        if blocked:
            if (blocked[1] == "document_changed" and not reanchored
                    and isinstance(navigation_anchor, tuple) and len(navigation_anchor) == 2
                    and navigation_anchor[0] is page and navigation_anchor[1]
                    and sites_origin.origin_of_url(observed_url) == navigation_anchor[1]
                    and sites_origin.origin_of_url(page.url) == navigation_anchor[1]
                    and scope and frozenset(entry.get("allowlist") or ()) == scope
                    and entry.get("authenticated") is True
                    and not any(entry.get(key) for key in (
                        "navigation_challenge", "user_control_pending", "user_control_frame",
                        "factor_pending", "secret_pending", "gate_pending"))
                    and observation["status"] in {"clear", "unavailable"}):
                observed_url = page.url
                reanchored = True
                sites_audit.record("navigation_challenge_reobserved",
                                   session_id=entry.get("_sid", ""), reason="document_changed")
                # Discard the obsolete result, including clear. Only a new
                # complete scan of this document may authorize continuation.
                continue
            return refuse(*blocked)
        if observation["status"] != "unavailable":
            break
    context = entry.get("navigation_challenge")
    if observation["status"] == "unavailable":
        return {"ok": False, "error_class": "challenge_observation_unavailable"}
    if observation["status"] == "clear":
        if context:
            for key in ("navigation_challenge", "user_control_frame",
                        "user_control_pending", "factor_pending", "factor_started"):
                entry.pop(key, None)
        return None
    if _host_of_url(entry["page"].url) not in set(entry.get("allowlist") or ()):
        return {"ok": False, "error_class": "mandate_scope_exceeded"}
    if entry.get("secret_pending") or (entry.get("factor_pending")
                                      and not entry.get("user_control_pending")):
        return {"ok": False, "error_class": "user_control_unavailable"}
    if (solve and not context and not entry.get("user_control_pending")
            and await _resolve_navigation_captcha(entry)):
        return await _navigation_challenge(entry, solve=False)
    # Preserve a pending login's existing context and expiry. Authenticated
    # sessions receive a separate context; authentication alone grants nothing.
    if not entry.get("user_control_pending") or context:
        if not context:
            context = {"started": time.time()}
            entry["navigation_challenge"] = context
        context["observation"] = observation
        entry.update(user_control_pending=True, factor_pending=True,
                     factor_started=context["started"])
    return {"ok": False, "error_class": "captcha_required",
            "user_control_required": True, "challenge": observation}


async def op_user_control(*, action: dict, proof: str) -> dict:
    """A signed browser-control command acts only on a pending challenge.

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
    origin = sites_audit.actor_metadata(action.get("audit_origin") or {
        "actor_type": "browser_control", "executor": "playwright_sidecar",
        "source": "signed_browser_control", "authorization": "not_recorded"})
    sid, owner = action.get("session_id"), action.get("owner")
    entry, error = _validate_owned(sid, owner)
    if entry is None:
        return fail(error)
    async with entry["lock"]:
        navigation = bool(entry.get("navigation_challenge"))
        if (not entry.get("user_control_pending") or entry.get("gate_pending")
                or (entry.get("authenticated") and not navigation) or entry.get("secret_pending")
                or time.time() - float(entry.get("factor_started", 0)) > 600):
            return fail("user_control_unavailable")
        page = entry["page"]
        operation = action.get("operation")
        if not isinstance(operation, str) or operation not in {"snapshot", "click", "scroll"}:
            return fail("invalid_args")
        if navigation:
            # The person has this challenge: observe it, never act on it.
            blocked = await _navigation_challenge(entry, solve=False)
            if not blocked:
                return fail("user_control_complete")
            if not blocked.get("user_control_required"):
                return blocked
        if operation != "snapshot":
            frame = entry.get("user_control_frame") or {}
            if (not action.get("revision") or action["revision"] != frame.get("revision")
                    or page.url != frame.get("url")):
                return fail("page_changed")
            # Unavailable observation preserves the pending handoff and cannot
            # authorize an action or be mistaken for a completed challenge.
            if not navigation:
                pending = await credential_injection.observe_manual_login_challenge(page)
                if pending is None:
                    entry.pop("user_control_frame", None)
                    return fail("challenge_observation_unavailable")
                if pending is False:
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
            audit_event = "navigation_user_action" if navigation else "login_user_action"
            sites_audit.record(audit_event, owner=owner, session_id=sid,
                               action=operation, outcome="requested", action_origin=origin)
            try:
                if operation == "click":
                    await page.mouse.click(x * frame["width"], y * frame["height"])
                else:
                    await page.mouse.wheel(0, (1 if action["direction"] == "down" else -1)
                                           * frame["height"] * 0.65)
            except BaseException:
                sites_audit.record(audit_event, owner=owner, session_id=sid,
                                   action=operation, outcome="unconfirmed", action_origin=origin)
                raise
            sites_audit.record(audit_event, owner=owner, session_id=sid,
                               action=operation, outcome="completed", action_origin=origin)
            # One bounded render interval, not a background watcher.
            await asyncio.sleep(0.35)
        await _touch(entry)
        data = await _redacted_viewport(entry)
        if data is None:
            entry.pop("user_control_frame", None)
            return fail("redaction_failed")
        viewport = await page.evaluate("() => ({width:innerWidth,height:innerHeight})")
        revision = secrets.token_hex(16)
        entry["user_control_frame"] = {**viewport, "revision": revision, "url": page.url}
        if navigation:
            observed = await _navigation_challenge(entry, solve=False)
            if observed and not observed.get("user_control_required"):
                entry.pop("user_control_frame", None)
                return observed
            pending = bool(observed)
        else:
            pending = await credential_injection.observe_manual_login_challenge(page)
            if pending is None:
                entry.pop("user_control_frame", None)
                return fail("challenge_observation_unavailable")
        return {"ok": True, "image": base64.b64encode(data).decode("ascii"),
                "revision": revision, "url": scrub_url(page.url),
                "pending": pending}


async def op_read(*, session_id: str, owner: str | None = None,
                  include_screenshot: bool = True,
                  include_forms: bool = False,
                  goal: str = "") -> dict:
    entry, validation_error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "error": validation_error,
                "error_class": validation_error}
    # The holder is named: a challenge met by the read gets the chain too.
    async with _session_lock(entry):
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
    entry["_sid"] = session_id
    blocked = await _navigation_challenge(entry)
    if blocked:
        return {**blocked, "source_metadata": entry.get("collection_source_metadata", [])}
    page = entry["page"]
    entry["web_content_ingested"] = True
    await _touch(entry)
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
    out = {
        "ok": True, "session_id": session_id, "url": scrub_url(page.url),
        "title": title, "text": text, "sensitive": sensitive,
    }
    if entry.get("_source_scope_label"):
        out["_source_scope_label"] = str(entry["_source_scope_label"])
    if entry.get("collection_source_metadata"):
        from tabular_records import validate_source_metadata
        out["source_metadata"] = validate_source_metadata(entry["collection_source_metadata"])
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
                blocked = await _navigation_challenge(entry)
                if blocked:
                    return {**blocked, "source_metadata": entry.get("collection_source_metadata", [])}
                text = await page.locator("body").inner_text(timeout=3000)
            except Exception:  # noqa: BLE001
                break
    # DOM changes during a read must not turn a verification page into data.
    blocked = await _navigation_challenge(entry)
    if blocked:
        return {**blocked, "source_metadata": entry.get("collection_source_metadata", [])}
    if collected:
        # Collection membership comes from an observation, not from the page
        # where traversal happened to return. Keep each captured snapshot;
        # an unobserved SPA change must not replace it at the same URL.
        current = _page_identity(page.url, _context_key(
            await _enumerate_candidates(page)), text)
        pages = sites_collection_sources.read_pages(
            collected, entry.get("collection_record_views") or [], session_id, current)
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
    if include_screenshot:
        shot = await _capture_screenshot(entry)
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


def _safe_login_rejection(value) -> dict:
    """Project navigation refusals to fixed predicates and bounded counts."""
    if not isinstance(value, dict) or value.get("phase") not in (
            "replay_page", "replay_control", "current_page"):
        return {}
    result = {"phase": value["phase"]}
    for key in ("index", "match_count", "candidate_count", "same_name_count",
                "same_href_count"):
        if type(value.get(key)) is int and value[key] >= 0:
            result[key] = min(value[key], 1_000_000)
    if value.get("producer") in ("broker_page", "nav_refresh", "unknown"):
        result["producer"] = value["producer"]
    predicates = ["same_page", "urls_present", "same_origin", "same_path",
                  "same_query", "same_fragment"]
    for label in ("observation", "location_key", "context_key"):
        predicates.extend((f"saved_{label}_present",
                           f"current_{label}_present", f"same_{label}"))
    result.update({key: value[key] for key in predicates
                   if key in value and (type(value[key]) is bool
                                        or value[key] is None)})
    changes = value.get("single_field_changes")
    if isinstance(changes, dict):
        # Attribute names identify counters, never observed attribute values.
        result["single_field_changes"] = {
            key: min(changes[key], 1_000_000)
            for key in login_navigation._IDENTITY_FIELDS
            if type(changes.get(key)) is int and changes[key] >= 0}
    return result


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
    async with _login_scope(entry) as ownership:
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
        handed_over = bool(entry.pop("user_control_pending", None))
        entry.pop("user_control_frame", None)
        login_budget = _LOGIN_TIMEOUT_S
        flow = entry.get("login_flow")
        if (not isinstance(flow, dict) or flow.get("domain") != dom
                or time.time() - float(flow.get("started", 0)) > _GATE_MAX_S):
            if not approval_token and one_time_code is None:
                # A fresh login: a saved session first; after a person-only
                # challenge the next attempt waits (anti-robot escalation).
                restore_started = _monotonic()
                restored = await _restore_saved_session(entry, session_id, dom)
                if restored is not None:
                    return restored
                login_budget = max(15.0, _LOGIN_TIMEOUT_S - (_monotonic() - restore_started))
                waiting = _challenge_pause(entry, dom)
                if waiting is not None:
                    return {**waiting, "session_id": session_id}
                entry["login_start_url"] = str(getattr(entry.get("page"), "url", "") or "")
            flow = {
                "domain": dom, "started": time.time(), "steps": 0,
                "factor_state": {},
            }
            entry["login_flow"] = flow
            entry["factor_pending"] = False
        elif handed_over:
            # The person had the challenge until this resume: whatever is
            # shown now is a new appearance for the automatic stages.
            flow["captcha_person_handled"] = True
        flow["call_started"] = time.time()

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
            if (executed.get("error_class") in {"target_changed", "page_changed"}
                    and entry.get("pending_actions", {}).get(approval_token) is plan):
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
        captcha_resolutions_before = flow.get("captcha_resolution_invocations", 0)
        try:
            login_task = asyncio.create_task(
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
                    total_timeout_s=login_budget,
                    stealth_techniques=entry.get(
                        "stealth_techniques", ())), name="sites-perform-login")
            if ownership is not None:
                ownership.bind(login_task)
            # wait_for must not wait forever for perform_login to acknowledge
            # cancellation. The scope owns the actual task and its bounded drain.
            res = await asyncio.wait_for(
                asyncio.shield(login_task) if ownership is not None else login_task,
                timeout=login_budget)
            if res.get("logged_in"):
                # Reuse the existing recovery anchor, but bind it to the page
                # just verified by login (including OTP/popup completion), not
                # to the public URL used to open the session. Capture before
                # another await; never promote a failed or pending login.
                entry["entry_url"] = str(
                    getattr(entry.get("page"), "url", "") or "")
                entry["landing_pending"] = True
        except _LoginCaptchaBlocked as blocked:
            res = {"ok": False, "logged_in": False,
                   "reason_code": blocked.reason, "error_class": blocked.reason,
                   "retryable": False, "user_action_required": False,
                   "session_id": session_id}
        except asyncio.TimeoutError:
            if ownership is not None:
                try:
                    await ownership.cancel_delegate()
                except DrainIncomplete:
                    res = {"ok": False, "logged_in": False,
                           "reason_code": "captcha_cleanup_pending",
                           "error_class": "captcha_cleanup_pending",
                           "retryable": False, "user_action_required": False,
                           "session_id": session_id}
            if res is None:
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
        if res.get("reason_code") in {"captcha_cleanup_pending", "captcha_resource_busy"}:
            entry["user_control_pending"] = False
            entry["factor_pending"] = False
            return res  # Preserve flow/handles; no screenshot, manual dialog or retry.
        search = flow.get("entry_search")
        search = search if isinstance(search, dict) else {}
        actions = search.get("actions")
        sites_audit.record(
            "login_result", owner=entry.get("owner", ""),
            session_id=session_id, domain=dom,
            logged_in=bool(res.get("logged_in")),
            approval_required=bool(res.get("approval_required")),
            reason_code=res.get("reason_code"),
            error_class=res.get("error_class"), phase=flow.get("phase"),
            actions=(min(actions, 1_000_000)
                     if type(actions) is int and actions >= 0 else 0),
            rejection=_safe_login_rejection(search.get("rejection")),
            # Which precondition stopped the login (real run Telepass 21/10:
            # `cookie_precondition_unresolved` without its cause). Closed
            # codes only, never page text.
            **{key: res[key] for key in ("obstruction_kind", "obstruction_reason")
               if isinstance(res.get(key), str) and re.fullmatch(r"[a-z_]{1,40}", res[key])})
        if res.get("logged_in"):
            entry["authenticated"] = True
            entry["saved_session_binding"] = dom
            await _save_site_session(entry, landing=True)
        _note_challenge(entry, dom, res)
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
        if entry["user_control_pending"]:
            _audit_captcha_resolution(
                entry, session_id, flow, "handoff", "handoff",
                "resolver_not_called" if flow.get("captcha_resolution_invocations", 0)
                == captcha_resolutions_before else "resolver_unresolved")
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
    blocked = []
    if close_all:
        for sid, e in list(_sessions.items()):
            if e.get("owner") == owner:
                if _captcha_scope_busy(e):
                    blocked.append(sid)
                    continue
                _sessions.pop(sid, None)
                await _close_entry(e)
                closed.append(sid)
                sites_audit.record("session_close", owner=e.get("owner", ""),
                                   session_id=sid, domain=e.get("domain", ""),
                                   kill_switch=True)
        if blocked:
            return {"ok": False, "closed": closed, "count": len(closed),
                    "error_class": "captcha_cleanup_pending", "blocked": blocked}
        return {"ok": True, "closed": closed, "count": len(closed)}
    if not session_id:
        return {"ok": False, "error": "session_id or close_all required",
                "error_class": "invalid_args"}
    e = _sessions.get(session_id)
    if e is None:
        # Idempotente: chiudere una sessione già morta è ok (onesto: count 0).
        return {"ok": True, "closed": [], "count": 0}
    if e.get("owner") != owner:
        return {"ok": False, "error": "not owner", "error_class": "forbidden"}
    if _captcha_scope_busy(e):
        return {"ok": False, "closed": [], "count": 0,
                "error_class": "captcha_cleanup_pending"}
    _sessions.pop(session_id, None)
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


def _target_aria_state(candidate: dict | None) -> dict:
    """Closed state of the clicked control before dispatch; no text or URL.

    A tab or filter already selected answers a click with no observable
    effect: these booleans let a commit wait be read without the page.
    """
    c = candidate if isinstance(candidate, dict) else {}

    def value(key: str) -> str:
        return str(c.get(key) or "").strip().lower()

    return {"target_aria_selected": value("aria_selected") == "true",
            "target_aria_pressed": value("aria_pressed") == "true",
            "target_aria_current": value("aria_current") not in {"", "false"}}


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


class _CandidateObservationError(ValueError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__("candidate_observation_failed")
        self.reason, self.detail = reason, detail


def _evaluation_failure_kind(exc: BaseException) -> str:
    """Closed diagnostic class of a failed read; the message is not kept."""
    text = str(exc).lower()
    for kind, marks in (("context_destroyed", ("context was destroyed", "cannot find context")),
                        ("target_closed", ("has been closed", "target closed")),
                        ("frame_detached", ("detached",)),
                        ("navigation", ("navigat",))):
        if any(mark in text for mark in marks):
            return kind
    return re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:40] or "error"


async def _await_main_document(page) -> bool:
    """Wait, within the settle bound, for a main-frame document to end."""
    deadline = _monotonic() + _CONTENT_SETTLE_MS / 1000.0
    while credential_injection.main_document_pending(page):
        if _monotonic() >= deadline:
            return False
        await asyncio.sleep(_REVEAL_POLL_MS / 1000.0)
    return True


_DOM_SIZE_JS = "() => document.getElementsByTagName('*').length"
_PAGE_REPLACED_KINDS = frozenset({"context_destroyed", "frame_detached", "navigation"})
# A page still drawing after a click changes within this interval.
_DOM_QUIET_MS = 800


async def _wait_for_dom_quiet(page) -> bool:
    """After a click, wait until the page stops changing, within the bound.

    Quiet means the same number of elements, no main document and no recent
    main-frame data request, for _DOM_QUIET_MS. A script link may change the
    address and draw the new page after the click returned; a page that keeps
    changing is read at the bound, as before.
    """
    bound = _CONTENT_SETTLE_MS / 1000.0
    deadline = _monotonic() + bound
    last, quiet_since = None, _monotonic()
    while True:
        try:
            size = await asyncio.wait_for(page.evaluate(_DOM_SIZE_JS), timeout=0.5)
        except Exception as exc:  # noqa: BLE001
            # A page being replaced is not quiet; a page that cannot be read
            # for another reason gives no evidence to wait for.
            if _evaluation_failure_kind(exc) not in _PAGE_REPLACED_KINDS:
                return False
            size = None
        now = _monotonic()
        if (size is None or size != last
                or credential_injection.main_document_pending(page)
                or credential_injection.data_requests_pending(page, bound) > 0):
            last, quiet_since = size, now
        elif now - quiet_since >= _DOM_QUIET_MS / 1000.0:
            return True
        if now >= deadline:
            return False
        await asyncio.sleep(_REVEAL_POLL_MS / 1000.0)


async def _enumerate_candidates(page, *, record_contexts: dict | None = None,
                                held=None) -> list[dict]:
    def evaluate():
        return asyncio.wait_for(
            (page.evaluate(_ENUMERATE_ACTION_TARGETS_JS, held) if held is not None
             else page.evaluate(_ENUMERATE_ACTION_TARGETS_JS)),
            timeout=_ENUMERATE_TIMEOUT_MS / 1000.0,
        )

    try:
        try:
            out = await evaluate()
        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            # A page replaced or redrawn while it is read loses the evaluation
            # (real turns ff8f684c and 4b0ae427): read it once more, after its
            # main document, if any, and its drawing have settled.
            first = _evaluation_failure_kind(exc)
            if not await _await_main_document(page):
                raise _CandidateObservationError("evaluation_failed", first) from exc
            await _wait_for_dom_quiet(page)
            try:
                out = await evaluate()
            except asyncio.TimeoutError:
                raise
            except Exception as again:
                raise _CandidateObservationError(
                    "evaluation_failed", first + "+" + _evaluation_failure_kind(again)) from again
        if not isinstance(out, list) or any(not isinstance(candidate, dict) for candidate in out):
            raise _CandidateObservationError("invalid_observation")
        for candidate in out:
            if candidate.pop("_held", False) is True and held is not None:
                candidate["_held"] = True
            diagnostic = _safe_record_diagnostics(candidate.pop("_record_diagnostics", None))
            if diagnostic:
                candidate["_record_diagnostics"] = diagnostic
            context = candidate.pop("_record_context", "")
            if isinstance(context, str) and context:
                parts = json.loads(context)
                identity = json.dumps(parts[:4], ensure_ascii=False) if len(parts) == 5 else context
                candidate["_record_context_key"] = hashlib.sha256(
                    identity.encode("utf-8")).hexdigest()
                if record_contexts is not None:
                    try:
                        parts = json.loads(context)
                        if isinstance(parts, list) and len(parts) in (3, 5) and isinstance(parts[2], str):
                            record_contexts[candidate["_record_context_key"]] = (
                                {"text": parts[2], **parts[4], "resource": parts[3]} if len(parts) == 5
                                else parts[2])
                    except (ValueError, TypeError):
                        pass
        return out
    except _CandidateObservationError:
        raise
    except asyncio.TimeoutError as exc:
        raise _CandidateObservationError("timeout") from exc
    except Exception as exc:
        raise _CandidateObservationError(
            "evaluation_failed", _evaluation_failure_kind(exc)) from exc


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


async def _page_has_transient_loading(entry: dict, *, timeout_s: float = .5,
                                      observation: dict | None = None) -> bool:
    """Keep the boolean decision; optionally describe its structural evidence."""
    def finish(loading: bool, reason: str, *, value_attribute_present=None,
               unavailable_reason: str = "") -> bool:
        if observation is not None:
            observation.clear()
            observation.update(reason=reason,
                               value_attribute_present=value_attribute_present)
            if unavailable_reason:
                observation["unavailable_reason"] = unavailable_reason
        return loading

    # A main-frame navigation still in flight is transient even without a
    # visible marker: the current document is about to be replaced.
    if credential_injection.main_document_pending(entry.get("page")):
        return finish(True, "document_pending")
    try:
        markers = list(action_resolver.loading_marker_forms())
        observed = await asyncio.wait_for(
            entry["page"].evaluate(
                _TRANSIENT_LOADING_JS,
                {"markers": markers, "detailed": True}
                if observation is not None else markers),
            timeout=timeout_s)
        if observation is not None and isinstance(observed, dict):
            loading, reason = observed.get("loading"), observed.get("reason")
            value_present = observed.get("value_attribute_present")
            if (set(observed) == {"loading", "reason", "value_attribute_present"}
                    and type(loading) is bool
                    and isinstance(reason, str)
                    and reason in {"clear", "aria_busy", "progressbar", "progress",
                                   "class_loading", "class_spinner", "veil", "loading_text"}
                    and loading == (reason != "clear")
                    and (type(value_present) is bool if reason in {"progressbar", "progress"}
                         else value_present is None)):
                return finish(loading, reason, value_attribute_present=value_present)
        # Preserve boolean observations for callers which do not request
        # detail, and existing adapters. True alone cannot identify a marker.
        if observed is False:
            return finish(False, "clear")
        if observed is True:
            return finish(True, "loading_unclassified")
        # Only an observed False proves absence. Failed or malformed
        # observations are unknown and cannot authorize a complete snapshot.
        return finish(True, "observation_unavailable", unavailable_reason="invalid_result")
    except asyncio.TimeoutError:
        return finish(True, "observation_unavailable", unavailable_reason="timeout")
    except Exception:
        return finish(True, "observation_unavailable", unavailable_reason="evaluation_error")


async def _visible_controls(entry: dict) -> int:
    """Visible links, buttons and fields of the page; 0 when unknown."""
    try:
        count = await asyncio.wait_for(entry["page"].evaluate(_VISIBLE_CONTROLS_JS), timeout=.5)
    except Exception:  # noqa: BLE001 -- unknown counts as a shell: the bound applies
        return 0
    return count if type(count) is int and count > 0 else 0


async def _wait_for_content_settle(entry: dict) -> bool:
    """Attende che uno stato di caricamento visibile scompaia.

    Nessuna attesa viene introdotta sulle pagine gia' stabili. Se il marker
    resta visibile oltre il budget, il goal non viene dichiarato completo.
    Main-frame data requests started within the bound are awaited as well;
    one still open at the bound is a hint, not a marker: the page is then
    read as it is.
    """
    credential_injection._install_request_tracker(entry.get("page"))
    bound = _CONTENT_SETTLE_MS / 1000.0

    def fetching() -> bool:
        return credential_injection.data_requests_pending(entry.get("page"), bound) > 0

    started = _monotonic()
    deadline = started + bound
    observation: dict = {"reason": "observation_unavailable",
                         "value_attribute_present": None,
                         "unavailable_reason": "not_sampled"}
    last_valid_observation = None
    last_valid_at = None

    def finish(settled: bool, *, budget_exhausted: bool) -> bool:
        history = {}
        if (observation.get("reason") == "observation_unavailable"
                and last_valid_observation is not None):
            # A failed final sample cannot erase earlier evidence, or turn
            # that earlier evidence into a claim about the current document.
            history["last_valid_observation"] = {
                **last_valid_observation,
                "age_ms": max(0, round((_monotonic() - last_valid_at) * 1000)),
            }
        sites_audit.record(
            "content_settle", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            outcome=settled, budget_exhausted=budget_exhausted,
            elapsed_ms=round((_monotonic() - started) * 1000),
            **observation, **history, **({"shell_without_controls": True} if shell else {}))
        return settled

    loading = True
    quiet = None
    shell = False
    while (remaining := deadline - _monotonic()) > 0:
        loading = await _page_has_transient_loading(
            entry, timeout_s=min(.5, remaining), observation=observation)
        if observation.get("reason") != "observation_unavailable":
            last_valid_observation = dict(observation)
            last_valid_at = _monotonic()
        if not loading and not fetching():
            return finish(True, budget_exhausted=False)
        if loading and observation.get("reason") in _CLASS_MARKERS:
            seen = (await _goal_content_signature(entry),
                    credential_injection.requests_started(entry.get("page")))
            if not seen[0] or fetching() or quiet is None or quiet[1] != seen:
                quiet = (_monotonic(), seen)
            elif _monotonic() - quiet[0] >= _STALE_MARKER_QUIET_MS / 1000:
                if await _visible_controls(entry):
                    observation["stale_marker"] = True
                    return finish(True, budget_exhausted=False)
                shell = True
        else:
            quiet = None
        remaining = deadline - _monotonic()
        if remaining <= 0:
            break
        page = entry["page"]
        pause = min(_REVEAL_POLL_MS / 1000, remaining)
        pause_until = min(deadline, _monotonic() + pause)
        if hasattr(page, "wait_for_timeout"):
            try:
                await asyncio.wait_for(page.wait_for_timeout(pause * 1000),
                                       timeout=remaining)
            except asyncio.TimeoutError:
                break
        else:
            await asyncio.sleep(pause)
        # The browser timer may return before this process's monotonic target.
        # Finish only that pause: do not turn its residual into a DOM sample
        # with an insufficient deadline, or extend the shared settle budget.
        while (pause_remaining := pause_until - _monotonic()) > 0:
            await asyncio.sleep(pause_remaining)
    return finish(not loading, budget_exhausted=True)


async def _wait_for_scroll_effect(entry: dict, before: str, requests_before) -> bool:
    """New content after one scroll step, waited for only while it can come.

    A page that loads more on scroll asks the site for data or redraws at
    once. Without either, the full reveal wait was spent on every step of
    every observed page (real run R41: about 2 s, 124 times, 42% of the
    collection). The wait stops after a short quiet without data requests;
    with one started or pending, it keeps the whole reveal budget.
    """
    page = entry.get("page")
    started = _monotonic()
    deadline = started + _REVEAL_SETTLE_MS / 1000
    while True:
        current = await _goal_content_signature(entry)
        if before and current and current != before:
            return True
        now = _monotonic()
        if now >= deadline:
            return False
        after = credential_injection.requests_started(page)
        fetched = (requests_before is None or after is None
                   or after.get("data") != requests_before.get("data")
                   or credential_injection.data_requests_pending(page, _REVEAL_SETTLE_MS / 1000) > 0)
        if not fetched and now - started >= _SCROLL_QUIET_MS / 1000:
            return False
        await asyncio.sleep(min(_REVEAL_POLL_MS / 1000, max(0.0, deadline - now)))


async def _expand_collection_by_scrolling(entry: dict, flow: dict, *,
                                           max_scrolls: int | None = None) -> bool:
    """Carica porzioni lazy di una collezione con scroll progressivo bounded."""
    if (not flow.get("collection") or flow.get("collection_scroll_complete")):
        return False
    changed_any = False
    limit = _MAX_COLLECTION_SCROLLS if max_scrolls is None else max_scrolls

    async def end_confirmed() -> bool:
        # The short quiet decides each step; the end of a scrolled list is
        # declared after one whole reveal wait, once per flow (a list that
        # loads more after a delay: universality review 21/10/2026).
        if flow.get("collection_scroll_confirmed"):
            return True
        flow["collection_scroll_confirmed"] = True
        return not (await _wait_for_goal_content_change(
            entry, await _goal_content_signature(entry)))[0]

    while int(flow.get("collection_scrolls", 0)) < limit:
        if entry.get("_navigation_action_active") or entry.get("collection_search"):
            blocked = await _navigation_challenge(entry)
            if blocked:
                flow["challenge"] = blocked
                break
        before = await _goal_content_signature(entry)
        credential_injection._install_request_tracker(entry.get("page"))
        requests_before = credential_injection.requests_started(entry.get("page"))
        try:
            scroll = await entry["page"].evaluate(_SCROLL_COLLECTION_JS)
        except Exception:
            flow["collection_scroll_limited"] = True
            break
        if not isinstance(scroll, dict):
            flow["collection_scroll_limited"] = True
            break
        if not scroll.get("moved"):
            if int(flow.get("collection_scrolls", 0)) == 0 or await end_confirmed():
                flow["collection_scroll_complete"] = True
                break
            continue
        flow["collection_scrolls"] = int(
            flow.get("collection_scrolls", 0)) + 1
        if entry.get("_navigation_action_active") or entry.get("collection_search"):
            blocked = await _navigation_challenge(entry)
            if blocked:
                flow["challenge"] = blocked
                break
        progressed = await _wait_for_scroll_effect(entry, before, requests_before)
        await _wait_for_content_settle(entry)
        if entry.get("_navigation_action_active") or entry.get("collection_search"):
            blocked = await _navigation_challenge(entry)
            if blocked:
                flow["challenge"] = blocked
                break
        after = await _goal_content_signature(entry)
        if (before and after and not progressed and after == before
                and scroll.get("after", scroll.get("maximum", 0))
                >= scroll.get("maximum", 0) - 2 and await end_confirmed()):
            flow["collection_scroll_complete"] = True
            break
        changed_any = changed_any or bool(before and after and after != before)
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
        snapshot = await entry["page"].evaluate(_GOAL_CONTENT_SNAPSHOT_JS)
    except Exception:
        return ""
    # Missing or failed observations are not an empty document.  Valid empty
    # documents still have a digest; callers must not infer progress or an
    # exhausted collection from this unavailable sentinel.
    if (not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("url"), str)
            or not isinstance(snapshot.get("evidence"), list)
            or not isinstance(snapshot.get("body"), str)):
        return ""
    # The strict goal evidence excludes interactive rows; the broader body
    # signature detects newly appended links without using them as proof that
    # the goal itself was satisfied.  URL, evidence and body belong to one
    # synchronous browser observation, even if navigation follows its return.
    payload = [scrub_url(snapshot["url"]), snapshot["evidence"][:400],
               snapshot["body"][:50000]]
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
    try:
        snapshot = await entry["page"].evaluate(_GOAL_FACET_SNAPSHOT_JS)
    except Exception:
        return ""
    if (not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("url"), str)
            or not isinstance(snapshot.get("evidence"), list)
            or not isinstance(snapshot.get("destinations"), list)
            or not all(isinstance(url, str) for url in snapshot["destinations"])):
        return ""
    url = snapshot["url"]
    payload = [action_resolver.url_place_key(url) or scrub_url(url),
               snapshot["evidence"], sorted(snapshot["destinations"])]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _ritira_il_passo(flow: dict) -> None:
    """The budget is spent on progress: a step that moved nothing is taken back.

    Not an amnesty: the step returns to the exploration budget but weighs on
    the sterile ceiling, because not even nothing may repeat forever.
    """
    flow["steps"] = max(0, int(flow.get("steps", 0)) - 1)
    flow["sterile"] = int(flow.get("sterile", 0)) + 1


_UNRESOLVED_CONTROL_JS = r"""
(input) => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width >= 2 && r.height >= 2 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const bucket = n => n <= 0 ? 0 : n < 100 ? 1 : n < 1000 ? 2 : n < 10000 ? 3 : 4;
  const copies = Array.from(document.querySelectorAll('a[href]')).filter(a => a.href === input.href);
  const self = copies[0] || null;
  const targets = (input.targets || []).slice(0, 3).map(id => {
    const el = document.getElementById(id);
    if (!el) return {exists: false};
    return {exists: true, display_none: getComputedStyle(el).display === 'none',
            visible: visible(el), height: bucket(Math.round(el.getBoundingClientRect().height)),
            text: bucket((el.innerText || '').length), links: el.querySelectorAll('a[href]').length,
            children: el.childElementCount, aria_hidden: el.getAttribute('aria-hidden') === 'true',
            contains_link: !!(self && el.contains(self))};
  });
  return {copies: copies.length, visible: !!(self && visible(self)),
          expanded: self ? (self.getAttribute('aria-expanded') || '') : '',
          data_attributes: self ? Array.from(self.attributes).map(a => a.name)
            .filter(n => n.startsWith('data-') && !n.startsWith('data-metnos-')).slice(0, 8) : [],
          onclick: !!(self && self.hasAttribute('onclick')), scroll: bucket(Math.round(scrollY)),
          targets};
}
"""


def _href_kind(href: str, page_url: str) -> str:
    """Closed shape of a link relative to its page, never the link itself."""
    if not href:
        return "none"
    if href.lower().startswith("javascript:"):
        return "js"
    link, page = urllib.parse.urlsplit(href), urllib.parse.urlsplit(page_url)
    if link.netloc != page.netloc:
        return "host"
    if link.path != page.path:
        return "path"
    if link.query != page.query:
        return "query"
    return "frag" if link.fragment else "self"


def _scroll_anchor(facts: dict) -> bool:
    """A plain same-page link whose every target exists and is shown."""
    targets = facts.get("targets") or []
    return (facts.get("href_kind") == "frag" and bool(targets)
            and not facts.get("expanded") and not facts.get("onclick")
            and not facts.get("data_attributes")
            and all(t.get("exists") and t.get("visible") and not t.get("display_none")
                    and not t.get("aria_hidden") for t in targets))


async def _audit_unresolved_control(entry: dict, session_id: str, plan: dict, steps: int) -> dict:
    """Closed facts of a clicked collection control that showed no effect.

    Real run Fastweb3 (21/10/2026): «mostra altre» pointed at a fragment of
    the same page and nothing said whether its target existed, was shown, or
    had content. Shapes, counts and attribute names only, never text or
    addresses; any failure leaves the facts out (and the outcome unknown).
    """
    candidate = plan.get("candidate") if isinstance(plan.get("candidate"), dict) else {}
    href = str(candidate.get("href") or "")
    targets = [t for t in candidate.get("control_targets") or () if isinstance(t, str)][:3]
    facts = {"href_kind": _href_kind(href, str(getattr(entry["page"], "url", "") or ""))}
    try:
        observed = await asyncio.wait_for(entry["page"].evaluate(
            _UNRESOLVED_CONTROL_JS, {"href": href, "targets": targets}), timeout=2.0)
        if isinstance(observed, dict):
            facts.update(
                copies=min(int(observed.get("copies") or 0), 1000),
                visible=observed.get("visible") is True,
                expanded=observed.get("expanded") if observed.get("expanded") in {"true", "false"} else "",
                data_attributes=[n for n in observed.get("data_attributes") or ()
                                 if isinstance(n, str) and re.fullmatch(r"data-[a-z0-9-]{1,30}", n)],
                onclick=observed.get("onclick") is True,
                scroll=min(int(observed.get("scroll") or 0), 4),
                targets=[{key: (value if isinstance(value, bool) else min(int(value), 1000))
                          for key, value in target.items()
                          if key in {"exists", "display_none", "visible", "height", "text", "links",
                                     "children", "aria_hidden", "contains_link"}
                          and isinstance(value, (bool, int))}
                         for target in observed.get("targets") or () if isinstance(target, dict)])
    except Exception:  # noqa: BLE001 -- diagnostics never change the outcome
        pass
    facts["scroll_anchor"] = _scroll_anchor(facts)
    sites_audit.record("collection_unresolved_control", owner=entry.get("owner", ""),
                       session_id=session_id, domain=entry.get("domain", ""), steps=steps,
                       **facts)
    return facts


async def _wait_for_goal_content_change(
        entry: dict, before: str, firma=None) -> tuple[bool, str]:
    """Wait, bounded, for the page to differ from `before` by `firma`."""
    firma = firma or _goal_content_signature
    started = _monotonic()
    deadline = started + _REVEAL_SETTLE_MS / 1000
    interval = max(1, _REVEAL_POLL_MS) / 1000
    sample_number = 0
    longest_sample = 0.0
    final_sample_advanced = False
    baseline = before
    current = before
    while True:
        # Read immediately.  Subsequent samples retain the original cadence
        # and final deadline; time spent reading does not extend the budget.
        # At the deadline an already available result is usable, but another
        # asynchronous wait is cancelled. External cancellation propagates.
        sample_started = _monotonic()
        if sample_started > deadline:
            return False, current
        try:
            async with asyncio.timeout(max(0, deadline - _monotonic())):
                observed = await firma(entry)
        except TimeoutError:
            return False, current
        # A delayed event-loop callback may return before its pending timeout
        # callback runs. Such a late observation is not in-budget progress.
        if _monotonic() > deadline:
            return False, current
        if observed:
            current = observed
            if baseline and current != baseline:
                return True, current
            if not baseline:
                baseline = current
        now = _monotonic()
        longest_sample = max(longest_sample, now - sample_started)
        if now >= deadline:
            return False, current
        # Derive each deadline from the origin. Repeated float addition loses
        # the final boundary on hosts with a large monotonic clock value.
        sample_number += 1
        next_sample = started + sample_number * interval
        if next_sample < now:
            sample_number = int((now - started) / interval) + 1
            next_sample = started + sample_number * interval
        if not final_sample_advanced and next_sample >= deadline:
            # A real browser read started at the deadline cannot complete in
            # budget. Reserve its observed cost (with one such cost of slack),
            # within the last poll interval, without shortening the wait or
            # adding a separate timeout. Only one extra sample is permitted.
            next_sample = max(now, deadline - min(interval, 2 * longest_sample))
            final_sample_advanced = True
        await asyncio.sleep(max(0, min(next_sample, deadline) - now))


def _context_key(candidates: list[dict], *, controls=None) -> str:
    """Selected values of account/category selectors; empty without any."""
    chosen = (collection_context.selectors(action_resolver.goal_navigation_candidates(
        candidates, include_offscreen=True, include_covered=True)) if controls is None else controls)
    return collection_context.value_key(chosen) if chosen else ""


def _page_identity(url: str, context: str, text: str) -> str:
    # One definition with the reader's projection of collected pages.
    return sites_collection_sources.page_identity(url, context, text)


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


def _login_entry_exclusion(candidate: dict, current_place: str) -> str:
    """Closed reason why a navigation control is not a login entry, or ""."""
    if (current_place
            and action_resolver.goal_place_key(candidate) == current_place
            and str(candidate.get("aria_current") or "").lower() in {
                "true", "page", "step", "location"}):
        return "current_place"
    if str(candidate.get("form_method") or "").upper() == "POST":
        return "form_post"
    if (str(candidate.get("type") or "").lower() in {"reset", "checkbox", "radio"}
            or str(candidate.get("role") or "").lower() in {
                "checkbox", "radio", "switch", "combobox"}):
        return "toggle"
    return ""


def _login_entry_candidates(candidates: list[dict], current_url: str = "") -> list[dict]:
    """Navigation only: no fields, form submissions or preference toggles."""
    current_place = action_resolver.url_place_key(current_url)
    return [candidate for candidate in
            action_resolver.goal_navigation_candidates(candidates)
            if not _login_entry_exclusion(candidate, current_place)]


_LOGIN_CHOICE_SAMPLE = 8
_LOGIN_LIKENESS_PRESENTATION = {"rendered": True, "visible": True,
                                "in_viewport": True, "topmost": True,
                                "disabled": False}


def _login_choice_audit(observation: dict, tried, *, mechanism: str,
                        lexicon: dict, chosen: dict | None,
                        model_offered: int) -> dict:
    """Closed metadata of one login entry decision, for the audit only.

    Counts, closed reasons and truncated identity hashes: never names,
    values, destinations or page text. For every observed control that the
    shared resolver would accept as a login entry on its own, it says whether
    it was available, already tried, or excluded and why.
    """
    current_place = action_resolver.url_place_key(str(observation.get("url") or ""))
    entry_keys = {login_navigation.candidate_key(candidate)
                  for candidate in observation["candidates"]}
    excluded: dict[str, int] = {}
    matches = []
    for candidate in observation["all_candidates"]:
        key = login_navigation.candidate_key(candidate)
        if key in entry_keys:
            status = "tried" if key in tried else "available"
        else:
            status = (action_resolver.navigation_exclusion(candidate)
                      or _login_entry_exclusion(candidate, current_place)
                      or "other")
            excluded[status] = excluded.get(status, 0) + 1
        # Login-likeness ignores presentation: a covered, hidden or scrolled
        # entry is still reported as one, with its real exclusion reason.
        alone = action_resolver.choose_candidate(
            "login", [{**candidate, **_LOGIN_LIKENESS_PRESENTATION}], "click")
        if alone.get("ok"):
            matches.append((float(alone.get("confidence") or 0.0), key, status,
                            candidate))
    matches.sort(key=lambda item: item[0], reverse=True)
    match_status: dict[str, int] = {}
    for _score, _key, status, _candidate in matches:
        match_status[status] = match_status.get(status, 0) + 1
    available = len(login_navigation.untried_candidates(
        observation["candidates"], tried))
    return {
        "observed": len(observation["all_candidates"]),
        "entry_candidates": len(observation["candidates"]),
        "available": available,
        "tried": len(observation["candidates"]) - available,
        "excluded": excluded,
        "login_matches": len(matches),
        "login_match_status": match_status,
        "login_match_sample": [
            {"key": key[:12], "status": status, "score": round(score, 2),
             "reveal": bool(action_resolver.is_reveal_control(candidate)),
             "verifiable": bool(
                 action_resolver._safe_navigation_identity(candidate))}
            for score, key, status, candidate in matches[:_LOGIN_CHOICE_SAMPLE]],
        "mechanism": mechanism,
        "lexicon_ok": bool(lexicon.get("ok")),
        "lexicon_ambiguous": bool(lexicon.get("ambiguous")),
        "model_offered": int(model_offered),
        "chosen_key": (login_navigation.candidate_key(chosen)[:12]
                       if chosen else ""),
    }


_CAPTCHA_DIAGNOSTIC_REASONS = frozenset({
    "entered", "authority_missing", "binding_changed", "action_budget", "time_budget",
    "textual_challenge", "challenge_not_required", "observation_timeout", "appearance_limit",
    "observation_unavailable", "provider_unsupported", "iframe_not_unique",
    "iframe_provider_mismatch", "frontier_unavailable", "attempt_already_used",
    "delegate_started", "delegate_error", "cancelled", "resolver_not_called",
    "resolver_unresolved", "local_accepted", "local_unresolved", "visual_unresolved",
    "site_postcondition", "unresolved", "effect_budget", "visual_attempt_limit",
    "visual_transport_unbound", "drain_incomplete", "resource_busy", "budget_or_authority",
    "widget_not_unique", "widget_missing", "unqualified_widget",
    "widget_geometry", "widget_crop_budget", "widget_crop_changed",
    "widget_changed_during_capture", "unqualified_challenge_identity", "unstable_widget",
    "invalid_plan", "stale_widget", "extra_model_strategy", "nested_delegation",
    "invalid_strategies", "verification_reserve", "proposal_budget", "no_valid_proposal",
    "postcondition_failed", "budget_or_deadline", "stale_authority", "adapter_failed",
}) | captcha_delegate.WIDGET_QUALIFICATION_REASONS | captcha_checkbox.REASONS | captcha_grid.REASONS


def _audit_captcha_resolution(entry: dict, session_id: str, flow: dict,
                              stage: str, status: str, reason: str, *, allocation=None) -> None:
    """Closed diagnostic facts only; None means consumption was not observed."""
    state_key = {"local": "captcha_state", "visual": "captcha_vision_state",
                 "checkbox": "captcha_checkbox_state", "grid": "captcha_grid_state"}.get(stage)
    state = flow.get(state_key) if state_key else None
    state = state if isinstance(state, dict) else {}

    def count(key):
        value = getattr(allocation, key, None) if allocation is not None else None
        return min(value, 1_000_000) if type(value) is int and value >= 0 else None

    attempted = state.get("attempted")
    def state_count(key):
        value = state.get(key)
        return min(value, 1_000_000) if type(value) is int and value >= 0 else None

    document = state.get("checkbox_document")
    document = document if type(document) is dict else {}
    checkbox_document = None
    if type(document.get("ready")) is str and document["ready"] in {"loading", "interactive", "complete"}:
        checkbox_document = {"phase": "post_refusal", "ready": document["ready"]}
        for key in ("total", "visible", "aria_hidden", "inert", "sampled"):
            value = document.get(key)
            checkbox_document[key] = min(value, 1_000_000) if type(value) is int and value >= 0 else None
    event = state.get("authority_first_event")
    event = event if type(event) is dict else {}
    first_event = None
    if (type(event.get("relation")) is str and event["relation"] in captcha_checkbox.AUTHORITY_EVENT_RELATIONS
            and type(event.get("phase")) is str and event["phase"] in captcha_checkbox.AUTHORITY_PHASES
            and type(event.get("command_requested")) is bool):
        first_event = {key: event[key] for key in ("relation", "phase", "command_requested")}
    sites_audit.record(
        "captcha_resolution", owner=entry.get("owner", ""),
        session_id=session_id, domain=entry.get("domain", ""),
        stage=stage if stage in {"resolver", "local", "checkbox", "grid", "visual", "handoff"} else "unknown",
        status=status if status in {"entered", "skipped", "started", "accepted", "unresolved",
                                    "blocked", "cancelled", "failed", "handoff"} else "unknown",
        reason=reason if type(reason) is str and reason in _CAPTCHA_DIAGNOSTIC_REASONS else "other",
        attempted=attempted if type(attempted) is bool else None,
        authority_failure=(state.get("authority_failure")
                           if type(state.get("authority_failure")) is str
                           and state["authority_failure"] in captcha_checkbox.AUTHORITY_FAILURES else None),
        unrelated_frame_navigations=(min(state["unrelated_frame_navigations"], 1_000_000)
            if type(state.get("unrelated_frame_navigations")) is int
            and state["unrelated_frame_navigations"] >= 0 else None),
        non_owner_frame_navigations=state_count("non_owner_frame_navigations"),
        authority_first_event=first_event,
        authority_additional_events=state_count("authority_additional_events"),
        authority_lifecycle_phase=(state.get("authority_lifecycle_phase")
            if type(state.get("authority_lifecycle_phase")) is str
            and state["authority_lifecycle_phase"] in captcha_checkbox.AUTHORITY_PHASES else None),
        command_requested=(state.get("command_requested")
                           if type(state.get("command_requested")) is bool else None),
        checkbox_count_first=state_count("checkbox_count_first"),
        checkbox_count_last=state_count("checkbox_count_last"),
        checkbox_count_polls=state_count("checkbox_count_polls"),
        checkbox_phase=(state.get("checkbox_phase") if type(state.get("checkbox_phase")) is str
                         and state["checkbox_phase"] in {"bind", "validate"} else None),
        checkbox_expected_same=(state.get("checkbox_expected_same")
                                if type(state.get("checkbox_expected_same")) is bool else None),
        checkbox_document=checkbox_document,
        grid_draws=state_count("draws") if stage == "grid" else None,
        grid_reloads=state_count("reloads") if stage == "grid" else None,
        decision_actor={"actor_type": "executor",
                        "source": "provider_checkbox" if stage == "checkbox" else "public_grid_classifier"}
                       if stage in {"checkbox", "grid"} else {"actor_type": "unknown"},
        model_calls=count("model_calls"), effects=count("effects"))


def _captcha_frontier_available() -> bool:
    """Exact central workload, explicitly Frontier; no implicit role or fallback."""
    from llm_workloads import tier_for
    from llm_router import LLMRouter, TierConfigError
    try:
        request = tier_for("sites.challenge_vision")
    except ValueError:  # The central registry rejects an absent workload.
        return False
    if request != "frontier" or getattr(request, "level", None) is not None:
        return False
    try:
        provider = LLMRouter().provider(request)
    except TierConfigError:
        return False
    return getattr(provider, "name", None) in {"openai", "anthropic"}


async def _resolve_login_captcha(entry: dict, session_id: str, flow: dict, page) -> bool:
    """Semantic provider checkbox first; existing local/visual fallback if unissued.

    Both stages retain the original session lock and one shrinking deadline.
    The historical local library remains opaque: its effects are not certified
    by the visual broker's per-effect fences. Neither solver asserts login.
    """
    flow["captcha_resolution_invocations"] = flow.get("captcha_resolution_invocations", 0) + 1
    _audit_captcha_resolution(entry, session_id, flow, "resolver", "entered", "entered")
    scope = entry.get("_captcha_scope")
    if scope is None or not scope.authorized():
        _audit_captcha_resolution(entry, session_id, flow, "resolver", "blocked", "authority_missing")
        raise _LoginCaptchaBlocked("captcha_resource_busy")
    owner = entry.get("owner")
    allowed_hosts = frozenset(entry.get("allowlist") or ())

    def still_owned():
        origin = urllib.parse.urlsplit(str(page.url))
        # Identity is also checked by tracked effect tasks; task authority is
        # separately enforced by the scope's explicit loan to perform_login.
        return (scope.entered and not scope.exited and not scope.quarantined
                and not scope.revoked and scope.lock.locked() and page is entry.get("page")
                and entry.get("owner") == owner and _sessions.get(session_id) is entry
                and entry.get("_captcha_scope") is scope
                and entry.get("lock") is scope.lock
                and origin.scheme in {"http", "https"}
                and origin.hostname in allowed_hosts)

    def require_binding():
        if not still_owned():
            _audit_captcha_resolution(entry, session_id, flow, "resolver", "blocked", "binding_changed")
            raise _LoginCaptchaBlocked("captcha_resource_busy")

    if not still_owned():
        _audit_captcha_resolution(entry, session_id, flow, "resolver", "skipped", "binding_changed")
        return False
    search = flow.setdefault("entry_search", {})
    # The clock of this call only: a person's time on an earlier challenge of
    # the same login is not the machine's (Fastweb, 28/10/2026: the second
    # checkbox, on the credentials page, was skipped after the person's wait).
    remaining = min(float(search.get("remaining_s", _LOGIN_TIMEOUT_S)),
                    max(0.0, _LOGIN_TIMEOUT_S - (time.time() - float(flow["call_started"]))))
    if remaining <= 0 or int(search.get("actions", 0)) >= login_navigation.MAX_ACTIONS:
        _audit_captcha_resolution(entry, session_id, flow, "resolver", "skipped",
                                  "time_budget" if remaining <= 0 else "action_budget")
        return False
    started = _monotonic()
    deadline = started + min(20.0, remaining)
    try:
        async with asyncio.timeout_at(deadline) as window:
            textual = await credential_injection._manual_only_login_challenge(page)
            require_binding()
            if textual:
                _audit_captcha_resolution(entry, session_id, flow, "resolver", "skipped", "textual_challenge")
                return False
            surface = await credential_injection.classify_login_surface(page)
            require_binding()
            if surface != "captcha_required":
                _audit_captcha_resolution(entry, session_id, flow, "resolver", "skipped", "challenge_not_required")
                return False
            if not _captcha_appearance(entry, flow):
                _audit_captcha_resolution(entry, session_id, flow, "resolver", "skipped", "appearance_limit")
                return False
            checkbox = await _resolve_login_captcha_checkbox(
                entry, session_id, flow, page, deadline=deadline, binding_valid=still_owned)
            require_binding()
            if checkbox is True:
                return True
            # The checkbox opened an image grid, or the grid was already open:
            # the public classifier answers a 3x3 grid, else the person does.
            opened = (checkbox is False and flow.get("captcha_checkbox_state", {}).get("reason")
                      == "checkbox_interactive")
            if not opened and checkbox is None:
                shown = await challenges.observe(page)
                require_binding()
                opened = shown.get("provider") == "recaptcha" and shown.get("kind") == "interactive"
            if opened:
                deadline = started + min(_CAPTCHA_GRID_TIME_S, remaining)
                window.reschedule(deadline)
                return await _resolve_login_captcha_grid(
                    entry, session_id, flow, page, deadline=deadline, binding_valid=still_owned)
            if checkbox is False:
                return False
            kind = await page.evaluate(captcha_solver._CHALLENGE_KIND_JS)
            require_binding()
            if kind in {"turnstile", "interstitial"}:
                solved = await _resolve_login_captcha_local(
                    entry, session_id, flow, page, deadline=deadline,
                    binding_valid=still_owned)
                require_binding()
                if solved:
                    challenge = await credential_injection.observe_manual_login_challenge(page)
                    require_binding()
                    if challenge is False:
                        _audit_captcha_resolution(entry, session_id, flow, "resolver", "accepted", "local_accepted")
                        return True
            return await _resolve_login_captcha_visual(
                entry, session_id, flow, page, deadline=deadline,
                binding_valid=still_owned)
    except TimeoutError:
        require_binding()
        _audit_captcha_resolution(entry, session_id, flow, "resolver", "unresolved", "observation_timeout")
        return False
    except asyncio.CancelledError:
        _audit_captcha_resolution(entry, session_id, flow, "resolver", "cancelled", "cancelled")
        raise
    finally:
        search["remaining_s"] = max(0.0, remaining - (_monotonic() - started))


_CAPTCHA_STAGE_STATES = ("captcha_checkbox_state", "captcha_grid_state", "captcha_state",
                         "captcha_vision_state")


def _captcha_appearance(entry: dict, flow: dict) -> bool:
    """Whether the challenge now shown may get automatic attempts.

    Roberto, 28/10/2026: «il captcha può presentarsi più volte. metnos deve
    riconoscere il captcha e cercare di risolverlo indipendentemente da dove
    e quando si presenta». Each stage still runs once per appearance; an
    appearance ends when a stage was accepted or a person handled it, and the
    next one starts with fresh stages. At most `_CAPTCHA_APPEARANCES_MAX` per
    browser session: a site that keeps asking gets the person.
    """
    if (flow.pop("captcha_person_handled", False)
            or any((flow.get(key) or {}).get("status") in {"accepted", "solved"}
                   for key in _CAPTCHA_STAGE_STATES)):
        for key in (*_CAPTCHA_STAGE_STATES, "captcha_appearance"):
            flow.pop(key, None)
    if flow.get("captcha_appearance"):
        return True
    if int(entry.get("captcha_appearances", 0)) >= _CAPTCHA_APPEARANCES_MAX:
        return False
    entry["captcha_appearances"] = int(entry.get("captcha_appearances", 0)) + 1
    flow["captcha_appearance"] = True
    return True


async def _resolve_login_captcha_checkbox(entry: dict, session_id: str, flow: dict, page,
                                        *, deadline: float, binding_valid) -> bool | None:
    """None permits fallback only when no native command has been requested."""
    scope = entry.get("_captcha_scope")
    if scope is None or not scope.authorized() or not binding_valid():
        _audit_captcha_resolution(entry, session_id, flow, "checkbox", "blocked", "authority_missing")
        raise _LoginCaptchaBlocked("captcha_resource_busy")
    attempt = flow.setdefault("captcha_checkbox_state", {})
    search = flow.setdefault("entry_search", {})
    if (attempt.get("attempted") or flow.get("captcha_state", {}).get("attempted")
            or flow.get("captcha_vision_state", {}).get("effects", 0)):
        _audit_captcha_resolution(entry, session_id, flow, "checkbox", "skipped", "attempt_already_used")
        return False
    if int(search.get("actions", 0)) >= login_navigation.MAX_ACTIONS or deadline <= _monotonic():
        _audit_captcha_resolution(entry, session_id, flow, "checkbox", "skipped",
                                  "time_budget" if deadline <= _monotonic() else "action_budget")
        return False
    allocation = Budget(deadline, 0, 0, 1)

    async def site_clear():
        if not binding_valid():
            return False
        challenge = await credential_injection.observe_manual_login_challenge(page)
        return binding_valid() and challenge is False

    try:
        result = await captcha_checkbox.solve_checkbox(
            page=page, session=session_id, owner=entry.get("owner", ""), budget=allocation,
            resource_lock=entry["lock"], resource_loan=scope,
            binding_valid=binding_valid, site_accepted=site_clear, state=attempt)
        attempt.update(status=result.status, reason=result.reason)
        _audit_captcha_resolution(entry, session_id, flow, "checkbox",
                                  "accepted" if result.status == "accepted" else
                                  "blocked" if result.status == "blocked" else "unresolved",
                                  result.reason, allocation=allocation)
        if result.status == "blocked":
            raise _LoginCaptchaBlocked("captcha_resource_busy")
        return True if result.status == "accepted" else False if result.attempted else None
    except DrainIncomplete:
        _audit_captcha_resolution(entry, session_id, flow, "checkbox", "blocked", "drain_incomplete", allocation=allocation)
        raise _LoginCaptchaBlocked("captcha_cleanup_pending") from None
    except asyncio.CancelledError:
        _audit_captcha_resolution(entry, session_id, flow, "checkbox", "cancelled", "cancelled", allocation=allocation)
        raise
    finally:
        # Reservations are conservative even when a final guard refused input.
        search["actions"] = int(search.get("actions", 0)) + allocation.effects


async def _resolve_login_captcha_grid(entry: dict, session_id: str, flow: dict, page,
                                     *, deadline: float, binding_valid) -> bool:
    """An open reCAPTCHA image grid, answered by the public local classifier.

    Roberto, 29/10/2026: «usa solo risolutore pubblico senza usare frontier; se
    non passa da solo passa all'utente il captcha». Once per appearance, under
    the session loan; any doubt leaves the grid to the person.
    """
    scope = entry.get("_captcha_scope")
    if scope is None or not scope.authorized() or not binding_valid():
        _audit_captcha_resolution(entry, session_id, flow, "grid", "blocked", "authority_missing")
        raise _LoginCaptchaBlocked("captcha_resource_busy")
    attempt = flow.setdefault("captcha_grid_state", {})
    if attempt.get("attempted") or attempt.get("status"):
        _audit_captcha_resolution(entry, session_id, flow, "grid", "skipped", "attempt_already_used")
        return False
    # Grid clicks and reloads have their own cap per appearance; the login's
    # action counter keeps counting only the login's own steps.
    allocation = Budget(deadline, captcha_grid.MAX_MODEL_CALLS,
                        captcha_grid.MAX_MODEL_CALLS * captcha_grid.LABEL_TOKENS, captcha_grid.MAX_EFFECTS)

    async def site_clear():
        if not binding_valid():
            return False
        challenge = await credential_injection.observe_manual_login_challenge(page)
        return binding_valid() and challenge is False

    try:
        result = await captcha_grid.solve_grid(
            page=page, session=session_id, owner=entry.get("owner", ""), budget=allocation,
            resource_lock=entry["lock"], resource_loan=scope,
            binding_valid=binding_valid, site_accepted=site_clear, state=attempt)
        attempt.update(status=result.status, reason=result.reason)
        _audit_captcha_resolution(entry, session_id, flow, "grid",
                                  "accepted" if result.status == "accepted" else
                                  "blocked" if result.status == "blocked" else "unresolved",
                                  result.reason, allocation=allocation)
        if result.status == "blocked":
            raise _LoginCaptchaBlocked("captcha_resource_busy")
        return result.status == "accepted"
    except DrainIncomplete:
        _audit_captcha_resolution(entry, session_id, flow, "grid", "blocked", "drain_incomplete",
                                  allocation=allocation)
        raise _LoginCaptchaBlocked("captcha_cleanup_pending") from None
    except asyncio.CancelledError:
        _audit_captcha_resolution(entry, session_id, flow, "grid", "cancelled", "cancelled",
                                  allocation=allocation)
        raise


async def _resolve_navigation_captcha(entry: dict) -> bool:
    """The login's chain on a challenge met outside the login.

    Roberto, 28/10/2026: the challenge is recognized and tried wherever and
    whenever it appears, not only at the login (real run Fastweb: a checkbox,
    images, «Sei un computer?», then a second checkbox). The provider checkbox,
    the local library and one visual proposal run as at the login, under a
    loan of the session lock the caller already holds, bound to this page,
    owner and session, within the session's appearance cap. True only when
    the site no longer shows the challenge.
    """
    # The registered session holding this entry, not a label it may carry.
    session_id = next((sid for sid, item in _sessions.items() if item is entry), None)
    lock = entry.get("lock")
    if (session_id is None or lock is None or not lock.locked()
            or entry.get("_lock_holder") is not asyncio.current_task()
            or _captcha_scope_busy(entry) or entry.get("secret_pending")
            or entry.get("gate_pending")):
        return False
    page = entry["page"]
    before = str(getattr(page, "url", "") or "")
    flow = {"call_started": time.time(),
            "entry_search": {"remaining_s": _CAPTCHA_GRID_TIME_S, "actions": 0}}
    accepted = False
    async with _login_scope(entry, held=True) as scope:
        task = asyncio.create_task(_resolve_login_captcha(entry, session_id, flow, page))
        scope.bind(task)
        try:
            accepted = await task is True
        except _LoginCaptchaBlocked:
            accepted = False
        except Exception:  # noqa: BLE001 -- the challenge stays for the person
            accepted = False
    if accepted and entry.get("page") is page:
        await _return_after_challenge(entry, before)
    return accepted


async def _resolve_login_captcha_visual(entry: dict, session_id: str, flow: dict, page,
                                      *, deadline: float, binding_valid) -> bool:
    """One closed visual proposal; same Page, challenge absence checked after input."""
    scope = entry.get("_captcha_scope")
    if scope is None or not scope.authorized():
        _audit_captcha_resolution(entry, session_id, flow, "visual", "blocked", "authority_missing")
        raise _LoginCaptchaBlocked("captcha_resource_busy")

    def require_binding():
        if not binding_valid():
            _audit_captcha_resolution(entry, session_id, flow, "visual", "blocked", "binding_changed")
            raise _LoginCaptchaBlocked("captcha_resource_busy")

    require_binding()
    attempt = flow.setdefault("captcha_vision_state", {})
    search = flow.setdefault("entry_search", {})
    if (attempt.get("attempted") or int(search.get("actions", 0)) >= login_navigation.MAX_ACTIONS):
        _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped",
                                  "attempt_already_used" if attempt.get("attempted") else "action_budget")
        return False
    if deadline <= _monotonic():
        _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped", "time_budget")
        return False
    try:
        async with asyncio.timeout_at(deadline):
            textual = await credential_injection._manual_only_login_challenge(page)
            require_binding()
            if textual:
                _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped", "textual_challenge")
                return False
            surface = await credential_injection.classify_login_surface(page)
            require_binding()
            if surface != "captcha_required":
                _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped", "challenge_not_required")
                return False
            observed = await challenges.observe(page)
            require_binding()
            if observed.get("status") != "required" or observed.get("provider") not in {"recaptcha", "hcaptcha", "turnstile"}:
                _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped",
                                          "challenge_not_required" if observed.get("status") == "clear" else
                                          "observation_unavailable" if observed.get("status") != "required"
                                          else "provider_unsupported")
                return False
            # Bind exactly one visible recognized iframe on this Page, never a tab search.
            frames = page.locator("iframe")
            candidates = await frames.evaluate_all(credential_injection._LOGIN_CAPTCHA_FRAMES_JS)
            require_binding()
            if len(candidates) != 1 or candidates[0]["info"]["provider"] != observed["provider"]:
                _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped",
                                          "iframe_not_unique" if len(candidates) != 1
                                          else "iframe_provider_mismatch")
                return False
    except TimeoutError:
        require_binding()
        _audit_captcha_resolution(entry, session_id, flow, "visual", "unresolved", "observation_timeout")
        return False
    if not _captcha_frontier_available():
        _audit_captcha_resolution(entry, session_id, flow, "visual", "skipped", "frontier_unavailable")
        return False
    allocation = Budget(deadline, 1,
                        captcha_delegate.VISION_TOKEN_RESERVATION, 1)
    attempt.update(attempted=True, status="unresolved")

    async def site_clear():
        # Independent DOM observer, never a token or model success assertion.
        # This permits only login continuation; perform_login still owns auth proof.
        if not binding_valid():
            return False
        challenge = await credential_injection.observe_manual_login_challenge(page)
        return binding_valid() and challenge is False

    broker = captcha_delegate.WidgetBroker(
        page=page, widget=frames.nth(candidates[0]["index"]), provider=observed["provider"],
        session=session_id, owner=entry.get("owner", ""), resource_lock=entry["lock"],
        resource_loan=scope, binding_valid=binding_valid, site_accepted=site_clear)
    try:
        _audit_captcha_resolution(entry, session_id, flow, "visual", "started", "delegate_started", allocation=allocation)
        result = await captcha_delegate.solve_widget(broker=broker, budget=allocation, visual=_captcha_visual)
        attempt.update(status=result.status, reason=result.reason)
        _audit_captcha_resolution(
            entry, session_id, flow, "visual",
            "accepted" if result.status == "accepted" else "blocked" if result.status == "blocked" else "unresolved",
            result.reason, allocation=allocation)
        if result.status == "blocked" or result.reason == "stale_authority":
            raise _LoginCaptchaBlocked("captcha_cleanup_pending" if result.reason == "drain_incomplete" else "captcha_resource_busy")
        return result.status == "accepted"
    except asyncio.CancelledError:
        _audit_captcha_resolution(entry, session_id, flow, "visual", "cancelled", "cancelled", allocation=allocation)
        raise
    except _LoginCaptchaBlocked:
        raise
    except Exception:
        _audit_captcha_resolution(entry, session_id, flow, "visual", "failed", "delegate_error", allocation=allocation)
        raise
    finally:
        # Preserve reservations even if cancellation or an exception interrupts
        # the result: another invocation must not dispatch after visual input.
        attempt.update(model_calls=allocation.model_calls, effects=allocation.effects)
        search["actions"] = int(search.get("actions", 0)) + allocation.effects
        try:
            await broker.close()
        except DrainIncomplete:
            # The scope retains the callback and original lock through cleanup.
            _audit_captcha_resolution(entry, session_id, flow, "visual", "blocked", "drain_incomplete", allocation=allocation)
            raise _LoginCaptchaBlocked("captcha_cleanup_pending") from None


async def _resolve_login_captcha_local(entry: dict, session_id: str, flow: dict, page,
                                     *, deadline: float, binding_valid) -> bool:
    """One local attempt within the existing login's permission and budgets."""
    # Text without a unique supported widget has no solver target. Leave the
    # attempt and action budgets untouched for manual handling.
    textual = await credential_injection._manual_only_login_challenge(page)
    if not binding_valid():
        _audit_captcha_resolution(entry, session_id, flow, "local", "blocked", "binding_changed")
        raise _LoginCaptchaBlocked("captcha_resource_busy")
    if textual:
        _audit_captcha_resolution(entry, session_id, flow, "local", "skipped", "textual_challenge")
        return False
    attempt = flow.setdefault("captcha_state", {})
    search = flow.setdefault("entry_search", {})
    if (attempt.get("attempted") or page is not entry.get("page")
            or int(search.get("actions", 0)) >= login_navigation.MAX_ACTIONS):
        _audit_captcha_resolution(entry, session_id, flow, "local", "skipped",
                                  "attempt_already_used" if attempt.get("attempted") else
                                  "binding_changed" if page is not entry.get("page") else "action_budget")
        return False
    remaining = max(0.0, deadline - _monotonic())
    if remaining <= 0:
        _audit_captcha_resolution(entry, session_id, flow, "local", "skipped", "time_budget")
        return False
    try:
        solved = await captcha_solver.solve_once(
            page=page, state=attempt, allowed_hosts=set(entry.get("allowlist") or ()),
            pending_script=credential_injection._DETECT_CAPTCHA_JS,
            owner=entry.get("owner", ""), session_id=session_id,
            domain=entry.get("domain", ""), timeout_s=remaining)
        _audit_captcha_resolution(entry, session_id, flow, "local", "accepted" if solved else "unresolved",
                                  "local_accepted" if solved else "local_unresolved")
        return solved
    except asyncio.CancelledError:
        _audit_captcha_resolution(entry, session_id, flow, "local", "cancelled", "cancelled")
        raise
    except Exception:
        _audit_captcha_resolution(entry, session_id, flow, "local", "failed", "delegate_error")
        raise
    finally:
        if attempt.get("status") not in {"unsupported", "unavailable", "origin_unverified"}:
            search["actions"] = int(search.get("actions", 0)) + 1


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
        tried = state.get("tried", {}).get(
            login_navigation.state_key(page.url, candidates), ())
        pending = state.get("pending") or {}
        available = login_navigation.untried_candidates(
            candidates, tried, pending_key=pending.get("key"))
        if (state.get("pending")
                and not action_resolver.choose_candidate(
                    "login", available, "click").get("ok")
                and not action_resolver.choose_reveal_candidate(
                    "login", available, "click").get("ok")):
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

    async def decide(observation: dict, tried: set[str],
                     candidates: list[dict], chosen: dict) -> tuple:
        if chosen.get("ok") and not chosen.get("ambiguous"):
            return ({"candidate": chosen["candidate"],
                     "confidence": chosen.get("confidence", 1.0)}, "lexicon", 0)
        # Reuse the multilingual resolver and DOM relations. A sole menu is
        # also useful on pages that expose no login words yet.
        reveal = action_resolver.choose_reveal_candidate(
            "login", login_navigation.untried_candidates(
                observation["all_candidates"], tried), "click")
        reveals = [candidate for candidate in candidates
                   if action_resolver.is_reveal_control(candidate)
                   and str(candidate.get("aria_expanded") or "").lower() != "true"]
        candidate = reveal.get("candidate") if reveal.get("ok") else None
        mechanism = "reveal"
        if candidate is None and len(reveals) == 1:
            candidate, mechanism = reveals[0], "sole_reveal"
        if candidate is not None and candidate in candidates:
            return {"candidate": candidate, "confidence": 0.5}, mechanism, 0
        hint = action_resolver.choose_login_area_hint(
            candidates, observation["url"])
        if hint.get("ok"):
            return ({"candidate": hint["candidate"],
                     "confidence": hint["confidence"]}, "area_hint", 0)
        candidate = await _local_llm_choose_goal_candidate(
            entry, "login", candidates, [], set(), login=True)
        if candidate is not None:
            return ({"candidate": candidate, "confidence": 0.5,
                     "model_selected": True}, "model", len(candidates))
        return None, "none", len(candidates)

    async def choose(observation: dict, tried: set[str]) -> dict | None:
        candidates = login_navigation.untried_candidates(observation["candidates"], tried)
        chosen = action_resolver.choose_candidate("login", candidates, "click")
        decision, mechanism, offered = await decide(
            observation, tried, candidates, chosen)
        # Diagnostics only: a failure to describe the decision never changes it.
        try:
            fields = _login_choice_audit(
                observation, tried, mechanism=mechanism, lexicon=chosen,
                chosen=(decision or {}).get("candidate"), model_offered=offered)
        except Exception as exc:  # noqa: BLE001
            fields = {"mechanism": mechanism, "diagnostic_failed": type(exc).__name__}
        sites_audit.record(
            "login_entry_choice", owner=entry.get("owner", ""),
            session_id=session_id, domain=entry.get("domain", ""),
            depth=len(state.get("frames") or ()) - 1,
            actions=int(state.get("actions", 0)), **fields)
        return decision

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
            state, observe=observe, choose=choose, execute=execute, restore=restore,
            restart_on_replay_change=True),
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


def _distrust_restored_session(entry: dict, result: dict) -> None:
    """A restored session that led the collection to no page of items is not
    used again: the next login is a full one (real runs fw10/fw11 21/10/2026:
    the saved Fastweb state passed every check of the restore, yet its
    private page was another, smaller one and the collection ended empty;
    the logins of the same day had reached the invoices)."""
    if (not entry.get("session_restored") or result.get("ok")
            or entry.get("collected_pages")):
        return
    binding = entry.get("saved_session_binding")
    if not binding:
        return
    try:
        import sites_saved_sessions
        sites_saved_sessions.remove(_session_owner_key(entry), binding)
        removed = True
    except Exception:  # noqa: BLE001 -- the next restore is checked again anyway
        removed = False
    entry["session_restored"] = False
    # Not saved again when the session closes.
    entry["session_distrusted"] = True
    sites_audit.record("login_session_distrusted", owner=entry.get("owner", ""),
                       session_id=entry.get("_sid", ""), domain=binding, outcome=removed,
                       reason="collection_found_nothing")


def _collection_metadata(entry: dict, state: dict, result: dict) -> list[dict]:
    """Declare collection completeness independently of presentation limits.

    The scope names this traversal, never an invoice or an extraction group.
    Only broker-owned state enters this envelope; page text cannot declare it.
    """
    from tabular_records import validate_source_metadata
    item = {"producer": "act_sites", "scope": state["source_scope"],
            "partial": bool(not result.get("ok") or result.get("approval_required")
                            or result.get("collection_partial") or result.get("truncated")),
            "collection_partial": bool(result.get("collection_partial")),
            "truncated": bool(result.get("truncated"))}
    for key in ("truncated_what", "used", "available_total", "cap_field", "cap_value"):
        if key in result:
            item[key] = result[key]
    if result.get("error_class"):
        item["reason_code"] = result["error_class"]
    observed = next((p for p in entry.get("collected_pages", [])
                     if isinstance(p, dict) and p.get("url")), None)
    if observed:
        item["provenance"] = {"url": observed["url"]}
    return validate_source_metadata([item])


def _collection_get_restore_link(candidate, current_url):
    """Eligibility only: native document request and fresh restore gates prove more."""
    if (candidate.get("tag") != "a" or candidate.get("download")
            or candidate.get("secret_input") or candidate.get("form_action")
            or str(candidate.get("form_method") or "GET").upper() != "GET"):
        return ""
    try:
        url = urllib.parse.urljoin(current_url, str(candidate.get("href") or ""))
        parsed = urllib.parse.urlsplit(url)
        if (not candidate.get("href") or parsed.scheme not in {"http", "https"}
                or not parsed.hostname or parsed.fragment or parsed.username or parsed.password):
            return ""
        return url
    except ValueError:
        return ""


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
                 "found": False, "pages": set(), "source_scope": secrets.token_hex(16)}
        entry["collection_search"] = state
        entry.pop("collected_pages", None)
        entry.pop("collection_record_views", None)
        entry.pop("collection_empty_details", None)
        entry.pop("collection_observations", None)
        entry.pop("collection_source_metadata", None)
        entry.pop("_source_scope_label", None)

    # An approval resume created by the previous runtime keeps its traversal
    # and remaining budget; only its missing transport scope is initialized.
    state.setdefault("source_scope", secrets.token_hex(16))
    record_contexts = {}
    navigation_anchor = None

    class SceneChanged(ValueError):
        """A semantic answer has no effect authority after its scene changed."""

    def scene_key(snapshot, candidates):
        return collection_observation.scene_key(url=snapshot.get("url"),
            text=snapshot.get("text", ""),
            candidates=[{**collection_context.describe(c),
                         "identity": login_navigation.candidate_key(c)} for c in candidates],
            context=collection_context.fingerprint(collection_context.selectors(candidates)),
            fence=(id(entry["page"]), bool(entry.get("authenticated")),
                   str(entry.get("goal_scope") or "")))

    def spend(phase, started):
        """Time each observation phase over the whole collection (speed
        review 21/10/2026: ENI spent 261 s between settles, cause unmeasured)."""
        spent = state.setdefault("phase_ms", {})
        spent[phase] = spent.get(phase, 0) + round((_monotonic() - started) * 1000)
        spent[phase + "_count"] = spent.get(phase + "_count", 0) + 1

    enumerated = []

    async def enumerate_controls(page):
        started = _monotonic()
        try:
            # The last whole enumeration: saved context controls are found
            # there by identity, whatever subset a caller holds.
            enumerated[:] = [await _enumerate_candidates(page, record_contexts=record_contexts)]
            return enumerated[0]
        finally:
            spend("enumerate", started)

    async def read_text():
        started = _monotonic()
        try:
            return await _continuation_snapshot(entry)
        finally:
            spend("snapshot", started)

    async def verify_scene(snapshot, candidates, expected):
        raw = await read_text()
        fresh = await read_controls(entry["page"])
        if scene_key(raw, fresh) != expected:
            sites_audit.record("collection_decision_reobserved", session_id=session_id,
                               phase="collection_snapshot_fence", reason="scene_changed")
            raise SceneChanged("collection_snapshot_fence")

    async def read_controls(page, observed=None):
        candidates = action_resolver.goal_navigation_candidates(
            observed if observed is not None else await enumerate_controls(page),
            include_offscreen=True, include_covered=True)
        return _collapse_collection_links([c for c in candidates
            if str(c.get("form_method") or "").upper() != "POST"
            and action_resolver.goal_candidate_is_admissible(
                target, c, scope=str(entry.get("goal_scope") or ""))])

    def page_context(page_ctx: str) -> str:
        """A page is read inside the active record partition AND its own
        controls: neither may hide the other (real run R32: three contracts,
        one context, because a fixed filter on the page won)."""
        active = state.get("partition_active_key", "")
        if not active or not page_ctx or page_ctx == active:
            return page_ctx or active
        return hashlib.sha256(f"{active}\0{page_ctx}".encode()).hexdigest()

    def still_shown(controls):
        """Saved context controls found again, by identity, in the last whole
        enumeration and showing the same values; None otherwise."""
        rebound = collection_context.rebind_controls(controls, action_resolver.goal_navigation_candidates(
            enumerated[0] if enumerated else [], include_offscreen=True, include_covered=True))
        return (rebound if rebound is not None
                and collection_context.value_key(rebound) == collection_context.value_key(controls) else None)

    def seen_again(text: str):
        """The saved frame this observation reads again: the same address,
        record partition and text, with its context controls showing the
        saved values. A context the answer names now and did not name then,
        already matching, leaves that page what it was (real run am1, ENI
        23/10/2026: the year filter, already set, named on the fifth return
        to a list only; the page's identity changed and the replay ended
        before the third contract). None otherwise."""
        frames = state.get("frames", [])
        replay_at = (state.get("replay") or {}).get("index", len(frames) - 1)
        saved = frames[replay_at]["observation"] if 0 <= replay_at < len(frames) else None
        if (saved is None or saved.get("url") != entry["page"].url
                or saved.get("partition_at") != state.get("partition_active_key", "")
                or saved.get("content_key") != hashlib.sha256(text.encode()).hexdigest()
                or not isinstance(saved.get("context_value"), str)
                or not isinstance(saved.get("cutoff_context"), str)):
            return None
        return saved if still_shown(saved.get("effective_context_controls") or []) is not None else None

    def covered_controls(observed):
        return [c for c in observed
                if c.get("rendered") is True and c.get("visible") is True
                and c.get("in_viewport") is True and c.get("topmost") is False
                and action_resolver.goal_navigation_candidates(
                    [{**c, "topmost": True}])
                and action_resolver.goal_candidate_is_admissible(
                    target, c, scope=str(entry.get("goal_scope") or ""))]

    def remember_partition(snapshot):
        # Kept beside the page, never inside it: the page signature dedups
        # equal captures (review 20/10: a key inside split the anchor page).
        entry.setdefault("collected_partitions", {})[snapshot["key"]] = state.get(
            "partition_active_key", "")

    def preserve_snapshot(snapshot, status, *, parent=None, actor=None):
        observed_collection = status in {"matching_items", "unmatched_items"}
        if observed_collection and state.get("partition_transition"):
            # A page seen while switching partition is read again afterwards,
            # inside the partition it belongs to.
            observed_collection = False
            state["found"] = True
        if observed_collection:
            state["found"] = True
            if collection_evidence.retain(entry, snapshot):
                remember_partition(snapshot)
                if parent:
                    collection_evidence.add_view(entry, parent, snapshot["key"])
        sites_audit.record("collection_observation", owner=entry.get("owner", ""),
            session_id=session_id, domain=entry.get("domain", ""),
            collection_observed=observed_collection,
            proximity=collection_observation.STATUSES.get(status, "none"),
            phase="before_context_effect", decision_actor=actor or {"actor_type":"unknown"},
            steps=int(state.get("actions", 0)))

    def list_memo_key(scene):
        return hashlib.sha256(f"{state.get('partition_active_key', '')}\0{scene}".encode()).hexdigest()

    def list_scene_key(snapshot, candidates):
        # The same list seen again: same text and the same controls in any
        # order. Enumeration lists the visible controls first and numbers them
        # in that order, so a return that restores the scroll position
        # reorders and renumbers them (real run bn1, ENI 23/10: five of
        # fourteen returns asked the model again for a list already decided).
        return collection_observation.scene_key(url=snapshot.get("url"),
            text=snapshot.get("text", ""), candidates=_control_set(candidates), context="",
            fence=(id(entry["page"]), bool(entry.get("authenticated")),
                   str(entry.get("goal_scope") or "")))

    def opened_detail(page):
        # A record's own page, opened from its row at another address: the
        # leaf of `_reached_by_detail`, before its frame exists.
        frames = state.get("frames") or []
        return bool(state.get("pending_record_view") and frames
                    and frames[-1]["observation"].get("url") != page.url)

    def page_type(page, candidates):
        # Pages of one kind: same address without its parameters, same
        # record partition, the same controls in any order; their text and
        # the destinations of their links may differ (the details of a
        # list's rows; real run bq1, ENI 23/10: each detail links to its own
        # invoice, so no two details were of one kind).
        parts = urllib.parse.urlsplit(page.url)
        return hashlib.sha256(json.dumps([
            urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
            state.get("partition_active_key", ""),
            sorted(json.dumps({k: v for k, v in collection_context.describe(c).items() if k != "id"},
                              sort_keys=True) for c in candidates),
            bool(entry.get("authenticated")), str(entry.get("goal_scope") or "")]).encode()).hexdigest()

    def remember_list(scene, decision, snapshot, candidates):
        # A list classified as leading to its rows' details. Returning to the
        # same list (same text, controls and partition) needs no new decision
        # while rows of that control kind remain unopened (performance review
        # 21/10/2026: one model call per return, about 2 s each).
        chosen = decision.get("candidate") or {}
        if (decision.get("error_class") or chosen.get("collection_purpose") != "detail"
                or not chosen.get("_record_context_key") or decision["context"].get("candidate")):
            return
        memo = state.setdefault("list_decisions", {})
        key = list_memo_key(scene)
        memo.pop(key, None)
        memo[key] = {"decision": copy.deepcopy(decision), "identities": _identities(candidates),
                     "context": snapshot.get("context"), "key": snapshot.get("key")}
        while len(memo) > 16:
            memo.pop(next(iter(memo)))

    def reuse_list(scene, scene_candidates, candidates, snapshot):
        memo = state.get("list_decisions", {}).get(list_memo_key(scene))
        if memo is None:
            return None
        decision = _rebind_decision(memo["decision"], memo["identities"], scene_candidates)
        if decision is None:
            return None
        snapshot = {**snapshot, "context": memo["context"], "key": memo["key"]}
        siblings = _pending_detail_siblings(candidates, snapshot,
            {_detail_kind(memo["decision"]["candidate"])}, entry, target,
            reopen=lambda key: reopen_in_place({"parent": snapshot["key"], "record": key}))
        if not siblings:
            return None
        actor = {"actor_type": "runtime", "rule": "list_unchanged"}
        decision["candidate"] = {**siblings[0], "collection_observed": True,
            "proximity": decision["candidate"].get("proximity", "matching"),
            "collection_purpose": "detail", "collection_continuation": False,
            "_decision_actor": actor}
        decision["_decision_actor"] = actor
        preserve_snapshot(snapshot, decision["content_status"], actor=actor)
        sites_audit.record("collection_list_reused", session_id=session_id,
                           pending=len(siblings), steps=int(state.get("actions", 0)))
        return decision, snapshot

    def remember_detail(kind, decision, candidates):
        # The model's reading of the first detail of a kind, kept when it
        # found the requested records there and asked for nothing more.
        if (decision.get("error_class") or decision.get("content_status") != "matching_items"
                or decision["context"].get("candidate")):
            return
        memo = state.setdefault("detail_decisions", {})
        memo.setdefault(kind, {"decision": copy.deepcopy(decision),
                               "identities": _identities(candidates)})
        while len(memo) > 16:
            memo.pop(next(iter(memo)))

    def reuse_detail(kind, candidates, snapshot):
        # Another detail of the same kind, opened from a row of the requested
        # year and showing that row's date, takes that reading: the model
        # labelled the first, the code decides the next ones (real run bn1,
        # ENI 23/10: twelve details of two kinds, each read by the model with
        # the same answer). A row without a date or of another year, or a
        # page not showing its row's date (empty, another record), is read
        # by the model.
        memo = state.get("detail_decisions", {}).get(kind)
        if memo is None:
            return None
        year = collection_cutoff.target_year(target)
        native = record_contexts.get((state.get("pending_record_view") or {}).get("record"))
        row = native.get("text") if isinstance(native, dict) else native
        months = collection_termination.observed_month_names()
        dated = collection_termination.row_date(row, months) if isinstance(row, str) else None
        decision = None
        reason = ("no_year" if year is None else "row_unread" if not isinstance(row, str)
                  else "row_undated" if dated is None else "row_other_year" if dated[0] != year
                  else None)
        if reason is None and not any(collection_termination.row_date(line, months) == dated
                                      for line in str(snapshot.get("text") or "").splitlines()):
            reason = "date_not_shown"
        if reason is None:
            decision = _rebind_decision(memo["decision"], memo["identities"], candidates)
            reason = "rebind" if decision is None else None
        if reason is not None:
            # Why the model reads it: closed reasons only.
            sites_audit.record("collection_detail_read", session_id=session_id, reason=reason,
                               steps=int(state.get("actions", 0)))
            return None
        actor = {"actor_type": "runtime", "rule": "detail_kind"}
        # A leaf: its reading matters, not where to go from it.
        decision["candidate"] = {"collection_observed": True, "proximity": "matching",
                                 "collection_purpose": "done", "collection_continuation": False,
                                 "_decision_actor": actor}
        decision["_decision_actor"] = actor
        ctx = page_context(_context_key([], controls=decision["context_controls"]))
        snapshot = {**snapshot, "context": ctx, "key": _page_identity(
            snapshot.get("url") or entry["page"].url, ctx, snapshot["text"])}
        keep_snapshot(snapshot, decision["content_status"], actor)
        sites_audit.record("collection_detail_reused", session_id=session_id,
                           steps=int(state.get("actions", 0)))
        return decision, snapshot

    def keep_snapshot(snapshot, status, actor):
        retained = entry.setdefault("collection_observations", [])
        sites_collection_sources.retain_snapshot(retained, snapshot)
        del retained[:-16]
        preserve_snapshot(snapshot, status, actor=actor)

    async def inspect_scene(candidates, controls, choices, snapshot, *, parent=None):
        # A frame/pending parent may still reference the input. Classifying a
        # later window must not mutate an already retained page or edge key.
        snapshot = copy.deepcopy(snapshot)
        # Bound each model window, not the navigation tree. Context controls
        # accompany every window; NONE is accepted only after all windows.
        context_ids = {c["id"] for c in controls + choices}
        routes = [c for c in candidates if c["id"] not in context_ids]
        controls = list(controls)
        classified = {}
        last, first_route, observed_status = None, None, "no_items"

        def finish(decision):
            # Finalize once before any context effect. A provisional key must
            # never enter retention and then change to an existing final key.
            if decision.get("error_class"):
                snapshot["unverified_context"] = snapshot["context"]
                snapshot.update(context=None, context_observed=False,
                    key=_page_identity(snapshot.get("url") or entry["page"].url,
                                       "", snapshot["text"]))
            keep_snapshot(snapshot, observed_status, decision.get("_decision_actor"))
            decision["_snapshot"] = snapshot
            return decision
        # One window holds a whole page of ordinary size (real run R43: pages
        # of about a hundred controls cost two requests each): every route
        # the per-request bound leaves after the context controls and their
        # options; a window whose request exceeds the prompt bound is halved.
        size = max(collection_observation.MAX_CONTROLS,
                   2 * collection_observation.MAX_CONTROLS + collection_observation.MAX_CONTEXTS
                   - len(controls) - len(choices))
        offset = 0
        while offset < max(1, len(routes)):
            context_ids = {c["id"] for c in controls + choices}
            window = controls + choices + [c for c in
                routes[offset:offset + size]
                if c["id"] not in context_ids]
            try:
                decision = await _frontier_collection_observation(entry, action,
                    window, controls, choices, snapshot, previous_scope=state.get("record_scope"),
                    scene_candidates=candidates)
            except BaseException:
                # Cancellation still leaves bounded raw evidence, with no
                # certified context or effect authority; propagate unchanged.
                finish({"error_class": "collection_observation_unresolved"})
                raise
            if decision.get("reason") == "prompt_observation_limit" and size > _MIN_COLLECTION_WINDOW:
                size //= 2
                continue
            offset += size
            if not decision.get("error_class"):
                # Promoted controls accompany later windows of this SAME
                # scene. They never survive a fresh enumeration by ID alone.
                for check in decision["contexts"]:
                    normalized = {**check, "matching_options": sorted(check["matching_options"])}
                    if check["id"] in classified and classified[check["id"]] != normalized:
                        decision = {"error_class": "selector_missing",
                            "phase": "context_grounding", "reason": "changed_context_classification",
                            "content_status": decision["content_status"],
                            "_decision_actor": decision.get("_decision_actor", {})}
                        break
                    classified[check["id"]] = normalized
                if not decision.get("error_class"):
                    controls = decision["context_controls"]
                    ctx = page_context(_context_key([], controls=controls))
                    saved = seen_again(snapshot["text"]) if decision["context"].get("ready") else None
                    if saved is not None and saved["cutoff_context"] != ctx:
                        ctx = saved["cutoff_context"]
                        sites_audit.record("collection_context_identity_kept", session_id=session_id,
                                           phase="snapshot", steps=int(state.get("actions", 0)))
                    snapshot.update(context=ctx, key=_page_identity(
                        snapshot.get("url") or entry["page"].url, ctx, snapshot["text"]))
            status = decision.get("content_status", "no_items")
            if status == "matching_items" or (status == "unmatched_items" and observed_status == "no_items"):
                observed_status = status
            if len(snapshot.get("text", "")) > 12000:
                state.setdefault("coverage_reasons", set()).add("semantic_text_window_unverified")
            if decision.get("error_class"):
                state["rejection"] = {"phase": decision.get("phase", "collection_observe"),
                    "reason": decision.get("reason", "invalid_response"),
                    "decision_actor": decision.get("_decision_actor", {"actor_type":"unknown"})}
                return finish(decision)
            state["record_scope"] = decision["scope"]
            # Unknown context roles or unvisited record partitions cannot
            # attest TUTTE from the current selection alone.
            if any(c["kind"] == "unknown" for c in decision["contexts"]):
                state.setdefault("coverage_reasons", set()).add("context_role_unverified")
            if any(c["requested"] is None and c["kind"] != "partition"
                   and c["id"] not in {hint["id"] for hint in collection_context.selectors(window)}
                   for c in decision["contexts"]):
                # An unrequested plain control cannot silently disappear from
                # the search and make an arbitrary archive look exhausted.
                state.setdefault("coverage_reasons", set()).add("semantic_context_unverified")
            if any(p["requested"] is None for p in decision["partitions"]):
                state.setdefault("coverage_reasons", set()).add("record_partitions_unverified")
            if decision["scope"]["kind"] == "all" and any(
                    p["requested"] is None and any(
                        option["id"] not in {o["id"] for o in p["options"]}
                        and collection_observation.option_owners(controls, option) == [p["id"]]
                        for option in choices) for p in decision["partitions"]):
                state.setdefault("coverage_reasons", set()).add("partition_options_unclassified")
            last = decision
            # A later window may contain a non-ARIA account/filter control.
            # Classify the whole observed scene before any context or route
            # effect; keep the original first route's priority and actor.
            if first_route is None and decision["candidate"].get("id"):
                first_route = decision["candidate"]
        if first_route is not None:
            last["candidate"] = first_route
        if observed_status != "no_items":
            last["content_status"] = observed_status
            last["candidate"].update(collection_observed=True,
                proximity=collection_observation.STATUSES[observed_status])
        return finish(last)

    async def page_guards(*, verifying_get=False):
        """Current-page gates shared by every observation, before any read."""
        nonlocal navigation_anchor
        # Consume before any gate: a challenge, unstable page or blocked
        # context must never retain evidence for a later resumed observation.
        anchor, navigation_anchor = navigation_anchor, None
        blocked = (await _navigation_challenge(entry, navigation_anchor=anchor)
                   if anchor is not None else await _navigation_challenge(entry))
        if blocked:
            return {"terminal": blocked}
        privacy = None if verifying_get else await _dismiss_privacy_obstruction(entry)
        if isinstance(privacy, cookie_privacy.CookieOutcome) and privacy.status == "blocked":
            return {"terminal": {"ok": False,
                    "error_class": "cookie_precondition_unresolved"}}
        if not verifying_get:
            await _dismiss_obstructing_overlay(entry)
        if not await _wait_for_content_settle(entry):
            return {"terminal": {"ok": False, "error_class": "target_unstable"}}
        page = entry["page"]
        if _host_of_url(page.url) not in set(entry.get("allowlist") or ()):
            return {"terminal": {"ok": False, "error_class": "mandate_scope_exceeded"}}
        blocked = await _navigation_challenge(entry)
        if blocked:
            return {"terminal": blocked}
        state["pages"].add(page)
        return None

    async def guard_before_restore():
        # A return is already decided: keep every gate, skip scrolling,
        # enumeration and semantic decisions on the page being left.
        state.pop("cutoff_continuation", None)
        guarded = await page_guards()
        sites_audit.record("collection_restore_guarded", session_id=session_id,
                           terminal=guarded is not None,
                           steps=int(state.get("actions", 0)))
        return None if guarded is None else guarded["terminal"]

    async def observe_once():
        carried = state.pop("cutoff_continuation", None)
        verifying_get = bool((state.get("replay") or {}).get("verify_get")
                             and (state.get("replay") or {}).get("index", -1) >= 0)
        guarded = await page_guards(verifying_get=verifying_get)
        if guarded is not None:
            return guarded
        page = entry["page"]
        observed, passing = None, False
        replay_index = (state.get("replay") or {}).get("index", -1)
        replay_frames = state.get("frames", [])
        if 0 <= replay_index < len(replay_frames) - 1:
            # A replay passes through pages already read at their first visit:
            # scroll only to reach a saved control or to read a page to decide.
            observed = await enumerate_controls(page)
            saved = replay_frames[replay_index + 1]["via"]["key"]
            passing = any(login_navigation.candidate_key(c) == saved
                          for c in await read_controls(page, observed))
        if verifying_get:
            observed = await enumerate_controls(page)
        if not passing and not verifying_get:
            remaining = max(0, login_navigation.COLLECTION_MAX_ACTIONS - int(state.get("actions", 0)))
            # One whole wait at the end of a list per address of this search:
            # returns to a page already confirmed keep the short quiet only.
            confirmed = state.setdefault("scroll_confirmed", set())
            scrolling = {"collection": True,
                         "collection_scroll_confirmed": page.url in confirmed}
            # A page showing, before scrolling, what it showed at an earlier
            # observation (same address and partition) whose scrolling revealed
            # nothing new is not scrolled again (real run bm5, ENI 23/10: 46
            # of 52 observations scrolled, 112 s, mostly pages seen again).
            unrevealed = state.setdefault("scroll_unrevealed", set())
            before_scroll = (page.url, state.get("partition_active_key", ""),
                             await _goal_content_signature(entry))
            # A new page is always scrolled, a detail of a known kind too: its
            # waits also let late content arrive (real run bp1, ENI 23/10: a
            # second-tab detail not scrolled was read twice as an empty shell
            # and its invoice was lost).
            started = _monotonic()
            if before_scroll[2] and before_scroll in unrevealed:
                sites_audit.record("collection_scroll_skipped", session_id=session_id,
                                   steps=int(state.get("actions", 0)))
            else:
                revealed = await _expand_collection_by_scrolling(
                    entry, scrolling, max_scrolls=min(_MAX_COLLECTION_SCROLLS, remaining))
                if (before_scroll[2] and not revealed and not scrolling.get("collection_scroll_limited")
                        and not scrolling.get("challenge")):
                    unrevealed.add(before_scroll)
            spend("scroll", started)
            if scrolling.get("collection_scroll_confirmed"):
                confirmed.add(page.url)
            scrolls = scrolling.get("collection_scrolls", 0)
            state["actions"] = int(state.get("actions", 0)) + scrolls
            blocked = scrolling.get("challenge") or await _navigation_challenge(entry)
            if blocked:
                return {"terminal": blocked}
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
                observed = await enumerate_controls(page)
        if not verifying_get and covered_controls(observed):
            # A modal may appear during scrolling. Try its existing safe
            # exit, but keep covered routes for semantic selection: a fixed
            # header over an unrelated footer link is not a page-wide veto.
            location = page.url
            await _dismiss_obstructing_overlay(entry, settle=True)
            if entry["page"] is not page or page.url != location:
                state["rejection"] = {"phase": "overlay_changed_page",
                                      "same_page": entry["page"] is page}
                return {"terminal": {"ok": False, "error_class": "target_changed"}}
            observed = await enumerate_controls(page)
        context_controls = await read_controls(page, observed)
        semantic, semantic_snapshot, verified_contexts = None, None, None

        async def inspect_context(controls, choices, candidates):
            nonlocal semantic, semantic_snapshot
            raw = await read_text()
            ctx = page_context(_context_key(observed))
            semantic_snapshot = {**raw, "context": ctx, "context_observed": True,
                "records": {c["_record_context_key"]: record_contexts[c["_record_context_key"]]
                    for c in candidates if c.get("_record_context_key") in record_contexts},
                "key": _page_identity(raw.get("url") or entry["page"].url, ctx, raw["text"])}
            if raw.get("truncated"):
                state["observation_limit"] = ("max_text_chars", 100000)
            expected_scene = scene_key(raw, candidates)
            listed = list_scene_key(raw, candidates)
            kind = page_type(entry["page"], candidates) if opened_detail(entry["page"]) else None
            reused = reuse_list(listed, candidates, candidates, semantic_snapshot) or (
                reuse_detail(kind, candidates, semantic_snapshot) if kind else None)
            if reused is not None:
                semantic, semantic_snapshot = reused
            else:
                semantic = await inspect_scene(candidates, controls, choices, semantic_snapshot,
                                               parent=state.get("pending_record_view"))
                semantic_snapshot = semantic.pop("_snapshot")
                if semantic.get("error_class"):
                    return semantic
                await verify_scene(raw, candidates, expected_scene)
                remember_list(listed, semantic, semantic_snapshot, candidates)
                if kind:
                    remember_detail(kind, semantic, candidates)
            semantic["scene_key"] = expected_scene
            if semantic.get("content_status") in {"matching_items", "unmatched_items"}:
                parent = state.get("pending_record_view")
                if parent and semantic_snapshot["text"]:
                    collection_evidence.add_view(entry, parent, semantic_snapshot["key"])
            return {**semantic["context"], "controls": semantic["context_controls"]}

        async def read_context():
            nonlocal context_controls, observed
            observed = await enumerate_controls(entry["page"])
            context_controls = await read_controls(entry["page"], observed)
            return context_controls

        light_contexts = None
        context_effects = []
        if passing and not verifying_get:
            # A page only passed through on a replay: the saved click is fixed.
            # With the same context controls showing the same values, the
            # model has nothing to decide; any difference takes the full path.
            saved = replay_frames[replay_index]["observation"].get("effective_context_controls") or []
            rebound = collection_context.rebind_controls(saved, context_controls)
            if (rebound is not None and collection_context.value_key(rebound)
                    == collection_context.value_key(saved)):
                light_contexts = rebound
        if light_contexts is not None:
            context = collection_context.adopt_verified(
                state.setdefault("context", {}), context_controls, light_contexts)
            if context is None:
                light_contexts = None
        if (state.get("replay") or {}).get("verify_get"):
            # Read-only replay verification has no semantic/effect authority.
            saved = replay_frames[replay_index]["observation"].get("effective_context_controls", [])
            verified_contexts = collection_context.rebind_controls(saved, context_controls)
            if verified_contexts is None:
                return {"terminal": {"ok": False, "error_class": "target_changed"},
                        "rejection": {"phase": "replay_context", "reason": "context_control_changed"}}
            # The context state describes this page, so the comparison with
            # the saved page is meaningful; values that differ fail it.
            context = (collection_context.adopt_verified(
                state.setdefault("context", {}), context_controls, verified_contexts)
                or {"candidates": collection_context.navigation_candidates(
                    context_controls, controls=verified_contexts)})
        elif light_contexts is not None:
            verified_contexts = light_contexts
            sites_audit.record("collection_replay_light", session_id=session_id, index=replay_index,
                               steps=int(state.get("actions", 0)))
        else:
            initial = await inspect_context(collection_context.selectors(context_controls),
                collection_context.options(context_controls, collection_context.selectors(context_controls),
                    state.get("context", {}).get("opened")), context_controls)
            if initial.get("error_class"):
                context = {"terminal": {"ok": False, **initial}, "rejection": state["rejection"]}
            else:
                initial_pending = True
                async def decide(controls, choices, candidates):
                    nonlocal initial_pending
                    if initial_pending:
                        initial_pending = False
                        return initial
                    return await inspect_context(controls, choices, candidates)
                async def execute_context(decision):
                    # A context effect of this observation: the page it shows
                    # is the effect's, not the one saved before.
                    context_effects.append(decision)
                    return await execute(decision)
                async def restore_context():
                    # Esc closes what a context click opened (a calendar, a
                    # menu, a dialog), then the page is read afresh.
                    try:
                        await entry["page"].keyboard.press("Escape")
                    except Exception:  # noqa: BLE001 -- the fresh read decides
                        pass
                    await _wait_for_content_settle(entry)
                    sites_audit.record("collection_context_restored", session_id=session_id,
                                       steps=int(state.get("actions", 0)))
                    return await read_context()
                context = await collection_context.ensure(
                    state.setdefault("context", {}), target, context_controls,
                    read=read_context, execute=execute_context,
                    settle=lambda: _wait_for_content_settle(entry), budget=state,
                    enabled=_MODEL_FALLBACKS_ENABLED,
                    timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000, decide=decide,
                    restore=restore_context)
        if context.get("terminal") is not None:
            if context.get("rejection"):
                sites_audit.record("collection_context_rejected", session_id=session_id,
                                   steps=int(state.get("actions", 0)),
                                   **context["rejection"])
            return context
        if context.get("unverified"):
            # The context attempt ended unverified; the collection goes on and
            # cannot attest coverage of that context.
            sites_audit.record("collection_context_unverified", session_id=session_id,
                               steps=int(state.get("actions", 0)), phase=context["unverified"])
            state.setdefault("coverage_reasons", set()).add("context_effect_unverified")
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
                    fresh_contexts = collection_context.rebind_controls(
                        ((semantic or {}).get("context_controls") or verified_contexts or []), candidates)
                    if fresh_contexts is None:
                        return {"terminal": {"ok": False, "error_class": "target_changed"},
                                "rejection": {"phase": "replay_context", "reason": "context_control_changed"}}
                    candidates = collection_context.navigation_candidates(candidates, controls=fresh_contexts)
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
        sites_audit.record("collection_controls", session_id=session_id,
                           observed_count=len(observed), navigation_count=len(candidates),
                           steps=int(state.get("actions", 0)),
                           pending_document=credential_injection.main_document_pending(page),
                           pending_requests=len(getattr(page, "_metnos_inflight", ()) or ()))
        snapshot = semantic_snapshot or await read_text()
        # Context selection may have changed the controls and page since the
        # initial enumeration. Key the captured text with its observed context.
        effective_contexts = ((semantic or {}).get("context_controls")
                              if not verifying_get and light_contexts is None else verified_contexts)
        snapshot_context = page_context(_context_key(observed, controls=effective_contexts))
        context_value = state.get("context", {}).get("value_key", "")
        saved = None if context_effects else seen_again(snapshot["text"])
        if saved is not None and (saved["context_value"], saved["cutoff_context"]) != (
                context_value, snapshot_context):
            context_value, snapshot_context = saved["context_value"], saved["cutoff_context"]
            sites_audit.record("collection_context_identity_kept", session_id=session_id,
                               phase="location", steps=int(state.get("actions", 0)))
        if snapshot.get("truncated"):
            state["observation_limit"] = ("max_text_chars", 100000)
        content_key = hashlib.sha256(snapshot["text"].encode()).hexdigest()
        # A refused bind is recoverable only against this page, URL and the
        # selected values of its account/category selectors.
        effective_contexts = (collection_context.selectors(context_controls)
                              if effective_contexts is None else effective_contexts)
        state["observed_scene"] = (page, page.url, collection_context.value_key(effective_contexts))
        state["observed_context_controls"] = effective_contexts
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
            collection_evidence.add_view(entry, pending_view, snapshot_key)
        # Only a successfully executed associated continuation may supply
        # the previous stream. Typed evidence failures retain ordinary search.
        cutoff_actions = {}
        streams = []
        if not state.get("observation_limit") and not verifying_get and light_contexts is None:
            try:
                streams = await asyncio.wait_for(page.evaluate(collection_cutoff.observation_script()),
                                                 timeout=_ENUMERATE_TIMEOUT_MS / 1000.0)
                previous = carried["streams"] if (carried and carried["page"] is page
                    and carried["context"] == snapshot_context
                    and carried["origin"] == sites_origin.origin_of_url(page.url)) else []
                cutoff_actions = collection_cutoff.carried_boundary(target, streams, previous)
            except Exception:
                pass
        controls_key = login_navigation.state_key(page.url, controls)
        observation = {"key": controls_key + content_key, "controls_key": controls_key,
                "cutoff_actions": cutoff_actions,
                "cutoff_streams": streams,
                "cutoff_context": snapshot_context,
                # Returning to the same full URL may redraw unrelated cards.
                # The navigator rechecks each saved edge, including context;
                # a fresh semantic decision reads any remaining alternatives.
                "location_key": hashlib.sha256((page.url + context_value).encode()).hexdigest(),
                "context_key": hashlib.sha256(context_value.encode()).hexdigest(),
                "context_value": context_value,
                # The record partition and text this page was read in (seen_again).
                "partition_at": state.get("partition_active_key", ""),
                "content_key": content_key,
                "url": page.url, "page": page, "candidates": candidates,
                "semantic": semantic,
                "semantic_context_controls": context_controls,
                "effective_context_controls": effective_contexts,
                "semantic_fence": (id(page), page.url, bool(entry.get("authenticated")),
                                   str(entry.get("goal_scope") or "")),
                # An address does not carry a record partition selected on
                # another page (real runs r50/r53, ENI 22/10/2026: the list of
                # the second contract, opened again by address, showed the
                # first). Inside such a partition a return starts from the
                # partition's own first page, where the selection is redone.
                "get_restore_context": (not effective_contexts
                    and not state.get("context", {}).get("value_key")
                    and state.get("partition_active_key", "")
                    == state.get("partition_initial_key", state.get("partition_active_key", ""))),
                "record_parent": pending_view,
                "snapshot": {**snapshot, "context": snapshot_context,
                    "context_observed": True,
                    "records": {c["_record_context_key"]: record_contexts[c["_record_context_key"]]
                        for c in candidates if c.get("_record_context_key") in record_contexts},
                    "key": snapshot_key}}
        if semantic and semantic.get("scope", {}).get("kind") == "all":
            partitions = [p for p in semantic["partitions"] if p["requested"] is None]
            if partitions:
                # The first qualified selector page is a restoration bookmark,
                # not an instruction to enumerate unrelated menus.
                state.setdefault("partition_anchor", observation)
                state.setdefault("partition_active_key", snapshot_context)
                state.setdefault("partition_initial_key", state["partition_active_key"])
                partition_ids = {p["id"] for p in partitions}
                selectors_seen = state.setdefault("partition_selector_keys", set())
                selectors_seen.update(hashlib.sha256(json.dumps({k: c.get(k) for k in
                    ("tag", "role", "type", "name", "dom_id", "control_targets")},
                    sort_keys=True).encode()).hexdigest()
                    for c in context_controls if c["id"] in partition_ids)
                # A selector on another page is still a separate population
                # dimension. Sweeping the first cannot certify both.
                if len(selectors_seen) > 1:
                    state.setdefault("coverage_reasons", set()).add("partition_combination_unverified")
        return observation

    async def observe():
        # A render during the model call permits a fresh observation, never a
        # stale click. Retries retain the search's time/action budgets.
        for attempt in range(3):
            try:
                return await observe_once()
            except SceneChanged:
                if attempt == 2:
                    state["rejection"] = {"phase": "collection_snapshot_fence",
                                          "reason": "scene_changed_repeatedly"}
                    return {"terminal": {"ok": False, "error_class": "target_changed"}}

    async def choose(observation, tried):
        # Pages repeat menus, breadcrumbs and links to themselves. A GET of a
        # link already opened in this search, or of the root that backtracking
        # restores, must not create a navigation cycle. A new bound row still
        # needs its own observed detail edge when its local presentation differs.
        first_visit = not tried
        # A row whose detail held nothing in a second tab only is not tried
        # yet: it is opened once more, in place (real run bp1, ENI 23/10).
        for c in observation["candidates"]:
            if c.get("_record_context_key") and reopen_in_place(
                    {"parent": observation["snapshot"]["key"], "record": c["_record_context_key"]}):
                tried.discard(login_navigation.candidate_key(c))
        root = state["frames"][0]["observation"]["url"]
        opened = state.setdefault("opened_links", set()) | {
            _link_destination({"href": root}, root)}
        pages = {p.get("key"): p for p in entry.get("collected_pages", [])}
        unlinked_details = set()
        for c in observation["candidates"]:
            destination = _link_destination(c, observation["url"])
            if destination in opened - {""}:
                if c.get("_record_context_key") and collection_evidence.detail_empty(
                        entry, observation["snapshot"].get("context"), c["_record_context_key"]) and not (
                        reopen_in_place({"parent": observation["snapshot"]["key"],
                                         "record": c["_record_context_key"]})):
                    # Its detail was observed and held nothing: not again.
                    tried.add(login_navigation.candidate_key(c))
                    continue
                # The same bound row may occur in a preview and its archive.
                # A previously observed detail link establishes their shared
                # resource in the same context without clicking it again.
                # A different local row key is not proof of the same record.
                record = c.get("_record_context_key")
                known = {e["detail"] for e in entry.get("collection_record_views", [])
                         if record and e.get("record") == record
                         and e.get("parent") in pages
                         and pages[e["parent"]].get("context")
                             == observation["snapshot"].get("context")}
                matches = [p for p in entry.get("collected_pages", [])
                           if p.get("key") in known
                           and _link_destination({"href": p.get("url")},
                                                 observation["url"]) == destination]
                if len(matches) == 1:
                    collection_evidence.add_view(
                        entry, {"parent": observation["snapshot"]["key"], "record": record},
                        matches[0]["key"])
                elif record in observation["snapshot"]["records"]:
                    # Only a fresh semantic detail choice may revisit this
                    # destination; its successful navigation supplies the edge.
                    unlinked_details.add(login_navigation.candidate_key(c))
                    continue
                tried.add(login_navigation.candidate_key(c))
        state["cutoff_observed"] = {"streams": observation.get("cutoff_streams", []),
            "context": observation.get("cutoff_context"), "page": entry["page"],
            "origin": sites_origin.origin_of_url(entry["page"].url)}
        cutoff = observation.get("cutoff_actions", {})
        for control in observation["candidates"]:
            if control.get("id") not in cutoff:
                continue
            probe = {**control, "visible": True, "in_viewport": True, "topmost": True}
            # An associated filter/sort control is not a continuation. Other
            # account/category branches remain available to the ordinary route.
            if control.get("_record_context_key") or action_resolver.choose_goal_continuation_candidate(
                    target, [probe]).get("ok", False):
                tried.add(login_navigation.candidate_key(control))
                state["year_boundary"] = collection_cutoff.target_year(target)
        candidates = login_navigation.untried_candidates(observation["candidates"], tried)
        # Row controls of a kind whose details held nothing, twice, on this
        # list, hold nothing on its other rows either (real run Fastweb5).
        empty_kinds = {kind for kind, seen in state.get("empty_detail_kinds", {}).get(
            observation.get("location_key"), {}).items() if _empty_detail_kind(seen)}
        candidates = [c for c in candidates if not (
            c.get("_record_context_key") and _detail_kind(c) in empty_kinds)]
        # A context control whose effect stayed unverified is never a route.
        blocked = state.get("context", {}).get("unverifiable", ())
        candidates = [c for c in candidates if login_navigation.candidate_key(c) not in blocked]
        def preserve(candidate):
            if candidate.get("collection_observed") and not state.get("partition_transition"):
                state["found"] = True
                snapshot = observation["snapshot"]
                if collection_evidence.retain(entry, snapshot):
                    remember_partition(snapshot)
                    if observation.get("record_parent"):
                        parent = observation["record_parent"]
                        collection_evidence.add_view(entry, parent, snapshot["key"])
                        # Details of this kind hold their record.
                        origin = state.get("detail_origins", {}).get(parent["parent"] + parent["record"])
                        if origin:
                            state.setdefault("filled_detail_kinds", set()).add(origin)
            sites_audit.record(
                "collection_observation", owner=entry.get("owner", ""),
                session_id=session_id, domain=entry.get("domain", ""),
                collection_observed=bool(candidate.get("collection_observed")),
                proximity=candidate.get("proximity", "none"),
                steps=int(state.get("actions", 0)))

        # Real turn 92f548a3: from each bill's detail the search followed
        # other sections and downloads, replaying the path every time, until
        # the 300 s budget ran out before the full archive.
        leaf = _reached_by_detail(state.get("frames") or [])
        while True:
            decision = observation.get("semantic")
            fence = (id(entry["page"]), entry["page"].url,
                     bool(entry.get("authenticated")), str(entry.get("goal_scope") or ""))
            if fence != observation.get("semantic_fence"):
                state["rejection"] = {"phase": "collection_decision_fence"}
                raise ValueError("collection_observation_unresolved")
            if decision and decision.get("scene_key"):
                await verify_scene(observation["snapshot"], observation["semantic_context_controls"],
                                   decision["scene_key"])
            if decision is None or (decision["candidate"].get("id") and not any(
                    c["id"] == decision["candidate"]["id"] for c in candidates)):
                context_candidates = observation["semantic_context_controls"]
                controls = observation["effective_context_controls"]
                choices = collection_context.options(context_candidates, controls,
                    state.get("context", {}).get("opened"))
                expected_scene = scene_key(observation["snapshot"], context_candidates)
                listed = list_scene_key(observation["snapshot"], context_candidates)
                reused = None if leaf else reuse_list(
                    listed, context_candidates, candidates, observation["snapshot"])
                if reused is not None:
                    decision, classified_snapshot = reused
                else:
                    decision = await inspect_scene(candidates, controls, choices,
                        observation["snapshot"], parent=observation.get("record_parent"))
                    classified_snapshot = decision.pop("_snapshot")
                    await verify_scene(observation["snapshot"], context_candidates, expected_scene)
                    remember_list(listed, decision, classified_snapshot, context_candidates)
                if (not decision.get("error_class") and (
                        decision["context"].get("candidate")
                        or collection_context.fingerprint(decision["context_controls"])
                            != collection_context.fingerprint(controls)
                        or {c["id"]: {**c, "matching_options": sorted(c["matching_options"])}
                            for c in decision["contexts"]}
                            != {c["id"]: {**c, "matching_options": sorted(c["matching_options"])}
                                for c in (observation.get("semantic") or {}).get("contexts", [])})):
                    # A route-only retry cannot acquire fresh context-effect
                    # authority. A requested date stays unverified and the
                    # routes go on; any other unapplied request stops honestly.
                    unapplied = decision["context"].get("candidate")
                    if unapplied and decision["context"].get("role") != "temporal":
                        state["rejection"] = {"phase": "context_grounding",
                            "reason": "changed_context_classification",
                            "decision_actor": decision.get("_decision_actor", {"actor_type":"unknown"})}
                        raise ValueError("collection_observation_unresolved")
                    if unapplied:
                        state.setdefault("context", {}).setdefault("unverifiable", set()).add(
                            login_navigation.candidate_key(unapplied))
                    sites_audit.record("collection_context_unverified", session_id=session_id,
                                       steps=int(state.get("actions", 0)),
                                       phase="changed_context_classification")
                    state.setdefault("coverage_reasons", set()).add("context_effect_unverified")
                if not decision.get("error_class"):
                    observation["snapshot"] = classified_snapshot
                decision["scene_key"] = expected_scene
                observation["semantic"] = decision
            if decision.get("error_class"):
                raise ValueError("collection_observation_unresolved")
            # The same text in the same context at another address is a page
            # already explored (real run Fastweb8: the invoice list reached
            # again from another menu and its details opened again).
            seen_page = observation["snapshot"]
            duplicate = bool(seen_page.get("text")) and any(
                isinstance(page, dict) and page.get("text") == seen_page["text"]
                and page.get("context") == seen_page.get("context")
                and not _same_place(str(page.get("url") or ""),
                                    str(seen_page.get("url") or observation.get("url") or ""))
                for page in entry.get("collected_pages", []))
            candidate = decision["candidate"]
            preserve(candidate)
            parent = observation.get("record_parent")
            if parent and not candidate.get("collection_observed"):
                # The row's detail was observed and held nothing: it is not
                # opened again, from this list or the same list elsewhere,
                # once seen so in place; seen so in a second tab, it is
                # opened once more in place.
                origin = state.get("detail_origins", {}).get(parent["parent"] + parent["record"])
                reopened = not state.get("aside") and parent["record"] in state.get("reopened_rows", set())
                if collection_evidence.add_empty(entry, parent, aside=bool(state.get("aside"))):
                    if origin:
                        kinds_seen = state.setdefault("empty_detail_kinds", {}).setdefault(origin[0], {})
                        # A second tab may render an empty shell where the
                        # site keeps state per tab: counted apart. A row
                        # opened again in place counts once.
                        seen = kinds_seen.setdefault(origin[1], {"aside": 0, "in_place": 0})
                        if reopened:
                            seen["aside"] = max(0, seen["aside"] - 1)
                        seen["aside" if state.get("aside") else "in_place"] += 1
            location = observation.get("location_key")
            kinds = state.setdefault("detail_kinds", {})
            if (location and candidate.get("collection_purpose") == "detail"
                    and candidate.get("_record_context_key") in observation["snapshot"]["records"]):
                kinds.setdefault(location, set()).add(_detail_kind(candidate))
                state.setdefault("detail_origins", {})[
                    observation["snapshot"]["key"] + candidate["_record_context_key"]] = (
                    location, _detail_kind(candidate))
            if (not leaf and location and kinds.get(location)
                    and candidate.get("collection_purpose") in {"done", "navigate"}):
                siblings = _pending_detail_siblings(
                    candidates, observation["snapshot"], kinds[location], entry, target,
                    reopen=lambda key: reopen_in_place(
                        {"parent": observation["snapshot"]["key"], "record": key}))
                if siblings:
                    sites_audit.record("collection_detail_sibling", session_id=session_id,
                                       replaced=candidate.get("collection_purpose"),
                                       pending=len(siblings), steps=int(state.get("actions", 0)))
                    candidate = {**siblings[0], "collection_observed": True,
                                 "proximity": candidate.get("proximity", "matching"),
                                 "collection_purpose": "detail", "collection_continuation": False,
                                 "_decision_actor": {"actor_type": "runtime",
                                                     "rule": "detail_sibling"}}
            rows = collection_termination.page_rows(
                observation["snapshot"].get("records"), observation["snapshot"].get("text"))
            year = collection_cutoff.target_year(target)
            filter_ids = collection_termination.period_filter_ids(year, decision.get("contexts"))
            filters = [c for c in decision.get("context_controls") or () if c.get("id") in filter_ids]
            if filters:
                # Kept for this collection: the same control showing the same
                # values later still limits a list to the year, whether or not
                # that answer names it (real run bm4, ENI 23/10: named on the
                # second contract only; the third one explored ten pages more).
                state["period_filter_controls"] = copy.deepcopy(filters)
            elif state.get("period_filter_controls"):
                filters = still_shown(state["period_filter_controls"]) or []
            if (not leaf and not candidate.get("id") and candidate.get("collection_observed")
                    and not action_resolver.choose_goal_continuation_candidate(target, [
                        {**c, "visible": True, "in_viewport": True, "topmost": True}
                        for c in candidates]).get("ok")
                    and (collection_termination.stream_covers_year(year, rows)
                         or collection_termination.stream_reaches_year_start(year, rows)
                         or (filters and collection_termination.only_year(year, rows)))):
                # The model is done with this list, no next page is left, and
                # its own rows cover the requested year in any order: rows of
                # the year with an older one, or down to its January, or only
                # rows of the year with the site's filter limiting the list to
                # it. The search does not go back to other branches of this
                # partition (performance review 21/10/2026; real run am7, ENI
                # 23/10: 34 of 64 observations after each contract's list was
                # complete).
                state["search_complete"] = True
                sites_audit.record("collection_year_covered", session_id=session_id,
                                   steps=int(state.get("actions", 0)))
            if duplicate and not leaf:
                sites_audit.record("collection_duplicate_page", session_id=session_id,
                                   steps=int(state.get("actions", 0)))
                return None
            if (not candidate.get("id") and not state["found"] and first_visit
                    and len(state.get("frames") or ()) <= 1
                    and not state.get("first_page_reread")):
                # Nothing found and nothing done on the first page: a page
                # still loading its content (real run R47, a restored session
                # read before its contracts arrived) is read once more when
                # it changes within the reveal wait.
                state["first_page_reread"] = True
                if (await _wait_for_goal_content_change(
                        entry, await _goal_content_signature(entry)))[0]:
                    sites_audit.record("collection_first_page_reread", session_id=session_id,
                                       steps=int(state.get("actions", 0)))
                    raise SceneChanged("first_page_loading")
            if leaf or not candidate.get("id"):
                return None
            identity = login_navigation.candidate_key(candidate)
            if identity not in unlinked_details or (
                    candidate.get("collection_purpose") == "detail"
                    and candidate.get("_record_context_key")
                        in observation["snapshot"]["records"]):
                break
            # A repeated navigation link cannot use the detail exception.
            # Exclude this choice before preparation, leaving other rows usable.
            tried.add(identity)
            candidates = [c for c in candidates
                          if login_navigation.candidate_key(c) != identity]
        # Next/more is pagination, not another level in the site's hierarchy.
        probe = {**candidate, "visible": True, "in_viewport": True, "topmost": True}
        continuation = candidate.get("collection_continuation")
        if not isinstance(continuation, bool):
            continuation = action_resolver.choose_goal_continuation_candidate(
                target, [probe]).get("ok", False)
        return {"candidate": candidate,
                "model_selected": (candidate.get("_decision_actor") or {}).get("actor_type") != "runtime",
                "confidence": 0.5, "continuation": continuation,
                "record_parent": {"parent": observation["snapshot"]["key"],
                    "record": candidate["_record_context_key"]}
                    if candidate.get("collection_purpose") == "detail"
                    and candidate.get("_record_context_key") in observation["snapshot"]["records"]
                    else None}

    async def choose_fresh(observation, tried):
        # A page still changing after the decision (real run Fastweb 20/10):
        # no effect was taken, so the search observes and decides again.
        try:
            return await choose(observation, tried)
        except SceneChanged:
            raise login_navigation.Reobserve("collection_snapshot_fence") from None

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

    async def execute(choice, *, resume_token=None):
        page = entry["page"]
        eligible = _collection_get_restore_link(choice.get("candidate") or {}, page.url)
        documents = []
        def requested(request):
            if request.is_navigation_request() and request.frame is page.main_frame:
                documents.append(request.method)
        if eligible:
            page.on("request", requested)
        try:
            state.pop("cutoff_continuation", None)
            observed_cutoff = state.get("cutoff_observed", {})
            result = await execute_native(choice, resume_token=resume_token)
            if (choice.get("continuation") and not choice.get("replay")
                    and result.get("ok") and result.get("executed")
                    and not result.get("user_control_required")
                    and observed_cutoff.get("page") is page and entry["page"] is page):
                associated = [stream for stream in observed_cutoff.get("streams", [])
                    if (choice.get("candidate") or {}).get("id") in stream.get("continuations", [])]
                if associated:
                    state["cutoff_continuation"] = {**observed_cutoff, "streams": associated}
            # A plain anchor that the application routed to exactly its own
            # URL, without a document request, is bookmarked like a GET
            # navigation (Roberto, 19/10/2026). The restore still has to
            # match the saved page exactly, or it falls back to the replay.
            routed = not documents and page.url == eligible
            if (eligible and result.get("ok") and result.get("executed")
                    and choice.get("_get_plan_navigation")
                    and (routed or (documents and all(method == "GET" for method in documents)))
                    and entry["page"] is page and not page.is_closed()
                    and _collection_get_restore_link({"tag": "a", "href": page.url}, page.url)):
                choice["observed_get"] = (page, page.url)
                if routed:
                    sites_audit.record("collection_routed_bookmark", session_id=session_id,
                                       steps=int(state.get("actions", 0)))
            return result
        finally:
            if eligible:
                page.remove_listener("request", requested)

    async def execute_native(choice, *, resume_token=None):
        nonlocal navigation_anchor
        started = _monotonic()
        effect_page = entry["page"]
        effect_origin = sites_origin.origin_of_url(effect_page.url)
        effect_url = effect_page.url
        destination = _link_destination(choice.get("candidate") or {}, entry["page"].url)
        aside_url = "" if resume_token is not None else aside_link(choice, effect_page)
        pages_before = list(getattr(entry.get("context"), "pages", ()) or ())
        recovered = False
        while True:
            if resume_token is not None:
                plan = entry.get("pending_actions", {}).get(resume_token)
                if (not isinstance(plan, dict) or plan.get("collection_search") != key
                        or not plan.get("_challenge_before_dispatch")):
                    return {"ok": False, "error_class": "action_expired"}
                # A resumed detail click opens in place: the second tab is
                # only checked on a fresh choice.
                plan.pop("open_aside", None)
                prepared = {"ok": True, "token": resume_token, "plan": plan}
                # The ordinary gate passed before this challenge. Dispatch
                # the exact saved plan with all current native checks.
                result = await _execute_plan(entry, resume_token, plan)
                resume_token = None
            else:
                prepared = await _prepare_action(
                    entry, session_id, action, None, primitive_override="search",
                    target_override=target, allow_model=False, collection_choice=choice)
                if prepared.get("ok"):
                    prepared["plan"]["collection_search"] = key
                    # The audit separates a replayed saved edge from a new choice.
                    prepared["plan"]["collection_replay"] = bool(choice.get("replay"))
                    if aside_url:
                        prepared["plan"]["open_aside"] = True
                result = await _handle_prepared_action(entry, session_id, action, prepared)
            if result.get("user_control_required") and (
                    not prepared.get("ok")
                    or prepared["plan"].get("_challenge_before_dispatch")):
                state["challenge_resume"] = {"step": "execute", "choice": choice,
                                              "token": prepared.get("token")}
            audit_failed_action(result, str((prepared.get("plan") or {}).get("primitive")
                                            or "click"), bool(choice.get("replay")), started)
            if result.get("pre_dispatch_refused") and prepared.get("token"):
                # The refused plan never clicked: retire its token so that no
                # old plan stays executable. Other consents are untouched.
                entry.get("pending_actions", {}).pop(prepared["token"], None)
            if result.get("collection_bind_refused"):
                page, location, selected = state.get("observed_scene") or (None, "", "")
                current = entry["page"]
                fresh_contexts = collection_context.rebind_controls(
                    state.get("observed_context_controls", []), await read_controls(current))
                if ((prepared.get("ok") and not result.get("pre_dispatch_refused"))
                        or current is not page or current.url != location
                        or fresh_contexts is None
                        or selected != collection_context.value_key(fresh_contexts)):
                    # Only a refusal before any effect on the observed page,
                    # URL and context leaves the branch unresolved; anything
                    # else is terminal.
                    result = {k: v for k, v in result.items() if k != "collection_bind_refused"}
            if (choice.get("replay") and not recovered
                    and result.get("pre_dispatch_refused")
                    and result.get("collection_bind_refused")):
                # A replayed edge has already spent its action and advanced
                # the cursor: prepare the same saved choice once more, model
                # off, through the ordinary gate. A second refusal is final.
                recovered = True
                sites_audit.record("collection_pre_dispatch_recovery",
                                   session_id=session_id,
                                   steps=int(state.get("actions", 0)),
                                   detail=str(result.get("detail") or ""))
                continue
            break
        if (prepared.get("plan") or {}).get("open_aside"):
            settled = await settle_aside(choice, effect_page, effect_url, aside_url,
                                         pages_before, result)
            if settled == "retry":
                return await execute_native(choice)
        if result.get("ok") and result.get("executed"):
            if destination:
                state.setdefault("opened_links", set()).add(destination)
            content_change = await _wait_for_goal_content_change(
                entry, (prepared.get("plan") or {}).get("facet_sig_before", ""),
                _goal_facet_signature)
            if ((prepared.get("plan") or {}).get(
                    "_collection_navigation_unresolved")
                    and not content_change[0]):
                # Quiet or exhausted waits are not proof that this branch
                # was explored. The click/token were already consumed: stop
                # before another choice, backtracking or saved-edge replay.
                facts = await _audit_unresolved_control(
                    entry, session_id, prepared.get("plan") or {}, int(state.get("actions", 0)))
                if not _scroll_anchor(facts):
                    state.setdefault("unresolved_control", "action_outcome_unknown")
                    return {**result, "ok": False,
                            "error_class": "action_outcome_unknown",
                            "automatic_resume_blocked": True}
                # A plain link to a part of this same page that is already
                # shown can only scroll: no request, no change, nothing
                # explored beyond it (real run Fastweb4: «mostra altre»).
            # A script link can change the page after the click returned:
            # read it once it has stopped changing (real turn 4b0ae427).
            await _wait_for_dom_quiet(entry["page"])
            if choice.get("aside"):
                navigation_anchor = (entry["page"], sites_origin.origin_of_url(entry["page"].url))
                state["aside"]["anchor"] = (effect_page, effect_origin)
            elif (prepared.get("plan") or {}).get("kind") == "goal_navigation":
                choice["_get_plan_navigation"] = True
                navigation_anchor = (effect_page, effect_origin)
            if not choice.get("replay") and choice.get("record_parent"):
                state["pending_record_view"] = choice["record_parent"]
        return result

    async def reselect_partition():
        """A return by address may show the site's default record partition
        again (real run r50, ENI 22/10/2026: back on the list of the second
        contract the reloaded page showed the first one, and the collection
        ended before the third). The active partition is selected again with
        its verified selector and option through the ordinary gate, then
        checked against the controls that showed it selected."""
        sweep = state.get("partition_sweep") or {}
        saved, option = sweep.get("active_controls"), sweep.get("active_option")
        active = state.get("partition_active_key", "")
        if (not saved or not option or not sweep.get("selector_key") or sweep.get("phase")
                or active == state.get("partition_initial_key", active)):
            return None

        def shown(candidates):
            rebound = collection_context.rebind_controls(saved, candidates)
            return rebound is not None and _context_key([], controls=rebound) == active

        # The page opened again may draw its selector only after its own data
        # requests (real run a4, ENI 22/10/2026), as the observation does.
        await _wait_for_content_settle(entry)
        candidates = await read_controls(entry["page"])
        if shown(candidates):
            return None
        found = []
        for wanted in (sweep["selector_key"], option):
            # The controls as the collection observes them: the selector and,
            # once it is open, its options.
            target = next((c for c in candidates if login_navigation.candidate_key(c) == wanted), None)
            found.append(target is not None)
            if target is None or (wanted != option and target.get("aria_expanded") == "true"):
                continue
            if not login_navigation.spend(state, login_navigation.COLLECTION_MAX_ACTIONS):
                return {"ok": False, "error_class": "goal_step_limit"}
            result = await execute({"candidate": target, "replay": True, "confidence": 1.0})
            if result.get("approval_required") or not result.get("ok"):
                return result
            await _wait_for_content_settle(entry)
            candidates = await read_controls(entry["page"])
        reselected = shown(candidates)
        sites_audit.record("collection_partition_reselected", session_id=session_id,
                           outcome=reselected, steps=int(state.get("actions", 0)),
                           selector_found=found[0], option_found=found[1],
                           controls=len(candidates))
        if reselected:
            return None
        state["rejection"] = {"phase": "partition_reselect", "reason": "selection_unverified"}
        return {"ok": False, "error_class": "target_changed"}

    async def restore(root, *, resume_token=None):
        nonlocal navigation_anchor
        url = root["url"]
        if (urllib.parse.urlsplit(url).scheme not in {"http", "https"}
                or _host_of_url(url) not in set(entry.get("allowlist") or ())
                or entry.get("secret_pending")):
            return {"ok": False, "error_class": "mandate_scope_exceeded"}
        if root["page"].is_closed():
            return {"ok": False, "error_class": "session_lost"}
        entry["page"] = root["page"]
        effect_origin = sites_origin.origin_of_url(root["page"].url)
        started = _monotonic()
        if resume_token is not None:
            plan = entry.get("pending_actions", {}).get(resume_token)
            if (not isinstance(plan, dict) or plan.get("collection_search") != key
                    or not plan.get("_challenge_before_dispatch")):
                return {"ok": False, "error_class": "action_expired"}
            prepared = {"ok": True, "token": resume_token, "plan": plan}
            result = await _execute_plan(entry, resume_token, plan)
        else:
            prepared = await _prepare_action(
                entry, session_id, action, None, primitive_override="goto",
                target_override=url, allow_model=False)
            if prepared.get("ok"):
                prepared["plan"].update(kind="goal_navigation", collection_search=key)
            result = await _handle_prepared_action(entry, session_id, action, prepared)
        if result.get("user_control_required") and (
                not prepared.get("ok")
                or prepared["plan"].get("_challenge_before_dispatch")):
            state["challenge_resume"] = {"step": "restore", "root": root,
                                          "token": prepared.get("token")}
        audit_failed_action(result, "goto", False, started)
        if result.get("ok") and result.get("executed"):
            navigation_anchor = (root["page"], effect_origin)
            for page in state["pages"] - {root["page"]}:
                await page.close()
            state["pages"] = {root["page"]}
            sites_audit.record("collection_backtrack", session_id=session_id,
                               restore_depth=state["frames"][(state.get("replay") or {}).get("restore_index", 0)]["depth"],
                               steps=int(state.get("actions", 0)))
            reselected = await reselect_partition()
            if reselected is not None:
                return reselected
        return result

    def reopen_in_place(parent):
        # A row whose detail held nothing in a second tab only, where details
        # of its kind hold their record, is opened once more in place,
        # whatever that shows (real run bp1, ENI 23/10: a second tab read
        # before its data arrived). Details empty by nature keep the rule of
        # their kind (real run Fastweb5).
        origin = state.get("detail_origins", {}).get(
            str(parent.get("parent", "")) + str(parent.get("record", "")))
        return (origin in state.get("filled_detail_kinds", set())
                and parent.get("record") not in state.get("reopened_rows", set())
                and collection_evidence.empty_in_second_tab(entry, parent))

    def aside_link(choice, page):
        """The detail link a middle click may open in a second tab, or "".

        Only a bound record's plain GET link to another address of an allowed
        host, never from inside another aside, never on a replay and never
        once the site has refused one (Roberto, 20/10/2026). A second tab
        starts from the site's own selection, which a site may share with its
        other tabs: once the search has changed a selection (a record
        partition or a context control), details open in place (real run R36).
        """
        candidate = choice.get("candidate") or {}
        parent = choice.get("record_parent") or {}
        origin = state.get("detail_origins", {}).get(
            str(parent.get("parent", "")) + str(parent.get("record", "")))
        if origin and _needs_in_place_witness(
                state.get("empty_detail_kinds", {}).get(origin[0], {}).get(origin[1], {})):
            return ""
        if parent and reopen_in_place(parent):
            state.setdefault("reopened_rows", set()).add(parent["record"])
            return ""
        url = _collection_get_restore_link(candidate, page.url)
        selection_changed = (
            (state.get("context") or {}).get("changed")
            or state.get("partition_active_key", "")
            != state.get("partition_initial_key", state.get("partition_active_key", "")))
        if (choice.get("replay") or state.get("aside") or state.get("aside_disabled")
                or selection_changed
                or candidate.get("collection_purpose") != "detail"
                or not choice.get("record_parent") or not url
                or urllib.parse.urldefrag(url)[0] == urllib.parse.urldefrag(page.url)[0]
                or _host_of_url(url) not in set(entry.get("allowlist") or ())):
            return ""
        return url

    async def settle_aside(choice, parent, parent_url, url, before, result):
        """After a middle click on a detail link: keep the verified tab,
        accept an effect in place, or close the tab for the ordinary click.

        The list stays as it was in its own tab, so the return needs no
        restore or replay (real run R32: 18 returns to the home page for 8
        details). A tab that lands elsewhere, does not settle, asks for a
        password or shows a challenge is closed, and this and later details
        use the ordinary click. Returns "opened", "kept" or "retry".
        """
        started = _monotonic()
        current = entry["page"]
        strays = [p for p in (getattr(entry.get("context"), "pages", ()) or ())
                  if all(p is not old for old in before) and p is not current]
        for stray in strays:
            try:
                await stray.close()
            except Exception:  # noqa: BLE001
                pass
        reason = ""
        if not (result.get("ok") and result.get("executed")):
            reason = str(result.get("error_class") or "not_executed")[:40]
        elif current is parent:
            reason = "in_place" if parent.url != parent_url else "no_tab"
        else:
            try:
                # A tab-bound page may send a fresh tab elsewhere by script.
                await _wait_for_dom_quiet(current)
                wanted, landed = urllib.parse.urlsplit(url), urllib.parse.urlsplit(current.url)
                if (landed.scheme, landed.netloc, landed.path) != (wanted.scheme, wanted.netloc, wanted.path):
                    reason = "landed_elsewhere"
                elif not await _wait_for_content_settle(entry):
                    reason = "unsettled"
                elif await _password_in_frames(current):
                    reason = "password"
                elif (await challenges.observe(current))["status"] != "clear":
                    reason = "challenge"
            except Exception as exc:  # noqa: BLE001 -- any doubt is the ordinary click
                reason = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:40] or "error"
        sites_audit.record("collection_detail_aside", session_id=session_id,
                           outcome=not reason, reason=reason or None,
                           elapsed_ms=round((_monotonic() - started) * 1000),
                           steps=int(state.get("actions", 0)))
        if not reason:
            state["aside"] = {"parent": parent, "page": current, "anchor": navigation_anchor}
            state["pages"].add(current)
            choice["aside"] = True
            return "opened"
        state["aside_disabled"] = True
        if current is not parent:
            state["pages"].discard(current)
            try:
                await current.close()
            except Exception:  # noqa: BLE001
                pass
            entry["page"] = parent
            return "retry"
        if result.get("error_class") == "popup_ambiguous" or (
                result.get("ok") and parent.url == parent_url):
            # No tab was kept and the list did not change: nothing of the
            # detail was read yet.
            return "retry"
        return "kept"

    async def close_aside():
        """Close an open detail tab and make its list the current page."""
        nonlocal navigation_anchor
        aside = state.pop("aside", None)
        if aside is None:
            return None
        state["pages"].discard(aside["page"])
        try:
            if not aside["page"].is_closed():
                await aside["page"].close()
        except Exception:  # noqa: BLE001 -- the list is observed next anyway
            pass
        entry["page"] = aside["parent"]
        navigation_anchor = aside["anchor"]
        return aside

    async def return_back(popped, frame):
        """After a detail opened in place, «back» in the browser's history,
        kept only when the list shows again exactly its saved text at its
        address, in the same partition (Roberto 23/10/2026: ritorno con
        «indietro» verificato; real run bm5, ENI: 17 returns through the home
        page, the partition selection and two clicks). Otherwise the ordinary
        return, and no other «back» in this partition."""
        nonlocal navigation_anchor
        saved = frame["observation"]
        partition = state.get("partition_active_key", "")
        failed = state.setdefault("back_failed", set())
        page = entry["page"]
        if (not _COLLECTION_RETURN_BACK or len(popped) != 1 or (popped[0].get("via") or {}).get("aside")
                or partition in failed or saved.get("page") is not page or page.is_closed()
                or not isinstance(saved.get("content_key"), str)
                or saved.get("partition_at") != partition
                or not login_navigation.spend(state, login_navigation.COLLECTION_MAX_ACTIONS)):
            return False
        started, reason = _monotonic(), ""
        try:
            await asyncio.wait_for(page.go_back(wait_until="load", timeout=int(_OP_TIMEOUT_S * 1000)),
                                   timeout=_OP_TIMEOUT_S)
        except Exception:  # noqa: BLE001 -- an application page may change without a load
            pass
        if page.is_closed() or not await _wait_for_content_settle(entry):
            reason = "unsettled"
        elif page.url != saved.get("url"):
            reason = "elsewhere"
        elif hashlib.sha256((await read_text())["text"].encode()).hexdigest() != saved["content_key"]:
            reason = "changed"
        sites_audit.record("collection_return_back", session_id=session_id, outcome=not reason,
                           reason=reason or None, elapsed_ms=round((_monotonic() - started) * 1000),
                           steps=int(state.get("actions", 0)))
        if reason:
            failed.add(partition)
            return False
        navigation_anchor = (page, sites_origin.origin_of_url(page.url))
        return True

    async def return_from_aside(popped, frame, ending=False):
        aside = await close_aside()
        if aside is None:
            return False if ending else await return_back(popped, frame)
        return bool(len(popped) == 1
                    and (popped[0].get("via") or {}).get("aside") is True
                    and not aside["parent"].is_closed()
                    and frame["observation"].get("page") is aside["parent"])

    async def continue_search():
        interrupted = state.pop("challenge_resume", None)
        if interrupted is not None:
            # The FSM already charged and advanced this step. Finish only
            # that pre-effect step before letting it observe the next node.
            if interrupted["step"] == "execute":
                result = await execute(interrupted["choice"],
                                       resume_token=interrupted["token"])
            else:
                result = await restore(interrupted["root"],
                                       resume_token=interrupted["token"])
            if result.get("approval_required") or result.get("user_control_required") or not result.get("ok"):
                return result
            sweep = state.get("partition_sweep")
            if sweep and sweep.get("pending_phase"):
                sweep["phase"] = sweep.pop("pending_phase")
        while True:
            sweep = state.get("partition_sweep")
            if sweep and sweep.get("phase"):
                result = await advance_partition(sweep)
                if result is not None:
                    return result
                continue
            result = await login_navigation.discover(
                state, observe=observe, choose=choose_fresh, execute=execute,
                restore=restore, max_depth=login_navigation.COLLECTION_MAX_DEPTH,
                max_actions=login_navigation.COLLECTION_MAX_ACTIONS, exhaustive=True,
                guard=guard_before_restore, aside_return=return_from_aside)
            anchor = state.get("partition_anchor")
            if (not result.get("exhausted") or not anchor
                    or state.get("record_scope", {}).get("kind") != "all"):
                return result
            sweep = state.setdefault("partition_sweep", {"visited": set(), "completed": set(),
                "options": set(), "revealed": set(), "selector_key": None})
            sweep["completed"].add(state.get("partition_active_key", ""))
            sweep.update(phase="restore", exhausted=result)

    async def advance_partition(sweep):
        """Sweep observed record partitions after each DFS, sharing all caps.

        No option is an ordinary navigation edge. Each sweep restores and
        requalifies its selector, so replay never borrows another context's
        handles, attempted links, record identities or semantic decision.
        """
        async def effect(choice=None, *, next_phase):
            if not login_navigation.spend(state, login_navigation.COLLECTION_MAX_ACTIONS):
                return {"ok": False, "error_class": "goal_step_limit"}
            sweep["pending_phase"] = next_phase  # before await: no duplicated effect on resume
            result = (await restore(state["partition_anchor"]) if choice is None
                      else await execute(choice))
            if result.get("approval_required") or not result.get("ok"):
                return result
            sweep["phase"] = sweep.pop("pending_phase")
            return None

        if sweep["phase"] == "restore":
            # Keep the old frames until restore has completed (audit/replay
            # bookmarks belong to that context). The next DFS has a new cursor.
            return await effect(next_phase="inspect")
        state["partition_transition"] = True
        try:
            observation = await observe()
        finally:
            state.pop("partition_transition", None)
        if observation.get("terminal") is not None:
            return observation["terminal"]
        semantic = observation.get("semantic") or {}
        partitions = [p for p in semantic.get("partitions", []) if p["requested"] is None]
        if len(partitions) != 1:
            state.setdefault("coverage_reasons", set()).add("partition_combination_unverified")
            return sweep["exhausted"]
        partition = partitions[0]
        controls = observation["effective_context_controls"]
        selector = next((c for c in controls if c["id"] == partition["id"]), None)
        selector_key = login_navigation.candidate_key(selector) if selector else None
        if selector is None or (sweep["selector_key"] is not None
                                and selector_key != sweep["selector_key"]):
            state["rejection"] = {"phase": "partition_selector", "reason": "selector_changed"}
            return {"ok": False, "error_class": "target_changed"}
        sweep["selector_key"] = selector_key
        active = _context_key([], controls=controls)
        if sweep["phase"] == "switched":
            if active == sweep["before"] or selector.get("aria_expanded") == "true":
                state["rejection"] = {"phase": "partition_selection", "reason": "selection_unverified"}
                return {"ok": False, "error_class": "target_changed"}
            sweep["active_option"] = sweep.pop("selected")
            sweep["visited"].add(sweep["active_option"])
            # The controls that showed this partition selected: a return by
            # address is checked against them (reselect_partition).
            sweep["active_controls"] = copy.deepcopy(controls)
            state["partition_active_key"] = active
            state.setdefault("partition_labels", {})[active] = (
                sweep.pop("selected_label", "") or _control_label(selector))
            # Evidence, elapsed time and effects remain global; only the
            # navigation cursor and URL attempts belong to the new context.
            login_navigation.reset_cursor(state)
            for field in ("opened_links", "cutoff_continuation", "cutoff_observed", "search_complete"):
                state.pop(field, None)
            sweep.pop("phase", None)
            sites_audit.record("collection_partition_selected", session_id=session_id,
                visited_count=len(sweep["visited"]), completed_count=len(sweep["completed"]),
                decision_actor=semantic.get("_decision_actor", {"actor_type": "unknown"}))
            return None
        options = partition["options"]
        if selector.get("aria_expanded") != "true":
            # The guard against revealing without progress counts the
            # partitions visited: a page loaded again on the site's default
            # partition is a new reveal once another one was completed.
            reveal_key = (selector_key, active, len(sweep["visited"]))
            if reveal_key in sweep["revealed"]:
                state.setdefault("coverage_reasons", set()).add("partition_options_unverified")
                return sweep["exhausted"]
            sweep["revealed"].add(reveal_key)
            state.setdefault("context", {})["opened"] = {"before": [login_navigation.candidate_key(c)
                for c in observation["semantic_context_controls"]]}
            choice = {"candidate": {**selector, "_decision_actor": semantic.get("_decision_actor", {})},
                      "model_selected": True, "confidence": .5}
            return await effect(choice, next_phase="inspect")
        if not options:
            state.setdefault("coverage_reasons", set()).add("partition_options_unverified")
            return sweep["exhausted"]
        labels = state.setdefault("partition_labels", {})
        for option in options:
            identity = login_navigation.candidate_key(option)
            sweep["options"].add(identity)
            sweep.setdefault("option_labels", {})[identity] = _control_label(option)
            if (str(option.get("aria_selected") or "").lower() == "true"
                    or str(option.get("aria_checked") or "").lower() == "true"):
                sweep["visited"].add(identity)
                labels.setdefault(active, _control_label(option))
        remaining = [c for c in options if login_navigation.candidate_key(c) not in sweep["visited"]]
        if not remaining:
            if sweep["options"] - sweep["visited"]:
                state.setdefault("coverage_reasons", set()).add("partition_alternative_unverified")
            if state.get("coverage_reasons", set()).intersection({
                    "partition_options_unclassified", "partition_alternative_unverified"}):
                return sweep["exhausted"]
            state.setdefault("coverage_reasons", set()).discard("record_partitions_unverified")
            sweep.pop("phase", None)
            result = {**sweep["exhausted"], "partition_coverage": {"status": "observed_alternatives_exhausted",
                "alternatives": len(sweep["options"]), "contexts_exhausted": len(sweep["completed"])}}
            return result
        selected = remaining[0]
        sweep.update(selected=login_navigation.candidate_key(selected), before=active,
                     selected_label=_control_label(selected))
        choice = {"candidate": {**selected, "_decision_actor": semantic.get("_decision_actor", {})},
                  "model_selected": True, "confidence": .5}
        return await effect(choice, next_phase="switched")

    remaining = float(state["remaining_s"])
    started = _monotonic()
    try:
        result = await asyncio.wait_for(continue_search(), timeout=remaining)
    except _CandidateObservationError as exc:
        sites_audit.record("collection_observation_failed", session_id=session_id,
                           reason=exc.reason, detail=exc.detail)
        result = {"ok": False, "error_class": "target_unstable"}
    except asyncio.TimeoutError:
        result = {"ok": False, "error_class": "goal_step_limit",
                  "cap_field": "active_seconds",
                  "cap_value": login_navigation.COLLECTION_TIMEOUT_S}
    except SceneChanged:
        state["rejection"] = {"phase": "collection_snapshot_fence", "reason": "scene_changed"}
        result = {"ok": False, "error_class": "target_changed"}
    except ValueError as exc:
        if str(exc) != "collection_observation_unresolved":
            raise
        result = {"ok": False, "error_class": "selector_missing"}
    finally:
        state["remaining_s"] = max(0.0, remaining - (_monotonic() - started))
    if result.get("approval_required") or result.get("user_control_required"):
        metadata = _collection_metadata(entry, state, result)
        entry["collection_source_metadata"] = metadata
        return {**result, "source_metadata": metadata}
    # A detail tab still open at the end (budget, refusal) is closed: the
    # list is the page the turn goes on from. A pause above keeps it.
    await close_aside()
    if result.get("error_class") == "target_changed" and state.get("rejection"):
        sites_audit.record("collection_navigation_rejected", session_id=session_id,
                           steps=int(state.get("actions", 0)), **state["rejection"])
    result, reasons = collection_outcome.outcome(
        result, state, [item["text"] for item in entry.get("collected_pages", [])
                        if isinstance(item, dict) and item.get("text")])
    if reasons:
        state["coverage_reasons"] = reasons
    _distrust_restored_session(entry, result)
    _label_collected_partitions(entry, state)
    metadata = _collection_metadata(entry, state, result)
    entry["collection_source_metadata"] = metadata
    entry.pop("collection_search", None)
    cloud = int(state.get("cloud_llm_calls") or 0)
    return {**result, "source_metadata": metadata,
            "executed": bool(state["found"]), "primitive": "observe",
            "url": scrub_url(entry["page"].url),
            **({"cloud_llm_calls": cloud} if cloud else {})}


def _control_label(control: dict) -> str:
    """What the site shows for a control: its text (a selector's value) first."""
    return " ".join(str(control.get("text") or control.get("name") or "").split())[:160]


def _label_collected_partitions(entry: dict, state: dict) -> None:
    """Each collected page states the record partition it was read in.

    The partition is the opaque active key; the label is what the site
    showed for the chosen option. The starting partition, when no option was
    marked, takes the only option never chosen, and only once the sweep has
    finished. Without two labelled partitions no page is labelled.
    """
    labels = dict(state.get("partition_labels") or {})
    sweep = state.get("partition_sweep") or {}
    initial = state.get("partition_initial_key")
    if initial and initial not in labels and sweep and not sweep.get("phase"):
        unvisited = [label for identity, label in (sweep.get("option_labels") or {}).items()
                     if identity not in sweep.get("visited", set()) and label]
        if len(unvisited) == 1:
            labels[initial] = unvisited[0]
    if len({label for label in labels.values() if label}) < 2:
        return
    partitions = entry.get("collected_partitions") or {}
    for page in entry.get("collected_pages") or ():
        if not isinstance(page, dict):
            continue
        partition = partitions.get(page.get("key")) or (
            initial if initial and page.get("context") == initial else "")
        if labels.get(partition):
            page["partition_ref"] = hashlib.sha256(partition.encode()).hexdigest()[:24]
            page["context_label"] = labels[partition]


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


def _reached_by_detail(frames: list[dict]) -> bool:
    """The current page was opened as a record's detail on another location.

    A detail completes the record it belongs to: it is read and kept, and the
    search returns from it instead of exploring the site again from there. A
    detail opened in place (a menu revealing the record's actions) is not a
    leaf: what it revealed is still to be followed.
    """
    if len(frames) < 2:
        return False
    current, parent = frames[-1], frames[-2]
    via = (current.get("via") or {}).get("candidate") or {}
    location = current["observation"].get("location_key")
    return (via.get("collection_purpose") == "detail" and bool(location)
            and location != parent["observation"].get("location_key"))


def _collection_control_observation(candidate: dict) -> dict:
    """Expose native affordances to the observer, without changing scene keys."""
    return {**collection_context.describe(candidate),
            "tag": str(candidate.get("tag") or ""),
            "type": str(candidate.get("type") or ""),
            "has_href": bool(candidate.get("href")),
            "aria_haspopup": str(candidate.get("aria_haspopup") or "")}


_RECORD_CONTEXT_CHARS = 240
_RECORD_CONTEXT_BUDGET = 8000


def _control_set(candidates: list[dict]) -> list[str]:
    """The controls of a scene in any order and without enumeration numbers,
    which follow the scroll position."""
    return sorted(json.dumps({**{k: v for k, v in collection_context.describe(c).items() if k != "id"},
                              "identity": login_navigation.candidate_key(c)}, sort_keys=True)
                  for c in candidates)


def _identities(candidates: list[dict]) -> dict:
    return {c["id"]: login_navigation.candidate_key(c) for c in candidates if c.get("id")}


def _rebind_decision(decision: dict, identities: dict, current: list[dict]) -> dict | None:
    """A decision taken on an earlier enumeration, its controls found again by
    identity in the current one (enumeration numbers change with the scroll
    position, identities do not); None when one is missing or not unique."""
    found = {}
    for candidate in current:
        found.setdefault(login_navigation.candidate_key(candidate), []).append(candidate)
    position = {c["id"]: n for n, c in enumerate(current)}

    def now(old_id):
        same = found.get(identities.get(old_id), [])
        if len(same) != 1:
            raise LookupError(old_id)
        return same[0]
    try:
        rebound = copy.deepcopy(decision)
        # In the current page order, as a fresh classification lists them.
        rebound["context_controls"] = sorted((dict(now(c["id"])) for c in decision["context_controls"]),
                                             key=lambda c: position[c["id"]])
        rebound["contexts"] = [{**c, "id": now(c["id"])["id"],
                                "matching_options": [now(i)["id"] for i in c["matching_options"]]}
                               for c in decision["contexts"]]
        rebound["partitions"] = [{**p, "id": now(p["id"])["id"],
                                  "options": [dict(now(o["id"])) for o in p["options"]]}
                                 for p in decision["partitions"]]
        rebound["requested"] = {now(k)["id"]: v for k, v in decision["requested"].items()}
    except LookupError:
        return None
    return rebound


def _detail_kind(candidate: dict) -> str:
    """The same control of another row: browser semantics without its row."""
    return json.dumps({key: candidate.get(key) for key in ("tag", "role", "type", "name")},
                      sort_keys=True)


def _pending_detail_siblings(candidates: list[dict], snapshot: dict, kinds: set[str],
                             entry: dict, target: str, reopen=lambda _key: False) -> list[dict]:
    """Rows still unopened through a control of a kind already chosen as detail.

    The model labelled one row control as opening its own record. The same
    control of the other rows of this snapshot opens theirs: deterministic,
    no new decision. Downloads, forms, secrets and ambiguous rows are never
    opened this way; with a single requested year, rows of other years only
    are left to the model.
    """
    records = snapshot.get("records") if isinstance(snapshot.get("records"), dict) else {}
    year = collection_cutoff.target_year(target)
    pending = []
    for candidate in candidates:
        key = candidate.get("_record_context_key")
        native = records.get(key) if key else None
        text = native.get("text") if isinstance(native, dict) else native
        count = native.get("count") if isinstance(native, dict) else 1
        if (not key or not isinstance(text, str) or not text.strip()
                or (isinstance(count, int) and count > 1)
                or candidate.get("download") or candidate.get("secret_input")
                or candidate.get("form_action") or _detail_kind(candidate) not in kinds
                or (collection_evidence.detail_observed(entry, snapshot.get("context"), key)
                    and not reopen(key))):
            continue
        years = set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", text))
        if year is not None and years and str(year) not in years:
            continue
        pending.append(candidate)
    return pending


def _record_context_observations(candidates: list[dict], snapshot: dict,
                                 scene: list[dict] | None = None) -> list[dict]:
    """Row text bound to each control, read only from this immutable snapshot.

    A bound control without the text of its row looks identical to the same
    control of another row. The text is page data with no authority; the
    binding stays native, and limits or ambiguity are declared, not guessed.
    """
    records = snapshot.get("records") if isinstance(snapshot.get("records"), dict) else {}
    keys = [c.get("_record_context_key") or "" for c in candidates]
    # Two controls of the observed scene bound to the same row identity are
    # not distinguishable by that row text, whichever window shows them.
    scene_keys = [c.get("_record_context_key") or "" for c in (
        candidates if scene is None else scene)]
    shared = {key for key in keys if key and scene_keys.count(key) > 1}
    budget, observed = _RECORD_CONTEXT_BUDGET, []
    for key in keys:
        if not key:
            observed.append({})
            continue
        native = records.get(key)
        text = native.get("text") if isinstance(native, dict) else native
        if not isinstance(text, str) or not text.strip():
            observed.append({"record_context_status": "missing"})
            continue
        if budget <= 0:
            observed.append({"record_context_status": "budget_exhausted"})
            continue
        text = " ".join(text.split())
        count = native.get("count") if isinstance(native, dict) else None
        clipped = text[:min(_RECORD_CONTEXT_CHARS, budget)]
        budget -= len(clipped)
        observed.append({"record_context": clipped,
            "record_context_status": "ambiguous" if key in shared or (
                isinstance(count, int) and count > 1) else "bound",
            "record_context_truncated": len(clipped) < len(text)})
    return observed


_COLLECTION_OBSERVATION_CACHE_LIMIT = 32


def _collection_observation_cache_key(entry, search, provider, tier, system, prompt,
        snapshot, candidates, controls, choices, scene_candidates):
    """Hash exact inputs privately; unsupported native values disable reuse."""
    def native_controls(items):
        return [{"native": c, "identity": login_navigation.candidate_key(c)} for c in items]

    try:
        value = {
            "system": system, "prompt": prompt, "snapshot": snapshot,
            "scene": native_controls(scene_candidates),
            "window": native_controls(candidates), "selectors": controls, "options": choices,
            "provider": {"tier": str(tier), "name": getattr(provider, "name", ""),
                "model": getattr(provider, "model", ""), "mode": getattr(provider, "mode", ""),
                "policy": getattr(provider, "_policy", {})},
            "fence": [id(entry.get("page")), getattr(entry.get("page"), "url", ""),
                bool(entry.get("authenticated")), str(entry.get("goal_scope") or ""),
                search.get("key"), search.get("source_scope")],
        }
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _collection_observation_payload(result):
    """Retain validated schema fields, never native candidates or reply extras."""
    return copy.deepcopy({
        "scope": result["scope"], "content_status": result["content_status"],
        "contexts": [{k: c[k] for k in
            ("id", "kind", "requested", "current_matches", "matching_options")}
            for c in result["contexts"]],
        "next_control": result["candidate"].get("id", "NONE"),
        "purpose": result["candidate"]["collection_purpose"],
    })


# The smallest window of route controls a too long request is halved to.
_MIN_COLLECTION_WINDOW = 16

# Empty details of one control kind on one list before its other rows are
# no longer opened through that kind.
_EMPTY_DETAILS_PER_KIND = 2


def _empty_detail_kind(seen: dict) -> bool:
    """Enough empty details of one kind, one of them opened in the same tab
    (universality review 21/10: a second tab can render an empty shell)."""
    return (seen.get("in_place", 0) >= 1
            and seen.get("aside", 0) + seen.get("in_place", 0) >= _EMPTY_DETAILS_PER_KIND)


def _needs_in_place_witness(seen: dict) -> bool:
    """Empty only in second tabs so far: the next detail is opened in place."""
    return seen.get("in_place", 0) == 0 and seen.get("aside", 0) >= _EMPTY_DETAILS_PER_KIND


# An answer that is missing or outside the reply contract. A well-formed
# answer the contract refuses (a context used as route, an option or span not
# observed) is a decision, never asked again (R9, R13).
# Waits before asking again a provider that refused the request.
_PROVIDER_RETRY_WAITS_S = (2.0, 6.0)
_COLLECTION_OBSERVATION_RETRY = frozenset({
    "provider_timeout", "provider_error", "invalid_response", "response_limit",
    "invalid_content_status", "invalid_route"})


async def _frontier_collection_observation(entry: dict, request: str,
        candidates: list[dict], controls: list[dict], choices: list[dict],
        snapshot: dict, *, previous_scope=None, scene_candidates=None) -> dict:
    """The semantic decision, asked once more when the answer is missing or
    malformed.

    Such an answer is not proof of a dead end (real run Enel4 21/10/2026: one
    invalid route among 48 decisions ended the collection; Enel2: one provider
    timeout). One more request, never more; a provider refusing the request
    is asked twice more after short waits. Each attempt is audited and
    validated on its own.
    """
    async def attempt():
        return await _frontier_collection_observation_attempt(
            entry, request, candidates, controls, choices, snapshot,
            previous_scope=previous_scope, scene_candidates=scene_candidates)

    result = await attempt()
    if result.get("error_class") and result.get("reason") == "provider_error":
        # A provider refusing requests is often a passing outage: asked
        # again after short waits (real run tp9 21/10/2026: four provider
        # errors in a minute ended the collection on the invoice list).
        for wait in _PROVIDER_RETRY_WAITS_S:
            await asyncio.sleep(wait)
            result = await attempt()
            if not (result.get("error_class") and result.get("reason") == "provider_error"):
                break
    elif result.get("error_class") and result.get("reason") in _COLLECTION_OBSERVATION_RETRY:
        result = await attempt()
    return result


async def _frontier_collection_observation_attempt(entry: dict, request: str,
        candidates: list[dict], controls: list[dict], choices: list[dict],
        snapshot: dict, *, previous_scope=None, scene_candidates=None) -> dict:
    """One semantic authority for record scope, contexts, content and route.

    The legacy local context interpreter is deliberately not a fallback here.
    Only collection work uses this Frontier workload; ordinary actions retain
    their existing routing. No response can grant browser authority.
    """
    import i18n
    import prompt_loader
    from llm_router import LLMRouter
    from llm_workloads import tier_for

    workload = "sites.collection_observe"
    actor = {"actor_type": "unknown"}
    status, phase, reason = "no_items", "collection_observe", "provider_error"
    cache, cache_key, cache_hit = None, None, False
    failure: dict = {}
    started = _monotonic()
    try:
        if len(controls) > collection_observation.MAX_CONTEXTS:
            raise collection_observation.InvalidObservation("context_observation_limit")
        if (len(choices) > collection_observation.MAX_CONTROLS
                or len(candidates) > 2 * collection_observation.MAX_CONTROLS + collection_observation.MAX_CONTEXTS):
            raise collection_observation.InvalidObservation("control_observation_limit")
        routes = [c for c in candidates if c not in controls and c not in choices]
        data = {"request": request, "previous_scope": previous_scope,
                "content": str(snapshot.get("text") or "")[:12000],
                "content_truncated": len(str(snapshot.get("text") or "")) > 12000,
                "selectors": [_collection_control_observation(c) for c in controls],
                "options": [{**_collection_control_observation(c),
                             "selector_ids": collection_observation.option_owners(controls, c)}
                            for c in choices],
                "controls": [{**_collection_control_observation(c),
                              "record_bound": bool(c.get("_record_context_key")), **row,
                              **({"record_detail_observed": collection_evidence.detail_observed(
                                  entry, snapshot.get("context"), c["_record_context_key"])}
                                 if c.get("_record_context_key") else {})}
                             for c, row in zip(routes, _record_context_observations(
                                 routes, snapshot, scene_candidates))]}
        prompt = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if len(prompt) > 64000:
            raise collection_observation.InvalidObservation("prompt_observation_limit")
        tier = tier_for(workload)
        provider = LLMRouter().provider(tier)
        system = prompt_loader.get("agentic_sites_collection_observation_system", i18n.current_lang())
        search = entry.get("collection_search")
        if isinstance(search, dict):
            cache_key = _collection_observation_cache_key(entry, search, provider, tier,
                system, prompt, snapshot, candidates, controls, choices,
                candidates if scene_candidates is None else scene_candidates)
            if cache_key is not None:
                cache = search.setdefault("semantic_observation_cache", {})
        cached = cache.get(cache_key) if cache is not None else None
        if cached is not None:
            payload, actor = copy.deepcopy(cached["payload"]), copy.deepcopy(cached["actor"])
            cache_hit = True
        else:
            if getattr(provider, "mode", "") != "local" and isinstance(search, dict):
                search["cloud_llm_calls"] = int(search.get("cloud_llm_calls") or 0) + 1
            reply = await asyncio.wait_for(asyncio.to_thread(provider.chat, system, prompt,
                max_tokens=collection_observation.MAX_RESPONSE_TOKENS,
                request_timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0),
                timeout=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0)
            actor = sites_audit.model_actor(workload, reply)
            phase, reason = "collection_observe", "invalid_response"
            raw = str(getattr(reply, "text", "") or "")
            if len(raw) > 16384:
                raise collection_observation.InvalidObservation("response_limit")
            payload = json.loads(raw)
        phase, reason = "collection_observe", "invalid_response"
        if isinstance(payload, dict) and payload.get("content_status") in collection_observation.STATUSES:
            status = payload["content_status"]
        phase = "context_grounding"
        result = collection_observation.parse(request, payload, candidates, controls,
                                              choices, previous_scope=previous_scope)
        if cache is not None:
            cache.pop(cache_key, None)
            cache[cache_key] = {"payload": _collection_observation_payload(result),
                                "actor": copy.deepcopy(actor)}
            while len(cache) > _COLLECTION_OBSERVATION_CACHE_LIMIT:
                cache.pop(next(iter(cache)))
        if cache_hit:
            search["semantic_cache_hits"] = int(search.get("semantic_cache_hits") or 0) + 1
        result["candidate"]["_decision_actor"] = actor
        result["context"]["_decision_actor"] = actor
        if result["context"].get("candidate"):
            result["context"]["candidate"] = {
                **result["context"]["candidate"], "_decision_actor": actor}
        reason = "accepted"
        return {**result, "_decision_actor": actor}
    except collection_observation.InvalidObservation as exc:
        phase = exc.phase
        reason = str(exc)
        failure = exc.facets
    except (ValueError, TypeError, KeyError) as exc:
        failure = {"error_type": type(exc).__name__}
    except asyncio.TimeoutError:
        reason = "provider_timeout"
    except Exception as exc:
        # Provider failures remain bounded and inspectable without copying
        # credential-bearing exception strings or page text into the audit:
        # only the exception type and the HTTP status the adapters write in
        # their fixed text, with the provider's closed reason (tp9/tp10
        # 21/10/2026: every request refused within a second, cause not recorded).
        reason = "provider_error"
        failure = {"error_type": type(exc).__name__}
        status_code = re.fullmatch(r"(?:openai|anthropic) api error (\d{3})(?: ([a-z][a-z0-9_]{0,63}))?",
                                   str(exc))
        if status_code:
            failure["http_status"] = int(status_code.group(1))
            if status_code.group(2):
                # The provider's own closed reason (error.code).
                failure["provider_code"] = status_code.group(2)
    finally:
        if cache_hit and reason != "accepted" and cache is not None:
            cache.pop(cache_key, None)
        sites_audit.record("collection_semantic_decision", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            workload=workload, decision_actor=actor, phase=phase, reason=reason,
            candidate_count=len(candidates), content_status=status, cache_hit=cache_hit,
            elapsed_ms=round((_monotonic() - started) * 1000),
            **(failure if reason in {"provider_error", "invalid_route"} else {}))
    return {"error_class": "selector_missing", "phase": phase, "reason": reason,
            "content_status": status, "_decision_actor": actor}


async def _collection_route_decision(entry: dict, target: str,
                                     candidates: list[dict], *, first_visit: bool,
                                     preserve, route_needed: bool = True) -> dict:
    """Content recognition and, when configured, Frontier's separate route.

    The two answer independent questions (a page with invoices can still offer
    unrelated menus), so both requests start together. The observed evidence is
    preserved as soon as it exists, even if routing then fails. An observation
    failure discards the pending route: an unavailable or invalid model answer
    is not proof of a dead end, and no late answer may change the state.
    """
    route = None
    if route_needed and _FRONTIER_COLLECTION_ROUTES and _frontier_routes_configured():
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
            routed = await route
            if routed is None:
                raise ValueError("collection_observation_unresolved")
            # An exhaustive request expands the list before opening details
            # (real turns 7ff3e239, 92f548a3: the local reading named «view
            # all bills» while the route opened one bill after another until
            # the budget ran out). The detail stays available afterwards.
            expand_first = (candidate.get("id")
                and candidate.get("collection_continuation") is True
                and routed.get("collection_purpose") == "detail")
            # A content-aware, observed record detail remains work even when
            # the content-free route observer considers the list exhausted.
            # It still passes the normal action gate; no row-value identity
            # or unobserved link is inferred here.
            detail_pending = (not routed.get("id") and candidate.get("id")
                and candidate.get("collection_purpose") == "detail"
                and candidate.get("_record_context_key")
                and any(c.get("id") == candidate["id"]
                    and c.get("_record_context_key") == candidate["_record_context_key"]
                    for c in candidates))
            if not (expand_first or detail_pending):
                candidate = routed
        return candidate
    finally:
        if route is not None:
            if not route.done():
                route.cancel()
            await asyncio.gather(route, return_exceptions=True)


def _valid_collection_route(choice, purpose, by_id, *, continuation=False):
    """Local and Frontier choices use the same typed, observed route contract."""
    if not isinstance(choice, str) or not isinstance(purpose, str):
        return False
    if choice == "NONE":
        return purpose == "done"
    purposes = {"expand_collection"} if continuation else {
        "navigate", "expand_collection", "detail"}
    return choice != "CURRENT_CONTENT" and choice in by_id and purpose in purposes


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
            if not _valid_collection_route(choice, purpose, by_id):
                outcome = "invalid_response"
                return None
            sites_audit.record(
                "collection_route_selection", owner=entry.get("owner", ""),
                session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
                decision_actor=sites_audit.model_actor("sites.collection_route", reply),
                offset=offset, selected_control=choice, purpose=purpose)
            if choice in by_id:
                outcome = "route"
                return {**by_id[choice],
                        "_decision_actor": sites_audit.model_actor("sites.collection_route", reply),
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
    """Decisione testuale per un passo di navigazione nel mandato.

    Il modello resta locale salvo l'osservazione delle raccolte con Frontier
    configurato. Vede ID e nomi accessibili enumerati dal broker. Per una
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
    navigation_started = time.monotonic() if not collection else 0.0
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
    decision_actor = {"actor_type": "unknown"}

    def record_navigation(outcome, reason, decision_failure,
                          selected_control_valid=False):
        # Counts and fixed reason codes only: never page/model text or IDs.
        sites_audit.record(
            "goal_navigation_decision", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""),
            decision_actor=decision_actor,
            login=bool(login), candidate_count=len(eligible),
            observed_count=len(observed), valid_id_count=len(by_id),
            candidate_limit=limit, observation_complete=observation_complete,
            outcome=outcome, reason=reason, decision_failure=decision_failure,
            selected_control_valid=selected_control_valid,
            elapsed_ms=round((time.monotonic() - navigation_started) * 1000))

    if not observed:
        if not collection:
            record_navigation("inconclusive", "no_observations", "no_observations")
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

    frontier_observation = (collection and not login
        and _FRONTIER_COLLECTION_ROUTES and _frontier_routes_configured())
    workload = "sites.collection_observe" if frontier_observation else "sites.action_reduce"

    def _model_request():
        from llm_router import LLMRouter
        from llm_workloads import tier_for
        provider = LLMRouter().provider(tier_for(workload))
        local = getattr(provider, "mode", "") == "local"
        if not local and not frontier_observation:
            return None
        import i18n
        import prompt_loader
        system_prompt = prompt_loader.get(
            "agentic_sites_action_system", i18n.current_lang(),
            collection=collection, continuation=continuation)
        response_options = {}
        if local and continuation:
            response_options["grammar"] = (
                'root ::= "{" ws "\\\"next_control\\\"" ws ":" ws choice ws "," ws "\\\"purpose\\\"" ws ":" ws purpose ws "}"\n'
                'purpose ::= "\\\"expand_collection\\\"" | "\\\"done\\\""\n'
                'choice ::= ' + ' | '.join(json.dumps(json.dumps(cid)) for cid in by_id if cid != "CURRENT_CONTENT") + '\n'
                'ws ::= [ \\t\\n\\r]*\n')
        elif local and collection:
            # Presence and the next route are independent decisions. The
            # broker derives its internal end-of-branch sentinel below.
            response_options["grammar"] = (
                'root ::= "{" ws "\\\"content_status\\\"" ws ":" ws status ws "," ws "\\\"next_control\\\"" ws ":" ws choice ws "," ws "\\\"purpose\\\"" ws ":" ws purpose ws "}"\n'
                'status ::= "\\\"no_items\\\"" | "\\\"unmatched_items\\\"" | "\\\"matching_items\\\""\n'
                'purpose ::= "\\\"navigate\\\"" | "\\\"expand_collection\\\"" | "\\\"detail\\\"" | "\\\"done\\\""\n'
                'choice ::= ' + ' | '.join(json.dumps(json.dumps(cid)) for cid in by_id if cid != "CURRENT_CONTENT") + '\n'
                'ws ::= [ \\t\\n\\r]*\n')
        return provider, system_prompt, response_options

    decision_failure = ""
    started = time.monotonic()

    async def propose(ctx):
        nonlocal decision_failure, decision_actor
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
            request = _model_request()
            if request is None:
                decision_failure = "provider_error"
                return None
            provider, system_prompt, response_options = request
            search = entry.get("collection_search")
            if getattr(provider, "mode", "") != "local" and isinstance(search, dict):
                # Update on the event loop, shared with concurrent route calls.
                # Count dispatched requests even when the provider then fails.
                search["cloud_llm_calls"] = int(search.get("cloud_llm_calls") or 0) + 1
            result = await asyncio.wait_for(
                asyncio.to_thread(provider.chat, system_prompt, nonlocal_prompt,
                    max_tokens=64,
                    request_timeout_s=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0,
                    **response_options),
                timeout=_LOCAL_RESOLVER_TIMEOUT_MS / 1000.0,
            )
            decision_actor = sites_audit.model_actor(workload, result)
            raw = str(getattr(result, "text", "") or "")
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
            choice, purpose = payload.get("next_control"), payload.get("purpose")
            if not _valid_collection_route(choice, purpose, by_id, continuation=True):
                return None
            description = choice
            by_id[description] = {**by_id[description],
                "collection_purpose": purpose,
                "collection_continuation": purpose == "expand_collection"}
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
            choice, purpose = payload.get("next_control"), payload.get("purpose")
            if not _valid_collection_route(choice, purpose, by_id):
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
                "collection_purpose": purpose,
                "collection_continuation": purpose == "expand_collection",
            }
        decision_failure = ""
        return AgenticProposal(description)

    async def execute(proposal, _ctx):
        candidate = by_id.get(str(proposal.action))
        if candidate is not None:
            candidate["_decision_actor"] = decision_actor
        return candidate

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
            decision_actor=decision_actor,
            workload=workload,
            candidate_count=len(eligible), observation_complete=observation_complete,
            outcome=outcome.status, reason=outcome.reason,
            decision_failure=decision_failure,
            selected_control=str((outcome.result or {}).get("id") or ""),
            collection_observed=bool((outcome.result or {}).get("collection_observed")),
            proximity=str((outcome.result or {}).get("proximity") or "none"),
            elapsed_ms=round((time.monotonic() - started) * 1000))
    else:
        failure = decision_failure or (
            "invalid_response" if outcome.reason == "proposal_rejected" else "")
        record_navigation(
            outcome.status, outcome.reason, failure,
            selected_control_valid=outcome.result is not None)
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
            scope = page
            if (not markers and isinstance(info, dict)
                    and not info.get("found") and not info.get("navigating_only")):
                for frame in await _overlay_frames(page):
                    frame_info = await frame.evaluate(
                        _LOCATE_SAFE_OVERLAY_DISMISS_JS,
                        {"forms": list(allowed_forms), "markers": [],
                         "frameRoot": True})
                    if isinstance(frame_info, dict) and (
                            frame_info.get("found")
                            or frame_info.get("navigating_only")):
                        info, scope = {**frame_info, "framed": True}, frame
                        break
            if isinstance(info, dict) and info.get("found"):
                control = scope.locator(
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
                    framed=bool(info.get("framed")),
                    outcome=True)
                return True
            if isinstance(info, dict) and info.get("navigating_only"):
                # Fix adversarial #11: esiste un controllo di chiusura ma e'
                # NAVIGANTE/submitter → non lo clicchiamo (P5). Lo SEGNALIAMO su
                # entry (osservabile, non stallo silenzioso): un passo navigante
                # passa dal piano firmato + gate F2, mai dalla dismissione sicura.
                entry["navigating_obstruction"] = info.get("control") or {}
                kind = str((info.get("control") or {}).get("kind") or "")
                sites_audit.record(
                    "overlay_navigating_control", owner=entry.get("owner", ""),
                    session_id=entry.get("_sid", ""),
                    domain=entry.get("domain", ""), procedure=procedure,
                    framed=bool(info.get("framed")),
                    href_kind=kind if kind in {
                        "none", "submitter", "javascript_code", "same_origin",
                        "cross_origin", "invalid"} else "other",
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


async def _overlay_frames(page) -> list:
    """Frames whose iframe is the topmost layer over a large viewport area."""
    try:
        if not await page.evaluate(_MARK_OVERLAY_FRAMES_JS):
            return []
        frames = []
        for handle in await page.query_selector_all(
                'iframe[data-metnos-overlay-frame="1"]'):
            frame = await handle.content_frame()
            if frame is not None:
                frames.append(frame)
        return frames
    except Exception:  # noqa: BLE001 - an unreadable frame offers no exit
        return []


async def _record_covering_layers(entry: dict, session_id: str,
                                  candidate: dict) -> None:
    """Audit the closed structure covering a chosen control; never raises."""
    rect = candidate.get("rect") or {}
    try:
        point = {"x": float(rect.get("x", 0)) + float(rect.get("width", 0)) / 2,
                 "y": float(rect.get("y", 0)) + float(rect.get("height", 0)) / 2,
                 "id": str(candidate.get("id") or "")}
        info = await entry["page"].evaluate(_COVERING_LAYERS_JS, point)
    except Exception as exc:  # noqa: BLE001 - diagnostics never decide
        info = {"found": False, "failure": type(exc).__name__}
    evidence = {}
    if not any(entry.get(k) for k in (
            "secret_pending", "factor_pending", "gate_pending",
            "user_control_pending")):
        from . import page_snapshot
        try:
            _sweep_old_shots(entry["owner"])
            evidence = await page_snapshot.save(
                entry["page"], _shots_dir(entry["owner"]))
        except Exception as exc:  # noqa: BLE001
            evidence = {"status": "unavailable", "reason": type(exc).__name__}
    sites_audit.record(
        "collection_covering_layers", owner=entry.get("owner", ""),
        session_id=session_id, domain=entry.get("domain", ""),
        covering=info if isinstance(info, dict) else {"found": False},
        snapshot_status=str((evidence or {}).get("status") or ""))


async def _dismiss_privacy_obstruction(entry: dict, *,
                                       settle: bool = False) -> cookie_privacy.CookieOutcome:
    """Reject a privacy overlay as a bounded precondition for any action.

    Consent belongs to an ORIGIN, not to a session. Crossing to the login
    origin raises that origin's own banner, and the state of the previous one
    says nothing about it: a new origin gets its own click budget, and is worth
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
    # Classification belongs to this resolution attempt, including stale-node
    # and late-panel retries. Cache and click accounting remain per origin.
    classification_budget = {"calls": 0}
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
            enabled=_MODEL_FALLBACKS_ENABLED,
            classification_budget=classification_budget)
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
            frames=outcome.frames, panels=outcome.panels, controls=outcome.controls,
            safe_controls=outcome.safe_controls, rejections=outcome.rejections,
            unsafe_rejections=outcome.unsafe_rejections)
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


def _goal_flow_key(entry: dict, action: str) -> str:
    normalized = action_resolver.normalize(action)
    if entry.get("_record_navigation") is True:
        normalized += "\0records"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _target_changed(entry: dict, detail: str) -> dict:
    """The refusal as before; its closed cause goes to the audit only (live
    run a5, ENI 22/10/2026: a replayed click ended the collection with no
    recorded cause)."""
    sites_audit.record("site_action_refused", session_id=entry.get("_sid", ""),
                       error_class="target_changed", detail=detail)
    return {"ok": False, "error_class": "target_changed"}


async def _prepare_action(entry: dict, session_id: str, action: str,
                          value_ref: str | None, primitive_override: str | None = None,
                          target_override: str | None = None,
                          allow_model: bool = True,
                          login_choice: dict | None = None,
                          collection_choice: dict | None = None) -> dict:
    if entry.get("_navigation_action_active"):
        blocked = await _navigation_challenge(entry)
        if blocked:
            return blocked
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
            return _target_changed(entry, "page_moved_after_reveal")
        observed = await _enumerate_candidates(entry["page"])
        fresh = action_resolver.goal_navigation_candidates(observed)
        matches = [item for item in fresh
                   if login_navigation.candidate_key(item)
                   == login_navigation.candidate_key(expected)
                   and action_resolver.goal_candidate_is_admissible(
                       parsed["target"], item,
                       scope=str(entry.get("goal_scope") or ""))]
        matches = _collapse_collection_links(matches)
        covered = []
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
                # A loading veil hides the modal's own exit: wait for it first.
                await _wait_for_content_settle(entry)
                if await _dismiss_obstructing_overlay(entry, settle=True):
                    # Closing the overlay acted on the page: a later refusal
                    # is no longer free of effects.
                    acted = True
                    if entry["page"] is not page or page.url != location:
                        return _target_changed(entry, "page_moved_by_overlay")
                    observed = await _enumerate_candidates(page)
                    fresh = action_resolver.goal_navigation_candidates(observed)
                    matches = _collapse_collection_links([
                        item for item in fresh
                        if login_navigation.candidate_key(item)
                        == login_navigation.candidate_key(expected)
                        and action_resolver.goal_candidate_is_admissible(
                            parsed["target"], item,
                            scope=str(entry.get("goal_scope") or ""))])
        if len(matches) != 1 and len(covered) == 1:
            # Diagnostics only: describe the covering layer and keep a
            # redacted local page snapshot; the refusal below is unchanged.
            await _record_covering_layers(entry, session_id, covered[0])
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
        if model_selected:
            candidate["_decision_actor"] = sites_audit.actor_metadata(
                expected.get("_decision_actor") or sites_audit.model_actor(""))
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
            return _target_changed(entry, "entry_signature_ambiguous")
        candidate = matches[0]
        confidence = float(login_choice.get("confidence", 0.5))
        model_selected = bool(login_choice.get("model_selected"))
        if model_selected:
            candidate["_decision_actor"] = sites_audit.actor_metadata(
                expected.get("_decision_actor") or sites_audit.model_actor(""))
    elif primitive == "search":
        goal_flow_key = _goal_flow_key(entry, action)
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
                    "collection": (entry.get("_record_navigation") is True
                                   or action_resolver.is_collection_search_request(action)),
                    "collection_attested": False,
                    "collection_facets_visited": set(),
                    "collection_scrolls": 0,
                    "collection_scroll_complete": False}
            flows[goal_flow_key] = flow
        elif (entry.get("_record_navigation") is True
              or action_resolver.is_collection_search_request(action)):
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
                    list(flow.get("history") or ()), excluded,
                    collection=bool(flow.get("collection")), first_visit=False)
                    if allow_model else None)
                # Collection content/NONE sentinels are observations, not
                # controls. Completion still requires the existing page proof.
                if candidate is not None and candidate.get("id"):
                    primitive = "click"
                    plan_kind = "goal_navigation"
                    confidence = 0.5
                    ambiguous = True
                    model_selected = True
                elif (int(flow.get("steps", 0)) > 0
                      and await _page_satisfies_goal(
                          entry, parsed.get("target", ""), candidates)):
                    candidate = None
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
                model_selected = reveal_candidate is not None
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
    if collection_choice is not None:
        # Consent or a closed overlay during preparation is an effect: a later
        # refusal of this plan can no longer be recovered as effect-free.
        plan["preparation_acted"] = bool(acted)
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
                f'[data-metnos-action-id="{candidate.get("id")}"]').first.element_handle(
                    timeout=max(1, _REVEAL_POLL_MS))
        except Exception:
            handle = None
        if handle is None:
            return _target_changed(entry, "reveal_handle_missing")
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
    if plan.get("download_request"):
        grant = (entry.get("task_mandate") or {}).get("download")
        request = plan["download_request"]
        return bool(isinstance(grant, dict) and "download" in operations
                    and grant.get("dst_dir") == request["destination"]["dst_dir"]
                    and all(request["limits"][key] <= grant.get(key, 0)
                            for key in request["limits"]))
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
        # Il mandato non crea autorita': richiede la stessa tupla esatta
        # ancora autorizzata nel vault. Un binding gia' caricato non deve
        # ripristinare un consenso revocato; gli host nudi non sono origini.
        origin = sites_origin.normalize_entry(str(plan.get("exact_origin") or ""))
        origins = {sites_origin.normalize_entry(str(entry_origin))
                   for entry_origin in (binding.get("credential_origins") or ())}
        vault_domain = str(plan.get("vault_domain") or "")
        if ("login" not in operations or not credential_authorized
                or not origin or origin not in origins or not vault_domain):
            return False
        try:
            payload, storage_domain = credential_injection._load_site_credentials(
                vault_domain)
        except Exception:
            return False
        return bool(isinstance(payload, dict) and payload
                    and sites_origin.origin_authorized(
                        origin, payload, storage_domain))

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
        goal_flow_key = _goal_flow_key(entry, action)
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
    """Return once to the landing observed after verified native login.

    This is not a generic retry: it is available once, before any goal step,
    only under an existing mandate that authorizes the exact same-host
    navigation. No page text, vendor label, or guessed URL participates.
    """
    def result(reason: str, *, outcome: bool = False,
               invalidate: bool = False) -> bool:
        if invalidate:
            # A redirect/failed observation is not proof of authentication on
            # the new page. Keep the old anchor, but never reuse stale auth.
            entry["authenticated"] = False
        sites_audit.record(
            "landing_recovery", owner=entry.get("owner", ""),
            session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
            outcome=outcome, reason=reason)
        return outcome

    pending = ("secret_pending", "gate_pending", "factor_pending",
               "user_control_pending")
    if not entry.get("authenticated"):
        return False
    if any(entry.get(key) for key in pending):
        return result("pending_context")
    entry_url = str(entry.get("entry_url") or "")
    origin = sites_origin.origin_of_url(entry_url)
    if not origin:
        return result("anchor_unavailable")
    parts = urllib.parse.urlsplit(entry_url)
    entry_host = _host_of_url(entry_url)
    if (parts.username is not None or parts.password is not None
            or not entry_host
            or entry_host not in set(entry.get("allowlist") or ())):
        return result("anchor_unavailable")
    flow_key = _goal_flow_key(entry, action)
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
        return result("mandate_unavailable")

    # Set before I/O so timeout/failure cannot create a retry loop.
    flow["landing_recovery_attempted"] = True
    blocked = entry.get("blocked_requests")
    if isinstance(blocked, dict):
        blocked.clear()
    page = entry["page"]
    try:
        _resp = await asyncio.wait_for(
            page.goto(
                entry_url, wait_until="load",
                timeout=int(_OP_TIMEOUT_S * 1000)),
            timeout=_OP_TIMEOUT_S)
        # ADR 0191 P4: codice osservativo dell'atterraggio goal (side-channel).
        _sig = sites_observed.response_signals(_resp)
        entry["observed_reason"] = sites_observed.observational_reason(
            status=_sig["status"], retry_after=_sig["retry_after"])
        await _settle_resource_discovery(page)
    except Exception:
        return result("navigation_error", invalidate=True)
    if entry.get("page") is not page:
        return result("page_changed", invalidate=True)
    actual_url = str(getattr(page, "url", "") or "")
    if (sites_origin.origin_of_url(actual_url) != origin
            or entry_host not in set(entry.get("allowlist") or ())):
        return result("origin_changed", invalidate=True)
    if actual_url != entry_url:
        return result("anchor_changed", invalidate=True)
    if any(entry.get(key) for key in pending):
        return result("pending_context", invalidate=True)
    if not _mandate_allows_plan(entry, mandate_probe):
        return result("mandate_unavailable", invalidate=True)
    if _sig["status"] is not None and _sig["status"] >= 400:
        return result("response_rejected", invalidate=True)
    entry["web_content_ingested"] = True
    entry.setdefault("reveal_attempts", set()).clear()
    entry.setdefault("action_replans", {}).clear()
    flow.setdefault("visited", set()).clear()
    flow.setdefault("continuation_exhausted", set()).clear()
    await _touch(entry)
    return result("anchor_reached", outcome=True)


def _same_place(first: str, second: str) -> bool:
    a, b = urllib.parse.urlsplit(first), urllib.parse.urlsplit(second)
    return (a.scheme, a.netloc, a.path) == (b.scheme, b.netloc, b.path)


async def _reopen_address(entry: dict, address: str, action: str) -> str:
    """Open again an address this session reached: same allowed http(s) host,
    under the action's mandate. A page answering an error or a login form
    there is left with Back (an address may be single use: an authorization
    code, a ticket; universality review 21/10/2026). The audited reason."""
    page = entry["page"]
    wanted = urllib.parse.urlsplit(address)
    host = _host_of_url(address)
    probe = {"kind": "goal_navigation", "primitive": "click", "target": action,
             "original_action": action, "destination_host": host,
             "candidate": {"form_method": "", "download": False, "secret_input": False},
             "sensitivity_reasons": ["navigation", "tainted_turn"]}
    if (wanted.scheme not in {"http", "https"} or wanted.username is not None
            or wanted.password is not None or not host
            or host not in set(entry.get("allowlist") or ())):
        return "anchor_unavailable"
    if not _mandate_allows_plan(entry, probe):
        return "mandate_unavailable"
    reason = "reopened"
    try:
        response = await asyncio.wait_for(page.goto(address, wait_until="load",
                                                    timeout=int(_OP_TIMEOUT_S * 1000)),
                                          timeout=_OP_TIMEOUT_S)
        await _settle_resource_discovery(page)
        status = sites_observed.response_signals(response)["status"]
        if ((status is not None and status >= 400) or await _password_in_frames(page)
                or await credential_injection.classify_login_surface(page)):
            reason = "landing_rejected"
            await asyncio.wait_for(page.go_back(wait_until="load",
                                                timeout=int(_OP_TIMEOUT_S * 1000)),
                                   timeout=_OP_TIMEOUT_S)
    except Exception:  # noqa: BLE001 -- the action observes the page itself
        reason = reason if reason == "landing_rejected" else "navigation_error"
    return reason


async def _reopen_login_landing(entry: dict, action: str) -> bool:
    """The first action after a login starts where the login left the page.

    A guard met before it (a cookie panel, the challenge checkbox, an overlay)
    may move the page: real run R42, a restored session met the navigation
    checkbox and the collection began on another page, found nothing and
    stopped. The landing is opened once more before the action does
    anything; never later. True when the page was opened again.
    """
    if not entry.pop("landing_pending", False) or not entry.get("authenticated"):
        return False
    landing = str(entry.get("entry_url") or "")
    if not landing or _same_place(landing, str(getattr(entry["page"], "url", "") or "")):
        return False
    reason = await _reopen_address(entry, landing, action)
    sites_audit.record("landing_reopened", owner=entry.get("owner", ""),
                       session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
                       outcome=reason == "reopened", reason=reason)
    return reason == "reopened"


async def _return_after_challenge(entry: dict, before: str) -> None:
    """An accepted challenge checkbox is not the person's navigation.

    Real run R46: a restored session reached its landing; the challenge then
    appeared on the collection's first page and, once accepted, the site
    showed another page where the search found nothing. When the page moved
    away from an address this session had reached (its login landing or a
    page it observed), that address is opened once more, with the guards of
    `_reopen_address`.
    """
    current = str(getattr(entry["page"], "url", "") or "")
    known = [str(entry.get("entry_url") or "")] + [
        str(item.get("url") or "") for key in ("collected_pages", "collection_observations")
        for item in entry.get(key) or () if isinstance(item, dict)]
    if (not entry.get("authenticated") or not before or _same_place(before, current)
            or not any(address and _same_place(before, address) for address in known)):
        return
    reason = await _reopen_address(entry, before, str(entry.get("goal_scope") or ""))
    sites_audit.record("challenge_return", owner=entry.get("owner", ""),
                       session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
                       outcome=reason == "reopened", reason=reason)


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
            last = _target_changed(entry, "signature_changed_after_reveal")
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
        return _target_changed(entry, "expansion_hosts_changed")
    navigation_url = ""
    if plan.get("login_entry_cross_host"):
        current_url = getattr(entry.get("page"), "url", "")
        current_host = (_host_of_url(current_url)
                        if urllib.parse.urlsplit(current_url).scheme
                        in {"http", "https"} else "")
        if current_host not in hosts:
            if len(hosts) != 1:
                return _target_changed(entry, "expansion_host_ambiguous")
            navigation_url = str((entry.get("blocked_requests") or {})
                                 .get(hosts[0], {}).get("navigation_url") or "")
            if (urllib.parse.urlsplit(navigation_url).scheme
                    not in {"http", "https"}
                    or _host_of_url(navigation_url) != hosts[0]):
                return _target_changed(entry, "expansion_navigation_mismatch")
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
            return _target_changed(entry, "expansion_candidate_ambiguous")
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
        "action_origin": {"actor_type": "executor", "executor": "playwright_sidecar",
                          "source": "site_action"},
        "decision_actor": sites_audit.actor_metadata(
            candidate.get("_decision_actor") or sites_audit.model_actor("")
            if plan.get("model_selected") else {
                "actor_type": "executor", "executor": "playwright_sidecar",
                "source": "deterministic_resolver"}),
        # Collection and login keep the replay flag in different places; the
        # audit reports one flag so a replayed click is never read as a choice.
        "replay": bool(plan.get("collection_replay")
                       or (plan.get("login_choice") or {}).get("replay")),
        "url_before": str(plan.get("page_url") or ""),
    }


async def _apply_interaction_behavior(entry: dict, locator=None) -> None:
    from playwright_sidecar import stealth as _st
    techniques = entry.get("stealth_techniques", ())
    await _st.prepare_interaction(
        entry["page"], locator, techniques=techniques)
    await _st.pause_before_interaction(
        entry["page"], techniques=techniques)


async def _goal_navigation_dom_signature(page, before_url: str, *,
                                         timeout_s: float) -> str:
    """Read bounded, non-interactive content; an unavailable/empty read is no proof."""
    if timeout_s <= 0:
        return ""
    try:
        async with asyncio.timeout(timeout_s):
            snapshot = await page.evaluate(_GOAL_NAVIGATION_DOM_SNAPSHOT_JS)
    except Exception:
        return ""
    if (not isinstance(snapshot, dict)
            or snapshot.get("ready") is not True
            or snapshot.get("busy") is not False
            or not isinstance(snapshot.get("url"), str)
            or _page_signature(snapshot["url"]) != _page_signature(before_url)
            or not isinstance(snapshot.get("evidence"), list)
            or not 0 < len(snapshot["evidence"]) < 400
            or not all(isinstance(value, str) and value.strip()
                       for value in snapshot["evidence"])):
        return ""
    # A heading or a selected control alone is not an answer. Reuse the
    # ordinary goal evidence, which excludes navigation, controls and chrome.
    evidence = [value for value in snapshot["evidence"]
                if not value.startswith("[heading]")]
    if not evidence:
        return ""
    return hashlib.sha256(json.dumps(
        evidence, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


async def _wait_for_goal_navigation_commit(page, before_url: str, *,
                                           timeout_ms: int | None = None,
                                           started_before: dict | None = None,
                                           dom_before: str = "",
                                           observation: dict | None = None,
                                           observe_no_progress: bool = False
                                           ) -> bool:
    """Wait for URL commit or a positive in-place response within the same budget.

    Pending/started document navigation stays on the URL-only path. A DOM
    answer can end only this wait, never certify goal/collection completion.
    Collection discovery may also yield an unchanged, readable DOM to its
    content observation. That is uncertainty, never proof of no effect.
    """
    # Closed booleans only; they do not certify collection completion.
    evidence = {"dom_baseline": bool(dom_before), "requests_quiet_reached": False,
                "document_request_started": False, "dom_sampled": False,
                "dom_changed": False}

    def finish(committed: bool, reason: str) -> bool:
        if observation is not None:
            observation.clear()
            observation["reason"] = reason
            if reason in {"budget_exhausted", "quiet_no_progress"}:
                observation.update(evidence)
        return committed

    budget_ms = (_GOAL_NAVIGATION_COMMIT_MS if timeout_ms is None
                 else max(0, int(timeout_ms)))
    before_sig = _page_signature(before_url)
    started = _monotonic()
    deadline = started + budget_ms / 1000.0
    # Positive evidence of an in-place answer ends the wait early: since the
    # click the main frame requested data, started no document, and has had
    # no request in flight for two reads after the start interval. Without
    # that evidence (a navigation request merely late) the budget applies.
    quiet = 0
    dom_candidate = ""
    while _monotonic() < deadline:
        if _page_signature(getattr(page, "url", "")) != before_sig:
            return finish(True, "url_changed")
        now = credential_injection.requests_started(page)
        quiet = 0 if getattr(page, "_metnos_inflight", None) else quiet + 1
        if (started_before is not None and now is not None
                and now["document"] != started_before["document"]):
            evidence["document_request_started"] = True
        if (started_before is not None and now is not None
                and now["document"] == started_before["document"] and quiet >= 2
                and _monotonic() - started >= _NAVIGATION_START_GRACE_MS / 1000.0):
            evidence["requests_quiet_reached"] = True
            if now["data"] > started_before["data"]:
                return finish(False, "in_place_data")
            if dom_before and not credential_injection.main_document_pending(page):
                current = await _goal_navigation_dom_signature(
                    page, before_url, timeout_s=min(
                        _REVEAL_POLL_MS / 1000.0, max(0, deadline - _monotonic())))
                if current:
                    evidence["dom_sampled"] = True
                    evidence["dom_changed"] |= current != dom_before
                after = credential_injection.requests_started(page)
                # A document may have started while evaluate was awaiting its
                # reply. Recheck the tracker/URL before accepting DOM evidence.
                still_in_place = (
                    after is not None
                    and after["document"] == started_before["document"]
                    and not getattr(page, "_metnos_inflight", None)
                    and not credential_injection.main_document_pending(page)
                    and _page_signature(getattr(page, "url", "")) == before_sig)
                if still_in_place and current:
                    if current == dom_candidate:
                        if current != dom_before:
                            return finish(False, "in_place_dom")
                        if (observe_no_progress
                                and after["data"] == started_before["data"]):
                            return finish(False, "quiet_no_progress")
                    dom_candidate = current
                else:
                    dom_candidate = ""
        else:
            dom_candidate = ""
        await asyncio.sleep(min(
            _REVEAL_POLL_MS / 1000.0, max(0, deadline - _monotonic())))
    committed = _page_signature(getattr(page, "url", "")) != before_sig
    return finish(committed, "url_changed" if committed else "budget_exhausted")


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


async def _collection_pre_dispatch_refusal(entry: dict, plan: dict, page,
                                           handle) -> str | None:
    """Closed reason when the held node is no longer the planned control.

    Runs after the interaction preparation and immediately before the click
    of a collection plan. Identity is the shared derivation of the
    enumeration (role, context and row context included): the held node must
    be physically one of the controls with the planned key, never matched by
    a page-writable attribute or by geometry. Equivalent HTTP copies stay one
    admissible edge, as at preparation, and the handle is never swapped for
    another copy. A residual race until the click itself remains possible.
    """
    def same_page() -> bool:
        return entry.get("page") is page and _page_signature(page.url) == plan.get("page_sig")

    if not same_page():
        return "page_changed"
    observed = await _enumerate_candidates(page, held=handle)
    if not same_page():
        return "page_changed"
    expected = login_navigation.candidate_key(plan.get("candidate") or {})
    held = next((item for item in observed if item.get("_held") is True), None)
    if held is None:
        try:
            connected = await handle.evaluate("node => node.isConnected")
        except Exception:
            connected = False
        # The label only: a connected node absent from the enumeration is
        # refused as well.
        return "identity_changed" if connected is True else "detached"
    if login_navigation.candidate_key(held) != expected:
        return "identity_changed"
    # Competitors are admitted exactly as at preparation; the held node is
    # never excluded for geometry that hover or scrolling may have changed.
    competitors = [item for item in action_resolver.goal_navigation_candidates(observed)
                   if item is not held
                   and login_navigation.candidate_key(item) == expected
                   and action_resolver.goal_candidate_is_admissible(
                       plan.get("target", ""), item,
                       scope=str(entry.get("goal_scope") or ""))]
    if len(_collapse_collection_links([held, *competitors])) != 1:
        return "identity_changed"
    return None


def _pre_dispatch_refusal(entry: dict, plan: dict, page, reason: str) -> dict:
    """The planned click was not invoked; recovery admissibility is explicit.

    ``click_invoked`` concerns the planned click only: preparation, hover or
    focus may already have had effects. Only the same page object at the
    same URL, after a preparation that did not act, leaves the refusal
    recoverable by the collection. The token stays as every pre-click refusal
    leaves it; the collection retires it before any replacement.
    """
    recoverable = (entry.get("page") is page
                   and _page_signature(page.url) == plan.get("page_sig")
                   and plan.get("preparation_acted") is False)
    sites_audit.record(
        "site_action", owner=entry.get("owner", ""),
        session_id=entry.get("_sid", ""), domain=entry.get("domain", ""),
        primitive=plan.get("primitive", ""), target=plan.get("target", ""),
        sensitivity=plan.get("sensitivity_reasons", []),
        outcome=False, reason="pre_dispatch_refused", detail=reason,
        click_invoked=False, pre_dispatch_blocked=True, recoverable=recoverable,
        **_plan_audit_fields(plan))
    return {"ok": False, "error_class": "target_changed", "detail": reason,
            "pre_dispatch_refused": True,
            **({"collection_bind_refused": True} if recoverable else {})}


async def _execute_plan(entry: dict, token: str, plan: dict) -> dict:
    if entry.get("_navigation_action_active"):
        blocked = await _navigation_challenge(entry)
        if blocked:
            if blocked.get("user_control_required"):
                # This plan reached dispatch through its ordinary approval
                # path. Keep the exact plan; only its human wait changes.
                entry["gate_pending"] = False
                plan["_challenge_before_dispatch"] = True
            return blocked
    plan.pop("_challenge_before_dispatch", None)
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
        action_url = (info or {}).get("actionResolved") or ""
        observed = _host_of_url(action_url)
        exact_origin = sites_origin.normalize_entry(
            str(plan.get("exact_origin") or ""))
        if (not info or not info.get("found") or observed != plan.get("target")
                or not exact_origin
                or sites_origin.origin_of_url(action_url) != exact_origin):
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
        # Asked once: the approved exact origin is remembered for this
        # credential (Roberto, 13/10/2026). A failed write only means the
        # question is asked again next time; this login proceeds either way.
        remembered = credential_injection.remember_approved_origin(
            str(plan.get("vault_domain") or ""),
            str(plan.get("exact_origin") or ""))
        sites_audit.record(
            "credential_origin_remembered", owner=entry.get("owner", ""),
            owner_user_id=entry.get("owner_user_id", ""),
            session_id=entry.get("_sid", ""),
            domain=plan.get("vault_domain", ""), origin=observed,
            outcome=bool(remembered))
        return {"ok": True, "executed": True, "approved": True,
                "primitive": "authorize",
                "credential_origin": exact_origin,
                "url": scrub_url(entry["page"].url)}
    # A scope label belongs to the page reached by the last completed goal.
    # Any new effect invalidates it; a collection goal will bind a fresh label
    # after its navigation has actually succeeded.
    if primitive not in {"wait", "observe"}:
        entry.pop("_source_scope_label", None)
    locator = None
    target_aria = _target_aria_state(None)
    if candidate:
        handle = plan.get("element_handle")
        if handle is None:
            return _target_changed(entry, "element_handle_missing")
        try:
            current = await handle.evaluate(_ELEMENT_STATE_JS)
        except Exception:
            current = None
        if _candidate_signature(current) != plan.get("candidate_sig"):
            return _target_changed(entry, "element_signature_changed")
        locator = handle
        target_aria = _target_aria_state(current)
    value_ref = plan.get("value_ref")
    if plan.get("download_request"):
        if locator is None or primitive not in {"click", "submit"}:
            return {"ok": False, "error_class": "download_action_invalid"}
        request = plan["download_request"]
        if request != entry.get("_download_request"):
            return {"ok": False, "error_class": "download_approval_binding_changed"}
        try:
            downloads.verified_scratch()
        except sites_downloads.DownloadError as exc:
            return {"ok": False, "error_class": str(exc)}
        capture = entry.get("downloads")
        if capture is None:
            return {"ok": False, "error_class": "download_capture_unavailable"}
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
    if plan.get("download_request"):
        async def dispatch_download():
            # Consume the exact plan before the first website effect.
            entry["pending_actions"].pop(token, None)
            entry["gate_pending"] = False
            receipt = entry.get("_navigation_receipt")
            if receipt is not None:
                receipt["effect_started"] = True
            await _touch(entry)
            await locator.click(timeout=int(request["limits"]["timeout_s"] * 1000),
                                no_wait_after=True)
        result = await capture.acquire(
            page, locator, action_id=token, binding=request,
            dispatch=dispatch_download, download_limits=request["limits"],
            timeout_s=request["limits"]["timeout_s"])
        result["url"] = scrub_url(page.url)
        return result
    continuation_snapshot = None
    # The failed operation, for diagnostics only: never a retry decision.
    phase = "prepare"
    try:
        if primitive == "wait":
            phase = "dispatch"
            if entry.get("_navigation_receipt") is not None:
                entry["_navigation_receipt"]["effect_started"] = True
            await asyncio.sleep(min(20, max(1, int(plan.get("seconds") or 2))))
        elif primitive == "observe":
            pass
        elif primitive == "goto":
            target_url = plan.get("target") or ""
            if not re.match(r"^https?://", target_url):
                return {"ok": False, "error_class": "invalid_url"}
            phase = "dispatch"
            if entry.get("_navigation_receipt") is not None:
                entry["_navigation_receipt"]["effect_started"] = True
            _resp = await page.goto(target_url, wait_until="load",
                                    timeout=int(_OP_TIMEOUT_S * 1000))
            phase = "after_dispatch"
            # ADR 0191 P4: codice osservativo (side-channel su entry).
            _sig = sites_observed.response_signals(_resp)
            entry["observed_reason"] = sites_observed.observational_reason(
                status=_sig["status"], retry_after=_sig["retry_after"])
            entry["web_content_ingested"] = True
        elif primitive == "fill":
            if (value_ref or "").startswith("cred:"):
                phase = "dispatch"
                if entry.get("_navigation_receipt") is not None:
                    entry["_navigation_receipt"]["effect_started"] = True
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
                phase = "dispatch"
                if entry.get("_navigation_receipt") is not None:
                    entry["_navigation_receipt"]["effect_started"] = True
                await locator.fill(str(value_ref or ""),
                                   timeout=int(_OP_TIMEOUT_S * 1000))
        elif primitive == "search":
            if locator is None:
                return {"ok": False, "error_class": "selector_missing"}
            await _apply_interaction_behavior(entry, locator)
            phase = "dispatch"
            if entry.get("_navigation_receipt") is not None:
                entry["_navigation_receipt"]["effect_started"] = True
            await locator.fill(str(plan.get("target") or ""),
                               timeout=int(_OP_TIMEOUT_S * 1000))
            phase = "prepare"
            await _apply_interaction_behavior(entry, locator)
            phase = "dispatch"
            if entry.get("_navigation_receipt") is not None:
                entry["_navigation_receipt"]["effect_started"] = True
            await locator.press("Enter", timeout=int(_OP_TIMEOUT_S * 1000))
            phase = "after_dispatch"
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
            dom_before = ""
            if (plan.get("kind") == "goal_navigation" and destination_host
                    and credential_injection.requests_started(page) is not None
                    and not getattr(page, "_metnos_inflight", None)
                    and not credential_injection.main_document_pending(page)):
                # Fresh after interaction preparation, before the final
                # collection bind/guard and the one authorized click.
                dom_before = await _goal_navigation_dom_signature(
                    page, page.url, timeout_s=_REVEAL_POLL_MS / 1000.0)
            if plan.get("collection_search"):
                refused = await _collection_pre_dispatch_refusal(
                    entry, plan, page, locator)
                if refused is not None:
                    return _pre_dispatch_refusal(entry, plan, page, refused)
            click_url_before = page.url
            requests_before = credential_injection.requests_started(page)
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
                # Do not let Playwright spend the whole operation waiting
                # for a navigation implicitly. Navigation is observed by
                # the bounded load-state wait immediately below.
                # Once click is invoked its effects may be unknown, including
                # on timeout. Consume only this plan before dispatch so that
                # neither recovery nor repeated approval can click it again.
                # Approval pauses the idle TTL. Resume it from this dispatch,
                # keeping the context and terminal result readable on failure.
                await _touch(entry)
                entry.get("pending_actions", {}).pop(token, None)
                entry["gate_pending"] = False
                phase = "dispatch"
                if entry.get("_navigation_receipt") is not None:
                    entry["_navigation_receipt"]["effect_started"] = True
                # A collection detail may open in a second tab, as the browser
                # does for a link's middle click: unlike a modified click it
                # fires no click event a page script could turn into an
                # in-place navigation (real run R35).
                await locator.click(timeout=_CLICK_TIMEOUT_MS, no_wait_after=True,
                                    **({"button": "middle"} if plan.get("open_aside") else {}))
                phase = "after_dispatch"
                # Un anchor di navigazione goal usa `no_wait_after=True`: non
                # creare subito un waiter DOM mentre il vecchio execution
                # context viene distrutto. Su Chromium questo puo' lasciare un
                # Future Playwright rifiutato dopo che il click e' gia'
                # ritornato. Il commit del top-level viene osservato piu' sotto
                # tramite page.url; con documento non pendente, una risposta
                # DOM positiva puo' solo abbreviare questa attesa. Il normale
                # settle/replan verifica poi obiettivo e contenuto.
                if plan.get("kind") != "goal_navigation":
                    try:
                        await page.wait_for_load_state(
                            "domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                # L'evento popup puo' seguire il ritorno del click; attesa
                # bounded, interrotta appena il listener osserva una pagina.
                if hasattr(context, "on"):
                    popup_wait_ms = (int(_LOGIN_ENTRY_SETTLE_S * 1000)
                                     if (plan.get("login_flow") and candidate
                                         and candidate.get("href")) or plan.get("open_aside")
                                     else 500)
                    for _ in range(0, popup_wait_ms, 50):
                        if observed_pages or page.url != click_url_before:
                            break
                        if hasattr(page, "wait_for_timeout"):
                            await page.wait_for_timeout(50)
                        else:
                            await asyncio.sleep(0.05)
            finally:
                # A successful cleanup keeps the phase of a pending error.
                interrupted_phase, phase = phase, "cleanup"
                if hasattr(context, "remove_listener"):
                    context.remove_listener("page", _record_page)
                phase = interrupted_phase
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
                    # A detail opened aside is a whole site page loading.
                    await popup.wait_for_load_state(
                        "domcontentloaded", timeout=5000 if plan.get("open_aside") else 1500)
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
                waited = _monotonic()
                wait_observation: dict = {}
                committed = await _wait_for_goal_navigation_commit(
                    page, click_url_before, started_before=requests_before,
                    dom_before=dom_before, observation=wait_observation,
                    observe_no_progress=bool(plan.get("collection_search")))
                if plan.get("collection_search"):
                    # A dispatched click without a URL commit must still
                    # prove content progress before discovery can continue.
                    plan["_collection_navigation_unresolved"] = not committed
                after = credential_injection.requests_started(page)
                sites_audit.record(
                    "navigation_commit_wait", owner=entry.get("owner", ""),
                    session_id=entry.get("_sid", ""),
                    domain=entry.get("domain", ""), outcome=bool(committed),
                    reason=wait_observation.get("reason", "observation_unavailable"),
                    **{key: value for key, value in wait_observation.items()
                       if key != "reason" and type(value) is bool},
                    **target_aria,
                    elapsed_ms=round((_monotonic() - waited) * 1000),
                    documents_started=(after["document"] - requests_before["document"]
                                       if after and requests_before else None),
                    data_started=(after["data"] - requests_before["data"]
                                  if after and requests_before else None))
            phase = "commit"
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
        error_diagnostic = action_errors.diagnose(exc, phase)
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
            detail=error_detail, error_diagnostic=error_diagnostic,
            url_after=scrub_url(getattr(entry.get("page"), "url", "")),
            destination_url=str(plan.get("destination_url") or ""),
            blocked_navigation_hosts=blocked_navigation_hosts[:16],
            navigation_trace=list(plan.get("navigation_trace") or ())[:12],
            **_plan_audit_fields(plan))
        return {"ok": False, "error_class": "action_failed",
                "detail": error_detail, "error_diagnostic": error_diagnostic}
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
            if action_resolver.url_place_key(
                    getattr(entry.get("page"), "url", "") or ""
                    ) == str(plan.get("place_before") or ""):
                # If the first observation was unavailable, the bounded
                # wait must establish a baseline and observe a later change.
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
    if entry.get("_download_request"):
        if plan.get("primitive") not in {"click", "submit"}:
            entry["pending_actions"].pop(token, None)
            return {"ok": False, "error_class": "download_action_invalid"}
        plan["download_request"] = entry["_download_request"]
        plan["sensitive"] = True
        plan["sensitivity_reasons"] = list(dict.fromkeys(
            [*plan.get("sensitivity_reasons", ()), "download"]))
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
    remembered = (mandated or (not is_resource_reload and not plan.get("download_request")
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
        elif plan.get("download_request"):
            request = plan["download_request"]
            cap = request["limits"]
            description = _msg("MSG_SITES_DOWNLOAD_APPROVAL", action=description,
                destination=request['destination']['dst_dir'],max_files=cap['max_files'],
                max_file_bytes=cap['max_file_bytes'],max_total_bytes=cap['max_total_bytes'],
                timeout_s=cap['timeout_s'])
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

    `_execute_plan` lascia il token pending quando rifiuta un piano prima
    del dispatch per `target_changed`/`page_changed`. Un click lo consuma prima
    della chiamata Playwright: se il token e' gia' stato consumato, restituiamo
    l'errore senza ripetere l'azione, anche se proviene da un piano successivo.
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
            or result.get("user_control_required")
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
                 value_ref: str | None = None, approval_token: str | None = None,
                 goal_query: str | None = None, done_when: str | None = None,
                 scope: str | None = None, continuation_id: str | None = None,
                 download_request: dict | None = None,
                 record_mode: bool = False) -> dict:
    entry, error = _validate_owned(session_id, owner)
    if entry is None:
        return {"ok": False, "error_class": error}
    binding = {"action": action, "value_ref": value_ref, "goal_query": goal_query,
               "done_when": done_when, "scope": scope}
    if any(v is not None and not isinstance(v, str) for v in binding.values()):
        return {"ok": False, "error_class": "invalid_args"}
    if type(record_mode) is not bool:
        return {"ok": False, "error_class": "invalid_args"}
    binding["record_mode"] = record_mode
    if download_request is not None:
        try:
            destination = download_request["destination"]
            path = destination["dst_dir"]
            if (set(destination) != {"dst_dir", "device", "inode"}
                    or not isinstance(path, str) or not Path(path).is_absolute()
                    or any(isinstance(destination[k], bool) or not isinstance(destination[k], int)
                           for k in ("device", "inode"))):
                raise sites_downloads.DownloadError("download_destination_invalid")
            cap = sites_downloads.limits(download_request["limits"])
            from loader import load_catalog
            catalog = load_catalog()
            executor = catalog.executors.get("act_sites")
            if executor is None:
                raise sites_downloads.DownloadError("download_scope_missing")
            sites_downloads.check_scope(path, actor=entry["owner"], executor=executor)
            download_request = {"destination": dict(destination), "limits": cap}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error_class": str(exc) if isinstance(exc, sites_downloads.DownloadError)
                    else "download_request_invalid"}
        binding["download_request"] = download_request
    async with _session_lock(entry):
        receipts = entry.setdefault("action_continuations", {})
        now = time.time()
        for key, item in list(receipts.items()):
            if now - item["created"] > 3600:
                receipts.pop(key)
        if approval_token:
            cached = entry.get("completed_approvals", {}).get(approval_token)
            approved_binding = cached.get("binding") if cached else None
            if approved_binding is None:
                approved_binding = next((item["binding"] for item in receipts.values()
                    if item.get("approval_token") == approval_token), None)
            # A consumed consent keeps its original action and destination,
            # even when the caller omits or has lost its continuation ID.
            if ((approved_binding is not None and approved_binding != binding)
                    or (cached and approved_binding is None
                        and (download_request is not None or cached["result"].get("download")))):
                return {"ok": False, "error_class": "approval_invalid"}
        if continuation_id is not None:
            record = receipts.get(continuation_id) if isinstance(continuation_id, str) else None
            if not record or record["binding"] != binding:
                return {"ok": False, "error_class": "continuation_invalid"}
            if record["phase"] == "complete":
                return {**record["result"], "continuation_id": continuation_id}
            if record["phase"] == "running":
                return {"ok": False, "error_class": "action_outcome_unknown",
                        "continuation_id": continuation_id}
            approved = record.get("approval_token")
            if approved and approval_token != approved:
                if approval_token is not None:
                    return {"ok": False, "error_class": "approval_invalid"}
                return {**record["result"], "continuation_id": continuation_id}
        else:
            if len(receipts) >= 32:
                # Evicted receipts fail explicitly if presented later.
                receipts.pop(next(iter(receipts)))
            continuation_id = secrets.token_urlsafe(24)
            record = {"binding": binding, "created": now, "phase": "paused"}
            receipts[continuation_id] = record
        if entry.get("gate_pending"):
            entry.pop("landing_pending", None)
        else:
            def anchor():
                # A same-origin document change while the page is checked (a
                # late redirect after a restored session, real run R44) is read
                # once more, as after a consumed navigation; nothing has acted.
                return (entry["page"], sites_origin.origin_of_url(entry["page"].url))
            blocked = await _navigation_challenge(entry, navigation_anchor=anchor())
            if not blocked and await _reopen_login_landing(entry, action):
                blocked = await _navigation_challenge(entry, navigation_anchor=anchor())
            if blocked:
                record["result"] = blocked
                return {**blocked, "continuation_id": continuation_id}
        if "after_effect" in record:
            record.update(phase="complete", result=record.pop("after_effect"))
            return {**record["result"], "continuation_id": continuation_id}
        record["phase"] = "running"
        entry["_navigation_action_active"] = True
        entry["_navigation_receipt"] = record
        if download_request:
            entry["_download_request"] = download_request
        try:
            entry["_record_navigation"] = record_mode
            search = entry.get("collection_search")
            # The approved action may already have run before the challenge.
            # Resume the saved traversal, not that consumed approval token.
            if (search and record.get("result", {}).get("user_control_required")
                    and (search.get("challenge_resume")
                         or not entry.get("pending_actions", {}).get(approval_token))):
                result = await _discover_collection(entry, session_id, search["action"], search["target"])
            else:
                result = await _act_locked(entry, session_id=session_id, owner=owner,
                                          approval_token=approval_token,
                                          **{k: v for k, v in binding.items()
                                             if k not in {"download_request", "record_mode"}})
            cached = entry.get("completed_approvals", {}).get(approval_token)
            if cached is not None and "binding" not in cached:
                cached["binding"] = dict(binding)
            if result.get("approval_required"):
                record.update(phase="paused", result=result,
                              approval_token=result.get("approval_token"))
            elif result.get("user_control_required"):
                record.update(phase="paused", result=result)
                if record.get("effect_started") and not entry.get("collection_search"):
                    # A partially dispatched non-collection action has no safe
                    # resumable cursor. Preserve uncertainty instead of replay.
                    record["after_effect"] = {"ok": False, "error_class": "action_outcome_unknown"}
            else:
                record.update(phase="complete", result=result)
                if result.get("executed"):
                    blocked = await _navigation_challenge(entry)
                    if blocked:
                        record.update(phase="paused", after_effect=result, result=blocked)
            return {**record["result"], "continuation_id": continuation_id}
        finally:
            entry.pop("_record_navigation", None)
            entry.pop("_navigation_action_active", None)
            entry.pop("_navigation_receipt", None)
            entry.pop("_download_request", None)


async def _act_locked(entry: dict, *, session_id: str, owner: str | None, action: str,
                 value_ref: str | None = None,
                 approval_token: str | None = None,
                 goal_query: str | None = None,
                 done_when: str | None = None,
                 scope: str | None = None) -> dict:
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
            if not executed.get("ok") and not executed.get("user_control_required"):
                entry.get("pending_actions", {}).pop(approval_token, None)
                entry["gate_pending"] = False
                entry.pop("collection_search", None)
            executed = await _with_action_failure_evidence(entry, executed)
            entry.setdefault("completed_approvals", {})[approval_token] = {
                "ts": time.time(), "result": dict(executed)}
            return executed
        if (executed.get("error_class") not in {
                "target_changed", "page_changed"}
                or entry.get("pending_actions", {}).get(approval_token) is not plan):
            executed = await _with_action_failure_evidence(entry, executed)
            entry.setdefault("completed_approvals", {})[approval_token] = {
                "ts": time.time(), "result": dict(executed)}
            return executed
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
    if entry.get("_download_request"):
        explicit_goal = None
    elif explicit_goal is not None:
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
    # A linked read/extract consumer needs saved observations even when the
    # elementary action has no exhaustive quantifier. The traversal owns its
    # evidence and partial metadata; the contract never asserts completeness.
    if (goal_target and not value_ref
            and (entry.get("_record_navigation") is True
                 or action_resolver.goal_is_exhaustive(action))):
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
