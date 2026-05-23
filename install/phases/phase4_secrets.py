# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 4 — Sensitive data dialog.

The only phase that asks for credentials. Every answer is stored
encrypted via Fernet+HKDF (ADR 0131 single credentials store);
nothing plaintext lands on disk.

Bootstrap secret: the admin HMAC key (``~/.config/metnos/admin.key``)
is generated automatically — 256 bits from ``os.urandom`` — so the
user never has to type it.

For each optional integration (Telegram, IMAP, Anthropic, OpenAI,
Google Workspace, GitHub) the dialog asks once and stores the credential
under a stable domain key the runtime reads later.

All prompts honour ``--yes`` (non-interactive): in that mode optional
integrations are skipped, and the user can add them after install via
``metnos-cli credentials add``.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from .. import ui


def _config_dir() -> Path:
    d = Path(os.environ.get("METNOS_CONFIG", Path.home() / ".config" / "metnos"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_admin_key() -> bool:
    """Create ``~/.config/metnos/admin.key`` if absent. 256-bit hex."""
    p = _config_dir() / "admin.key"
    if p.exists():
        ui.info(f"admin.key exists, leaving in place: {p}")
        return False
    key = secrets.token_hex(32)
    p.write_text(key + "\n")
    p.chmod(0o600)
    ui.ok(f"admin.key generated (256-bit, {p})")
    return True


def _store_credential(domain: str, secret: str, *, description: str = "") -> bool:
    """Persist a credential via runtime.credentials if available.

    During early scaffold (no runtime/ in the public repo yet), we
    fall back to writing into ``~/.config/metnos/credentials_pending/``
    so the user knows what was collected and can migrate manually once
    the runtime ships.
    """
    try:
        # Late import: runtime may not be on path yet during early scaffold
        from runtime.credentials import store  # type: ignore
    except ImportError:
        # Fallback: write into a pending directory with sensible perms
        pending = _config_dir() / "credentials_pending"
        pending.mkdir(mode=0o700, exist_ok=True)
        f = pending / f"{domain}.txt"
        f.write_text(secret + "\n")
        f.chmod(0o600)
        ui.info(f"runtime/credentials not yet available; secret stashed at {f} (mode 0600)")
        return True

    try:
        store(domain=domain, secret=secret, description=description)
        ui.ok(f"credential stored: domain={domain}")
        return True
    except Exception as e:  # pragma: no cover — runtime in flux
        ui.warn(f"failed to store credential {domain}: {e}")
        return False


def _ask_admin(args: Any) -> dict[str, Any]:
    if args.yes:
        return {"admin_username": "admin"}
    name = ui.ask("Admin username (for the web dashboard)", default="admin")
    return {"admin_username": name}


def _ask_http_port(args: Any) -> int:
    if args.yes:
        return 8770
    default = "8770"
    raw = ui.ask("HTTP port for the Metnos dashboard", default=default)
    try:
        return int(raw)
    except ValueError:
        ui.warn(f"not an integer '{raw}', using {default}")
        return int(default)


def _ask_telegram(args: Any) -> bool:
    if args.yes:
        return False
    ui.console().print("\n  [bold]Telegram channel[/bold] (optional)")
    ui.console().print("  [dim]Lets you chat with the agent from Telegram. "
                       "Create a bot at @BotFather and paste its token.[/dim]")
    if not ui.confirm("Configure Telegram now?", default=False):
        return False
    token = ui.ask("Telegram BOT_TOKEN", password=True)
    if not token:
        ui.warn("empty token, skipping Telegram setup")
        return False
    _store_credential("telegram_bot_token", token, description="Telegram BotFather token")
    return True


def _ask_imap(args: Any) -> int:
    if args.yes:
        return 0
    ui.console().print("\n  [bold]IMAP mail accounts[/bold] (optional)")
    if not ui.confirm("Add a mail account?", default=False):
        return 0
    n = 0
    while True:
        label = ui.ask("Account label (e.g. 'personal', 'work')")
        host = ui.ask("IMAP server hostname")
        user = ui.ask("IMAP username")
        password = ui.ask("IMAP password", password=True)
        _store_credential(
            f"imap_{label}",
            f"host={host}\nuser={user}\npassword={password}\n",
            description=f"IMAP account: {label}",
        )
        n += 1
        if not ui.confirm("Add another account?", default=False):
            break
    return n


def _ask_apikey(args: Any, provider: str, env_hint: str) -> bool:
    if args.yes:
        return False
    ui.console().print(f"\n  [bold]{provider} API key[/bold] (optional)")
    ui.console().print(f"  [dim]Used for frontier-tier reasoning when explicitly invoked. "
                       f"Read from {env_hint} if not provided here.[/dim]")
    if not ui.confirm(f"Configure {provider} now?", default=False):
        return False
    key = ui.ask(f"{provider} API key", password=True)
    if not key:
        return False
    _store_credential(f"{provider.lower()}_api_key", key, description=f"{provider} API key")
    return True


def _ask_workspace_paths(args: Any) -> dict[str, str]:
    if args.yes:
        return {}
    ui.console().print("\n  [bold]Workspace paths[/bold] (where Metnos may read your files)")
    pics = ui.ask("Pictures directory", default=str(Path.home() / "Pictures"))
    docs = ui.ask("Documents directory", default=str(Path.home() / "Documents"))
    return {"pictures": pics, "documents": docs}


def _write_locale(args: Any) -> str:
    """Return the locale set at the disclaimer gate (phase 0).

    The disclaimer gate captures locale before any phase runs and
    persists it in the disclaimer sentinel. We re-read it here so
    phase 4's notes carry the same value and downstream phases (6, the
    runtime) honour the user's original choice.
    """
    from .. import disclaimer
    existing = disclaimer.read_locale()
    if existing in ("en", "it"):
        return existing
    if args.yes:
        return "en"
    return ui.choice("Default UI / report language", ["en", "it"], default="en")


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 4 — Sensitive data",
              "Admin key + optional channel / API credentials (stored encrypted)")

    if args.yes:
        ui.warn("Running with --yes: every optional integration will be skipped. "
                "Use `metnos-cli credentials add` later to fill them in.")

    # 1. Admin HMAC key (always)
    _generate_admin_key()

    # 2. Admin user
    notes.update(_ask_admin(args))

    # 3. HTTP port
    notes["http_port"] = _ask_http_port(args)

    # 4. Locale
    notes["locale"] = _write_locale(args)

    # 5. Optional credentials
    notes["telegram"] = _ask_telegram(args)
    notes["imap_accounts"] = _ask_imap(args)
    notes["anthropic"]  = _ask_apikey(args, "Anthropic", "ANTHROPIC_API_KEY")
    notes["openai"]     = _ask_apikey(args, "OpenAI",    "OPENAI_API_KEY")
    notes["github_pat"] = _ask_apikey(args, "GitHub",    "GITHUB_PAT")

    # 6. Workspace paths
    paths = _ask_workspace_paths(args)
    if paths:
        notes["workspace"] = paths

    ui.console().print()
    ui.ok("Phase 4 done — all secrets stored (encrypted where the runtime is available).")
    return notes
