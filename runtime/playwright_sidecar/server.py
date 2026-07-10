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
    pip install playwright>=1.40 aiohttp
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
import sys
import time

from aiohttp import web

logger = logging.getLogger("playwright_sidecar")

# Stato globale: un solo browser Chromium per processo (ADR 0125 §F).
_browser = None
_playwright = None
_browser_version = ""

# Cap di sicurezza: timeout assoluto su una singola render. Sopra di questo,
# preferiamo dichiarare timeout che restare appesi (ADR 0125 §G).
_RENDER_TIMEOUT_HARD_S = 30.0
# Wait dopo `goto` per dare al JS il tempo di idratare la pagina.
_DEFAULT_WAIT_MS = 2000
_MAX_WAIT_MS = 15000

# Viewport di default (desktop FullHD-ish).
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


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
    if _browser is None:
        return web.json_response(
            {"ok": False, "error": "browser not initialized",
             "error_class": "unknown"},
            status=503,
        )
    return web.json_response({
        "ok": True,
        "browser": "chromium",
        "version": _browser_version,
    })


async def handle_render(request: web.Request) -> web.Response:
    """POST /render: renderizza JS su una pagina e ritorna HTML finale."""
    if _browser is None:
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

async def _broker_call(request, opname):
    if _browser is None:
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
    res = await opname(session_broker, body)
    return web.json_response(res)


async def handle_session_open(request):
    async def _op(sb, b):
        return await sb.op_open(
            owner=b.get("owner", "host"), url=b.get("url", ""),
            allowlist_arg=b.get("allowlist"), session_label=b.get("session_label", ""))
    return await _broker_call(request, _op)


async def handle_session_read(request):
    async def _op(sb, b):
        return await sb.op_read(
            session_id=b.get("session_id", ""),
            include_screenshot=bool(b.get("include_screenshot", True)),
            include_forms=bool(b.get("include_forms", False)))
    return await _broker_call(request, _op)


async def handle_session_screenshot(request):
    async def _op(sb, b):
        return await sb.op_screenshot(session_id=b.get("session_id", ""))
    return await _broker_call(request, _op)


async def handle_session_login(request):
    async def _op(sb, b):
        return await sb.op_login(
            session_id=b.get("session_id", ""), domain=b.get("domain"),
            form_hint=b.get("form_hint"))
    return await _broker_call(request, _op)


async def handle_session_close(request):
    async def _op(sb, b):
        return await sb.op_close(
            session_id=b.get("session_id"), owner=b.get("owner"),
            close_all=bool(b.get("all", False)))
    return await _broker_call(request, _op)


async def _on_startup(app: web.Application) -> None:
    """Inizializza Playwright + Chromium browser."""
    global _browser, _playwright, _browser_version
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        # §2.8 fail-loud: niente fallback. Roberto sceglie quando installare.
        logger.error("playwright import failed: %s — run `pip install "
                     "playwright>=1.40 && playwright install chromium`", e)
        raise SystemExit(1)

    _playwright = await async_playwright().start()
    try:
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",  # systemd-user gia' contiene
                "--disable-dev-shm-usage",
                # spec sites §3.1 FIX D: WebRTC off a livello browser (difesa in
                # profondità oltre all'init-script per-contesto). Il canale
                # STUN/TURN esfiltra fuori banda scavalcando route().
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        _browser_version = _browser.version
        # spec sites §3.1: registra il browser nel session-broker + avvia il
        # reaper (TTL idle, salta gate_pending). Import lazy: il sidecar resta
        # avviabile anche senza il modulo (degrade graceful del solo /render).
        try:
            from playwright_sidecar import session_broker
            session_broker.configure(_browser)
            session_broker.start_reaper()
            logger.info("session_broker configured (sites domain ready)")
        except Exception as e:  # noqa: BLE001
            logger.warning("session_broker not available: %s", e)
        logger.info("playwright chromium %s ready", _browser_version)
    except Exception as e:
        # Tipico: `playwright install chromium` non eseguito.
        logger.error("chromium launch failed: %s — run `playwright install "
                     "chromium`", e)
        await _playwright.stop()
        raise SystemExit(1)


async def _on_shutdown(app: web.Application) -> None:
    """Chiusura pulita del browser."""
    global _browser, _playwright
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None


def make_app() -> web.Application:
    """Costruisce l'aiohttp app. Esposto come funzione cosi' i test possono
    sostituire il browser con un mock prima dello startup."""
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/render", handle_render)
    # Session-broker (dominio sites §3.1)
    app.router.add_post("/session/open", handle_session_open)
    app.router.add_post("/session/read", handle_session_read)
    app.router.add_post("/session/screenshot", handle_session_screenshot)
    app.router.add_post("/session/login", handle_session_login)
    app.router.add_post("/session/close", handle_session_close)
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
