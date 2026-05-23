# SPDX-License-Identifier: AGPL-3.0-only
"""Metnos HTTP server — public stub.

Minimal aiohttp server. Three endpoints today:

- ``GET /agent/health`` — returns 200 + JSON status (used by the
  installer's phase 5 health probe)
- ``GET /admin/onboard?t=<token>`` — validates the HMAC token from
  the installer's phase 6 and, on success, sets a cookie marking the
  caller as admin
- ``GET /`` — placeholder landing page directing to metnos.com

The full server (channel dashboard, turn streaming, admin pages,
proposals UI) lands when the rest of the runtime ships. This stub
exists so the installer produces a functional service end-to-end.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from aiohttp import web


log = logging.getLogger("metnos.http")


# ─── Config from environment ──────────────────────────────────────

def _config_dir() -> Path:
    return Path(os.environ.get("METNOS_CONFIG") or (Path.home() / ".config" / "metnos"))


def _admin_key() -> str | None:
    p = _config_dir() / "admin.key"
    if not p.exists():
        return None
    return p.read_text().strip()


def _validate_onboard_token(token: str) -> bool:
    """Replays the HMAC scheme from install/phases/phase6_firstboot.py."""
    key_hex = _admin_key()
    if not key_hex:
        return False
    try:
        expires_s, nonce, sig = token.split(".")
        expires = int(expires_s)
    except (ValueError, AttributeError):
        return False
    if expires < time.time():
        return False
    payload = f"{expires}.{nonce}"
    expected = hmac.new(bytes.fromhex(key_hex), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(expected, sig)


# ─── Handlers ─────────────────────────────────────────────────────

async def health(_: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "service": "metnos-http",
        "version": "0.1.0-stub",
        "stub": True,
    })


async def onboard(request: web.Request) -> web.Response:
    token = request.query.get("t", "")
    if not _validate_onboard_token(token):
        return web.Response(
            status=401,
            text="Invalid or expired onboarding token. "
                 "Run `python -m install --force-phase 6` to regenerate.",
        )
    session_id = secrets.token_hex(16)
    response = web.Response(
        status=200,
        content_type="text/html",
        text=(
            "<!doctype html><meta charset=utf-8>"
            "<title>Metnos — admin onboarded</title>"
            "<body style='font-family:system-ui;max-width:36em;margin:3em auto;padding:0 1em'>"
            "<h1>Admin session active</h1>"
            "<p>Cookie set. This window can be closed.</p>"
            "<p style='color:#5e6571'>The full admin dashboard lands when the "
            "runtime modules ship. Until then, visit "
            "<a href='https://metnos.com'>metnos.com</a> for architecture and design notes.</p>"
            "</body>"
        ),
    )
    response.set_cookie(
        "metnos_admin",
        session_id,
        max_age=86400,
        httponly=True,
        samesite="Lax",
    )
    return response


async def index(_: web.Request) -> web.Response:
    return web.Response(
        content_type="text/html",
        text=(
            "<!doctype html><meta charset=utf-8>"
            "<title>Metnos</title>"
            "<body style='font-family:system-ui;max-width:36em;margin:3em auto;padding:0 1em'>"
            "<h1>Metnos</h1>"
            "<p><em>A personal assistant that runs on your hardware — and stops there.</em></p>"
            "<p>This instance is running. Architecture, design rationale, and a "
            "guided tour at <a href='https://metnos.com'>metnos.com</a>.</p>"
            "<p style='color:#5e6571'>This is a stub. The agent runtime modules "
            "land in upcoming releases — see "
            "<a href='https://github.com/brunialti/metnos/releases'>releases</a>.</p>"
            "</body>"
        ),
    )


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/agent/health", health)
    app.router.add_get("/admin/onboard", onboard)
    app.router.add_get("/", index)
    return app


def main() -> None:
    p = argparse.ArgumentParser(prog="metnos-http-server")
    p.add_argument("--host", default=os.environ.get("METNOS_HTTP_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("METNOS_HTTP_PORT", "8770")))
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("starting metnos-http stub on %s:%d (config=%s)", args.host, args.port, _config_dir())
    web.run_app(make_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
