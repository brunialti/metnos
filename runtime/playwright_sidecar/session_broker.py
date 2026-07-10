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
import re
import secrets
import time
from pathlib import Path

from playwright_sidecar import credential_injection
from playwright_sidecar import redaction
import sites_audit
from sites_url_scrub import scrub_url

try:
    import config as _C  # §7.11
    _SHOTS_ROOT = _C.PATH_USER_DATA / "sites-shots"
except Exception:  # pragma: no cover
    _SHOTS_ROOT = Path.home() / ".local" / "share" / "metnos" / "sites-shots"

# ── Cap di sicurezza (§3.1 FIX C) ──────────────────────────────────────────
_OP_TIMEOUT_S = 20.0            # timeout per singola operazione
_MAX_CONTEXTS = 4              # contesti concorrenti totali
_PER_USER_QUOTA = 2           # sessioni per owner
_TTL_IDLE_S = 15 * 60         # scadenza idle sessione
_SHOT_TTL_S = 30 * 60         # scadenza screenshot su disco
_REAP_INTERVAL_S = 60.0       # cadenza del reaper

# ── Stato globale del broker ───────────────────────────────────────────────
_browser = None                       # impostato da configure()
_sessions: dict[str, dict] = {}       # session_id -> entry
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


def configure(browser) -> None:
    """Chiamato da server._on_startup dopo il launch del Chromium."""
    global _browser
    _browser = browser


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

def _make_route_guard(allowlist: frozenset):
    """Ritorna un handler `context.route` che ABORTA le richieste fuori
    allowlist e la navigazione top-level `data:`/`blob:`."""
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
                host = ""
                m = re.match(r"[a-z]+://([^/:?#]+)", url, re.IGNORECASE)
                if m:
                    host = m.group(1).lower()
                if host in allowlist:
                    await route.continue_()
                else:
                    await route.abort()
                return
            # about:/chrome-error:/blank → lascia passare (pagine locali)
            await route.continue_()
        except Exception:
            # In dubbio: aborta (fail-closed sul confine di rete).
            try:
                await route.abort()
            except Exception:
                pass
    return _guard


def _default_allowlist(url: str, allowlist_arg) -> frozenset:
    """D-D: default = dominio ESATTO dell'url. `allowlist_arg` (lista hostname)
    la sostituisce se fornita (estensione = decisione dell'executor/utente)."""
    hosts = set()
    if allowlist_arg and isinstance(allowlist_arg, list):
        for h in allowlist_arg:
            if isinstance(h, str) and h.strip():
                hosts.add(h.strip().lower())
    m = re.match(r"[a-z]+://([^/:?#]+)", url or "", re.IGNORECASE)
    if m:
        hosts.add(m.group(1).lower())
    return frozenset(hosts)


# ── Validazione sessione (§3.1 FIX A) ──────────────────────────────────────

def _validate(session_id: str) -> dict | None:
    entry = _sessions.get(session_id)
    if entry is None:
        return None
    # FIX B: se gate_pending, il TTL è in pausa → mai considerata scaduta.
    if not entry.get("gate_pending"):
        if time.time() - entry["last_used"] > _TTL_IDLE_S:
            return None
    return entry


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
            if e.get("gate_pending"):
                continue  # TTL in pausa
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
    if _reaper_task is None:
        _reaper_task = asyncio.ensure_future(_reaper_loop())


# ── Operazioni ─────────────────────────────────────────────────────────────

async def op_open(*, owner: str, url: str, allowlist_arg=None,
                  session_label: str = "") -> dict:
    """Apre UNA sessione su `url` (§3.4 open_sites fa fan-out su N url)."""
    if _browser is None:
        return {"ok": False, "error": "browser not ready", "error_class": "unknown"}
    if not isinstance(url, str) or not url:
        return {"ok": False, "error": "url required", "error_class": "invalid_args"}
    # Cap globale + quota per-owner (FIX C).
    if len(_sessions) >= _MAX_CONTEXTS:
        return {"ok": False, "error": "max concurrent sessions reached",
                "error_class": "capacity"}
    owner_count = sum(1 for e in _sessions.values() if e.get("owner") == owner)
    if owner_count >= _PER_USER_QUOTA:
        return {"ok": False, "error": "per-user session quota reached",
                "error_class": "quota_exceeded"}

    allowlist = _default_allowlist(url, allowlist_arg)
    context = await _browser.new_context(
        user_agent="metnos-sites/1.0 (+metnos@metnos.com) playwright",
        viewport={"width": 1280, "height": 800},
    )
    # FIX D: WebRTC off + route-guard per-sessione.
    try:
        await context.add_init_script(_WEBRTC_OFF_JS)
        await context.route("**/*", _make_route_guard(allowlist))
    except Exception as e:
        await context.close()
        return {"ok": False, "error": f"context setup failed: {e}",
                "error_class": "unknown"}

    page = await context.new_page()
    try:
        await asyncio.wait_for(
            page.goto(url, wait_until="load", timeout=int(_OP_TIMEOUT_S * 1000)),
            timeout=_OP_TIMEOUT_S)
    except asyncio.TimeoutError:
        await context.close()
        return {"ok": False, "error": "navigation timeout", "error_class": "timeout"}
    except Exception as e:
        await context.close()
        return {"ok": False, "error": f"navigation failed: {e}",
                "error_class": "network"}

    session_id = secrets.token_hex(16)
    m = re.match(r"[a-z]+://([^/:?#]+)", url, re.IGNORECASE)
    domain = m.group(1).lower() if m else ""
    _sessions[session_id] = {
        "context": context, "page": page, "allowlist": allowlist,
        "owner": owner, "domain": domain, "label": session_label or "",
        "created": time.time(), "last_used": time.time(),
        "gate_pending": False, "authenticated": False,
        "lock": asyncio.Lock(),
    }
    try:
        title = await page.title()
    except Exception:
        title = ""
    sites_audit.record("session_open", owner=owner, session_id=session_id,
                       domain=domain, url=page.url, allowlist=sorted(allowlist))
    return {"ok": True, "session_id": session_id, "url": scrub_url(page.url),
            "title": title}


async def _capture_screenshot(entry: dict) -> str | None:
    """Cattura uno screenshot REDATTO (§3.3). Ritorna il path (0600) o None.
    §3.2 CRITICO-3: la redazione avviene PRIMA del capture; se fallisce, NON
    si cattura (fail-closed)."""
    page = entry["page"]
    owner = entry["owner"]
    redacted = await redaction.apply_redaction(page)
    if redacted < 0:
        return None  # redazione fallita → mai catturare (fail-closed)
    _sweep_old_shots(owner)
    d = _shots_dir(owner)
    fname = f"{entry.get('_sid','s')}_{int(time.time()*1000)}.png"
    path = d / fname
    try:
        await page.screenshot(path=str(path), full_page=False)
        path.chmod(0o600)
    except Exception:
        return None
    return str(path)


async def op_read(*, session_id: str, include_screenshot: bool = True,
                  include_forms: bool = False) -> dict:
    entry = _validate(session_id)
    if entry is None:
        return {"ok": False, "error": "session lost or expired",
                "error_class": "session_lost"}
    async with entry["lock"]:
        try:
            return await asyncio.wait_for(
                _read_impl(entry, session_id, include_screenshot, include_forms),
                timeout=_OP_TIMEOUT_S)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "read timeout", "error_class": "timeout"}


async def _read_impl(entry, session_id, include_screenshot, include_forms) -> dict:
    page = entry["page"]
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
    sensitive = bool(entry.get("authenticated"))
    shot = None
    if include_screenshot:
        shot = await _capture_screenshot(entry)
    out = {
        "ok": True, "session_id": session_id, "url": scrub_url(page.url),
        "title": title, "text": text, "sensitive": sensitive,
    }
    if shot:
        out["screenshot_path"] = shot
    return out


async def op_screenshot(*, session_id: str) -> dict:
    entry = _validate(session_id)
    if entry is None:
        return {"ok": False, "error": "session lost or expired",
                "error_class": "session_lost"}
    async with entry["lock"]:
        entry["_sid"] = session_id
        await _touch(entry)
        shot = await _capture_screenshot(entry)
        if not shot:
            return {"ok": False, "error": "capture failed",
                    "error_class": "screenshot_failed"}
        return {"ok": True, "session_id": session_id, "screenshot_path": shot,
                "sensitive": bool(entry.get("authenticated"))}


async def op_login(*, session_id: str, domain: str | None = None,
                   form_hint: str | None = None) -> dict:
    entry = _validate(session_id)
    if entry is None:
        return {"ok": False, "logged_in": False, "reason_code": "session_lost",
                "error": "session lost or expired", "error_class": "session_lost"}
    # domain default = origine della sessione (verificata poi in §3.2).
    dom = (domain or entry.get("domain") or "").lower()
    async with entry["lock"]:
        await _touch(entry)
        # Il TTL è in pausa durante il login (può attendere navigazioni lente).
        entry["gate_pending"] = True
        try:
            res = await asyncio.wait_for(
                credential_injection.perform_login(
                    page=entry["page"], context=entry["context"], domain=dom,
                    form_hint=form_hint, owner=entry["owner"],
                    session_id=session_id, op_timeout_s=_OP_TIMEOUT_S),
                timeout=_OP_TIMEOUT_S * 3)  # login = più operazioni + navigazioni
        except asyncio.TimeoutError:
            res = {"ok": True, "logged_in": False, "reason_code": "login_timeout",
                   "error_class": "timeout"}
        finally:
            entry["gate_pending"] = False
            await _touch(entry)
        if res.get("logged_in"):
            entry["authenticated"] = True
        res["session_id"] = session_id
        return res


async def op_close(*, session_id: str | None = None, owner: str | None = None,
                   close_all: bool = False) -> dict:
    """Chiude UNA sessione o TUTTE quelle dell'owner (§9 kill-switch)."""
    closed = []
    if close_all:
        for sid, e in list(_sessions.items()):
            if owner is None or e.get("owner") == owner:
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
    if owner is not None and e.get("owner") != owner:
        # Non chiudere sessioni di un altro owner: rimetti e rifiuta.
        _sessions[session_id] = e
        return {"ok": False, "error": "not owner", "error_class": "forbidden"}
    await _close_entry(e)
    sites_audit.record("session_close", owner=e.get("owner", ""),
                       session_id=session_id, domain=e.get("domain", ""))
    return {"ok": True, "closed": [session_id], "count": 1}
