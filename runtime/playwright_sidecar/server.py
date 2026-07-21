#!/usr/bin/env python3
"""Sidecar HTTP server per JS-rendering via Playwright headless Chromium.

ADR 0125, Phase 1.

Endpoint:
    GET  /health      -> {"ok": true, "browser": "chromium", "version": "..."}
    POST /render      -> {"ok": true, "body_text", "body_html", "title",
                          "final_url", "render_ms"}
                      o {"ok": false, "error", "error_class"}

Body POST /render: {"url": str, "wait_ms"?: int=2000, "viewport"?: {w, h}}.

Design:
    - aiohttp server async, single-process, single-browser.
    - Un solo `browser` Chromium persistente (~200MB RAM); ogni request
      apre un nuovo `context` (isolamento cookie/storage) + `page`.
    - Timeout hard 30s per page-load + render-wait combinato.
    - Fail-loud §2.8: tutti i path d'errore producono dict esplicito,
      mai eccezioni silenziose.

Avvio (NON enable di default — Roberto avvia manualmente):
    python -m playwright_sidecar.server --host 127.0.0.1 --port 8771

Requisiti runtime:
    pip install -r runtime/playwright_sidecar/requirements.txt
    playwright install chromium     # ~300MB download

Vincoli:
    - §7.1 no shim: se playwright manca, il server fa exit 1 immediato
      al main() con errore chiaro. Niente fallback fittizio.
    - §7.4 single-instance: NIENTE pool di browser/context paralleli.
      Throughput limitato di proposito (sidecar e' opt-in §7.9 bordo).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time

from aiohttp import web

from playwright_sidecar import contract as _contract

logger = logging.getLogger("playwright_sidecar")


def _contract_json_response(payload: dict, *, status: int) -> web.Response:
    """Return a contract-labelled response, including mismatch failures."""
    return web.json_response(
        payload, status=status,
        headers={_contract.HEADER_NAME: _contract.LOADED_FINGERPRINT})


def _protected_contract_path(path: str) -> bool:
    return path == "/render" or path.startswith("/session/")


@web.middleware
async def _contract_middleware(request: web.Request, handler):
    """Fail closed before browser work when client/server sources diverge."""
    if _protected_contract_path(request.path):
        status = _contract.source_status()
        if not status["contract_aligned"]:
            return _contract_json_response(
                _contract.failure("sidecar_source_stale", process="sidecar"),
                status=503)
        received = request.headers.get(_contract.HEADER_NAME)
        if not _contract.same_fingerprint(
                received, _contract.LOADED_FINGERPRINT):
            return _contract_json_response(
                _contract.failure(
                    "client_sidecar_contract_mismatch",
                    peer_fingerprint=received or "missing",
                    process="sidecar"),
                status=409)

    response = await handler(request)
    response.headers[_contract.HEADER_NAME] = _contract.LOADED_FINGERPRINT
    return response

# Stato globale browser. HONEST = `_browser` (default onesto, sempre pronto).
# Le varianti non-default sono lazy: headless+LAUNCH, side, side+LAUNCH.
# Owner ESCLUSIVO di Playwright/browser = questo modulo (B1): il broker riceve
# un provider (`_get_browser`) e non lancia mai.
_browser = None
_browser_stealth = None
_browser_side = None
_browser_side_stealth = None
_stealth_launch_lock = None   # asyncio.Lock(), creato in _on_startup
_playwright = None
_browser_version = ""
_browser_ready_since = 0.0
_browser_generation = 0
_watchdog_task = None
_stopping = False

# Argomenti di lancio del browser HONEST: nessun flag anti-rilevamento. Il
# browser stealth (lazy) parte da questi + le tecniche LAUNCH di `stealth.py`.
_HONEST_LAUNCH_ARGS = [
    "--no-sandbox",  # systemd-user gia' contiene
    "--disable-dev-shm-usage",
    # spec sites §3.1 FIX D: WebRTC off a livello browser (difesa in profondita'
    # oltre all'init-script per-contesto).
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
]

# Cap di sicurezza: timeout assoluto su una singola render. Sopra di questo,
# preferiamo dichiarare timeout che restare appesi (ADR 0125 §G).
_RENDER_TIMEOUT_HARD_S = 30.0
# Wait dopo `goto` per dare al JS il tempo di idratare la pagina.
_DEFAULT_WAIT_MS = 2000
_MAX_WAIT_MS = 15000
_BROKER_REQUEST_TIMEOUT_S = 85.0
_BROKER_LOGIN_TIMEOUT_S = 135.0

# Viewport di default (desktop FullHD-ish).
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


def _is_connected(browser) -> bool:
    if browser is None:
        return False
    try:
        probe = getattr(browser, "is_connected", None)
        return bool(probe() if callable(probe)
                    else probe if probe is not None else True)
    except Exception:
        return False


def _browser_connected() -> bool:
    # Honest browser: sempre atteso pronto.
    return _is_connected(_browser)


def _side_browser_available() -> bool:
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


async def _get_browser(browser_mode: str, launch_stealth: bool):
    """Ritorna la variante esatta richiesta, lanciandola lazy.

    `headless` usa il browser base o la variante LAUNCH stealth. `side` usa il
    Chromium completo grafico, pilotato da Playwright, con una variante distinta
    quando e' selezionato WebDriver. Nessun fallback fra superfici.
    """
    global _browser_stealth, _browser_side, _browser_side_stealth
    if browser_mode not in {"headless", "side"}:
        raise RuntimeError("invalid_browser_mode")
    if browser_mode == "headless" and not launch_stealth:
        if not _browser_connected():
            raise RuntimeError("browser_unavailable")
        return _browser
    if browser_mode == "side" and not _side_browser_available():
        raise RuntimeError("side_browser_display_unavailable")

    current = (_browser_stealth if browser_mode == "headless"
               else _browser_side_stealth if launch_stealth
               else _browser_side)
    if _is_connected(current):
        return current
    lock = _stealth_launch_lock
    if lock is None or _playwright is None:
        raise RuntimeError("browser_unavailable")
    async with lock:
        current = (_browser_stealth if browser_mode == "headless"
                   else _browser_side_stealth if launch_stealth
                   else _browser_side)
        if _is_connected(current):
            return current
        from playwright_sidecar import stealth as _stealth_mod
        args = list(_HONEST_LAUNCH_ARGS)
        if launch_stealth:
            _stealth_mod.apply_launch_args(
                args, techniques=("webdriver_launch_arg",))
        try:
            browser = await _playwright.chromium.launch(
                headless=(browser_mode == "headless"), args=args)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s chromium launch failed (launch_stealth=%s): %s",
                         browser_mode, launch_stealth, exc)
            error = ("side_browser_unavailable" if browser_mode == "side"
                     else "browser_unavailable")
            raise RuntimeError(error) from exc
        if hasattr(browser, "on"):
            browser.on("disconnected", _browser_disconnected)
        if browser_mode == "headless":
            _browser_stealth = browser
        elif launch_stealth:
            _browser_side_stealth = browser
        else:
            _browser_side = browser
        logger.info("%s chromium launched (lazy, launch_stealth=%s)",
                    browser_mode, launch_stealth)
        return browser


def _broker_health_snapshot() -> dict:
    try:
        from playwright_sidecar import session_broker
        snapshot = session_broker.health_snapshot()
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


def _sd_notify(message: str) -> bool:
    """Send an sd_notify datagram without adding a systemd Python dependency."""
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
        return True
    except OSError as exc:
        logger.warning("sd_notify failed: %s", exc)
        return False


def _watchdog_interval_s() -> float | None:
    try:
        usec = int(os.environ.get("WATCHDOG_USEC", "0"))
    except ValueError:
        return None
    if usec <= 0:
        return None
    return max(1.0, min(20.0, usec / 2_000_000.0))


async def _watchdog_loop() -> None:
    interval = _watchdog_interval_s()
    if interval is None:
        return
    while True:
        await asyncio.sleep(interval)
        if not _browser_connected():
            _sd_notify("STATUS=Chromium disconnected; waiting for restart")
            return
        if not _broker_health_snapshot().get("reaper_running"):
            logger.critical("session reaper stopped; waiting for watchdog restart")
            _sd_notify("STATUS=Session reaper stopped; waiting for restart")
            return
        _sd_notify("WATCHDOG=1")


async def _terminate_after_disconnect() -> None:
    """Lose all invalid contexts cleanly, then let systemd restart us."""
    global _browser
    if _stopping:
        return
    logger.critical("chromium disconnected unexpectedly; restarting sidecar")
    _browser = None
    _sd_notify("STATUS=Chromium disconnected; restarting")
    try:
        from playwright_sidecar import session_broker
        await session_broker.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("broker cleanup after disconnect failed: %s", exc)
    os.kill(os.getpid(), signal.SIGTERM)


def _browser_disconnected() -> None:
    if _stopping:
        return
    try:
        asyncio.get_running_loop().create_task(_terminate_after_disconnect())
    except RuntimeError:
        logger.critical("chromium disconnected without a running event loop")


def _classify_playwright_error(exc: BaseException) -> str:
    """Mappa eccezioni Playwright -> error_class deterministica.

    Coerente con `read_urls_html._classify_*` (ADR 0101).
    """
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "net::err_name_not_resolved" in msg or "name not resolved" in msg:
        return "network"
    if "net::err_connection_refused" in msg or "connection refused" in msg:
        return "network"
    if "net::err_aborted" in msg:
        return "network"
    if "403" in msg or "forbidden" in msg:
        return "forbidden"
    if "404" in msg or "not_found" in msg or "not found" in msg:
        return "not_found"
    if "5" in msg and "server" in msg:
        return "server_error"
    return "unknown"


async def handle_health(request: web.Request) -> web.Response:
    """Probe per `client.is_up()`. Ritorna 200 se browser e' pronto."""
    contract_status = _contract.source_status()
    if not contract_status["contract_aligned"]:
        return web.json_response({
            **_contract.failure("sidecar_source_stale", process="sidecar"),
        }, status=503)
    if not _browser_connected():
        return web.json_response(
            {"ok": False, "error": "browser not connected",
             "error_class": "sidecar_down", **contract_status},
            status=503,
        )
    out = {
        "ok": True,
        "browser": "chromium",
        "version": _browser_version,
        "generation": _browser_generation,
        "uptime_s": max(0, int(time.monotonic() - _browser_ready_since)),
        # ADR 0191 P1/C3: stato separato delle varianti browser.
        "browser_honest_connected": _browser_connected(),
        "browser_stealth_state": (
            "not_started" if _browser_stealth is None
            else "connected" if _is_connected(_browser_stealth)
            else "disconnected"),
        "browser_side_state": (
            "not_started" if _browser_side is None
            else "connected" if _is_connected(_browser_side)
            else "disconnected"),
        "browser_side_stealth_state": (
            "not_started" if _browser_side_stealth is None
            else "connected" if _is_connected(_browser_side_stealth)
            else "disconnected"),
        "side_browser_available": _side_browser_available(),
        **contract_status,
    }
    out["broker"] = _broker_health_snapshot()
    if not out["broker"].get("reaper_running"):
        out.update({"ok": False, "error": "session reaper not running",
                    "error_class": "sidecar_degraded"})
        return web.json_response(out, status=503)
    return web.json_response(out)


async def handle_render(request: web.Request) -> web.Response:
    """POST /render: renderizza JS su una pagina e ritorna HTML finale."""
    if not _browser_connected():
        return web.json_response(
            {"ok": False, "error": "browser not initialized",
             "error_class": "unknown"},
            status=503,
        )

    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return web.json_response(
            {"ok": False, "error": f"invalid json body: {e}",
             "error_class": "unknown"},
            status=400,
        )

    url = body.get("url")
    if not isinstance(url, str) or not url:
        return web.json_response(
            {"ok": False, "error": "url required (str)",
             "error_class": "unknown"},
            status=400,
        )

    wait_ms = int(body.get("wait_ms", _DEFAULT_WAIT_MS))
    if wait_ms < 0 or wait_ms > _MAX_WAIT_MS:
        wait_ms = _DEFAULT_WAIT_MS

    viewport = body.get("viewport") or _DEFAULT_VIEWPORT
    if not isinstance(viewport, dict):
        viewport = _DEFAULT_VIEWPORT
    vw = int(viewport.get("w") or viewport.get("width") or 1280)
    vh = int(viewport.get("h") or viewport.get("height") or 800)

    t0 = time.time()
    context = None
    page = None
    try:
        context = await _browser.new_context(
            viewport={"width": vw, "height": vh},
            user_agent="metnos-crawler/1.2 (+metnos@metnos.com) playwright",
        )
        page = await context.new_page()
        # Hard cap: navigation + wait combinati non eccedano il timeout.
        # goto wait_until="load" attende l'evento load (DOM + assets sync).
        try:
            await asyncio.wait_for(
                page.goto(url, wait_until="load",
                          timeout=int(_RENDER_TIMEOUT_HARD_S * 1000)),
                timeout=_RENDER_TIMEOUT_HARD_S,
            )
        except asyncio.TimeoutError:
            return web.json_response(
                {"ok": False,
                 "error": f"timeout after {_RENDER_TIMEOUT_HARD_S}s on goto",
                 "error_class": "timeout"},
                status=200,
            )

        # Wait extra per JS post-load (idratazione SPA).
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000.0)

        final_url = page.url
        title = await page.title()
        body_html = await page.content()
        # `inner_text` del `body` raccoglie il testo visibile (skip script/style).
        try:
            body_text = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            body_text = ""
        render_ms = int((time.time() - t0) * 1000)
        return web.json_response({
            "ok": True,
            "body_text": body_text,
            "body_html": body_html,
            "title": title,
            "final_url": final_url,
            "render_ms": render_ms,
        })
    except Exception as e:
        return web.json_response(
            {"ok": False,
             "error": f"{type(e).__name__}: {e}",
             "error_class": _classify_playwright_error(e)},
            status=200,
        )
    finally:
        # Best-effort cleanup. Le eccezioni qui sono mute di proposito
        # (siamo gia' nella response; un context-leak non e' user-facing
        # ma viene tracciato dal logger del processo).
        if page is not None:
            try:
                await page.close()
            except Exception as e:
                logger.debug("page.close failed: %s", e)
        if context is not None:
            try:
                await context.close()
            except Exception as e:
                logger.debug("context.close failed: %s", e)


# ── Session-broker endpoints (spec sites §3.1) ─────────────────────────────
# Thin HTTP wrapper attorno a `session_broker`. Tutta la logica di sicurezza
# (registry, TTL, route-guard, iniezione credenziali, redazione) vive nel
# broker; qui si fa solo parse-body → op → json_response.

async def _broker_call(request, opname, *,
                       timeout_s: float = _BROKER_REQUEST_TIMEOUT_S):
    if not _browser_connected():
        return web.json_response(
            {"ok": False, "error": "browser not initialized",
             "error_class": "unknown"}, status=503)
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return web.json_response(
            {"ok": False, "error": f"invalid json body: {e}",
             "error_class": "invalid_args"}, status=400)
    try:
        from playwright_sidecar import session_broker
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {"ok": False, "error": f"session_broker unavailable: {e}",
             "error_class": "unknown"}, status=503)
    try:
        res = await asyncio.wait_for(
            opname(session_broker, body), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.error("broker operation timed out after %.0fs",
                     timeout_s)
        return web.json_response(
            {"ok": False, "error": "broker operation timeout",
             "error_class": "timeout"}, status=504)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        # Do not expose exception text: a browser/DOM exception can contain a
        # URL or page-provided text. The traceback remains in local journald.
        logger.exception("unhandled session broker operation failure")
        return web.json_response(
            {"ok": False, "error": "session broker internal failure",
             "error_class": "sidecar_internal"}, status=500)
    if not isinstance(res, dict):
        logger.error("session broker returned %s instead of dict",
                     type(res).__name__)
        return web.json_response(
            {"ok": False, "error": "invalid broker response",
             "error_class": "sidecar_internal"}, status=500)
    return web.json_response(res)


async def handle_session_open(request):
    async def _op(sb, b):
        return await sb.op_open(
            owner=b.get("owner"), url=b.get("url", ""),
            allowlist_arg=b.get("allowlist"), session_label=b.get("session_label", ""),
            approval_token=b.get("approval_token"),
            task_name=b.get("task_name"),
            credential_mode=b.get("credential_mode", "default"),
            # Fix adversarial #8: solo bool VERO attiva lo stealth (bool("false")
            # sarebbe True). Un JSON malformato/ambiguo → honest.
            stealth=(b.get("stealth") is True),
            stealth_techniques=b.get("stealth_techniques"),
            browser_mode=b.get("browser_mode", "headless"),
            lang=b.get("lang"))
    return await _broker_call(request, _op)


async def handle_session_read(request):
    async def _op(sb, b):
        return await sb.op_read(
            session_id=b.get("session_id", ""),
            owner=b.get("owner"),
            include_screenshot=bool(b.get("include_screenshot", True)),
            include_forms=bool(b.get("include_forms", False)))
    return await _broker_call(request, _op)


async def handle_session_screenshot(request):
    async def _op(sb, b):
        return await sb.op_screenshot(session_id=b.get("session_id", ""),
                                      owner=b.get("owner"))
    return await _broker_call(request, _op)


async def handle_session_login(request):
    async def _op(sb, b):
        return await sb.op_login(
            session_id=b.get("session_id", ""), owner=b.get("owner"),
            domain=b.get("domain"),
            form_hint=b.get("form_hint"),
            approval_token=b.get("approval_token"),
            one_time_code=b.get("one_time_code"),
            credential_mode=b.get("credential_mode", "default"))
    return await _broker_call(
        request, _op, timeout_s=_BROKER_LOGIN_TIMEOUT_S)


async def handle_session_close(request):
    async def _op(sb, b):
        return await sb.op_close(
            session_id=b.get("session_id"), owner=b.get("owner"),
            close_all=bool(b.get("all", False)))
    return await _broker_call(request, _op)


async def handle_session_act(request):
    async def _op(sb, b):
        return await sb.op_act(
            session_id=b.get("session_id", ""), owner=b.get("owner"),
            action=b.get("action", ""), value_ref=b.get("value_ref"),
            approval_token=b.get("approval_token"),
            goal_query=b.get("goal_query"))
    return await _broker_call(request, _op)


def _primitive_handler(opname, *, value_key=None):
    async def _handler(request):
        async def _op(sb, b):
            kwargs = {
                "session_id": b.get("session_id", ""),
                "owner": b.get("owner"),
                "value_ref": b.get("value_ref"),
                "approval_token": b.get("approval_token"),
            }
            if value_key:
                kwargs[value_key] = b.get(value_key)
            return await getattr(sb, opname)(**kwargs)
        return await _broker_call(request, _op)
    return _handler


handle_session_goto = _primitive_handler("op_goto", value_key="url")
handle_session_click = _primitive_handler("op_click", value_key="target")
handle_session_fill = _primitive_handler("op_fill", value_key="target")
handle_session_submit = _primitive_handler("op_submit", value_key="target")
handle_session_wait = _primitive_handler("op_wait", value_key="seconds")


async def _on_startup(app: web.Application) -> None:
    """Inizializza Playwright + Chromium browser."""
    global _browser, _playwright, _browser_version, _browser_ready_since
    global _browser_generation, _watchdog_task, _stopping, _stealth_launch_lock
    _stopping = False
    if not _contract.source_status()["contract_aligned"]:
        logger.critical("Playwright source changed during sidecar startup")
        raise SystemExit(1)
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        # §2.8 fail-loud: niente fallback. Roberto sceglie quando installare.
        logger.error("playwright import failed: %s — run "
                     "`runtime/playwright_sidecar/install.sh`", e)
        raise SystemExit(1)

    _playwright = await async_playwright().start()
    _stealth_launch_lock = asyncio.Lock()
    # Browser HONEST (default onesto, ADR 0191): nessun flag anti-rilevamento.
    # `navigator.webdriver` resta nativo. Le altre combinazioni superficie/layer
    # LAUNCH sono lazy in `_get_browser` e cambiano per-sessione dalla UI Website
    # browsing senza restart.
    launch_args = list(_HONEST_LAUNCH_ARGS)
    last_error = None
    for attempt in range(1, 4):
        try:
            _browser = await _playwright.chromium.launch(
                headless=True, args=launch_args)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("chromium launch attempt %d/3 failed: %s",
                           attempt, exc)
            if attempt < 3:
                await asyncio.sleep(0.5 * attempt)
    if _browser is None:
        logger.error("chromium launch failed after 3 attempts: %s", last_error)
        await _playwright.stop()
        _playwright = None
        raise SystemExit(1)

    try:
        _browser_version = _browser.version
        _browser_ready_since = time.monotonic()
        _browser_generation += 1
        if hasattr(_browser, "on"):
            _browser.on("disconnected", _browser_disconnected)
        # spec sites §3.1: registra il browser nel session-broker + avvia il
        # reaper (TTL idle, salta gate_pending). Import lazy: il sidecar resta
        # avviabile anche senza il modulo (degrade graceful del solo /render).
        try:
            from playwright_sidecar import session_broker
            # B1: il broker riceve un PROVIDER, non un browser. Non lancia mai.
            session_broker.configure(_get_browser)
            session_broker.start_reaper()
            logger.info("session_broker configured (sites domain ready)")
        except Exception as e:  # noqa: BLE001
            logger.error("session_broker not available: %s", e)
            _stopping = True
            await _browser.close()
            _browser = None
            await _playwright.stop()
            _playwright = None
            raise SystemExit(1)
        _sd_notify("READY=1\nSTATUS=Chromium ready")
        _watchdog_task = asyncio.create_task(_watchdog_loop())
        logger.info("playwright chromium %s ready", _browser_version)
    except Exception as e:
        # Tipico: `playwright install chromium` non eseguito.
        logger.error("chromium launch failed: %s — run `playwright install "
                     "chromium`", e)
        await _playwright.stop()
        raise SystemExit(1)


async def _on_shutdown(app: web.Application) -> None:
    """Chiusura pulita di tutte le varianti browser avviate."""
    global _browser, _browser_stealth, _browser_side, _browser_side_stealth
    global _playwright, _watchdog_task, _stopping
    _stopping = True
    _sd_notify("STOPPING=1\nSTATUS=Stopping Playwright sidecar")
    task = _watchdog_task
    _watchdog_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    try:
        from playwright_sidecar import session_broker
        await session_broker.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("session broker shutdown failed: %s", exc)
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _browser_stealth is not None:
        try:
            await _browser_stealth.close()
        except Exception:
            pass
        _browser_stealth = None
    if _browser_side is not None:
        try:
            await _browser_side.close()
        except Exception:
            pass
        _browser_side = None
    if _browser_side_stealth is not None:
        try:
            await _browser_side_stealth.close()
        except Exception:
            pass
        _browser_side_stealth = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None


def make_app() -> web.Application:
    """Costruisce l'aiohttp app. Esposto come funzione cosi' i test possono
    sostituire il browser con un mock prima dello startup."""
    app = web.Application(middlewares=[_contract_middleware])
    app.router.add_get("/health", handle_health)
    app.router.add_post("/render", handle_render)
    # Session-broker (dominio sites §3.1)
    app.router.add_post("/session/open", handle_session_open)
    app.router.add_post("/session/read", handle_session_read)
    app.router.add_post("/session/screenshot", handle_session_screenshot)
    app.router.add_post("/session/login", handle_session_login)
    app.router.add_post("/session/close", handle_session_close)
    app.router.add_post("/session/act", handle_session_act)
    app.router.add_post("/session/goto", handle_session_goto)
    app.router.add_post("/session/click", handle_session_click)
    app.router.add_post("/session/fill", handle_session_fill)
    app.router.add_post("/session/submit", handle_session_submit)
    app.router.add_post("/session/wait", handle_session_wait)
    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="playwright_sidecar.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    app = make_app()
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
