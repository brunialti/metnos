# SPDX-License-Identifier: AGPL-3.0-only
"""Metnos Telegram daemon — public stub.

Minimal long-poll loop. When the user has configured a BOT_TOKEN via
the installer (phase 4), this stub exists so the systemd unit
``metnos-telegram-daemon.service`` actually has something to run.

Behaviour today: connect to Telegram, log the bot identity, then
acknowledge every incoming message with a placeholder note pointing
at the dashboard.

The full daemon (channel adapter, dialog routing, agent invocation,
HTML formatting) lands when the agent runtime ships.
"""

from __future__ import annotations

import logging
import os
import sys
import time

import httpx


log = logging.getLogger("metnos.telegram")

_API_BASE = "https://api.telegram.org/bot{token}"
_POLL_TIMEOUT = 25


def _load_token() -> str | None:
    """Look up BOT_TOKEN from the credentials store; fall back to env.

    Order of precedence:
      1. METNOS_TELEGRAM_BOT_TOKEN env var (escape hatch for tests)
      2. runtime.credentials.fetch('telegram_bot_token')  (ADR 0131)
      3. ~/.config/metnos/credentials_pending/telegram_bot_token.txt
         (installer fallback when runtime.credentials isn't on path yet)
    """
    if t := os.environ.get("METNOS_TELEGRAM_BOT_TOKEN"):
        return t.strip()
    try:
        from runtime.credentials import fetch  # type: ignore
        return (fetch("telegram_bot_token") or "").strip() or None
    except (ImportError, KeyError):
        pass
    from pathlib import Path
    cfg = Path(os.environ.get("METNOS_CONFIG", Path.home() / ".config" / "metnos"))
    f = cfg / "credentials_pending" / "telegram_bot_token.txt"
    if f.exists():
        return f.read_text().strip()
    return None


def _get_me(token: str) -> dict | None:
    try:
        r = httpx.get(_API_BASE.format(token=token) + "/getMe", timeout=10.0)
        r.raise_for_status()
        return r.json().get("result")
    except httpx.RequestError as e:
        log.error("getMe failed: %s", e)
        return None


def _poll_loop(token: str) -> None:
    me = _get_me(token)
    if not me:
        log.error("Bot identity check failed — exiting")
        sys.exit(1)
    log.info("Telegram bot ready: @%s (id=%s)", me.get("username"), me.get("id"))

    offset = 0
    url = _API_BASE.format(token=token) + "/getUpdates"
    placeholder = (
        "Metnos is installed and listening, but the full agent runtime is not "
        "yet enabled on this instance.\n\n"
        "Visit http://127.0.0.1:8770/ for the dashboard, or read the design at "
        "https://metnos.com."
    )

    while True:
        try:
            r = httpx.get(url, params={"timeout": _POLL_TIMEOUT, "offset": offset}, timeout=_POLL_TIMEOUT + 5)
            r.raise_for_status()
            updates = r.json().get("result", [])
            for upd in updates:
                offset = max(offset, int(upd["update_id"]) + 1)
                msg = upd.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                if chat_id:
                    httpx.post(
                        _API_BASE.format(token=token) + "/sendMessage",
                        json={"chat_id": chat_id, "text": placeholder},
                        timeout=10.0,
                    )
        except httpx.RequestError as e:
            log.warning("poll error: %s — backing off 5s", e)
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("Telegram daemon stopped by user")
            return


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    token = _load_token()
    if not token:
        log.error(
            "No Telegram BOT_TOKEN configured. Run `python -m install --force-phase 4` "
            "to register one, or set METNOS_TELEGRAM_BOT_TOKEN."
        )
        sys.exit(2)
    log.info("Starting Telegram daemon (long-poll, stub)")
    _poll_loop(token)


if __name__ == "__main__":
    main()
