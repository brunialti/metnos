# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 6 — First boot.

Final phase. After all the moving parts are in place, this:

- generates a one-shot admin onboarding URL with the HMAC token from
  ``~/.config/metnos/admin.key`` (so the user can claim the web
  dashboard without re-authenticating)
- prints a Telegram pairing snippet if the bot is enabled
- writes a Markdown summary at
  ``$METNOS_HOME/install_summary.md`` so the user has a single doc
  recording every choice they made
- opens the browser to the dashboard if ``$DISPLAY`` / ``$WAYLAND_DISPLAY``
  is set and the user agrees

After this phase finishes, Metnos is fully installed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import state, ui


def _onboard_token(admin_key_hex: str) -> str:
    """One-shot HMAC token good for 15 minutes."""
    expires = int(time.time()) + 15 * 60
    nonce = secrets.token_hex(8)
    payload = f"{expires}.{nonce}"
    sig = hmac.new(bytes.fromhex(admin_key_hex), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


def _read_admin_key() -> str | None:
    p = Path(os.environ.get("METNOS_USER_CONFIG", Path.home() / ".config" / "metnos")) / "admin.key"
    if not p.exists():
        return None
    return p.read_text().strip()


def _write_summary(rows: list[dict]) -> Path:
    home = Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local" / "share" / "metnos"))
    home.mkdir(parents=True, exist_ok=True)
    p = home / "install_summary.md"

    lines = [
        "# Metnos installation summary",
        "",
        f"_Generated {datetime.now().isoformat(timespec='seconds')}_",
        "",
        "## Phases",
        "",
        "| # | Name | Status | Notes |",
        "|--:|------|:------:|-------|",
    ]
    for r in rows:
        status = "✓ done" if r["done"] else "⋯ pending"
        notes = ", ".join(f"`{k}={v}`" for k, v in (r.get("notes") or {}).items()) or "—"
        lines.append(f"| {r['phase']} | {r['name']} | {status} | {notes} |")

    lines += [
        "",
        "## Files of interest",
        "",
        "- `~/.config/metnos/admin.key` — HMAC key for admin onboarding (mode 0600)",
        "- `~/.config/metnos/llm_tiers.toml` — tier routing config",
        "- `~/.local/share/metnos/install_summary.md` — this file",
        "- `~/.local/share/metnos/.venv/` — Python virtual environment",
        "- `~/.local/state/metnos/install/phase*.done` — phase sentinels (delete to re-run)",
        "",
        "## Day-2 commands",
        "",
        "```bash",
        "systemctl --user status metnos-http",
        "systemctl --user restart metnos-http",
        "journalctl --user -u metnos-http -f",
        "python -m install --force-phase 4   # re-run secrets dialog",
        "```",
        "",
        "## How to connect",
        "",
        "Metnos talks to you over **two channels**:",
        "",
        "- **Web UI (HTTP)** — open `http://127.0.0.1:<port>/` in a browser (from",
        "  another device, replace `127.0.0.1` with this machine's IP).",
        "  **First connect needs the admin key** (`~/.config/metnos/admin.key`,",
        "  auto-created on first boot). Easiest path: the one-shot onboarding URL",
        "  printed during install (valid 15 min) claims access for your browser; if it",
        "  expired, `cat ~/.config/metnos/admin.key` or re-run `--force-phase 6`.",
        "- **Telegram** — if configured, open your BotFather bot, send `/start`, and",
        "  paste the pairing code from the Web UI. Not configured? Create a bot with",
        "  @BotFather, then `python -m install --force-phase 4` to add the token.",
        "",
        "## Next steps",
        "",
        "- Claim admin access via the one-shot onboarding URL printed during install (15 min).",
        "- Read the full architecture at https://metnos.com",
        "- Issues / questions: https://github.com/brunialti/metnos/issues",
        "",
    ]
    p.write_text("\n".join(lines))
    return p


def _select_skills(args: Any) -> dict[str, bool]:
    """Pick which first-party SKILLS (modular capabilities) start enabled.

    All default to ON (auto_enable) so a fresh install matches the reference
    instance; a skill you enable but haven't configured stays DORMANT (visible,
    inert) until its prerequisite is met. You can change this any time later
    with ``metnos-skills enable/disable`` or by asking in chat. Honours
    ``--yes`` (enable every auto_enable default, no prompts)."""
    try:
        from runtime.skills_catalog import FIRST_PARTY_SKILLS
        from runtime.skill_registry import set_skill_enabled
    except Exception as e:  # pragma: no cover — never block first boot on this
        ui.warn(f"skill selection unavailable ({e}); leaving defaults.")
        return {}
    ui.step("Skills (modular capabilities)")
    ui.info("'core' is always on. Each skill below is dormant until its "
            "backend/credential is configured. Change later: metnos-skills.")
    decisions: dict[str, bool] = {}
    for sk in FIRST_PARTY_SKILLS:
        name = sk["name"]
        default_on = bool(sk.get("auto_enable", True))
        if getattr(args, "yes", False):
            enabled = default_on
        else:
            enabled = ui.confirm(
                f"Enable '{name}' — {sk.get('desc', '')} (needs {sk.get('requires','—')})",
                default=default_on)
        try:
            set_skill_enabled(name, enabled)
        except Exception as e:  # pragma: no cover
            ui.warn(f"could not persist skill '{name}': {e}")
        decisions[name] = enabled
    on = [k for k, v in decisions.items() if v]
    ui.ok(f"skills enabled: {', '.join(on) if on else '(core only)'}")
    return decisions


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 6 — First boot", "Admin onboarding + summary + next steps")

    # Pull port + telegram + service state from phase 4/5
    phase4 = state.load(4)
    phase5 = state.load(5)
    port = (phase5.notes.get("http_port") if phase5 else None) or (phase4.notes.get("http_port") if phase4 else 8770)
    telegram_on = bool(phase4 and phase4.notes.get("telegram"))
    http_enabled = bool(phase5 and phase5.notes.get("http_enabled"))
    http_healthy = bool(phase5 and phase5.notes.get("http_healthy"))

    # 0. If the HTTP service never started, the onboarding URL would be a
    #    dead link. Be honest (§2.8): tell the user how to recover instead.
    if not http_enabled:
        ui.warn("The HTTP service is not running (phase 5 could not start it). "
                "The onboarding URL below would not resolve yet.")
        ui.console().print("  [bold]To recover:[/bold]")
        ui.console().print("    1) Inspect: [cyan]systemctl --user status metnos-http[/cyan]")
        ui.console().print("    2) Logs:    [cyan]journalctl --user -u metnos-http -e[/cyan]")
        ui.console().print("    3) Re-run:  [cyan]python -m install --force-phase 5[/cyan]")
        ui.console().print()
        notes["http_enabled"] = False
    elif not http_healthy:
        ui.warn("metnos-http started but did not pass the health probe yet — it may "
                "still be warming up. Check `systemctl --user status metnos-http`.")

    # 1. Onboarding URL (only meaningful once the service is up)
    admin_key = _read_admin_key()
    if admin_key and http_enabled:
        token = _onboard_token(admin_key)
        url = f"http://127.0.0.1:{port}/admin/onboard?t={token}"
        ui.console().print()
        ui.console().print("  [bold green]One-shot admin onboarding URL[/bold green] (valid 15 min):")
        ui.console().print(f"  [link={url}]{url}[/link]")
        ui.console().print()
        notes["onboard_url_emitted"] = True
    elif not admin_key:
        ui.warn("admin.key not found — was phase 4 completed?")
        notes["onboard_url_emitted"] = False
    else:
        # admin.key present but the service is down — URL deferred, not emitted.
        ui.info("Onboarding URL deferred until the service is up (see recovery steps above).")
        notes["onboard_url_emitted"] = False

    # 2. How to connect — Web UI (needs the admin key on first connect)
    ui.console().print("  [bold green]Connect to the Web UI[/bold green]:")
    ui.console().print(f"    • From this machine:   http://127.0.0.1:{port}/")
    ui.console().print(f"    • From another device: http://<this-machine-ip>:{port}/")
    ui.console().print("    [bold]First connect needs the admin key.[/bold] Easiest: open the")
    ui.console().print("    one-shot onboarding URL above (valid 15 min) — it claims access for")
    ui.console().print("    your browser. The key itself lives at [cyan]~/.config/metnos/admin.key[/cyan]")
    ui.console().print("    (`cat` it if you need it). Lost the URL? re-run")
    ui.console().print("    `python -m install --force-phase 6` to print a fresh one.")
    ui.console().print()

    # 3. How to connect — Telegram
    if telegram_on:
        ui.console().print("  [bold green]Connect via Telegram[/bold green] (enabled):")
        ui.console().print("    1) On Telegram, open the bot you configured (the one BotFather gave you).")
        ui.console().print("    2) Send /start.")
        ui.console().print("    3) Paste the pairing code shown on the Web UI to link your account.")
    else:
        ui.console().print("  [bold]Telegram[/bold] is not configured. To enable it later:")
        ui.console().print("    1) Create a bot with @BotFather on Telegram and copy its token.")
        ui.console().print("    2) Run:  python -m install --force-phase 4   (enter the token)")
        ui.console().print("    3) Then /start the bot and pair as above.")
    ui.console().print()

    # 2b. Skill selection (modular capabilities)
    notes["skills"] = _select_skills(args)

    # 3. Write the summary
    ui.step("Writing install summary")
    summary_path = _write_summary(state.summary())
    ui.ok(f"summary at {summary_path}")
    notes["summary_path"] = str(summary_path)

    # 4. Final note — honest about whether the service is actually up.
    ui.console().print()
    if http_enabled and http_healthy:
        ui.console().print("  [bold]All done.[/bold] Metnos is installed and running.")
    elif http_enabled:
        ui.console().print("  [bold]Installed.[/bold] The service started but has not passed its "
                           "health check yet — give it a moment, then re-check.")
    else:
        ui.console().print("  [bold yellow]Installed, but the service is not running yet.[/bold yellow] "
                           "Follow the recovery steps above before connecting.")
    ui.console().print("  [dim]Run `cat ~/.local/share/metnos/install_summary.md` anytime.[/dim]")

    return notes
