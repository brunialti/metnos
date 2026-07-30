# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 4 — Sensitive data dialog.

The only phase that asks for credentials. Every answer is stored
encrypted via the runtime's Fernet+HKDF credential store;
nothing plaintext lands on disk.

Bootstrap secret: the admin HMAC key (``~/.config/metnos/admin.key``)
is generated automatically — 256 bits from ``os.urandom`` — so the
user never has to type it.

For each optional integration offered here (Telegram, IMAP/SMTP, Anthropic,
OpenAI, GitHub) the dialog asks once and stores the credential under a stable
domain key the runtime reads later. Google Workspace uses its own OAuth flow
after installation; the installer never asks for the Google account password.

All prompts honour ``--yes`` (non-interactive): in that mode optional
integrations are skipped, and the user can add them after install via
``metnos-cli credentials add``.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Any

from .. import ui


def _config_dir() -> Path:
    d = Path(os.environ.get("METNOS_USER_CONFIG", Path.home() / ".config" / "metnos"))
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


def _store_credential(domain: str, payload: dict[str, Any], *,
                      description: str = "") -> bool:
    """Persist one canonical payload through the encrypted credential store."""
    if not isinstance(payload, dict) or not payload:
        ui.warn(f"failed to store credential {domain}: empty payload")
        return False
    # ``python -m install`` starts with the repository root on sys.path,
    # whereas runtime modules use flat imports (``import config``).  Admit the
    # canonical runtime directory explicitly instead of falling back to a
    # second, plaintext credential format.
    install_root = Path(os.environ.get(
        "METNOS_INSTALL_ROOT", Path(__file__).resolve().parents[2]))
    runtime_dir = str(install_root / "runtime")
    if runtime_dir not in sys.path:
        sys.path.insert(0, runtime_dir)
    try:
        from credentials import store  # type: ignore
    except ImportError as exc:
        ui.warn(f"encrypted credential store unavailable for {domain}: {exc}")
        return False

    try:
        stored = dict(payload)
        if description:
            stored["_description"] = description
        store(domain, stored)
        ui.ok(f"credential stored encrypted: domain={domain}")
        return True
    except Exception as e:  # pragma: no cover — runtime in flux
        ui.warn(f"failed to store credential {domain}: {e}")
        return False


def _ask_admin(args: Any) -> dict[str, Any]:
    if args.yes:
        return {"admin_username": "admin"}
    name = ui.ask("Admin username (for the web dashboard)", default="admin")
    return {"admin_username": name}


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _valid_port(raw: str) -> int | None:
    try:
        p = int(raw)
    except (ValueError, TypeError):
        return None
    return p if 1024 <= p <= 65535 else None


def _ask_http_port(args: Any) -> int:
    """HTTP dashboard port. Honours $METNOS_HTTP_PORT, validates range + in-use."""
    default = os.environ.get("METNOS_HTTP_PORT", "8770")
    if _valid_port(default) is None:
        default = "8770"
    if args.yes:
        port = int(default)
        if _port_in_use(port):
            ui.warn(f"port {port} is already in use — set METNOS_HTTP_PORT to a free port.")
        return port
    while True:
        raw = ui.ask("HTTP port for the Metnos dashboard (1024-65535)", default=default)
        port = _valid_port(raw)
        if port is None:
            ui.warn(f"'{raw}' is not a valid port (1024-65535) — try again.")
            continue
        if _port_in_use(port):
            if not ui.confirm(f"port {port} looks already in use — use it anyway?", default=False):
                continue
        return port


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
    return _store_credential(
        "telegram_bot_token", {"value": token},
        description="Telegram BotFather token")


def _ask_mail_port(question: str, *, default: int) -> int:
    """Ask for a network port using the full IANA port range."""
    while True:
        raw = ui.ask(question, default=str(default))
        try:
            port = int(raw)
        except (TypeError, ValueError):
            port = 0
        if 1 <= port <= 65535:
            return port
        ui.warn(f"'{raw}' is not a valid port (1-65535) — try again.")


def _ask_imap(args: Any) -> int:
    if args.yes:
        return 0
    ui.console().print("\n  [bold]IMAP/SMTP mail accounts[/bold] (optional)")
    if not ui.confirm("Add a mail account?", default=False):
        return 0
    n = 0
    while True:
        label = ui.ask("Account label (e.g. 'personal', 'work')")
        imap_host = ui.ask("IMAP server hostname")
        imap_port = _ask_mail_port("IMAP server port", default=993)
        user = ui.ask("IMAP username")
        password = ui.ask("IMAP password", password=True)
        smtp_host = ""
        smtp_port = 465
        if ui.confirm("Configure SMTP sending for this account?", default=True):
            smtp_host = ui.ask("SMTP server hostname")
            smtp_port = _ask_mail_port("SMTP server port", default=465)
        stored = _store_credential(
            f"smtp_{label}",
            {
                "user": user,
                "password": password,
                "imap_host": imap_host,
                "imap_port": imap_port,
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "verify_tls": True,
            },
            description=f"IMAP account: {label}",
        )
        if stored:
            n += 1
        if not ui.confirm("Add another account?", default=False):
            break
    return n


def _ask_apikey(args: Any, provider: str, env_hint: str, domain: str) -> bool:
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
    return _store_credential(
        domain, {"value": key}, description=f"{provider} API key")


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
    notes["anthropic"] = _ask_apikey(
        args, "Anthropic", "ANTHROPIC_API_KEY", "anthropic_api_key")
    notes["openai"] = _ask_apikey(
        args, "OpenAI", "OPENAI_API_KEY", "openai_api_key")
    notes["github_pat"] = _ask_apikey(
        args, "GitHub", "GITHUB_PAT", "github")

    # 6. Workspace paths
    paths = _ask_workspace_paths(args)
    if paths:
        notes["workspace"] = paths

    ui.console().print()
    ui.ok("Phase 4 done — all secrets stored (encrypted where the runtime is available).")
    return notes
